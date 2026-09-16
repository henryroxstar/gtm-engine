"""gtm_core.video_finish.burn_captions() — the caption burn as a verb, against real ffmpeg.

The glue this replaces was hand-written into three throwaway scripts in a single session on
2026-08-30. Two properties are what make it worth owning, and both are asserted here: the burn
happens PER SHOT before the stitch (a window inside one shot cannot drift out of it, whatever the
concat demuxer's container padding later does to the master's timeline), and a shot may say on
screen something different from what it says aloud (`caption_text_override`).

Fixture font is matplotlib's bundled DejaVuSans.ttf — already a declared dependency, and carrying
no tenant identity, so this file stays safe for the release-mode de-brand lint.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib
import pytest

from gtm_core import video_finish as vf

_FONT_ABS = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


def _kit():
    return {"typography": {"font_files": {"caption": _FONT_ABS}}}


def _make_shot(path: Path, *, duration: float = 2.0, w: int = 640, h: int = 360) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _shots_doc(tmp_path: Path, shots: list[dict]) -> Path:
    p = tmp_path / "film.shots.json"
    p.write_text(json.dumps({"slug": "film", "shots": shots}), encoding="utf-8")
    return p


def _burn(tmp_path: Path, shots: list[dict], **kw):
    doc = _shots_doc(tmp_path, shots)
    return vf.burn_captions(
        shots,
        ratio=kw.pop("ratio", "16:9"),
        kit=_kit(),
        out_dir=tmp_path / "out",
        workdir=tmp_path / "work",
        shots_root=doc.parent,
        **kw,
    )


# ── the override, which is the whole reason this owns the text ───────────────────────────


def test_caption_text_override_wins_over_spoken(tmp_path):
    """The VO says "acme dot com" because that is how a voice reads a URL. The burned caption
    must say "acme.com", because that is what a viewer can type. Both are correct; only a
    field that says so can express it."""
    _make_shot(tmp_path / "shots" / "h17.mp4")
    result = _burn(
        tmp_path,
        [
            {
                "id": "h17",
                "file": "shots/h17.mp4",
                "spoken": "visit acme dot com",
                "caption_text_override": "visit acme.com",
                "caption_override_reason": "a viewer types what is on screen",
            }
        ],
    )
    (burned,) = result.burned
    assert burned.text == "visit acme.com"
    assert burned.text_source == "caption_text_override"
    assert Path(burned.out_path).is_file()


def test_without_an_override_the_spoken_line_is_burned(tmp_path):
    _make_shot(tmp_path / "shots" / "h05.mp4")
    result = _burn(tmp_path, [{"id": "h05", "file": "shots/h05.mp4", "spoken": "one two three"}])
    (burned,) = result.burned
    assert burned.text == "one two three"
    assert burned.text_source == "spoken"


def test_an_empty_override_falls_back_rather_than_burning_nothing(tmp_path):
    """A present-but-empty override is a half-finished edit, not an instruction to burn "".
    Burning an empty string would produce zero screens and look exactly like a silent shot."""
    _make_shot(tmp_path / "shots" / "h05.mp4")
    result = _burn(
        tmp_path,
        [
            {
                "id": "h05",
                "file": "shots/h05.mp4",
                "spoken": "one two",
                "caption_text_override": "  ",
            }
        ],
    )
    (burned,) = result.burned
    assert burned.text == "one two"
    assert burned.text_source == "spoken"


# ── the window is the shot's own, measured ───────────────────────────────────────────────


def test_the_burn_window_is_probed_off_the_file_not_read_from_the_shot_list(tmp_path):
    """The declared duration is what was ASKED for; the probe is what exists. The gap between the
    two is exactly why this burns per shot: a concat's output measured 0.637s longer than the sum
    of its inputs' video durations, so a caption timed against a master drifts late."""
    _make_shot(tmp_path / "shots" / "s1.mp4", duration=3.0)
    result = _burn(
        tmp_path,
        [{"id": "s1", "file": "shots/s1.mp4", "spoken": "a b c d", "duration_s": 99.0}],
    )
    (burned,) = result.burned
    assert burned.duration_s == pytest.approx(3.0, abs=0.15)
    assert burned.duration_s != 99.0


