# tests/agent/test_onboard_buyer_journey.py
"""render() emits knowledge/buyer-journey.md (the why-now / journey / objections layer) and
groups competitors into tiers when the draft carries buckets. Both are new onboarding depth —
previously hand-authored per profile (see profiles/<profile>/knowledge/buyer-journey.md).

Backward-compat: a draft with no buyer_journey ships a persona-derived skeleton, and competitors
with no bucket render as the old flat table — never a broken template placeholder.
"""

from __future__ import annotations

import re

from agent.onboard import render
from tests.agent.test_onboard_settings import _base_draft

PLACEHOLDER = re.compile(r"<[^>\n]{1,60}>")

_BJ = {
    "primary_persona": "Head of Ops",
    "triggers": [
        {
            "event": "New funding round",
            "activates": "Head of Ops",
            "predicts": "budget to modernize",
            "detect_via": "press → RocketReach",
        }
    ],
    "stages": [
        {
            "stage": "Problem-aware",
            "buyer_question": "Could this be better?",
            "angle": "reframe the cost",
            "proof": "benchmark",
            "objection": "it's just how it is",
            "pillar": "p1",
        }
    ],
    "persona_deltas": [{"persona": "VP Eng", "notes": "Enters at Evaluation."}],
    "operator_confirm": ["Real sales-cycle length?", "Who signs?"],
}


def test_buyer_journey_file_always_rendered():
    """buyer-journey.md ships whether or not the draft carries a derived journey."""
    assert "knowledge/buyer-journey.md" in render(_base_draft())
    d = _base_draft()
    d["buyer_journey"] = _BJ
    assert "knowledge/buyer-journey.md" in render(d)


def test_buyer_journey_full_render():
    d = _base_draft()
    d["buyer_journey"] = _BJ
    bj = render(d)["knowledge/buyer-journey.md"]
    assert "New funding round" in bj  # trigger table
    assert "Head of Ops" in bj  # primary persona journey heading
    assert "it's just how it is" in bj  # objection column
    assert "Enters at Evaluation" in bj  # persona delta
    assert "Real sales-cycle length?" in bj  # operator-confirm item
    assert "⚠️" in bj  # confirm items flagged
    assert not PLACEHOLDER.search(bj)


def test_buyer_journey_skeleton_when_absent():
    """No buyer_journey in the draft → a persona-derived fill-in skeleton, with the default
    confirm prompts, and no <...> placeholder."""
    d = _base_draft()  # personas present, no buyer_journey
    bj = render(d)["knowledge/buyer-journey.md"]
    assert "Skeleton built from your personas" in bj
    assert "Head of Ops" in bj  # the persona from _base_draft
    assert "Confirm with your sales team" in bj
    assert not PLACEHOLDER.search(bj)


def test_buyer_journey_inferred_disclaimer_present():
    """The full render must state the journey is inferred, so the review gate treats it harder."""
    d = _base_draft()
    d["buyer_journey"] = _BJ
    bj = render(d)["knowledge/buyer-journey.md"]
    assert "inferred" in bj.lower()


def test_competitors_grouped_by_bucket():
    d = _base_draft()
    d["competitors"] = [
        {"name": "RivalCo", "differentiator": "We are faster.", "bucket": "Direct rivals"},
        {"name": "OldGuard", "differentiator": "We are modern.", "bucket": "Incumbents"},
    ]
    comp = render(d)["knowledge/competitors.md"]
    assert "## Direct rivals" in comp
    assert "## Incumbents" in comp
    assert "RivalCo" in comp and "OldGuard" in comp


def test_competitors_flat_when_no_bucket():
    """No buckets → the original flat table, no stray 'Other' heading."""
    d = _base_draft()
    d["competitors"] = [{"name": "RivalCo", "differentiator": "We are faster."}]
    comp = render(d)["knowledge/competitors.md"]
    assert "RivalCo" in comp
    assert "## Other" not in comp
    assert "| Competitor | How we differ |" in comp
