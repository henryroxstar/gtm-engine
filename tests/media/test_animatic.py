"""C12 — building the animatic with real pixels.

PRD test ids C12-T1 (the output is as long as the shots say), C12-T2 (changing one duration moves
only that shot), C12-T3 (a read that does not fit fails without leaving a partial file), C12-T4
(no VO still builds, with the captions burned so the beat is legible), C12-T5 (each verdict check
fails on its own fixture) and C12-T6 (it can only be written under `build/`).

Everything runs under `tmp_path`. Nothing here calls a provider — that is the whole point of the
animatic, and a poisoned httpx asserts it rather than trusting it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core.animatic import AnimaticError, build
from gtm_core.video_finish.stills import ANIMATIC_FPS, still_to_segment

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")

FRAME = 1.0 / ANIMATIC_FPS


def _still(path: Path, colour: str, w: int = 216, h: int = 384) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:s={w}x{h}:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _tone(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:duration={seconds}",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _streams(path: Path) -> list[str]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    # The animatic confines every read and write to the CONTENT ROOT (review finding: it used to
    # confine only to run_dir, which the caller supplies). Bind the root to tmp_path so the
    # fixture tree is inside it, exactly as a real run's is.
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    d = tmp_path / "video" / "2026-09-07-demo"
    (d / "build").mkdir(parents=True)
    return d


def _fixture(run_dir: Path, durations=(2.0, 3.0, 1.5)):
    colours = ("red", "green", "blue")
    for i, colour in enumerate(colours[: len(durations)], 1):
        _still(run_dir / f"storyboard-{i:02d}.png", colour)
    storyboard = {
        "entries": [
            {"shot": i, "image_path": f"storyboard-{i:02d}.png", "aspect_ratio": "9:16"}
            for i in range(1, len(durations) + 1)
        ]
    }
    shots = {
        "total_duration_s": sum(durations),
        "shots": [{"n": i, "duration_s": d} for i, d in enumerate(durations, 1)],
    }
    return storyboard, shots


# ── C12-T1 / C12-T2: the output is as long as the shots say ───────────────────────────────────


def test_the_animatic_is_as_long_as_the_shot_durations_sum_to(run_dir):
    """C12-T1. Three stills at 2.0 / 3.0 / 1.5 make a 6.5s film, within one frame."""
    storyboard, shots = _fixture(run_dir)
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    out = Path(report["path"])
    assert out.is_file()
    assert _duration(out) == pytest.approx(6.5, abs=FRAME * 2)
    assert report["total_s"] == 6.5


def test_changing_one_duration_moves_only_that_shot(run_dir):
    """C12-T2. The property that makes the animatic worth watching: it answers a timing question
    with the timings the film will actually have."""
    storyboard, shots = _fixture(run_dir)
    before = _duration(Path(build(storyboard=storyboard, shots=shots, run_dir=run_dir)["path"]))

    shots["shots"][1]["duration_s"] = 4.0
    shots["total_duration_s"] = 7.5
    after = _duration(Path(build(storyboard=storyboard, shots=shots, run_dir=run_dir)["path"]))
    assert after - before == pytest.approx(1.0, abs=FRAME * 2)


def test_a_single_still_segment_is_exactly_as_long_as_asked(run_dir):
    """The building block, measured on its own so a stitch bug cannot be blamed on it."""
    still = _still(run_dir / "one.png", "orange")
    seg = still_to_segment(still, len_s=2.0, ratio="9:16", out_path=run_dir / "build" / "s.mp4")
    assert _duration(seg) == pytest.approx(2.0, abs=FRAME * 2)
    assert _streams(seg) == ["video", "audio"], (
        "a segment with no audio stream desyncs a concat that expects one"
    )


def test_a_still_is_letterboxed_into_the_ratio_rather_than_stretched(run_dir):
    """Storyboard stills arrive at whatever size the provider coerced them to."""
    from gtm_core.video_lint import SAFE_AREAS

    wide = _still(run_dir / "wide.png", "purple", w=400, h=100)
    seg = still_to_segment(wide, len_s=1.0, ratio="9:16", out_path=run_dir / "build" / "w.mp4")
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(seg),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = (int(x) for x in out.stdout.strip().split(","))
    area = SAFE_AREAS["9:16"]
    assert (w, h) == (area.width, area.height)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_non_positive_length_is_refused(run_dir, bad):
    still = _still(run_dir / "x.png", "red")
    with pytest.raises(ValueError, match="len_s"):
        still_to_segment(still, len_s=bad, ratio="9:16", out_path=run_dir / "build" / "x.mp4")


def test_an_unknown_ratio_names_the_one_place_ratios_live(run_dir):
    still = _still(run_dir / "x.png", "red")
    with pytest.raises(ValueError, match="SAFE_AREAS"):
        still_to_segment(still, len_s=1.0, ratio="7:3", out_path=run_dir / "build" / "x.mp4")


# ── C12-T4: no VO still builds ────────────────────────────────────────────────────────────────


def test_with_no_narration_it_builds_a_silent_animatic_and_says_so(run_dir):
    """C12-T4. The common case at the storyboard gate: the stills exist, the VO does not yet."""
    storyboard, shots = _fixture(run_dir)
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    assert report["read"] == "absent"
    assert report["hook_in_first_beat"] is None
    assert "audio" in _streams(Path(report["path"])), (
        "even the silent animatic carries a track — a video-only file desyncs downstream"
    )


# ── C12-T3: a read that does not fit fails cleanly ────────────────────────────────────────────


def test_a_narration_line_longer_than_its_file_leaves_no_partial_animatic(run_dir):
    """C12-T3. The failure must not leave something that looks like a finished animatic."""
    storyboard, shots = _fixture(run_dir, durations=(2.0, 2.0))
    _tone(run_dir / "vo-1.wav", 0.5)
    shots["narration"] = {
        "lines": [
            {"line": "the first line", "voice_onset_s": 0.2, "len_s": 9.0, "file": "vo-1.wav"},
        ]
    }
    with pytest.raises(Exception):
        build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    assert not (run_dir / "build" / "animatic.mp4").exists(), (
        "a failed build left a file that reads as a finished animatic"
    )


# ── C12-T6: it can only be written under build/ ───────────────────────────────────────────────


def test_the_animatic_refuses_to_be_written_into_deliver(run_dir):
    """C12-T6. Anything under deliver/ is a candidate for shipping, and this is six stills."""
    storyboard, shots = _fixture(run_dir, durations=(1.0,))
    (run_dir / "deliver").mkdir(exist_ok=True)
    with pytest.raises(AnimaticError, match="only be written under"):
        build(
            storyboard=storyboard,
            shots=shots,
            run_dir=run_dir,
            out_path=run_dir / "deliver" / "animatic.mp4",
        )


@pytest.mark.parametrize("target", ["../escape.mp4", "build/../deliver/x.mp4"])
def test_a_path_outside_the_build_directory_is_refused(run_dir, target):
    storyboard, shots = _fixture(run_dir, durations=(1.0,))
    with pytest.raises(AnimaticError):
        build(storyboard=storyboard, shots=shots, run_dir=run_dir, out_path=run_dir / target)


def test_a_path_inside_build_is_accepted(run_dir):
    """Positive control for both refusals above."""
    storyboard, shots = _fixture(run_dir, durations=(1.0,))
    report = build(
        storyboard=storyboard,
        shots=shots,
        run_dir=run_dir,
        out_path=run_dir / "build" / "named.mp4",
    )
    assert Path(report["path"]).name == "named.mp4"


# ── the verdict is written beside the film, and nothing is bought ─────────────────────────────


def test_the_verdict_is_written_to_build_animatic_json(run_dir):
    storyboard, shots = _fixture(run_dir, durations=(1.0, 1.0))
    build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    saved = json.loads((run_dir / "build" / "animatic.json").read_text())
    assert saved["shots"] == 2 and saved["total_s"] == 2.0


def test_building_an_animatic_spends_nothing(run_dir, monkeypatch):
    """Zero render spend is the claim the whole workstream rests on, so it is asserted."""
    import httpx

    def _boom(*a, **k):  # pragma: no cover — the point is that it never runs
        raise AssertionError("the animatic made a network call")

    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    storyboard, shots = _fixture(run_dir, durations=(1.0,))
    build(storyboard=storyboard, shots=shots, run_dir=run_dir)


def test_the_mp4_carries_no_identity_marker_in_its_metadata(run_dir):
    """C12-T7's pixel half: an animatic must not be able to declare synthetic identity."""
    storyboard, shots = _fixture(run_dir, durations=(1.0,))
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", report["path"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "IDENTITY" not in out.stdout


