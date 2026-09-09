"""``gtm_core.video_finish.narration`` — the reader that makes the ``narration`` lane a contract.

The lane landed in ``schemas/shots.schema.json`` with a linter and no consumer, which is a field
with no consequence: the onsets that placed the 2026-09-04 film lived in a production note and in
somebody's shell history, and the mux got them by hand. This module builds the VO master FROM the
shot list, so the declared numbers are checked against the bytes and a stale one is an error
rather than a comment that quietly rots.

Against real ffmpeg (see this directory's conftest): the placement assertions MEASURE where the
speech landed rather than reading the filtergraph back, for the same reason ``mix_sfx_cues``
does — a graph that says ``adelay=800`` and a file whose word starts at 1.0s are different
claims, and only the second one is the one that ships.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core.video_finish import narration as nr
from gtm_core.video_finish.errors import FfmpegUnavailable
from gtm_core.video_finish.ffmpeg import _measure_window_dbfs, _probe_duration

#: Anything this loud in a 100 ms window is speech; the base is digital silence, so the two are
#: not close. Deliberately far from both, so the assertions do not encode a threshold.
_LOUD_DBFS = -30.0
_SILENT_DBFS = -70.0


def _clip(path: Path, *, lead_in_s: float, len_s: float, tail_s: float = 0.1) -> Path:
    """A stand-in for a rendered VO line: silence, a tone, silence.

    The SHAPE is the point — a TTS clip is never flush at either end, which is the fact that made
    ``lead_in_s`` necessary and that a clip generated flush would quietly stop testing.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={len_s}",
            "-af", f"adelay={int(lead_in_s * 1000)}|{int(lead_in_s * 1000)},apad=pad_dur={tail_s}",
            "-ac", "2", "-ar", "48000", str(path),
        ],
        check=True, capture_output=True,
    )  # fmt: skip
    return path


@pytest.fixture
def read_dir(tmp_path: Path) -> Path:
    """Two lines, each 0.20s of lead-in over a 0.50s word, in a base dir they are relative to."""
    d = tmp_path / "film"
    d.mkdir()
    _clip(d / "l1.wav", lead_in_s=0.20, len_s=0.50)
    _clip(d / "l2.wav", lead_in_s=0.20, len_s=0.50)
    return d


def _lines(**overrides) -> list[nr.NarrationLine]:
    base = [
        nr.NarrationLine(line="one", voice_onset_s=1.0, len_s=0.5, file="l1.wav", lead_in_s=0.2),
        nr.NarrationLine(line="two", voice_onset_s=2.0, len_s=0.5, file="l2.wav", lead_in_s=0.2),
    ]
    if overrides:
        base[0] = nr.NarrationLine(**{**base[0].__dict__, **overrides})
    return base


# --- the build ------------------------------------------------------------------------------


def test_the_track_is_exactly_the_length_of_the_cut(read_dir: Path, tmp_path: Path):
    out = tmp_path / "vo.m4a"
    result = nr.build_narration_track(
        _lines(), base_dir=read_dir, out_path=out, total_duration_s=4.0
    )
    assert out.is_file()
    assert _probe_duration(out, stream_selector="a:0") == pytest.approx(4.0, abs=0.05)
    assert result.duration_s == 4.0


def test_each_word_lands_where_the_shot_list_says_it_does(read_dir: Path, tmp_path: Path):
    """The property the whole module exists for, measured rather than read off the graph."""
    out = tmp_path / "vo.m4a"
    nr.build_narration_track(_lines(), base_dir=read_dir, out_path=out, total_duration_s=4.0)
    assert _measure_window_dbfs(out, start_s=1.05, duration_s=0.1) > _LOUD_DBFS
    assert _measure_window_dbfs(out, start_s=2.05, duration_s=0.1) > _LOUD_DBFS


def test_the_clips_lead_in_does_not_push_its_word_late(read_dir: Path, tmp_path: Path):
    """The defect real data found. Delayed by the bare onset, this clip's word would start at
    1.2s and the window just before the onset would be silent; delayed by ``onset - lead_in`` it
    starts at 1.0s. Asserting the word IS there at 1.0 is what separates the two."""
    out = tmp_path / "vo.m4a"
    nr.build_narration_track(_lines(), base_dir=read_dir, out_path=out, total_duration_s=4.0)
    assert _measure_window_dbfs(out, start_s=1.01, duration_s=0.08) > _LOUD_DBFS
    # ...and nothing before it: the lead-in's silence sits in the gap, not on the word.
    assert _measure_window_dbfs(out, start_s=0.5, duration_s=0.2) < _SILENT_DBFS


def test_the_gaps_between_lines_stay_silent(read_dir: Path, tmp_path: Path):
    """The base is digital silence and stays that way. Room tone, beds and SFX are laid downstream
    against the picture — this track is the sidechain KEY for duck_music_bed, and signal that
    never stops is a duck that never releases."""
    out = tmp_path / "vo.m4a"
    nr.build_narration_track(_lines(), base_dir=read_dir, out_path=out, total_duration_s=4.0)
    for start in (0.2, 1.8, 3.2):
        assert _measure_window_dbfs(out, start_s=start, duration_s=0.15) < _SILENT_DBFS


