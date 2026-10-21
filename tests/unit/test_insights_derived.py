"""strategic_lenses_html() must render from `m`, never from a fixed script (PS20 follow-up).

The 2026-09-27 review found the Insights tab's cards were typed prose — the same finding text
regardless of what the run's own data said. The fix (`insights_lenses.py`) computes every
`finding`/`context`/`evidence_base` from `m`. This test is the regression guard: build a minimal
empty model and a populated one, and confirm each lens's card actually changes when the data
does. A card that renders byte-identical on both is either still hardcoded, or has quietly
stopped reading its input.

Every card on an empty `m` is expected to name what is missing (`"No …"`, `"Not measurable"`,
`"not declared"`) rather than show a value — that is the sufficiency-gate contract this module
documents at its top. So this test's real assertion is stronger than "the text differs": on the
empty model, the card must ALSO admit the absence, not merely differ by coincidence.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from gtm_core.email_campaign_dashboard.insights_lenses import strategic_lenses_html

#: Substrings that mark a card as truthfully reporting "nothing here yet" rather than a
#: pre-written conclusion. Matched case-sensitively against the empty-model card's own text.
_ABSENCE_MARKERS = (
    "No judge run",
    "No rendered emails",
    "Not measurable yet",
    "No campaign roster",
    "No opt-out or unreadable",
    "Not measurable: reply text",
    "No campaign has declared",
    "No market gate",
    "The pre-flight gate has not refused",
    "No sendable cell size",
    "Not measurable: the outcomes ledger",
)

POPULATED_M: dict[str, Any] = {
    "profile": "acme",
    "roster": {
        "judge_tally": {
            "rows": 40,
            "dest:re-target": 10,
            "dest:re-argue": 30,
            "reargue:settled": 5,
            "reargue:open": 25,
        },
        "accounts": 10,
        "signal": 8,
        "signal_sourced": 6,
        "rows": [{"country": "US"}, {"country": "US"}, {"country": "CA"}],
    },
    "seat_fit": {"total": 20, "matched": 15, "elsewhere": 3, "unresolved": 2},
    # Replies by Seat / by Hook read the sending tool's per-sequence figures (2026-10-06), the
    # same ones the Results tab shows, so the populated model carries sequences with them.
    "campaigns": {
        "campaigns": [
            {
                "slug": "c1",
                "sequences": [
                    {
                        "sequence_id": "s-a",
                        "title": "Run · Hook A · CEO",
                        "live": {"sent": 12, "replied": 3},
                    },
                    {
                        "sequence_id": "s-b",
                        "title": "Run · Hook B · CTO",
                        "live": {"sent": 9, "replied": 1},
                    },
                ],
            }
        ]
    },
    "messages": [
        {"sequence_id": "s-a", "audience": [{"seat": "ceo"}]},
        {"sequence_id": "s-b", "audience": [{"seat": "cto"}]},
    ],
    "cells": {
        "cells": [
            {"seat": "ceo", "variant": "hook-a", "sent": 12, "replied": 3, "sendable": 12},
            {"seat": "cto", "variant": "hook-b", "sent": 9, "replied": 1, "sendable": 9},
        ],
        "baseline": 0.05,
        "baseline_declared": True,
    },
    "inbound": {
        "optout_detected": 3,
        "optout_dnc_added": 2,
        "unreadable": 1,
        "optout_unattributable": 1,
    },
    "market": {
        "gate_on": True,
        "markets": ["US", "CA"],
        "out_of_market": 2,
        "unknown_country": 1,
    },
    # `_gtm_finding` reads `readiness.refusals`: [(batch, refused_count, classes)].
    "readiness": SimpleNamespace(refusals=[("2026-09-27", 7, [("stale_email", 7, "row")])]),
}


def _split_cards(html_text: str) -> list[str]:
    """Split rendered lens HTML into individual `<div class="card" data-lens="…">` blocks."""
    pieces = re.split(r'(?=<div class="card" data-lens=)', html_text)
    return [p for p in pieces if p.strip()]


def test_lens_toolbar_matches_card_lens_ids():
    empty_cards = _split_cards(strategic_lenses_html({}))
    lens_ids = {re.match(r'<div class="card" data-lens="([^"]+)"', c).group(1) for c in empty_cards}
    assert lens_ids == {"targeting", "messaging", "objections", "product", "geo", "gtm"}


def test_every_card_on_an_empty_model_names_the_missing_input():
    for card in _split_cards(strategic_lenses_html({})):
        assert any(marker in card for marker in _ABSENCE_MARKERS), (
            f"empty-model card shows no named absence and no data — looks hardcoded: {card[:200]}"
        )


#: Cards with no engine-derived metric BY DESIGN (see module docstring): "Product & Solution"
#: renders only manifest-declared questions (none supplied here), "Objection Themes" and
#: "Replies by Region" say plainly that no classifier/ledger field exists for them yet. All
#: three are expected to render identically on the empty and populated models — anything else
#: is expected to change when the data does.
_STATIC_BY_DESIGN_TITLES = {"Product &amp; Solution", "Objection Themes", "Replies by Region"}


def test_populated_data_changes_every_derived_card():
    empty_cards = _split_cards(strategic_lenses_html({}))
    populated_cards = _split_cards(strategic_lenses_html(POPULATED_M))
    assert len(empty_cards) == len(populated_cards)
    for empty_card, full_card in zip(empty_cards, populated_cards, strict=True):
        title = re.search(r"<h2[^>]*>([^<]+)</h2>", empty_card).group(1)
        if title in _STATIC_BY_DESIGN_TITLES:
            assert empty_card == full_card
            continue
        assert empty_card != full_card, f"{title!r} card is identical on empty and populated data"


def test_empty_model_is_falsy_safe():
    # `m=None` must behave exactly like `m={}` — never crash on the missing-model path.
    assert strategic_lenses_html(None) == strategic_lenses_html({})
