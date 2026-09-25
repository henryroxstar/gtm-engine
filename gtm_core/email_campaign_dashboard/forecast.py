"""How long the run takes once it starts, and what sets the ceiling.

Split out of :mod:`~gtm_core.email_campaign_dashboard.views_status` when the forecast stopped
being one row over one denominator. A campaign is not its sequencer: this profile's runs load a
handful of named seats into the sending tool and then send the role inboxes and the 1:1 packs by
hand, which is most of the traffic and — because a pack ladder runs to day 12 against a
sequence's day 5 — all of the finish date. Deriving that needs the roster, the specs and the
pack ladders, which is more machinery than a view module should carry beside four other views.

Every duration here is arithmetic over a number printed beside it. A schedule quoted without
its divisor is exactly the figure that outlives the list it was computed from.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .aggregate import _cadence_split, _scope_figures
from .format import _e, _i


def drafted_to(m: dict) -> set[str]:
    """Every address that has a composed body waiting for it, deduplicated.

    Drafting and LOADING are different steps and the headline tile used to conflate them: it
    read `packs + sum(sequences.loaded)`, so a campaign with 6 packs and 4 rows in the
    sequencer reported "10 emails drafted" while its own body text said all 23 contactable
    accounts had one. Two errors in one figure. `loaded` is a sequencer fact — 10 named-seat
    rows were composed and 4 of them enrolled — and the role-inbox lane is in no sequence at
    all, so a sum over sequences is structurally blind to it, the same gap the 1:1 packs had
    before ``packs_model``.

    Counted over the rendered bodies instead, which is where a drafted email actually is. One
    address can be both a merge row and a 1:1 pack, so the two sets are unioned rather than
    added.
    """
    samples = m.get("samples") or {}
    return {r["to"] for r in samples.get("rendered") or []} | {
        pk["to"] for pk in samples.get("packs") or []
    }


def _lanes(m: dict) -> list[dict]:
    """Every lane this campaign actually sends on, with the arc each one runs.

    A campaign is not its sequencer. This one loads 4 named seats into Saleshandy and then
    sends 8 role inboxes and 5 hand-written 1:1 packs BY HAND — three quarters of the traffic
    and, because the pack ladder runs to day 12 against the sequence's day 5, all of the
    finish date. Counting only the sequencer answered "how long does the automated lane take"
    under the heading "how long it runs", which is how the card came to read *1 working day*
    for a campaign that is not done for a fortnight.

    Recipients are deduplicated by address across lanes. One pack per campaign is typically
    written to a published team inbox that is ALSO a role-inbox merge row — the same two
    emails on the same two days — so that address is counted once, in the lane that renders
    it.

    `cells.toml` can register the same physical sequence more than once — a second segment
    variant, a second campaign's row pointing at the address the first already claimed — and
    each registration becomes its own entry in ``m["messages"]``. Both the campaign-wide
    `reviewed` sum and this per-lane loop deduplicate by `sequence_id` so a sequence reachable
    from four registrations is counted once, not four times over.
    """
    samples = m.get("samples") or {}
    lanes: list[dict] = []
    claimed: set[str] = set()

    seqs = {
        sq["sequence_id"]: sq for c in m["campaigns"]["campaigns"] for sq in c.get("sequences", [])
    }
    rendered: dict[str, set[str]] = {}
    for r in samples.get("rendered") or []:
        rendered.setdefault(r["spec"], set()).add(r["to"])

    messages = list({msg["sequence_id"]: msg for msg in m["messages"]}.values())

    # 1. The sequencer lanes. Denominator and label are unchanged from when this card had
    #    only one row: a checked list beats the manifest's target, because "the list that was
    #    checked, once it is loaded" is the number a reader can go and look at. `enrolled` is
    #    the fallback — the provider's own read-back, not the number of rows the spec renders
    #    (this campaign drafted 10 named-seat emails and loaded 4 of them; only what is
    #    loaded is on a schedule, so only what is loaded is forecast).
    reviewed = sum(_i((msg.get("lint") or {}).get("rows")) for msg in messages)
    target = _i(
        next((c["targets"] for c in m["campaigns"]["campaigns"] if c.get("targets")), {}).get(
            "prospects"
        )
    )
    # `reviewed` and `target` are CAMPAIGN-WIDE denominators, so they describe one lane and
    # only one. With a second sequence they were applied to each message in turn and the
    # campaign's whole population was counted once per sequence — two identical rows of 12
    # for a campaign of 12 (found 2026-09-09, the day this campaign grew its second
    # sequence). Past one sequence the only honest per-lane number is that sequence's own
    # enrolled count, read back from the provider.
    multi = len(messages) > 1
    for msg in messages:
        days = sorted(c["day"] for c in msg["copy"])
        enrolled = _i((seqs.get(msg["sequence_id"]) or {}).get("enrolled"))
        if multi:
            label, people = (
                (
                    f"{(seqs.get(msg['sequence_id']) or {}).get('name') or msg['sequence_id']}"
                    " — loaded, paces itself once a person starts it"
                ),
                enrolled,
            )
        elif reviewed:
            label, people = "the list that was checked, once it is loaded", reviewed
        elif target:
            label, people = "the campaign's own qualified-and-sendable target", target
        else:
            label, people = "the staged sequence — paces itself once a person resumes it", enrolled
        if not (days and people):
            continue
        claimed |= rendered.get(Path(msg["spec"]).stem, set())
        lanes.append(
            {
                "label": label,
                "people": people,
                "touches": len(days),
                "span": days[-1] - days[0],
                "auto": True,
            }
        )

    # 2. Merge lanes with no sequence behind them. Same spec, same cadence — but the
    #    merge-field gate refuses a role inbox, so every one of these is a person's own hand.
    for tspec, addrs in sorted(rendered.items()):
        if not (addrs - claimed):
            continue
        days = sorted({t["day"] for t in samples.get("touches") or [] if t["spec"] == tspec})
        if not days:
            continue
        lanes.append(
            {
                "label": "role inboxes — no named seat, so every send is by hand",
                "people": len(addrs - claimed),
                "touches": len(days),
                "span": days[-1] - days[0],
            }
        )
        claimed |= addrs

    # 3. The 1:1 packs. Only the EMAIL touches: touch 1 of the standard ladder is a LinkedIn
    #    connection request, which no mailbox sends and no ceiling bounds.
    packs = [
        pk
        for pk in samples.get("packs") or []
        if pk.get("mail_days") and pk.get("to") not in claimed
    ]
    if packs:
        days = [d for pk in packs for d in pk["mail_days"]]
        lanes.append(
            {
                "label": "1:1 packs — hand-written, sent by hand",
                "people": len(packs),
                "touches": round(sum(len(pk["mail_days"]) for pk in packs) / len(packs), 1),
                "span": max(days) - min(days),
                "emails": sum(len(pk["mail_days"]) for pk in packs),
            }
        )
        claimed |= {pk["to"] for pk in packs}
    return lanes


def _page_day(m: dict) -> date:
    """The day the page was generated, from the model's own clock (`generated_at`, UTC).

    Every other date on the page reads that clock, so the forecast does too: a page rendered
    at 23:00 UTC on a Friday must not date its sends from a local Saturday. `date.today()` is
    only the fallback for a model that carries no readable clock.
    """
    try:
        return date.fromisoformat(str(m.get("generated_at") or "")[:10])
    except ValueError:
        return date.today()


def _wd(n: int) -> str:
    return f"{n} working day{'' if n == 1 else 's'}"


def _schedule(m: dict) -> dict:
    """The forecast's arithmetic, once: the Operator-notes card and the Results sentence read it.

    Returns ``{"why", "split": True}`` when the scope has no single ceiling or cadence,
    ``{"empty": reason}`` when there is nothing to date (the reason names WHICH input is
    missing: a scope with neither reads the ceiling one, because it is checked first), else
    the figures. Every duration here is arithmetic over a number printed beside it. A schedule
    quoted without its divisor is exactly the figure that outlives the list it was computed
    from — "about five weeks" was true of 1,359 emails and is still sitting in the campaign
    file now that the checked list is smaller.
    """
    fig = _scope_figures(m)
    cap, cap_why = fig["cap"]
    boxes = fig["boxes"][0]
    touches, touches_why = fig["touches"]
    touches = touches or max(
        (_i((msg.get("lint") or {}).get("touches")) for msg in m["messages"]), default=0
    )
    # REFUSE THE WHOLE BLOCK when the scope has no single ceiling. Every row below divides by
    # it, and a duration is exactly the figure that outlives the list it was computed from. A
    # card cannot carry that much "why" in a stat sub-line, so it is refused at card level
    # with the numbers named. A cadence SPLIT is no longer a refusal: the lanes below carry
    # one cadence each and print it, which is what the split was warning could not be done.
    if cap_why:
        return {"why": cap_why, "split": True}
    lanes = _lanes(m)
    if not (cap and lanes):
        if touches_why:
            return {"why": touches_why, "split": True}
        if not cap:
            return {"empty": "no sending ceiling is declared"}
        return {"empty": "nothing is loaded to send yet"}

    # Day 1 of sending is the first working day AFTER the page's day, whatever day that is.
    # `_finish` skips weekends itself; moving a Saturday start to Monday first made Monday
    # day 0 and every weekend-rendered date one working day late.
    start = _page_day(m)

    def _finish(total: int) -> str:
        day, added = start, 0
        while added < total:
            day += timedelta(days=1)
            if day.weekday() < 5:
                added += 1
        return f"{day:%-d %b}"

    rows = ""
    for ln in lanes:
        emails = ln.get("emails") or int(round(ln["people"] * ln["touches"]))
        send_days = -(-emails // cap)
        tail = -(-ln["span"] * 5 // 7)
        total = send_days + tail
        per = f"{ln['touches']:g} over {ln['span']} days"
        rows += (
            f"<tr><td>{_e(ln['label'])}</td><td class='num-cell'>{ln['people']:,}</td>"
            f"<td class='num-cell'>{emails:,}</td><td class='muted'>{per}</td>"
            f"<td class='num-cell'>{send_days} + {tail}</td>"
            f"<td class='num-cell'>{_wd(total)}</td>"
            f"<td class='muted'>{_finish(total)}</td></tr>"
        )

    people_all = sum(ln["people"] for ln in lanes)
    emails_all = sum(ln.get("emails") or int(round(ln["people"] * ln["touches"])) for ln in lanes)
    span_all = max(ln["span"] for ln in lanes)
    send_days, tail = -(-emails_all // cap), -(-span_all * 5 // 7)
    total_all = send_days + tail
    if len(lanes) > 1:
        rows += (
            "<tr><td><strong>the whole campaign</strong></td>"
            f"<td class='num-cell'><strong>{people_all:,}</strong></td>"
            f"<td class='num-cell'><strong>{emails_all:,}</strong></td>"
            "<td class='muted'>—</td>"
            f"<td class='num-cell'>{send_days} + {tail}</td>"
            f"<td class='num-cell'><strong>{_wd(total_all)}</strong></td>"
            f"<td class='muted'><strong>{_finish(total_all)}</strong></td></tr>"
        )
    return {
        "lanes": lanes,
        "cap": cap,
        "boxes": boxes,
        "touches": touches,
        "rows": rows,
        "emails_all": emails_all,
        "span_all": span_all,
        "send_days": send_days,
        "tail": tail,
        "total_all": total_all,
        "finish": _finish(total_all),
        "people_all": people_all,
    }


def when_done(m: dict) -> tuple[str | None, str | None]:
    """The "when we will know" sentence, or None and the reason it cannot be dated.

    Reads :func:`_schedule`, the same arithmetic as the Operator-notes table, so the date on
    Results can never disagree with the table it summarises. It never says "Done by": that
    phrase is the table's own header, which the aggregation-refusal test looks for.
    """
    s = _schedule(m)
    if s.get("split"):
        return None, s["why"]
    if "empty" in s:
        return None, s["empty"]
    return (
        f"If sending starts on the next working day, the last email goes out by {s['finish']}"
        f" — {_wd(s['total_all'])}.",
        None,
    )


def _forecast_block(m: dict) -> str:
    """How long the run takes once it starts, and what sets the ceiling.

    Rendered from :func:`_schedule`. Each sentence claims only what that arithmetic shows: the
    follow-up arc sets the finish date only when sending is shorter than the arc, and the hand
    note counts the rows that pace themselves rather than assuming one.
    """
    camps = m["campaigns"]["campaigns"]
    s = _schedule(m)
    if s.get("split"):
        return _cadence_split(m, camps, s["why"])
    if "empty" in s:
        return ""
    window: dict = next((c["window"] for c in camps if c.get("window")), {})
    lanes, cap, boxes, touches = s["lanes"], s["cap"], s["boxes"], s["touches"]
    emails_all, span_all, total_all = s["emails_all"], s["span_all"], s["total_all"]
    send_days, tail = s["send_days"], s["tail"]
    arc_binds = send_days <= tail

    # Rows drafted for a merge lane that the sequencer never loaded. They are written and
    # checked and they send nothing today, so they are named rather than counted: adding
    # them to a forecast would date a send that has no schedule behind it.
    unloaded = max(0, len(drafted_to(m)) - s["people_all"])
    unloaded_note = (
        f"<strong>{unloaded} more {'person has' if unloaded == 1 else 'people have'} a drafted "
        "email that nothing will send.</strong> "
        "They were rendered for a merge lane and never loaded into the sequence, so they are "
        "absent from every row above — a forecast can only date a send that has a schedule "
        "behind it. Loading them is a decision, not a missing step."
        if unloaded
        else ""
    )

    # `_lanes` puts the sequence lanes first, so "first" is true by construction.
    autos = sum(1 for ln in lanes if ln.get("auto"))
    hand = len(lanes) - autos
    paces = (
        "The first row paces itself" if autos == 1 else f"The first {autos} rows pace themselves"
    )
    hand_note = (
        f"""<p class="note"><strong>{paces}.</strong> The other {hand}
        {"row is a hand send" if hand == 1 else "rows are hand sends"}: the merge-field gate
        refuses a row with no first name, and a 1:1 pack has no sequence to put it in. Their
        dates are what the ladder asks for, not what any tool will enforce: if nobody sends on
        the day, that row's finish date moves and nothing reports it.</p>"""
        if autos and hand
        else ""
    )

    arc = (
        f"""<strong>The follow-up arc sets the finish date, not the mailboxes.</strong>
        All {emails_all:,} emails fit in {send_days} sending day{"" if send_days == 1 else "s"}
        at a {cap}/day ceiling; what takes {total_all} working days is that the last person
        enrolled still has their own {span_all}-day ladder to live through after the final send
        starts. That is the "+ {tail}" column, and the longest row is the one that decides it."""
        if arc_binds
        else f"""<strong>The mailboxes set the finish date.</strong> Sending {emails_all:,}
        emails at {cap} a day takes {_wd(send_days)}, longer than the {span_all}-day
        follow-up arc."""
    )
    constraint = (
        f"""The list is not the constraint — the mailboxes are, and on this campaign not
        even they are: {emails_all:,} emails is {emails_all * 100 // (cap * max(total_all, 1))}%
        of what the ceiling would absorb over the same {total_all} working days."""
        if arc_binds
        else f"""The list is not the constraint — the mailboxes are: at {cap} a day,
        {emails_all:,} emails take {_wd(send_days)} to send."""
    )

    per_box = (cap // boxes) if boxes else 0
    people_day = cap // touches if touches else 0
    month = people_day * 21
    why = (
        f"<strong>{cap} emails a day is the ceiling, and it is {boxes} mailboxes at "
        f"{per_box} a day each.</strong> "
        if boxes and per_box
        else f"<strong>{cap} emails a day is the ceiling.</strong> "
    )
    cap_note = window.get("capacity_note") or ""
    caveat = window.get("caveat") or ""

    return f"""
      <div class="card">
        <h2>How long it runs, once it starts</h2>
        <table><thead><tr><th>Audience</th><th>People</th><th>Emails</th>
        <th>Each person gets</th><th>Sending + tail</th><th>Total</th><th>Done by</th>
        </tr></thead>
        <tbody>{s["rows"]}</tbody></table>
        <p class="note">{arc}
        Dates assume it starts on the next working day.
        {_e(caveat) and "<strong>Caveat:</strong> " + _e(caveat) + "."}</p>
        {hand_note}
        {f'<p class="note">{unloaded_note}</p>' if unloaded_note else ""}
      </div>

      <div class="card">
        <h2>The ceiling, and why it is where it is</h2>
        <p>{why}{constraint}
        At {touches} emails a person that ceiling absorbs about <strong>{people_day} new people a working day</strong>,
        or <strong>{month:,} a month</strong>. Going faster means more mailboxes,
        not a setting: the per-mailbox rate is the number deliberately held down, because
        volume out of a single mailbox is what costs a sending domain its reputation.</p>
        {f'<p class="note">{_e(cap_note)}.</p>' if cap_note else ""}
      </div>"""
