"""Doc-freshness gates for the docs no generator owns.

Every doc in this repo that has a *generator* stayed correct on its own
(``docs/SKILLS.md`` via ``tests/skills/test_codegen.py``, ``docs/knowledge-usage.md``
via ``tests/skills/test_knowledge_usage.py`` — both drift-gated). Every doc a *human*
maintains drifted: on 2026-08-15 an audit found ``README.md`` claiming 43 skills while
the generated inventory said 44, the umbrella build spec citing schema ``V001-V008``
against a tree at ``V020``, and a locked decision naming a repo URL that 404s after a
rename. All three had been wrong for weeks with CI green.

These gates cover the mechanically-checkable subset of that class. They deliberately do
NOT try to prove a narrative is current — no test can. The umbrella's §0 status table
still depends on the repo's Definition of Done; what is enforced here is only the part
where a number in prose must equal a number derivable from the tree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gtm_core.skills import registry

REPO = Path(__file__).resolve().parents[2]


# ── 1. Hand-maintained skill counts ─────────────────────────────────────────────
# docs/SKILLS.md is generated and already gated. These three are hand-written and are
# the user-facing surfaces: the plugin manifest a user installs, and the two READMEs
# they read first. The Definition of Done lists them as a manual checklist item and warns
# that "a new skill can pass all eight gates and still be invisible to a user" — this
# turns the count half of that warning into a gate.


def _skill_count() -> int:
    return len(registry.all_skills())


@pytest.mark.parametrize(
    ("rel_path", "pattern", "fix"),
    [
        (
            "README.md",
            r"## GTM skill suite \((\d+) skills",
            "update the heading and the categorized table below it",
        ),
        (
            "plugin/README.md",
            r"The full set is \*\*(\d+) skills\*\*",
            "update the count",
        ),
    ],
)
def test_hand_maintained_skill_count_matches_registry(
    rel_path: str, pattern: str, fix: str
) -> None:
    text = (REPO / rel_path).read_text(encoding="utf-8")
    match = re.search(pattern, text)
    assert match is not None, (
        f"{rel_path}: could not find the skill-count line (pattern: {pattern})"
    )
    claimed = int(match.group(1))
    assert claimed == _skill_count(), (
        f"{rel_path} claims {claimed} skills; the registry has {_skill_count()} — {fix}. "
        "Generated inventory: docs/SKILLS.md."
    )


def test_plugin_manifest_count_and_enumeration_match_registry() -> None:
    """The manifest carries BOTH a count and an explicit list; drift either way is
    user-visible in the plugin marketplace, so check both against the registry."""
    manifest = json.loads((REPO / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    description = manifest["description"]

    match = re.search(r"(\d+) skills:", description)
    assert match is not None, "plugin.json description has no 'N skills:' enumeration"
    claimed = int(match.group(1))
    assert claimed == _skill_count(), (
        f"plugin.json claims {claimed} skills; the registry has {_skill_count()}"
    )

    # The enumeration is the tail of the description after "N skills:", comma-separated.
    listed = {
        name.strip().rstrip(".")
        for name in description.split(f"{claimed} skills:", 1)[1].split(",")
        if name.strip()
    }
    expected = {s.name for s in registry.all_skills()}
    assert listed == expected, (
        "plugin.json's enumerated skill list drifted from the registry — "
        f"missing: {sorted(expected - listed)}; unknown: {sorted(listed - expected)}"
    )


# ── 1b. Hand-maintained pack inventory ─────────────────────────────────────────
# Same class as the skill count above, and it drifted the same way: on 2026-09-05 an audit
# found README.md claiming "Four packs ship in-repo — planning · marketing · prospecting ·
# solution-architecture" against a tree holding NINE packs and 22 graph variants. The whole
# creator (video) lane — 7 variants — was invisible in "what it does out of the box" while
# its ten skills were listed in the skill table two sections below. The count gate written
# in August covered skills only; this is the same gate for the layer above them.


def _pack_variants() -> dict[str, int]:
    """Pack slug -> number of graph variants, read off packs/ on disk."""
    root = REPO / "packs"
    assert root.is_dir(), "packs/ not found"
    return {
        d.name: len(list((d / "graphs").glob("*.toml")))
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / "graphs").is_dir()
    }


def test_readme_pack_inventory_matches_disk() -> None:
    """The README's pack count, variant count, and pack names must match packs/.

    Both numbers are spelled exactly once (in "What it does out of the box"); everywhere
    else links to that section rather than restating them, so there is one place to fix.
    """
    packs = _pack_variants()
    assert packs, "no packs with a graphs/ directory found"
    text = (REPO / "README.md").read_text(encoding="utf-8")

    for pattern, actual, what in (
        (r"\*\*(\d+) packs ship in-repo", len(packs), "pack count"),
        (r"spanning (\d+) workflow variants", sum(packs.values()), "variant count"),
    ):
        found = re.findall(pattern, text)
        assert found, f"README.md: no {what} line matching {pattern!r}"
        assert len(found) == 1, (
            f"README.md states the {what} in {len(found)} places; it must be spelled once "
            "and linked to from anywhere else (a restated number goes stale silently)."
        )
        assert int(found[0]) == actual, (
            f"README.md claims {found[0]} for the {what}; packs/ has {actual} "
            f"({len(packs)} packs, {sum(packs.values())} variants). Update "
            "'What it does out of the box'."
        )

    lowered = text.lower()
    for slug in packs:
        assert slug.replace("-", " ") in lowered, (
            f"packs/{slug}/ ships but is not named in README.md's pack table — a pack a "
            "reader cannot discover is a pack that does not exist to them."
        )


# ── 1c. README internal anchors ────────────────────────────────────────────────
# The README is the source of truth for BOTH cuts (scripts/oss_readme.py renders the public
# one from it), so a dead in-page link ships twice. Some anchors embed a number the gates
# above force to change -- e.g. #gtm-skill-suite-58-skills--all-profile-driven -- which
# makes an unchecked table of contents a drift surface, not a convenience.


def _github_slug(heading: str) -> str:
    """GitHub's heading-anchor slug: lowercase, drop punctuation, spaces to hyphens."""
    slug = re.sub(r"[^\w\s-]", "", heading.strip().lower(), flags=re.UNICODE)
    return slug.replace(" ", "-")


