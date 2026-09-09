"""The prospect → enrichment → ready-to-load **status model**. Model only, no HTML.

:func:`build_status` reads the files ``prospects_consolidate`` already wrote and answers,
in one dict, *where the data is* and *what to do next*:

  * the **account layer** — companies identified by the bulk (Vibe) pulls in
    ``prospects/imports/`` that carry no person/email yet (the enrichment backlog),
  * the **email funnel** — people who DO have an email, gated by deliverability
    into ready / needs-verification / blocked / excluded, and
  * the cost model and the enrollment gate's own numbers, computed on the same
    population and the same filter ``account_integrity --require-verdict send`` uses.

**This module renders nothing and writes nothing.** Until 2026-09-05 it carried a second
renderer over this same model, writing ``prospects/status-standalone.html`` — a page the
``prospect`` skill explicitly told the agent not to render, kept fresh by nobody, and free
to diverge from the one page an operator actually opens. Retired: one model, one renderer.
:mod:`gtm_core.email_campaign_dashboard` is the only writer of a status page, and it
consumes :func:`build_status` from here.

It is deliberately stdlib-only (like :mod:`gtm_core.prospects_consolidate`) so it can run
standalone. Anything that needs an MCP call (the live sequencer state) is read from an
optional ``.pool/sequence-stats.json`` the skill/agent layer drops; the model degrades
gracefully when it is absent (same plumbing-vs-judgment split as consolidate).

Numbers are read from the *files on disk* (the source of truth already written by
consolidate), never recomputed — so the model can never disagree with the CSVs.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.account_integrity import audit_rows, filter_by_verdict
from gtm_core.outreach_log import collect_rows
from gtm_core.paths import resolve_content_root
from gtm_core.prospect_paths import suppression_ledger
from gtm_core.prospects_backlog import enrichment_queue_path
from gtm_core.prospects_consolidate import (
    _load_master,
    _pool_dir,
    _prospects_dir,
    needs_verification_path,
    pool_status,
    ready_to_load_path,
)
from gtm_core.prospects_consolidate import (
    account_has_dossier as _account_has_dossier,
)
from gtm_core.prospects_consolidate import (
    org_token as _org_token,
)
from gtm_core.prospects_state import load_latest
from gtm_core.suppression import load_index as _load_suppression_index

# Reference cost figures (NOT live — see gtm_core cost ledger for actuals).
# Sourced from the Vibe/RocketReach cost-model notes; shown so the operator can
# see the shape of the spend before authorizing an enrichment pass.
COST_MODEL = [
    (
        "Bulk account pull (Vibe fetch)",
        "free",
        "masked 5-row preview; identifies companies, no emails",
    ),
    (
        "Person resolution (RocketReach person_search)",
        "~metered lookup",
        "find the decision-maker at each account",
    ),
    (
        "Email + grade (RocketReach lookup)",
        "~metered lookup",
        "returns the address + A/B/F deliverability grade",
    ),
    (
        "Vibe export (unmask emails)",
        "~1 credit/row (~$0.03)",
        "alternative bulk path; ~$30 per 1,000 rows",
    ),
    ("Saleshandy verify-on-import", "included", "grades Valid/Risky/Bad inside the sequencer"),
]


def _domain_from_site(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0]


def _count_rows(path: Path) -> int:
    """Data rows (excluding header) in a CSV, or 0 if missing."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def _import_tokens(profile: str, content_root: Path | None) -> tuple[set[str], int, int]:
    """Org tokens across the bulk-pull batches in ``imports/``, plus row and file counts.

    Split out of :func:`_account_layer` because the *identified* headline is a
    union across every discovery path (see :func:`_lifecycle_funnel`), not just
    this one — both callers need the token set, and re-globbing per caller would
    read the same batches twice.
    """
    imports_dir = _prospects_dir(profile, content_root) / "imports"
    acct_tokens: set[str] = set()
    acct_rows = 0
    files = sorted(glob.glob(str(imports_dir / "*.csv")))
    for f in files:
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                acct_rows += 1
                dom = (row.get("business_domain") or "").strip() or _domain_from_site(
                    row.get("business_website", "")
                )
                tok = _org_token(dom, row.get("business_name", ""))
                if tok:
                    acct_tokens.add(tok)
    return acct_tokens, acct_rows, len(files)


