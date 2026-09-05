from __future__ import annotations

import csv
import sys
from pathlib import Path

from ..merge_hygiene import row_signal_freshness
from .columns import MASTER_COLS
from .confidence import _get, _score_num
from .io import _atomic_write_csv_cols, _load_master
from .paths import (
    _pool_dir,
    _prospects_dir,
    _sequences_dir,
    needs_verification_path,
    ready_to_load_path,
)
from .suppression import _load_dnc


def split_by_signal(profile: str, content_root: Path | None = None) -> dict:
    """Split ``ready-to-load.csv`` into a signal-led list and a generic one.

    A merge sequence can only open on a "why now" that EVERY enrolled row carries — an
    empty merge tag renders a broken sentence, so one list cannot serve both populations.
    This writes two, each matched to the copy it can support:

    * ``ready-to-load-signal.csv``  — rows whose ``why_now`` reduces to a clause safe to
      render (:func:`gtm_core.merge_hygiene.signal_clause`), with that clause in a
      ``signal_clause`` column for the sequencer to map to a custom field.
    * ``ready-to-load-generic.csv`` — everything else, for the existing generic copy.

    Fail-closed: a row whose signal cannot be reduced verbatim and safely lands in the
    generic list. Intent-topic scores ("machine learning (intent score 81)") and research
    notes recording the ABSENCE of a signal are never treated as signals.

    A clause must clear TWO independent gates, because being well-formed is not the same
    as being true today. :func:`~gtm_core.merge_hygiene.signal_clause` judges the shape;
    :func:`~gtm_core.merge_hygiene.signal_is_fresh` judges the age, since the copy opens
    on "saw the news" and a 15-month-old acquisition is not news. A stale or undated
    clause is demoted to the generic arc, which makes no recency claim.
    """
    ready = _load_master(ready_to_load_path(profile, content_root))
    signal_rows: list[dict] = []
    generic_rows: list[dict] = []
    stale_signal = 0
    for row in ready:
        # One predicate, shared with the lane router (`gtm_core.lanes`), so the two can
        # never disagree about which rows may open on "saw the news". Its docstring
        # carries the `signal_observed`-first reasoning that used to live here.
        clause, fresh = row_signal_freshness(row)
        if clause and fresh:
            signal_rows.append({**row, "signal_clause": clause})
        else:
            if clause:
                stale_signal += 1
            generic_rows.append(row)

    seq_dir = _sequences_dir(profile, content_root)
    signal_path = seq_dir / "ready-to-load-signal.csv"
    generic_path = seq_dir / "ready-to-load-generic.csv"

    _atomic_write_csv_cols(signal_path, signal_rows, [*MASTER_COLS, "signal_clause"])
    _atomic_write_csv_cols(generic_path, generic_rows, MASTER_COLS)

    populated = sum(1 for r in ready if (r.get("why_now") or "").strip())
    result = {
        "profile": profile,
        "ready_total": len(ready),
        "why_now_populated": populated,
        "signal_led": len(signal_rows),
        "generic": len(generic_rows),
        "why_now_unusable": populated - len(signal_rows),
        "signal_stale_demoted": stale_signal,
        "signal_path": str(signal_path),
        "generic_path": str(generic_path),
    }
    print(
        f"split[{profile}]: {result['signal_led']} signal-led · {result['generic']} generic "
        f"({result['why_now_unusable']} of {populated} why_now values were not usable "
        f"as an opening clause)",
        file=sys.stderr,
    )
    return result


def _print_banner(result: dict) -> None:
    """One human-readable status line to stderr (stdout stays clean JSON) — the
    'one number, always shown' surface so readiness never needs archaeology."""
    print(
        f"pool[{result['profile']}]: {result['ready_to_load']} ready · "
        f"{result['needs_verification']} verifying · "
        f"{result['blocked_excluded']} blocked · "
        f"{result['net_new_folded']} net-new folded"
        + (
            f" · {result['merge_field_excluded']} merge-field blocked"
            if result.get("merge_field_excluded")
            else ""
        )
        + (
            f" · {result['sent_person_excluded']} already-contacted skipped"
            if result.get("sent_person_excluded")
            else ""
        )
        + (
            f" · {result['out_of_market_excluded']} out-of-market"
            if result.get("out_of_market_excluded")
            else ""
        )
        + (
            f" · {result['unknown_country']} unknown-country"
            if result.get("unknown_country")
            else ""
        )
        + (
            f" · {result['reclassified_from_source']} reclassified"
            if result.get("reclassified_from_source")
            else ""
        )
        + (
            f" · {result['rebuilt_master_tier_changes']} rebuilt-tiers"
            if result.get("rebuilt_master_tier_changes")
            else ""
        ),
        file=sys.stderr,
    )


def pool_status(profile: str, content_root: Path | None = None) -> dict:
    """Cheap read-only summary of the last consolidation run — no MCP, no
    re-sweep. Safe for a cockpit brief or a daily heartbeat check."""
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    unconsolidated = 0
    master_emails = {r["email"] for r in master}
    for path in _prospects_dir(profile, content_root).glob("prospects-*-hubspot.csv"):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                email = _get(row, "email").lower()
                if email and "@" in email and email not in master_emails:
                    unconsolidated += 1
    by_tier = {"high": 0, "medium": 0, "unknown": 0, "blocked": 0}
    for r in master:
        by_tier[r["conf_tier"]] = by_tier.get(r["conf_tier"], 0) + 1
    return {
        "master_total": len(master),
        "by_confidence_tier": by_tier,
        "unconsolidated_in_raw_exports": unconsolidated,
    }


def next_verification_batch(
    profile: str,
    limit: int = 50,
    content_root: Path | None = None,
    *,
    dnc_file: Path | None = None,
    require_dnc: bool = False,
) -> list[dict]:
    """The top ``limit`` rows from the hold queue, highest score first, shaped as
    Saleshandy prospect objects — the single call the email-sequence skill makes
    to auto-drain ``needs-verification.csv`` (import these with verify=true, let
    the sequencer's verifier grade them, survivors re-gate to ready on the next
    sweep). Rows without a usable first/last/email are skipped.

    Suppression is re-checked here even though ``consolidate`` already filtered the
    hold queue: the file on disk is a snapshot, and someone can opt out between the
    sweep that wrote it and the import that drains it. The provider only enforces its
    own DNC at *send* time, so this is the last point at which we can avoid handing a
    suppressed address to a third party at all.
    """
    dnc = _load_dnc(profile, content_root, dnc_file, require=require_dnc)
    rows = _load_master(needs_verification_path(profile, content_root))
    rows.sort(key=lambda r: _score_num(r["score"]), reverse=True)
    out: list[dict] = []
    for r in rows:
        if dnc.blocks(r["email"], r["company_domain"]):
            continue
        if not (r["first"] and r["last"] and r["email"]):
            continue
        out.append(
            {
                "First Name": r["first"],
                "Last Name": r["last"],
                "Email": r["email"],
                "Company": r["company"],
                "Job Title": r["title"],
                # Saleshandy's actual field label is "Company Domain" (confirmed via
                # list_fields 2026-07-23) — NOT "Company Domain Name", which the
                # import API rejects with a 400.
                "Company Domain": r["company_domain"],
                "Country": r["country"],
            }
        )
        if len(out) >= limit:
            break
    return out