def test_no_caption_screen_extends_past_its_own_shot(tmp_path):
    """The per-shot property, stated on the screens themselves: every window lies inside [0, D]."""
    from gtm_core.captions import split_screens_segmented

    _make_shot(tmp_path / "shots" / "s1.mp4", duration=2.0)
    result = _burn(tmp_path, [{"id": "s1", "file": "shots/s1.mp4", "spoken": "a b c d e f g h"}])
    (burned,) = result.burned
    screens = split_screens_segmented(
        [{"text": burned.text, "start_s": 0.0, "end_s": burned.duration_s}], max_words=5
    )
    assert screens
    assert all(0.0 <= s.start_s < s.end_s <= burned.duration_s + 1e-6 for s in screens)


# ── skips are reported, refusals are refusals ────────────────────────────────────────────


def test_a_shot_with_no_spoken_line_is_reported_not_silently_dropped(tmp_path):
    _make_shot(tmp_path / "shots" / "card.mp4")
    result = _burn(
        tmp_path,
        [
            {"id": "card", "file": "shots/card.mp4"},
            {"id": "vo", "file": "shots/card.mp4", "spoken": "hello there"},
        ],
    )
    assert [b.shot_id for b in result.burned] == ["vo"]
    assert result.skipped and result.skipped[0][0] == "card"
    assert "spoken" in result.skipped[0][1]


def test_a_shot_whose_file_is_missing_is_refused_not_skipped(tmp_path):
    """A stale path means the shot list and the disk disagree. Skipping would leave an out_dir
    that looks like a complete pass."""
    with pytest.raises(ValueError, match="does not exist"):
        _burn(tmp_path, [{"id": "gone", "file": "shots/gone.mp4", "spoken": "hi"}])


def test_shot_ids_filters_and_an_unknown_id_is_refused(tmp_path):
    _make_shot(tmp_path / "shots" / "a.mp4")
    _make_shot(tmp_path / "shots" / "b.mp4")
    shots = [
        {"id": "a", "file": "shots/a.mp4", "spoken": "first line"},
        {"id": "b", "file": "shots/b.mp4", "spoken": "second line"},
    ]
    result = _burn(tmp_path, shots, shot_ids=["b"])
    assert [x.shot_id for x in result.burned] == ["b"]

    with pytest.raises(ValueError, match="not in this shot list"):
        _burn(tmp_path, shots, shot_ids=["nope"])


def test_an_unknown_ratio_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown ratio"):
        _burn(tmp_path, [{"id": "a", "file": "shots/a.mp4", "spoken": "x"}], ratio="21:9")


# ── the source is never the destination ──────────────────────────────────────────────────


def test_the_source_shot_is_never_overwritten(tmp_path):
    src = _make_shot(tmp_path / "shots" / "s1.mp4")
    before = src.read_bytes()
    result = _burn(tmp_path, [{"id": "s1", "file": "shots/s1.mp4", "spoken": "a b c"}])
    assert src.read_bytes() == before
    assert Path(result.burned[0].out_path) != src


def test_the_audio_track_survives_the_burn(tmp_path):
    """`-c:a copy`: a caption pass has no business re-encoding a VO it did not change."""
    _make_shot(tmp_path / "shots" / "s1.mp4")
    result = _burn(tmp_path, [{"id": "s1", "file": "shots/s1.mp4", "spoken": "a b c"}])
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            result.burned[0].out_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(out.stdout)["streams"], "the burned shot lost its audio stream"


def test_a_shot_with_no_audio_still_burns(tmp_path):
    """A shot whose VO has not been muxed yet must still caption — refusing would make the
    caption pass depend on the mux order."""
    silent = tmp_path / "shots" / "silent.mp4"
    silent.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1.5:size=640x360:rate=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )
    result = _burn(tmp_path, [{"id": "silent", "file": "shots/silent.mp4", "spoken": "a b"}])
    assert Path(result.burned[0].out_path).is_file()


# ── the CLI's write boundary ─────────────────────────────────────────────────────────────


def test_the_cli_refuses_an_out_dir_outside_the_content_root(tmp_path, capsys):
    doc = _shots_doc(tmp_path, [{"id": "a", "file": "shots/a.mp4", "spoken": "x"}])
    root = tmp_path / "content-root"
    root.mkdir()
    code = vf.main(
        [
            "burn-captions",
            "--shots",
            str(doc),
            "--ratio",
            "16:9",
            "--profile",
            "tenant",
            "--out-dir",
            str(tmp_path / "elsewhere"),
            "--content-root",
            str(root),
        ]
    )
    assert code == 2
    assert "content root" in capsys.readouterr().err


