"""Contract: the two SHIPPING_DOCS allowlists stay in sync, and every doc a skill cites is on them.

`docs/*.md` files that a skill body instructs the agent to read must ship in the public OSS carve,
or the carve is silently broken for whoever relies on that skill. Two independent shell scripts
each carry their own copy of the shipping-docs list — `tests/lint/debrand_check.sh` (which scans
them for tenant tokens in `--release` mode) and `scripts/oss-export.sh` (which copies them into the
export). Nothing has ever asserted the two agree; `tests/lint/pii_allowlist.txt` names this exact
drift as having "bitten repeatedly". The export's §6 doc-reference gate would otherwise catch a
missing file, but only at release time — this test moves that failure into every CI run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: `_oss_export_shipping_docs()` reads `scripts/oss-export.sh`, which the carve replaces
#: with `bootstrap.sh` alone — the three tests that call it have nothing to compare
#: against inside a public cut, so they carry `private_tree`. The other two read only
#: `debrand_check.sh` and `plugin/skills/`, both of which DO ship, so they stay live
#: there: a skill citing an unshipped doc is exactly the release-time hard failure this
#: module exists to move earlier, and it is worth catching in the carve too.

REPO = Path(__file__).resolve().parents[2]
DEBRAND_SCRIPT = REPO / "tests" / "lint" / "debrand_check.sh"
OSS_EXPORT_SCRIPT = REPO / "scripts" / "oss-export.sh"

_ARRAY_RE = re.compile(r"SHIPPING_DOCS=\((.*?)\n\)", re.DOTALL)


def _strip_comments(block: str) -> str:
    return "\n".join(line for line in block.splitlines() if not line.strip().startswith("#"))


def _debrand_shipping_docs() -> set[str]:
    text = DEBRAND_SCRIPT.read_text(encoding="utf-8")
    match = _ARRAY_RE.search(text)
    assert match, f"could not find a SHIPPING_DOCS=( ... ) array in {DEBRAND_SCRIPT}"
    block = _strip_comments(match.group(1))
    return set(re.findall(r'"\$ROOT/docs/([A-Za-z0-9_./-]+)"', block))


def _oss_export_shipping_docs() -> set[str]:
    text = OSS_EXPORT_SCRIPT.read_text(encoding="utf-8")
    match = _ARRAY_RE.search(text)
    assert match, f"could not find a SHIPPING_DOCS=( ... ) array in {OSS_EXPORT_SCRIPT}"
    block = _strip_comments(match.group(1))
    return set(block.split())


#: `docs/prds/**` is documented PRIVATE and never copied into the OSS carve (its §6 must-not-ship
#: invariant) — a skill citing one is a separate, real question (does that skill itself ship
#: publicly?) that this test does not attempt to answer. Excluded here so this test stays a
#: precise regression guard for the SHIPPING_DOCS registration this PR performs, not a repo-wide
#: audit.
_PRIVATE_DOC_PREFIXES = ("prds/",)


def _docs_cited_by_skills() -> set[str]:
    """Every `docs/<name>.md` a skill body_template.md tells the agent to read (relative to docs/),
    excluding the documented-private `docs/prds/**` tree."""
    cited: set[str] = set()
    for body in (REPO / "plugin" / "skills").glob("*/body_template.md"):
        text = body.read_text(encoding="utf-8")
        for m in re.finditer(r"\bdocs/([A-Za-z0-9_./-]+\.md)\b", text):
            rel = m.group(1)
            if not rel.startswith(_PRIVATE_DOC_PREFIXES):
                cited.add(rel)
    return cited


@pytest.mark.private_tree  # compares against the un-carved scripts/oss-export.sh
def test_both_shipping_docs_arrays_are_nonempty():
    assert len(_debrand_shipping_docs()) > 10
    assert len(_oss_export_shipping_docs()) > 10


@pytest.mark.private_tree  # compares against the un-carved scripts/oss-export.sh
def test_shipping_docs_arrays_agree():
    debrand = _debrand_shipping_docs()
    oss_export = _oss_export_shipping_docs()
    only_debrand = debrand - oss_export
    only_oss_export = oss_export - debrand
    assert not only_debrand and not only_oss_export, (
        f"SHIPPING_DOCS drift between {DEBRAND_SCRIPT.name} and {OSS_EXPORT_SCRIPT.name}: "
        f"only in debrand_check.sh: {sorted(only_debrand)}; "
        f"only in oss-export.sh: {sorted(only_oss_export)}"
    )


def test_every_shipping_doc_exists_on_disk():
    for rel in _debrand_shipping_docs():
        path = REPO / "docs" / rel
        assert path.is_file(), f"SHIPPING_DOCS entry {rel!r} does not exist at {path}"


#: Escape hatch for a skill citing a doc that is not in ALLOW_FILES/SHIPPING_DOCS. EMPTY, and
#: meant to stay that way: the one entry it ever held (the private umbrella build spec, cited by
#: builder-evidence/builder-radar) was retired on 2026-08-28 when those citations were reworded
#: to stand on their own. An entry here is a doc the carve ships a pointer to and not the file —
#: prefer rewording the citation, or adding the doc to both SHIPPING_DOCS arrays.
_KNOWN_PREEXISTING_GAPS: frozenset[str] = frozenset()


def test_every_skill_cited_doc_is_a_shipping_doc():
    """A skill body citing a doc that isn't in the carve is `oss-export.sh` hard-failing at
    release time — catch it here instead."""
    cited = _docs_cited_by_skills()
    shipping = _debrand_shipping_docs()
    uncovered = {c for c in cited if c not in shipping} - _KNOWN_PREEXISTING_GAPS
    assert not uncovered, (
        f"skill bodies cite docs/*.md file(s) not registered in SHIPPING_DOCS: {sorted(uncovered)}. "
        "Add them to both tests/lint/debrand_check.sh and scripts/oss-export.sh, or the OSS export "
        "will hard-fail on the first release after this lands."
    )


@pytest.mark.private_tree  # compares against the un-carved scripts/oss-export.sh
def test_x_tweet_patterns_catalog_is_registered():
    """Anchor for this specific addition — the general
    sync tests above would already catch a regression, but this names the expectation directly."""
    assert "x-tweet-patterns.md" in _debrand_shipping_docs()
    assert "x-tweet-patterns.md" in _oss_export_shipping_docs()
