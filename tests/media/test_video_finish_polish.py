"""gtm_core.video_finish.polish_with_reap() — lint-gated Reap polish helper.

Tests the gtm_core-side contract: path confinement, lint ERROR refusal, and delegation to the
injected reap_caller. The actual Reap MCP invocation stays in the skill layer and is mocked here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gtm_core import video_finish as vf

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_low_res_clip(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=640x480:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_clean_clip(path: Path) -> Path:
    """Generate a high-entropy 1080x1920 clip whose measured bitrate clears the 6 Mbps floor.

    ``testsrc`` is too compressible for CBR to register in the container header, so we use the
    ``mandelbrot`` lavfi source which produces enough detail that the reported bitrate lands
    above :data:`gtm_core.video_lint.MIN_BITRATE_1080P_BPS`.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "mandelbrot=s=1080x1920:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-t",
            "10",
            "-c:v",
            "libx264",
            "-b:v",
            "8M",
            "-minrate",
            "8M",
            "-maxrate",
            "8M",
            "-bufsize",
            "16M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_polish_with_reap_refuses_missing_reap_caller(tmp_path):
    asset = tmp_path / "content" / "acme" / "asset.mp4"
    asset.parent.mkdir(parents=True)
    _make_clean_clip(asset)
    with pytest.raises(vf.PolishError, match="reap_caller is required"):
        vf.polish_with_reap(asset, "acme", ratio="9:16", content_root=tmp_path / "content")


def test_polish_with_reap_refuses_path_outside_content_root(tmp_path):
    asset = tmp_path / "outside.mp4"
    _make_clean_clip(asset)

    def caller(_):
        raise AssertionError("should not be called")

    with pytest.raises(vf.PolishError, match="outside the resolved content root"):
        vf.polish_with_reap(
            asset, "acme", ratio="9:16", content_root=tmp_path / "content", reap_caller=caller
        )


def test_polish_with_reap_refuses_asset_that_fails_lint(tmp_path):
    content_root = tmp_path / "content"
    asset = content_root / "acme" / "asset.mp4"
    asset.parent.mkdir(parents=True)
    _make_low_res_clip(asset)

    def caller(_):
        raise AssertionError("should not be called")

    with pytest.raises(vf.LintError, match="lint ERROR"):
        vf.polish_with_reap(
            asset, "acme", ratio="9:16", content_root=content_root, reap_caller=caller
        )


def test_polish_with_reap_calls_caller_when_asset_passes_lint(tmp_path):
    content_root = tmp_path / "content"
    asset = content_root / "acme" / "asset.mp4"
    asset.parent.mkdir(parents=True)
    _make_clean_clip(asset)

    calls = []

    def caller(path: Path):
        calls.append(path)
        return {"status": "ok"}

    result = vf.polish_with_reap(
        asset, "acme", ratio="9:16", content_root=content_root, reap_caller=caller
    )
    assert result == {"status": "ok"}
    assert len(calls) == 1
    assert calls[0] == asset.resolve()


def test_polish_with_reap_uses_resolve_content_root_when_not_given(monkeypatch, tmp_path):
    """When content_root is None, the helper resolves via gtm_core.paths.resolve_content_root."""
    from gtm_core import paths

    content_root = tmp_path / "content"
    asset = content_root / "acme" / "asset.mp4"
    asset.parent.mkdir(parents=True)
    _make_clean_clip(asset)

    monkeypatch.setattr(paths, "resolve_content_root", lambda: content_root)

    def caller(path: Path):
        return "called"

    result = vf.polish_with_reap(asset, "acme", ratio="9:16", reap_caller=caller)
    assert result == "called"


def test_polish_with_reap_unknown_ratio_raises(tmp_path):
    content_root = tmp_path / "content"
    asset = content_root / "acme" / "asset.mp4"
    asset.parent.mkdir(parents=True)
    _make_clean_clip(asset)

    def caller(_):
        return None

    with pytest.raises(vf.PolishError, match="unknown ratio"):
        vf.polish_with_reap(
            asset, "acme", ratio="21:9", content_root=content_root, reap_caller=caller
        )
