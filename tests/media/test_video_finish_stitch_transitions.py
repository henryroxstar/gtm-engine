"""Per-join transitions: `stitch(transitions=...)` against real ffmpeg.

A shot list declares how each shot is cut INTO (`production.transition_in`) and `stitch` had one
global `crossfade_s`, so honouring a film with ONE dissolve and ONE fade to black meant dissolving
every join in it. These tests are the consumer's proof: the master must actually LOSE the declared
overlap (a test that only asserted the file exists would pass while the transitions were ignored),
an all-cut list must still take the stream-copy path, and the caption sidecar offsets must move by
each join's own overlap rather than by a film-wide constant.

Fixtures are synthetic clips generated the way the sibling stitch tests generate them.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gtm_core import video_finish as vf
from gtm_core.cover_frame import extract_candidates
from gtm_core.video_finish.shots import transitions_from_shots

stitch_mod = importlib.import_module("gtm_core.video_finish.stitch")


def _make_clip(path: Path, *, duration: float = 2.0, w: int = 1080, h: int = 1080) -> Path:
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


def _clips(tmp_path: Path, n: int, *, duration: float = 2.0) -> list[Path]:
    return [_make_clip(tmp_path / f"c{i}.mp4", duration=duration) for i in range(n)]


def _stitch(tmp_path: Path, clips: list[Path], *, name: str = "master.mp4", **kw) -> Path:
    return vf.stitch(
        [vf.ShotSegment(path=str(c), reframed=False) for c in clips],
        ratio="1:1",
        out_path=tmp_path / name,
        workdir=tmp_path / f"w-{name}",
        **kw,
    )


def _shots(*transitions: dict | None) -> dict:
    """A shot list whose first shot is a hard cut by construction and whose rest carry the given
    `production.transition_in` values (None = the key is absent, i.e. a hard cut)."""
    shots: list[dict] = [{"id": "s00", "n": 1, "duration_s": 2.0}]
    for k, entry in enumerate(transitions, 1):
        shot = {"id": f"s{k:02d}", "n": k + 1, "duration_s": 2.0}
        if entry is not None:
            shot["production"] = {"transition_in": entry}
        shots.append(shot)
    return {"shots": shots}


# ── the shot list -> per-join list ────────────────────────────────────────────────────────────


def test_each_shots_declaration_becomes_the_join_INTO_that_shot():
    doc = _shots(
        None,
        {"kind": "dissolve", "duration_s": 0.4, "why": "months later"},
        {"kind": "fade", "duration_s": 0.5},
    )
    assert transitions_from_shots(doc) == [("cut", 0.0), ("dissolve", 0.4), ("fade", 0.5)]


def test_a_list_with_no_declarations_is_all_cuts():
    assert transitions_from_shots(_shots(None, None)) == [("cut", 0.0), ("cut", 0.0)]


def test_a_transition_on_the_first_shot_is_refused_rather_than_dropped():
    doc = _shots(None)
    doc["shots"][0]["production"] = {"transition_in": {"kind": "fade", "duration_s": 0.5}}
    with pytest.raises(ValueError, match="first shot"):
        transitions_from_shots(doc)


@pytest.mark.parametrize(
    "entry",
    [
        {"kind": "wipe", "duration_s": 0.4},
        {"kind": "cut", "duration_s": 0.0},
        {"kind": "dissolve", "duration_s": 0},
        {"kind": "dissolve", "duration_s": 1.5},
        {"kind": "dissolve"},
        "dissolve, 0.4s",
    ],
)
def test_a_malformed_declaration_is_refused(entry):
    with pytest.raises(ValueError):
        transitions_from_shots(_shots(entry))


# ── the join actually happens ─────────────────────────────────────────────────────────────────


def test_the_master_is_shorter_by_exactly_the_declared_overlaps(tmp_path):
    """The negative control: a stitch that IGNORED the transitions would land on the hard-cut
    duration, which this asserts it does not."""
    clips = _clips(tmp_path, 4)
    lengths = [vf._probe_duration(c) for c in clips]
    joins = [("dissolve", 0.4), ("cut", 0.0), ("fade", 0.5)]

    cuts_only = _stitch(tmp_path, clips, name="cuts.mp4")
    with_joins = _stitch(tmp_path, clips, name="joins.mp4", transitions=joins)

    assert vf._probe_duration(cuts_only) == pytest.approx(sum(lengths), abs=0.15)
    assert vf._probe_duration(with_joins) == pytest.approx(sum(lengths) - 0.9, abs=0.15)


def test_an_all_cut_list_still_takes_the_stream_copy_path(tmp_path, monkeypatch):
    clips = _clips(tmp_path, 3)
    calls: list[list[str]] = []
    real = stitch_mod._run_ffmpeg
    monkeypatch.setattr(
        stitch_mod, "_run_ffmpeg", lambda args: (calls.append(list(args)), real(args))[1]
    )
    out = _stitch(tmp_path, clips, transitions=[("cut", 0.0), ("cut", 0.0)])
    assert out.is_file()
    joined = [a for a in calls if str(out) + ".part" in a]
    assert len(joined) == 1
    assert "-c" in joined[0] and "copy" in joined[0], "an all-cut list must not re-encode"
    assert "-filter_complex" not in joined[0]


def test_each_kind_reaches_the_filtergraph_as_its_own_xfade_and_a_cut_as_concat(
    tmp_path, monkeypatch
):
    """A dissolve is xfade's `fade`, a fade is `fadeblack`, a push is `slideup`, and a cut inside
    a transition graph is the concat filter — the mapping is the whole difference between the
    kinds on screen."""
    clips = _clips(tmp_path, 5)
    graphs: list[str] = []
    real = stitch_mod._run_ffmpeg

    def spy(args):
        if "-filter_complex" in args:
            graphs.append(args[args.index("-filter_complex") + 1])
        return real(args)

    monkeypatch.setattr(stitch_mod, "_run_ffmpeg", spy)
    _stitch(
        tmp_path,
        clips,
        transitions=[("dissolve", 0.4), ("cut", 0.0), ("fade", 0.5), ("push", 0.3)],
    )

    graph = next(g for g in graphs if "xfade" in g)
    assert "xfade=transition=fade:duration=0.4" in graph
    assert "xfade=transition=fadeblack:duration=0.5" in graph
    assert "xfade=transition=slideup:duration=0.3" in graph
    assert "concat=n=2:v=1:a=0" in graph and "concat=n=2:v=0:a=1" in graph
    assert (
        "acrossfade=d=0.4" in graph and "acrossfade=d=0.5" in graph and "acrossfade=d=0.3" in graph
    )


def test_the_pixel_format_stays_pinned_on_the_per_join_path(tmp_path, monkeypatch):
    clips = _clips(tmp_path, 2)
    calls: list[list[str]] = []
    real = stitch_mod._run_ffmpeg
    monkeypatch.setattr(
        stitch_mod, "_run_ffmpeg", lambda args: (calls.append(list(args)), real(args))[1]
    )
    _stitch(tmp_path, clips, transitions=[("dissolve", 0.4)])
    encode = next(a for a in calls if "-filter_complex" in a)
    assert encode[encode.index("-pix_fmt") + 1] == "yuv420p"


# ── push: empirical direction proof (both pictures move, outgoing up, incoming from below) ─────

_PUSH_SIZE = 1080
_PUSH_FPS = 20
_PUSH_CLIP_S = 1.5
_PUSH_DURATION_S = 0.4
_PUSH_BG_A = (40, 40, 40)
_PUSH_MARKER_A = (255, 210, 0)
_PUSH_MARKER_A_TOP = 260
_PUSH_MARKER_A_BOTTOM = 340
_PUSH_BG_B = (20, 90, 200)
_PUSH_MARKER_B = (0, 230, 90)
_PUSH_MARKER_B_TOP = 100
_PUSH_MARKER_B_BOTTOM = 180


def _push_frame_a() -> Image.Image:
    """Clip A: a dark background with a bright marker bar near the top (full width, so a single
    sampled column stands in for the whole row)."""
    img = Image.new("RGB", (_PUSH_SIZE, _PUSH_SIZE), _PUSH_BG_A)
    ImageDraw.Draw(img).rectangle(
        [0, _PUSH_MARKER_A_TOP, _PUSH_SIZE - 1, _PUSH_MARKER_A_BOTTOM - 1], fill=_PUSH_MARKER_A
    )
    return img


def _push_frame_b() -> Image.Image:
    """Clip B: a different solid background, ALSO carrying its own marker near ITS top. Without
    this, `push` is indistinguishable from `revealup` (only A moves; B sits still and is merely
    uncovered) — a bare bottom-of-frame colour check is satisfied by either kind, since B's colour
    ends up at the bottom either way. Only a marker that must be seen to TRANSLATE away from B's
    own top edge is something a static reveal cannot fake."""
    img = Image.new("RGB", (_PUSH_SIZE, _PUSH_SIZE), _PUSH_BG_B)
    ImageDraw.Draw(img).rectangle(
        [0, _PUSH_MARKER_B_TOP, _PUSH_SIZE - 1, _PUSH_MARKER_B_BOTTOM - 1], fill=_PUSH_MARKER_B
    )
    return img


def _write_static_clip(tmp_path: Path, name: str, frame: Image.Image, *, count: int) -> Path:
    """`count` copies of one still frame, through frames_to_video — the sanctioned encode path,
    never a hand-authored ffmpeg call."""
    frames_dir = tmp_path / f"{name}-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        frame.save(frames_dir / f"f-{i:04d}.png")
    return vf.frames_to_video(
        str(frames_dir / "f-%04d.png"), fps=_PUSH_FPS, out_path=tmp_path / f"{name}.mp4"
    )


def _row_color(img: Image.Image, y: int) -> tuple[int, int, int]:
    return img.getpixel((img.width // 2, y))


def _close(c1, c2, tol: int = 30) -> bool:
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(c1, c2))


def _band_of(
    img: Image.Image, color: tuple[int, int, int], *, tol: int = 25
) -> tuple[int, int] | None:
    """The (min, max) row where `color` pixels are found, or None when that colour is off-screen —
    shared by both clips' markers, since the whole point is to compare where each one lands."""
    rows = [y for y in range(0, img.height, 2) if _close(_row_color(img, y), color, tol=tol)]
    return (min(rows), max(rows)) if rows else None