# ── C12-T5: each verdict check fails on its own fixture ───────────────────────────────────────


def test_a_cover_that_is_not_the_first_frame_is_reported(run_dir):
    storyboard, shots = _fixture(run_dir, durations=(1.0, 1.0))
    brief = {"decisions": {"cover": {"value": {"still": "storyboard-02.png"}}}}
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir, brief=brief)
    assert report["cover_is_frame_one"] is False


def test_a_matching_cover_is_reported_as_frame_one(run_dir):
    storyboard, shots = _fixture(run_dir, durations=(1.0, 1.0))
    brief = {"decisions": {"cover": {"value": {"still": "storyboard-01.png"}}}}
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir, brief=brief)
    assert report["cover_is_frame_one"] is True


def test_a_declared_total_that_disagrees_with_the_sum_is_reported(run_dir):
    storyboard, shots = _fixture(run_dir, durations=(1.0, 1.0))
    shots["total_duration_s"] = 5.0
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    assert report["declared_total_vs_sum"]["agrees"] is False


def test_a_narration_track_is_muxed_onto_the_picture(run_dir):
    """The positive control the failure tests above would otherwise hide: when the read exists and
    fits, it reaches the file, and the verdict answers the two questions it could not before."""
    storyboard, shots = _fixture(run_dir, durations=(3.0, 3.0))
    _tone(run_dir / "vo-1.wav", 1.0)
    _tone(run_dir / "vo-2.wav", 1.0)
    shots["narration"] = {
        "lines": [
            {"line": "the hook", "voice_onset_s": 0.3, "len_s": 1.0, "file": "vo-1.wav"},
            {"line": "the payoff", "voice_onset_s": 3.4, "len_s": 1.0, "file": "vo-2.wav"},
        ]
    }
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)

    assert report["read"] == "present"
    assert report["hook_in_first_beat"] is True, "a hook at 0.3s is inside a 3.0s first shot"
    assert report["read_fits"] is True and report["tight_lines"] == []
    assert "audio" in _streams(Path(report["path"]))
    assert _duration(Path(report["path"])) == pytest.approx(6.0, abs=0.2)


