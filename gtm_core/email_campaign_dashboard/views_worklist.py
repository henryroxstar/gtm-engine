"""The worklist — every account in the campaign, grouped by what is left to do with it.

This is the panel an operator opens to answer "where does each account stand", which is a
different question from the other four: they describe the campaign (its forecast, its copy,
its experiment), and this one describes the WORK. It is modelled on the hand-built
2026-09-04 SG-builders dashboard, which answered exactly this and answered it well, and
which nothing generated — so it froze on the day it was written and quietly went wrong.

Two properties it inherits deliberately from that page:

* **One table, not five.** Every account appears exactly once, in exactly one group. A
  reader can count the groups and get the roster back, which is what makes it a worklist
  rather than a report.
* **Every row ends in a sentence.** "Where it stands" is the column that turns a status
  word into something actionable; a bare ``drop`` reads as our opinion, while "Gate B —
  body-shop SI" is a finding the reader can disagree with.

What it does NOT do is re-derive any of those facts. Group membership comes from the same
lane vocabulary the router writes (:mod:`gtm_core.lane_verdicts`, ``gtm_core.lanes.model``),
pack existence from ``packs_model``, and enrolment from each sequence's own recipient list —
so a row cannot say "staged" here and "held" on another panel.
"""

from __future__ import annotations

import csv as _csv
from pathlib import Path

from ..lane_verdicts import LANE_VERDICTS
from ..prospects_consolidate import _prospects_dir
from ..slugify import slug
from .format import _e, _row_status, roster_partial, scope_label

#: Group id -> (heading, the one-line explanation of what being in this group means).
#: ORDER IS THE READING ORDER, and it is work-first: the groups a person can act on today
#: come before the groups that are already settled. The old page's order, kept because it
#: was right — closest-to-sending at the top, nothing-to-do at the bottom.
GROUPS: tuple[tuple[str, str, str], ...] = (
    (
        "pack",
        "Drafted — Tier-A 1:1 packs, manual send",
        "A hand-written email exists for one named person. No sequence, no schedule: these "
        "go out when someone sends them.",
    ),
    (
        "staged",
        "Staged in the sequencer",
        "Loaded and waiting. Nothing sends until a human starts the sequence in the "
        "provider's own UI.",
    ),
    (
        "handsend",
        "Composed, not staged — role inbox, no named person",
        "A body is written, but the merge-field gate refuses a row with no first name, so no "
        "sequencer will take it. These are sent by hand.",
    ),
    (
        "held",
        "Held — contact resolved, not cleared to send",
        "A real account with a reachable person, blocked on something other than the "
        "contact: usually evidence, or an unresolved entity.",
    ),
    (
        "nocontact",
        "No contact exists",
        "Researched, in scope, and no address was found. Nothing to do here without new "
        "spend on contact resolution.",
    ),
    (
        "excluded",
        "Excluded — the account itself did not qualify",
        "A research verdict of `drop`. Contact quality is irrelevant: these enrol in no "
        "lane, and the enrollment gate refuses them in every one.",
    ),
)

#: Verdicts that may enrol anywhere. A row outside this set is excluded on the ACCOUNT,
#: which is a different fact from having no contact — and the two must not merge, because
#: one is answered by more research and the other by more spend.
_SENDABLE = LANE_VERDICTS["generic"]


def _staged_candidates(m: dict) -> dict[str, dict]:
    """``email -> {sequence, admissible}`` for every row on a sequence's recipient list.

    **A recipient CSV is a CANDIDATE list, not an enrolment record.** The file says who was
    proposed; the provider says who was loaded. On the 2026-09-04 SG-builders sequence the
    CSV holds 10 rows and the provider reports 4 enrolled — reading the file as enrolment
    put five Gate-B `drop` accounts under "staged in the sequencer", which is the single
    most dangerous thing this panel could say.

    So the file is read for MEMBERSHIP only, and every row is marked with whether the
    enrollment gate would admit it (`gtm_core.lane_verdicts`). A row the gate refuses is not
    staged no matter which file it sits in. :func:`_reconcile` then compares the admissible
    count against the provider's own number and reports the gap rather than resolving it —
    nothing on disk can say WHICH four of the five admissible rows were loaded.
    """
    root: Path | None = m.get("_content_root")
    seq_dir = _prospects_dir(m["profile"], root) / "sequences"
    out: dict[str, dict] = {}
    for msg in m.get("messages") or []:
        label = msg.get("sequence_id") or "a sequence"
        path = seq_dir / (msg.get("csv") or "")
        if not (msg.get("csv") and path.is_file()):
            continue
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    email = (row.get("email") or "").strip().lower()
                    if not email:
                        continue
                    out[email] = {
                        "sequence": label,
                        "admissible": (row.get("verdict") or "").strip() in _SENDABLE,
                    }
        except OSError:
            continue
    return out


