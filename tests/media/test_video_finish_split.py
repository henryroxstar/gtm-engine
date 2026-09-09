"""gtm_core.video_finish.split() — against real ffmpeg. This directory requires ffmpeg on PATH
(see conftest.py).

`split` is the inverse of `stitch`. It was added for a gap the 2026-08-28 one-take render
strategy opened — presenter footage as a single file, inserts as separate files, `stitch` only
joins. That strategy was retired 2026-09-07 (HeyGen bills per second, `gtm_core.heygen_cost`;
`video-avatar` renders per beat again), so no skill invokes `split` now; it stays as the only
sanctioned frame-accurate cut behind the ffmpeg door, and these tests hold that contract. `Bash(ffmpeg:*)` is denied, so "cut it locally"
named an operation with no reachable implementation.

The refusal that matters most here is the out-of-range one: a cut whose `end_s` runs past the
delivered take is precisely what an ASKED duration copied from the shot list looks like once the
render came back shorter, which is the desync the retired "one clip per presenter shot" guardrail
named.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_finish as vf


def _make_clip(path: Path, *, duration: float, w: int = 320, h: int = 240) -> Path:
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
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def take(tmp_path: Path) -> Path:
    """A 6-second continuous 'presenter take'."""
    return _make_clip(tmp_path / "take-9x16.mp4", duration=6.0)


def test_split_cuts_a_take_into_beats_at_the_measured_points(take, tmp_path):
    """The core behaviour: one file in, one file per beat out, each at its asked length."""
    cuts = [
        vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0),
        vf.TakeCut(shot_id="shot-2", start_s=2.0, end_s=4.5),
        vf.TakeCut(shot_id="shot-3", start_s=4.5, end_s=6.0),
    ]
    result = vf.split(take, cuts, out_dir=tmp_path / "beats", workdir=tmp_path / "_work")

    assert len(result.out_paths) == 3
    assert [p.name for p in result.out_paths] == ["shot-1.mp4", "shot-2.mp4", "shot-3.mp4"]
    assert all(p.is_file() for p in result.out_paths)
    assert result.source_duration_s == pytest.approx(6.0, abs=0.15)

    for path, cut in zip(result.out_paths, cuts, strict=True):
        actual = vf._probe_duration(path)
        assert actual == pytest.approx(cut.duration_s, abs=0.15), (
            f"{cut.shot_id} asked for {cut.duration_s}s and got {actual:.3f}s — a cut landing "
            "off its measured out-point is the defect this verb exists to avoid"
        )


def test_split_leaves_no_part_files_behind(take, tmp_path):
    """Intermediates land in workdir/*.part and are atomically replaced, never left as a
    plausible-looking half-written mp4 a caller might mistake for a finished beat."""
    workdir = tmp_path / "_work"
    vf.split(
        take,
        [vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0)],
        out_dir=tmp_path / "beats",
        workdir=workdir,
    )
    assert list(workdir.glob("*.part")) == []


def test_split_refuses_a_cut_past_the_takes_real_duration(take, tmp_path):
    """THE refusal. A 6s take with a cut ending at 8.4s is a shot list's ASKED timing carried
    forward as though it described the delivered file. Silently clamping would produce a short
    beat that looks fine and desyncs everything after it."""
    with pytest.raises(ValueError) as exc:
        vf.split(
            take,
            [
                vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0),
                vf.TakeCut(shot_id="shot-2", start_s=2.0, end_s=8.4),
            ],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )
    msg = str(exc.value)
    assert "shot-2" in msg, "the error does not name which cut overran"
    assert "8.4" in msg and "6." in msg, (
        "the error does not put the asked out-point beside the take's real duration, which is "
        "the comparison that explains the failure"
    )


def test_split_tolerates_a_cut_ending_at_the_takes_own_duration(take, tmp_path):
    """A container's reported duration and its last frame's presentation time disagree by about a
    frame. A cut asking for the whole take must not trip on that rounding."""
    result = vf.split(
        take,
        [vf.TakeCut(shot_id="whole", start_s=0.0, end_s=6.0)],
        out_dir=tmp_path / "beats",
        workdir=tmp_path / "_work",
    )
    assert len(result.out_paths) == 1


def test_split_refuses_a_zero_or_inverted_length_cut(take, tmp_path):
    with pytest.raises(ValueError, match="at or before"):
        vf.split(
            take,
            [vf.TakeCut(shot_id="shot-1", start_s=3.0, end_s=3.0)],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )


def test_split_refuses_a_negative_start(take, tmp_path):
    with pytest.raises(ValueError, match="before the beginning"):
        vf.split(
            take,
            [vf.TakeCut(shot_id="shot-1", start_s=-0.5, end_s=2.0)],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )


@pytest.mark.parametrize("shot_id", ["../escape", "sub/dir", "..", "", "a\\b"])
def test_split_refuses_a_shot_id_that_is_not_a_bare_segment(take, tmp_path, shot_id):
    """`shot_id` names a file inside the output directory, so it is caller data reaching a
    filesystem path. Directory traversal is the highest-risk error class in this repo."""
    with pytest.raises(ValueError, match="unsafe shot_id"):
        vf.split(
            take,
            [vf.TakeCut(shot_id=shot_id, start_s=0.0, end_s=2.0)],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )


def test_split_refuses_duplicate_shot_ids(take, tmp_path):
    """Two cuts writing the same filename would silently overwrite, leaving one beat missing from
    a run that reported success."""
    with pytest.raises(ValueError, match="duplicate shot_id"):
        vf.split(
            take,
            [
                vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0),
                vf.TakeCut(shot_id="shot-1", start_s=2.0, end_s=4.0),
            ],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )


def test_split_needs_at_least_one_cut(take, tmp_path):
    with pytest.raises(ValueError, match="at least one cut"):
        vf.split(take, [], out_dir=tmp_path / "beats", workdir=tmp_path / "_work")


def test_split_allows_overlapping_cuts_because_stitch_crossfade_needs_them(take, tmp_path):
    """Refusing overlap would fight `stitch(crossfade_s=...)`, which requires overlapping
    tails/heads. Gaps are allowed for the same reason a dropped fluffed line is legitimate."""
    result = vf.split(
        take,
        [
            vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.5),
            vf.TakeCut(shot_id="shot-2", start_s=2.0, end_s=4.5),  # overlaps
            vf.TakeCut(shot_id="shot-3", start_s=5.0, end_s=6.0),  # gap before it
        ],
        out_dir=tmp_path / "beats",
        workdir=tmp_path / "_work",
    )
    assert len(result.out_paths) == 3


def test_split_raises_ffmpeg_unavailable_when_absent(take, tmp_path, monkeypatch):
    # `shutil` is imported per submodule since video_finish became a package; patching the
    # stdlib module object itself covers every one of them.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(vf.FfmpegUnavailable):
        vf.split(
            take,
            [vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0)],
            out_dir=tmp_path / "beats",
            workdir=tmp_path / "_work",
        )


def test_split_output_round_trips_through_stitch(take, tmp_path):
    """The whole reason the verb exists: cut a take into beats, and the beats still assemble.
    Without this pair the creator pack's `finish` node cannot interleave presenter and insert
    shots at all."""
    cuts = [
        vf.TakeCut(shot_id="shot-1", start_s=0.0, end_s=2.0),
        vf.TakeCut(shot_id="shot-2", start_s=2.0, end_s=4.0),
        vf.TakeCut(shot_id="shot-3", start_s=4.0, end_s=6.0),
    ]
    split_result = vf.split(take, cuts, out_dir=tmp_path / "beats", workdir=tmp_path / "_work")

    joined = vf.stitch(
        [vf.ShotSegment(path=str(p), reframed=True) for p in split_result.out_paths],
        ratio="9:16",
        out_path=tmp_path / "rejoined.mp4",
        workdir=tmp_path / "_stitch",
    )
    assert joined.is_file()
    assert vf._probe_duration(joined) == pytest.approx(6.0, abs=0.4)


def test_confined_dir_refuses_an_out_dir_outside_the_content_root(tmp_path):
    """The write-boundary mirror of `_confined_output` for a verb that writes many files into one
    directory. Checking the directory's PARENT (what `_confined_output` does for a file) would be
    one level too loose here."""
    root = tmp_path / "content"
    root.mkdir()
    assert vf._confined_dir(root / "beats", content_root=root) == (root / "beats").resolve()
    with pytest.raises(vf.PolishError, match="outside the resolved content root"):
        vf._confined_dir(tmp_path / "elsewhere", content_root=root)


def test_cli_split_writes_the_beats_and_reports_the_measured_cuts(take, tmp_path, capsys):
    """End-to-end through the CLI, which is the only surface a skill can reach."""
    root = tmp_path / "content"
    (root / "beats").mkdir(parents=True)
    cuts_json = tmp_path / "cuts.json"
    cuts_json.write_text(
        json.dumps(
            {
                "cuts": [
                    {"shot_id": "shot-1", "start_s": 0.0, "end_s": 2.0},
                    {"shot_id": "shot-2", "start_s": 2.0, "end_s": 6.0},
                ]
            }
        )
    )

    rc = vf.main(
        [
            "split",
            "--in",
            str(take),
            "--cuts",
            str(cuts_json),
            "--out-dir",
            str(root / "beats"),
            "--content-root",
            str(root),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["out_paths"]) == 2
    assert payload["source_duration_s"] == pytest.approx(6.0, abs=0.15)
    assert payload["cuts"][1]["duration_s"] == pytest.approx(4.0, abs=0.001)


def test_cli_split_exits_4_on_a_cut_past_the_take(take, tmp_path, capsys):
    """A refusal has to reach the caller as a non-zero exit, not a warning inside a zero."""
    root = tmp_path / "content"
    (root / "beats").mkdir(parents=True)
    cuts_json = tmp_path / "cuts.json"
    cuts_json.write_text(json.dumps({"cuts": [{"shot_id": "s", "start_s": 0.0, "end_s": 99.0}]}))

    rc = vf.main(
        [
            "split",
            "--in",
            str(take),
            "--cuts",
            str(cuts_json),
            "--out-dir",
            str(root / "beats"),
            "--content-root",
            str(root),
        ]
    )
    assert rc == 4
    assert "99.0" in capsys.readouterr().err


def test_cli_split_exits_2_on_an_out_dir_outside_the_content_root(take, tmp_path, capsys):
    root = tmp_path / "content"
    root.mkdir()
    cuts_json = tmp_path / "cuts.json"
    cuts_json.write_text(json.dumps({"cuts": [{"shot_id": "s", "start_s": 0.0, "end_s": 2.0}]}))

    rc = vf.main(
        [
            "split",
            "--in",
            str(take),
            "--cuts",
            str(cuts_json),
            "--out-dir",
            str(tmp_path / "elsewhere"),
            "--content-root",
            str(root),
        ]
    )
    assert rc == 2
    assert "outside the resolved content root" in capsys.readouterr().err
