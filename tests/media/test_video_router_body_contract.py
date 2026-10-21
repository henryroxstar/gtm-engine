"""Contract: the router body names each known request shape and gives it a stated answer.

Two of the nine findings behind the hardening PRD are not bugs — they are request shapes that
reached the router from real sessions and got no answer:

* **A batch** ("make me three clips from this") — 2026-08-27. Ambiguous: one routing decision or
  three?
* **A stock avatar** ("I don't want me in it; a different avatar is fine") — 2026-08-24. Neither
  `presenter-video` (which renders the operator's own trained twin) nor faceless (which drops the
  presenter entirely) is the answer, and bending it into either is worse than saying so.

A request shape with no stated answer gets improvised differently every session. Writing the
answer down — including "no lane covers this" — is what makes the behaviour reproducible.

The last test here is the regression that matters most: adding request shapes is exactly the kind
of change that erodes a never-guess guardrail, so it is pinned.

Design: the 2026-08-29 video-router hardening note, items C4 and C6.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BODY = REPO / "plugin" / "skills" / "video-router" / "body_template.md"

if not BODY.is_file():
    pytest.skip(
        "video-router body_template.md not present in this distribution (paid-tier stub)",
        allow_module_level=True,
    )


def _body() -> str:
    """Body text with runs of whitespace collapsed.

    Markdown hard-wraps, so any phrase these tests look for can straddle a newline. Collapsing
    first means the contract is checked, not the line width.
    """
    return re.sub(r"\s+", " ", BODY.read_text(encoding="utf-8"))


def test_the_body_defines_batch_request_handling():
    """N videos is ONE routing decision, not N. The pack graph fans out; the router does not."""
    text = _body().lower()
    assert "one routing decision" in text, (
        "the body does not say that a request for N videos is a single routing decision"
    )


def test_the_body_says_the_batch_directive_carries_the_item_count():
    """Routing once but emitting N directives would start N runs — the fan-out belongs to the
    graph, so the count rides on one directive."""
    text = _body().lower()
    assert "item count" in text, (
        "the body does not say the single directive carries the item count, which is what stops "
        "one batch request becoming N separate runs"
    )


def test_the_body_allows_per_clip_routing_when_the_lanes_genuinely_differ():
    """The rule is 'one decision', not 'never re-route'. A batch mixing footage and from-scratch
    is genuinely two lanes, and collapsing it would be the opposite error."""
    text = _body().lower()
    assert "genuinely differ" in text or "differ in lane" in text


def test_the_body_still_refuses_to_guess():
    """Regression on the property most at risk from adding request shapes: every new shape is a
    new temptation to route something 'close enough'."""
    text = _body()
    assert "Never guess" in text, "the never-guess guardrail has been lost"
    assert "do not bend it into the nearest one" in text.lower(), (
        "the guardrail against bending an unmatched request into the nearest lane has been lost"
    )


# --- the live-action lane (C8c) ----------------------------------------------------------------
#
# "Write it from scratch, then I'll shoot it myself on a real camera" was the 2026-08-28 dead end:
# real footage is the DOCUMENTED PREFERRED lane (no Article 50 duty, clears the likeness bar by
# construction) and it was the one lane reachable only by arriving with footage already shot.


def test_the_body_routes_write_then_shoot_to_live_action():
    """The request shape now reaches a real lane."""
    text = _body()
    assert "live-action-video" in text, "the routing table does not offer the live-action lane"


def test_the_body_no_longer_reports_write_then_shoot_as_a_gap():
    """Regression on the STALE-GUARDRAIL class.

    The unmatched-request guardrail names this exact shape as covered by no lane. That block is
    true today and becomes false the moment the graph lands, so it has to be deleted in the same
    change — otherwise the router tells the operator the lane does not exist while the graph sits
    next to it, which is worse than the original gap: it stops them looking.
    """
    text = _body().lower()
    assert "then i'll shoot it myself" not in text, (
        "the unmatched-request guardrail still reports write-then-shoot as having no lane, but "
        "live-action-video now covers it"
    )


def test_the_guardrail_still_refuses_shapes_that_genuinely_have_no_lane():
    """Positive control: deleting one stale example must not delete the RULE. Without this the
    test above is satisfied by removing the whole guardrail, which is the opposite of the fix."""
    text = _body().lower()
    assert "matches no lane" in text or "matches none" in text, (
        "the unmatched-request guardrail itself has been lost, not just its stale example"
    )


# --- the stock avatar (C4, amended by C9) -------------------------------------------------------
#
# "I do not want me to appear in it; a different avatar is fine" — 2026-08-24. The identity model
# has two positions, operator likeness or faceless, and this is a third. C9's catalog probe found
# `character-sheet`, which builds a consistent non-real character across views, so the honest
# answer moved from "nothing covers this" to "a generated character answers the likeness half and
# none of the talking half". Either way the routing rule is the same: name it, do not bend it.


def test_the_body_names_the_stock_avatar_case():
    """A request shape with no stated answer gets improvised differently every session."""
    text = _body().lower()
    assert "stock avatar" in text, (
        "the body does not name the stock-avatar request shape, so it has no stated answer and "
        "gets re-improvised every session"
    )


def test_the_body_does_not_route_a_stock_avatar_to_presenter_video():
    """The specific bend the guardrail exists to forbid: `presenter-video` renders the operator's
    OWN trained twin, which is the one thing this operator said they did not want."""
    text = _body()
    collapsed = text.lower()
    window_start = collapsed.find("stock avatar")
    assert window_start != -1, "the stock-avatar case is not named at all"
    # Wide enough to hold the whole block: the disclosure fact is the last of the three and
    # the point of the window is only that these assertions are about THIS block.
    window = collapsed[window_start : window_start + 1600]
    assert "presenter-video" in window, (
        "the stock-avatar block does not name `presenter-video`, so it does not forbid the bend "
        "that actually happens"
    )
    assert "trained twin" in window or "own twin" in window, (
        "the block does not say WHY presenter-video is wrong here (it renders the operator's own "
        "trained twin) — a rule with no reason is one a future session overturns"
    )
    assert "disclos" in window, (
        "the block does not carry the disclosure duty forward. A stock avatar needs no likeness "
        "consent, which is exactly why it reads as the cheap option — but it is still synthetic "
        "and the Art. 50 duty is unchanged."
    )
