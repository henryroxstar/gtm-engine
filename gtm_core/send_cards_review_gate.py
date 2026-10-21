"""A "send this cell" approval must say it came from the review page, or ``apply`` refuses that cell.

``create_card_export`` is a plain Python function, so anything that can run Python can write an
export saying every cell is approved. On 2026-09-26 that is exactly what happened: an agent was told
"Proceed", wrote "send this cell" for three cells itself, and the export it wrote recorded
``revealed_before_decision: false`` with nobody ticking or unticking a single member. Those three
approvals were applied, and 201 people were loaded under them.

:func:`refuse_unreviewed` is the check ``send_cards_apply`` now runs before it writes anything: a
card whose decision is ``send this cell`` and whose ``revealed_before_decision`` is not literally
``true`` is taken out, **that cell only**, and listed in :class:`gtm_core.send_cards.ApplyResult`
as a refusal. The rest of the export applies as before.

**What this does and does not prove.** The flag is a claim in a file. The page does not yet export
(its script records ``clientState`` but has no export control, and sets ``revealed_before_decision``
only when the panel verdicts are revealed before a decision, R8.1), so today it can only be set by
the code that writes the export; code that writes ``true`` it did not earn passes this check. What
the check stops is an export that does not even claim a review, which is what the incident script
wrote, and it makes the claim an explicit act rather than a default. A gate that cannot be forged by
whatever writes the export needs the page to sign its export; that is a separate change.

Only the literal ``true`` grants. ``"true"``, ``1``, a missing key and ``null`` all refuse (the
closed list of granting values, test plan §4.2).
"""

from __future__ import annotations

import sys

SEND = "send this cell"
REFUSAL = (
    "Cell {cell_id} was approved without being opened on the review page. "
    "Open the page, reveal the emails, then decide."
)


def refuse_unreviewed(cards: list) -> tuple[list, list[dict]]:
    """``(cards still to apply, the refusals)``; a refusal is ``{cell_id, decision, reason}``.

    Cards that are not "send this cell" pass through untouched (a skip or a rewrite sends nothing,
    and an unknown decision word still reaches the caller's own refusal).
    """
    kept: list = []
    refused: list[dict] = []
    for card in cards:
        sends = isinstance(card, dict) and str(card.get("decision") or "").strip() == SEND
        if sends and card.get("revealed_before_decision") is not True:
            cell_id = str(card.get("cell_id") or card.get("card_id") or "")
            refused.append(
                {"cell_id": cell_id, "decision": SEND, "reason": REFUSAL.format(cell_id=cell_id)}
            )
        else:
            kept.append(card)
    return kept, refused


def render_apply_summary(result) -> int:
    """Print the apply summary (and each refusal, on stderr) and return the CLI exit code.

    Exit 0 when nothing was refused; 2 when any cell was, so a wrapper that only reads the exit code
    cannot mistake a partly applied export for a clean one. What was written is stated either way.
    """
    line = f"outreach-campaign apply: wrote {len(result.draft_paths)} draft(s), "
    line += f"{len(result.repair_rows)} repair row(s)"
    if result.refused_cells:
        line += f", REFUSED {len(result.refused_cells)} cell(s) that were not opened on the page"
    print(line)
    for refusal in result.refused_cells:
        print(f"REFUSED: {refusal['reason']}", file=sys.stderr)
    return 2 if result.refused_cells else 0
