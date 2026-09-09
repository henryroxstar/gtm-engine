"""The provider workflow register has a dated verdict for every catalogued workflow.

Higgsfield's MCP ships 16 bundled workflows, six of which overlap lanes this repo built by hand.
Until 2026-08-29 the repo had never called ``get_workflow_instructions`` — so every overlap was an
accident nobody had decided about, and the same rediscovery was available to every future session
(finding P1).

``docs/reference/provider-workflows.md`` is that decision, written down once and dated. These tests
pin its **shape**, never its verdicts: a verdict is a human decision, and a test that asserted a
particular one would just be the decision restated in a second place. What they refuse is a
workflow with *no* row — which is indistinguishable from having overlooked it.

The catalog itself lives with the provider and this test cannot call it, so the 16 names are pinned
here with their probe date. When the provider ships a seventeenth, the honest failure mode is that
somebody re-runs the catalog and this list is short — not that the register silently looks complete.

Design: the 2026-08-29 video-router hardening note, item C9.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTER = REPO / "docs" / "reference" / "provider-workflows.md"
ROUTER_BODY = REPO / "plugin" / "skills" / "video-router" / "body_template.md"

#: The catalog as ``get_workflow_instructions`` returned it with no arguments, 2026-08-29.
CATALOGUED_WORKFLOWS = (
    "ad-multiplier",
    "brand-asset-creation",
    "character-sheet",
    "faceless-video",
    "narrator",
    "product-photoshoot",
    "subtitles",
    "thumbnail-generation",
    "ugc-product-video",
    "ugc-review-video",
    "ugc-try-on-video",
    "ugc-tutorial-video",
    "ugc-unboxing-video",
    "ugc-website-video",
    "video-editing",
    "website-builder-flow",
)

_VERDICT = re.compile(r"\b(adopt|decline)\b", re.IGNORECASE)
_PROBE_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _rows() -> dict[str, list[str]]:
    """Every table row of the register, keyed by the workflow name in its first cell.

    Parsed rather than pattern-matched over the whole file so that a name merely *mentioned* in
    the prose above the table cannot stand in for a row that carries a verdict.
    """
    rows: dict[str, list[str]] = {}
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name = cells[0].strip("`") if cells else ""
        if name in CATALOGUED_WORKFLOWS:
            rows[name] = cells
    return rows


def test_every_catalogued_workflow_has_an_adopt_or_decline_row() -> None:
    """The anti-drift check. A provider workflow with no recorded decision is P1 repeating."""
    rows = _rows()
    missing = [w for w in CATALOGUED_WORKFLOWS if w not in rows]
    assert not missing, (
        f"{REGISTER.relative_to(REPO)} has no row for {missing}. A catalogued workflow with no "
        "row has not been declined — it has been overlooked, which is exactly the failure this "
        "register exists to make visible."
    )
    unjudged = [w for w, cells in rows.items() if not _VERDICT.search(" ".join(cells[1:3]))]
    assert not unjudged, (
        f"rows for {unjudged} carry no adopt/decline verdict. A row without a verdict records "
        "that somebody looked, not what they decided."
    )


def test_every_row_carries_a_reason_and_a_probe_date() -> None:
    """A decline without a reason is indistinguishable from an oversight, and a verdict without a
    date is a claim about a provider surface that has no expiry."""
    for name, cells in sorted(_rows().items()):
        joined = " ".join(cells)
        assert _PROBE_DATE.search(joined), f"{name}: row carries no probe date"
        # The reason cell is the one long enough to be a sentence; a bare "n/a" is not a reason.
        assert any(len(c) > 40 for c in cells[3:]), (
            f"{name}: row carries no reason. State why, in one line — the next reader's "
            "alternative to a written reason is re-deriving it from the provider catalog."
        )


def test_faceless_video_is_recorded_as_declined() -> None:
    """The near-miss. Its name reads like our faceless lane and it is not: its own scope note
    excludes ads and product demos, which is what these shorts are. Pinned so it is not
    rediscovered and adopted on the strength of the name alone."""
    cells = _rows().get("faceless-video")
    assert cells is not None, "faceless-video has no row in the register"
    verdict = " ".join(cells[1:3]).lower()
    assert "decline" in verdict, f"faceless-video is no longer declined (verdict: {verdict!r})"
    reason = " ".join(cells[3:]).lower()
    assert "ads" in reason or "product demo" in reason, (
        "the faceless-video decline no longer states its SCOPE reason. Without it the decline "
        "reads as taste, and the next reader overturns it."
    )


def test_the_router_body_cites_the_register_rather_than_restating_it() -> None:
    """C3's derive-don't-restate rule, applied to C9.

    A workflow table copied into a skill body drifts from the catalog the moment the provider
    ships a new version — and the body is the copy nobody re-probes.
    """
    body = ROUTER_BODY.read_text(encoding="utf-8")
    assert "docs/reference/provider-workflows.md" in body, (
        "the router body does not cite the provider workflow register, so the rule "
        "('check the register before hand-rolling a generation flow') has no home the operator "
        "can reach"
    )
    # Naming one or two workflows as a load-bearing fact, next to a citation, is not restating the
    # register — `subtitles` carries the captions decline in the provider-boundary table, and
    # `character-sheet` is the fact that makes the stock-avatar answer decidable. What must not
    # appear is the TABLE: a row pairing a workflow with its verdict is the copy that goes stale.
    named = [w for w in CATALOGUED_WORKFLOWS if w in body]
    assert len(named) <= 3, (
        f"the router body names {len(named)} catalogued workflows ({named}) — at that count it is "
        "enumerating the register rather than citing it. One home per fact."
    )
    # The anti-copy check proper: a TABLE of workflows is the register duplicated. One row is not
    # — the provider-boundary table's captions row records the `subtitles` decline as a boundary
    # fact the body owns, and a sibling test requires that reason to be there.
    workflow_rows = [
        line
        for line in body.splitlines()
        if line.startswith("|")
        and any(w in line for w in CATALOGUED_WORKFLOWS)
        and "provider-workflows.md" not in line
    ]
    assert len(workflow_rows) <= 1, (
        "the router body carries a table of provider workflows:\n  "
        + "\n  ".join(workflow_rows)
        + "\nThe register owns the table; the body cites it."
    )
