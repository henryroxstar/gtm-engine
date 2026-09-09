#!/usr/bin/env python3
"""§R17 — a provider's RATE lives in exactly one module, and is pointed at from everywhere else.

A rate is a number bound to a unit of spend: "0.865 credits/second", "~23 credits per render",
"1 credit/page". Unlike a balance it does not change on every call, which is what makes it
tempting to write into prose — and what makes a stale one so durable once it is there.

WHAT THIS COSTS WHEN IT IS NOT ENFORCED. Between 2026-08-27 and 2026-09-07 a HeyGen rate was
restated in seven places: a skill manifest, a skill body, a pack node prompt, three code
rationale comments, and two TENANT VOICE NOTES. When the underlying model turned out to be
per-second rather than per-job, five of those were wrong, and two of the wrong ones were tenant
knowledge the brain loads at runtime and writes copy against. No gate saw any of it: the
neighbouring lint (`test_no_stale_provider_facts_in_bodies.py`) polices a hardcoded credit
BALANCE and deliberately permits a rate card, because a rate is what a cost estimate is built
from. It is right to permit them. This rule governs WHERE they may live, not whether they exist.

SCOPE — the surfaces the BRAIN reads and acts on:

  * `plugin/skills/*/body_template.md`  — the executed procedure
  * `gtm_core/skills/*.py`              — manifest prose, which becomes SKILL.md frontmatter
  * `packs/**/*.toml`                   — node prompts, fed to the agent verbatim
  * `profiles/**`, `identity/**`        — tenant knowledge loaded into a run

Deliberately NOT `docs/` or `tests/`: `docs/prds/` and `docs/archive/` are dated point-in-time
records where a superseded figure is the *content*, and a test asserting on a constant is
already derived rather than typed. Code outside `gtm_core/skills/` is read by people, not by
the agent, and a comment there cannot steer a run.

RELATIONSHIP TO §R15 (`manifest_prose_check.py`), which shares the manifest surface. The two are
PARTITIONED, not stacked: §R15 owns dates, trial counts ("12 of 12 jobs") and the length budget;
this rule owns rates, everywhere. `_MEASUREMENT` in that module had its rate alternative removed
when this one was written, so a single string is reported by exactly one of them. Two rules
firing on one line teaches people the output is noise, and then the real findings go unread.

Stdlib-only, so it runs at pre-commit before any sync.

Layers (docs/RULES.md §R17): pre-commit, CI, pytest, and the release export.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name("provider_rate_allow.txt")

#: The files that OWN a rate. A rate literal here is the single source everything else points at.
#: Keep this list short and specific: every entry is a place a figure can go stale, and the whole
#: point of the rule is that there is exactly one such place per provider.
RATE_HOMES = frozenset(
    {
        "gtm_core/heygen_cost.py",  # HeyGen: per-second band, engine-dependent
        "gtm_core/video_preflight.py",  # Reap: published per-billed-minute rate card
        "agent/mcp/apollo/server.py",  # Apollo: 1 credit/page, stated at the connector
        "agent/mcp/apollo/__init__.py",
    }
)

#: A figure bound to credits (or dollars) AND a per-unit denominator. A bare "23 credits" is a
#: COST, not a rate, and is not matched — the denominator is what makes it a rule you can apply
#: forward, and therefore the thing that gets copied.
_RATE = re.compile(
    r"[~≈]?\$?\d[\d,]*\.?\d*\s*(?:premium\s+|media\s+|ai\s+)?credits?\s*(?:/|per\s+)\s*"
    # one optional qualifier: "1 media credit per BILLED minute" is the published Reap form.
    r"(?:billed\s+|finished\s+|delivered\s+|output\s+|source\s+)?"
    r"(?:second|sec|minute|min|render|job|video|frame|pass|call|page|image|clip)\b"
    r"|[~≈]?\d[\d,]*\.?\d*\s*c/s\b"
    r"|[~≈]?\$\d[\d,]*\.?\d*\s*(?:/|per\s+)\s*(?:second|sec|minute|min|render|job|video)\b",
    re.IGNORECASE,
)


def load_allowlist() -> set[str]:
    """`<path>` entries, each carrying a dated reason on the same line."""
    if not ALLOWLIST.is_file():
        return set()
    out: set[str] = set()
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.add(line.split()[0])
    return out


def scanned_files(repo: Path):
    """Every brain-read prose surface, in a stable order."""
    yield from sorted((repo / "plugin" / "skills").glob("*/body_template.md"))
    yield from sorted((repo / "gtm_core" / "skills").glob("*.py"))
    yield from sorted((repo / "packs").rglob("*.toml"))
    for root in ("profiles", "identity"):
        base = repo / root
        if base.is_dir():
            yield from sorted(
                p
                for p in base.rglob("*")
                if p.is_file() and p.suffix in {".md", ".toml"} and "__pycache__" not in p.parts
            )


def scan(repo: Path) -> list[str]:
    allow = load_allowlist()
    findings: list[str] = []
    for path in scanned_files(repo):
        rel = path.relative_to(repo).as_posix()
        if rel in RATE_HOMES or rel in allow:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in _RATE.finditer(line):
                findings.append(f"  {rel}:{i}: {m.group(0).strip()!r}")
    return findings


def main(argv: list[str] | None = None) -> int:
    repo = Path(argv[0]).resolve() if argv else REPO
    findings = scan(repo)
    if not findings:
        return 0
    print(
        "✗ a provider RATE is stated outside the module that owns it.\n"
        "  A rate copied into prose is a rate that goes stale silently — one HeyGen figure\n"
        "  reached seven files, and when the billing model turned out to be different five of\n"
        "  them were wrong, including two tenant voice notes the brain writes copy against.\n"
        "  Point at the owning module instead of restating the number.\n"
    )
    for f in findings[:40]:
        print(f)
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    print(f"\n  Rate homes: {', '.join(sorted(RATE_HOMES))}")
    print(f"  Deliberate exception? Add the path to {ALLOWLIST.name} with a dated reason.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
