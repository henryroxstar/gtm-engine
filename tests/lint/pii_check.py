#!/usr/bin/env python3
"""Block a real person's contact details from entering the shippable source surface.

The de-brand lint proves the TENANT's name is gone. It says nothing about anyone else,
and the recurring leak is exactly that: a real contact from a live prospecting run kept
as a test fixture because it was the genuine messy-data case the parser had to handle.
Two such leaks reached the public repo (v0.7.0 prospect fixtures, v0.8.0 merge-hygiene
fixtures) with every automated gate green.

This runs at COMMIT time, so a real contact never lands in the repo at all — the export
gate in scripts/oss-export.sh is the backstop, not the first line of defence.

Scope: the shippable source surface only. `content/` and `profiles/` legitimately hold
real customer PII (that is the product), and are never carved into a public cut.

Usage:
    python tests/lint/pii_check.py [paths...]   # defaults to the whole source surface

Exit 0 clean, 1 on a finding. Stdlib only — it runs in pre-commit before any sync.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).resolve().parent / "pii_allowlist.txt"

# The shippable surface. content/ + profiles/ hold real PII by design and are excluded.
SOURCE_DIRS = (
    "gtm_core",
    "agent",
    "backend",
    "cockpit",
    "mcp_server",
    "plugin",
    "tests",
    "schemas",
    "docs",
    "scripts",
)
SCAN_SUFFIXES = {".py", ".md", ".json", ".toml", ".csv", ".yaml", ".yml", ".txt", ".sh", ".html"}
# Binary/vendored trees.
EXCLUDE_PARTS = {".venv", "__pycache__", "node_modules", ".git", ".pytest_cache"}
# Self-referential files: the allowlist names domains on purpose, and the checker's own
# tests must hold rule-triggering samples to prove the rule fires. Those samples are
# synthetic by construction (see the note in test_pii_check.py) — never real contacts.
SELF_REFERENTIAL = {"pii_allowlist.txt", "test_pii_check.py"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A personal LinkedIn profile identifies one named human — there is no "fictional" form of
# it, so ANY occurrence outside profiles//content/ is a finding. Company pages (/company/)
# are org-level and legitimate in ICP/market docs, so they are not matched here.
LINKEDIN_RE = re.compile(r"linkedin\.com/in/[A-Za-z0-9._%-]+", re.IGNORECASE)
# Real phone numbers arrive with prospect rows (Apollo/RocketReach return E.164). Matched
# ONLY in unmistakable phone form — a leading '+' or a parenthesised NANP area code.
# A looser pattern matched UUIDs, run ids and date stamps ("11111111-1111", "20260614-0001"),
# and a gate that cries wolf is a gate people learn to skip.
PHONE_RE = re.compile(r"\+\d[\d\s.-]{7,17}\d\b|\(\d{3}\)\s*\d{3}[-.\s]\d{4}\b")
# NANP 555-01xx is the reserved fictional range; plus the obvious repeated/sequential fakes.
FICTIONAL_PHONE_RE = re.compile(r"555[\s.-]?01\d{2}|(\d)\1{6,}|1234567|5551234")
# RFC-2606 reserved TLDs + example.com and any subdomain of it.
RESERVED_RE = re.compile(
    r"^(.+\.)?(example|invalid|test|localhost)$|^([a-z0-9-]+\.)*example\.(com|org|net)$",
    re.IGNORECASE,
)
# Single-letter placeholder hosts: a@x.com, b@y.com.
PLACEHOLDER_RE = re.compile(r"^[a-z]\.(com|net|org)$", re.IGNORECASE)
# URL hosts in test fixtures. The 2026-08-11 identity read found a real practitioner's
# blog domain (with their verbatim published words) as a parser fixture — no email, no
# tenant token, so every automated gate passed it. Fixtures are where real identities
# sneak in, so inside tests/ a URL host must be provably non-real: reserved, fictional,
# or an allowlisted vendor/infra domain ([fixture-url-domains]). Dotless hosts
# (http://fake-renderer) are internal stand-ins, not real-world domains — skipped.
URL_HOST_RE = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", re.IGNORECASE)


def load_allowlist() -> tuple[set[str], list[str], set[str]]:
    """Parse the shared allowlist into
    (fictional domain labels, known-good fragments, fixture URL domains)."""
    domains: set[str] = set()
    addresses: list[str] = []
    url_domains: set[str] = set()
    section = None
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "fictional-domains":
            domains.add(line.lower())
        elif section == "known-good-addresses":
            addresses.append(line.lower())
        elif section == "fixture-url-domains":
            url_domains.add(line.lower())
    return domains, addresses, url_domains


def is_allowed_literal(value: str, addresses: list[str]) -> bool:
    """Allowlist match for non-email findings (LinkedIn URLs, phone numbers)."""
    low = value.lower()
    return any(frag in low for frag in addresses)


def is_allowed(addr: str, domains: set[str], addresses: list[str]) -> bool:
    low = addr.lower()
    if any(frag in low for frag in addresses):
        return True
    host = low.split("@", 1)[1]
    if RESERVED_RE.match(host) or PLACEHOLDER_RE.match(host):
        return True
    # The registrable label: "cascade" in "mail.cascade.example" / "cascade.com".
    return any(label in domains for label in host.split("."))


def is_allowed_url_host(host: str, domains: set[str], url_domains: set[str]) -> bool:
    low = host.lower().strip(".")
    if RESERVED_RE.match(low) or PLACEHOLDER_RE.match(low):
        return True
    # Suffix match so one entry covers subdomains: "explorium.ai" allows
    # "share.explorium.ai" and the suffix-confusion negative fixtures stay writable
    # on .example ("share.explorium.ai.evil.example" ends in .example → reserved).
    if any(low == d or low.endswith("." + d) for d in url_domains):
        return True
    return any(label in domains for label in low.split("."))


def iter_files(paths: list[str]) -> list[Path]:
    """Expand args into scannable files. An argument may be a file OR a directory —
    the export passes the whole carve root, pre-commit passes individual staged files."""
    if paths:
        cands = []
        for p in paths:
            path = Path(p)
            if path.is_dir():
                cands.extend(f for f in path.rglob("*") if f.is_file())
            else:
                cands.append(path)
    else:
        cands = [f for d in SOURCE_DIRS for f in (ROOT / d).rglob("*") if f.is_file()]
    out = []
    for f in cands:
        if not f.is_file() or f.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if EXCLUDE_PARTS & set(f.parts) or f.name in SELF_REFERENTIAL:
            continue
        out.append(f)
    return out


def display_path(path: Path) -> Path | str:
    """Repo-relative when inside the repo; otherwise as given (e.g. a carve directory)."""
    try:
        return path.relative_to(ROOT) if path.is_absolute() else path
    except ValueError:
        return path


def main(argv: list[str]) -> int:
    domains, addresses, url_domains = load_allowlist()
    findings: list[tuple[object, int, str]] = []

    for path in iter_files(argv):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: the export's binary sweep covers those
        rel = display_path(path)
        # Fixture surface = tests/. docs/ and plugin/ cite real external sources by
        # design (vendor guides, standards bodies); a fixture has no such excuse.
        in_tests = "tests" in path.parts
        for lineno, line in enumerate(text.splitlines(), 1):
            for addr in EMAIL_RE.findall(line):
                if not is_allowed(addr, domains, addresses):
                    findings.append((rel, lineno, addr))
            for url in LINKEDIN_RE.findall(line):
                if not is_allowed_literal(url, addresses):
                    findings.append((rel, lineno, url))
            for phone in PHONE_RE.findall(line):
                if not FICTIONAL_PHONE_RE.search(phone) and not is_allowed_literal(
                    phone, addresses
                ):
                    findings.append((rel, lineno, phone))
            if in_tests:
                for host in URL_HOST_RE.findall(line):
                    if not is_allowed_url_host(host, domains, url_domains):
                        findings.append((rel, lineno, f"url-host {host}"))

    if not findings:
        return 0

    print(
        "✗ possible real-person PII in the source surface"
        " (email / LinkedIn / phone / fixture URL host):\n"
    )
    for rel, lineno, addr in findings[:40]:
        print(f"  {rel}:{lineno}: {addr}")
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    print(
        "\n  Test fixtures and docstrings must not name real people. Replace with a"
        "\n  fictional contact on an RFC-2606 domain (someone@acme.example), preserving"
        "\n  whatever string shape the test actually asserts on."
        f"\n\n  If an address genuinely identifies nobody, add it to {ALLOWLIST.name}"
        "\n  with a comment saying why. Never allowlist a free-mail domain wholesale."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