def _provider_enrolled(m: dict) -> dict[str, int]:
    """``sequence_id -> enrolled``, as the PROVIDER reported it. The only authority on who
    is actually loaded; everything else here is a claim about a file."""
    return {
        sq["sequence_id"]: int(sq.get("enrolled") or 0)
        for c in (m.get("campaigns") or {}).get("campaigns") or []
        for sq in c.get("sequences") or []
        if sq.get("sequence_id")
    }


def _group_of(row: dict, packs: set[str], candidates: dict[str, dict]) -> str:
    """Which group this account belongs to. First match wins, and the order is the claim.

    Enrolment and pack come first because they are POSITIVE facts about work already done.
    Between the two, enrolment wins: a sequencer holding the row describes the send that is
    about to happen, while a pack file describes one that was drafted. Everything after them
    is a reason nothing is ready.
    """
    cand = candidates.get(row["email"].lower())
    # ENROLMENT OUTRANKS A PACK FILE, and the order matters. A pack on disk is a record that
    # someone once hand-wrote an email; being on a sequencer's list is what is true about the
    # send NOW. On 2026-09-09 the 5 Tier-A pack contacts were folded onto the generic arc, and
    # with `pack` checked first the worklist kept filing them under "sent by hand" while the
    # provider had them enrolled — the page disagreeing with the sequencer about six people.
    # The pack files are deliberately left on disk; they are history, not a work state.
    #
    # On a candidate list AND admissible. A row the gate refuses is not staged, whichever
    # file it sits in — it falls through to `excluded` below, where its verdict puts it.
    if cand and cand["admissible"]:
        return "staged"
    if slug(row["company"]) in packs:
        return "pack"
    if row["verdict"] and row["verdict"] not in _SENDABLE:
        return "excluded"
    if not row["email"]:
        return "nocontact"
    if not row["named"]:
        return "handsend"
    return "held"


def _stands(row: dict, group: str, candidates: dict[str, dict]) -> str:
    """The "where it stands" sentence. Derived, never typed — see docs/RULES.md §R14.

    Falls back to the researcher's own ``verdict_reason`` wherever there is one, because
    that sentence was written by whoever made the call and is better than anything this
    function could compose from status words.
    """
    reason = row.get("verdict_reason") or ""
    if group == "pack":
        return "Pack written for one named person. Sent by hand."
    if group == "staged":
        seq = (candidates.get(row["email"].lower()) or {}).get("sequence", "a sequence")
        return f"On the recipient list for {seq}. Nothing sent."
    if group == "handsend":
        return "Body written for a role inbox. Manual send — the merge-field gate refuses it."
    if group == "nocontact":
        return reason or "No address found. Nothing further without new contact spend."
    if group == "excluded":
        return reason or "Research verdict `drop`."
    return reason or "Contact resolved; not cleared to send."


def _verified(row: dict) -> str:
    """How the address was established, in the reader's words rather than the provider's."""
    if not row["email"]:
        return "—"
    if not row["named"]:
        return "role inbox, published on the site"
    return "verified contact lookup"


def _row_html(m: dict, row: dict, group: str, candidates: dict[str, dict]) -> str:
    tier = f' <span class="pill">{_e(row["tier"])}</span>' if row.get("tier") else ""
    contact = _e(row["seat"]) if row.get("seat") else '<span class="muted">no named seat</span>'
    email = _e(row["email"]) if row["email"] else '<span class="muted">none</span>'
    verdict = _e(row["verdict"]) if row.get("verdict") else '<span class="muted">—</span>'
    return (
        # `data-row` is the CANONICAL roster index, not this table's display position: the
        # who-tab renders the same rows in a different order and stamps the same index, so
        # one filter selection hides the same account on both.
        f'<tr data-row="{row["i"]}">'
        f"<td><strong>{_e(row['company'])}</strong>{tier}</td>"
        f"<td>{_row_status(m, row['email'])}</td>"
        f"<td>{contact}</td>"
        f"<td class='mono'>{email}</td>"
        f"<td>{_e(_verified(row))}</td>"
        f"<td class='tech'>{verdict}</td>"
        f"<td>{_e(_stands(row, group, candidates))}</td>"
        "</tr>"
    )


