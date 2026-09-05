"""Caption placement and the V3 face-band rule (2026-08-19).

The defect: ``captions.render`` centred the block inside the safe box. That sounds safe and is the
worst available position — the safe area reserves *edges*, so the centre is simultaneously
furthest from every margin and squarely on the speaker's mouth. For 4:5 it computed to y=595,
h=106 in a 1350px frame (44-52% of frame height). Twenty-four screens shipped there on 2026-08-18
and the operator's second note was "text covering face".

The brand kit had the right answer the whole time: the live tenant declares
``[captions].preset = "system_indigo"``, which Reap's own catalogue describes as upper placement.
The renderer never read it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import captions as cap
from gtm_core import video_lint as vl

REPO = Path(__file__).resolve().parents[2]
#: Any tenant's caption font will do — this exercises geometry, not branding. Resolved by
#: glob so the test stays tenant-agnostic (no profile name may appear in tests, §R9).
FONT = next(
    iter(sorted(REPO.glob("profiles/*/knowledge/brand/fonts/*.ttf"))), REPO / "__absent__.ttf"
)

pytestmark = pytest.mark.skipif(not FONT.is_file(), reason="caption font not present")


def _kit(**captions_section) -> dict:
    kit = {"typography": {"font_files": {"caption": str(FONT)}}}
    if captions_section:
        kit["captions"] = captions_section
    return kit


def _screens():
    return cap.split_screens("nobody had locked that door every member could open it", total_s=6.0)


# --- placement resolution ------------------------------------------------------------------


def test_default_placement_is_lower_not_center():
    """The historical behaviour (centre) is the defect; it must not be what you get by default."""
    assert cap.DEFAULT_PLACEMENT == "lower"
    assert cap.resolve_placement({}) == "lower"


def test_an_explicit_placement_wins():
    assert cap.resolve_placement({"captions": {"placement": "upper"}}) == "upper"


def test_an_upper_placement_preset_implies_upper():
    """system_indigo is upper placement per Reap's own catalogue — the tenant kit already said
    so while the renderer centred anyway."""
    assert cap.resolve_placement({"captions": {"preset": "system_indigo"}}) == "upper"


def test_an_animated_preset_does_not_imply_upper():
    """Motion and face-safety are a real trade-off in Reap's system catalogue, not a free
    combination — kinetic typography is not an upper-placement preset."""
    assert cap.resolve_placement({"captions": {"preset": "system_kinetic_typography"}}) == "lower"


def test_an_explicit_placement_beats_the_preset_inference():
    kit = {"captions": {"preset": "system_indigo", "placement": "lower"}}
    assert cap.resolve_placement(kit) == "lower"


def test_an_unknown_placement_degrades_to_the_default_rather_than_raising():
    """Placement is a layout preference; a typo must not block a finish run. (Contrast the caption
    FONT, which fails loudly, because a silent substitute would ship off-brand type.)"""
    assert cap.resolve_placement({"captions": {"placement": "middle-ish"}}) == "lower"


# --- geometry ------------------------------------------------------------------------------


def test_render_rejects_an_unknown_placement():
    with pytest.raises(ValueError, match="unknown placement"):
        cap.render(_screens(), ratio="4:5", kit=_kit(), out_dir=Path("/tmp/x"), placement="middle")


@pytest.mark.parametrize("placement", ["upper", "lower"])
def test_upper_and_lower_clear_the_face_band(tmp_path, placement):
    area = vl.SAFE_AREAS["4:5"]
    rendered = cap.render(
        _screens(), ratio="4:5", kit=_kit(), out_dir=tmp_path, placement=placement
    )
    assert rendered
    for r in rendered:
        assert not cap.overlaps_face_band(r.box["y"], r.box["h"], area.height, "4:5"), (
            f"{placement} caption at y={r.box['y']} h={r.box['h']} still hits the face band"
        )


def test_center_reproduces_the_shipped_defect(tmp_path):
    """Guard the regression from the other direction: `center` must still be the position that
    lands on the face, so the V3 rule below has something real to catch."""
    area = vl.SAFE_AREAS["4:5"]
    rendered = cap.render(_screens(), ratio="4:5", kit=_kit(), out_dir=tmp_path, placement="center")
    assert any(cap.overlaps_face_band(r.box["y"], r.box["h"], area.height, "4:5") for r in rendered)


def test_every_placement_stays_inside_the_safe_box(tmp_path):
    """Placement moves the block WITHIN the reserved area — it must never push it outside, or
    V3's original containment check would start failing."""
    area = vl.SAFE_AREAS["4:5"]
    box_x, box_y, box_w, box_h = cap._safe_box_px(area)
    for placement in cap.PLACEMENTS:
        for r in cap.render(
            _screens(), ratio="4:5", kit=_kit(), out_dir=tmp_path / placement, placement=placement
        ):
            assert box_y <= r.box["y"]
            assert r.box["y"] + r.box["h"] <= box_y + box_h
            assert box_x <= r.box["x"]
            assert r.box["x"] + r.box["w"] <= box_x + box_w


# --- V3 face-band rule ---------------------------------------------------------------------


def _probe(**kw):
    defaults = {
        "width": 1080,
        "height": 1350,
        "fps": 30.0,
        "duration_s": 10.0,
        "bit_rate": 8_000_000,
    }
    defaults.update(kw)
    return vl.Probe(**defaults)


def _manifest(y: int, h: int = 106) -> dict:
    return {
        "frame": [1080, 1350],
        "screens": [{"index": 0, "box": {"x": 100, "y": y, "w": 800, "h": h}}],
    }


def test_v3_errors_on_a_caption_over_the_face_when_an_identity_is_on_screen():
    findings = vl.evaluate(
        _probe(),
        ratio="4:5",
        manifest=_manifest(y=595),  # the exact shipped geometry
        identity_used=["element", "voice"],
    )
    hits = [f for f in findings if f.rule == "caption_over_face"]
    assert len(hits) == 1
    assert hits[0].severity == vl.ERROR
    assert "297-837" in hits[0].excerpt


def test_v3_only_warns_when_no_identity_was_rendered():
    """A pure screen-capture asset has no face to cover — still worth flagging, not worth
    failing."""
    findings = vl.evaluate(_probe(), ratio="4:5", manifest=_manifest(y=595), identity_used=[])
    hits = [f for f in findings if f.rule == "caption_over_face"]
    assert len(hits) == 1
    assert hits[0].severity == vl.WARN


def test_v3_is_clean_for_a_lower_third_caption():
    findings = vl.evaluate(
        _probe(), ratio="4:5", manifest=_manifest(y=1050), identity_used=["element"]
    )
    assert not [f for f in findings if f.rule == "caption_over_face"]


def test_v3_is_clean_for_an_upper_caption():
    findings = vl.evaluate(
        _probe(), ratio="4:5", manifest=_manifest(y=115), identity_used=["element"]
    )
    assert not [f for f in findings if f.rule == "caption_over_face"]
