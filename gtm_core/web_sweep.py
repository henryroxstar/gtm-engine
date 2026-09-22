"""Standardized 6-source signal hunt and web sweep normalizer.

Implements Step 6 of the prospect skill (PRD 2026-09-20):
- Generates the standardized 6 search queries for a candidate to avoid LLM context thrashing.
  The search-term vocabulary is neutral by default; pass ``--profile`` (and optional
  ``--product``) to load a tenant's own vocabulary from its ``web-sweep.toml`` knowledge file
  (see :func:`generate_queries`, in :mod:`gtm_core.web_sweep_queries`).
- Normalizes and validates raw web sweep findings:
  - Accepts a JSON array of hit objects. Each hit needs ``url``/``source_url`` (an https,
    non-search-engine page), ``date``/``observed`` (ISO ``YYYY-MM-DD``), and
    ``evidence``/``snippet`` (a short quote). Optional: ``type`` (newsroom | hiring | eng |
    regulatory | funding | incident; default newsroom), ``title``, ``strength`` (H|M|L), and
    ``subject`` (the entity the evidence is actually ABOUT, when it is not the account itself).
    Example (fictional): ``[{"url": "https://northwind.example/news/a", "date": "2026-09-10",
    "type": "newsroom", "evidence": "Northwind Robotics opened its agent platform to
    partners.", "strength": "H"}]``.
  - Validates HTTPS URLs and rejects search-engine results pages (not their other pages) —
    see :mod:`gtm_core.web_sweep_urls`.
  - Enforces date freshness: <=90 days for enterprise, <=210 days for everything else
    (``GENERAL_MAX_AGE_DAYS`` — this is also the ceiling the downstream load gate enforces on
    ``signal_observed``), except a startup funding hit may be up to 540 days (18 months) old
    and still be surfaced as background *context* — never as the selected why-now signal, since
    it would fail that same load gate. See :mod:`gtm_core.web_sweep_hits`.
  - Rejects a hit whose evidence is not about the account (no explicit ``subject`` match and no
    mention of the account by name), so a stranger's news is never attributed to the account.
  - Formats hits as `[type | date | URL | H/M/L]`.
  - Ranks hits by strength (H > M > L) and recency, then walks them in that order to pick the
    first whose evidence reduces to a usable clause (:func:`gtm_core.merge_hygiene.signal_clause`).
  - Every input hit is accounted for in the result: ``hits`` (usable, ranked), ``context_hits``
    (visible but never selectable — the 210-540 day startup-funding carve-out), or ``rejected``
    (``{"index", "reason"}``, reason one of: not-an-object, missing-url, invalid-url,
    missing-date, unparseable-date, missing-evidence, future-dated, stale, subject-mismatch).
  - If EVERY hit was rejected for a shape reason (the caller guessed the wrong keys), raises
    ``ValueError`` naming the expected keys — that failure must be loud, never a silent re-angle.
  - If no dated signal survives for another reason, sets `verdict: "re-angle"` and `why_now: ""`
    so negative prose is never written to why_now, with a `verdict_reason` that says why.

Stdlib-only, deterministic, zero egress (all external I/O remains via MCP tools).

Split (§R10) across three sibling modules, all re-exported here so this stays the one import
path for callers: :mod:`gtm_core.web_sweep_urls` (source-URL validation),
:mod:`gtm_core.web_sweep_queries` (vocabulary + query generation), and
:mod:`gtm_core.web_sweep_hits` (single-hit validation, freshness, subject, agent-kind).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gtm_core.merge_hygiene import clean_company, signal_clause
from gtm_core.web_sweep_hits import (
    _SHAPE_REJECT_REASONS,
    _VALID_SEGMENTS,
    _determine_agent_kind,
    _evaluate_hit,
    _validate_segment,
    parse_date,
)
from gtm_core.web_sweep_hits import (
    _check_freshness as _check_freshness,
)
from gtm_core.web_sweep_hits import (
    normalize_hit as normalize_hit,
)
from gtm_core.web_sweep_queries import generate_queries
from gtm_core.web_sweep_urls import is_valid_source_url as is_valid_source_url


def normalize_sweep(
    company: str,
    raw_hits: list[dict],
    segment: str = "startup",
    as_of: str | None = None,
) -> dict:
    """Process all raw hits for a company, select the strongest usable 🔥 signal, or route to
    re-angle.

    Every input hit lands in `hits` (usable, ranked) or in `rejected` (`{"index", "reason"}`;
    see the module docstring for the reason codes). A startup-funding hit too old to be a
    why-now (PSK-021) is BOTH rejected as `stale` and echoed in `context_hits`, so the
    researcher can see what was close without it ever becoming the signal. Raises
    `ValueError` when EVERY hit was rejected for a shape reason (the caller guessed the wrong
    keys) — that must fail loudly, never look like a genuine empty-handed research pass.
    """
    _validate_segment(segment)
    cleaned_co = clean_company(company)
    if as_of:
        ref_date = parse_date(as_of)
        if ref_date is None:
            raise ValueError(f"--as-of value {as_of!r} is not a valid ISO YYYY-MM-DD date")
    else:
        ref_date = None

    valid_hits: list[dict] = []
    rejected: list[dict] = []
    context_hits: list[dict] = []
    stranger_subject: str | None = None

    for i, h in enumerate(raw_hits):
        hit, reason, context_hit, stranger = _evaluate_hit(h, cleaned_co, segment, ref_date)
        if stranger and stranger_subject is None:
            stranger_subject = stranger
        if context_hit is not None:
            context_hits.append(context_hit)
        if hit is not None:
            valid_hits.append(hit)
        else:
            rejected.append({"index": i, "reason": reason})

    if raw_hits and rejected and len(rejected) == len(raw_hits):
        if all(r["reason"] in _SHAPE_REJECT_REASONS for r in rejected):
            seen_keys = sorted({str(k) for h in raw_hits if isinstance(h, dict) for k in h})
            raise ValueError(
                "hits do not match the expected shape (need url/source_url, date/observed, "
                f"evidence/snippet); saw keys: {seen_keys or '[hits were not JSON objects]'}"
            )

    strength_rank = {"H": 0, "M": 1, "L": 2}
    valid_hits.sort(key=lambda item: (strength_rank.get(item["strength"], 1), item["age_days"]))

    if not valid_hits:
        if not raw_hits:
            verdict_reason = "no dated public why-now found this pass"
        elif {r["reason"] for r in rejected} == {"subject-mismatch"}:
            verdict_reason = "evidence is not about this account"
        else:
            verdict_reason = "no hit in this pass survived validation (see rejected)"
        return {
            "company": cleaned_co,
            "why_now": "",
            "signal_source_url": "",
            "signal_observed": "",
            "signal_evidence": "",
            "signal_subject": stranger_subject or cleaned_co,
            "signal_agent_kind": "none",
            "category_relation": "",
            "verdict": "re-angle",
            "verdict_reason": verdict_reason,
            "fire_tag": "",
            "hits": [],
            "rejected": rejected,
            "context_hits": context_hits,
        }

    # Walk ranked hits in order; use the first whose evidence reduces to a usable clause
    # (PSK-020) — a lower-ranked hit is not ignored just because the top one cannot reduce.
    chosen = valid_hits[0]
    why_now = ""
    for candidate in valid_hits:
        clause = signal_clause(candidate["evidence"]) if candidate["evidence"] else ""
        if clause:
            chosen = candidate
            why_now = clause
            break

    agent_kind = _determine_agent_kind(why_now + " " + chosen["evidence"])
    verdict = "" if why_now else "re-angle"
    verdict_reason = "" if why_now else "evidence rejected by signal_clause (e.g. contains digits)"

    return {
        "company": cleaned_co,
        "why_now": why_now,
        "signal_source_url": chosen["url"],
        "signal_observed": chosen["date"],
        "signal_evidence": chosen["evidence"],
        "signal_subject": cleaned_co,
        "signal_agent_kind": agent_kind,
        "category_relation": "",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "fire_tag": chosen["tag"],
        "hits": valid_hits,
        "rejected": rejected,
        "context_hits": context_hits,
    }


def _write_out_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: a tmp file in the same directory, then `os.replace`."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


_HIT_SHAPE_HELP = """
Each raw hit passed to --hits is a JSON object:
  url | source_url    (required)  https URL of a real, non-search-engine page
  date | observed      (required)  ISO date, e.g. "2026-09-10"
  evidence | snippet   (required)  a short quote/summary of what was found
  type                 (optional, default "newsroom")
                        one of: newsroom | hiring | eng | regulatory | funding | incident
  title                (optional)  page title — helps confirm the hit is about this account
  strength              (optional)  H | M | L; heuristically defaulted when absent
  subject                (optional)  the entity the evidence is actually ABOUT, when it is
                        not this account (a mismatch is reported, never silently relabelled)

