"""gtm_core.video_finish overlays — a shot's `production.overlay` bubbles, composited per shot.

The gap this closes: a shot list carried `production.overlay` on four shots and nothing consumed
it — no scene, no finish verb, no lint — so a film shipped its bubbles as a note in a JSON file.
These tests run the whole path against real ffmpeg: a synthetic clip at the ratio's native size,
a shot list with one overlay shot and one without, and the sidecar whose shape a stitch-time
reader and a lint depend on.

Fixture font is matplotlib's bundled DejaVuSans.ttf; copy is fictional.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageChops

from gtm_core import video_finish as vf
from gtm_core.screen_ui.scenes import chat_bubble as cb
from gtm_core.video_finish import overlays as ov

_FONT_ABS = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


def _kit():
    return {"typography": {"font_files": {"caption": _FONT_ABS}}}


def _make_shot(path: Path, *, duration: float = 2.0, w: int = 1080, h: int = 1920) -> Path:
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
            "-preset",
            "ultrafast",
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


def _frame_at(path: Path, at_s: float) -> Image.Image:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{at_s:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return Image.open(io.BytesIO(proc.stdout)).convert("RGB")


def _mean_diff(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> float:
    diff = ImageChops.difference(a.crop(box), b.crop(box)).convert("L")
    data = list(diff.get_flattened_data())
    return sum(data) / len(data)


_OVERLAY = {
    "kind": "outgoing bubble",
    "text": "write a post about the release\nin my voice",
    "note": "his prompt, in his own words",
    "arrive_s": 0.25,
    "end_s": 1.5,
}


def _shots(tmp_path: Path) -> list[dict]:
    _make_shot(tmp_path / "shots" / "beat-01.mp4")
    _make_shot(tmp_path / "shots" / "beat-02.mp4")
    return [
        {"id": "shot-01", "file": "shots/beat-01.mp4"},
        {"id": "shot-02", "file": "shots/beat-02.mp4", "production": {"overlay": dict(_OVERLAY)}},
    ]


def _apply(tmp_path: Path, shots: list[dict], **kw) -> ov.OverlayResult:
    return ov.apply_overlays(
        shots,
        ratio=kw.pop("ratio", "9:16"),
        kit=_kit(),
        fps=kw.pop("fps", 12),
        shots_root=tmp_path,
        out_dir=tmp_path / "out",
        workdir=tmp_path / "work",
        **kw,
    )


# ── the path end to end ────────────────────────────────────────────────────────────────────


def test_an_overlay_shot_is_composited_and_the_other_is_reported_skipped(tmp_path):
    result = _apply(tmp_path, _shots(tmp_path))
    (done,) = result.overlaid
    assert done.shot_id == "shot-02"
    assert Path(done.out_path) == tmp_path / "out" / "shot-02-overlaid.mp4"
    assert Path(done.out_path).is_file()
    assert result.skipped == (("shot-01", "no production.overlay — nothing to draw"),)


def test_the_sidecar_has_exactly_the_contracted_shape(tmp_path):
    """Another track merges this at stitch time and a lint reads it. The key set AND order are
    the contract; `box` is measured off the scene, `anchor` is the resolved placement."""
    result = _apply(tmp_path, _shots(tmp_path))
    (done,) = result.overlaid
    sidecar = tmp_path / "out" / "shot-02-overlaid.overlays.json"
    assert Path(done.sidecar_path) == sidecar
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert list(doc) == ["frame", "shot_id", "overlays"]
    assert doc["frame"] == [1080, 1920] and doc["shot_id"] == "shot-02"
    (row,) = doc["overlays"]
    assert list(row) == ["kind", "text", "start_s", "end_s", "box", "anchor"]
    assert row["kind"] == "outgoing"
    assert row["text"] == "write a post about the release\nin my voice"
    assert row["start_s"] == 0.25 and row["end_s"] == 1.5
    assert list(row["box"]) == ["x", "y", "w", "h"]
    assert row["anchor"] == {"side": "right", "y_frac": 0.62, "w_frac": 0.46}
    assert doc["overlays"] == list(done.overlays)


def test_the_box_is_the_scenes_own_measurement_and_sits_inside_the_frame(tmp_path):
    result = _apply(tmp_path, _shots(tmp_path))
    (row,) = result.overlaid[0].overlays
    box = row["box"]
    assert 0 <= box["x"] and box["x"] + box["w"] <= 1080
    assert 0 <= box["y"] and box["y"] + box["h"] <= 1920
    bubble = {k: v for k, v in _OVERLAY.items() if k in ("kind", "text", "arrive_s")}
    assert box == cb.bubble_geometry(kit=_kit(), ratio="9:16", bubble=bubble)


def test_the_bubble_lands_in_its_box_during_its_window_and_nowhere_else(tmp_path):
    """Pixels, not bookkeeping: inside the window the box region differs from the source clip;
    outside the window (after `end_s`) the whole frame is the source again — the sequence was
    padded, not looped."""
    result = _apply(tmp_path, _shots(tmp_path))
    done = result.overlaid[0]
    src = Path(done.src_path)
    out = Path(done.out_path)
    (row,) = done.overlays
    b = row["box"]
    box = (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])
    # Well inside the window and well past the entrance.
    assert _mean_diff(_frame_at(src, 1.0), _frame_at(out, 1.0), box) > 20
    # A region far from the bubble is untouched — no scrim.
    assert _mean_diff(_frame_at(src, 1.0), _frame_at(out, 1.0), (0, 0, 200, 200)) < 4
    # After the window the bubble is gone.
    assert _mean_diff(_frame_at(src, 1.8), _frame_at(out, 1.8), box) < 4


def test_the_output_keeps_the_shots_duration_and_audio(tmp_path):
    result = _apply(tmp_path, _shots(tmp_path))
    out = Path(result.overlaid[0].out_path)
    assert abs(vf._probe_duration(out) - 2.0) < 0.1
    assert vf._probe_stream_kinds(out) == (True, True)


def test_a_list_of_overlays_composites_each_in_order(tmp_path):
    """ "A chip, then a bubble" is two entries, never a compound kind."""
    shots = _shots(tmp_path)
    shots[1]["production"]["overlay"] = [
        {"kind": "action chip", "text": "Open the app", "end_s": 1.0},
        {"kind": "outgoing bubble", "text": "Sent.", "start_s": 1.0},
    ]
    result = _apply(tmp_path, shots)
    (done,) = result.overlaid
    kinds = [row["kind"] for row in done.overlays]
    assert kinds == ["chip", "outgoing"]
    assert done.overlays[0]["anchor"]["side"] == "left"
    assert done.overlays[1]["anchor"]["side"] == "right"
    assert done.overlays[1]["start_s"] == 1.25  # window start plus arrive_s


# ── refusals, not skips ────────────────────────────────────────────────────────────────────


def test_a_missing_file_is_a_refusal_not_a_skip(tmp_path):
    shots = _shots(tmp_path)
    shots[1]["file"] = "shots/nope.mp4"
    with pytest.raises(ValueError, match="does not exist"):
        _apply(tmp_path, shots)


def test_a_base_of_the_wrong_size_is_refused(tmp_path):
    """The scene draws at the ratio's native size and lands at (0, 0) unscaled; a smaller base
    would put the bubble at the wrong scale and the sidecar's box in a frame that does not
    exist."""
    _make_shot(tmp_path / "shots" / "small.mp4", w=640, h=360)
    shots = [{"id": "s", "file": "shots/small.mp4", "production": {"overlay": dict(_OVERLAY)}}]
    with pytest.raises(ValueError, match="reframe the shot first"):
        _apply(tmp_path, shots)


def test_an_unknown_overlay_key_is_refused_by_name(tmp_path):
    shots = _shots(tmp_path)
    shots[1]["production"]["overlay"]["txet"] = "typo"
    with pytest.raises(ValueError, match=r"unknown keys \['txet'\]"):
        _apply(tmp_path, shots)


def test_a_backwards_window_is_refused(tmp_path):
    shots = _shots(tmp_path)
    shots[1]["production"]["overlay"].update({"start_s": 1.5, "end_s": 1.0})
    with pytest.raises(ValueError, match="not a forward span"):
        _apply(tmp_path, shots)


def test_shot_ids_naming_an_unknown_shot_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not in this shot list"):
        _apply(tmp_path, _shots(tmp_path), shot_ids=["shot-99"])


# ── the CLI: confined, and reporting ───────────────────────────────────────────────────────


def _shots_doc(tmp_path: Path) -> Path:
    doc = tmp_path / "film.shots.json"
    doc.write_text(json.dumps({"slug": "film", "shots": _shots(tmp_path)}), encoding="utf-8")
    return doc


def test_the_overlays_cli_refuses_an_out_dir_outside_the_content_root(tmp_path, capsys):
    root = tmp_path / "content"
    root.mkdir()
    argv = ["overlays", "--shots", str(_shots_doc(tmp_path)), "--ratio", "9:16"]
    argv += [
        "--profile",
        "p",
        "--out-dir",
        str(tmp_path / "elsewhere"),
        "--content-root",
        str(root),
    ]
    assert vf.main(argv) == 2
    assert "outside the resolved content root" in capsys.readouterr().err


def test_the_overlays_cli_runs_the_path_and_reports_json(tmp_path, capsys):
    root = tmp_path / "content"
    profiles = tmp_path / "profiles"
    kit_dir = profiles / "fixture" / "knowledge"
    kit_dir.mkdir(parents=True)
    (kit_dir / "BRAND.toml").write_text(
        f'[typography.font_files]\ncaption = "{_FONT_ABS}"\n', encoding="utf-8"
    )
    argv = ["overlays", "--shots", str(_shots_doc(tmp_path)), "--ratio", "9:16"]
    argv += ["--profile", "fixture", "--profiles-root", str(profiles)]
    argv += ["--out-dir", str(root / "out"), "--content-root", str(root), "--fps", "12", "--json"]
    assert vf.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ratio"] == "9:16"
    assert [o["shot_id"] for o in payload["overlaid"]] == ["shot-02"]
    assert payload["skipped"] == [
        {"shot_id": "shot-01", "why": "no production.overlay — nothing to draw"}
    ]
    assert (root / "out" / "shot-02-overlaid.mp4").is_file()
    assert (root / "out" / "shot-02-overlaid.overlays.json").is_file()


def test_the_overlay_scene_cli_composites_a_drawn_sequence_over_a_shot(tmp_path, capsys):
    root = tmp_path / "content"
    frames = tmp_path / "frames"
    n = cb.render_chat_bubble_frames(
        kit=_kit(), ratio="9:16", fps=8, duration_s=2.0, out_dir=frames, bubble={"text": "Hi."}
    )
    assert n == 16
    clip = _make_shot(tmp_path / "shots" / "beat.mp4")
    out = root / "beat-overlaid.mp4"
    base = [
        "overlay-scene",
        "--in",
        str(clip),
        "--frames-glob",
        str(frames / "chat-bubble-%04d.png"),
    ]
    base += ["--fps", "8", "--content-root", str(root), "--json"]
    assert vf.main([*base, "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {"out_path": str(out), "fps": 8}
    assert out.is_file()
    assert vf.main([*base, "--out", str(tmp_path / "elsewhere.mp4")]) == 2