# ── caption_segments — two speakers sharing one caption channel ──────────────────────────


def test_caption_segments_place_the_second_speaker_after_the_first(tmp_path):
    """The v8 Act-1 beat is a caller's line and then the agent's reply, in ONE caption channel.
    Spreading a single string evenly over the shot puts the handover wherever the word count
    falls; the reply has to land at the moment the caller stops. A shot whose second line drifts
    early shows the agent answering a question that has not been asked yet."""
    _make_shot(tmp_path / "shots" / "s01.mp4", duration=8.0)
    result = _burn(
        tmp_path,
        [
            {
                "id": "s01",
                "file": "shots/s01.mp4",
                "spoken": "caller line here",
                "caption_segments": [
                    {"text": "aaa bbb ccc", "start_s": 0.35, "end_s": 4.0},
                    {"text": "xxx yyy zzz", "start_s": 4.0, "end_s": 8.0},
                ],
            }
        ],
        max_words=3,
    )
    (burned,) = result.burned
    assert burned.text_source == "caption_segments"
    assert burned.screens == 2
    # The caller's words never appear after the handover, and the agent's never before it.
    assert "aaa" in burned.text and "zzz" in burned.text


def test_caption_segments_win_over_spoken_and_the_override(tmp_path):
    _make_shot(tmp_path / "shots" / "s04.mp4", duration=4.0)
    result = _burn(
        tmp_path,
        [
            {
                "id": "s04",
                "file": "shots/s04.mp4",
                "spoken": "the spoken line",
                "caption_text_override": "the override",
                "caption_segments": [{"text": "the segments", "start_s": 0.0, "end_s": 4.0}],
            }
        ],
    )
    (burned,) = result.burned
    assert burned.text_source == "caption_segments"
    assert "segments" in burned.text


def test_a_segment_window_that_runs_backwards_is_refused(tmp_path):
    """A refusal, not a silent reorder: a backwards window is a caller that mis-derived its
    handover, and burning it anyway would look like a caption that simply never appeared."""
    _make_shot(tmp_path / "shots" / "s07.mp4", duration=4.0)
    with pytest.raises(ValueError, match="not a forward span"):
        _burn(
            tmp_path,
            [
                {
                    "id": "s07",
                    "file": "shots/s07.mp4",
                    "caption_segments": [{"text": "late", "start_s": 3.0, "end_s": 1.0}],
                }
            ],
        )


def test_a_segment_with_no_text_is_refused(tmp_path):
    _make_shot(tmp_path / "shots" / "s10.mp4", duration=4.0)
    with pytest.raises(ValueError, match="has no text"):
        _burn(
            tmp_path,
            [
                {
                    "id": "s10",
                    "file": "shots/s10.mp4",
                    "caption_segments": [{"text": "  ", "start_s": 0.0, "end_s": 4.0}],
                }
            ],
        )


# ── the per-shot sidecar — how the geometry survives the stitch ──────────────────────────


def test_each_burned_shot_writes_a_timed_caption_sidecar_beside_it(tmp_path):
    """Burning per shot keeps a caption inside its shot; it also used to be where the geometry
    STOPPED. A stitched, pre-burned master then finished with `captions: null`, and the contrast
    tier — which runs only when a manifest supplies geometry — never ran on a film whose type was
    near-black on a dark picture. The sidecar is what `stitch` carries forward."""
    _make_shot(tmp_path / "shots" / "s1.mp4", duration=2.0, w=1080, h=1920)
    result = _burn(
        tmp_path,
        [{"id": "s1", "file": "shots/s1.mp4", "spoken": "one two three four five six seven"}],
        ratio="9:16",
    )
    (burned,) = result.burned
    sidecar = Path(burned.sidecar_path)
    assert sidecar == Path(burned.out_path).with_name("s1-captioned.captions.json")
    data = json.loads(sidecar.read_text())
    assert data["frame"] == [1080, 1920]
    assert data["shot_id"] == "s1"
    assert data["text"] == "one two three four five six seven"
    assert len(data["screens"]) == burned.screens == 2
    for screen in data["screens"]:
        assert 0.0 <= screen["start_s"] < screen["end_s"] <= burned.duration_s + 1e-6
        assert set(screen["box"]) == {"x", "y", "w", "h"}
        assert screen["glyph_rgb"] is not None
    assert [s["box"] for s in data["screens"]] == list(burned.boxes)
