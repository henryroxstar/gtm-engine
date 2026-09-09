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

Third-party names (2026-09-05): the leak class none of the rules above can see. A bare
company name has no shape — a customer's name in a docstring is just a word — so six of nine
identity leaks that reached the public repo were caught, if at all, by a human recalling
the name at release time. tests/lint/third_party_roster.py derives the roster from the
tenant data instead (account folders, curated case-study lists) and this module applies
it. See that module for how the keys are made non-colliding.

Bare domains (2026-09-03): real ORGANISATIONS leak the same way real people do — a
prospect's parent/subsidiary domain pair, a bank's brand-vs-legal domain, a health
system's .edu — as bare domains in docstrings, comments and fixture rows, with no email,
no URL and no tenant token for any other rule to see. Nineteen of them sat on the shipped
surface at the 2026-09-03 identity read, every one below every gate. On CODE surfaces a
domain is a fixture or an example, never a citation, so it must be provably non-real:
RFC-2606 reserved, a listed fictional domain, or a listed vendor/infra domain. docs/ and
skill prose cite vendor guides and published benchmarks by design and stay with the
identity read. In a .py file only string literals and comments are read (tokenize), so
a dotted attribute chain whose last label happens to spell a TLD can never match.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_party_roster  # noqa: E402  (same directory, stdlib-only, no package)

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
# Self-referential files: the allowlist names domains on purpose, the checker's own tests
# hold rule-triggering samples, and the roster module documents the rule it implements.
SELF_REFERENTIAL = {
    "pii_allowlist.txt",
    # The tenant denylist IS the roster of names, by design, and scripts/oss-export.sh
    # excludes it from the carve — the same carve-out debrand_check.sh already documents.
    "safe_share_denylist.txt",
    # Same shape, one rule over: every line of the cross-tenant allowlist is a path under
    # profiles/<tenant>/ plus the foreign slug it may name, so the file cannot state its
    # rule without naming tenants — and the reason column names the RELATIONSHIP (whose
    # vendor, whose account), which is the third-party name this rule fires on. Excluded
    # from the carve by scripts/oss-export.sh, and from debrand by .debrandignore.
    "cross_tenant_allow.txt",
    "test_pii_check.py",
    # gtm_core.fictionalize's tests must feed it real-SHAPED inputs to prove it strips
    # them — an unlisted mail domain, a plain E.164 number. Those inputs are invented
    # (see that file's note); the values it EMITS are asserted to pass this checker.
    "test_fictionalize.py",
    "third_party_roster.py",
    "third_party_digest.txt",
    "test_third_party_roster.py",
}

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
# Bare-domain rule. A domain-shaped token that is NOT part of an email (preceded by `@`),
# a URL (preceded by `/`) or a longer token. Only TLDs a real prospect/customer would
# actually carry; `.app`/`.dev` are deliberately absent — in Python they are attribute
# access far more often than domains, and a real organisation on them is rare.
BARE_DOMAIN_RE = re.compile(
    r"(?<![@/\w.-])((?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|ai|io|co|sg|au|uk|de))(?![\w-])",
    re.IGNORECASE,
)
# Where a domain is data, not a citation: the engine, the tests, the schemas, the pack
# graphs, the template profile, and the code files (not the prose) under plugin/.
BARE_DOMAIN_DIRS = frozenset(
    {
        "gtm_core",
        "agent",
        "backend",
        "cockpit",
        "mcp_server",
        "telegram",
        "tests",
        "schemas",
        "packs",
    }
)
BARE_DOMAIN_PLUGIN_SUFFIXES = frozenset({".py", ".toml", ".json"})


# Where a third-party NAME is a leak. This is the carve's own shape: every directory the
# export copies. docs/ is excluded — shipped docs cite vendors, standards bodies and
# published research by name as content, and that citation surface stays with the human
# identity read rather than a gate that would fire on every reference.
THIRD_PARTY_DIRS = frozenset(
    {
        "gtm_core",
        "agent",
        "backend",
        "cockpit",
        "mcp_server",
        "telegram",
        "tests",
        "schemas",
        "packs",
        "plugin",
    }
)


def in_third_party_scope(path: Path) -> bool:
    parts = set(path.parts)
    if parts & THIRD_PARTY_DIRS:
        return True
    return "profiles" in parts and "_template" in parts


def load_roster() -> tuple[re.Pattern[str] | None, set[str]]:
    """(matcher, digests). Derived from live tenant data when the account tree is present;
    otherwise the committed digests, so CI — which checks out no content/ — enforces the
    same roster rather than the partial one `profiles/` alone would yield."""
    if third_party_roster.has_tenant_data(ROOT):
        return third_party_roster.matcher(third_party_roster.derive_keys(ROOT)), set()
    return None, third_party_roster.load_digests()


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


def in_bare_domain_scope(path: Path) -> bool:
    """A code surface, where a domain is a fixture or example rather than a citation."""
    parts = set(path.parts)
    if parts & BARE_DOMAIN_DIRS:
        return True
    if "profiles" in parts and "_template" in parts:
        return True
    return "plugin" in parts and path.suffix.lower() in BARE_DOMAIN_PLUGIN_SUFFIXES


