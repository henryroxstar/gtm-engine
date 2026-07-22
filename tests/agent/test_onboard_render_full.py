# tests/agent/test_onboard_render_full.py
"""Guard: no rendered onboarding file may ship a raw `<...>` template placeholder.

The staged files are reviewed by a semi-technical founder (the artifact IS the
interface — PRD docs/prds/2026-07-19-onboarding-front-door.md). Literal `<...>`
template syntax reads as broken software, not a draft to fill in. This test
renders a full draft and fails if ANY output file still contains one.

Covers Task 3: `_render_icp_md` used to ship `<Enterprise / Startup, ...>`,
`<N>`, `<M>`, `<ceiling>`, `<criterion (pts)>` etc. in its "Scoring & gates"
section. The fix derives real starting values from the draft's `icp` data and
`settings.segment_mix` instead.
"""

from __future__ import annotations

import re

from agent.onboard import render
from tests.agent.test_onboard_settings import _base_draft

PLACEHOLDER = re.compile(r"<[^>\n]{1,60}>")  # matches <…>, <N>, <criterion (pts)>, etc.


def test_no_angle_bracket_placeholders_ship():
    files = render(_base_draft({"segment_mix": "70% startup / 30% enterprise"}))
    offenders = {
        path: PLACEHOLDER.findall(text) for path, text in files.items() if PLACEHOLDER.search(text)
    }
    assert not offenders, f"placeholders leaked into staged files: {offenders}"


def test_full_bundle_files_present():
    """Task 4: render() ships the peer-quality bundle — voice-bans (evidence, full),
    case-studies (experience — a fill-in prompt, never a fabricated customer story),
    and audience-psychology (evidence, derived from real ICP pain_points/goals)."""
    files = render(_base_draft())
    assert "knowledge/voice-bans.txt" in files
    assert "knowledge/case-studies.md" in files
    assert "knowledge/audience-psychology.md" in files
    assert "synergy" in files["knowledge/voice-bans.txt"]  # seeded from ban_list
    cs = files["knowledge/case-studies.md"]
    assert "Add one customer win" in cs or "your own" in cs.lower()  # a prompt, not a fabrication
    assert "Acme helped" not in cs  # no invented story
