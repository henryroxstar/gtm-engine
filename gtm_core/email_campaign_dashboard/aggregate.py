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

from collections import Counter

from ..prospect_lede import GO_LIVE_WORDS, go_live
from .format import _agree, _e, _i, _rate_of, figure_span, scope_label

#: PS20 P3.1 — the sequencer's own reply-label vocabulary. Names the exact ``actuals``
#: fields ``campaigns_dashboard.build_campaigns`` writes (no second vocabulary here).
_LABEL_FIELDS = (
    "interested",
    "not_interested",
    "not_now",
    "out_of_office",
    "unsubscribed",
    "do_not_contact",
)


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

    # PEOPLE CONTACTED (PS20 T1.2), and whether the pieces add up to the whole (T1.3).
    # `current`/`earlier` are SUMMED across campaigns' own `actuals`/`archived_actuals` —
    # each campaign's own count of what it sent — while `not_linked` reads the live rows
    # directly for anything no campaign claims. `sum_ok` — comparing the split's total to
    # one pass over the live rows — catches a mismatch in EITHER direction: a sequence two
    # campaigns both claim sums into BOTH campaigns' `actuals` and OVER-counts; a snapshot
    # that lists the same id twice sums once per campaign (`actuals` is keyed by id) but
    # twice in the raw row total, and UNDER-counts. It is not an aggregation-RULE question
    # like `cap`/`touches` above, so it gets no refusal of its own and instead flags the
    # whole page (`page_warnings`).
    status = m.get("status")
    if not status:
        # A model with no `status` at all must refuse, not read as a readable snapshot
        # with zero rows — that would drop every campaign's `actuals` out of `sum_ok`'s
        # comparison and mislabel an ordinary page "counted twice".
        why = "no sending figures in this model"
        contacted = loaded = replied = meetings = labels = bounces = (None, why)
        sum_ok = True
    elif (status.get("snapshot") or {}).get("unreadable"):
        why = "the sending tool's figures couldn't be read"
        contacted = loaded = replied = meetings = labels = bounces = (None, why)
        sum_ok = True
    else:
        rows = status.get("sequences") or []
        listed = {
            s["sequence_id"] for c in camps for k in ("sequences", "archived") for s in c.get(k, [])
        }

        def total(key: str, field: str) -> int:
            return sum(_i((c.get(key) or {}).get(field)) for c in camps)

        split = {
            "current": total("actuals", "sent"),
            "earlier": total("archived_actuals", "sent"),
            "not_linked": sum(_i(r.get("sent")) for r in rows if r.get("id") not in listed),
        }
        contacted = (split, None)
        loaded, replied, meetings = (
            (total("actuals", f), None) for f in ("loaded", "replied", "meetings")
        )
        # PS20 P3.1/P3.4 — the reply labels are the sequencer's own vocabulary, summed the
        # same way as `loaded`/`replied`/`meetings`: over campaigns' CURRENT `actuals`
        # only, never re-derived from `rows`.
        labels = ({f: total("actuals", f) for f in _LABEL_FIELDS}, None)
        # PS20 P3.5 — bounces are an EMAIL count, a different unit from `contacted`
        # (people). `build_campaigns` already restricted the sum to rows whose per-email
        # status block was present (`bounce_source == "emails"`), so this is a plain sum
        # of what it recorded, not a second filter.
        bounces = (
            {
                "bounced": total("actuals", "bounced"),
                "delivered": total("actuals", "delivered"),
                "unavailable": total("actuals", "bounce_unavailable"),
            },
            None,
        )
        sum_ok = sum(split.values()) == sum(_i(r.get("sent")) for r in rows)

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
        "contacted": contacted,
        "loaded": loaded,
        "replied": replied,
        "meetings": meetings,
        "labels": labels,
        "bounces": bounces,
        "sum_ok": sum_ok,
    }


def bounce_rate(bounced: int, delivered: int) -> float | None:
    """``bounced / (delivered + bounced)``, as a percentage — the same shape as
    ``BOUNCE_RISK_PCT`` (``config.py``), so a caller compares this return value against
    that constant directly with a plain ``>``. ``None`` when there is no denominator: an
    empty ratio is not a 0% rate, it is no rate (PS20 P3.5)."""
    total = bounced + delivered
    if not total:
        return None
    return 100 * bounced / total


def _people_n(n: int) -> str:
    return f"{n:,} {'person' if n == 1 else 'people'}"