def data_regions(path: Path, text: str) -> list[tuple[int, str]]:
    """``(line, text)`` for the parts of a file where a domain is DATA.

    For Python that is string literals and comments only — read through ``tokenize`` so an
    attribute chain whose last label spells a TLD is code and never a candidate. Every
    other file type is data throughout. A file tokenize cannot parse falls back to whole lines."""
    if path.suffix.lower() != ".py":
        return list(enumerate(text.splitlines(), 1))
    import io
    import tokenize

    wanted = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    out: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in wanted:
                out.extend((tok.start[0] + i, seg) for i, seg in enumerate(tok.string.splitlines()))
    except (tokenize.TokenError, SyntaxError):
        return list(enumerate(text.splitlines(), 1))
    return out


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


def scan_file(
    path: Path,
    text: str,
    domains: set[str],
    addresses: list[str],
    url_domains: set[str],
    roster: re.Pattern[str] | None = None,
    digests: set[str] | None = None,
) -> list[tuple[object, int, str]]:
    """Every finding in one file, as ``(display path, line, what)``."""
    findings: list[tuple[object, int, str]] = []
    rel = display_path(path)
    # Fixture surface = tests/. docs/ and plugin/ cite real external sources by
    # design (vendor guides, standards bodies); a fixture has no such excuse.
    in_tests = "tests" in path.parts
    in_bare = in_bare_domain_scope(path)
    for lineno, line in enumerate(text.splitlines(), 1):
        for addr in EMAIL_RE.findall(line):
            if not is_allowed(addr, domains, addresses):
                findings.append((rel, lineno, addr))
        for url in LINKEDIN_RE.findall(line):
            if not is_allowed_literal(url, addresses):
                findings.append((rel, lineno, url))
        for phone in PHONE_RE.findall(line):
            if not FICTIONAL_PHONE_RE.search(phone) and not is_allowed_literal(phone, addresses):
                findings.append((rel, lineno, phone))
        if in_tests or in_bare:
            for host in URL_HOST_RE.findall(line):
                if not is_allowed_url_host(host, domains, url_domains):
                    findings.append((rel, lineno, f"url-host {host}"))
    if in_bare:
        for lineno, seg in data_regions(path, text):
            for host in BARE_DOMAIN_RE.findall(seg):
                if not is_allowed_url_host(host, domains, url_domains):
                    findings.append((rel, lineno, f"bare-domain {host}"))
    if (roster or digests) and in_third_party_scope(path):
        findings.extend(_third_party_findings(path, text, rel, roster, digests))
    return findings


def _third_party_findings(
    path: Path,
    text: str,
    rel: object,
    roster: re.Pattern[str] | None,
    digests: set[str] | None,
) -> list[tuple[object, int, str]]:
    """Third-party-name findings for one in-scope file.

    Same data-region rule as bare domains: in Python only literals and comments, so a
    variable or attribute that happens to spell a company name is never a finding.
    """
    out: list[tuple[object, int, str]] = []
    for lineno, seg in data_regions(path, text):
        hits = roster.findall(seg) if roster is not None else _digest_hits(seg, digests or set())
        out.extend((rel, lineno, f"third-party name {hit!r}") for hit in hits)
    return out


# Digest mode has no pattern to match with, so it enumerates candidates instead: every
# contiguous run of 1..MAX_KEY_WORDS words on a line, each hashed and looked up. Testing
# only the greedy span would miss the single-word key inside it ("the <name> deck was" is
# not a key; "<name>" is) — the failure a positive control caught before this shipped.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
MAX_KEY_WORDS = 5


def _digest_hits(seg: str, digests: set[str]) -> list[str]:
    words = [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(seg)]
    out: list[str] = []
    for i in range(len(words)):
        for n in range(1, min(MAX_KEY_WORDS, len(words) - i) + 1):
            span = words[i : i + n]
            # Only a run joined by single spaces, hyphens or underscores can be one key.
            if any(
                seg[span[k][2] : span[k + 1][1]] not in (" ", "-", "_")
                for k in range(len(span) - 1)
            ):
                break
            norm = " ".join(w[0].lower() for w in span)
            if third_party_roster.digest(norm) in digests:
                out.append(seg[span[0][1] : span[-1][2]])
    return out


def main(argv: list[str]) -> int:
    domains, addresses, url_domains = load_allowlist()
    roster, digests = load_roster()
    findings: list[tuple[object, int, str]] = []

    for path in iter_files(argv):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: the export's binary sweep covers those
        findings.extend(scan_file(path, text, domains, addresses, url_domains, roster, digests))

    if not findings:
        return 0

    print(
        "✗ possible real-person or real-organisation identity in the source surface"
        " (email / LinkedIn / phone / URL host / bare domain / third-party name):\n"
    )
    for rel, lineno, addr in findings[:40]:
        print(f"  {rel}:{lineno}: {addr}")
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    print(
        "\n  Test fixtures, docstrings and comments must not name real people or real"
        "\n  organisations. Replace with a fictional one on an RFC-2606 domain"
        "\n  (someone@acme.example, acme.example/us), preserving whatever string shape the"
        "\n  test actually asserts on. A vendor API, public-infrastructure or standards-body"
        "\n  domain belongs in [fixture-url-domains]; a legacy fictional fixture on a real TLD"
        "\n  in [fictional-domains]."
        "\n\n  A `third-party name` finding is a real account, customer or case study from"
        "\n  the tenant data appearing in shipped text. Keep the property the code is about"
        '\n  and drop the identity: "the Northwind deck" becomes "a customer deck".'
        f"\n\n  If a value genuinely identifies nobody, add it to {ALLOWLIST.name}"
        "\n  with a comment saying why. Never allowlist a free-mail domain wholesale."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
