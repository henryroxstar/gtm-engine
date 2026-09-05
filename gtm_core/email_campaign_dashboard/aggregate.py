"""Can these campaigns be added up, and if not, what does the page say instead?

Split out of ``views_status`` on 2026-09-05: choosing what a scope's number MEANS is a
different job from drawing it, and folding the two together is what let a rendering loop
quietly answer an aggregation question (``window = window or c.get("window")`` — a real
number belonging to whichever campaign came first).

The rule per figure lives HERE, beside the arithmetic, not in a doc. Every one of them
answers the same question: when a page is about more than one campaign, is there an honest
single number, and if not, does the tile say so or does it print one campaign's value under
the set's label? The second is how "0 of 990 emails · 51 people · 3 sequences" appeared on a
page whose campaign had 8 emails and 4 people — every figure real, every figure someone
else's. Enforced by ``tests/contracts/test_dashboard_aggregation_refusal.py``.
"""

from __future__ import annotations

from .format import _agree, _e, _i, _rate_of, scope_label


def _scope_figures(m: dict) -> dict:
    """Header figures for the campaigns in scope, each with its refusal reason or None.

    A ``None`` value with a reason means **refuse**: render an em dash and say why. A
    ``None`` with no reason means nobody declared it, so there is no question to answer
    and the tile is omitted. A campaign that declares nothing is never read as a zero —
    it cannot disagree, and averaging it in would drag every figure toward nothing.
    """
    camps = m["campaigns"]["campaigns"]
    n = len(camps)

    def declared(path: str) -> list[tuple[str, int]]:
        head, _, tail = path.partition(".")
        return [
            (c.get("slug", "?"), _i((c.get(head) or {}).get(tail)))
            for c in camps
            if (c.get(head) or {}).get(tail)
        ]

    # SHARED INFRASTRUCTURE — never summed. The nine mailboxes are the same nine mailboxes
    # whichever campaign is looking at them; a two-campaign page printing 180/day would be
    # inventing capacity that does not exist. Agreement, or refusal.
    cap, cap_why = _agree(declared("window.daily_cap"), "sending ceilings")
    boxes, _ = _agree(declared("window.mailboxes"), "mailbox counts")
    # PER-CAMPAIGN CADENCE, and the one that actually disagrees today (3 vs 2). Every
    # duration downstream divides by it, so a wrong `touches` is a wrong finish date.
    touches, touches_why = _agree(declared("window.touches"), "emails-per-person cadences")

    # SUM, but only over a COMPLETE set. A partial sum reads as a total: if one campaign
    # of three declares its email target, "8 planned" is not the scope's plan, it is one
    # third of it wearing the whole label.
    emails = declared("targets.emails")
    planned = sum(v for _, v in emails) if n and len(emails) == n else None
    planned_why = (
        None
        if planned is not None or not emails
        else f"{len(emails)} of the {n} campaigns in scope declare an email target"
    )

    # WEIGHTED BY PROSPECTS, never Σreplies/Σprospects. Neither live manifest declares
    # `replies`, so the pooled form would print a 0.0% goal; and `_rate_of` exists
    # precisely to prefer the declared rate over that division. Weighting by the people
    # each target was set against is the only blend that keeps the meaning of "target".
    rated = [
        (
            c.get("slug", "?"),
            _rate_of(c.get("targets") or {}),
            _i((c.get("targets") or {}).get("prospects")),
        )
        for c in camps
        if _rate_of(c.get("targets") or {})
    ]
    distinct = {r for _, r, _ in rated}
    if not rated:
        target_rate, rate_why = None, None
    elif len(distinct) == 1:
        target_rate, rate_why = next(iter(distinct)), None
    elif all(w for _, _, w in rated):
        target_rate = sum(r * w for _, r, w in rated) / sum(w for _, _, w in rated)
        rate_why = None
    else:
        # A rate with no denominator cannot be weighted, and averaging rates unweighted
        # gives the smallest campaign the same vote as the largest.
        target_rate, rate_why = None, "a campaign in scope sets a rate but no prospect count"

    return {
        "n": n,
        "cap": (cap, cap_why),
        "boxes": (boxes, None),
        "touches": (touches, touches_why),
        "planned": (planned, planned_why),
        "target_rate": (target_rate, rate_why),
        # True when the blend above is a real blend, so the comparator paragraph must
        # stop asserting one verdict about "our target" and list them instead.
        "rates_differ": len(distinct) > 1,
        "rates": rated,
    }


def _ceiling_sub(fig: dict, cap, why: str | None) -> str:
    """Sub-line for the sending ceiling. At N>1 it MUST say the ceiling is SHARED, or a
    reader takes it as per-campaign and silently doubles the capacity in their head."""
    if why:
        return why + " — the ceiling is a fact about the mailboxes, so it is not a total"
    if not cap:
        return "no daily cap declared"
    boxes = fig["boxes"][0]
    base = f"{boxes} mailboxes at {cap // boxes} a day each" if boxes else "the daily cap"
    return base if fig["n"] < 2 else f"{base} · shared across all {fig['n']} campaigns, not each"


def _planned_sub(fig: dict, why: str | None) -> str:
    """The email target is a SUM, and at N>1 the sum needs its composition shown.

    Two targets set months apart, each assuming the whole shared ceiling for itself, add
    to a plan the mailboxes were never sized to deliver. The total is real; what a reader
    cannot see without this line is that nobody ever agreed to it as one number.
    """
    if why:
        return f" · target not shown: {why}, and a partial sum reads as a total"
    if fig["n"] < 2:
        return ""
    return f" · the target is {fig['n']} campaign plans added together, each set on its own"


def _cadence_split(m: dict, camps: list[dict], why: str) -> str:
    """What replaces the forecast when the scope has no single cadence or ceiling.

    Refusing is not the same as saying nothing. The per-campaign rows below are each true;
    what cannot be done is add them up, because they contend for the SAME mailboxes — so
    the durations are not concurrent and the ceilings are not additive.
    """
    rows = "".join(
        f"<tr><td>{_e(c.get('title') or c.get('slug'))}</td>"
        f"<td class='num-cell'>{_i((c.get('targets') or {}).get('prospects')):,}</td>"
        f"<td class='num-cell'>{_i((c.get('targets') or {}).get('emails')):,}</td>"
        f"<td class='num-cell'>{_i((c.get('window') or {}).get('touches')) or '—'}</td>"
        f"<td class='num-cell'>{_i((c.get('window') or {}).get('daily_cap')) or '—'}/day</td></tr>"
        for c in camps
    )
    return f"""
      <div class="card">
        <h2>How long it runs, once it starts</h2>
        <p class="note"><strong>Not shown as one figure for {_e(scope_label(m))}</strong> —
        {_e(why)}. Every duration is a division by one of those, so a single "done by" date
        here would be one campaign's arithmetic under all of their names.</p>
        <table><thead><tr><th>Campaign</th><th>People</th><th>Emails</th>
        <th>Emails each</th><th>Ceiling</th></tr></thead><tbody>{rows}</tbody></table>
        <p class="note">These do not run side by side. They draw on the same mailboxes, so
        the ceiling is shared and the durations add rather than overlap. Render one campaign
        on its own (<code>--scope campaign</code>) for its dates.</p>
      </div>"""