def test_readme_internal_anchors_resolve() -> None:
    readme_files = sorted(REPO.glob("README*.md"))
    assert len(readme_files) >= 6, f"Expected at least 6 README files, found: {readme_files}"
    for path in readme_files:
        text = path.read_text(encoding="utf-8")
        headings = {
            _github_slug(line.lstrip("#").strip())
            for line in text.splitlines()
            if re.match(r"#{1,6} \S", line)
        }
        md_anchors = re.findall(r"\]\(#([^)]+)\)", text)
        html_anchors = re.findall(r"<a\s+href=\"#([^\"]+)\"", text)
        dead = sorted({a for a in (md_anchors + html_anchors) if a not in headings})
        assert not dead, (
            f"{path.name} links to headings that do not exist: {dead}. Known anchors: {sorted(headings)}"
        )


# ── 2. Documented schema range vs. backend/schema/ ──────────────────────────────
# The build spec cited V001-V004 in three places and V001-V008 in two while the tree was
# at V020 — a reader sizing up the data spine would have been six months out of date.

#: The umbrella build spec. A private-tree doc — it is NOT in the OSS carve's SHIPPING_DOCS,
#: so both gates below feature-detect it and skip. Spelled once, here, because the export's
#: doc-reference gate holds every OTHER `docs/*.md` mention in the carve to "must exist";
#: this one is registered in that gate's allowlist as a guarded, absent-by-design reference.
_UMBRELLA = "docs/content-os-build-spec.md"


def _max_migration() -> int:
    versions = [
        int(re.match(r"V(\d+)__", path.name).group(1))
        for path in (REPO / "backend/schema").glob("V*.sql")
        if re.match(r"V(\d+)__", path.name)
    ]
    assert versions, "no migrations found under backend/schema/"
    return max(versions)


def test_documented_schema_range_matches_disk() -> None:
    """Any doc citing a ``V001-VNNN`` range must cite the real head migration.

    A doc may legitimately describe an *older* phase's scope (e.g. "Phase C laid
    V001-V004"), so only ranges that start at V001 AND are presented as the current
    extent are checked — enforced by requiring the head to appear at least once in
    each file that cites any V001- range.
    """
    if not (REPO / _UMBRELLA).exists():
        # Private umbrella doc, not in the OSS carve's SHIPPING_DOCS — nothing to check.
        pytest.skip(f"{_UMBRELLA} not present (private umbrella doc)")
    head = _max_migration()
    head_token = f"V{head:03d}"

    for rel_path in (_UMBRELLA,):
        text = (REPO / rel_path).read_text(encoding="utf-8")
        cited = re.findall(r"V001[–-]V(\d{3})", text)
        if not cited:
            continue
        assert head_token in text, (
            f"{rel_path} cites schema range(s) V001-V{max(cited)} but never mentions "
            f"the head migration {head_token} (backend/schema/ is at {head_token}). "
            "Either update the range or state explicitly which phase the older range describes."
        )


