"""The campaign roster — every account a campaign declared it is working.

Split out of :mod:`gtm_core.email_campaign_dashboard.sources` on 2026-09-10. ``sources`` is the
grab-bag of "where each panel's numbers come from"; the roster is one coherent thing with its own
identity rules — which campaign contributed a row, how rows fold, what counts as one account — and
it is about to grow the per-row campaign/product/country/segment attribution the filter bar reads.
Same §R10 move the complexity allowlist already records for ``render_manifest_pool``.

``judge_queue`` and its two helpers came along because ``roster_model`` is their only caller; leaving
them behind would have made this module import from ``sources`` while ``sources`` re-exports from
here — a cycle that works today and breaks on the first import-order change.
"""

from __future__ import annotations

import csv as _csv
from dataclasses import dataclass
from pathlib import Path

from ..email_compliance import normalize_market
from ..merge_hygiene.company import clean_segment
from ..prospects_consolidate import _prospects_dir, column_value

#: What each queue destination means to the person reading the page, in plain English. The
#: destination is the FOLLOW-UP, and it is the half a bare verdict word leaves out: eleven of
#: this campaign's twenty-four rejections are not asking for better copy at all.
_DESTINATION_GLOSS = {
    "prospect:re-target": (
        "find a different seat",
        "the argument is fine, the recipient does not own the problem — this goes back to "
        "prospecting for a person who does, not back to the writer",
    ),
    "spec:re-argue": (
        "rewrite the argument",
        "the seat is right and the copy is what the judge rejected — this goes back to the "
        "spec or the pack",
    ),
}


#: How a judge verdict ranks against another for the SAME recipient. A recipient scored on
#: two lanes gets the worst of the two, never the friendliest. One address per campaign
#: typically has two rows — a role-inbox merge row and a 1:1 pack written to the same inbox —
#: and reporting the kinder of the pair would tell a reader a row is workable while a sibling
#: row says it is held.
_JUDGE_RANK = {"send": 0, "re-angle": 1, "drop": 2}


def _normalise_defect(raw: str) -> str:
    """One id per finding, via the adjudication package's own normaliser."""
    try:
        from ..adjudication.defects import normalize_defect_class
    except ImportError:  # pragma: no cover - the package is in-repo
        return raw.strip().lower().replace("_", "-")
    return normalize_defect_class(raw) or raw.strip()


