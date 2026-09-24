"""An onboarded tenant inherits the registry tables, not just the prose.

`_supplement_from_template` exists so *"a new profile isn't missing files the skills expect …
[which] would otherwise silently run on defaults"* (its own docstring). It filtered on
`suffix not in (".md", ".txt")` under a comment reading *"Text knowledge topics only — never
the binary brand assets or raw source briefs"* — but a `.toml` is neither binary nor a raw
brief, so the filter excluded every machine-readable knowledge file the skeleton ships.

*How many* is deliberately not written down here. Three attempts to say it disagreed — this
docstring said six, the code comment enumerated eight, the tree held ten — and none of the
three could be told apart from a fresh reading at a glance (§R14).
`test_every_template_toml_reaches_an_onboarded_tenant` derives the set from the template
instead, so it cannot go stale the next time a `.toml` is added to the skeleton.

Measured on 2026-09-24 before the fix: `files supplemented: 23`, `toml files carried: []`.

That is worse than "runs on defaults" for the fact registry, because `registry.load` is
all-or-nothing: a tenant onboarded from the template would refuse on its first read, naming
three files the operator never touched and cannot find in their own profile. The same filter
also left `premise-vocab.toml`'s header claiming *"a freshly-cloned profile resolves to an
EMPTY vocabulary"* true only by accident — no file at all, rather than the inert file it
describes.

The `.md`-only rule that matters is a **different** one and is still enforced:
`_ensure_knowledge_frontmatter` stamps only topics `is_managed_topic()` accepts, and that
requires `.md` (`gtm_core/knowledge_meta.py`). So a carried `.toml` is never handed a
frontmatter block, which would have made it invalid TOML. The test below pins that too — it
is the hazard a one-word widening would otherwise have introduced silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.onboard.knowledge import TEMPLATE_KNOWLEDGE_EXCLUDED, _supplement_from_template
from gtm_core.knowledge_meta import EXCLUDED_DIRS
from gtm_core.messaging import registry

_TEMPLATE = Path(__file__).resolve().parents[2] / "profiles" / "_template" / "knowledge"

#: The tables `registry.load` refuses without. Read from the loader's own constants so this
#: check keeps covering a fourth table the day one is added.
_REGISTRY_FILES = (registry.CLAIMS_FILE, registry.PROOF_FILE, registry.ANGLES_FILE)


def _template_tomls() -> set[str]:
    """Every `.toml` the skeleton ships, as the supplement's own relative keys.

    `EXCLUDED_DIRS` is imported from the module the supplement itself reads it from, rather
    than restated: a directory rule stated twice is a directory rule that drifts.
    """
    out: set[str] = set()
    for path in _TEMPLATE.rglob("*.toml"):
        rel = path.relative_to(_TEMPLATE)
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        out.add("knowledge/" + rel.as_posix())
    return out


@pytest.fixture
def supplemented() -> dict[str, str]:
    files: dict[str, str] = {}
    _supplement_from_template(files, _TEMPLATE)
    return files


def test_the_template_is_where_we_think_it_is(supplemented):
    """Guard against a vacuous pass: a missing template dir makes the function a no-op."""
    assert _TEMPLATE.is_dir()
    assert supplemented, (
        "_supplement_from_template carried nothing — every assertion below is vacuous"
    )


@pytest.mark.parametrize("name", _REGISTRY_FILES)
def test_a_registry_table_reaches_an_onboarded_tenant(name):
    files: dict[str, str] = {}
    _supplement_from_template(files, _TEMPLATE)
    assert f"knowledge/{name}" in files, (
        f"{name} is not carried to a new tenant — registry.load would refuse on first read"
    )


def test_every_template_toml_reaches_an_onboarded_tenant(supplemented):
    """The carried set, derived from the tree — never a typed count (§R14).

    The three registry tables above are named because the loader names them. The rest of the
    machine-readable knowledge has no such constant to read, and a hand-written list of it
    would be exactly the number this test replaced: correct once, unreadable as stale after.
    So the expectation is computed from the skeleton on every run — a `.toml` added to the
    template is covered the day it lands, and one the supplement quietly stops carrying fails
    here rather than in some tenant's first read.

    The supplement's deliberate exclusions are read off its own source rather than retyped, for
    the same reason: naming the withheld file here too would put the exclusion in two homes, and
    the copy nobody edits is the one that wins.
    """
    expected = _template_tomls() - {f"knowledge/{name}" for name in TEMPLATE_KNOWLEDGE_EXCLUDED}
    assert expected, "the skeleton ships no .toml at all — the comparison below is vacuous"
    carried = {rel for rel in supplemented if rel.endswith(".toml")}
    assert carried == expected, (
        "the supplement and the skeleton disagree about which machine-readable knowledge a "
        f"new tenant inherits: missing {sorted(expected - carried)}, "
        f"unexpected {sorted(carried - expected)}"
    )
    # ...and the exclusion is a real subtraction, not a no-op that makes the set match by
    # accident: a withheld name is genuinely absent from what a tenant inherits.
    for name in TEMPLATE_KNOWLEDGE_EXCLUDED:
        assert f"knowledge/{name}" not in supplemented


def test_the_exclusion_set_is_not_empty():
    """The control for the subtraction above.

    `test_every_template_toml_reaches_an_onboarded_tenant` subtracts the exclusions from the
    skeleton's `.toml` set. If that set were ever empty the subtraction would be a no-op and the
    comparison would pass while carrying everything — the exact widening the exclusion exists to
    prevent. An empty exclusion set is a legitimate future state, but it is one somebody has to
    decide, so it fails here rather than passing quietly.

    The set is imported, not re-derived: naming the withheld file in a second place would put
    the contract in two homes, and the copy nobody edits is the one that wins.
    """
    assert TEMPLATE_KNOWLEDGE_EXCLUDED, (
        "TEMPLATE_KNOWLEDGE_EXCLUDED is empty — if that is deliberate, say so here; until then "
        "it makes the carried-set comparison unable to detect a widened supplement"
    )


def test_an_onboarded_tenant_registry_loads(tmp_path):
    """The end state, not the mechanism: the supplemented tree is loadable.

    Asserting the three filenames is necessary but not sufficient — a carried-but-mangled file
    (a frontmatter block prepended to TOML, say) satisfies the name check and still refuses.
    """
    files: dict[str, str] = {}
    _supplement_from_template(files, _TEMPLATE)
    root = tmp_path / "profiles"
    for rel, content in files.items():
        dest = root / "newtenant" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    loaded = registry.load("newtenant", profiles_root=root)
    assert loaded.claims, "the carried claims.toml parsed to nothing"


def test_a_carried_toml_is_never_frontmatter_stamped(supplemented):
    """The hazard the widening could have introduced.

    A TOML file handed a `---` frontmatter block is not valid TOML, and the failure would
    surface as a load refusal in a tenant nobody had edited.
    """
    for name in _REGISTRY_FILES:
        assert not supplemented[f"knowledge/{name}"].lstrip().startswith("---")


def test_binary_brand_assets_are_still_excluded(supplemented):
    """The negative control the original filter was actually written for.

    Widening a suffix list is only safe if the thing it was guarding against is still guarded.
    """
    carried = set(supplemented)
    for rel in carried:
        assert Path(rel).suffix in {".md", ".txt", ".toml"}, f"unexpected suffix carried: {rel}"
    assert not any("brand/" in rel for rel in carried), "brand assets must not be inherited"


def test_a_draft_derived_file_is_never_overwritten():
    """`setdefault`, not assignment — the tenant's own extracted file always wins."""
    mine = "# the tenant's own, extracted from their site\n"
    files = {f"knowledge/{registry.CLAIMS_FILE}": mine}
    _supplement_from_template(files, _TEMPLATE)
    assert files[f"knowledge/{registry.CLAIMS_FILE}"] == mine
