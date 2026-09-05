"""Deterministic backlog → enrichment queue selection (the missing first half of the loop).

Resolving a named decision-maker + verified email for a backlog account has two halves.
The second half — the paid MCP person-resolution — always had a home. The FIRST half —
*which* accounts to work, in *what* order — never did: score by ICP fit, drop anyone already
resolved, keep only emailable jurisdictions. That judgment was re-done by hand every pass, so a
~900-account backlog turned into weeks of 300-at-a-time passes that each re-scored the whole set
from scratch and re-derived the same exclusions.

This module makes that half deterministic and one-shot. It reads the account universe (the
Vibe/Explorium ``imports/*.csv`` firmographic pulls), removes every account already in the
consolidated master list, gates to the profile's ``target_markets``, scores each against the
profile's ICP rubric (``knowledge/icp-scoring.toml``), and writes ONE ranked
``enrichment-queue.csv``. The agent/MCP half then just walks that queue in provider-native
batches (:func:`batches`) — Explorium's bulk enrichment takes 50 ids/request, so a whole pass is
a handful of ``fetch → enrich → export`` calls, not a hundred 5-company micro-batches.

Plumbing vs judgment, same split as the rest of gtm_core: stdlib-only, MCP-free, and holds NO
tenant facts — the cohort weights are read from the profile (CLAUDE.md tenant boundary). Re-running
is idempotent and free: an account resolved by a prior pass disappears from the next queue on its
own, so "work the misses again through a second provider" (waterfall) needs no extra state — it is
just the next :func:`select_backlog`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tomllib
from pathlib import Path

from gtm_core.email_compliance import normalize_market, read_target_markets
from gtm_core.paths import resolve_content_root, resolve_profiles_root
from gtm_core.prospects_consolidate import MarketGate, _pool_dir
from gtm_core.prospects_consolidate import org_token as _org_token
from gtm_core.prospects_import import (
    _get as _import_get,
)
from gtm_core.prospects_import import (
    _parse_intent_topics,
    _segment_from_size,
    _title_market,
)

# --- schema -----------------------------------------------------------------

#: Queue columns. ``icp_backlog_score`` is deliberately NOT called ``score``.
#:
#: ``prospects_consolidate.columns`` maps an incoming column named ``score`` onto ``GTM_Score``,
#: which is the per-account QUALIFICATION verdict from ``icp-personas.md`` (a 0-12 rubric). This
#: number is a different thing on a different scale: a weighted SPEND ranking (cohort + segment +
#: intent + geo, observed 0-53) that says which account should draw the next enrichment credit.
#: While both were called ``score``, feeding a queue CSV through the consolidate/import path
#: silently rewrote one as the other — 749 rows across the 2026-07-24 and 2026-08-11 bulk runs
#: landed in published HubSpot CSVs carrying a spend ranking in the qualification column, and
#: one of those runs invented a ``Tier C`` to describe the bottom of a distribution the A/B split
#: was never shaped for. Nothing failed: the CSV schema declares ``GTM_Score`` as "numeric, no
#: denominator", so a 53 was as acceptable as a 9.
#:
#: Renamed 2026-09-04. The two scales stay distinguishable in the historical rows via
#: ``GTM_Rubric_Version`` (``2026-07-24`` = this ranker; ``2026-05-15``/blank = the rubric).
QUEUE_COLS = [
    "business_id",
    "company",
    "domain",
    "country",
    "segment",
    "industry",
    "employees_range",
    "revenue_range",
    "top_intent_score",
    "intent_topics",
    "cohort",
    "icp_backlog_score",
]


# --- rubric (profile-owned ICP weights) -------------------------------------


def rubric_path(profile: str, profiles_root: Path | None = None) -> Path:
    root = profiles_root or resolve_profiles_root()
    return root / profile / "knowledge" / "icp-scoring.toml"


def load_rubric(profile: str, profiles_root: Path | None = None) -> dict:
    """Read the profile's ICP scoring rubric.

    A missing rubric is a hard error, not a silent neutral default — an unscored selection would
    spend enrichment credits in arbitrary order, the exact failure this module exists to prevent
    (mirrors :func:`read_target_markets`, which also fails closed on a missing profile fact).
    """
    path = rubric_path(profile, profiles_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"no ICP scoring rubric for profile {profile!r} at {path}. Selection needs cohort "
            "weights — add knowledge/icp-scoring.toml (see an existing tenant profile for the shape)."
        )
    rubric = tomllib.loads(path.read_text(encoding="utf-8"))
    # Normalize keyword casing; patterns compile lazily in _cohort_pattern().
    for cohort in rubric.get("cohort", []):
        cohort["keywords"] = [k.lower() for k in cohort.get("keywords", [])]
    rubric.setdefault("segment", {})
    rubric.setdefault("intent", {})
    rubric["geo_bonus"] = {normalize_market(k): v for k, v in rubric.get("geo_bonus", {}).items()}
    return rubric


# --- account universe -------------------------------------------------------


def _business_id(row: dict) -> str:
    for key in ("business_id", "id"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return ""


def _first_int(s: str) -> int:
    """First number in a band string — the range's lower bound. ``"500-999" -> 500`` (NOT 500999),
    ``"10001+" -> 10001``. Matches :func:`gtm_core.prospects_import._segment_from_size`."""
    m = re.search(r"\d+", (s or "").replace(",", ""))
    return int(m.group()) if m else 0


def _imports_dir(profile: str, content_root: Path | None) -> Path:
    root = content_root or resolve_content_root()
    return root / profile / "prospects" / "imports"


def load_resolved_org_tokens(profile: str, content_root: Path | None = None) -> set[str]:
    """Org tokens for every account we already hold a contact for (any row in the master list).

    Keyed by :func:`_org_token` (domain-first, so ``vertex.example`` and bare ``"Vertex"`` collapse)
    — the same identity key consolidation uses — so an account resolved under a slightly different
    company string is still recognised as done and never re-enriched.
    """
    master = _pool_dir(profile, content_root) / "master-list.csv"
    resolved: set[str] = set()
    if not master.exists():
        return resolved
    with master.open(newline="", encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            tok = _org_token(row.get("company_domain", ""), row.get("company", ""))
            if tok:
                resolved.add(tok)
    return resolved


def load_backlog_accounts(profile: str, content_root: Path | None = None) -> list[dict]:
    """Every unique account across ``imports/*.csv`` that carries a ``business_id``.

    Deduped twice: by ``business_id`` (the same account pulled into more than one import file) and
    by org token (two ids at the same root domain — e.g. a conglomerate's sub-brands). On a
    collision the row with the stronger intent signal wins, so we keep the richest copy.
    """
    by_id: dict[str, dict] = {}
    for path in sorted(_imports_dir(profile, content_root).glob("*.csv")):
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "business_id" not in reader.fieldnames:
                continue
            for row in reader:
                bid = _business_id(row)
                company = _import_get(row, "company")
                if not bid or not company:
                    continue
                topics = _parse_intent_topics(_import_get(row, "intent"))
                top = max((t.get("score", 0) for t in topics), default=0)
                rec = {
                    "business_id": bid,
                    "company": company,
                    "domain": _import_get(row, "domain"),
                    "country": _import_get(row, "country"),
                    "industry": _import_get(row, "industry"),
                    "description": _import_get(row, "description"),
                    "employees_range": _import_get(row, "employees_range"),
                    "revenue_range": _import_get(row, "revenue_range"),
                    "segment": _segment_from_size(_import_get(row, "employees_range")),
                    "intent_topics": topics,
                    "top_intent_score": top,
                    "org_token": _org_token(_import_get(row, "domain"), company),
                }
                prev = by_id.get(bid)
                if prev is None or top > prev["top_intent_score"]:
                    by_id[bid] = rec

    by_org: dict[str, dict] = {}
    loose: list[dict] = []
    for rec in by_id.values():
        tok = rec["org_token"]
        if not tok:
            loose.append(rec)  # can't identify an org — never collapse, keep as-is
            continue
        prev = by_org.get(tok)
        if prev is None or rec["top_intent_score"] > prev["top_intent_score"]:
            by_org[tok] = rec
    return list(by_org.values()) + loose


# --- scoring ----------------------------------------------------------------


def _cohort_pattern(cohort: dict, key: str) -> re.Pattern | None:
    """Word-boundary regex for a cohort's keyword list, compiled once and cached on the dict.

    `\\b<kw>\\w*` keeps deliberately short stems working (financ -> financial/financing,
    insur -> insurer/insurance) while refusing mid-word matches. Compiled lazily so a rubric
    built inline (tests, callers that skip load_rubric) behaves identically to a loaded one.
    """
    cache_key = f"_pattern__{key}"
    if cache_key not in cohort:
        keywords = [k.lower() for k in cohort.get(key, []) or []]
        cohort[cache_key] = (
            re.compile("|".join(rf"\b{re.escape(k)}\w*" for k in keywords)) if keywords else None
        )
    return cohort[cache_key]


def score_account(rec: dict, rubric: dict) -> tuple[int, str]:
    """Additive ICP score + the cohort label that fired. Deterministic: the same account and rubric
    always yield the same number, so ordering never depends on which pass looked at it."""
    # Match cohort stems against the INDUSTRY field only (NAICS-derived, reliable).
    # The free-text description is not a cohort signal: on 2026-08-11 it put 227/500 accounts
    # into capital-markets because ordinary prose says "financial year" and "we financed our
    # growth" — a senior-care provider drew the syndicated-lending case study that way. A cohort
    # that genuinely needs description evidence declares `description_keywords` (distinctive
    # multi-word phrases), which is matched separately and deliberately.
    industry = str(rec.get("industry", "")).lower()
    description = str(rec.get("description", "")).lower()
    score = 0
    cohort = ""
    for c in rubric.get("cohort", []):
        pattern = _cohort_pattern(c, "keywords")
        desc_pattern = _cohort_pattern(c, "description_keywords")
        if (pattern is not None and pattern.search(industry)) or (
            desc_pattern is not None and desc_pattern.search(description)
        ):
            score += int(c.get("weight", 0))
            cohort = c.get("name", "")
            break

    seg = rubric.get("segment", {})
    employees = _first_int(rec.get("employees_range", ""))
    if employees >= int(seg.get("enterprise_floor", 1_000_000_000)):
        score += int(seg.get("enterprise_bonus", 0))
    elif employees >= int(seg.get("midmarket_floor", 1_000_000_000)):
        score += int(seg.get("midmarket_bonus", 0))

    intent = rubric.get("intent", {})
    top = rec.get("top_intent_score", 0)
    if top >= int(intent.get("high_score", 10_000)):
        score += int(intent.get("high_bonus", 0))
    elif top >= int(intent.get("elevated_score", 10_000)):
        score += int(intent.get("elevated_bonus", 0))

    score += int(rubric["geo_bonus"].get(normalize_market(rec.get("country", "")), 0))
    return score, cohort


def _format_intent_topics(topics: list[dict]) -> str:
    ranked = sorted(topics or [], key=lambda t: t.get("score", 0), reverse=True)
    return ";".join(f"{t.get('topic', '')}:{t.get('score', 0)}" for t in ranked if t.get("topic"))


# --- selection --------------------------------------------------------------


def enrichment_queue_path(profile: str, content_root: Path | None = None) -> Path:
    """The one ranked queue the enrichment step reads. Hidden in ``.pool/`` with the other
    plumbing files — it is machine input, not a human-facing list."""
    return _pool_dir(profile, content_root) / "enrichment-queue.csv"


def select_backlog(
    profile: str,
    *,
    limit: int | None = None,
    include_unknown_country: bool = False,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> dict:
    """Rank the not-yet-enriched, in-market backlog and write ``enrichment-queue.csv``.

    ``include_unknown_country=False`` (default) drops accounts with a blank country from the queue:
    an un-targetable account is wasted enrichment spend. The count is always reported so the gap
    stays visible; pass ``True`` to include them (they still face the market gate at consolidation).
    """
    rubric = load_rubric(profile, profiles_root)
    markets = read_target_markets(profile, profiles_root)
    gate = MarketGate(markets, strict=not include_unknown_country)
    resolved = load_resolved_org_tokens(profile, content_root)
    accounts = load_backlog_accounts(profile, content_root)

    already_resolved = out_of_market = unknown_country = 0
    queue: list[dict] = []
    for rec in accounts:
        if rec["org_token"] in resolved:
            already_resolved += 1
            continue
        country = rec.get("country", "")
        if not country.strip():
            unknown_country += 1
        if gate.blocks(country):
            out_of_market += 1
            continue
        score, cohort = score_account(rec, rubric)
        queue.append(
            {
                "business_id": rec["business_id"],
                "company": rec["company"],
                "domain": rec["domain"],
                "country": _title_market(country),
                "segment": rec["segment"],
                "industry": rec["industry"],
                "employees_range": rec["employees_range"],
                "revenue_range": rec["revenue_range"],
                "top_intent_score": rec["top_intent_score"],
                "intent_topics": _format_intent_topics(rec["intent_topics"]),
                "cohort": cohort,
                "icp_backlog_score": score,
            }
        )

    # Highest ICP score first; break ties on live intent, then company name for a stable order.
    queue.sort(
        key=lambda r: (-r["icp_backlog_score"], -r["top_intent_score"], r["company"].lower())
    )
    total_selected = len(queue)
    if limit is not None:
        queue = queue[:limit]

    out_path = enrichment_queue_path(profile, content_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_COLS)
        w.writeheader()
        w.writerows(queue)

    by_cohort: dict[str, int] = {}
    for r in queue:
        by_cohort[r["cohort"] or "(none)"] = by_cohort.get(r["cohort"] or "(none)", 0) + 1

    result = {
        "profile": profile,
        "backlog_accounts": len(accounts),
        "already_resolved_skipped": already_resolved,
        "out_of_market_skipped": out_of_market,
        "unknown_country": unknown_country,
        "queued": len(queue),
        "total_in_market_unresolved": total_selected,
        "by_cohort": dict(sorted(by_cohort.items(), key=lambda kv: -kv[1])),
        "market_gate": ", ".join(markets),
        "rubric_version": rubric.get("rubric_version", ""),
        "queue_path": str(out_path),
        "top_preview": [
            {
                "company": r["company"],
                "cohort": r["cohort"],
                "icp_backlog_score": r["icp_backlog_score"],
                "country": r["country"],
            }
            for r in queue[:15]
        ],
    }
    _print_banner(result)
    return result


def _print_banner(result: dict) -> None:
    print(
        f"queue[{result['profile']}]: {result['queued']} to enrich · "
        f"{result['already_resolved_skipped']} already-resolved skipped · "
        f"{result['out_of_market_skipped']} out-of-market skipped "
        f"({result['unknown_country']} unknown-country) · gate: {result['market_gate']}",
        file=sys.stderr,
    )


def batches(
    profile: str,
    *,
    batch_size: int = 50,
    limit: int | None = None,
    content_root: Path | None = None,
) -> list[list[str]]:
    """The queue's ``business_id``s chunked for provider-native bulk enrichment (Explorium bulk
    takes 50/request). ``limit`` caps how many ids are drawn from the top of the queue before
    chunking — use it to size a pass to the per-run budget."""
    path = enrichment_queue_path(profile, content_root)
    if not path.exists():
        raise FileNotFoundError(f"no enrichment queue at {path} — run `select` first")
    with path.open(newline="", encoding="utf-8") as f:
        ids = [row["business_id"] for row in csv.DictReader(f) if row.get("business_id")]
    if limit is not None:
        ids = ids[:limit]
    return [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]


# --- CLI --------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_backlog")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser(
        "select", help="rank the in-market, unresolved backlog into enrichment-queue.csv"
    )
    s.add_argument("--profile", required=True)
    s.add_argument("--limit", type=int, default=None, help="cap the queue to the top N accounts")
    s.add_argument(
        "--include-unknown-country",
        action="store_true",
        help="keep accounts with a blank country (default: drop them as un-targetable spend)",
    )

    b = sub.add_parser(
        "batches", help="emit queued business_ids as JSON batches for bulk enrichment"
    )
    b.add_argument("--profile", required=True)
    b.add_argument("--batch-size", type=int, default=50)
    b.add_argument(
        "--limit", type=int, default=None, help="draw only the top N ids before chunking"
    )

    args = ap.parse_args(argv)
    if args.cmd == "select":
        result = select_backlog(
            args.profile,
            limit=args.limit,
            include_unknown_country=args.include_unknown_country,
        )
    else:
        result = {"batches": batches(args.profile, batch_size=args.batch_size, limit=args.limit)}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
