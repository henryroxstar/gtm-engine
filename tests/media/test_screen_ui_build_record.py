"""``gtm_core.screen_ui.build_record`` — the fingerprint that proves an out-dir's PNGs still
match the inputs that produced them (`glittery-sleeping-yao` change #3).

Real Pillow-free, stdlib-only checks: this module never touches pixels, only file bytes and JSON,
so its tests build small fixture files directly rather than rendering a scene.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.screen_ui import build_record as br

KIT = {
    "palette": {"canvas": "#0B1A2E", "accent": "#00C6C6"},
    "typography": {"font_files": {"caption": "does-not-exist.ttf"}},
}


def _extras(image: Path, **more) -> dict:
    return {"image": image, **more}


# ── fingerprint ─────────────────────────────────────────────────────────────────────────────────


def test_fingerprint_changes_when_image_bytes_change(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"one")
    fp1 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    img.write_bytes(b"two")
    fp2 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    assert fp1 != fp2


def test_fingerprint_unchanged_when_a_file_is_moved_with_identical_bytes(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"stable content")
    fp1 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    moved = tmp_path / "renamed.png"
    img.rename(moved)
    fp2 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(moved), KIT, "caption", None)
    assert fp1 == fp2


def test_fingerprint_changes_on_stills_order(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    img = tmp_path / "img.png"
    img.write_bytes(b"img")
    fp1 = br.fingerprint(
        "phone-walkthrough", "9:16", 24, 2.0, _extras(img, stills=[a, b]), KIT, "caption", None
    )
    fp2 = br.fingerprint(
        "phone-walkthrough", "9:16", 24, 2.0, _extras(img, stills=[b, a]), KIT, "caption", None
    )
    assert fp1 != fp2


@pytest.mark.parametrize(
    "extra_key,extra_value",
    [
        ("crop_frac", (0.1, 0.2, 0.9, 0.8)),
        ("title", {"line_one": "Tidewater"}),
        ("message", {"body": "hello"}),
        ("bubble", {"text": "hi"}),
        ("cta_text", "Free on the App Store"),
        ("logo_variant", "symbol"),
        ("speak_from_s", 1.5),
    ],
)
def test_fingerprint_changes_on_every_extras_field_not_a_hand_picked_subset(
    tmp_path, extra_key, extra_value
):
    """The earlier draft this replaced hashed only image/actions/stills — crop_frac, title,
    message, bubble, cta_text, logo_variant, speak_from_s all change a render without touching an
    image or an actions file, so each must independently move the fingerprint."""
    img = tmp_path / "a.png"
    img.write_bytes(b"same")
    base = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    changed = br.fingerprint(
        "hero-reveal",
        "9:16",
        24,
        2.0,
        _extras(img, **{extra_key: extra_value}),
        KIT,
        "caption",
        None,
    )
    assert base != changed, f"{extra_key} did not move the fingerprint"


def test_fingerprint_changes_on_kit_bytes(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"same")
    fp1 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    other_kit = {**KIT, "palette": {**KIT["palette"], "canvas": "#FFFFFF"}}
    fp2 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), other_kit, "caption", None)
    assert fp1 != fp2


def test_fingerprint_changes_on_font_file_bytes(tmp_path):
    font = tmp_path / "face.ttf"
    font.write_bytes(b"font-v1")
    kit = {**KIT, "typography": {"font_files": {"caption": str(font)}}}
    img = tmp_path / "a.png"
    img.write_bytes(b"same")
    fp1 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), kit, "caption", None)
    font.write_bytes(b"font-v2-different-length")
    fp2 = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), kit, "caption", None)
    assert fp1 != fp2


def test_fingerprint_changes_on_fps_and_duration(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"same")
    base = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None)
    assert base != br.fingerprint(
        "hero-reveal", "9:16", 30, 2.0, _extras(img), KIT, "caption", None
    )
    assert base != br.fingerprint(
        "hero-reveal", "9:16", 24, 3.0, _extras(img), KIT, "caption", None
    )


def test_fingerprint_is_best_effort_when_the_kit_has_no_configured_font(tmp_path):
    """Not every scene this record covers draws text (hero-reveal, phone-walkthrough never call
    load_face) — a kit missing font_role must degrade to a fingerprint, never raise FontMissing."""
    img = tmp_path / "a.png"
    img.write_bytes(b"same")
    bare_kit = {"palette": {"canvas": "#000000"}, "typography": {"font_files": {}}}
    fp = br.fingerprint("hero-reveal", "9:16", 24, 2.0, _extras(img), bare_kit, "caption", None)
    assert isinstance(fp, str) and len(fp) == 64


# ── build_record / read / write / delete ───────────────────────────────────────────────────────


def test_build_record_shape(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"content")
    record = br.build_record(
        scene="hero-reveal",
        ratio="9:16",
        fps=24,
        duration_s=2.0,
        extras=_extras(img),
        kit=KIT,
        font_role="caption",
        repo_root=None,
        frames=48,
        frame_pattern="hero-reveal-%04d.png",
    )
    assert record["scene"] == "hero-reveal"
    assert record["frames"] == 48
    assert record["frame_pattern"] == "hero-reveal-%04d.png"
    assert record["inputs"] == [{"arg": "image", "path": str(img), "sha256": br.digest(img)}]
    assert record["kit_sha256"]
    assert record["font_sha256"] is None  # KIT's font path does not exist on disk
    assert record["fingerprint"] == br.fingerprint(
        "hero-reveal", "9:16", 24, 2.0, _extras(img), KIT, "caption", None
    )


def test_write_then_read_round_trips(tmp_path):
    record = {"scene": "hero-reveal", "frames": 2, "fingerprint": "abc"}
    br.write(tmp_path, record)
    assert br.read(tmp_path) == record
    assert (tmp_path / br.RECORD_NAME).is_file()


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    br.write(tmp_path, {"scene": "hero-reveal"})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_read_absent_record_is_none(tmp_path):
    assert br.read(tmp_path) is None
    assert br.read(tmp_path / "does-not-exist") is None


def test_read_malformed_json_is_none_not_a_raise(tmp_path):
    (tmp_path / br.RECORD_NAME).write_text("{not valid json", encoding="utf-8")
    assert br.read(tmp_path) is None


def test_read_a_json_array_is_none_a_record_must_be_an_object(tmp_path):
    (tmp_path / br.RECORD_NAME).write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert br.read(tmp_path) is None


def test_delete_is_a_no_op_when_no_record_exists(tmp_path):
    br.delete(tmp_path)  # must not raise
    br.delete(tmp_path / "nested" / "deeper")  # nor when the directory itself is absent


def test_delete_removes_an_existing_record(tmp_path):
    br.write(tmp_path, {"scene": "x"})
    assert br.read(tmp_path) is not None
    br.delete(tmp_path)
    assert br.read(tmp_path) is None
