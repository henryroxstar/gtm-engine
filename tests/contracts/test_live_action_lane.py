"""Contract: the live-action lane's graph shape, alongside the existing pack-generality contracts.

`live-action-video` closes the one gap the 08-28 PRD left open: real footage is the documented
PREFERRED lane — it clears the likeness bar by construction and carries no EU AI Act Art. 50 duty
— and it was the only lane reachable exclusively by arriving with footage already shot. This lane
lets the script come first: write it, go and film it, resume.

The properties pinned here are the ones that make it that lane rather than a copy of another one.

Design: the 2026-08-29 video-router hardening note, item C8.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.packs.loader import load_pack_graph

REPO = Path(__file__).resolve().parents[2]
GRAPH = REPO / "packs" / "creator" / "graphs" / "live-action-video.toml"

#: Every skill that SYNTHESISES footage. None may appear on this lane.
_RENDER_SKILLS = {"video-render", "video-avatar", "video-storyboard", "video-restyle"}


def _graph():
    return load_pack_graph(GRAPH)


def _nodes() -> dict:
    return {n.id: n for n in _graph().nodes}


def test_the_live_action_graph_loads_clean():
    """The loader is fail-closed — unknown skill, unknown model role, a `depends_on` cycle, a
    revision back-edge, or a gate on anything but the publish mechanism is refused at load. So
    loading clean IS the structural gate."""
    graph = _graph()
    assert graph.variant == "live-action-video"
    assert graph.pack == "creator"


def test_it_has_no_render_nodes():
    """THE DEFINING PROPERTY. Nothing is synthesised, which is why the lane carries no Art. 50
    duty and why its shot list is refused if it declares one."""
    used = {n.skill for n in _graph().nodes}
    assert not (used & _RENDER_SKILLS), (
        f"live-action lane renders something: {used & _RENDER_SKILLS}"
    )


def test_the_capture_node_is_gated():
    """Without it the run never parks — and the upload leaves the machine unapproved."""
    assert _nodes()["capture"].gate is True


def test_the_script_node_precedes_the_capture_node():
    """Write-then-shoot is the ordering that distinguishes this lane from `repurpose-clips`,
    which needs footage up front."""
    assert "script" in _nodes()["capture"].depends_on


def test_publish_is_the_only_external_effect():
    """Every other node is side-effect-free. Note the capture node deliberately does NOT carry
    `external_effect`: in this codebase that field means "reaches an audience", and uploading
    footage to a private processing pipeline is not that — setting it would be a lie the loader
    would accept."""
    effects = {n.id: n.external_effect for n in _graph().nodes if n.external_effect}
    assert effects == {"publish": "publish"}, effects


def test_every_skill_in_the_graph_is_registered():
    """Guards against a lane that loads locally but is unreachable under skill scoping."""
    from gtm_core.skills import registry

    registered = {s.name for s in registry.all_skills()}
    used = {n.skill for n in _graph().nodes}
    assert used <= registered, f"unregistered skills on the lane: {sorted(used - registered)}"


def test_the_script_prompt_requires_the_live_action_capture_mode():
    """Without `capture_mode = "live_action"` on the shot list, shots_lint applies the
    rendered-list rules and refuses a perfectly valid live-action script for lacking a synthetic
    presenter engine it must not have. The prompt is the only thing that can ask for it."""
    prompt = _nodes()["script"].prompt
    assert "live_action" in prompt
    assert "capture_mode" in prompt


def test_the_script_prompt_forbids_declaring_synthetic_disclosure():
    """The mirror of the disclosure duty: claiming synthetic provenance over real footage of a
    real person is a false statement, not a cautious one."""
    prompt = _nodes()["script"].prompt.lower()
    assert "synthetic_disclosure" in prompt
    assert "do not set" in prompt or "not set" in prompt


def test_the_capture_node_prompt_requires_a_freshness_recheck():
    """C8d — the accepted cost of one graph is actually MITIGATED, not merely acknowledged.

    A run parked at this gate resumes weeks later; spending credits clipping footage of a claim
    that has since moved is the one failure mode this shape introduces.
    """
    prompt = _nodes()["capture"].prompt.lower()
    assert "research_ref" in prompt
    assert "fresh" in prompt or "aged" in prompt


def test_the_capture_prompt_checks_footage_retention_on_resume():
    """C13 -> C8d. The concrete failure this prevents: a parked run resuming onto footage the
    provider has already purged. The retention window is read LIVE, not assumed."""
    prompt = _nodes()["capture"].prompt
    assert "projectRetentionDays" in prompt
    assert "get_plan_usage" in prompt


def test_the_publish_prompt_refuses_a_synthetic_disclosure_marker():
    """Nothing was synthesised, so an identity/disclosure marker on this lane would be false."""
    prompt = _nodes()["publish"].prompt.lower()
    assert "no synthetic" in prompt or "not add one" in prompt