# ── 2b. Documented rule range vs. docs/RULES.md ────────────────────────────────
# Identical in shape to the schema-range gate above, and it had drifted just as far: on
# 2026-09-05, RULES.md held §R1-§R10 while README.md, CLAUDE.md's documentation map, the
# umbrella, and two files that SHIP to the public repo all still said §R1-§R8 or §R1-§R9.
# A dated design doc may legitimately cite the range as it stood, so only the living
# surfaces below are gated -- never docs/prds/ or docs/archive/.

#: Living docs that state the enforced-rule range as current fact. Feature-detected:
#: some are private-only, some are the public-carve sources.
_RULE_RANGE_DOCS = (
    "README.md",
    "CLAUDE.md",
    "docs/content-os-build-spec.md",
    "oss/CONTRIBUTING.md",
    "oss/readme/development-lead.md",
    "oss/overlays/docs/ARCHITECTURE.md",
)


def _max_rule() -> int:
    text = (REPO / "docs/RULES.md").read_text(encoding="utf-8")
    rules = [int(n) for n in re.findall(r"^## \u00a7R(\d+)", text, re.MULTILINE)]
    assert rules, "no '## \u00a7RN' headings found in docs/RULES.md"
    return max(rules)


def test_documented_rule_range_matches_rules_md() -> None:
    head = _max_rule()
    checked = 0
    for rel_path in _RULE_RANGE_DOCS:
        path = REPO / rel_path
        if not path.is_file():
            continue  # private-only doc absent from this tree (e.g. the OSS carve)
        checked += 1
        for cited in re.findall(r"\u00a7R1[\u2013-]\u00a7R(\d+)", path.read_text(encoding="utf-8")):
            assert int(cited) == head, (
                f"{rel_path} cites the enforced rules as \u00a7R1-\u00a7R{cited}, but docs/RULES.md "
                f"defines \u00a7R1-\u00a7R{head}. Update the range (or, for a historical claim, "
                "say which date it describes)."
            )
    assert checked, f"none of {_RULE_RANGE_DOCS} exist -- the gate is inert"


# ── 3. A recent PRD must be reachable from the umbrella ─────────────────────────
# CLAUDE.md's documentation map makes content-os-build-spec.md the umbrella source of
# truth. Track A (12 work items, 5 stacked PRs, 3 PRDs) landed 2026-08-09 and the
# umbrella had no record of it at all until 2026-08-15. A PRD nobody links is a design
# only its author knows about.

# PRDs deliberately outside the umbrella's scope. The umbrella covers the *engine* and its
# productization; a build spec for one named customer's demo is tenant work whose owner doc
# lives under content/, so linking it here would put a customer in the engine narrative.
#
# The list lives beside the PRDs rather than here because every entry IS a customer name, and
# this file ships in the public carve while docs/prds/ does not (R9: third-party identity stays
# out of code). One filename per line; `#` comments and blank lines ignored. Absent -> empty,
# which is also what the carve sees, where the test skips for want of docs/prds anyway.
_UMBRELLA_EXEMPT_FILE = REPO / "docs/prds/.umbrella-exempt"


def _umbrella_exempt() -> frozenset[str]:
    if not _UMBRELLA_EXEMPT_FILE.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in _UMBRELLA_EXEMPT_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_recent_prds_are_referenced_by_the_umbrella() -> None:
    """Every PRD dated within 30 days of the newest PRD must be named in the umbrella.

    Anchored to the newest PRD rather than today's date so the gate is deterministic and
    does not spontaneously fail on a quiet month or in a stale checkout.
    """
    if not (REPO / "docs/prds").exists():
        # docs/prds is excluded from the OSS carve by design (private design docs).
        pytest.skip("docs/prds not present (private design docs)")
    prds = sorted((REPO / "docs/prds").glob("20*-*.md"))
    assert prds, "no PRDs found"

    def _date(path: Path) -> str:
        match = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
        return match.group(1) if match else ""

    newest = max(_date(p) for p in prds)
    year, month, day = (int(part) for part in newest.split("-"))
    # 30-day window, approximated by month arithmetic — exact day math is not needed,
    # the gate only has to catch "a whole program landed and nobody linked it".
    cutoff_month = month - 1 if month > 1 else 12
    cutoff_year = year if month > 1 else year - 1
    cutoff = f"{cutoff_year:04d}-{cutoff_month:02d}-{day:02d}"

    umbrella = (REPO / _UMBRELLA).read_text(encoding="utf-8")
    unreferenced = [
        path.name
        for path in prds
        if _date(path) >= cutoff
        and path.name not in _umbrella_exempt()
        and path.name not in umbrella
    ]
    assert not unreferenced, (
        f"PRD(s) dated on or after {cutoff} are not referenced anywhere in {_UMBRELLA} "
        f"(§0 status row or document map): {unreferenced}. "
        "Add a row, or add the filename to docs/prds/.umbrella-exempt with a reason."
    )
