"""The Accounts tab — every account in the campaign, grouped by what is left to do with it
(PS20 PRD Phase 2).

* **One table.** Every account appears exactly once, in exactly one group, so a reader can
  count the groups and get the roster back.
* **Every row ends in a sentence** ("What is left to do"), derived from facts other modules
  own: group membership from the router's vocabulary (:mod:`gtm_core.lane_verdicts`), pack
  existence from ``packs_model``, enrolment from each sequence's own recipient list.
* **A recipient file is read for membership, never enrolment.** The provider is the only
  authority on who is loaded; :func:`_list_vs_provider` reports the gap for Operator notes.
"""

from __future__ import annotations

import re

from ..lane_verdicts import LANE_VERDICTS
from ..slugify import slug
from .aggregate import _scope_figures
from .filters import bar_html, sub_counts
from .forecast import drafted_to
from .format import (
    _e,
    _row_status,
    _stat,
    figure_span,
    roster_gap,
    roster_partial,
    scope_label,
    section,
)

#: Group id -> (heading, the one-line explanation of what being in this group means).
#: ORDER IS THE READING ORDER, and it is work-first: closest-to-sending at the top,
#: nothing-to-do at the bottom.
GROUPS: tuple[tuple[str, str, str], ...] = (
    (
        "pack",
        "Drafted — 1:1 packs, manual send",
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
        "The account itself did not qualify in research. Contact quality does not matter "
        "here: these go on no sending list, and loading refuses them on every one.",
    ),
)

#: Verdicts that may enrol anywhere. A row outside this set is excluded on the ACCOUNT,
#: which is a different fact from having no contact — one is answered by more research and
#: the other by more spend, so the two must not merge.
_SENDABLE = LANE_VERDICTS["generic"]


def _staged_candidates(m: dict) -> dict[str, dict]:
    """``email -> {sequences, admissible}`` for every row on a sequence's recipient list.

    A recipient CSV is a CANDIDATE list, not an enrolment record: on the 2026-09-04
    SG-builders sequence the CSV held 10 rows and the provider reported 4 enrolled, and
    reading the file as enrolment put five `drop` accounts under "staged". So the file is
    read for membership only, and a row the enrollment gate refuses is never staged.

    Membership is a SET, sorted: one label per file let the last list overwrite the rest
    (2026-09-21: a sequence read 41 admissible of 45 enrolled forwards, 0 of 45 reversed).
    Rows are each message's ``list_rows`` (PS20 P1.6), so a campaign page sees its lists only.
    """
    found: dict[str, set[str]] = {}
    for msg in m.get("messages") or []:
        label = msg.get("sequence_id") or "a sequence"
        for row in msg.get("list_rows") or []:
            email = (row.get("email") or "").strip().lower()
            if not email:
                continue
            entry = found.setdefault(email, set())
            if (row.get("verdict") or "").strip() in _SENDABLE:
                entry.add(label)
    return {
        email: {"sequences": tuple(sorted(seqs)), "admissible": bool(seqs)}
        for email, seqs in found.items()
    }


def _provider_enrolled(m: dict) -> dict[str, int]:
    """``sequence_id -> enrolled``, as the PROVIDER reported it — the only authority."""
    return {
        sq["sequence_id"]: int(sq.get("enrolled") or 0)
        for c in (m.get("campaigns") or {}).get("campaigns") or []
        for sq in c.get("sequences") or []
        if sq.get("sequence_id")
    }


def _group_of(row: dict, packs: set[str], candidates: dict[str, dict]) -> str:
    """Which group this account belongs to. First match wins, and the order is the claim.

    * Enrolment outranks a pack file: on 2026-09-09 the Tier-A pack contacts were folded
      onto the generic arc, and checking `pack` first filed them "sent by hand" while the
      provider had them enrolled.
    * Staged needs BOTH verdicts to admit — the recipient file's and the roster's. A file
      written before the research pass carries a blank verdict, which the generic lane
      admits by design; on 2026-09-21 that rendered 70 of a tenant's 107 already-dropped
      enterprise accounts as staged. AND, not OR: a disagreement refuses.
    """
    cand = candidates.get(row["email"].lower())
    if cand and cand["admissible"] and (row.get("verdict") or "") in _SENDABLE:
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


