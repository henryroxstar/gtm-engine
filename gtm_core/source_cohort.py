"""Deterministic cohort builder for signal-first Gate 2 outreach review pages.

Builds structured send-cards cells from an evidence JSON, a members CSV, and touch specs,
cross-checking against the account ledger, and generates the review HTML.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import run_scope
from .confine import confined_output_path, confined_source_file
from .paths import resolve_content_root
from .send_cards import generate_cards_page


def sanitize_formula(val: str) -> str:
    """Escape formula-injection prefixes for safe CSV export (§R6)."""
    s = str(val or "").strip()
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def parse_spec_touch(spec_path: Path) -> tuple[str, str]:
    """Parse subject and HTML body from a markdown sequence spec."""
    text = spec_path.read_text(encoding="utf-8")
    subj_m = re.search(r"\*\*Step 1[^\n]*Subject:\s*`?([^`\n]+)`?", text)
    subj = subj_m.group(1).strip() if subj_m else "agents in production"

    touches_idx = text.find("## 3. Touches")
    touches_text = text[touches_idx:] if touches_idx != -1 else text

    lines = touches_text.splitlines()
    quote_lines: list[str] = []
    in_step1 = False
    for line in lines:
        if "**Step 1" in line:
            in_step1 = True
            continue
        if in_step1:
            if line.startswith("**Step 2"):
                break
            if line.startswith(">"):
                quote_lines.append(line[1:].strip())
            elif quote_lines and not line.strip():
                continue

    paras: list[list[str]] = []
    curr: list[str] = []
    for ql in quote_lines:
        if not ql:
            if curr:
                paras.append(curr)
                curr = []
        else:
            curr.append(ql)
    if curr:
        paras.append(curr)

    html_parts: list[str] = []
    for p in paras:
        if len(p) == 2 and p[0].lower() in ("regards", "best regards", "thanks", "cheers"):
            html_parts.append(f"<p>{p[0]}<br>{p[1]}</p>")
        else:
            joined = " ".join(p)
            html_parts.append(f"<p>{joined}</p>")

    body = "".join(html_parts)
    return subj, body


def load_cohort_members(
    members_csv_path: Path,
    content_root: Path,
) -> list[dict[str, Any]]:
    """Load and validate cohort members from CSV, ensuring paths are confined."""
    confined_source_file(members_csv_path, content_root=content_root)
    text = members_csv_path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    members = []
    for row in reader:
        members.append(
            {
                "slug": row["slug"].strip(),
                "evidence_index": row["evidence_index"].strip(),
                "name": sanitize_formula(row["name"].strip()),
                "email": row["email"].strip().lower(),
                "company": sanitize_formula(row["company"].strip()),
                "title": row["title"].strip(),
                "country": row["country"].strip(),
                "industry": row["industry"].strip(),
                "borderline": str(row.get("borderline", "")).strip().lower()
                in ("true", "1", "yes"),
                "spec_path": row["spec_path"].strip(),
            }
        )
    return members


def build_cohort_cells(
    members: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    specs_dir: Path,
    content_root: Path,
    sequence_ids: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build cell dicts for send_cards and flat rows for CSV export.

    Returns ``(cells, rows)``.
    """
    ev_by_account = {x["account"]: x for x in evidence}
    sequence_ids = sequence_ids or {}

    cells = []
    rows = []
    for m in members:
        acct = m["evidence_index"]
        if acct not in ev_by_account:
            raise ValueError(
                f"Cohort member {m['name']!r} specifies account {acct!r} which is missing from evidence JSON"
            )

        r = ev_by_account[acct]
        cid = f"source-test-{m['slug']}-2026-10"

        spec_file = specs_dir / m["spec_path"]
        confined_source_file(spec_file, content_root=content_root)
        if not spec_file.is_file():
            raise ValueError(f"Spec file not found for member {m['name']}: {spec_file}")

        subj, body = parse_spec_touch(spec_file)

        card_title = f"DRAFT · Source test · {m['title']} · agents in operation · Enterprise"
        if m["borderline"]:
            card_title += " · BORDERLINE"

        seq_id = sequence_ids.get(cid) or sequence_ids.get(m["slug"]) or f"seq-{cid}"
        step_id = f"step-{cid}"

        member_dict = {
            "name": m["name"],
            "email": m["email"],
            "company": m["company"],
            "industry": m["industry"],
            "country": m["country"],
            "level": "cxo",
            "seat": "architect",
            "opener": r["why_now"],
            "source_url": r["signal_source_url"],
            "capture_date": r["signal_observed"],
            "signal_kind": "event",
            "signal_class": "news_event",
            "premise_via": "evidence",
            "source_id": "",
        }

        cell_data = {
            "cell_id": cid,
            "title": card_title,
            "seat": "architect",
            "cohort": "source-test",
            "segment": "enterprise",
            "angle": "ent-architect-agents-in-operation",
            "message_variant": "angle-1-touch",
            "is_personalised": True,
            "premise_ids": ["agents-in-operation"],
            "proof_ids": [],
            "example_member": {"name": m["name"]},
            "members": [member_dict],
            "sequence_id": seq_id,
            "step_id": step_id,
            "steps": [
                {
                    "step_id": step_id,
                    "variants": [{"subject": subj, "content": body, "preheader": ""}],
                }
            ],
            "spec": m["spec_path"],
        }
        cells.append(cell_data)

        rows.append(
            {
                "email": m["email"],
                "company": m["company"],
                "name": m["name"],
                "domain": r.get("domain", ""),
                "company_domain": r.get("domain", ""),
                "country": m["country"],
                "industry": m["industry"],
                "signal_source_url": r["signal_source_url"],
                "signal_observed": r["signal_observed"],
                "signal_evidence": r["signal_evidence"],
                "why_now": r["why_now"],
                "signal_subject": r.get("signal_subject", m["company"]),
                "signal_agent_kind": r.get("signal_agent_kind", "ai"),
                "category_relation": r.get("category_relation", "prospect"),
                "verdict": "send",
                "verdict_reason": "signal-first cohort test",
                "cell_id": cid,
            }
        )

    return cells, rows