def sent_heading(contacted: dict | None) -> str:
    """PS20 P1.3 — the one answer to 'has anything gone out?', from the figures."""
    if contacted is None:
        return "Sending figures unavailable"
    live = contacted["current"] + contacted["not_linked"]
    if live:
        return f"{_people_n(live)} contacted so far"
    if contacted["earlier"]:
        return "Nothing sent in the current campaigns"
    return "Nothing has been sent"


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


def goal_sub_html(fig: dict) -> str:
    """The contacted tile's sub-line: the email goal in ITS OWN unit, never "N of M" — the
    tile counts people and the goal counts emails (PS20 P1.4).

    The goal is a SUM, and at N>1 the sum needs its composition shown. Two targets set
    months apart, each assuming the whole shared ceiling for itself, add to a plan the
    mailboxes were never sized to deliver. The total is real; what a reader cannot see
    without this line is that nobody ever agreed to it as one number.
    """
    planned, why = fig["planned"]
    mark = figure_span("planned-emails", planned)
    if why:
        return f"goal {mark} · not shown: {_e(why)}, and a partial sum reads as a total"
    if planned is None:
        return "no email goal declared"
    if fig["n"] < 2:
        return f"goal: {mark} emails"
    return (
        f"goal: {mark} emails · the target is {fig['n']} campaign plans added together, "
        "each set on its own"
    )


def sequence_word(row: dict | None, on_record: bool, readable: bool) -> str:
    """One sequence's go-live word (PS20 P1.10, per sequence): the ONE rule over that row's own
    snapshot status and people contacted. No snapshot row means no status and no count."""
    row = row or {}
    return go_live([row.get("status")], row.get("sent"), on_record, readable=readable)


def seq_tally(rows: list[dict], readable: bool) -> str:
    """PS20 P1.10 — the "sequences set up" sub-line: each sequence's go-live word, counted
    ("2 started · 1 staged"), so the tile's value and its tally count the same thing.

    Per row, the ONE liveness rule (``prospect_lede.go_live``) over that row's own snapshot
    status and contacted count — on record by definition, since it is a campaign's
    sequence. No word claims present-tense sending unless the snapshot's own row says so,
    and a row synthesized from the ledger (status ``""``) reads ``staged``, never the
    ledger's by-construction "paused".
    """
    words = Counter(sequence_word(r, True, readable) for r in rows)
    return " · ".join(f"{words[w]} {w}" for w in GO_LIVE_WORDS if words[w])


def sending_tiles(m: dict, fig: dict) -> dict:
    """What the status tab's sending tiles show — read off ``fig``, never re-summed.

    The tiles used to add up snapshot rows themselves, each with its own idea of which rows
    count, so the send tile, "enrolled" and the ops card could disagree about one fact
    (PS20 P1.2). ``_scope_figures`` is the only place a headline figure is summed; this only
    picks the scope's current rows for the per-sequence table and the tally. A refused
    figure is ``None`` and renders as an em dash.
    """
    camps = m["campaigns"]["campaigns"]
    ids = {s["sequence_id"] for c in camps for s in c.get("sequences", [])}
    current = [x for x in m["status"].get("sequences", []) if x.get("id") and x["id"] in ids]
    readable = not (m["status"].get("snapshot") or {}).get("unreadable")
    split = fig["contacted"][0]
    # The first campaign that declares a window, as the loop this replaced took it. Only its
    # capacity blocker is read, and only as a pointer to Operator notes.
    window = next((c["window"] for c in camps if c.get("window")), {})
    return {
        "current": current,
        "contacted": None if split is None else split["current"],
        "loaded": fig["loaded"][0],
        "replied": fig["replied"][0],
        # Unknown, not zero, when an unreadable snapshot left no row to count.
        "sequences": len(current) if readable or current else None,
        "tally": seq_tally(current, readable),
        "goal": goal_sub_html(fig),
        "blocker": window.get("capacity_blocker") or "",
    }


def campaign_contacted(fig: dict, c: dict) -> dict | None:
    """One campaign's own split, shaped like ``fig["contacted"]`` so ``sent_heading`` reads
    it. ``None`` when the scope's figures refuse: a campaign card must not read a zero off
    an unreadable snapshot. ``replied``/``meetings`` are the campaign's own ``actuals``;
    ``sent_heading`` ignores them."""
    if fig["contacted"][0] is None:
        return None
    actuals = c.get("actuals") or {}
    return {
        "current": _i(actuals.get("sent")),
        "earlier": _i((c.get("archived_actuals") or {}).get("sent")),
        "not_linked": 0,
        "replied": _i(actuals.get("replied")),
        "meetings": _i(actuals.get("meetings")),
    }


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
