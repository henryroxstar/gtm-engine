"""Standardized 6-source signal hunt and web sweep normalizer.

Implements Step 6 of the prospect skill (PRD 2026-09-20):
- Generates the standardized 6 search queries for a candidate to avoid LLM context thrashing.
- Normalizes and validates raw web sweep findings:
  - Validates HTTPS URLs and rejects search engine pages.
  - Enforces date freshness: <90 days for enterprise, <18 months (540 days) for startup funding,
    and <=210 days for signal_observed.
  - Formats hits as `[type | date | URL | H/M/L]`.
  - Ranks hits by strength (H > M > L) and recency to select the single strongest 🔥 signal.
  - If no dated signal survives, sets `verdict: "re-angle"` and `why_now: ""` so negative prose
    is never written to why_now.

Stdlib-only, deterministic, zero egress (all external I/O remains via MCP tools).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gtm_core.merge_hygiene import clean_company, signal_clause

# Freshness thresholds in days
ENTERPRISE_MAX_AGE_DAYS = 90
STARTUP_FUNDING_MAX_AGE_DAYS = 540  # 18 months
GENERAL_MAX_AGE_DAYS = 210

# Search engines forbidden as source URLs
DISALLOWED_DOMAINS = frozenset(
    {
        "google.com",
        "www.google.com",
        "bing.com",
        "www.bing.com",
        "duckduckgo.com",
        "yahoo.com",
        "search.yahoo.com",
        "baidu.com",
        "yandex.com",
    }
)

QUERY_TEMPLATES: list[tuple[str, str, str]] = [
    (
        "newsroom",
        "Newsroom / PR",
        '"{company}" (agentic OR "AI governance" OR "agent identity")',
    ),
    (
        "hiring",
        "Hiring",
        '"{company}" ("AI platform" OR agent OR "ML platform") site:linkedin.com/jobs',
    ),
    (
        "eng",
        "Engineering Signal",
        '"{company}" (MCP OR A2A OR "engineering blog" OR github)',
    ),
    (
        "regulatory",
        "Regulatory & Standards",
        '"{company}" (regulator OR compliance OR standards OR MAS OR IMDA OR W3C OR DIF OR "earnings call")',
    ),
    (
        "funding",
        "Funding",
        '"{company}" (raised OR "Series")',
    ),
    (
        "incident",
        "Pressure & Incidents",
        '"{company}" (breach OR audit OR "EU AI Act" OR incident OR stall)',
    ),
]


def generate_queries(
    company: str,
    segment: str = "startup",
    domain: str | None = None,
) -> list[dict[str, str]]:
    """Generate the fixed 6-source signal hunt search queries."""
    cleaned = clean_company(company)
    queries: list[dict[str, str]] = []

    for q_type, label, template in QUERY_TEMPLATES:
        # Segment customization: skip funding query for enterprise if not needed,
        # but the standard 6-source hunt keeps all 6 in sequence.
        q_str = template.format(company=cleaned)
        if domain and q_type == "eng":
            q_str = f'"{cleaned}" OR site:{domain} (MCP OR A2A OR "engineering blog" OR github)'

        queries.append(
            {
                "type": q_type,
                "label": label,
                "query": q_str,
            }
        )

    return queries


def is_valid_source_url(url: str) -> bool:
    """Verify that the URL is https and not a search engine results page."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        return False
    netloc = (parsed.netloc or "").lower()
    for bad in DISALLOWED_DOMAINS:
        if netloc == bad or netloc.endswith("." + bad):
            return False
    return bool(parsed.netloc)


def parse_date(date_str: str) -> dt.date | None:
    """Parse ISO YYYY-MM-DD date."""
    if not date_str:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date_str))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _check_freshness(hit_type: str, segment: str, age_days: int) -> bool:
    """Check if the hit falls within the required freshness window."""
    if age_days < 0:
        # Future date or clock skew: reject
        return False

    seg = (segment or "").lower()
    if hit_type == "funding" and seg == "startup":
        return age_days <= STARTUP_FUNDING_MAX_AGE_DAYS
    elif seg == "enterprise":
        return age_days <= ENTERPRISE_MAX_AGE_DAYS
    else:
        return age_days <= GENERAL_MAX_AGE_DAYS


def _determine_agent_kind(text: str) -> str:
    """Classify agent kind: ai, human, none, unclear."""
    lower = text.lower()
    if "ai" in lower or "agentic" in lower or "mcp" in lower or "llm" in lower:
        return "ai"
    if "agent" in lower:
        return "ai"
    return "none"