def _seq_titles(m: dict) -> dict[str, str]:
    """``sequence_id -> title`` (Decision 13 / Q3): the registration's ``title``, else the
    sending tool's own ``name``. An id with neither is absent, so callers fall back to it."""
    out: dict[str, str] = {}
    for msg in m.get("messages") or []:
        sid, title = msg.get("sequence_id"), (msg.get("title") or "").strip()
        if sid and title:
            out.setdefault(sid, title)
    for sq in (m.get("status") or {}).get("sequences") or []:
        sid, name = sq.get("id"), (sq.get("name") or "").strip()
        if sid and name:
            out.setdefault(sid, name)
    return out


def _staged_seqs(row: dict, group: str, candidates: dict[str, dict]) -> tuple[str, ...]:
    if group != "staged":
        return ()
    return (candidates.get(row["email"].lower()) or {}).get("sequences") or ()


def _stands(row: dict, group: str, candidates: dict[str, dict], titles: dict[str, str]) -> str:
    """The "what is left to do" sentence. Derived, never typed (docs/RULES.md §R14).

    Falls back to the researcher's own ``verdict_reason`` wherever there is one. A staged
    row names every list it is on, by title (PS14 keeps ids out of operator lines); no
    "Nothing sent." (PS20 P1.3): nothing on disk records one person's sends.
    """
    reason = row.get("verdict_reason") or ""
    if group == "pack":
        return "Pack written for one named person. Sent by hand."
    if group == "staged":
        names = [titles.get(s) or s for s in _staged_seqs(row, group, candidates)]
        if len(names) > 1:
            return f"On {len(names)} recipient lists: {', '.join(names)}."
        return f"On the recipient list for {names[0] if names else 'a sequence'}."
    if group == "handsend":
        return "Body written for a role inbox. Manual send — the merge-field gate refuses it."
    if group == "nocontact":
        return reason or "No address found. Nothing further without new contact spend."
    if group == "excluded":
        return reason or f"Research call: {row['verdict']}."
    return reason or "Contact resolved; not cleared to send."


def _judge_clause(row: dict) -> str:
    """The email judge's next step as a plain phrase — never the verdict word."""
    j = row.get("judge") or {}
    return f" Email judge: {j['action']}." if j.get("action") else ""


def _verified(row: dict) -> str:
    """How the address was established, in the reader's words rather than the provider's."""
    if not row["email"]:
        return "—"
    if not row["named"]:
        return "role inbox, published on the site"
    return "verified contact lookup"


_MUTED_DASH = '<span class="muted">—</span>'


def _row_html(m: dict, row: dict, group: str, candidates: dict[str, dict]) -> str:
    titles = _seq_titles(m)
    if row.get("contact"):
        contact = _e(row["contact"])
    elif row.get("email"):
        contact = '<span class="muted">role inbox</span>'
    else:
        contact = _MUTED_DASH
    title = _e(row["seat"]) if row.get("seat") else _MUTED_DASH
    status = str(row.get("email_status") or "").strip().lower()
    conf = str(row.get("conf_tier") or "").strip().lower()
    if status == "unverified" or conf in ("unknown", "unverified") or row["email"] == "unverified":
        email = '<span class="muted">unverified</span>'
    elif row.get("email"):
        email = _e(row["email"])
    else:
        email = '<span class="muted">none</span>'
    seqs = [s for s in _staged_seqs(row, group, candidates) if s != "a sequence"]
    tech_ids = f'<span class="tech"> Sequence id: {_e(", ".join(seqs))}.</span>' if seqs else ""
    tier = _e(row["tier"]) if row.get("tier") else _MUTED_DASH
    verdict = _e(row["verdict"]) if row.get("verdict") else _MUTED_DASH
    return (
        # `data-row` is the CANONICAL roster index: one selection hides the account everywhere.
        f'<tr data-row="{row["i"]}">'
        f"<td><strong>{_e(row['company'])}</strong></td>"
        f"<td>{contact}</td>"
        f"<td>{title}</td>"
        f"<td class='mono'>{email}</td>"
        f"<td>{_row_status(m, row['email'])}</td>"
        f"<td>{_e(_stands(row, group, candidates, titles) + _judge_clause(row))}{tech_ids}</td>"
        f"<td class='tech'>{tier}</td>"
        f"<td class='tech'>{_e(_verified(row))}</td>"
        f"<td class='tech'>{verdict}</td>"
        "</tr>"
    )


