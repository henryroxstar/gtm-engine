"""Tests for gtm_core.video_presets and two-door routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import video_preflight as vp
from gtm_core.video_presets import (
    CORE_PRESET_KEYS,
    PresetError,
    VideoPreset,
    load_presets,
    resolve_preset,
)

REPO = Path(__file__).resolve().parents[2]


def test_core_presets_load_from_template():
    presets = load_presets("_template")
    for k in CORE_PRESET_KEYS:
        assert k in presets, f"Missing core preset {k}"
        p = presets[k]
        assert isinstance(p, VideoPreset)
        assert len(p.slot_schema) >= 2
        assert len(p.broll_list) >= 1
        assert p.capture_mode in ("rendered", "live_action")


def test_preset_to_brief_decisions_schema_compatible():
    preset = resolve_preset("_template", "product-walkthrough")
    decisions = preset.to_brief_decisions()
    assert "cover" in decisions
    assert "outlier_structure" in decisions
    assert "slot_schema" in decisions
    assert "cheapest_medium" in decisions
    assert "capture_mode" in decisions
    assert "invariant" in decisions
    assert "broll_list" in decisions
    assert "sampling_curve" in decisions
    assert "visual_hook" in decisions
    assert decisions["capture_mode"]["value"] == "rendered"
    assert decisions["slot_schema"]["value"] == preset.slot_schema


def test_unknown_preset_raises_preset_error():
    with pytest.raises(PresetError, match="Unknown preset"):
        resolve_preset("_template", "nonexistent-preset-slug")


def test_tenant_override_preset(tmp_path: Path):
    root = tmp_path / "profiles"
    root.mkdir(parents=True)
    (root / "_template").mkdir(parents=True)
    (root / "_template" / "video-presets.toml").write_text(
        """
[presets.product-walkthrough]
name = "Product Walkthrough"
description = "Template desc"
cover = "frame"
outlier_structure = "linear"
slot_schema = ["hook", "action"]
cheapest_medium = "video_required"
capture_mode = "rendered"
invariant = "dark"
broll_list = ["screen"]
sampling_curve = "1,1"
visual_hook = "cursor"
""",
        encoding="utf-8",
    )

    # Tenant overrides slot_schema
    (root / "acme").mkdir(parents=True)
    (root / "acme" / "video-presets.toml").write_text(
        """
[presets.product-walkthrough]
name = "Acme Walkthrough"
description = "Acme desc"
cover = "frame"
outlier_structure = "linear"
slot_schema = ["acme_hook", "acme_demo", "acme_cta"]
cheapest_medium = "video_required"
capture_mode = "rendered"
invariant = "dark"
broll_list = ["screen"]
sampling_curve = "1,1"
visual_hook = "cursor"
""",
        encoding="utf-8",
    )

    loaded = load_presets("acme", profiles_root=root)
    assert loaded["product-walkthrough"].name == "Acme Walkthrough"
    assert loaded["product-walkthrough"].slot_schema == ["acme_hook", "acme_demo", "acme_cta"]


def test_malformed_preset_raises_fail_closed(tmp_path: Path):
    root = tmp_path / "profiles"
    (root / "_template").mkdir(parents=True)
    (root / "_template" / "video-presets.toml").write_text(
        """
[presets.broken]
name = "Broken"
# missing required fields
""",
        encoding="utf-8",
    )
    with pytest.raises(PresetError, match="missing required fields"):
        load_presets("_template", profiles_root=root)


def test_two_door_routing_door_1():
    pf = vp.preflight("_template")
    res1 = pf.route("https://www.youtube.com/watch?v=12345")
    assert res1.door == 1
    assert res1.recommended_lane == "repurpose-clips"

    res2 = pf.route("demo-video.mp4")
    assert res2.door == 1
    assert res2.recommended_lane == "demo-clips"


def test_two_door_routing_door_2():
    pf = vp.preflight("_template")
    res1 = pf.route("Make a video about our new security feature")
    assert res1.door == 2
    assert res1.recommended_lane == "short-form-video"


def test_two_door_routing_ambiguous_requires_clarification():
    pf = vp.preflight("_template")
    res = pf.route("hi")
    assert res.requires_clarification
    assert res.clarification_question is not None


def test_video_presets_cli_invocations():
    from gtm_core import video_presets as vp_mod

    # list via subcommand
    assert vp_mod.main(["list", "--profile", "_template"]) == 0
    # list via default
    assert vp_mod.main(["--profile", "_template"]) == 0
    # resolve via subcommand
    assert vp_mod.main(["resolve", "product-walkthrough", "--profile", "_template"]) == 0
    # resolve via positional preset
    assert vp_mod.main(["product-walkthrough", "--profile", "_template"]) == 0
    # resolve via option
    assert vp_mod.main(["--preset", "product-walkthrough", "--profile", "_template", "--json"]) == 0