def test_a_hook_that_lands_after_the_first_shot_is_reported_on_a_real_build(run_dir):
    """The same measurement path, on the case it exists to catch."""
    storyboard, shots = _fixture(run_dir, durations=(1.0, 5.0))
    _tone(run_dir / "vo-1.wav", 1.0)
    shots["narration"] = {
        "lines": [{"line": "late", "voice_onset_s": 2.5, "len_s": 1.0, "file": "vo-1.wav"}]
    }
    report = build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    assert report["hook_in_first_beat"] is False, (
        "a hook at 2.5s is outside a 1.0s first shot and must be reported"
    )


# ── the content-root boundary (review finding) ────────────────────────────────────────────────


def test_a_still_outside_the_content_root_is_refused_before_any_encode(run_dir, tmp_path):
    """A storyboard entry can name an absolute path; that path may not leave the root."""
    from gtm_core.confine import ConfinementError

    elsewhere = _still(tmp_path.parent / f"{tmp_path.name}-other" / "01.png", "red")
    storyboard = {"entries": [{"shot": 1, "image_path": str(elsewhere), "aspect_ratio": "9:16"}]}
    shots = {"total_duration_s": 1.0, "shots": [{"n": 1, "duration_s": 1.0}]}
    with pytest.raises(ConfinementError):
        build(storyboard=storyboard, shots=shots, run_dir=run_dir)
    assert not (run_dir / "build" / "animatic.mp4").exists()


def test_a_run_dir_outside_the_content_root_is_refused(tmp_path, monkeypatch):
    """`--run-dir /tmp/x` used to write a tenant's preview outside every content root."""
    from gtm_core.confine import ConfinementError

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "root"))
    (tmp_path / "root").mkdir()
    outside = tmp_path / "elsewhere" / "run"
    outside.mkdir(parents=True)
    _still(outside / "storyboard-01.png", "red")
    storyboard = {
        "entries": [{"shot": 1, "image_path": "storyboard-01.png", "aspect_ratio": "9:16"}]
    }
    shots = {"total_duration_s": 1.0, "shots": [{"n": 1, "duration_s": 1.0}]}
    with pytest.raises(ConfinementError):
        build(storyboard=storyboard, shots=shots, run_dir=outside)


def test_the_animatic_pads_with_the_masters_own_letterbox_chain():
    """Review finding: three copies of the scale/pad chain. The animatic previews the master's
    framing, so the two must be one function or the preview can lie."""
    from gtm_core.video_finish.ratio import letterbox_vf, safe_area_or_raise
    from gtm_core.video_lint import SAFE_AREAS

    area = safe_area_or_raise("9:16")
    assert area is SAFE_AREAS["9:16"]
    chain = letterbox_vf(area)
    assert chain.startswith(
        f"scale={area.width}:{area.height}:force_original_aspect_ratio=decrease,pad="
    )
    assert ":color=" not in chain and letterbox_vf(area, background="black").endswith(
        ":color=black,setsar=1"
    )
