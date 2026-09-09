"""Contract tests for content-radar hook discovery (G-A+-4).

These tests verify that the content-radar skill body and manifest reference the
hook-bank discovery behaviour: listing existing hooks, adding candidates via the
CLI, and excluding candidates from rotation until promoted.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.packs.loader import load_pack_graph
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]


def test_content_radar_skill_is_registered():
    names = {s.name for s in all_skills()}
    assert "content-radar" in names


def test_content_radar_body_references_hook_discovery():
    body = (REPO / "plugin" / "skills" / "content-radar" / "body_template.md").read_text()
    assert "add-candidate" in body
    assert "hooks.toml" in body
    assert 'status = "candidate"' in body
    assert "candidate hooks" in body.lower()
    assert "promotes them to `test`" in body.lower()


def test_content_radar_pack_radars_load_as_brain_radar():
    for variant in ("short-form-video", "cross-modal-campaign"):
        pack = load_pack_graph(REPO / "packs" / "creator" / "graphs" / f"{variant}.toml")
        assert pack.node("radar").skill == "content-radar"
        assert pack.node("radar").model_role == "brain_radar"
