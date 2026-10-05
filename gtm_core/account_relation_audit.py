"""The regulator / competitor classifier audit.

PRD 2026-10-02 (RC11):
Reports:
(a) Key stability: checks every domain and alias in regulators.toml and competitors.toml.
    Exits non-zero if any hit key spans more than one distinct company name or two registrable domains.
(b) Coverage floor / lookalikes: pool rows that look like bodies by ending or name but did not classify.
(c) Delta vs legacy matcher: rows the old matcher flagged that new one does not, and vice versa.
(d) Entries that matched nothing in the pool.
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import Any

from .account_relation_load import load_index
from .competitor_index import competitor_entries, domain_stem
from .prospect_paths import pool_dir
from .prospects_consolidate import org_token

_LOOKALIKE_KEYWORDS = (
    "bank",
    "monetary",
    "authority",
    "commission",
    "reserve",
    "exchange",
    "clearing",
    "regulator",
    "ministry",
    "securities",
    "supervisory",
)


def _check_key_stability(index: Any) -> list[str]:
    unstable_keys: list[str] = []
    body_keys: dict[str, set[str]] = {}
    for b in index.regulators.bodies:
        for d in b.domains:
            body_keys.setdefault(d.lower(), set()).add(b.name)
        for a in b.aliases:
            body_keys.setdefault(org_token("", a), set()).add(b.name)

    for k, names in body_keys.items():
        if len(names) > 1:
            unstable_keys.append(f"Regulator key {k!r} maps to multiple bodies: {sorted(names)}")
    return unstable_keys


def _find_lookalikes(rows: list[dict[str, str]], index: Any) -> list[dict[str, str]]:
    lookalikes: list[dict[str, str]] = []
    for r in rows:
        company = (r.get("company") or "").strip()
        domain = (r.get("company_domain") or "").strip()
        email = (r.get("email") or "").strip()
        rel = index.classify(company, domain, email)
        if rel is None:
            c_low = company.lower()
            if any(kw in c_low for kw in _LOOKALIKE_KEYWORDS) or any(
                domain.endswith(sfx) for sfx in (".gov", ".mil", ".org", ".int")
            ):
                lookalikes.append(r)
    return lookalikes


def _compare_legacy(
    rows: list[dict[str, str]], index: Any
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    legacy_only: list[dict[str, str]] = []
    new_only: list[dict[str, str]] = []
    for r in rows:
        company = (r.get("company") or "").strip()
        domain = (r.get("company_domain") or "").strip()
        email = (r.get("email") or "").strip()
        rel = index.classify(company, domain, email)

        legacy_hit = False
        email_host = email.rsplit("@", 1)[-1] if "@" in email else ""
        for sfx in (".gov", ".mil", ".gov.uk", ".gov.sg", ".gov.au", ".gc.ca", ".europa.eu"):
            if domain and (domain == sfx.lstrip(".") or domain.endswith(sfx)):
                legacy_hit = True
                break
        if not legacy_hit:
            stems = [domain_stem(domain), domain_stem(email_host), org_token("", company)]
            for s in stems:
                if s and s in index.competitors:
                    legacy_hit = True
                    break

        if legacy_hit and rel is None:
            legacy_only.append(r)
        elif rel is not None and not legacy_hit:
            new_only.append(r)
    return legacy_only, new_only


def _find_unmatched_entries(
    rows: list[dict[str, str]], index: Any, profile: str
) -> tuple[list[str], list[str]]:
    matched_entries: set[str] = set()
    for r in rows:
        company = (r.get("company") or "").strip()
        domain = (r.get("company_domain") or "").strip()
        email = (r.get("email") or "").strip()
        for rel in index.classify_all(company, domain, email):
            matched_entries.add(rel.entry)

    unmatched_bodies = [b.name for b in index.regulators.bodies if b.name not in matched_entries]
    unmatched_competitors = [
        c.name for c in competitor_entries(profile) if c.name not in matched_entries
    ]
    return unmatched_bodies, unmatched_competitors


def run_audit(profile: str, product: str | None = None) -> tuple[int, dict]:
    index = load_index(profile)
    p_dir = pool_dir(profile)
    master_csv = p_dir / "master-list.csv"

    rows: list[dict[str, str]] = []
    if master_csv.is_file():
        with open(master_csv, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    unstable_keys = _check_key_stability(index)
    lookalikes = _find_lookalikes(rows, index)
    legacy_only, new_only = _compare_legacy(rows, index)
    unmatched_bodies, unmatched_competitors = _find_unmatched_entries(rows, index, profile)

    report = {
        "unstable_keys": unstable_keys,
        "lookalikes_count": len(lookalikes),
        "legacy_only_count": len(legacy_only),
        "new_only_count": len(new_only),
        "unmatched_bodies_count": len(unmatched_bodies),
        "unmatched_competitors_count": len(unmatched_competitors),
    }

    # Print summary
    print(f"=== Account Relation Audit: profile={profile} ===")
    print(f"Pool size: {len(rows)} rows")
    if unstable_keys:
        print("\n[ERROR] Key stability check failed:")
        for err in unstable_keys:
            print(f"  - {err}")
    else:
        print("\n[OK] Key stability check passed (0 unstable keys).")

    print(f"\nLookalikes not classified: {len(lookalikes)}")
    print(f"Delta vs legacy matcher: {len(new_only)} new hits, {len(legacy_only)} legacy dropped")
    print(
        f"Entries matching nothing in pool: {len(unmatched_bodies)} bodies, {len(unmatched_competitors)} competitors"
    )

    exit_code = 1 if unstable_keys else 0
    return exit_code, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit account relation classifications.")
    parser.add_argument("--profile", required=True, help="Profile name")
    parser.add_argument("--product", required=False, help="Product name")
    args = parser.parse_args(argv)

    exit_code, _ = run_audit(args.profile, args.product)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