def judge_queue(profile: str, content_root: Path | None = None) -> dict[str, dict]:
    """The email judge's verdict and FOLLOW-UP per recipient, from the retarget queue.

    A verdict word on its own is half a finding. ``re-angle`` printed beside an account tells
    a reader something was wrong and nothing about what happens next — and on this campaign
    the two answers are as far apart as they get: 11 of 24 rejections say *the copy is fine,
    the person is wrong* and route to prospecting, while 13 say *the person is fine, the copy
    is wrong* and route to the spec. Those are different teams, different weeks and different
    money, and the page rendered them as the same word.

    Read from the newest ``evals/retarget-queue-*.jsonl`` on disk. Rows are keyed by address
    and the worst verdict wins (:data:`_JUDGE_RANK`) — see the two-rows-one-address case in
    its docstring. This is a RANKING, never a gate: the deterministic ``account_integrity``
    check is what refuses a row, and nothing here changes what may be sent.
    """
    import json as _json

    base = _prospects_dir(profile, content_root) / "evals"
    files = sorted(base.glob("retarget-queue-*.jsonl"))
    if not files:
        return {}
    out: dict[str, dict] = {}
    tally: dict[str, int] = {}
    for line in files[-1].read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = _json.loads(line)
        except ValueError:
            continue
        key = (r.get("email") or "").strip().lower()
        if not key:
            continue
        # Counted per ROW, before the per-address fold below. The two denominators differ by
        # one on this campaign (24 rows, 23 addresses) and a page that quotes one under the
        # other's label is the restated-number failure this tally exists to remove.
        tally["rows"] = tally.get("rows", 0) + 1
        tally["backend"] = (r.get("backend") or "").strip() or tally.get("backend", "")
        tally["batch"] = max(int(tally.get("batch") or 0), int(r.get("judge_batch") or 0))
        if (r.get("copy_revised") or "").strip():
            tally["revised"] = tally.get("revised", 0) + 1
        tally[f"verdict:{(r.get('verdict') or '—').strip()}"] = (
            tally.get(f"verdict:{(r.get('verdict') or '—').strip()}", 0) + 1
        )
        dest = (r.get("destination") or "—").strip()
        cls = (r.get("defect_class") or "").strip()
        if cls and dest:
            # Per (destination, class), so the page can NAME the classes behind each queue
            # instead of carrying a hand-typed list of them. The list it used to carry was
            # written against one judge run and was wrong by the next: a re-scored batch
            # returns a different vocabulary for the same findings, and a stale parenthetical
            # reads exactly like a current one.
            # Normalised, so the judge's kebab and snake spellings of one finding
            # (`fact-earns-its-place` / `fact_earns_its_place`) count as one class rather than
            # rendering as two entries of the same thing.
            key = f"class:{dest}|{_normalise_defect(cls)}"
            tally[key] = tally.get(key, 0) + 1
        if dest == "spec:re-argue":
            # Of the rows routed back to the writer, how many are a defect the operator has
            # ALREADY ruled on rather than an open rewrite. Two classes qualify and both are
            # argued elsewhere on this page: `no-signal-evidence` / `fact-not-earned` fire on
            # a generic lane because it carries no per-row signal by design, and an
            # unclassified rejection of a 1:1 pack is the category-claim conflict the operator
            # settled in favour of the category claim. Counting them keeps a reader from
            # reading "13 to rewrite" as 13 pieces of writing that are waiting to be done.
            bucket = (
                "settled"
                if cls in {"no-signal-evidence", "fact-not-earned", "(unclassified)"}
                else "open"
            )
            tally[f"reargue:{bucket}"] = tally.get(f"reargue:{bucket}", 0) + 1
        tally[f"dest:{(r.get('destination') or '—').strip()}"] = (
            tally.get(f"dest:{(r.get('destination') or '—').strip()}", 0) + 1
        )
        prev = out.get(key)
        if prev and _JUDGE_RANK.get(prev["verdict"], 0) >= _JUDGE_RANK.get(r.get("verdict"), 0):
            prev["also"] = True
            continue
        act, why = _DESTINATION_GLOSS.get(r.get("destination") or "", ("", ""))
        out[key] = {
            "verdict": (r.get("verdict") or "").strip(),
            "destination": (r.get("destination") or "").strip(),
            "action": act,
            "action_why": why,
            "defect": (r.get("defect_class") or "").strip(),
            "note": (r.get("note") or "").strip(),
            "calibrated": bool(r.get("judge_calibrated")),
            "filed": (r.get("filed") or "").strip(),
            # When this row's COPY was last revised, if it has been. A verdict describes the
            # bytes it read, so a page that cannot say whether those bytes have since changed
            # reports a stale opinion as a current one.
            "revised": (r.get("copy_revised") or "").strip(),
            "attempt": r.get("attempt") or 1,
            "source": files[-1].name,
            "also": bool(prev),
        }
    out["__tally__"] = tally  # type: ignore[assignment]
    return out


@dataclass(frozen=True)
class RosterSource:
    """One campaign's declared account exports, carrying the identity the rows inherit.

    Before 2026-09-10 ``model.py`` flattened every in-scope campaign's globs into one list
    before calling :func:`roster_model`, and the fold keyed on company alone across all of
    them. Campaign identity was destroyed twice over, so no row could say which campaign
    was working it — and ``product`` (a per-campaign manifest field) had nowhere to live at
    all. Both filter dimensions depend on this record existing.

    Deliberately not a bare 3-tuple: :mod:`gtm_core.email_campaign_dashboard.scope` already
    establishes a frozen dataclass as this package's shape for "what a page is about", and a
    positional tuple makes the call site an unreadable nested comprehension.
    """

    campaign: str
    product: str
    globs: tuple[str, ...]


