"""V11 ``caption_geometry_missing`` and the bare-lint warning — an unreadable gate is a missing
gate.

The defect: a 30s film shipped with ~1.10:1 captions past a clean lint. The captions had been
burned per shot before the stitch, nothing carried the per-shot sidecars forward, and the finish
manifest handed to ``--manifest`` read ``captions: null`` — so V3 and V11 both SKIPPED, which
reads identical to both PASSING. Two closures: a manifest that says captions were burned and
carries no geometry is now an ERROR, and a bare lint that ignores a finish manifest sitting
beside the asset says so on stderr.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_lint as vl
from gtm_core.video_lint.cli import main, sibling_manifest

FFMPEG = shutil.which("ffmpeg")


def _probe(**over) -> vl.Probe:
    base = {
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration_s": 30.0,
        "bit_rate": 8_000_000,
        "has_audio": True,
        "pix_fmt": "yuv420p",
    }
    return vl.Probe(**{**base, **over})


def _v11(findings) -> list[vl.Finding]:
    return [f for f in findings if f.tier == "V11"]


_WITH_SCREENS = {
    "frame": [1080, 1920],
    "screens": [{"index": 0, "box": {"x": 100, "y": 1400, "w": 880, "h": 150}}],
}


# ── the pure rule ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["local", "reap"])
def test_a_burned_route_with_no_captions_payload_is_an_error(route):
    found = _v11(vl.evaluate(_probe(), ratio="9:16", manifest=None, caption_route=route))
    assert [f.rule for f in found] == ["caption_geometry_missing"]
    assert found[0].severity == vl.ERROR
    assert f"caption_route={route!r}" in found[0].excerpt
    assert "no captions payload" in found[0].excerpt
    assert "carry the per-shot captions sidecar" in found[0].fix


def test_captions_preburned_with_no_payload_is_an_error():
    """The per-shot burn route: nothing in the manifest names a caption_route, but the flag
    says the boxes exist somewhere — and they are not here."""
    found = _v11(vl.evaluate(_probe(), ratio="9:16", manifest=None, captions_preburned=True))
    assert [f.rule for f in found] == ["caption_geometry_missing"]
    assert "captions_preburned=true" in found[0].excerpt


def test_a_payload_with_no_screens_is_as_missing_as_no_payload():
    found = _v11(
        vl.evaluate(
            _probe(),
            ratio="9:16",
            manifest={"frame": [1080, 1920], "screens": []},
            caption_route="local",
        )
    )
    assert [f.rule for f in found] == ["caption_geometry_missing"]
    assert "no screens" in found[0].excerpt


def test_a_manifest_that_carries_the_boxes_is_clean():
    for kwargs in ({"caption_route": "local"}, {"captions_preburned": True}):
        assert _v11(vl.evaluate(_probe(), ratio="9:16", manifest=_WITH_SCREENS, **kwargs)) == []


def test_a_route_of_none_declares_no_captions_and_needs_no_geometry():
    """An uncaptioned cut is a legitimate deliverable; the rule is about a CLAIM without
    evidence, not about the absence of captions."""
    assert _v11(vl.evaluate(_probe(), ratio="9:16", manifest=None, caption_route="none")) == []
    assert _v11(vl.evaluate(_probe(), ratio="9:16", manifest=None)) == []
    assert _v11(vl.evaluate(_probe(), ratio="9:16", captions_preburned=False)) == []


def test_the_declaration_is_read_from_the_top_level_not_the_captions_payload():
    """``manifest`` is the CAPTIONS sub-payload; a route key smuggled inside it is not the
    manifest's declaration and must not be promoted to one."""
    payload = {"frame": [1080, 1920], "screens": [], "caption_route": "local"}
    assert _v11(vl.evaluate(_probe(), ratio="9:16", manifest=payload)) == []


# ── sibling detection ──────────────────────────────────────────────────────────────────────


def test_sibling_manifest_uses_the_finish_naming_with_the_ratio_slug(tmp_path):
    asset = tmp_path / "cut-9x16-final.mp4"
    asset.write_bytes(b"")
    assert sibling_manifest(asset, "9:16") is None
    (tmp_path / "finish-9x16.json").write_text("{}", encoding="utf-8")
    assert sibling_manifest(asset, "9:16") == tmp_path / "finish-9x16.json"
    assert sibling_manifest(asset, "4:5") is None, "a different ratio's manifest is not this one"


def test_sibling_manifest_ignores_a_directory_of_that_name(tmp_path):
    (tmp_path / "finish-16x9.json").mkdir()
    assert sibling_manifest(tmp_path / "x.mp4", "16:9") is None


# ── the CLI ────────────────────────────────────────────────────────────────────────────────


def _clip(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
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


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_bare_lint_beside_a_finish_manifest_warns_and_still_runs(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    (tmp_path / "finish-9x16.json").write_text(json.dumps({"captions": None}), encoding="utf-8")
    code = main([str(clip), "--ratio", "9:16", "--fast", "--json"])
    err = capsys.readouterr()
    assert "finish-9x16.json" in err.err
    assert "bare lint silently skips V3/V5/V8/V11 — pass --manifest" in err.err
    assert code in (0, 1), "the warning must not abort the run"
    assert json.loads(err.out)["asset"] == "cut.mp4"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_bare_lint_with_no_sibling_does_not_warn(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    main([str(clip), "--ratio", "9:16", "--fast", "--json"])
    assert "bare lint" not in capsys.readouterr().err


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
@pytest.mark.parametrize(
    "declaration",
    [{"caption_route": "local"}, {"caption_route": "reap"}, {"captions_preburned": True}],
)
def test_the_cli_refuses_a_burned_manifest_with_no_geometry(tmp_path, capsys, declaration):
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(json.dumps({**declaration, "captions": None}), encoding="utf-8")
    code = main([str(clip), "--ratio", "9:16", "--fast", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    rules = {(f["tier"], f["rule"]) for f in out["findings"]}
    assert ("V11", "caption_geometry_missing") in rules
    assert code == 1


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_the_cli_passes_a_none_route_with_no_geometry(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(json.dumps({"caption_route": "none", "captions": None}), encoding="utf-8")
    main([str(clip), "--ratio", "9:16", "--fast", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    assert "caption_geometry_missing" not in {f["rule"] for f in out["findings"]}
    assert "bare lint" not in capsys.readouterr().err


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_the_json_output_carries_the_measured_audio_numbers(tmp_path, capsys):
    """An operator sees what V10 judged, not only whether it fired. A 1s clip is under the
    floor-only minimum, so this asserts the NUMBERS surface, not the verdict."""
    clip = _clip(tmp_path / "cut.mp4")
    main([str(clip), "--ratio", "9:16", "--json"])
    out = json.loads(capsys.readouterr().out)
    audio = out["audio"]
    assert {"integrated_lufs", "true_peak_dbfs", "loudness_range_lu", "crest_db"} <= set(audio)
    assert audio["crest_db"] == round(audio["true_peak_dbfs"] - audio["integrated_lufs"], 1)


def test_the_text_report_prints_an_audio_line(capsys):
    vl.report(
        "x.mp4",
        [],
        {},
        audio={
            "integrated_lufs": -14.0,
            "loudness_range_lu": 4.7,
            "loudness_event_fraction": 0.031,
        },
    )
    out = capsys.readouterr().out
    assert "audio: I=-14 LUFS, LRA=4.7 LU, events=3.1%" in out
    assert "suppressed: 0" in out
