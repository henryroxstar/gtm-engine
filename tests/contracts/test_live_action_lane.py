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


def test_the_capture_prompt_points_at_the_phone_readable_shot_list():
    """C3. The operator does not read `.shots.json` on a phone at seven in the morning.

    This prompt is the ONLY place the instruction can live — a `PackNode` has no `gate_message`
    field, so the gate's text is the node prompt — which is why it is asserted here rather than
    trusted to a skill body the operator never sees at the gate.
    """
    prompt = _nodes()["capture"].prompt
    assert "--render-shotlist" in prompt
    assert "shotlist.md" in prompt


def test_the_lane_reports_ready_from_disk_without_resolving_an_engine(monkeypatch):
    """C3-T7. The live-action lane needs no identity handle and no render engine, so its
    feasibility is answerable from disk alone — zero spend, zero network, zero engine resolution.

    The negative controls are in-process (a poisoned resolver, a poisoned httpx) rather than a
    shell assertion, because a subprocess would prove only that THIS invocation was quiet.
    """
    import httpx

    import gtm_core.render_engines as render_engines
    from gtm_core.video_preflight import lane_status

    def _boom(*a, **k):  # pragma: no cover — the point is that none of these run
        raise AssertionError("the live-action lane spent something to answer a disk question")

    monkeypatch.setattr(render_engines, "resolve_engine", _boom)
    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    status = lane_status(
        "live-action-video",
        kit={},
        graphs_dir=GRAPH.parent,
        active_packs=frozenset({"creator"}),
    )
    assert status.ready, f"the lane is not ready with an empty kit: {status}"
    assert getattr(status, "engine", None) is None, "a live-action lane resolved a render engine"


def test_the_lane_is_blocked_when_the_creator_pack_is_not_active():
    """Positive control for the readiness above: it is a real check, not a constant True."""
    from gtm_core.video_preflight import lane_status

    status = lane_status(
        "live-action-video", kit={}, graphs_dir=GRAPH.parent, active_packs=frozenset()
    )
    assert not status.ready
    assert "creator" in (status.blocked_reason or "").lower()


def test_capture_mode_has_exactly_one_author_in_the_tree():
    """C3-T11. `capture_mode` is SET in one place and read in several.

    A second writer is how the two halves of this contract drift: the linter would apply the
    live-action exemptions to a list the pack never marked as a shoot, or the reverse. Readers are
    unbounded and fine; this pins the write side.
    """
    import re

    repo = Path(__file__).resolve().parents[2]
    assign = re.compile(r'capture_mode\s*=\s*\\?"|"capture_mode"\s*:')
    authors: list[str] = []
    for root in ("packs", "plugin", "gtm_core"):  # scanned in this order; the list below matches
        for path in sorted((repo / root).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".toml", ".md", ".py", ".json"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if assign.search(text):
                authors.append(str(path.relative_to(repo)))

    # Pinned as an exact set, each entry justified, rather than as a count. Only the first
    # INSTRUCTS a shot list to set the field; the other two match because a naive scan cannot
    # tell an assignment from a string that talks about one, and pretending otherwise would make
    # this a test that passes for the wrong reason:
    #
    #   packs/.../live-action-video.toml  — THE AUTHOR. The script node's prompt is what tells a
    #                                       run to set `capture_mode = "live_action"`.
    #   gtm_core/creator_brief.py         — a display-label map (`"capture_mode": "Capture
    #                                       contract"`) for the brief's markdown twin.
    #   gtm_core/shots_lint/identity.py   — advice inside an error message, telling an operator
    #                                       which value to set. Advice, not a write.
    #   gtm_core/video_presets.py         — preset dictionary template converting preset decisions
    #                                       into creator brief decision envelopes.
    #
    # A new entry here means a second place is deciding the mode. That is the drift this pins:
    # the linter applying live-action exemptions to a list the pack never marked as a shoot, or
    # the reverse.
    assert authors == [
        "packs/creator/graphs/live-action-video.toml",
        "gtm_core/creator_brief.py",
        "gtm_core/shots_lint/identity.py",
        "gtm_core/video_presets.py",
    ], (
        f"the capture_mode surface changed: {authors}. Exactly one place may INSTRUCT a run to "
        "set it (the live-action graph's script node); the other two are a label map and an "
        "error message. A new entry is a second author — read it before widening this list."
    )