def _account_layer(imports: tuple[set[str], int, int], master_tokens: set[str]) -> dict:
    """The bulk-pull backlog: unique companies in ``imports/`` and how many
    already have at least one person (with an email) in the master list.

    Scoped to ``imports/`` on purpose — this is the *bulk-pull* backlog, one of
    two discovery paths. The page's headline "identified" count is the union of
    both (``lifecycle["identified"]``); these keys stay narrow.
    """
    acct_tokens, acct_rows, files = imports
    enriched = acct_tokens & master_tokens
    return {
        "raw_rows": acct_rows,
        "files": files,
        "unique_accounts": len(acct_tokens),
        "enriched_accounts": len(enriched),
        "backlog_accounts": len(acct_tokens - master_tokens),
    }


def _latest_tokens(profile: str, content_root: Path | None) -> tuple[set[str], set[str], set[str]]:
    """Org tokens from ``latest.json`` — (all, ICP-scored, Tier-A).

    ``latest.json`` is the cumulative account state every prospect run merges
    into, and is the *only* record of a run that resolved people directly
    (``prospects-*-hubspot.csv``) without ever emitting an ``imports/`` batch.
    Reading it here is what stops such a run from showing as zero discovered.
    """
    all_tokens: set[str] = set()
    scored: set[str] = set()
    tier_a: set[str] = set()
    for item in load_latest(profile, content_root).get("items", []):
        tok = _org_token(item.get("domain", ""), item.get("company", ""))
        if not tok:
            continue
        all_tokens.add(tok)
        tier = (item.get("tier") or "").strip().upper()
        if tier or item.get("score") is not None:
            scored.add(tok)
        if tier == "A":
            tier_a.add(tok)
    return all_tokens, scored, tier_a


def _lifecycle_funnel(
    profile: str,
    content_root: Path | None,
    master: list[dict],
    master_tokens: set[str],
    import_tokens: set[str],
) -> dict:
    """Discovered -> Scored -> Enriched -> Dossier -> Drafted — the real sales-readiness
    funnel, distinct from Layer 2's *email-confidence* funnel (ready/needs-verification are
    about deliverability, not lifecycle stage). This is the pipeline's actual order: ICP
    scoring ranks the backlog *before* any enrichment spend (see
    ``prospects_backlog.select_backlog`` — "score before you spend"), so an account is scored
    first and only a subset of scored accounts get enriched, not the other way around.

    Dossier/Drafted are counted over **Tier-A accounts only** — the one cohort the pipeline
    auto-generates either for (see ``prospects_consolidate.tier_a_needing_dossier``) — so the
    funnel keeps its narrowing property instead of mixing in unrelated manually-drafted
    Tier-B/C accounts.

    Every stage is a **union over all sources that record it**, never one file: a run that
    resolved people directly (no ``imports/`` batch) and a bulk pull that never got enriched
    are both real discovery, and counting only one path reported a stage that ran as zero.
    The union is also what keeps the funnel monotone — identified ⊇ qualified ⊇ enriched —
    which reading unrelated files per stage did not guarantee.
    """
    root = content_root or resolve_content_root()

    queue_tokens: set[str] = set()
    qpath = enrichment_queue_path(profile, content_root)
    if qpath.exists():
        with qpath.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                tok = _org_token(row.get("domain", ""), row.get("company", ""))
                if tok:
                    queue_tokens.add(tok)

    latest_all, latest_scored, latest_tier_a = _latest_tokens(profile, content_root)

    enriched = len(master_tokens)
    # The queue is post-scoring by construction ("score before you spend" —
    # prospects_backlog.select_backlog), and every master row carries a tier/score.
    scored = len(latest_scored | queue_tokens | master_tokens)
    identified = len(latest_all | queue_tokens | master_tokens | import_tokens)

    tier_a: dict[str, dict] = {}
    for r in master:
        if (r.get("tier") or "").strip().upper() != "A":
            continue
        tok = _org_token(r.get("company_domain", ""), r.get("company", ""))
        if tok and tok not in tier_a:
            tier_a[tok] = {"company": r.get("company", ""), "domain": r.get("company_domain", "")}
    # Tier-A accounts scored but not yet enriched exist only in latest.json — they are the
    # accounts still needing a brief, so omitting them understated exactly the open work.
    for item in load_latest(profile, content_root).get("items", []):
        tok = _org_token(item.get("domain", ""), item.get("company", ""))
        if tok in latest_tier_a and tok not in tier_a:
            tier_a[tok] = {"company": item.get("company", ""), "domain": item.get("domain", "")}

    dossier_tokens: set[str] = set()
    dossier_samples: list[dict] = []
    for tok, acct in tier_a.items():
        has, matched = _account_has_dossier(profile, acct["company"], acct["domain"], content_root)
        if has:
            dossier_tokens.add(tok)
            if len(dossier_samples) < 5:
                dossier_samples.append({"company": acct["company"], "folder": matched})

    outreach_rows = collect_rows(root, profile)
    drafted_raw_tokens = {_org_token("", row.account) for row in outreach_rows if row.account}
    drafted_tokens = set(tier_a) & drafted_raw_tokens

    prospects_dir_abs = _prospects_dir(profile, content_root)
    draft_samples: list[dict] = []
    seen_draft_accounts: set[str] = set()
    for row in outreach_rows:
        tok = _org_token("", row.account)
        if tok not in tier_a or tok in seen_draft_accounts:
            continue
        seen_draft_accounts.add(tok)
        abs_file = root.parent / row.file
        draft_samples.append(
            {
                "company": row.account,
                "href": os.path.relpath(abs_file, start=prospects_dir_abs),
                "repo_path": row.file,
            }
        )
        if len(draft_samples) >= 5:
            break

    return {
        "identified": identified,
        "scored": scored,
        "enriched": enriched,
        "tier_a": len(tier_a),
        "dossier": len(dossier_tokens),
        "drafted": len(drafted_tokens),
        "dossier_samples": dossier_samples,
        "draft_samples": draft_samples,
    }