def _reconcile(m: dict, staged: list[dict], candidates: dict[str, dict]) -> str:
    """Compare "admissible on the list" against "enrolled at the provider", per sequence.

    Reported, never resolved. Nothing on disk can say WHICH rows the provider holds, so a
    gap is surfaced as a gap — the alternative is picking four of five and being confidently
    wrong about a person who does or does not get an email.

    This is the list half of the drift that bit this campaign on 2026-09-09; the copy half
    (the provider serving pre-revision bodies) is invisible to any offline renderer and is
    checked by reading the steps back before activating.
    """
    provider = _provider_enrolled(m)
    if not provider:
        return ""
    per_seq: dict[str, int] = {}
    for row in staged:
        seq = (candidates.get(row["email"].lower()) or {}).get("sequence", "")
        per_seq[seq] = per_seq.get(seq, 0) + 1

    lines = []
    for seq, listed in sorted(per_seq.items()):
        loaded = provider.get(seq)
        if loaded is None:
            continue
        if listed == loaded:
            lines.append(
                f"<li><code>{_e(seq)}</code>: {listed} admissible on the list, "
                f"{loaded} enrolled at the provider — they agree.</li>"
            )
        else:
            lines.append(
                f'<li class="warn"><code>{_e(seq)}</code>: <strong>{listed} admissible on the '
                f"list but {loaded} enrolled at the provider.</strong> The rows above are the "
                "ones the gate would admit; which of them are actually loaded is not knowable "
                "from disk. Check the sequence in the provider before sending.</li>"
            )
    if not lines:
        return ""
    return (
        '<p class="note"><strong>List vs provider.</strong> A recipient CSV says who was '
        "proposed; the provider says who was loaded. They are different questions and this "
        f'panel does not merge them.</p><ul class="note">{"".join(lines)}</ul>'
    )


def _worklist_view(m: dict) -> str:
    roster = (m.get("roster") or {}).get("rows") or []
    if not roster:
        return (
            '<div class="card"><h2>The worklist</h2><p class="note">Not shown: this scope has '
            "no campaign roster. The worklist is a per-campaign view — it needs a manifest "
            "declaring which run exports its accounts came from, or it would list the whole "
            "shared prospect pool, which is a different question.</p></div>"
        )

    packs = {r["account"] for r in (m.get("packs") or {}).get("packs") or []}
    candidates = _staged_candidates(m)

    buckets: dict[str, list[dict]] = {gid: [] for gid, _, _ in GROUPS}
    for row in roster:
        buckets[_group_of(row, packs, candidates)].append(row)

    # Every account, exactly once. Asserted rather than assumed: a row silently absent from
    # every bucket would make a shorter list look like a complete one, which is the whole
    # failure this panel replaced.
    placed = sum(len(v) for v in buckets.values())

    body = []
    for gid, heading, blurb in GROUPS:
        rows = buckets[gid]
        if not rows:
            continue
        rows.sort(key=lambda r: (r.get("tier") != "A", r["company"].lower()))
        body.append(
            f'<tr class="grp" data-group="{_e(gid)}"><td colspan="7">'
            f"<strong>{_e(heading)}</strong> "
            f'<span class="pill" data-group-count="{_e(gid)}">{len(rows)}</span>'
            f'<div class="note">{_e(blurb)}</div></td></tr>'
        )
        body.extend(_row_html(m, r, gid, candidates) for r in rows)

    # Each chip is a slot the filter rewrites, so the pills, the headings and the visible
    # rows cannot drift apart. `*` is the whole shown set rather than a seventh group.
    counts = " · ".join(
        f'<span data-group="{_e(g)}">{_e(h.split(" — ")[0])} '
        f'<span data-group-count="{_e(g)}">{len(buckets[g])}</span></span>'
        for g, h, _ in GROUPS
        if buckets[g]
    )
    # The worklist has one line per ROW — a (campaign, account) pair — while every tile on
    # this page counts distinct accounts. They are equal until two campaigns work the same
    # account, and the day they differ the reader is told rather than left to add up pills
    # that do not reach the headline.
    accounts = len({r["company"] for r in roster})
    shared = (
        f" {placed - accounts} of these lines are an account a second campaign is also "
        "working, so the account total above is lower."
        if placed != accounts
        else ""
    )
    return f"""
    <div class="card">
      <h2>Every account, and what is left to do with it</h2>
      <p class="note">All <span data-group-count="*">{placed}</span> accounts in
      {_e(scope_label(m))}, each in exactly one group, ordered closest-to-sending first.
      {counts}. {_e(shared)} {_e(roster_partial(m))}</p>
      <table>
        <thead><tr>
          <th>Account</th><th>Status</th><th>Contact</th><th>Email</th>
          <th>How it was verified</th><th class="tech">Research verdict</th>
          <th>Where it stands</th>
        </tr></thead>
        <tbody>{"".join(body)}</tbody>
      </table>
      {_reconcile(m, buckets["staged"], candidates)}
      <p class="note"><strong>Research verdict</strong> is the call on the ACCOUNT, made
      before any copy existed. It is not the email judge's read of the drafted copy — that
      is on <em>What we're saying</em>, and the two disagree usefully. Neither is a gate:
      <code>account_integrity --require-verdict send</code> is what refuses a row at
      enrollment.</p>
    </div>
    """