def _closest_candidate(paths: list[Path], duration_s: float, target_s: float) -> Path:
    """Which of `extract_candidates`'s evenly-spaced outputs sits closest to `target_s` — its own
    middle-90%-window formula (see its docstring), reproduced only to pick a file, not to
    second-guess the extraction."""
    count = len(paths)
    margin = duration_s * 0.05
    window = max(duration_s - 2 * margin, 0.0)
    times = [
        margin + (window * i / max(count - 1, 1)) if count > 1 else duration_s / 2
        for i in range(count)
    ]
    return min(zip(times, paths), key=lambda tp: abs(tp[0] - target_s))[1]


def test_push_slides_the_outgoing_shot_up_and_the_incoming_shot_in_from_the_bottom(tmp_path):
    """Empirical proof of the `push` -> xfade `slideup` mapping. NOT a wipe (a static reveal
    where neither picture moves) and NOT a cover/reveal (only ONE picture moves — e.g. `revealup`,
    where B sits still and is merely uncovered): a bare "clip B's colour is at the bottom" check
    cannot tell `slideup` from `revealup`, since B's colour ends up at the bottom either way. So
    BOTH clips carry a marker near THEIR OWN top edge, and the proof is that each marker is later
    found displaced from where it started — A's moved up off its own position, and separately B's
    own marker is found translated well below where IT started, never merely revealed in place."""
    frame_count = round(_PUSH_FPS * _PUSH_CLIP_S)
    clip_a = _write_static_clip(tmp_path, "push-a", _push_frame_a(), count=frame_count)
    clip_b = _write_static_clip(tmp_path, "push-b", _push_frame_b(), count=frame_count)
    out = _stitch(
        tmp_path, [clip_a, clip_b], name="push.mp4", transitions=[("push", _PUSH_DURATION_S)]
    )

    dur_a = vf._probe_duration(clip_a)
    master_dur = vf._probe_duration(out)
    transition_start = dur_a - _PUSH_DURATION_S
    transition_end = dur_a
    t_before = transition_start * 0.4
    t_during = transition_start + _PUSH_DURATION_S * 0.15
    t_mid = transition_start + _PUSH_DURATION_S * 0.5
    t_after = transition_end + (master_dur - transition_end) * 0.5

    candidates = extract_candidates(out, out_dir=tmp_path / "candidates", count=41)
    frame_before = Image.open(_closest_candidate(candidates, master_dur, t_before)).convert("RGB")
    frame_during = Image.open(_closest_candidate(candidates, master_dur, t_during)).convert("RGB")
    frame_mid = Image.open(_closest_candidate(candidates, master_dur, t_mid)).convert("RGB")
    frame_after = Image.open(_closest_candidate(candidates, master_dur, t_after)).convert("RGB")

    # A's marker: still visible early in the join, and already moved UP off its declared position.
    band_before = _band_of(frame_before, _PUSH_MARKER_A)
    band_during = _band_of(frame_during, _PUSH_MARKER_A)
    assert band_before is not None, "the marker must be visible before the transition starts"
    assert abs(band_before[0] - _PUSH_MARKER_A_TOP) <= 10, band_before
    assert abs(band_before[1] - (_PUSH_MARKER_A_BOTTOM - 1)) <= 10, band_before

    assert band_during is not None, "the marker must still be (partly) visible this early in push"
    assert band_during[0] < band_before[0] and band_during[1] < band_before[1], (
        f"marker band moved from {band_before} to {band_during} — push must move it UP (a "
        "smaller y), not down and not leave it in place"
    )

    top_before = _row_color(frame_before, 5)
    assert _close(top_before, _PUSH_BG_A, tol=15), "sanity: before the join the top is clip A"

    # B's marker: the discriminating check. `revealup` never moves it off [_PUSH_MARKER_B_TOP,
    # _PUSH_MARKER_B_BOTTOM) — under that kind it only ever surfaces there (near the frame's OWN
    # top), because B is sampled at its own unshifted row throughout. `slideup` carries it down
    # with the rest of B, so mid-join it must be found well below where it started.
    band_b_mid = _band_of(frame_mid, _PUSH_MARKER_B)
    assert band_b_mid is not None, (
        "clip B's marker must be visible mid-join — a static reveal would not have carried it "
        "into view here at all"
    )
    assert band_b_mid[0] > _PUSH_MARKER_B_BOTTOM + _PUSH_SIZE * 0.25, (
        f"B's marker at {band_b_mid} is too close to its own declared position "
        f"[{_PUSH_MARKER_B_TOP}, {_PUSH_MARKER_B_BOTTOM}) — push must carry B's OWN content down "
        "with it, not merely reveal B's colour where A used to be"
    )

    for y in (5, frame_after.height // 2, frame_after.height - 5):
        assert _close(_row_color(frame_after, y), _PUSH_BG_B, tol=30), (
            f"row {y} after the join is not clip B — the master should be fully B by then"
        )


# ── refusals ──────────────────────────────────────────────────────────────────────────────────


def test_the_two_ways_of_asking_cannot_be_combined(tmp_path):
    clips = _clips(tmp_path, 2)
    with pytest.raises(ValueError, match="not both"):
        _stitch(tmp_path, clips, crossfade_s=0.3, transitions=[("dissolve", 0.4)])


def test_one_entry_per_join_is_enforced(tmp_path):
    clips = _clips(tmp_path, 3)
    with pytest.raises(ValueError, match="one entry per join"):
        _stitch(tmp_path, clips, transitions=[("dissolve", 0.4)])


def test_a_transition_longer_than_an_adjacent_segment_is_refused(tmp_path):
    """Fail closed, per segment: a 1s shot cannot host a 0.6s join on either side."""
    clips = [
        _make_clip(tmp_path / "a.mp4", duration=2.0),
        _make_clip(tmp_path / "b.mp4", duration=1.0),
    ]
    with pytest.raises(ValueError, match="at least"):
        _stitch(tmp_path, clips, transitions=[("dissolve", 0.6)])
    assert not (tmp_path / "master.mp4").exists(), "refused before the encode, not after"


def test_a_cut_that_asks_for_overlap_is_refused(tmp_path):
    clips = _clips(tmp_path, 2)
    with pytest.raises(ValueError, match="cut but asks"):
        _stitch(tmp_path, clips, transitions=[("cut", 0.4)])


# ── carried forward: sidecar offsets and the join record ──────────────────────────────────────


def _write_captions(seg: Path, *, start_s: float, end_s: float) -> None:
    payload = {
        "frame": [1080, 1080],
        "shot_id": seg.stem,
        "screens": [
            {
                "index": 0,
                "text": seg.stem,
                "start_s": start_s,
                "end_s": end_s,
                "box": {"x": 100, "y": 900, "w": 880, "h": 120},
            }
        ],
    }
    stitch_mod.sidecar_path(seg, "captions").write_text(json.dumps(payload))


def test_a_caption_after_a_dissolve_lands_earlier_by_that_joins_overlap(tmp_path):
    """The join before a shot eats into the master's timeline, so every caption on that shot moves
    up by exactly its overlap — and a caption before the join does not move at all."""
    clips = _clips(tmp_path, 3)
    for clip in clips:
        _write_captions(clip, start_s=0.5, end_s=1.5)
    lengths = [vf._probe_duration(c) for c in clips]

    _stitch(tmp_path, clips, name="cuts.mp4")
    hard = json.loads((tmp_path / "cuts.captions.json").read_text())["screens"]
    _stitch(tmp_path, clips, name="joins.mp4", transitions=[("cut", 0.0), ("dissolve", 0.4)])
    dissolved = json.loads((tmp_path / "joins.captions.json").read_text())["screens"]

    assert dissolved[0]["start_s"] == hard[0]["start_s"]
    assert dissolved[1]["start_s"] == hard[1]["start_s"], "a hard-cut join moves nothing"
    assert dissolved[2]["start_s"] == pytest.approx(hard[2]["start_s"] - 0.4, abs=0.005)
    assert dissolved[2]["start_s"] == pytest.approx(sum(lengths[:2]) - 0.4 + 0.5, abs=0.005)


def test_the_master_records_the_joins_it_applied_and_clears_a_stale_record(tmp_path):
    clips = _clips(tmp_path, 4)
    out = _stitch(tmp_path, clips, transitions=[("cut", 0.0), ("fade", 0.5), ("push", 0.3)])
    record = json.loads(stitch_mod.sidecar_path(out, "transitions").read_text())
    assert record["joins"] == [
        {"join": 1, "kind": "fade", "duration_s": 0.5, "into": str(clips[2])},
        {"join": 2, "kind": "push", "duration_s": 0.3, "into": str(clips[3])},
    ], "only the non-cut joins, keyed by the join they are — push recorded verbatim, not slideup"

    _stitch(tmp_path, clips, transitions=[("cut", 0.0), ("cut", 0.0), ("cut", 0.0)])
    assert not stitch_mod.sidecar_path(out, "transitions").is_file(), (
        "a record from an earlier stitch must not outlive it"
    )


def test_the_finish_plan_carries_the_joins_off_the_master(tmp_path):
    """The evidence chain `shots_coverage --stage finish` reads: stitch records the joins beside
    the master, and the finish plan (and so the finish manifest) carries them."""
    clips = _clips(tmp_path, 2)
    out = _stitch(tmp_path, clips, transitions=[("dissolve", 0.4)])
    plan = vf.plan(profile="acme", slug="fixture", ratio="1:1", source=str(out), spec={})
    assert plan.transitions == [
        {"join": 0, "kind": "dissolve", "duration_s": 0.4, "into": str(clips[1])}
    ]

    cuts = _stitch(tmp_path, clips, name="cuts.mp4", transitions=[("cut", 0.0)])
    assert (
        vf.plan(profile="acme", slug="f", ratio="1:1", source=str(cuts), spec={}).transitions
        is None
    )
