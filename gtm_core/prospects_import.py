"""Ingest a Vibe/Explorium ``export-to-csv`` file into the prospect pipeline.

This is the "extractor" half of bulk-mode discovery. The Vibe MCP caps inline
rows at 5, so a large qualified set is materialized by the operator via
``export-to-csv`` → a CSV on disk; this module turns that CSV into normalized
candidate records the skill can score, and (once scored) merges them into
``latest.json`` and emits the run's ``.md`` / HubSpot ``.csv``.

Division of labour:
  * **This module = plumbing** — parse, derive heat from the inline Bombora
    intent scores, dedup vs the net-new exclude set, log the export's credit
    cost, and (on ``finalize``) merge + emit files.
  * **The skill/agent = judgment** — gates + rubric score + tier + why-now.
    ``ingest`` produces *candidates*; ``finalize`` consumes *scored* items.

stdlib-only, matching the rest of gtm_core. Reuses the safe, merge-only writer
in :mod:`gtm_core.prospects_state` for the ``latest.json`` write.
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import re
import sys
from pathlib import Path
from typing import Any

from gtm_core.ledgers import Ledgers
from gtm_core.paths import (
    PathConfig,
    _safe_segment,
    clean_env_var,
    resolve_content_root,
    resolve_profiles_root,
)
from gtm_core.prospects_export import RunExport
from gtm_core.prospects_item import (  # noqa: F401 — re-exported: the item rules live there
    CANONICAL_FIELDS,
    build_standard_item,
    build_standard_items,
    is_refusal,
    new_account_defaults,
    normalise_items,
)
from gtm_core.prospects_merge import is_blank
from gtm_core.prospects_state import _identity_key, _norm, upsert_latest
from gtm_core.slugify import slug as _slug

#: Default **ON**: a scored row must name the rubric it was scored against. Off exists only to
#: widen a migration window — it does not disable scoring, just this refusal. Parsed the same way
#: as the engine's own switch: only a recognised false value opens the gate, because this one
#: guards a refusal and an unrecognised value must never land on the permissive side.
STRICT_PROVENANCE_ENV = "GTM_SCORECARD_STRICT_PROVENANCE"
_PROVENANCE_FIELDS = ("rubric_source", "rubric_version")

# --- constants (from the Phase 0 spike, 2026-07-19) ---
EXPORT_CREDITS_PER_ROW = 2  # observed: 200 rows -> 400 credits on export-to-csv
CREDIT_USD = 0.02  # approx; a ~$160 pack ≈ 8–9k credits. Non-authoritative — the
#                    per_run_cap_usd $-gate before export is the real control.
HEAT_HIGH_INTENT_SCORE = 75  # >=75 on a Bombora topic = high intent (+2), per gates-and-scoring.md
HEAT_ELEVATED_SCORE = 60  # 60–74 is elevated but earns no heat points

# Canonical field -> the header variants seen across Vibe exports / hubspot CSVs.
_FIELD_ALIASES = {
    "company": ("business_name", "name", "company", "company name", "company_name"),
    "domain": ("business_domain", "business_website", "domain", "company domain name", "website"),
    "country": ("business_country_name", "country/region", "country", "market"),
    "city": ("business_city_name", "city"),
    "region": ("business_region", "region", "state"),
    "employees_range": (
        "business_number_of_employees_range",
        "number of employees",
        "employees",
        "company_size",
    ),
    "revenue_range": ("business_yearly_revenue_range", "revenue", "company_revenue"),
    "industry": ("business_naics_description", "business_linkedin_category", "industry"),
    "description": ("business_business_description", "description"),
    "intent": ("business_business_intent_topics", "intent_topics", "business_intent_topics"),
}


def _get(row: dict, field: str) -> str:
    """Case-insensitive lookup of a canonical field across its header aliases."""
    lower = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
    for alias in _FIELD_ALIASES[field]:
        v = lower.get(alias, "")
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _parse_intent_topics(raw: str) -> list[dict]:
    """Parse the inline ``business_business_intent_topics`` JSON string.

    Returns a list of ``{"topic": str, "score": int}``; tolerant of empty / ``[]``
    / malformed values (returns ``[]``).
    """
    if not raw or not raw.strip() or raw.strip() in ("[]", "null", "None"):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and "topic" in item:
            try:
                score = int(item.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            out.append({"topic": str(item["topic"]), "score": score})
    return out


def derive_heat(topics: list[dict]) -> tuple[int, list[str], int]:
    """Heat from inline Bombora scores. Single live feed this path (Vibe only), so
    the max heat is +2 (no double-intent bonus without a second feed).

    Returns ``(heat, intent_feeds, top_score)``.
    """
    top = max((t.get("score", 0) for t in topics), default=0)
    if top >= HEAT_HIGH_INTENT_SCORE:
        return 2, ["vibe-topic"], top
    return 0, [], top


def _segment_from_size(employees_range: str) -> str:
    """Coarse enterprise/startup split from the employee-count band (>=1000 =
    enterprise). A heuristic the agent may override during scoring.
    """
    if not employees_range:
        return "unknown"
    m = re.search(r"\d+", employees_range.replace(",", ""))
    if not m:
        return "unknown"
    return "enterprise" if int(m.group()) >= 1000 else "startup"


def _title_market(country: str) -> str:
    c = country.strip()
    if not c:
        return ""
    # Title-case handles "united states" -> "United States", "united arab emirates" -> "United Arab Emirates"
    return " ".join(w.capitalize() for w in c.split())


def parse_vibe_export(csv_path: str | Path, *, source: str = "vibe-export") -> list[dict]:
    """Parse a Vibe export CSV into normalized candidate records.

    In-file dedup by :func:`_identity_key` (domain-first, so two distinct
    companies with similar names are never collapsed, and a non-ASCII name is
    never dropped); on a key collision the richest-intent row wins. Each candidate
    carries firmographics + parsed intent + derived heat, but NOT
    tier/score/qualification_path — those are the skill's judgment.
    """
    path = Path(csv_path)
    seen: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            company = _get(row, "company")
            if not company:
                continue
            topics = _parse_intent_topics(_get(row, "intent"))
            heat, feeds, top_score = derive_heat(topics)
            cand = {
                "id": _slug(company),
                "company": company,
                "domain": _get(row, "domain"),
                "market": _title_market(_get(row, "country")),
                "segment": _segment_from_size(_get(row, "employees_range")),
                "city": _get(row, "city"),
                "region": _get(row, "region"),
                "employees_range": _get(row, "employees_range"),
                "revenue_range": _get(row, "revenue_range"),
                "industry": _get(row, "industry"),
                "intent_topics": topics,
                "heat": heat,
                "intent_feeds": feeds,
                "top_intent_score": top_score,
                "status": "new",
                "source": source,
            }
            key = _identity_key(cand)
            if key in seen:
                # keep whichever row has the stronger intent signal
                if top_score <= seen[key]["top_intent_score"]:
                    continue
            seen[key] = cand
    return list(seen.values())


def count_export_rows(csv_path: str | Path) -> int:
    """Count the data rows in the export CSV — what Vibe actually *bills* (per
    exported row), before any in-file dedup. Used for honest cost logging; a CSV
    with duplicate/dropped rows would otherwise under-report spend.
    """
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.DictReader(f))


def dedupe_against_exclude(
    candidates: list[dict], exclude_names: set[str]
) -> tuple[list[dict], list[str]]:
    """Split candidates into (kept, dropped-company-names) using the net-new
    exclude set (normalized-name match)."""
    exclude_norm = {_norm(n) for n in exclude_names}
    kept, dropped = [], []
    for c in candidates:
        if _norm(c["company"]) in exclude_norm:
            dropped.append(c["company"])
        else:
            kept.append(c)
    return kept, dropped


def _ledgers(profile: str, content_root: Path | None) -> Ledgers:
    root = content_root or resolve_content_root()
    cfg = PathConfig(
        content_root=root, profiles_root=resolve_profiles_root(), default_profile=profile
    )
    return Ledgers(cfg, profile)


def log_vibe_export_cost(
    profile: str, n_rows: int, source_run: str, content_root: Path | None = None
) -> dict:
    """Record the export's credit cost to costs.jsonl (Vibe is not self-metering).

    The record is honest about being an estimate — the authoritative control is the
    ``estimate-cost`` + ``per_run_cap_usd`` gate BEFORE the export happens.
    """
    credits = n_rows * EXPORT_CREDITS_PER_ROW
    record = {
        "tool": "vibe-prospecting",
        "skill": "prospect",
        "cost_usd": round(credits * CREDIT_USD, 2),
        "units": {"credits": credits, "exported_rows": n_rows},
        "run_id": source_run,
        "note": f"bulk export ingest: {n_rows} rows × {EXPORT_CREDITS_PER_ROW} cr (estimate; ~${CREDIT_USD}/cr)",
    }
    _ledgers(profile, content_root).append_cost(record)
    return record


def ingest(
    csv_path: str | Path,
    profile: str,
    source_run: str,
    exclude_path: str | Path | None = None,
    content_root: Path | None = None,
    source: str = "vibe-export",
) -> dict:
    """Parse an account CSV → dedup vs exclude → write candidates JSON → log cost.

    Produces ``content/<profile>/prospects/imports/candidates-<source_run>.json``
    for the skill to score. Returns a summary dict.

    ``source`` stamps provenance AND gates billing, on purpose: only the metered
    default ``"vibe-export"`` logs cost here (the one place Vibe spend reaches
    ``costs.jsonl``). Any other value is an unbought list — a curated sheet, a partner
    list — where billing ``rows × EXPORT_CREDITS_PER_ROW`` would charge fabricated
    spend against the §R2 cap and block real paid calls. A row claiming a metered
    origin is a row that was billed for one. Flow: discovery-and-budget.md §List mode.
    """
    # Both become path segments below; neither may name a path (tenant boundary).
    _safe_segment(profile, "profile")
    _safe_segment(source_run, "source_run")
    root = content_root or resolve_content_root()
    candidates = parse_vibe_export(csv_path, source=source)
    parsed_n = len(candidates)
    billed_rows = count_export_rows(csv_path)  # Vibe bills per raw row, not per deduped candidate

    exclude_names: set[str] = set()
    if exclude_path:
        data = json.loads(Path(exclude_path).read_text(encoding="utf-8"))
        exclude_names = set(data.get("companies", data) if isinstance(data, dict) else data)
    kept, dropped = dedupe_against_exclude(candidates, exclude_names)

    heated = sum(1 for c in kept if c["heat"] >= 2)
    out_dir = root / profile / "prospects" / "imports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"candidates-{source_run}.json"
    out_path.write_text(
        json.dumps(
            {"source_run": source_run, "profile": profile, "candidates": kept},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metered = source == "vibe-export"  # only the metered source bills; see the docstring
    cost_usd = 0.0
    if metered:
        cost_usd = log_vibe_export_cost(profile, billed_rows, source_run, content_root=root)[
            "cost_usd"
        ]
    return {
        "profile": profile,
        "source_run": source_run,
        "source": source,
        "parsed": parsed_n,
        "billed_rows": billed_rows if metered else 0,
        "excluded": len(dropped),
        "kept": len(kept),
        "high_heat": heated,
        "candidates_file": str(out_path),
        "cost_usd": cost_usd,
    }


def strict_provenance() -> bool:
    """Whether :func:`require_rubric_provenance` refuses. Default on; only a recognised false
    value opens it, so a typo in the env var can never quietly disable the gate."""
    from gtm_core.scorecard.cli import _DISABLING

    # `clean_env_var`, not `os.getenv`: it strips surrounding quotes and nulls `$PLACEHOLDER`
    # values, so a Doppler/.env value arriving as `"false"` (quotes included) means the same
    # thing to both switches. The comment above used to claim they parsed identically; they
    # did not, and the sibling would have been disabled while this one stayed strict.
    return (clean_env_var(STRICT_PROVENANCE_ENV) or "").strip().lower() not in _DISABLING


def require_rubric_provenance(raw_items: Any) -> None:
    """A row carrying a SCORE must name the rubric that produced it. Refuses before any write.

    A score is a claim, and until 2026-09-22 nothing recorded what it was a claim *against*. A
    1003-row pass scored every account on a rubric assembled from plan prose while the tenant's
    maintained one sat unread; the numbers merged into ``latest.json`` looking exactly like
    numbers from the real rubric, and nothing downstream could tell them apart — not review, not
    the dashboard, and not ``outcomes-sync``, which is why a rubric change still cannot be
    attributed to a reply-rate change.

    Unscored rows are not asked for provenance: they make no claim.
    """
    if not strict_provenance() or not isinstance(raw_items, list):
        return  # a non-list is normalise_items' error to name, in its own words
    gaps: list[str] = []
    for row, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict) or is_blank(item.get("score")):
            continue
        missing = [f for f in _PROVENANCE_FIELDS if is_blank(item.get(f))]
        if missing:
            who = item.get("company") or item.get("id") or "?"
            gaps.append(f"row {row} ({who}): missing {', '.join(missing)}")
    if gaps:
        raise ValueError(
            f"{len(gaps)} scored item(s) do not name the rubric they were scored against — "
            + "; ".join(gaps[:5])
            + (f"; and {len(gaps) - 5} more" if len(gaps) > 5 else "")
            + ". Put `rubric_source` and `rubric_version` on every scored item (the values "
            "`python -m gtm_core.scorecard score` already returns on each row, or the file and "
            "version of the rubric you applied by hand). Nothing was written."
        )


def finalize(
    profile: str,
    scored_items: list[dict],
    source_run: str,
    content_root: Path | None = None,
    *,
    run_date: str | None = None,
    rubric_version: str | None = None,
    standard: bool = False,
) -> dict:
    """Merge already-scored items into latest.json (snapshot-safe, merge-only) and
    emit the run's HubSpot CSV. ``scored_items`` come from the skill's scoring pass.

    Every item — minimal or full — is read through :func:`normalise_items` first, so
    ``standard`` no longer selects anything and is kept only so existing invocations
    parse. A bad item raises before anything is written. An account that already exists
    is refreshed only by what the item actually supplies; the canonical defaults reach
    new accounts alone (:func:`new_account_defaults`).

    The CSV is one row per item (= per contact), so two personas at one company both
    reach it while sharing one ledger account. A refusal (``tier``/``verdict`` drop) is
    recorded in the ledger and counted under ``refused``, but never written to the CSV:
    that file is pooled into the list a human loads into a sequencer. Each row carries the
    ``company``/``domain`` of the ledger account its item MERGED INTO, and an item whose
    account is retired, replied or dropped is counted under ``excluded_retired`` and named
    on stderr instead of exported (:mod:`gtm_core.prospects_export`).

    The CSV follows the exact column contract in
    ``references/hubspot-csv-map.md`` (the doc standard mode's Step 7 already writes
    to by hand) — same header names/order/custom-property set, so bulk-mode and standard-mode
    outputs are drop-in HubSpot imports identical in shape.
    """
    # ``source_run`` names the CSV below: unguarded, a run id could carry the PII export
    # out of the tenant folder. ``profile`` is guarded by the ledger path it resolves.
    _safe_segment(source_run, "source_run")
    require_rubric_provenance(scored_items)
    items = normalise_items(scored_items)

    render = functools.partial(_hubspot_row, run_date=run_date, rubric_version=rubric_version)
    export = RunExport(items, _HUBSPOT_COLUMNS, render)
    summary = upsert_latest(
        profile,
        items,
        source_run,
        content_root=content_root,
        new_account_defaults=new_account_defaults,
        on_merged=export.plan,  # rows are built — or refused — before the ledger is written
    )
    root = content_root or resolve_content_root()
    csv_path = root / profile / "prospects" / f"prospects-{source_run}-hubspot.csv"
    export.write(csv_path)
    for company, why in export.excluded:
        print(f"NOT EXPORTED {company}: the account's {why} in latest.json", file=sys.stderr)
    summary["refused"] = export.refused
    summary["excluded_retired"] = len(export.excluded)
    summary["hubspot_csv"] = str(csv_path)
    return summary


# Column order/names are the contract in references/hubspot-csv-map.md — standard columns
# (HubSpot's built-in contact properties) first, then the GTM_* custom properties.
_HUBSPOT_COLUMNS = [
    "First Name",
    "Last Name",
    "Email",
    "Email Status",
    "Phone Number",
    "Job Title",
    "LinkedIn Bio URL",
    "Company Name",
    "Company Domain Name",
    "City",
    "Country/Region",
    "Number of Employees",
    "GTM_Segment",
    "GTM_Score",
    "GTM_Tier",
    "GTM_Persona_Tier",
    "GTM_Why_Now",
    "GTM_Case_Study",
    "GTM_Source",
    "GTM_Run_Date",
    "GTM_Rubric_Version",
    "GTM_Heat",
    "GTM_Intent_Feeds",
    "GTM_Top_Intent_Score",
    "GTM_Intent_Topics",
    "GTM_Industry",
    "GTM_Revenue_Range",
    "GTM_New_In_Role",
    "GTM_Qualification_Path",
    "conf",
    "GTM_Signal_Source_URL",
    "GTM_Signal_Observed",
    "GTM_Signal_Evidence",
    "GTM_Signal_Subject",
    "GTM_Signal_Agent_Kind",
    "GTM_Category_Relation",
    "GTM_Verdict",
    "GTM_Verdict_Reason",
    "GTM_Signal_Column",
]


def _split_name(full_name: str) -> tuple[str, str]:
    """Best-effort First/Last split (HubSpot wants them as separate columns)."""
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _employees_midpoint(employees_range: str) -> str:
    """``"5001-10000"`` -> ``"7500"``; ``"10001+"`` -> ``"10001"``; blank if unparseable."""
    nums = [int(n) for n in re.findall(r"\d+", (employees_range or "").replace(",", ""))]
    if not nums:
        return ""
    return str(nums[0] if "+" in employees_range else sum(nums[:2]) // len(nums[:2]))


def _format_intent_topics(topics: list[dict]) -> str:
    """``[{"topic": "agentic ai", "score": 86}, ...]`` -> ``"agentic ai:86;mlops:72"``,
    highest score first — which signal(s) actually fired, not just the derived heat bucket.
    """
    ranked = sorted(topics or [], key=lambda t: t.get("score") or 0, reverse=True)
    # A topic nobody scored is written bare: ":0" would claim a measurement of zero.
    return ";".join(
        f"{t['topic']}:{t['score']}" if t.get("score") is not None else str(t["topic"])
        for t in ranked
        if t.get("topic")
    )


def _hubspot_row(
    a: dict, *, run_date: str | None = None, rubric_version: str | None = None
) -> list[Any]:
    first, last = _split_name(a.get("contact_name", ""))
    return [
        first,
        last,
        a.get("contact_email", ""),  # blank if unverified, never guessed (hubspot-csv-map.md)
        a.get("email_status", ""),  # deliverability signal, e.g. "RocketReach A", "verified"
        a.get("contact_phone", ""),
        a.get("contact_title", ""),
        a.get("contact_linkedin_url", ""),
        a.get("company", ""),
        a.get("domain", ""),
        a.get("city", ""),
        a.get("market", ""),
        a.get("employees_number", "") or _employees_midpoint(a.get("employees_range", "")),
        a.get("segment", "").capitalize(),
        a.get("score", ""),
        a.get("tier", ""),
        a.get("persona_tier", ""),
        a.get("why_now", ""),
        a.get("case_study", ""),
        a.get("gtm_source", "Cold"),
        run_date or "",
        rubric_version or "",
        a.get("heat", 0),
        ";".join(a.get("intent_feeds", [])),
        a.get("top_intent_score", ""),
        _format_intent_topics(a.get("intent_topics", [])),
        a.get("industry", ""),
        a.get("revenue_range", ""),
        "Yes" if a.get("new_in_role") else "",
        a.get("qualification_path", ""),
        a.get("conf", ""),
        a.get("signal_source_url", ""),
        a.get("signal_observed", ""),
        a.get("signal_evidence", ""),
        a.get("signal_subject", ""),
        a.get("signal_agent_kind", ""),
        a.get("category_relation", ""),
        a.get("verdict", ""),
        a.get("verdict_reason", ""),
        a.get("signal_column", ""),
    ]


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_import")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="parse an account CSV -> candidates [+ cost log]")
    ing.add_argument("--profile", required=True)
    ing.add_argument("--csv", required=True)
    ing.add_argument("--source-run", required=True)
    ing.add_argument("--exclude", default=None, help="path to exclude-set JSON ({companies:[...]})")
    ing.add_argument(
        "--source",
        default="vibe-export",
        help="provenance + billing switch; only 'vibe-export' bills (e.g. 'curated-sheet')",
    )

    stg = sub.add_parser(
        "stage-standard",
        help="validate minimal findings and print them in canonical shape (unsupplied = blank)",
    )
    stg.add_argument(
        "--items",
        required=True,
        help="path to JSON array of minimal findings, or '-' for stdin",
    )
    stg.add_argument(
        "--out",
        default=None,
        help="path to output full items JSON (defaults to stdout)",
    )

    fin = sub.add_parser("finalize", help="merge scored items -> latest.json + hubspot csv")
    fin.add_argument("--profile", required=True)
    fin.add_argument("--items", required=True, help="path to a JSON array of scored item objects")
    fin.add_argument("--source-run", required=True)
    fin.add_argument(
        "--run-date", default=None, help="YYYY-MM-DD for the HubSpot CSV's GTM_Run_Date column"
    )
    fin.add_argument(
        "--rubric-version", default=None, help="for the HubSpot CSV's GTM_Rubric_Version column"
    )
    fin.add_argument(
        "--standard",
        action="store_true",
        help="accepted for compatibility; every item is normalised either way",
    )

    args = ap.parse_args(argv)
    try:
        return _run(args)
    except ValueError as exc:
        # Unreadable JSON, a bad item, an unsafe path segment: one line naming the
        # cause — not a traceback for the brain to guess at.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.cmd == "ingest":
        summary = ingest(
            args.csv, args.profile, args.source_run, exclude_path=args.exclude, source=args.source
        )
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == "stage-standard":
        if args.items == "-":
            raw_text = sys.stdin.read()
        else:
            raw_text = Path(args.items).read_text(encoding="utf-8")
        staged = normalise_items(json.loads(raw_text))
        out_json = json.dumps(staged, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(out_json, encoding="utf-8")
        else:
            print(out_json)
        return 0
    if args.cmd == "finalize":
        items = json.loads(Path(args.items).read_text(encoding="utf-8"))
        summary = finalize(
            args.profile,
            items,
            args.source_run,
            run_date=args.run_date,
            rubric_version=args.rubric_version,
            standard=args.standard,
        )
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