def _reconcile(m: dict, staged: list[dict], candidates: dict[str, dict]) -> str:
    """Compare "admissible on the list" against "enrolled at the provider", per sequence.

    Reported, never resolved: nothing on disk says WHICH rows the provider holds. Counted per
    MEMBERSHIP (a person on two lists is a row on each), as the provider counts. List half of
    the 2026-09-09 drift; the copy half (the provider serving pre-revision bodies) is invisible
    offline and is checked by reading the steps back before activating.
    """
    provider = _provider_enrolled(m)
    if not provider:
        return ""
    per_seq: dict[str, int] = {}
    for row in staged:
        for seq in (candidates.get(row["email"].lower()) or {}).get("sequences") or ():
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
                f'<li class="warn" data-warn="records-disagree"><code>{_e(seq)}</code>: '
                f"<strong>{listed} admissible on the list but {loaded} enrolled at the "
                "provider.</strong> The rows above are the ones the gate would admit; which of "
                "them are actually loaded is not knowable from disk. Check the sequence in the "
                "provider before sending.</li>"
            )
    if not lines:
        return ""
    return (
        '<p class="note"><strong>List vs provider.</strong> A recipient CSV says who was '
        "proposed; the provider says who was loaded. They are different questions and this "
        f'page does not merge them.</p><ul class="note">{"".join(lines)}</ul>'
    )


def _buckets(m: dict) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    roster = (m.get("roster") or {}).get("rows") or []
    packs = {r["account"] for r in (m.get("packs") or {}).get("packs") or []}
    candidates = _staged_candidates(m)
    buckets: dict[str, list[dict]] = {gid: [] for gid, _, _ in GROUPS}
    for row in roster:
        buckets[_group_of(row, packs, candidates)].append(row)
    return buckets, candidates


def _list_vs_provider(m: dict) -> str:
    """The list-vs-provider gap, for Operator notes → Numbers that need a look (2.6b)."""
    if not (m.get("roster") or {}).get("rows"):
        return ""
    buckets, candidates = _buckets(m)
    return _reconcile(m, buckets["staged"], candidates)


def _account_tiles(m: dict) -> str:
    """One row of tiles over the roster, every one within the filter's reach."""
    r = m.get("roster") or {}
    if not r.get("accounts"):
        return ""
    # PARTIAL COVERAGE IS THE FAILURE HERE, not disagreement — see `format.roster_gap`.
    if gap := roster_gap(m):
        return (
            '<div class="card"><h2>Accounts in scope</h2><p class="note">Not shown for '
            f"{_e(scope_label(m))} — {_e(gap)}</p></div>"
        )
    camps = m["campaigns"]["campaigns"]
    packs = len((m.get("packs") or {}).get("packs") or [])
    # The CURRENT campaigns' loaded people (PS20 P1.2), never every snapshot row.
    enrolled = _scope_figures(m)["loaded"][0]
    loaded_html = figure_span("loaded", enrolled)
    # Over composed BODIES (`forecast.drafted_to`), else the old arithmetic — never a zero.
    merge = len({x["to"] for x in (m.get("samples") or {}).get("rendered") or []})
    drafted = len(drafted_to(m))
    rows = r["rows"]
    # A pack is claimed by a DATE SUFFIX: a dateless slug claims none, so drafted is a floor.
    dateless = [c.get("slug", "?") for c in camps if not re.search(r"\d{8}$", c.get("slug", ""))]
    floor = (
        " · at least: " + ", ".join(dateless) + " claim no packs (no date in the slug)"
        if dateless
        else ""
    )
    return (
        '<div class="stats">'
        + _stat(
            r["accounts"],
            "accounts researched",
            src="rows:all",
            reach=True,
            sub_html=sub_counts(rows, ()),
        )
        + _stat(
            r["contact_verified"],
            "have a verified address",
            src="rows:co_has_email",
            reach=True,
            sub_html=sub_counts(rows, (("co_no_email", " do not"),)),
        )
        + _stat(
            r["named_seat"],
            "resolve to a named seat",
            src="rows:co_named",
            reach=True,
            sub_html=sub_counts(rows, (("co_role_inbox", " are role inboxes"),)),
        )
        + _stat(
            r["signal"],
            "carry a dated why-now",
            src="rows:co_signal",
            reach=True,
            sub_html=sub_counts(rows, (("co_signal_sourced", " of them cite a source"),)),
        )
        + _stat(
            drafted or (None if enrolled is None else packs + enrolled),
            "have a drafted email",
            sub_html=(
                f"{packs} hand-written 1:1 · {merge} rendered per recipient · "
                f"{loaded_html} of them loaded into the sequence{_e(floor)}"
                if drafted
                else f"{packs} hand-written 1:1 · {loaded_html} in the sequence{_e(floor)}"
            ),
            raw={"drafted": drafted, "packs": packs, "merge": merge, "enrolled": enrolled},
            src={
                "drafted": "distinct:samples.rendered.to|samples.packs.to",
                "enrolled": "sum:campaigns.actuals.loaded",
            },
            reach=True,
        )
        + "</div>"
    )


