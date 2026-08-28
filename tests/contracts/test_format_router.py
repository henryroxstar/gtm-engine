"""Contract tests for the format-router skill and cross-modal-campaign pack graph.

The format-router skill is a prompt-level dispatcher: these tests verify the manifest,
pack graph shape, and that the generated SKILL.md references fatigue, budget, and the
video-router hand-off. Runtime behaviour (actual format selection given a hook and budget)
is exercised in the pack E2E test.
"""

from __future__ import annotations

from pathlib import Path

from agent.packs import load_engine_graph
from gtm_core.packs.loader import load_pack_graph
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]
CROSS_MODAL_GRAPH = REPO / "packs" / "creator" / "graphs" / "cross-modal-campaign.toml"


def test_format_router_skill_is_registered():
    names = {s.name for s in all_skills()}
    assert "format-router" in names


def test_format_router_skill_body_references_key_concepts():
    body = (REPO / "plugin" / "skills" / "format-router" / "body_template.md").read_text()
    assert "fatigue" in body.lower()
    assert "budget" in body.lower()
    assert "video-router" in body
    assert "hook_score" in body
    assert "modal_family" in body


def test_cross_modal_campaign_graph_loads():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.pack == "creator"
    assert pack.variant == "cross-modal-campaign"
    assert pack.ids == (
        "radar",
        "format-plan",
        "text-studio",
        "image-studio",
        "video-script",
        "publish",
    )


def test_cross_modal_campaign_plan_node_is_format_router_gate():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    plan = pack.node("format-plan")
    assert plan.skill == "format-router"
    assert plan.gate is True
    assert plan.model_role == "brain_plan"


def test_cross_modal_campaign_fan_out_from_plan():
    _, engine_graph = load_engine_graph(CROSS_MODAL_GRAPH)
    for node_id in ("text-studio", "image-studio", "video-script"):
        assert engine_graph.node(node_id).depends_on == ("format-plan",)


def test_cross_modal_campaign_publish_joins_all_modalities():
    _, engine_graph = load_engine_graph(CROSS_MODAL_GRAPH)
    publish = engine_graph.node("publish")
    assert publish.gate is True
    assert publish.external_effect == "publish"
    assert set(publish.depends_on) == {"text-studio", "image-studio", "video-script"}


def test_cross_modal_campaign_uses_content_studio_for_text_and_image():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.node("text-studio").skill == "content-studio"
    assert pack.node("image-studio").skill == "content-studio"


def test_cross_modal_campaign_uses_video_script_for_video():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.node("video-script").skill == "video-script"