def build_status(
    profile: str, content_root: Path | None = None, profiles_root: Path | None = None
) -> dict:
    """Assemble the full status model from files on disk. No MCP, no re-sweep."""
    ps = pool_status(profile, content_root)
    imports = _import_tokens(profile, content_root)
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    master_tokens = {_org_token(r.get("company_domain", ""), r.get("company", "")) for r in master}
    master_tokens.discard("")

    raw_files = sorted(
        glob.glob(str(_prospects_dir(profile, content_root) / "prospects-*-hubspot.csv"))
    )
    raw_rows = 0
    for f in raw_files:
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            raw_rows += max(0, sum(1 for _ in csv.reader(fh)) - 1)

    ready = _count_rows(ready_to_load_path(profile, content_root))
    needs = _count_rows(needs_verification_path(profile, content_root))
    blocked = ps["by_confidence_tier"].get("blocked", 0)
    master_total = ps["master_total"]
    excluded = max(0, master_total - ready - needs - blocked)  # DNC + already-sent

    # Copy-quality coverage — a DIFFERENT axis from the deliverability tiers above and
    # easy to conflate with them by name alone: this counts rows the `email-quality` skill's
    # judge has actually scored (message content), not rows whose address has been verified.
    # Surfaced even at zero so the gap is visible rather than silently absent.
    judged = sum(1 for r in master if (r.get("judge_verdict") or "").strip())
    judge_calibrated = sum(
        1 for r in master if (r.get("judge_calibrated") or "").strip().lower() == "true"
    )
    disagreements = sum(
        1
        for r in master
        if (r.get("verdict") or "").strip()
        and (r.get("judge_verdict") or "").strip()
        and r.get("verdict").strip().lower() != r.get("judge_verdict").strip().lower()
    )

    # The enrollment gate itself, on the SAME population and the SAME filter
    # `account_integrity --require-verdict send` uses — never `master-list.csv` (the
    # whole backlog, most of it not yet even judged) and never a hand-rolled verdict
    # filter that quietly drops the suppression check or the calibrated-judge rule.
    # Both this dashboard and the CLI now call `filter_by_verdict`, so they cannot
    # report two different counts for the same list again.
    ready_path = ready_to_load_path(profile, content_root)
    if ready_path.exists():
        with ready_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            ready_fieldnames = list(reader.fieldnames or [])
            ready_rows = list(reader)
        index = _load_suppression_index(suppression_ledger(profile, content_root))
        ready_rows = [
            r for r in ready_rows if not (r.get("suppression") or "").strip() and not index.match(r)
        ]
        gate_candidates, vstats = filter_by_verdict(ready_rows, "send", lane="signal")
        gate_audit = audit_rows(
            gate_candidates,
            profile,
            content_root,
            profiles_root,
            fieldnames=ready_fieldnames,
        )
        gate_kept = vstats.kept
        gate_errors = len(gate_audit.errors)
        gate_warnings = len(gate_audit.warnings)
        gate_ok = gate_kept > 0 and not gate_audit.failed
    else:
        gate_kept = gate_errors = gate_warnings = 0
        gate_ok = False

    sequences = _load_sequences(profile, content_root)

    prospects_dir_abs = _prospects_dir(profile, content_root)
    links = {
        "ready_csv": os.path.relpath(
            ready_to_load_path(profile, content_root), start=prospects_dir_abs
        ),
        "needs_csv": os.path.relpath(
            needs_verification_path(profile, content_root), start=prospects_dir_abs
        ),
        "accounts_dir": "../accounts/",
    }

    return {
        "profile": profile,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "links": links,
        "accounts": _account_layer(imports, master_tokens),
        "lifecycle": _lifecycle_funnel(profile, content_root, master, master_tokens, imports[0]),
        "funnel": {
            "raw_files": len(raw_files),
            "raw_rows": raw_rows,
            "master_total": master_total,
            "ready": ready,
            "needs_verification": needs,
            "blocked": blocked,
            "excluded_dnc_or_sent": excluded,
            "unconsolidated_in_raw": ps["unconsolidated_in_raw_exports"],
            "judged": judged,
            "judge_calibrated": judge_calibrated,
            "disagreements": disagreements,
            # `gate_candidates` is a COUNT, not a pass/fail claim — it is how many
            # ready-to-load.csv rows survived suppression + `verdict=send` before the
            # account-integrity audit ran against them. Whether that set actually
            # clears the gate is `gate_ok`; `account_integrity.AccountAudit.failed` is
            # a whole-batch boolean, so there is no per-row "passed" count to report.
            "gate_candidates": gate_kept,
            "gate_errors": gate_errors,
            "gate_warnings": gate_warnings,
            "gate_ok": gate_ok,
        },
        "sequences": sequences,
        "sequence": sequences[0] if sequences else None,  # back-compat
        "cost_model": COST_MODEL,
    }