def _filter_area(m: dict) -> str:
    """The filter bar, its tripwire, and the technical-detail toggle — this tab's alone."""
    if not (m.get("roster") or {}).get("rows"):
        return ""
    return (
        f"{bar_html(m)}"
        '<label class="techtoggle"><input type="checkbox" id="tech-toggle">'
        "Show the technical detail</label>"
    )


def _account_table(m: dict) -> str:
    roster = (m.get("roster") or {}).get("rows") or []
    if not roster:
        return (
            '<div class="card"><h2>Accounts</h2><p class="note">Not shown: this scope has '
            "no campaign roster. This list is a per-campaign view — it needs a manifest "
            "declaring which run exports its accounts came from, or it would list the whole "
            "shared prospect pool, which is a different question.</p></div>"
        )
    buckets, candidates = _buckets(m)
    # Every account, exactly once — a row in no bucket would pass a short list off as whole.
    placed = sum(len(v) for v in buckets.values())
    body = []
    for gid, heading, blurb in GROUPS:
        rows = buckets[gid]
        if not rows:
            continue
        rows.sort(key=lambda r: (r.get("tier") != "A", r["company"].lower()))
        body.append(
            f'<tr class="grp" data-group="{_e(gid)}"><td colspan="9">'
            f"<strong>{_e(heading)}</strong> "
            f'<span class="pill" data-group-count="{_e(gid)}">{len(rows)}</span>'
            f'<div class="note">{_e(blurb)}</div></td></tr>'
        )
        body.extend(_row_html(m, r, gid, candidates) for r in rows)

    # Each chip is a slot the filter rewrites, so pills, headings and rows cannot drift.
    counts = " · ".join(
        f'<span data-group="{_e(g)}">{_e(h.split(" — ")[0])} '
        f'<span data-group-count="{_e(g)}">{len(buckets[g])}</span></span>'
        for g, h, _ in GROUPS
        if buckets[g]
    )
    # One line per ROW — a (campaign, account) pair — while the tiles count distinct accounts.
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
          <th>Account</th><th>Contact</th><th>Title</th><th>Email</th><th>Status</th>
          <th>What is left to do</th><th class="tech">Tier</th>
          <th class="tech">How it was verified</th><th class="tech">Research verdict</th>
        </tr></thead>
        <tbody>{"".join(body)}</tbody>
      </table>
      <p class="note">The <strong>research column</strong>, shown with the technical detail,
      is the researcher's call on the account, made before any email existed. The email
      judge's read of a drafted email is the last clause of <em>What is left to do</em>.
      Neither decides a send: that is the check run when a list is loaded.</p>
    </div>
    """


def _accounts_view(m: dict) -> str:
    return (
        section("filter", _filter_area(m))
        + section("account-tiles", _account_tiles(m))
        + section("account-table", _account_table(m))
    )