def check_ledger_presence(
    members: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    ledger_items: list[dict[str, Any]],
) -> list[str]:
    """Check each member against the ledger, returning warnings for un-indexed contacts/domains."""
    ev_by_account = {x["account"]: x for x in evidence}
    ledger_emails = {
        str(item.get("email") or "").strip().lower() for item in ledger_items if item.get("email")
    }
    ledger_domains = {
        str(item.get("domain") or "").strip().lower().removeprefix("www.")
        for item in ledger_items
        if item.get("domain")
    }

    missing = []
    for m in members:
        email = m["email"]
        r = ev_by_account.get(m["evidence_index"], {})
        domain = str(r.get("domain") or "").strip().lower().removeprefix("www.")
        if not domain and "@" in email:
            domain = email.split("@", 1)[1]

        in_email = email in ledger_emails
        in_domain = domain in ledger_domains
        if not in_email and not in_domain:
            missing.append(
                f"{m['name']} ({m['company']}, email={email}, domain={domain}) not found in ledger"
            )
        elif not in_email:
            missing.append(
                f"{m['name']} ({m['company']}, email={email}) has domain in ledger but contact is not resolved in ledger"
            )

    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.source_cohort",
        description="Deterministic cohort builder for signal-first sourcing reviews.",
    )
    parser.add_argument("--profile", required=True, help="active tenant profile")
    parser.add_argument("--product", default="agent-gateway", help="product scope")
    parser.add_argument("--members", type=Path, default=None, help="path to cohort members CSV")
    parser.add_argument("--evidence", type=Path, default=None, help="path to fresh evidence JSON")
    parser.add_argument("--stamp", default="2026-10-01 DRAFT", help="review page stamp")
    parser.add_argument("--out", type=Path, default=None, help="output review HTML path")
    parser.add_argument("--out-csv", type=Path, default=None, help="output cohort rows CSV path")
    parser.add_argument(
        "--sequence-ids",
        type=Path,
        default=None,
        help="JSON file mapping cell_id -> sequencer sequence_id",
    )
    parser.add_argument(
        "--check", type=Path, default=None, help="check output matches existing file byte-for-byte"
    )

    args = parser.parse_args(argv)

    root = resolve_content_root()
    profile_dir = root / args.profile
    prospects_dir = profile_dir / "prospects"

    try:
        run_scope.require(args.profile, args.product)
    except run_scope.ScopeError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2

    members_csv = args.members or (prospects_dir / "source-test-cohort-members-2026-10-01.csv")
    evidence_json = args.evidence or (
        prospects_dir / "source-test-cohort-fresh-evidence-2026-10-01.json"
    )
    out_html = args.out or (prospects_dir / "send-cards-source-test-2026-10-01.html")

    confined_source_file(members_csv, content_root=root)
    confined_source_file(evidence_json, content_root=root)
    confined_output_path(out_html, content_root=root)

    members = load_cohort_members(members_csv, root)
    evidence = json.loads(evidence_json.read_text(encoding="utf-8"))

    # Ledger cross-check
    ledger_path = prospects_dir / "latest.json"
    if ledger_path.is_file():
        try:
            ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger_items = ledger_data.get("items", [])
            warnings = check_ledger_presence(members, evidence, ledger_items)
            for w in warnings:
                print(f"warning (ledger): {w}", file=sys.stderr)
        except Exception as exc:
            print(f"warning: could not cross-check ledger: {exc}", file=sys.stderr)

    seq_mapping = {}
    if args.sequence_ids:
        confined_source_file(args.sequence_ids, content_root=root)
        seq_mapping = json.loads(args.sequence_ids.read_text(encoding="utf-8"))

    cells, rows = build_cohort_cells(
        members,
        evidence,
        specs_dir=prospects_dir,
        content_root=root,
        sequence_ids=seq_mapping,
    )

    page_html = generate_cards_page(cells, stamp=args.stamp, profile=args.profile)

    if args.check:
        confined_source_file(args.check, content_root=root)
        if not args.check.is_file():
            print(f"Check failed: target file {args.check} does not exist", file=sys.stderr)
            return 1
        existing = args.check.read_text(encoding="utf-8")
        if page_html != existing:
            print(f"Check failed: generated HTML differs from {args.check}", file=sys.stderr)
            return 1
        print(f"Check passed: byte-for-byte identical to {args.check}")
        return 0

    out_html.write_text(page_html, encoding="utf-8")
    print(f"Wrote {len(cells)} cells to {out_html}")

    if args.out_csv:
        confined_output_path(args.out_csv, content_root=root)
        if rows:
            with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Wrote {len(rows)} cohort rows to {args.out_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
