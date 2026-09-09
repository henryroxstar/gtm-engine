"""Contract: `video-router` defines what it does when no operator is present.

The router's Step 1 is a menu — it asks the operator which lane they want. That is right in a
conversation and wrong inside a graph run: `cross-modal-campaign.toml`'s `video-script` node hands
off to the router from a `[[nodes]]` prompt, and that node is **not a gate**, so nobody is there to
answer. A skill whose central step is a question, invoked where questions cannot be answered, has
two failure modes and both are bad — hang waiting for input, or pick a lane arbitrarily and spend
against it.

The behaviour has to be written down rather than inferred, and the two artefacts that describe it
— the skill body and the node prompt that triggers it — must not drift apart. Hence the fourth
test.

The router is a prompt, so these are contract tests over the body text, in the shape of the
existing `tests/contracts/` checks.

Design: the 2026-08-29 video-router hardening note, item C2.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BODY = REPO / "plugin" / "skills" / "video-router" / "body_template.md"
CROSS_MODAL = REPO / "packs" / "creator" / "graphs" / "cross-modal-campaign.toml"


def _body() -> str:
    """Body text with runs of whitespace collapsed.

    Markdown hard-wraps, so any phrase these tests look for can straddle a newline. Collapsing
    first means the contract is checked, not the line width.
    """
    return re.sub(r"\s+", " ", BODY.read_text(encoding="utf-8"))


def test_the_body_defines_behaviour_when_no_operator_is_present():
    """There is a headless block, and it names the field the answer comes from."""
    text = _body()
    lowered = text.lower()
    assert "no operator" in lowered or "headless" in lowered, (
        "the body never says what to do when it is invoked with nobody to answer the menu"
    )
    assert "default_lane" in text, (
        "the headless path does not name the preflight's `default_lane` as the fallback"
    )


def test_the_body_forbids_the_menu_in_a_graph_run():
    """Never present the menu — stated as a prohibition, not a preference. A menu emitted into a
    node report is at best noise and at worst a run that waits forever."""
    text = _body().lower()
    assert "never present the menu" in text, (
        "the body does not forbid presenting the menu when there is no operator"
    )


def test_the_body_fails_the_node_when_every_lane_is_blocked():
    """Exit 2 fails the node rather than picking a lane.

    A blocked lane inside a graph is a run that cannot finish. Choosing one anyway converts a
    clean node-report failure into a spend that dies at the first paid step, which is strictly
    later and strictly more expensive.
    """
    text = _body()
    lowered = text.lower()
    assert "exit 2" in lowered or "exits 2" in lowered
    assert "fail the node" in lowered, (
        "the body does not say to fail the node when the preflight reports every lane blocked"
    )


def test_cross_modal_video_node_prompt_names_the_headless_contract():
    """The two artefacts must not drift.

    The node prompt is what actually triggers the hand-off; if it says "note the hand-off to
    video-router" and nothing more, the headless rule in the body is a rule the caller never
    learns about.
    """
    toml = CROSS_MODAL.read_text(encoding="utf-8")
    assert "video-router" in toml
    lowered = toml.lower()
    assert "default_lane" in lowered or "headless" in lowered, (
        "cross-modal-campaign's video node hands off to video-router without naming the headless "
        "contract, so the router is invoked with no operator and no instruction about it"
    )
