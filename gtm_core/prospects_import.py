"""Ingest a Vibe/Explorium ``export-to-csv`` file into the prospect pipeline.

This is the "extractor" half of bulk-mode discovery (PRD
``docs/prds/2026-07-19-bulk-discovery-explorium.md``). The Vibe MCP caps inline
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
import hashlib
import json
import re
from pathlib import Path

from gtm_core.ledgers import Ledgers
from gtm_core.paths import PathConfig, resolve_content_root, resolve_profiles_root
from gtm_core.prospects_state import _identity_key, _norm, upsert_latest

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


def _slug(name: str) -> str:
    n = re.sub(r"[^\x00-\x7f]", "", name.lower())
    n = re.sub(r"\s+", "-", n.strip())
    n = re.sub(r"[^a-z0-9\-]", "", n)
    n = n.strip("-")
    if n:
        return n
    # A pure non-ASCII name (e.g. a CJK company) strips to empty above; fall back
    # to a stable, non-empty, collision-resistant token so the row keeps a usable
    # id instead of "" (which would collide with every other such row downstream).
    stripped = name.strip()
    if not stripped:
        return ""
    return "co-" + hashlib.sha256(stripped.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


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


def parse_vibe_export(csv_path: str | Path) -> list[dict]:
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
                "source": "vibe-export",
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
) -> dict:
    """Parse a Vibe export → dedup vs exclude → write candidates JSON → log cost.

    Produces ``content/<profile>/prospects/imports/candidates-<source_run>.json``
    for the skill to score. Returns a summary dict.
    """
    root = content_root or resolve_content_root()
    candidates = parse_vibe_export(csv_path)
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

    cost = log_vibe_export_cost(profile, billed_rows, source_run, content_root=root)
    return {
        "profile": profile,
        "source_run": source_run,
        "parsed": parsed_n,
        "billed_rows": billed_rows,
        "excluded": len(dropped),
        "kept": len(kept),
        "high_heat": heated,
        "candidates_file": str(out_path),
        "cost_usd": cost["cost_usd"],
    }


def finalize(
    profile: str,
    scored_items: list[dict],
    source_run: str,
    content_root: Path | None = None,
    *,
    run_date: str | None = None,
    rubric_version: str | None = None,
) -> dict:
    """Merge already-scored items into latest.json (snapshot-safe, merge-only) and
    emit the run's HubSpot CSV. ``scored_items`` come from the skill's scoring pass.

    The CSV follows the exact column contract in
    ``references/hubspot-csv-map.md`` (the doc standard mode's Step 7 already writes
    to by hand) — same header names/order/custom-property set, so bulk-mode output is
    a drop-in HubSpot import identical in shape to a standard-mode run, not a
    parallel, incompatible format.
    """
    summary = upsert_latest(profile, scored_items, source_run, content_root=content_root)
    root = content_root or resolve_content_root()
    csv_path = root / profile / "prospects" / f"prospects-{source_run}-hubspot.csv"
    _write_hubspot_csv(scored_items, csv_path, run_date=run_date, rubric_version=rubric_version)
    summary["hubspot_csv"] = str(csv_path)
    return summary


# Column order/names are the contract in references/hubspot-csv-map.md — standard columns
# (HubSpot's built-in contact properties) first, then the GTM_* custom properties.
_HUBSPOT_COLUMNS = [
    "First Name",
    "Last Name",
    "Email",
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
    ranked = sorted(topics or [], key=lambda t: t.get("score", 0), reverse=True)
    return ";".join(f"{t.get('topic', '')}:{t.get('score', 0)}" for t in ranked if t.get("topic"))


def _write_hubspot_csv(
    items: list[dict],
    path: Path,
    *,
    run_date: str | None = None,
    rubric_version: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HUBSPOT_COLUMNS)
        for a in items:
            first, last = _split_name(a.get("contact_name", ""))
            w.writerow(
                [
                    first,
                    last,
                    a.get(
                        "contact_email", ""
                    ),  # blank if unverified, never guessed (hubspot-csv-map.md)
                    a.get("contact_phone", ""),
                    a.get("contact_title", ""),
                    a.get("contact_linkedin_url", ""),
                    a.get("company", ""),
                    a.get("domain", ""),
                    a.get("city", ""),
                    a.get("market", ""),
                    a.get("employees_number", "")
                    or _employees_midpoint(a.get("employees_range", "")),
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
                ]
            )


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_import")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="parse a Vibe export CSV -> candidates + cost log")
    ing.add_argument("--profile", required=True)
    ing.add_argument("--csv", required=True)
    ing.add_argument("--source-run", required=True)
    ing.add_argument("--exclude", default=None, help="path to exclude-set JSON ({companies:[...]})")

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

    args = ap.parse_args(argv)
    if args.cmd == "ingest":
        summary = ingest(args.csv, args.profile, args.source_run, exclude_path=args.exclude)
        print(json.dumps(summary, indent=2))
        return 0
    if args.cmd == "finalize":
        items = json.loads(Path(args.items).read_text(encoding="utf-8"))
        summary = finalize(
            args.profile,
            items,
            args.source_run,
            run_date=args.run_date,
            rubric_version=args.rubric_version,
        )
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
