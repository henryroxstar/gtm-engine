"""gtm_core.video_finish.execute() against real ffmpeg — this directory requires ffmpeg on PATH
(see conftest.py). Fixture clip is generated at session scope via lavfi testsrc, never committed
(a binary fixture is opaque to debrand/pii lint and rots against ffmpeg versions — the recipe is
the specification).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import matplotlib
import pytest

from gtm_core import video_finish as vf

REPO_ROOT = Path(__file__).resolve().parents[2]
#: matplotlib's bundled font — already a declared dependency, carries no tenant identity (unlike
#: a real brand font), so this file stays safe for the release-mode de-brand lint.
_FONT = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


@pytest.fixture(scope="session")
def source_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("video_finish_src")
    src = d / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    return src


def _kit():
    return {"typography": {"font_files": {"caption": _FONT}}}


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def test_execute_produces_a_1080x1920_mp4_with_captions_burned(source_clip, tmp_path):
    spec = {"caption_text": "hello from the finishing pipeline", "total_s": 3.0}
    p = vf.plan(profile="acme", slug="asset", ratio="9:16", source=str(source_clip), spec=spec)
    result = vf.execute(
        p, workdir=tmp_path / "work", out_dir=tmp_path / "out", kit=_kit(), repo_root=REPO_ROOT
    )

    assert result.executed
    assert not result.skipped
    assert result.out_path.is_file()
    assert result.caption_manifest_path.is_file()

    probe = _probe(result.out_path)
    vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert vstream["width"] == 1080
    assert vstream["height"] == 1920


def test_execute_overlays_captions_at_the_frame_origin_not_the_text_box(source_clip, tmp_path):
    """Regression: video_finish._build_filtergraph carried the same double-offset bug as
    captions.overlay_filtergraph — overlaying at r.box['x']/['y'] instead of (0, 0) pushed a wide
    caption line off the right edge of the finished frame."""
    spec = {
        "caption_text": "this line has enough words to sit well off the left edge of frame",
        "total_s": 3.0,
    }
    p = vf.plan(profile="acme", slug="asset9", ratio="9:16", source=str(source_clip), spec=spec)
    from gtm_core.captions import render as render_captions
    from gtm_core.captions import split_screens

    screens = split_screens(spec["caption_text"], total_s=spec["total_s"])
    rendered = render_captions(
        screens, ratio="9:16", kit=_kit(), out_dir=tmp_path / "cap", repo_root=REPO_ROOT
    )
    assert any(r.box["x"] > 0 for r in rendered)
    graph = vf._build_filtergraph(p, num_video_inputs=1 + len(rendered), caption_rendered=rendered)
    assert "overlay=x=0:y=0:" in graph
    for r in rendered:
        assert f"overlay=x={r.box['x']}:y={r.box['y']}:" not in graph


def test_execute_is_idempotent_zero_ffmpeg_calls_on_second_run(source_clip, tmp_path, monkeypatch):
    spec = {"caption_text": "idempotency check", "total_s": 2.0}
    p = vf.plan(profile="acme", slug="asset2", ratio="9:16", source=str(source_clip), spec=spec)
    workdir, out_dir = tmp_path / "work", tmp_path / "out"

    result1 = vf.execute(p, workdir=workdir, out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT)
    assert not result1.skipped

    calls = []
    real_run = subprocess.run

    def _tracking_run(args, **kw):
        calls.append(args)
        return real_run(args, **kw)

    monkeypatch.setattr(subprocess, "run", _tracking_run)
    result2 = vf.execute(p, workdir=workdir, out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT)
    assert result2.skipped
    assert calls == []  # zero ffmpeg invocations on the short-circuited run


def test_execute_without_captions_still_produces_a_finished_asset(source_clip, tmp_path):
    p = vf.plan(profile="acme", slug="asset3", ratio="9:16", source=str(source_clip), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    assert result.out_path.is_file()
    assert result.caption_manifest_path is None


def test_execute_writes_a_finish_sidecar_carrying_the_plan_id_and_census(source_clip, tmp_path):
    p = vf.plan(profile="acme", slug="asset4", ratio="9:16", source=str(source_clip), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    sidecar = json.loads(result.sidecar_path.read_text())
    assert sidecar["plan_id"] == p.plan_id
    assert sidecar["executed"] is True
    assert sidecar["census"]["grade"] == 1


def test_execute_never_uses_shell_true(source_clip, tmp_path, monkeypatch):
    calls = []
    real_run = subprocess.run

    def _tracking_run(args, **kw):
        calls.append(kw.get("shell", False))
        return real_run(args, **kw)

    monkeypatch.setattr(subprocess, "run", _tracking_run)
    p = vf.plan(profile="acme", slug="asset5", ratio="9:16", source=str(source_clip), spec={})
    vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    assert calls and all(shell_flag is False for shell_flag in calls)


def test_execute_raises_ffmpeg_unavailable_and_still_writes_the_degraded_sidecar(
    source_clip, tmp_path, monkeypatch
):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    p = vf.plan(
        profile="acme",
        slug="asset6",
        ratio="9:16",
        source=str(source_clip),
        spec={"caption_text": "x", "total_s": 1.0},
    )
    out_dir = tmp_path / "out"
    with pytest.raises(vf.FfmpegUnavailable):
        vf.execute(p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT)
    sidecar_path = out_dir / "finish-9x16.json"
    assert sidecar_path.is_file()
    data = json.loads(sidecar_path.read_text())
    assert data["executed"] is False
    assert data["stages"]  # full ordered stage list still recorded
    # Captions still rendered — Pillow-only, no ffmpeg needed.
    assert any((out_dir / "_work" / "captions").glob("*.png")) or any(
        (tmp_path / "work" / "captions").glob("*.png")
    )


def test_predictor_trim_re_encodes_never_stream_copies(source_clip, tmp_path):
    dst = tmp_path / "trimmed.mp4"
    vf.predictor_trim(source_clip, dst, duration_s=1.0)
    assert dst.is_file()
    probe = _probe(dst)
    duration = float(probe["format"]["duration"])
    assert duration == pytest.approx(1.0, abs=0.2)


def test_predictor_trim_never_exceeds_the_requested_cap(source_clip, tmp_path):
    """Regression for the live 2026-08-15 finding: a naive `-t 14.9` on a 24fps source re-encoded
    to 14.916667s (ffmpeg rounds a partial final frame UP), landing OVER the cap it was meant to
    enforce. The fixed_source_clip fixture is 24fps — pick a duration_s that isn't already frame-
    aligned (2.9s = 69.6 frames at 24fps) so an unfixed regression would reproduce here too."""
    dst = tmp_path / "trimmed-2.9.mp4"
    vf.predictor_trim(source_clip, dst, duration_s=2.9)
    duration = float(_probe(dst)["format"]["duration"])
    assert duration <= 2.9, (
        f"predictor_trim produced {duration}s, over the requested 2.9s cap — the frame-rounding "
        "fix regressed"
    )


def test_execute_refuses_a_plan_with_a_cuts_stage_rather_than_silently_dropping_it(
    source_clip, tmp_path
):
    """Phase A ships cuts=[] only — execute() has no cuts implementation. A plan carrying a cuts
    stage must never be silently claimed as fully executed."""
    p = vf.plan(
        profile="acme",
        slug="asset8",
        ratio="9:16",
        source=str(source_clip),
        spec={"cuts": [{"start": 0.0, "end": 1.0}]},
    )
    with pytest.raises(NotImplementedError, match="cuts"):
        vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)


def test_ratio_slug_never_leaves_a_colon_in_a_written_filename(source_clip, tmp_path):
    p = vf.plan(profile="acme", slug="asset7", ratio="9:16", source=str(source_clip), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    assert ":" not in result.out_path.name
    assert ":" not in result.sidecar_path.name


def test_execute_with_disclosure_line_does_not_extend_duration(source_clip, tmp_path):
    """The direct regression test for the black-tail defect: a disclosure_line must NOT grow the
    finished asset past the source's own duration — it burns onto existing frames, never appends
    a segment. source_clip is a 3s fixture; the finished asset must stay ~3s either way."""
    spec_plain = {"caption_text": "one two three four", "total_s": 3.0}
    spec_disclosed = {**spec_plain, "disclosure_line": "Made with AI."}

    p_plain = vf.plan(
        profile="acme", slug="asset-plain", ratio="9:16", source=str(source_clip), spec=spec_plain
    )
    p_disclosed = vf.plan(
        profile="acme",
        slug="asset-disclosed",
        ratio="9:16",
        source=str(source_clip),
        spec=spec_disclosed,
    )

    result_plain = vf.execute(
        p_plain,
        workdir=tmp_path / "work-plain",
        out_dir=tmp_path / "out-plain",
        kit=_kit(),
        repo_root=REPO_ROOT,
    )
    result_disclosed = vf.execute(
        p_disclosed,
        workdir=tmp_path / "work-disc",
        out_dir=tmp_path / "out-disc",
        kit=_kit(),
        repo_root=REPO_ROOT,
    )

    duration_plain = float(_probe(result_plain.out_path)["format"]["duration"])
    duration_disclosed = float(_probe(result_disclosed.out_path)["format"]["duration"])
    assert duration_disclosed == pytest.approx(duration_plain, abs=0.2)

    # The disclosure caption's own PNG exists and rendered as the last screen.
    sidecar = json.loads((tmp_path / "out-disc" / "finish-9x16.json").read_text())
    assert sidecar["captions"]["screens"][-1]["text"] == "Made with AI."