def test_ten_lines_are_not_quietly_attenuated_by_the_mix(tmp_path: Path):
    """``amix`` divides by its input count unless told otherwise, so a ten-line read would come
    out ~20 dB down — uniformly, so nothing looks wrong until the whole film is quiet. This is the
    lesson duck_music_bed already paid for once, asserted here rather than trusted."""
    d = tmp_path / "many"
    d.mkdir()
    lines = []
    for i in range(10):
        _clip(d / f"l{i}.wav", lead_in_s=0.2, len_s=0.5)
        lines.append(
            nr.NarrationLine(
                line=f"line {i}", voice_onset_s=1.0 + i, len_s=0.5, file=f"l{i}.wav", lead_in_s=0.2
            )
        )
    out = tmp_path / "vo.m4a"
    result = nr.build_narration_track(lines, base_dir=d, out_path=out, total_duration_s=12.0)
    assert "normalize=0" in result.filtergraph
    assert _measure_window_dbfs(out, start_s=1.05, duration_s=0.1) > _LOUD_DBFS


def test_the_duty_cycle_counts_speech_and_not_silence(read_dir: Path, tmp_path: Path):
    """1.0s of speech over a 4s cut is 25%, not the 42% the summed FILE durations would give.
    Overstating it makes a bed look safer than it is, which is the wrong direction for the one
    number duck depth gets chosen from."""
    out = tmp_path / "vo.m4a"
    result = nr.build_narration_track(
        _lines(), base_dir=read_dir, out_path=out, total_duration_s=4.0
    )
    assert result.speech_s == pytest.approx(1.0)
    assert result.speech_duty_pct == pytest.approx(25.0)


# --- what it refuses, before it writes anything ---------------------------------------------


def _refusal(read_dir: Path, out: Path, **overrides) -> str:
    with pytest.raises(nr.NarrationError) as excinfo:
        nr.build_narration_track(
            _lines(**overrides), base_dir=read_dir, out_path=out, total_duration_s=4.0
        )
    assert not out.exists(), "a refused build must not leave a file a caller could mistake for done"
    return str(excinfo.value)


def test_a_span_longer_than_its_clip_is_refused(read_dir: Path, tmp_path: Path):
    """The stale-number case: the line was re-cut and the clip was not, or the reverse."""
    message = _refusal(read_dir, tmp_path / "vo.m4a", len_s=3.0)
    assert "cannot hold the line the shot list says it holds" in message


def test_an_onset_earlier_than_its_own_lead_in_is_refused(read_dir: Path, tmp_path: Path):
    """Placing the word there would need the file to start before the film does. An editorial
    choice (move the onset, or trim the clip), so it is refused rather than silently clamped."""
    message = _refusal(read_dir, tmp_path / "vo.m4a", voice_onset_s=0.1)
    assert "would need the file to begin before the film does" in message


def test_a_line_running_past_the_cut_is_refused(read_dir: Path, tmp_path: Path):
    message = _refusal(read_dir, tmp_path / "vo.m4a", voice_onset_s=3.9)
    assert "ending past the 4s cut" in message


def test_a_missing_clip_is_refused_by_name(read_dir: Path, tmp_path: Path):
    message = _refusal(read_dir, tmp_path / "vo.m4a", file="nope.wav")
    assert "lines[1] (nope.wav)" in message


def test_a_clip_outside_the_base_dir_is_refused(read_dir: Path, tmp_path: Path):
    """Confinement, the same rule every other asset path in this package answers to."""
    outside = tmp_path / "elsewhere.wav"
    _clip(outside, lead_in_s=0.2, len_s=0.5)
    message = _refusal(read_dir, tmp_path / "vo.m4a", file="../elsewhere.wav")
    assert "outside the resolved content root" in message


def test_every_disagreement_is_reported_in_one_pass(read_dir: Path, tmp_path: Path):
    """A read is placed as a whole. Fixing one stale number per ffmpeg round trip is how a
    ten-line film takes ten builds."""
    lines = [
        nr.NarrationLine(line="one", voice_onset_s=1.0, len_s=3.0, file="l1.wav", lead_in_s=0.2),
        nr.NarrationLine(line="two", voice_onset_s=3.9, len_s=0.5, file="l2.wav", lead_in_s=0.2),
    ]
    with pytest.raises(nr.NarrationError) as excinfo:
        nr.build_narration_track(
            lines, base_dir=read_dir, out_path=tmp_path / "vo.m4a", total_duration_s=4.0
        )
    assert "lines[1]" in str(excinfo.value) and "lines[2]" in str(excinfo.value)


