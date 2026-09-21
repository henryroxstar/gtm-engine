from __future__ import annotations

import csv
import json
import sys
import uuid
from pathlib import Path

from .. import prospect_paths
from ..lane_verdicts import LANE_VERDICTS
from ..merge_hygiene import blocks as mh_blocks
from ..merge_hygiene import check_row as mh_check_row
from ..prospects_state import ACCOUNT_ID_FIELD, _identity_keys
from ..suppression import load_index as load_suppression_index
from .accounts import (
    _AUTHORITATIVE_RECORD_COLUMNS,
    _account_id_index,
    _account_item_of,
    _account_key_of,
    _account_record_index,
    _disqualified_account_keys,
    _verdict_at_least_as_strict,
)
from .confidence import (
    _CONF_RANK,
    _get,
    _person_key,
    _row_to_record,
    _score_num,
    classify_confidence,
)
from .io import (
    _append_blocked_log,
    _atomic_write_csv,
    _existing_master_path,
    _load_master,
    _snapshot,
)
from .paths import (
    _pool_dir,
    _prospects_dir,
    hand_send_path,
    needs_verification_path,
    ready_to_load_path,
)
from .queues import _print_banner
from .suppression import MarketGate, _load_dnc, _load_sent, _resolve_market_gate

#: Verdicts a hand-sent row may carry. A "Hi team," body makes no per-recipient claim, so
#: it is the generic lane's contract — and `drop` is admissible in no lane at all.
_HAND_SEND_OK = LANE_VERDICTS["generic"]

# --- core ---------------------------------------------------------------


def _stamp_lanes(rows: list[dict], profile: str, content_root: Path | None) -> int:
    """Join `lanes route`'s assignment back onto the pooled rows, by email.

    The router's own output is `evals/lanes-state.jsonl` plus per-lane CSVs; nothing
    carried the assignment onto `master-list.csv`/`ready-to-load.csv`, so the `lane`
    column those files declare read blank for every row — a field that asserts
    emptiness, which is worse than an absent one. The enrollment gate reads the
    COLUMN (`account_integrity --lane` refuses a list whose column disagrees), so
    without this join the routing decision is invisible exactly where it is enforced.

    A row the router never saw — never routed at all, or since dropped from the state file
    because a later route no longer includes its email — gets a BLANK ``lane``/``lane_reason``
    rather than keeping whatever it last had: a stale stamp reads as "still routed this way",
    which is worse than an admittedly-unrouted row (fixed 2026-09-10, PS2 — the enrollment
    gate's own rule against a field asserting emptiness applies just as much to a field
    asserting a lane that is no longer true). A row the router DID see is restamped every
    sweep, because the pooled CSVs are rebuilt and the router is the only thing that may
    author a lane.
    """
    from ..lanes.decisions import state_path

    path = state_path(profile, content_root)
    if not path.exists():
        return 0
    by_email: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            email = (rec.get("email") or "").strip().lower()
            if email:
                by_email[email] = rec
    stamped = 0
    for row in rows:
        rec = by_email.get((row.get("email") or "").strip().lower())
        if not rec:
            row["lane"] = ""
            row["lane_reason"] = ""
            continue
        row["lane"] = rec.get("lane") or ""
        row["lane_reason"] = rec.get("reason") or rec.get("trigger") or ""
        stamped += 1
    return stamped


def restamp_ready_to_load(profile: str, content_root: Path | None = None) -> int:
    """Re-stamp ``lane``/``lane_reason`` on ``ready-to-load.csv`` from whatever
    ``lanes-state.jsonl`` says right now, without waiting for the next full ``consolidate``
    sweep (PS2). ``lanes route`` calls this immediately after writing the state file, so the
    ONE file a human loads from never carries a stale lane between sweeps — on live data this
    file was seen 5 days stale, because routing and consolidation ran on independent
    schedules and only consolidation used to call ``_stamp_lanes``.

    Returns the number of rows ``_stamp_lanes`` found a matching state record for. A missing
    or empty ``ready-to-load.csv`` is a no-op (0), never an error — a profile that has not
    consolidated yet has nothing to restamp.
    """
    path = ready_to_load_path(profile, content_root)
    rows = _load_master(path)
    if not rows:
        return 0
    stamped = _stamp_lanes(rows, profile, content_root)
    _atomic_write_csv(path, rows)
    return stamped


