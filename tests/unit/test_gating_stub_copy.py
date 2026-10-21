"""Test that private skill stub bodies provide a warm hand-off, not a jarring stop."""

from __future__ import annotations

from gtm_core import gating
from gtm_core.skills.registry import all_skills


def test_stub_body_is_a_warm_handoff():
    skill = next(s for s in all_skills() if s.name == "video-render")
    body = gating.render_stub_body(skill, graph_refs=[])
    assert "Stop here" not in body.split("\n")[0]
    assert "part of the hosted GTM Engine" in body
    assert "Tell the user in one plain sentence" in body