Example (fictional data):
  [
    {
      "url": "https://northwind.example/news/agent-platform",
      "date": "2026-09-10",
      "type": "newsroom",
      "evidence": "Northwind Robotics opened its agent platform to partner organisations.",
      "strength": "H"
    }
  ]
"""


def _cmd_queries(args: argparse.Namespace) -> int:
    try:
        queries = generate_queries(
            args.company,
            segment=args.segment,
            domain=args.domain,
            profile=args.profile,
            product=args.product,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    out_json = json.dumps(queries, indent=2, ensure_ascii=False)
    if args.out:
        _write_out_atomic(Path(args.out), out_json)
    else:
        print(out_json)
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    if args.as_of is not None and parse_date(args.as_of) is None:
        print(
            f"Error: --as-of value '{args.as_of}' is not a valid ISO YYYY-MM-DD date",
            file=sys.stderr,
        )
        return 2

    p = Path(args.hits)
    if args.hits != "-" and not p.exists():
        print(f"Error: file '{p}' not found", file=sys.stderr)
        return 1
    try:
        raw_content = sys.stdin.read() if args.hits == "-" else p.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        print(f"Error: the hits input is not UTF-8 text: {err}", file=sys.stderr)
        return 1

    try:
        hits_data = json.loads(raw_content)
    except json.JSONDecodeError as err:
        print(f"Error parsing hits JSON: {err}", file=sys.stderr)
        return 1

    if not isinstance(hits_data, list):
        print("Error: hits must be a JSON array", file=sys.stderr)
        return 1

    try:
        res = normalize_sweep(
            company=args.company,
            raw_hits=hits_data,
            segment=args.segment,
            as_of=args.as_of,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    out_json = json.dumps(res, indent=2, ensure_ascii=False)
    if args.out:
        _write_out_atomic(Path(args.out), out_json)
    else:
        print(out_json)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.web_sweep",
        description="Standardized 6-source signal hunt queries and findings normalizer.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    q_p = sub.add_parser("queries", help="Generate 6 standardized search queries for a company")
    q_p.add_argument("--company", required=True, help="Target company name")
    q_p.add_argument("--segment", default="startup", choices=list(_VALID_SEGMENTS), help="Segment")
    q_p.add_argument("--domain", default=None, help="Optional company domain")
    q_p.add_argument(
        "--profile", default=None, help="Tenant profile to load web-sweep.toml vocabulary from"
    )
    q_p.add_argument("--product", default=None, help="Optional product slug for --profile")
    q_p.add_argument("--out", default=None, help="Output JSON path (defaults to stdout)")

    n_p = sub.add_parser(
        "normalize",
        help="Normalize raw findings and select 🔥 signal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_HIT_SHAPE_HELP,
    )
    n_p.add_argument("--company", required=True, help="Target company name")
    n_p.add_argument(
        "--hits", required=True, help="Path to JSON file of raw hits, or '-' for stdin"
    )
    n_p.add_argument("--segment", default="startup", choices=list(_VALID_SEGMENTS), help="Segment")
    n_p.add_argument("--as-of", default=None, help="Reference date ISO YYYY-MM-DD")
    n_p.add_argument("--out", default=None, help="Output JSON path (defaults to stdout)")

    args = parser.parse_args(argv)

    if args.cmd == "queries":
        return _cmd_queries(args)
    if args.cmd == "normalize":
        return _cmd_normalize(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