def normalize_hit(
    raw: dict[str, Any],
    company: str,
    segment: str = "startup",
    ref_date: dt.date | None = None,
) -> dict[str, Any] | None:
    """Normalize and validate a single hit. Returns None if invalid or stale."""
    url = str(raw.get("url") or raw.get("source_url") or "").strip()
    if not is_valid_source_url(url):
        return None

    date_val = parse_date(raw.get("date") or raw.get("observed"))
    if not date_val:
        return None

    today = ref_date or dt.datetime.now(dt.UTC).date()
    age_days = (today - date_val).days

    hit_type = str(raw.get("type") or "newsroom").lower()
    if not _check_freshness(hit_type, segment, age_days):
        return None

    # Determine strength H/M/L
    strength = str(raw.get("strength") or "").upper()
    if strength not in ("H", "M", "L"):
        # Heuristic default: newsroom / eng / incident with agent content -> H, else M
        evidence_text = str(raw.get("evidence") or raw.get("snippet") or "")
        if (
            hit_type in ("newsroom", "eng", "incident")
            and _determine_agent_kind(evidence_text) == "ai"
        ):
            strength = "H"
        else:
            strength = "M"

    date_iso = date_val.isoformat()
    tag = f"[{hit_type} | {date_iso} | {url} | {strength}]"
    evidence = str(raw.get("evidence") or raw.get("snippet") or "").strip()

    return {
        "type": hit_type,
        "date": date_iso,
        "age_days": age_days,
        "url": url,
        "strength": strength,
        "tag": tag,
        "evidence": evidence,
        "title": str(raw.get("title") or ""),
    }


def normalize_sweep(
    company: str,
    raw_hits: list[dict[str, Any]],
    segment: str = "startup",
    as_of: str | None = None,
) -> dict[str, Any]:
    """Process all raw hits for a company, select top 🔥 signal, or route to re-angle."""
    cleaned_co = clean_company(company)
    ref_date = parse_date(as_of) if as_of else None

    valid_hits: list[dict[str, Any]] = []
    for h in raw_hits:
        normalized = normalize_hit(h, company=cleaned_co, segment=segment, ref_date=ref_date)
        if normalized:
            valid_hits.append(normalized)

    # Sort valid hits: Strength (H > M > L), then age (youngest first)
    strength_rank = {"H": 0, "M": 1, "L": 2}
    valid_hits.sort(key=lambda item: (strength_rank.get(item["strength"], 1), item["age_days"]))

    if not valid_hits:
        return {
            "company": cleaned_co,
            "why_now": "",
            "signal_source_url": "",
            "signal_observed": "",
            "signal_evidence": "",
            "signal_subject": cleaned_co,
            "signal_agent_kind": "none",
            "category_relation": "",
            "verdict": "re-angle",
            "verdict_reason": "no dated public why-now found this pass",
            "fire_tag": "",
            "hits": [],
        }

    top = valid_hits[0]
    evidence = top["evidence"]

    # Formulate why_now clause without stray digits
    why_now = signal_clause(evidence) if evidence else ""

    agent_kind = _determine_agent_kind(why_now + " " + evidence)

    # If a signal was found but rejected by signal_clause (e.g. contains digits), route to re-angle
    verdict = "" if why_now else "re-angle"
    verdict_reason = "" if why_now else "evidence rejected by signal_clause (e.g. contains digits)"

    return {
        "company": cleaned_co,
        "why_now": why_now,
        "signal_source_url": top["url"],
        "signal_observed": top["date"],
        "signal_evidence": evidence,
        "signal_subject": cleaned_co,
        "signal_agent_kind": agent_kind,
        "category_relation": "",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "fire_tag": top["tag"],
        "hits": valid_hits,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.web_sweep",
        description="Standardized 6-source signal hunt queries and findings normalizer.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    q_p = sub.add_parser("queries", help="Generate 6 standardized search queries for a company")
    q_p.add_argument("--company", required=True, help="Target company name")
    q_p.add_argument("--segment", default="startup", help="Segment: enterprise or startup")
    q_p.add_argument("--domain", default=None, help="Optional company domain")
    q_p.add_argument("--out", default=None, help="Output JSON path (defaults to stdout)")

    n_p = sub.add_parser("normalize", help="Normalize raw findings and select 🔥 signal")
    n_p.add_argument("--company", required=True, help="Target company name")
    n_p.add_argument(
        "--hits", required=True, help="Path to JSON file of raw hits, or '-' for stdin"
    )
    n_p.add_argument("--segment", default="startup", help="Segment: enterprise or startup")
    n_p.add_argument("--as-of", default=None, help="Reference date ISO YYYY-MM-DD")
    n_p.add_argument("--out", default=None, help="Output JSON path (defaults to stdout)")

    args = parser.parse_args(argv)

    if args.cmd == "queries":
        queries = generate_queries(args.company, segment=args.segment, domain=args.domain)
        out_json = json.dumps(queries, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(out_json, encoding="utf-8")
        else:
            print(out_json)
        return 0

    if args.cmd == "normalize":
        if args.hits == "-":
            raw_content = sys.stdin.read()
        else:
            p = Path(args.hits)
            if not p.exists():
                print(f"Error: file '{p}' not found", file=sys.stderr)
                return 1
            raw_content = p.read_text(encoding="utf-8")

        try:
            hits_data = json.loads(raw_content)
        except json.JSONDecodeError as err:
            print(f"Error parsing hits JSON: {err}", file=sys.stderr)
            return 1

        if not isinstance(hits_data, list):
            print("Error: hits must be a JSON array", file=sys.stderr)
            return 1

        res = normalize_sweep(
            company=args.company,
            raw_hits=hits_data,
            segment=args.segment,
            as_of=args.as_of,
        )
        out_json = json.dumps(res, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(out_json, encoding="utf-8")
        else:
            print(out_json)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