def consolidate(
    profile: str,
    *,
    content_root: Path | None = None,
    dnc_file: Path | None = None,
    allow_shrink: bool = False,
    require_dnc: bool = False,
    target_markets: list[str] | None = None,
    strict_market: bool = False,
    reclassify: bool = False,
    allow_downgrade: bool = False,
    rebuild_master: bool = False,
    unattended: bool = False,
) -> dict:
    """Sweep every ``prospects-*-hubspot.csv`` export, fold net-new emails into
    the canonical ``sequences/master-list.csv``, gate by deliverability
    confidence, and write ``ready-to-load.csv`` / ``needs-verification.csv``.

    Idempotent: safe to call after every run (even a partial one) or on a
    schedule — re-running with no new exports reproduces the same output.

    ``require_dnc`` makes a missing/empty/undatable/stale suppression cache a hard
    error instead of an empty suppression set. Unattended callers must pass it: a
    successful-looking consolidate that suppressed nothing is the failure this guards.

    ``target_markets`` (default: read from the profile's PROFILE.md) is the allowed-jurisdiction
    list — rows outside it stay in the master list but never reach ``ready-to-load.csv`` or the
    hold queue, so an out-of-market lead is never something to strip at enrollment time.
    ``strict_market`` additionally drops rows with **no** country instead of counting them.

    ``reclassify`` fixes the "correction never lands" gap: by identity, a row whose email is
    already in the master list is normally skipped outright, so if a later export of the same
    contact now carries a verification signal the earlier ingestion didn't (e.g. an "Email
    Status" column added after the fact), that upgrade is silently lost forever. With
    ``reclassify=True``, an already-present row is still never duplicated, but its
    ``email_status``/``conf``/``conf_tier`` are recomputed from the newer source row and applied
    when they represent an upgrade. Downgrades are ignored unless ``allow_downgrade=True`` — a
    later export with a *weaker* signal should not evict a row that already proved deliverable.

    ``rebuild_master`` recomputes ``conf_tier`` for every row already in the master list from its
    own stored ``email_status``/``conf`` fields, with no source export needed — for when
    ``master-list.csv`` was hand-edited directly, or ``classify_confidence``'s rules changed.
    """
    master_path = _existing_master_path(profile, content_root)
    existing = _load_master(master_path)

    rebuilt_tier_changes = 0
    if rebuild_master:
        for r in existing:
            new_tier = classify_confidence(
                r["email_status"],
                r["conf"],
                segment=r["segment"],
                email=r["email"],
                company_domain=r["company_domain"],
            )
            if new_tier != r["conf_tier"]:
                rebuilt_tier_changes += 1
                r["conf_tier"] = new_tier

    by_email: dict[str, dict] = {}
    master_dups = 0
    for r in existing:
        if r["email"] in by_email:
            master_dups += 1
        else:
            by_email[r["email"]] = r

    dnc = _load_dnc(profile, content_root, dnc_file, require=require_dnc)
    market = (
        MarketGate(target_markets, strict=strict_market)
        if target_markets is not None
        else _resolve_market_gate(profile, strict_market)
    )
    sent_emails, sent_people = _load_sent(profile, content_root)
    existing_emails = {r["email"] for r in existing}

    net_new, dup_rows, already_present, dnc_hits, reclassified = 0, 0, 0, 0, 0
    for path in sorted(_prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if not email or "@" not in email:
                    continue
                rec = _row_to_record(row, path.name)
                # Suppression is checked against the record (not the bare address) so a
                # domain-typed DNC entry can match on company_domain too.
                if dnc.blocks(email, rec["company_domain"]):
                    dnc_hits += 1
                    continue
                if email in existing_emails:
                    already_present += 1
                    if reclassify:
                        current = by_email[email]
                        old_rank = _CONF_RANK[current["conf_tier"]]
                        new_rank = _CONF_RANK[rec["conf_tier"]]
                        if new_rank > old_rank or (
                            allow_downgrade and rec["conf_tier"] != current["conf_tier"]
                        ):
                            current["email_status"] = rec["email_status"]
                            current["conf"] = rec["conf"]
                            current["conf_tier"] = rec["conf_tier"]
                            reclassified += 1
                    continue
                if email in by_email:
                    # collision within this sweep's net-new rows
                    if _score_num(rec["score"]) > _score_num(by_email[email]["score"]):
                        by_email[email] = rec
                    dup_rows += 1
                    continue
                by_email[email] = rec
                net_new += 1

    all_rows = list(by_email.values())
    if not allow_shrink and master_path is not None and len(all_rows) < len(existing):
        msg = f"refusing to shrink master list: {len(existing)} -> {len(all_rows)} rows; pass allow_shrink=True to override"
        if unattended:
            print(f"UNATTENDED MODE: {msg}. Aborting consolidation safely.", file=sys.stderr)
            return {"status": "aborted", "reason": "shrink_prevented"}
        raise ValueError(msg)

    # Stamp identity before anything downstream reads it. Both ids are assigned once and
    # never reassigned — a join key that changes under a row is worse than none.
    account_index = _account_id_index(profile, content_root)
    account_records = _account_record_index(profile, content_root)
    rows_stamped, accounts_joined, orphan_rows, records_joined = 0, 0, 0, 0
    for row in all_rows:
        if not str(row.get("pool_row_id") or "").strip():
            row["pool_row_id"] = f"r-{uuid.uuid4().hex[:10]}"
            rows_stamped += 1
        if not str(row.get(ACCOUNT_ID_FIELD) or "").strip():
            hit = next(
                (
                    account_index[k]
                    for k in _identity_keys(_account_item_of(row))
                    if k in account_index
                ),
                "",
            )
            if hit:
                row[ACCOUNT_ID_FIELD] = hit
                accounts_joined += 1
            else:
                orphan_rows += 1
        # Carry the account's research record down onto its rows, so the verdict is
        # readable where the gate actually filters. Fill-only: a value the row already
        # carries came from its own source export, which is the more specific artifact,
        # and is never overwritten. Re-derived from `latest.json` on every sweep, so a
        # rebuild cannot drop it and a corrected record propagates on the next pass —
        # the same reason suppression stamping was moved inside this loop.
        record = account_records.get(str(row.get(ACCOUNT_ID_FIELD) or "").strip())
        if record:
            # A research record is ATOMIC. When the account's `why_now` supersedes the
            # row's, its provenance supersedes with it — source URL, observed date, evidence
            # span, subject, agent kind. Taking the clause alone and leaving the old
            # provenance in place produces a row whose cited source does not support its own
            # claim, which is precisely the `signal-evidence-unsupported` defect the record
            # columns exist to catch. Seen 2026-08-29 on the first cut of this code: Pace's
            # row carried a new clause about a named customer selecting them, still citing
            # the funding article the old clause came from.
            #
            # `verdict` is excluded and keeps its own monotone-stricter rule, so a
            # superseding record can still only tighten it, never promote a row to sendable.
            # Keyed on nothing: the account record simply wins for every account-level
            # column. Conditioning this on "the clause changed" was tried first and LATCHES —
            # the condition is true only on the single pass that copies the new clause down,
            # and on the pass after that the row already matches, so stale provenance sitting
            # beside a fresh clause becomes permanent. Measured before flipping: with the
            # account value and the row value both non-empty and different, this touches at
            # most 11 of 1,171 rows per column.
            filled = False
            for col, value in record.items():
                if col in ("verdict", "verdict_reason"):
                    authoritative = _verdict_at_least_as_strict(
                        row.get("verdict", ""), record.get("verdict", "")
                    )
                else:
                    authoritative = col in _AUTHORITATIVE_RECORD_COLUMNS
                if authoritative or not str(row.get(col) or "").strip():
                    if str(row.get(col) or "") != value:
                        filled = True
                    row[col] = value
            if filled:
                records_joined += 1

    # Re-derive the suppression cache from the LEDGER on every sweep, before the master
    # is written. The two columns are in MASTER_COLS now, so they survive the projection;
    # stamping here is what makes them true. Previously `suppression apply` was a separate
    # command a human had to remember to re-run after each consolidate, and the one time
    # nobody did, 55 exclusions were silently wiped an hour after being recorded.
    ledger_index = load_suppression_index(prospect_paths.suppression_ledger(profile, content_root))
    ledger_marked = 0
    for row in all_rows:
        hit = ledger_index.match(row)
        if hit:
            row["suppression"] = hit.reason
            row["suppression_date"] = hit.date
            ledger_marked += 1

    lanes_stamped = _stamp_lanes(all_rows, profile, content_root)

    pool = _pool_dir(profile, content_root)
    master_canonical = pool / "master-list.csv"
    _snapshot(master_canonical, pool / ".snapshots")
    _atomic_write_csv(master_canonical, all_rows)

    blocked = [r for r in all_rows if r["conf_tier"] == "blocked"]
    _append_blocked_log(profile, content_root, blocked)

    # Accounts an operator or an eval writeback has retired. Until 2026-08-27 the build
    # read no lifecycle status at all, so `status: disqualified` was decoration: the row
    # came back in the very next ready-to-load.csv.
    disqualified_keys = _disqualified_account_keys(profile, content_root)
    suppressed_excluded, disqualified_excluded = 0, 0
    ready_blocked: list[dict] = []

    # Loadable = not DNC'd, not already-sent (by email), not blocked-confidence, not
    # locally suppressed, not on a disqualified account. Re-checked against the full
    # master (not just this sweep's net-new rows) so a row folded in before an
    # address/domain was suppressed is dropped on the next sweep.
    loadable = []
    for r in all_rows:
        if dnc.blocks(r["email"], r["company_domain"]) or r["email"] in sent_emails:
            continue
        if r["conf_tier"] == "blocked":
            continue
        if (r.get("suppression") or "").strip():
            suppressed_excluded += 1
            ready_blocked.append({**r, "blocked_reason": f"suppressed:{r['suppression']}"})
            continue
        if _account_key_of(r) in disqualified_keys:
            disqualified_excluded += 1
            ready_blocked.append({**r, "blocked_reason": "status-disqualified"})
            continue
        loadable.append(r)

    if ready_blocked:
        _append_blocked_log(profile, content_root, ready_blocked)

    # Jurisdiction pass: a lead outside target_markets is dropped from BOTH the ready list and the
    # hold queue — verifying it would push their PII to the sequencer for a send that can't happen.
    # The row stays in the master list; this is a load gate, not a delete.
    out_of_market_excluded, unknown_country = 0, 0
    if market:
        in_market: list[dict] = []
        for r in loadable:
            if not (r.get("country") or "").strip():
                unknown_country += 1
            if market.blocks(r.get("country", "")):
                out_of_market_excluded += 1
                continue
            in_market.append(r)
        loadable = in_market

    # Person-level pass: drop anyone already sent under a DIFFERENT address, and
    # collapse the same human carrying two email formats to their best row.
    sent_person_excluded = 0
    best_by_person: dict[str, dict] = {}
    person_unique: list[dict] = []
    person_dups_collapsed = 0
    for r in loadable:
        pk = _person_key(r)
        if pk and pk in sent_people:
            sent_person_excluded += 1
            continue
        if not pk:
            person_unique.append(r)  # can't identify a person — never collapse
            continue
        prev = best_by_person.get(pk)
        if prev is None:
            best_by_person[pk] = r
            person_unique.append(r)
            continue
        person_dups_collapsed += 1
        # keep the higher-confidence row, breaking ties on score
        if (_CONF_RANK[r["conf_tier"]], _score_num(r["score"])) > (
            _CONF_RANK[prev["conf_tier"]],
            _score_num(prev["score"]),
        ):
            person_unique[person_unique.index(prev)] = r
            best_by_person[pk] = r

    # Merge-field gate: a row whose {{First Name}}/{{Company}} would render a broken or
    # obviously-templated email never reaches the load file, even at high confidence. The
    # repair in _row_to_record already fixed everything mechanically fixable, so what is
    # left needs a human — guessing someone's name is not a repair this code will make up.
    # Excluded, logged, and counted; never silently dropped.
    merge_blocked = [r for r in person_unique if r["conf_tier"] == "high" and mh_blocks(r)]
    if merge_blocked:
        _append_blocked_log(
            profile,
            content_root,
            merge_blocked,
            reason_of=lambda r: (
                "merge-field: " + ", ".join(f.rule for f in mh_check_row(r) if f.level == "block")
            ),
        )
    merge_blocked_emails = {r["email"] for r in merge_blocked}

    ready = [
        r
        for r in person_unique
        if r["conf_tier"] == "high" and r["email"] not in merge_blocked_emails
    ]
    needs_verification = [r for r in person_unique if r["conf_tier"] in ("medium", "unknown")]

    # The hand-send list. A merge-blocked row is not an unsendable row — a role inbox takes
    # a "Hi team," body perfectly well — it is a row no SEQUENCER can render, so a person
    # sends it. That person is downstream of every load-time gate, which is exactly why the
    # verdict filter has to be applied HERE. Until 2026-09-08 these rows were counted and
    # dropped, and the hand-send list got rebuilt by hand later with no filter at all.
    #
    # Filtered to the GENERIC lane's admissible verdicts (send / re-angle / unset): a body
    # addressed to "team" makes no per-recipient claim, which is the generic lane's own
    # definition. `drop` is admitted nowhere, and that is the whole point.
    hand_send = [r for r in merge_blocked if (r.get("verdict") or "").strip() in _HAND_SEND_OK]
    hand_send_refused = len(merge_blocked) - len(hand_send)

    _atomic_write_csv(ready_to_load_path(profile, content_root), ready)
    _atomic_write_csv(hand_send_path(profile, content_root), hand_send)
    _atomic_write_csv(needs_verification_path(profile, content_root), needs_verification)

    result = {
        "profile": profile,
        "master_total": len(all_rows),
        "master_dups_collapsed": master_dups,
        "net_new_folded": net_new,
        "net_new_dups_collapsed": dup_rows,
        "already_in_master_skipped": already_present,
        "reclassified_from_source": reclassified,
        "rebuilt_master_tier_changes": rebuilt_tier_changes,
        "dnc_hits_blocked": dnc_hits,
        "pool_row_ids_stamped": rows_stamped,
        "accounts_joined": accounts_joined,
        "records_joined": records_joined,
        "orphan_rows": orphan_rows,
        "ledger_suppressions_marked": ledger_marked,
        "suppressed_excluded": suppressed_excluded,
        "disqualified_excluded": disqualified_excluded,
        "market_gate": ", ".join(market.markets) if market else "off",
        "out_of_market_excluded": out_of_market_excluded,
        "unknown_country": unknown_country,
        "sent_person_excluded": sent_person_excluded,
        "person_dups_collapsed": person_dups_collapsed,
        "lanes_stamped": lanes_stamped,
        "ready_to_load": len(ready),
        "needs_verification": len(needs_verification),
        "merge_field_excluded": len(merge_blocked),
        "hand_send": len(hand_send),
        "hand_send_refused": hand_send_refused,
        "blocked_excluded": len(blocked),
        "master_list": str(master_canonical),
        "ready_to_load_path": str(ready_to_load_path(profile, content_root)),
        "hand_send_path": str(hand_send_path(profile, content_root)),
        "needs_verification_path": str(needs_verification_path(profile, content_root)),
    }
    _print_banner(result)
    try:
        from ..retention_sweep import sweep_stale_pii

        sweep_stale_pii(profile, ttl_days=7, content_root=content_root)
    except Exception as exc:  # noqa: BLE001
        print(f"retention sweep skipped: {exc}", file=sys.stderr)
    try:
        from ..email_campaign_dashboard import render_dashboard as _render_gtm

        _render_gtm(profile, content_root)
    except Exception as exc:  # noqa: BLE001
        print(f"dashboard refresh skipped: {exc}", file=sys.stderr)
    return result
