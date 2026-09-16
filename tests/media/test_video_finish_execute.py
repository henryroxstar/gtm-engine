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


# --- a pre-burned source: its caption geometry reaches the finish manifest ---------------------


def _make_source(path: Path, *, w: int, h: int, duration: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size={w}x{h}:rate=24",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_execute_ingests_a_preburned_caption_sidecar_into_the_finish_manifest(tmp_path):
    """The chain that was missing: a stitched, pre-burned master used to finish with
    `captions: null` / `caption_route: "none"`, so `video_lint --manifest` had no geometry and
    the contrast tier silently never ran. Now the manifest carries the (scaled) geometry and the
    tier has something to sample."""
    from gtm_core import render_manifest as rm
    from gtm_core.video_lint.measure import measure_caption_contrast

    src = _make_source(tmp_path / "master.mp4", w=1076, h=1926)
    box = {"x": 538, "y": 1600, "w": 538, "h": 100}
    (tmp_path / "master.captions.json").write_text(
        json.dumps(
            {
                "frame": [1076, 1926],
                "screens": [
                    {"index": 0, "text": "hi", "start_s": 0.0, "end_s": 1.0, "box": box},
                    {"index": 1, "text": "there", "start_s": 1.0, "end_s": 2.0, "box": box},
                ],
            }
        )
    )
    (tmp_path / "master.overlays.json").write_text(
        json.dumps(
            {
                "frame": [1076, 1926],
                "overlays": [{"kind": "k", "start_s": 0.0, "end_s": 0.5, "box": box}],
            }
        )
    )
    p = vf.plan(profile="acme", slug="pre", ratio="9:16", source=str(src), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)

    assert result.executed and result.caption_manifest_path is None  # nothing was rendered
    manifest = json.loads(result.sidecar_path.read_text())
    assert manifest["caption_route"] == "local"
    assert manifest["captions_preburned"] is True
    assert manifest["captions"]["frame"] == [1080, 1920]
    scaled = manifest["captions"]["screens"][0]["box"]
    assert scaled == {"x": 540, "y": 1595, "w": 540, "h": 100}
    assert manifest["overlays"][0]["box"] == scaled
    assert "captions" not in manifest["stages"]
    assert rm.load_finish(result.sidecar_path).captions_preburned is True

    # The tier that never ran can run now: it samples each screen's midpoint inside its box.
    samples = measure_caption_contrast(result.out_path, manifest["captions"])
    assert samples is not None
    assert [s["at_s"] for s in samples] == [pytest.approx(0.5), pytest.approx(1.5)]


def test_execute_without_a_sidecar_still_writes_captions_null(source_clip, tmp_path):
    p = vf.plan(profile="acme", slug="plain", ratio="9:16", source=str(source_clip), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    manifest = json.loads(result.sidecar_path.read_text())
    assert manifest["captions"] is None
    assert manifest["captions_preburned"] is False and manifest["overlays"] is None
    assert manifest["caption_route"] == "none"


# ── #1: run keeps suppressions across a re-run; only for the matching asset ─────────────────────


def test_execute_carries_a_valid_suppression_across_a_replan_with_a_new_plan_id(
    source_clip, tmp_path
):
    """The bug this guards: a re-run whose plan_id changed (a caption edit, a grade tweak) used
    to overwrite the manifest with zero suppressions, so V1/V10 fired again on findings a human
    had already reviewed and accepted."""
    out_dir = tmp_path / "out"
    p1 = vf.plan(profile="acme", slug="carry", ratio="9:16", source=str(source_clip), spec={})
    result1 = vf.execute(p1, workdir=tmp_path / "work1", out_dir=out_dir, repo_root=REPO_ROOT)
    manifest = json.loads(result1.sidecar_path.read_text())
    suppression = {
        "tier": "V9",
        "asset": result1.out_path.name,
        "reason": "static hero shot is intentional",
    }
    manifest["lint_suppressions"] = [suppression]
    result1.sidecar_path.write_text(json.dumps(manifest))

    p2 = vf.plan(
        profile="acme",
        slug="carry",
        ratio="9:16",
        source=str(source_clip),
        spec={"caption_text": "a new caption changes the plan id", "total_s": 1.0},
    )
    assert p2.plan_id != p1.plan_id
    result2 = vf.execute(
        p2, workdir=tmp_path / "work2", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT
    )
    manifest2 = json.loads(result2.sidecar_path.read_text())
    assert manifest2["lint_suppressions"] == [suppression]


def test_execute_does_not_carry_a_suppression_scoped_to_a_different_asset(source_clip, tmp_path):
    out_dir = tmp_path / "out"
    p1 = vf.plan(profile="acme", slug="carry2", ratio="9:16", source=str(source_clip), spec={})
    result1 = vf.execute(p1, workdir=tmp_path / "work1", out_dir=out_dir, repo_root=REPO_ROOT)
    manifest = json.loads(result1.sidecar_path.read_text())
    manifest["lint_suppressions"] = [
        {"tier": "V9", "asset": "a-completely-different-final.mp4", "reason": "not this asset"}
    ]
    result1.sidecar_path.write_text(json.dumps(manifest))

    p2 = vf.plan(
        profile="acme",
        slug="carry2",
        ratio="9:16",
        source=str(source_clip),
        spec={"caption_text": "now with captions", "total_s": 1.0},
    )
    result2 = vf.execute(
        p2, workdir=tmp_path / "work2", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT
    )
    manifest2 = json.loads(result2.sidecar_path.read_text())
    assert manifest2["lint_suppressions"] == []


def test_execute_ffmpeg_missing_branch_also_carries_suppressions(
    source_clip, tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    p1 = vf.plan(profile="acme", slug="carry3", ratio="9:16", source=str(source_clip), spec={})
    result1 = vf.execute(p1, workdir=tmp_path / "work1", out_dir=out_dir, repo_root=REPO_ROOT)
    manifest = json.loads(result1.sidecar_path.read_text())
    suppression = {
        "tier": "V1",
        "asset": result1.out_path.name,
        "reason": "resolution is intentionally low",
    }
    manifest["lint_suppressions"] = [suppression]
    result1.sidecar_path.write_text(json.dumps(manifest))

    p2 = vf.plan(
        profile="acme",
        slug="carry3",
        ratio="9:16",
        source=str(source_clip),
        spec={"caption_text": "trigger a new plan_id", "total_s": 1.0},
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.execute(p2, workdir=tmp_path / "work2", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT)
    manifest2 = json.loads((out_dir / "finish-9x16.json").read_text())
    assert manifest2["lint_suppressions"] == [suppression]


def test_execute_carries_nothing_from_a_malformed_prior_sidecar_and_does_not_raise(
    source_clip, tmp_path
):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "finish-9x16.json").write_text("{not valid json")
    p = vf.plan(profile="acme", slug="malformed", ratio="9:16", source=str(source_clip), spec={})
    result = vf.execute(p, workdir=tmp_path / "work", out_dir=out_dir, repo_root=REPO_ROOT)
    manifest = json.loads(result.sidecar_path.read_text())
    assert manifest["lint_suppressions"] == []


def test_execute_plan_id_skip_leaves_the_manifest_byte_identical(source_clip, tmp_path):
    out_dir = tmp_path / "out"
    p = vf.plan(profile="acme", slug="skip-check", ratio="9:16", source=str(source_clip), spec={})
    result1 = vf.execute(p, workdir=tmp_path / "work", out_dir=out_dir, repo_root=REPO_ROOT)
    before = result1.sidecar_path.read_bytes()
    result2 = vf.execute(p, workdir=tmp_path / "work", out_dir=out_dir, repo_root=REPO_ROOT)
    assert result2.skipped
    assert result1.sidecar_path.read_bytes() == before


def test_execute_prints_a_stderr_line_naming_carried_tiers(source_clip, tmp_path, capsys):
    out_dir = tmp_path / "out"
    p1 = vf.plan(
        profile="acme", slug="stderr-carry", ratio="9:16", source=str(source_clip), spec={}
    )
    result1 = vf.execute(p1, workdir=tmp_path / "work1", out_dir=out_dir, repo_root=REPO_ROOT)
    manifest = json.loads(result1.sidecar_path.read_text())
    manifest["lint_suppressions"] = [
        {"tier": "V1", "asset": result1.out_path.name, "reason": "a legit reason for V1 here"},
        {"tier": "V10", "asset": result1.out_path.name, "reason": "a legit reason for V10 here"},
    ]
    result1.sidecar_path.write_text(json.dumps(manifest))
    capsys.readouterr()

    p2 = vf.plan(
        profile="acme",
        slug="stderr-carry",
        ratio="9:16",
        source=str(source_clip),
        spec={"caption_text": "changes the plan_id", "total_s": 1.0},
    )
    vf.execute(p2, workdir=tmp_path / "work2", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT)
    err = capsys.readouterr().err
    assert "carried 2 lint suppression(s)" in err
    assert "V1" in err and "V10" in err
    assert "video_lint --no-suppress" in err


def test_execute_is_silent_on_stderr_when_nothing_was_carried(source_clip, tmp_path, capsys):
    p = vf.plan(profile="acme", slug="stderr-quiet", ratio="9:16", source=str(source_clip), spec={})
    capsys.readouterr()
    vf.execute(p, workdir=tmp_path / "work", out_dir=tmp_path / "out", repo_root=REPO_ROOT)
    err = capsys.readouterr().err
    assert "carried" not in err


# ── _carried_suppressions: pure, no ffmpeg needed ────────────────────────────────────────────────


def test_carried_suppressions_returns_empty_when_sidecar_is_missing(tmp_path):
    assert vf._carried_suppressions(tmp_path / "does-not-exist.json", "asset.mp4") == []


def test_carried_suppressions_returns_empty_on_malformed_json(tmp_path):
    sidecar = tmp_path / "finish-9x16.json"
    sidecar.write_text("{not json")
    assert vf._carried_suppressions(sidecar, "asset.mp4") == []


def test_carried_suppressions_returns_empty_when_lint_suppressions_is_not_a_list(tmp_path):
    sidecar = tmp_path / "finish-9x16.json"
    sidecar.write_text(json.dumps({"lint_suppressions": "not-a-list"}))
    assert vf._carried_suppressions(sidecar, "asset.mp4") == []


def test_carried_suppressions_drops_non_dict_entries(tmp_path):
    sidecar = tmp_path / "finish-9x16.json"
    sidecar.write_text(
        json.dumps(
            {
                "lint_suppressions": [
                    {"tier": "V9", "asset": "asset.mp4", "reason": "a real reason here"},
                    "not-a-dict",
                    123,
                    None,
                ]
            }
        )
    )
    assert vf._carried_suppressions(sidecar, "asset.mp4") == [
        {"tier": "V9", "asset": "asset.mp4", "reason": "a real reason here"}
    ]


def test_carried_suppressions_top_level_not_a_dict_carries_nothing(tmp_path):
    sidecar = tmp_path / "finish-9x16.json"
    sidecar.write_text(json.dumps([1, 2, 3]))
    assert vf._carried_suppressions(sidecar, "asset.mp4") == []


# ── #2: run saves the spec it was given ──────────────────────────────────────────────────────────


def test_execute_writes_a_spec_sidecar_beside_the_manifest(source_clip, tmp_path):
    spec = {"caption_text": "spec sidecar check", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec1", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    spec_path = out_dir / "finish-spec-9x16.json"
    assert spec_path.is_file()
    saved = json.loads(spec_path.read_text())
    assert saved["caption_text"] == "spec sidecar check"
    assert saved["_invocation"]["source"] == str(source_clip)


def test_execute_writes_a_spec_sidecar_on_the_ffmpeg_missing_branch_too(
    source_clip, tmp_path, monkeypatch
):
    spec = {"caption_text": "x", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec2", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.execute(
            p,
            workdir=tmp_path / "work",
            out_dir=out_dir,
            kit=_kit(),
            repo_root=REPO_ROOT,
            spec=spec,
        )
    assert (out_dir / "finish-spec-9x16.json").is_file()


def test_execute_does_not_write_a_spec_sidecar_when_spec_is_none(source_clip, tmp_path):
    p = vf.plan(profile="acme", slug="spec3", ratio="9:16", source=str(source_clip), spec={})
    out_dir = tmp_path / "out"
    vf.execute(p, workdir=tmp_path / "work", out_dir=out_dir, repo_root=REPO_ROOT)
    assert not (out_dir / "finish-spec-9x16.json").exists()


def test_execute_does_not_write_a_spec_sidecar_on_dry_run_because_execute_is_never_called(
    source_clip, tmp_path
):
    """--dry-run never reaches execute() at all (the CLI prints plan.to_json() and returns) — this
    pins the FinishPlan preview alone carries no spec-file side effect to guard against."""
    spec = {"caption_text": "dry run", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec-dry", ratio="9:16", source=str(source_clip), spec=spec)
    assert "finish-spec-9x16.json" not in json.dumps(p.to_json())


def test_execute_does_not_rewrite_the_spec_sidecar_on_a_plan_id_skip(source_clip, tmp_path):
    spec = {"caption_text": "skip check", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec4", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    spec_path = out_dir / "finish-spec-9x16.json"
    before = spec_path.read_bytes()
    before_mtime = spec_path.stat().st_mtime_ns
    result2 = vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    assert result2.skipped
    assert spec_path.read_bytes() == before
    assert spec_path.stat().st_mtime_ns == before_mtime


def test_execute_failed_encode_leaves_the_previous_spec_file_byte_identical(
    source_clip, tmp_path, monkeypatch
):
    # `gtm_core.video_finish.execute` (the attribute) is the re-exported FUNCTION —
    # `gtm_core.video_finish.__init__` rebinds that name to it, and `import a.b.c as x` resolves
    # via attribute access (`a.b.c`), so it would hand back the function too. The submodule that
    # actually owns `_run_ffmpeg`'s call site is reached only through sys.modules directly.
    import importlib

    vf_execute = importlib.import_module("gtm_core.video_finish.execute")

    spec1 = {"caption_text": "first run", "total_s": 1.0}
    p1 = vf.plan(profile="acme", slug="spec5", ratio="9:16", source=str(source_clip), spec=spec1)
    out_dir = tmp_path / "out"
    vf.execute(
        p1, workdir=tmp_path / "work1", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec1
    )
    spec_path = out_dir / "finish-spec-9x16.json"
    before = spec_path.read_bytes()

    spec2 = {"caption_text": "second run should fail mid-encode", "total_s": 1.0}
    p2 = vf.plan(profile="acme", slug="spec5", ratio="9:16", source=str(source_clip), spec=spec2)

    def _boom(args):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(vf_execute, "_run_ffmpeg", _boom)
    with pytest.raises(subprocess.CalledProcessError):
        vf.execute(
            p2,
            workdir=tmp_path / "work2",
            out_dir=out_dir,
            kit=_kit(),
            repo_root=REPO_ROOT,
            spec=spec2,
        )
    assert spec_path.read_bytes() == before


def test_execute_spec_sidecar_round_trips_through_plan_without_perturbing_plan_id(
    source_clip, tmp_path
):
    spec = {"caption_text": "round trip check", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec6", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    result = vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    saved_spec = json.loads((out_dir / "finish-spec-9x16.json").read_text())
    assert "_invocation" in saved_spec
    replayed = vf.plan(
        profile="acme", slug="spec6", ratio="9:16", source=str(source_clip), spec=saved_spec
    )
    manifest = json.loads(result.sidecar_path.read_text())
    assert replayed.plan_id == manifest["plan_id"] == p.plan_id


def test_execute_self_overwrite_spec_is_a_noop_write(source_clip, tmp_path, monkeypatch):
    """Replaying `--spec` with the exact file `run` already wrote there must not error, and must
    not needlessly rewrite a file whose content is already exactly what would be written — the
    ffmpeg-missing branch is used because it (unlike the executed branch) re-runs _write_manifest
    on every call regardless of plan_id, so two identical calls are a genuine repeat write."""
    spec = {"caption_text": "self overwrite", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec7", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.execute(
            p,
            workdir=tmp_path / "work",
            out_dir=out_dir,
            kit=_kit(),
            repo_root=REPO_ROOT,
            spec=spec,
        )
    spec_path = out_dir / "finish-spec-9x16.json"
    before_mtime = spec_path.stat().st_mtime_ns
    with pytest.raises(vf.FfmpegUnavailable):
        vf.execute(
            p,
            workdir=tmp_path / "work",
            out_dir=out_dir,
            kit=_kit(),
            repo_root=REPO_ROOT,
            spec=spec,
        )
    assert spec_path.stat().st_mtime_ns == before_mtime  # not rewritten — no .tmp, no replace
    assert list(out_dir.glob("*.tmp*")) == []


def test_execute_spec_sidecar_round_trips_non_ascii_and_leaves_no_tmp_file(source_clip, tmp_path):
    spec = {"caption_text": "café — naïve façade", "total_s": 1.0}
    p = vf.plan(profile="acme", slug="spec8", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    spec_path = out_dir / "finish-spec-9x16.json"
    raw = spec_path.read_text(encoding="utf-8")
    assert "café" in raw and "\\u" not in raw  # ensure_ascii=False — not escaped
    assert json.loads(raw)["caption_text"] == "café — naïve façade"
    assert list(out_dir.glob("*.tmp*")) == []


def test_execute_spec_sidecar_records_invocation_source_and_optional_product(source_clip, tmp_path):
    spec = {"caption_text": "x", "total_s": 1.0, "product": "widgetco"}
    p = vf.plan(profile="acme", slug="spec9", ratio="9:16", source=str(source_clip), spec=spec)
    out_dir = tmp_path / "out"
    vf.execute(
        p, workdir=tmp_path / "work", out_dir=out_dir, kit=_kit(), repo_root=REPO_ROOT, spec=spec
    )
    saved = json.loads((out_dir / "finish-spec-9x16.json").read_text())
    assert saved["_invocation"] == {"source": str(source_clip), "product": "widgetco"}