def roster_sources(campaigns) -> list[RosterSource]:
    """Shaped campaign dicts -> the sources that declare a roster. One construction site.

    A campaign declaring no ``roster_globs`` is dropped here rather than contributing an
    empty source, so "which campaigns does this roster cover" has exactly one answer and
    ``format.roster_gap`` does not re-derive it a second way.
    """
    return [
        RosterSource(c.get("slug", ""), c.get("product", ""), tuple(c.get("roster_globs") or []))
        for c in campaigns or []
        if c.get("roster_globs")
    ]


def roster_model(profile: str, sources, content_root: Path | None = None) -> dict:
    """The campaign's OWN account roster, from the run exports its manifest declares.

    Everything else on this page counts the shared prospect pool, which is the right
    denominator for "who could we email next" and the wrong one for "how is this campaign
    doing". A campaign whose whole roster is 26 accounts rendered "731 people we will
    actually email of 859 loaded" — true of the profile, false of the campaign.

    One account may appear in several exports (a discovery pass, then an enrichment pass), so
    rows are folded by company and the RICHEST row wins: an address beats none, then a
    why-now, then a verdict. Taking the first or last row instead would silently report the
    pre-enrichment snapshot — on 2026-09-04 the run's first export held 18 rows with 2
    addresses, while the four together hold 26 accounts with 23.
    """

    base = _prospects_dir(profile, content_root)
    best: dict[tuple[str, str], tuple[tuple, dict, RosterSource]] = {}
    files: list[str] = []
    for src in sources or []:
        for pattern in src.globs:
            for path in sorted(base.glob(pattern)):
                files.append(path.name)
                try:
                    with path.open(newline="", encoding="utf-8") as fh:
                        for r in _csv.DictReader(fh):
                            company = column_value(r, "company")
                            if not company:
                                continue
                            rank = (
                                bool(column_value(r, "email")),
                                bool(column_value(r, "why_now")),
                                bool(column_value(r, "verdict")),
                            )
                            key = (src.campaign, company)
                            if key not in best or rank > best[key][0]:
                                best[key] = (rank, r, src)
                except OSError:
                    continue
    rows = [v[1] for v in best.values()]
    row_src = {id(v[1]): v[2] for v in best.values()}
    judged = judge_queue(profile, content_root)
    judge_tally = judged.pop("__tally__", {})

    # DISTINCT COMPANIES, not rows. The fold is keyed on `(campaign, company)`, so an
    # account two campaigns are both working is two rows of work and still one account.
    # Counting rows here would double it — and `views_status.py:39` / `views_who.py:181-182`
    # subtract these figures from each other, so a mixed unit renders "-3 do not".
    def _co(field: str) -> set[str]:
        """The companies for which ANY row carries ``field``. The unit of every tile."""
        return {column_value(r, "company") for r in rows if column_value(r, field)}

    def _n(field: str) -> int:
        return len(_co(field))

    companies = sorted({column_value(r, "company") for r in rows})
    ci_of = {c: i for i, c in enumerate(companies)}
    has_email, is_named = _co("email"), _co("first")
    has_signal, has_source = _co("why_now"), _co("signal_source_url")

    def _count(field: str) -> list[tuple[str, int]]:
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(column_value(r, field) or "—", set()).add(column_value(r, "company"))
        return sorted(((k, len(v)) for k, v in out.items()), key=lambda kv: -kv[1])

    out = {
        "sources": files,
        "accounts": len({column_value(r, "company") for r in rows}),
        # Campaign-account PAIRS — what the filter counts, and what the worklist
        # table has one line per. Kept beside `accounts` so the page can say both.
        "rows_n": len(rows),
        "contact_verified": _n("email"),
        "named_seat": _n("first"),
        "signal": _n("why_now"),
        "signal_sourced": _n("signal_source_url"),
        "tiers": _count("tier"),
        "verdicts": _count("verdict"),
        "companies": sorted({column_value(r, "company") for r in rows}),
        "judge_source": next(iter(judged.values()), {}).get("source", ""),
        "judge_tally": judge_tally,
        "judge_actions": sorted(
            {(j["action"], j["action_why"]) for j in judged.values() if j["action"]}
        ),
        "rows": sorted(
            (
                {
                    "company": column_value(r, "company"),
                    "seat": column_value(r, "title"),
                    "email": column_value(r, "email"),
                    "named": bool(column_value(r, "first")),
                    "tier": column_value(r, "tier"),
                    "verdict": column_value(r, "verdict"),
                    "score": column_value(r, "score"),
                    "signal": bool(column_value(r, "why_now")),
                    # --- the four filter dimensions -------------------------------------
                    # Campaign and product are the SOURCE's, not the row's: a run export
                    # carries no such column, and product is a manifest field. Country and
                    # segment are the row's own, read through the alias map, which also
                    # resolves exports written under an earlier column prefix.
                    #
                    # Each keeps a raw value for the table cell and a normalised `_key` for
                    # grouping. `GTM_Segment` is spelled `Builder` in two of this campaign's
                    # four exports and `builder` in the other two, so a facet built on the
                    # raw value would offer one segment twice.
                    "campaign": row_src[id(r)].campaign,
                    "product": row_src[id(r)].product,
                    "country": column_value(r, "country"),
                    "country_key": normalize_market(column_value(r, "country")),
                    "segment": column_value(r, "segment"),
                    "segment_key": clean_segment(column_value(r, "segment")),
                    "signal_source_url": column_value(r, "signal_source_url"),
                    # --- what the client-side filter counts ------------------------------
                    # `ci` is an opaque index into `companies`, never the name: the filter
                    # payload ships in the page and a company name is third-party identity
                    # (§R9). It is what lets the browser count DISTINCT ACCOUNTS —
                    # `new Set(rows.filter(p).map(r => r.ci)).size` — which is the unit
                    # every reactive tile is in.
                    "ci": ci_of[column_value(r, "company")],
                    # Company-level, not row-level, and derived from the same sets `_n()`
                    # counts — so each reproduces its server value exactly at zero filter.
                    # They ship in COMPLEMENTARY PAIRS that partition the account set, which
                    # is how the two subtraction sub-lines ("3 do not", "6 are role inboxes")
                    # become counts rather than arithmetic done in JS.
                    #
                    # Set membership, not subtraction, is also the more durable claim: today
                    # every named seat happens to have an address, so `contact_verified -
                    # named_seat` is right by luck. The day one account resolves a name
                    # without an inbox, the subtraction is wrong and `co_role_inbox` is not.
                    #
                    # A row filtered out does not revoke its account's fact: an account whose
                    # only email-bearing row is hidden still counts as having a verified
                    # address, because that is true of the ACCOUNT, not of the row.
                    "co_has_email": column_value(r, "company") in has_email,
                    "co_no_email": column_value(r, "company") not in has_email,
                    "co_named": column_value(r, "company") in is_named,
                    "co_role_inbox": (
                        column_value(r, "company") in has_email
                        and column_value(r, "company") not in is_named
                    ),
                    "co_signal": column_value(r, "company") in has_signal,
                    "co_signal_sourced": column_value(r, "company") in has_source,
                    # A verdict word with no reason is an assertion the reader cannot check.
                    # "drop" on its own reads as our opinion; "Gate B - body-shop SI" is a
                    # finding they can disagree with.
                    "verdict_reason": column_value(r, "verdict_reason"),
                    "why_now": column_value(r, "why_now"),
                    # The email judge's read of the DRAFTED copy for this address, and what
                    # it routes to. A different question from GTM_Verdict, which is the
                    # researcher's call on the ACCOUNT before any copy existed — the two are
                    # kept side by side rather than merged, because they disagree usefully.
                    "judge": judged.get(column_value(r, "email").lower()),
                }
                for r in rows
            ),
            key=lambda x: (x["tier"] != "A", x["company"].lower()),
        ),
    }
    # The canonical row index, assigned AFTER the sort. Two tables render these same rows in
    # DIFFERENT orders — the who-tab table in this order, the worklist regrouped into buckets
    # and re-sorted within each — and both stamp `data-row` on their `<tr>`. Indexing by
    # display position would make the same account row 4 on one tab and row 19 on the other,
    # so the filter would hide a different row than it counted.
    for i, row in enumerate(out["rows"]):
        row["i"] = i
    return out