def _int(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_seq(d: dict) -> dict:
    """Flatten a sequence's live stats into the curated set the page shows.

    Accepts EITHER a raw Saleshandy ``get_sequence_stats`` payload (``prospects``
    list + ``emails``) OR an already-flat dict the skill assembled — so the skill
    layer can drop the MCP response verbatim without reshaping it.
    """
    if "prospects" in d and isinstance(d.get("prospects"), list):
        p = (d["prospects"] or [{}])[0]
        est = (d.get("emails") or {}).get("status", {})
        bounced = _int(est.get("bounced")) or (
            _int(est.get("hardBounced"))
            + _int(est.get("softBounced"))
            + _int(est.get("blockBounced"))
        )
        return {
            "id": d.get("sequenceId", ""),
            "name": d.get("sequenceName", ""),
            "status": d.get("status", ""),
            "loaded": _int(p.get("total")),
            "sent": _int(p.get("contacted")),
            "pending": _int(p.get("upcoming")) + _int(p.get("waiting")),
            "delivered": _int(est.get("delivered")),
            "opened": _int(p.get("open")) or _int(est.get("opened")),
            "replied": _int(p.get("replied")) or _int(est.get("replied")),
            "bounced": bounced or _int(p.get("bounced")),
            "interested": _int(p.get("interested")),
            "meetings": _int(p.get("meetingBooked")),
            "deal_value": _int(p.get("meetingBookedDealValue"))
            + _int(p.get("interestedDealValue")),
        }
    # already flat
    keys = (
        "id",
        "name",
        "status",
        "loaded",
        "sent",
        "pending",
        "delivered",
        "opened",
        "replied",
        "bounced",
        "interested",
        "meetings",
        "deal_value",
    )
    return {k: (d.get(k) if k in ("id", "name", "status") else _int(d.get(k))) for k in keys}


def _load_sequences(profile: str, content_root: Path | None) -> list[dict]:
    """Live sequencer stats, dropped by the skill/agent layer (MCP-free module).

    Prefers ``sequence-stats.json`` (a list of raw or flat sequence stats); falls
    back to the older single-sequence ``sequence-state.json``. Absent → empty list
    (the page renders fine without a sequencer section)."""
    pool = _pool_dir(profile, content_root)
    stats_file = pool / "sequence-stats.json"
    if stats_file.exists():
        try:
            raw = json.loads(stats_file.read_text(encoding="utf-8"))
            items = raw.get("sequences", raw) if isinstance(raw, dict) else raw
            return [_normalize_seq(s) for s in items if isinstance(s, dict)]
        except (json.JSONDecodeError, OSError):
            pass
    state_file = pool / "sequence-state.json"
    if state_file.exists():
        try:
            d = json.loads(state_file.read_text(encoding="utf-8"))
            d.setdefault("loaded", d.get("loaded", 0))
            return [_normalize_seq(d)]
        except (json.JSONDecodeError, OSError):
            pass
    return []