def test_a_wrong_suffix_is_refused_before_any_probe(read_dir: Path, tmp_path: Path):
    with pytest.raises(nr.NarrationError, match="must end in .m4a"):
        nr.build_narration_track(
            _lines(), base_dir=read_dir, out_path=tmp_path / "vo.mp3", total_duration_s=4.0
        )


def test_a_missing_ffmpeg_is_named_and_never_skipped(read_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(FfmpegUnavailable):
        nr.build_narration_track(
            _lines(), base_dir=read_dir, out_path=tmp_path / "vo.m4a", total_duration_s=4.0
        )


# --- reading the lane off a shot list --------------------------------------------------------


def _doc(**line_overrides) -> dict:
    line = {"line": "one", "voice_onset_s": 1.0, "len_s": 0.5, "file": "l1.wav"}
    line.update(line_overrides)
    return {"total_duration_s": 4.0, "narration": {"lines": [line]}}


def test_lead_in_defaults_to_zero_when_a_line_declares_none():
    """The field is optional, and a flush clip is a legitimate shape — it must not become a
    required number for the case that does not need it."""
    assert nr.lines_from_shotlist(_doc())[0].lead_in_s == 0.0


def test_a_line_with_no_file_is_refused_with_its_position():
    with pytest.raises(nr.NarrationError, match=r"narration.lines \[1\] name no `file`"):
        nr.lines_from_shotlist(_doc(file=""))


def test_a_shot_list_with_no_narration_lane_says_so():
    with pytest.raises(nr.NarrationError, match="declares no `narration.lines`"):
        nr.lines_from_shotlist({"total_duration_s": 4.0, "shots": []})


# --- the CLI: the lint is a GATE, not a report -----------------------------------------------


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """``--content-root .`` because this package confines every output it writes to the resolved
    content root, and a test's tmp dir is not it. Passing the tmp dir keeps the confinement rule
    in force rather than testing around it."""
    return subprocess.run(
        ["python", "-m", "gtm_core.video_finish", *args, "--content-root", "."],
        cwd=str(cwd), capture_output=True, text=True,
        env={"PATH": __import__("os").environ["PATH"], "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )  # fmt: skip


def _shots_file(path: Path, lines: list[dict], total: float = 4.0) -> Path:
    path.write_text(json.dumps({"total_duration_s": total, "narration": {"lines": lines}}), "utf-8")
    return path


def test_the_cli_builds_a_track_and_reports_what_it_placed(read_dir: Path, tmp_path: Path):
    shots = _shots_file(
        read_dir / "f.shots.json",
        [
            {"line": "one", "voice_onset_s": 1.0, "len_s": 0.5, "file": "l1.wav", "lead_in_s": 0.2},
            {"line": "two", "voice_onset_s": 2.0, "len_s": 0.5, "file": "l2.wav", "lead_in_s": 0.2},
        ],
    )
    proc = _cli(
        ["narration-track", "--shots", shots.name, "--out", "vo.m4a", "--json"], cwd=read_dir
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["speech_duty_pct"] == 25.0
    assert [line["delay_s"] for line in payload["lines"]] == [0.8, 1.8]
    assert (read_dir / "vo.m4a").is_file()


def test_the_cli_refuses_to_build_a_read_that_fails_its_lint(read_dir: Path, tmp_path: Path):
    """This is what makes the linter a gate rather than an annotation: two lines talking over each
    other cannot reach the mix by way of a warning nobody read."""
    shots = _shots_file(
        read_dir / "overlap.shots.json",
        [
            {"line": "one", "voice_onset_s": 1.0, "len_s": 1.5, "file": "l1.wav", "lead_in_s": 0.2},
            {"line": "two", "voice_onset_s": 1.4, "len_s": 0.5, "file": "l2.wav", "lead_in_s": 0.2},
        ],
    )
    proc = _cli(["narration-track", "--shots", shots.name, "--out", "vo.m4a"], cwd=read_dir)
    assert proc.returncode == 4
    assert "is still speaking until" in proc.stderr
    assert not (read_dir / "vo.m4a").exists(), "a refused build must write nothing"


def test_a_picture_finding_does_not_block_an_audio_build(read_dir: Path):
    """Only the narration rules gate this. A camera-move finding has no business refusing a VO
    mix — a gate that fires on unrelated craft is a gate people learn to bypass."""
    shots = read_dir / "camera.shots.json"
    shots.write_text(
        json.dumps(
            {
                "total_duration_s": 4.0,
                "style_scaffold": {"look": "", "provider_model": ""},
                "shots": [{"duration_s": 4.0, "camera": "dolly in while zooming and panning left"}],
                "narration": {
                    "lines": [
                        {
                            "line": "one",
                            "voice_onset_s": 1.0,
                            "len_s": 0.5,
                            "file": "l1.wav",
                            "lead_in_s": 0.2,
                        }
                    ]
                },
            }
        ),  # fmt: skip
        "utf-8",
    )
    proc = _cli(["narration-track", "--shots", shots.name, "--out", "vo.m4a"], cwd=read_dir)
    assert proc.returncode == 0, proc.stderr
