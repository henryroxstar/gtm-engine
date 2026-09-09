#!/usr/bin/env python3
"""Block one tenant's profile bundle from naming another tenant.

WHAT THIS IS FOR. `profiles/<tenant>/` is the unit that gets handed to a customer — the
whole point of the bundle is that it is one company's knowledge and nothing else. But it is
also a working tree the operator edits in place, and operator annotations accumulate: a
comment reconciling this brand kit against a sibling's, a note that two tenants share a
posting handle, a pointer at another tenant's config file. None of that is wrong in the
private repo. All of it travels the moment the folder is copied.

WHAT IT COST, ONCE, CONCRETELY. On 2026-09-08 a colleague at one tenant was given a clean
public engine checkout plus that tenant's profile folder, and his agent started asking him
about two OTHER tenants. The folder held four annotations naming a sibling tenant (in
`PROFILE.md` and `knowledge/BRAND.toml`) and two naming a third (in
`knowledge/syften-filters.json`, one of which reads "or any other profile sharing this
account"). Each was a dangling reference in his tree — a path to a file he did not have —
so the agent did the reasonable thing and asked. Nothing was malicious and nothing was
secret; the bundle was simply not portable, and no gate said so.

WHY THE EXISTING GATES CANNOT SEE IT. `debrand_check.sh` proves OUR name is gone from the
carve. `pii_check.py` + `third_party_roster.py` prove third-party identities are absent
from the SHIPPABLE surface — and `profiles/` is explicitly out of their scope, because
holding real customer knowledge is what a profile is FOR. So the one directory whose whole
job is to be tenant-specific is the one directory nothing checks for being tenant-PURE.
This is the same failure class CLAUDE.md already names one layer up ("the de-brand lint
cannot catch this — it proves *our* name is gone, never someone else's"), turned inward.

THE RULE. Inside `profiles/<T>/`, no other tenant's slug may appear — as a path
(`profiles/<other>/`, `content/<other>/`) or as a bare word. `<T>` referring to itself is
fine and is the common case. Placeholders (`profiles/<active>/`, `content/<profile>/`)
never match, because a real slug has no angle brackets.

SCOPE, AND WHY `content/` IS NOT IN IT. Only files under `profiles/<T>/` are scanned.
`content/` is gitignored runtime state, so a commit-time gate cannot see it — but more
importantly a tenant slug there is often a legitimate ACCOUNT of a different tenant: one
tenant we sell for is also an account another tenant sells to, and
`content/<A>/accounts/<B>/` is correct, not a leak. That collision is exactly why the two
trees get different treatment.

RESIDUALS THIS DOES NOT COVER, on purpose:
  * PRODUCT names. A sibling's product can be named without naming the sibling. Keying on
    product slugs is unusable for the reason `scripts/oss-export.sh` already documents for
    its own token list: a one-word product name is routinely an ordinary English noun, and
    a gate that fires on the noun is a gate people switch off. A product name reaching a
    foreign bundle stays with the human read.
  * Dangling paths OUTSIDE the repo (`~/Developer/…`, a sibling checkout). Those confuse a
    recipient the same way, but they are a portability defect rather than a tenant one.
  * Slugs that are ordinary English words (`personal`) are skipped by the bare-word rule —
    a gate that cries wolf is a gate people learn to skip. The PATH rule still covers them,
    and a path form is unambiguous.

Usage:
    python tests/lint/cross_tenant_check.py [paths...]   # defaults to profiles/
    python tests/lint/cross_tenant_check.py --list-tenants

Exit 0 clean, 1 on a finding. Stdlib only — it runs in pre-commit before any sync.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"
CONTENT = ROOT / "content"
ALLOWLIST = Path(__file__).resolve().parent / "cross_tenant_allow.txt"

# Slugs that are also ordinary words, so a bare-word match says nothing. They keep the
# PATH rule (`content/personal/` is unambiguous); only the bare-word rule skips them.
NON_DISTINCTIVE = frozenset(
    {
        "personal",
        "template",
        "tmp",
        "test",
        "shared",
        "common",
        "default",
        "example",
        "sample",
        "demo",
        "staging",
        "archive",
    }
)

# Directories under profiles/ and content/ that are not tenants at all.
NOT_A_TENANT = frozenset({"template", "tmp"})

# This file and its test discuss the rule and therefore quote slugs by necessity.
SELF_REFERENTIAL = frozenset(
    {
        "tests/lint/cross_tenant_check.py",
        "tests/lint/test_cross_tenant_check.py",
        "tests/lint/cross_tenant_allow.txt",
    }
)

SKIP_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".pdf",
        ".mp4",
        ".mov",
        ".mp3",
        ".wav",
        ".zip",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".ico",
        ".pyc",
    }
)


def tenants() -> set[str]:
    """Every tenant slug, derived from the two trees that hold one directory per tenant.

    Never typed: a tenant onboarded after this file was written is covered automatically,
    which is the failure mode `scripts/oss-export.sh`'s hand-maintained TOKENS list hit
    twice (a profile added after the list was last edited was invisible to it).
    """
    found: set[str] = set()
    for tree in (PROFILES, CONTENT):
        if not tree.is_dir():
            continue
        for child in tree.iterdir():
            name = child.name
            if child.is_dir() and not name.startswith((".", "_")) and name not in NOT_A_TENANT:
                found.add(name)
    return found


def load_allowlist() -> set[tuple[str, str]]:
    """`<repo-relative path>:<slug>` pairs a human has signed off on."""
    allowed: set[tuple[str, str]] = set()
    if not ALLOWLIST.exists():
        return allowed
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and ":" in line:
            path, _, slug = line.rpartition(":")
            allowed.add((path.strip(), slug.strip().lower()))
    return allowed


def patterns(slug: str) -> list[tuple[str, re.Pattern[str]]]:
    """The two rules, as named patterns, for one foreign slug."""
    esc = re.escape(slug)
    rules = [("path", re.compile(rf"\b(?:profiles|content)/{esc}\b", re.IGNORECASE))]
    if slug.lower() not in NON_DISTINCTIVE:
        rules.append(("name", re.compile(rf"\b{esc}\b", re.IGNORECASE)))
    return rules


def iter_files(argv: list[str]) -> list[tuple[Path, str]]:
    """(file, owning tenant) for every scannable file under a tenant directory."""
    roots = [Path(a).resolve() for a in argv] if argv else [PROFILES]
    out: list[tuple[Path, str]] = []
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if path.suffix.lower() in SKIP_SUFFIXES or "__pycache__" in path.parts:
                continue
            try:
                owner = path.relative_to(PROFILES).parts[0]
            except ValueError:
                continue  # outside profiles/ — not a bundle file
            if path != PROFILES / owner:  # a file directly in profiles/ owns no tenant
                out.append((path, owner))
    return out


def scan(
    path: Path, owner: str, text: str, foreign: set[str], allowed: set[tuple[str, str]]
) -> list[tuple[str, int, str, str, str]]:
    rel = path.relative_to(ROOT).as_posix()
    if rel in SELF_REFERENTIAL:
        return []
    findings = []
    for slug in sorted(foreign - {owner}):
        if (rel, slug.lower()) in allowed:
            continue
        seen: set[int] = set()
        for kind, pat in patterns(slug):
            for lineno, line in enumerate(text.splitlines(), 1):
                # Every match is reported, not just the first in the file: the fix is per
                # ANNOTATION, and a lint that reveals the next one only after you fix this
                # one turns a single triage into N rounds. `seen` keeps the path rule and
                # the bare-word rule from both claiming the same line.
                if lineno not in seen and pat.search(line):
                    seen.add(lineno)
                    findings.append((rel, lineno, slug, kind, line.strip()[:120]))
    return findings


def main(argv: list[str]) -> int:
    if "--list-tenants" in argv:
        print("\n".join(sorted(tenants())))
        return 0

    foreign, allowed = tenants(), load_allowlist()
    findings: list[tuple[str, int, str, str, str]] = []
    for path, owner in iter_files([a for a in argv if not a.startswith("-")]):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan(path, owner, text, foreign, allowed))

    if not findings:
        return 0

    owners = sorted({rel.split("/")[1] for rel, *_ in findings})
    print(
        f"✗ {len(findings)} cross-tenant reference(s) in {len(owners)} bundle(s)"
        f" ({', '.join(owners)}) — those bundles are not portable:\n"
    )
    for rel, lineno, slug, kind, line in findings[:60]:
        print(f"  {rel}:{lineno}: [{kind}] {slug}\n      {line}")
    if len(findings) > 60:
        print(f"  ... and {len(findings) - 60} more")
    print(
        "\n  profiles/<T>/ is the unit handed to a customer. Keep the FACT and drop the"
        '\n  sibling\'s identity: "the acme kit uses X" becomes "another profile on this'
        '\n  account uses X"; a pointer at profiles/<other>/<file> becomes a description of'
        "\n  what that file does. A cross-tenant fact that genuinely has to name the tenant"
        "\n  belongs in docs/ or PENDING.md, not in a bundle."
        f"\n\n  If a reference truly identifies no other tenant, add it to {ALLOWLIST.name}"
        "\n  as `<path>:<slug>` with a comment saying why."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
