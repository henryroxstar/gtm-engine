"""Discrimination suite for `gtm_core.design_lint`.

The false-negative tests matter as much as the positive ones. A rule that flags a design
which is actually fine teaches authors to route around the gate, and a gate people route
around is worse than no gate — it looks like coverage. So every rule below carries a
matched `_flags_` / `_quiet_on_` pair, the same contract `tests/lint/test_deck_lint.py`
states for deck_lint.

Every company and product name here is fictional (§R9).
"""

from __future__ import annotations

import pytest

from gtm_core.design_lint import (
    ADVISORY,
    NUMBER_WORDS,
    WARN,
    UnparseableDesign,
    coverage_map,
    dimensions,
    lint,
    lint_skill,
    parse_sections,
    statuses,
)
from gtm_core.design_lint.cli import _blocks

# ── fixture builders ──────────────────────────────────────────────────────────────────

COMPLETE_HEADINGS = [
    "# Solution design — Northwind Analytics",
    "## Executive summary",
    "## Tier 1 — Customer overview",
    "### The problem, today",
    "### What we propose",
    "### Architecture: context and current state",
    "### How it works, end to end",
    "### Deployment topology",
    "## Tier 2 — Technical appendix",
    "### A1. Constraints",
    "### A2. Open questions and dependencies",
    "### A3. Component inventory",
    "### A4. Identity, policy and crosscutting concerns",
    "### A5. Trade-offs and alternatives considered",
    "### A6. Quality requirements",
    "### A7. Glossary",
]


def design(*extra: str, headings: list[str] | None = None) -> str:
    """A structurally complete design, plus whatever the test appends."""
    body = "\n\n".join(
        f"{h}\n\nSome body text for this section.\n" for h in (headings or COMPLETE_HEADINGS)
    )
    return body + "\n\n" + "\n\n".join(extra)


def rules(text: str) -> list[str]:
    return [f"{f.tier} {f.rule}" for f in lint(text)]


def tiers(text: str) -> set[str]:
    return {f.tier for f in lint(text)}


# ── parsing: refuse, never skip (test plan §4.4) ──────────────────────────────────────


def test_a_document_with_no_headings_is_refused_not_linted_clean() -> None:
    with pytest.raises(UnparseableDesign):
        lint("just some prose with no structure at all")


def test_an_empty_document_is_refused() -> None:
    with pytest.raises(UnparseableDesign):
        lint("   \n\n  ")


def test_a_complete_design_parses_and_keeps_preamble() -> None:
    sections = parse_sections("intro line\n\n# Title\n\nbody")
    assert sections[0].level == 0 and sections[0].body == "intro line"
    assert sections[1].heading == "Title"


def test_a_hash_inside_a_code_fence_is_not_a_heading() -> None:
    sections = parse_sections("# Real\n\n```\n# not a heading\n```\n")
    assert [s.heading for s in sections] == ["Real"]


def test_appendix_id_is_read_from_the_heading() -> None:
    sections = parse_sections("### A3. Component inventory\n\nbody")
    assert sections[0].appendix_id == "A3"


# ── SD1 — tier order ──────────────────────────────────────────────────────────────────


def test_sd1_quiet_on_a_complete_ordered_design() -> None:
    assert "SD1" not in tiers(design())


def test_sd1_flags_a_missing_tier() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Tier 2" not in h]
    assert "SD1 tier missing" in rules(design(headings=headings))


def test_sd1_flags_the_appendix_before_the_customer_overview() -> None:
    swapped = list(COMPLETE_HEADINGS)
    i, j = (
        swapped.index("## Tier 1 — Customer overview"),
        swapped.index("## Tier 2 — Technical appendix"),
    )
    swapped[i], swapped[j] = swapped[j], swapped[i]
    assert "SD1 tier order" in rules(design(headings=swapped))


# ── SD2 / SD3 / SD4 — coverage ────────────────────────────────────────────────────────


def test_sd2_quiet_when_every_dimension_has_a_section() -> None:
    assert "SD2" not in tiers(design())


def test_sd2_flags_a_missing_dimension() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Component inventory" not in h]
    assert any(r.startswith("SD2 coverage gap") for r in rules(design(headings=headings)))


def test_sd2_does_not_double_report_the_dimensions_that_own_a_rule() -> None:
    """Quality requirements and glossary report through SD3/SD4, never also through SD2."""
    headings = [
        h for h in COMPLETE_HEADINGS if "Quality requirements" not in h and "Glossary" not in h
    ]
    found = rules(design(headings=headings))
    assert "SD3 quality requirements absent" in found
    assert "SD4 glossary absent" in found
    assert not [r for r in found if "COV-10" in r or "COV-12" in r]


def test_sd3_flags_a_design_with_no_service_levels() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Quality requirements" not in h]
    assert "SD3 quality requirements absent" in rules(design(headings=headings))


def test_sd3_quiet_when_an_slo_section_exists_under_another_name() -> None:
    headings = [
        h.replace("A6. Quality requirements", "A6. Service levels and SLOs")
        for h in COMPLETE_HEADINGS
    ]
    assert "SD3" not in tiers(design(headings=headings))


def test_sd4_flags_a_missing_glossary_as_a_warning_not_an_error() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Glossary" not in h]
    found = [f for f in lint(design(headings=headings)) if f.tier == "SD4"]
    assert found and found[0].severity == "warn"


# ── coverage_map: absence is never reported as presence ───────────────────────────────


def test_coverage_map_reports_every_dimension_with_a_declared_status() -> None:
    sections = parse_sections(design())
    mapped = coverage_map(sections)
    assert len(mapped) == len(dimensions())
    assert {s for _, _, s in mapped} <= statuses()


def test_coverage_map_marks_a_missing_dimension_absent_not_present() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Glossary" not in h]
    mapped = {i: s for i, _, s in coverage_map(parse_sections(design(headings=headings)))}
    assert mapped["COV-12"] == "absent"


def test_not_verified_is_a_declared_status_distinct_from_present() -> None:
    """An unevaluable dimension must have somewhere to land that is not `present`."""
    assert {"not_verified", "present", "absent"} <= statuses()


# ── SD12 — count drift ────────────────────────────────────────────────────────────────


def test_sd12_flags_a_stated_count_that_does_not_match_the_list() -> None:
    section = "## A8. Capabilities\n\nThe design ships three capabilities:\n\n- one\n- two\n"
    assert "SD12 count drift" in rules(design(section))


def test_sd12_quiet_when_the_count_matches() -> None:
    section = "## A8. Capabilities\n\nThe design ships two capabilities:\n\n- one\n- two\n"
    assert "SD12" not in tiers(design(section))


def test_sd12_reads_a_digit_as_well_as_a_word() -> None:
    section = "## A8. Capabilities\n\nThe design ships 4 capabilities:\n\n- one\n- two\n"
    assert "SD12 count drift" in rules(design(section))


# ── SD13 — a diagram that stands alone ────────────────────────────────────────────────


def test_sd13_flags_a_diagram_with_no_walkthrough() -> None:
    section = "## Target state\n\n```mermaid\ngraph TD\n  A-->B\n```\n"
    assert "SD13 diagram without walkthrough" in rules(design(section))


def test_sd13_quiet_when_the_diagram_is_read_for_the_reader() -> None:
    section = (
        "## Target state\n\n```mermaid\ngraph TD\n  A-->B\n```\n\n"
        "How to read this: A calls B. A is the caller; B is the target service.\n"
    )
    assert "SD13" not in tiers(design(section))


# ── suppression ───────────────────────────────────────────────────────────────────────


def test_a_section_scoped_finding_can_be_suppressed_from_its_own_section() -> None:
    section = "## Target state\n\n<!-- lint-ok SD13: the walkthrough is in the next section -->\n\n```mermaid\ngraph TD\n  A-->B\n```\n"
    assert "SD13" not in tiers(design(section))


def test_a_document_level_finding_can_be_suppressed_from_anywhere() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Glossary" not in h]
    body = design(
        "## Notes\n\n<!-- lint-ok SD4: glossary lives in the runbook -->\n", headings=headings
    )
    assert "SD4" not in tiers(body)


def test_a_two_digit_tier_is_not_suppressed_by_a_single_digit_comment() -> None:
    """`lint-ok SD1` must not silence SD12 — the deck_lint `D\\d+` bug, not repeated."""
    section = (
        "## A8. Capabilities\n\n<!-- lint-ok SD1: unrelated -->\n\n"
        "The design ships three capabilities:\n\n- one\n- two\n"
    )
    assert "SD12 count drift" in rules(design(section))


# ── the false-positive classes real documents found (each one permanent) ──────────────


def test_sd13_quiet_on_the_word_configured() -> None:
    """`figure` must not match inside "con**figure**d" — it did, on 11 real designs."""
    section = "## Terms\n\n| Term | Meaning |\n|---|---|\n| Surface | A configured route |\n"
    assert "SD13" not in tiers(design(section))


def test_markers_match_whole_words_not_substrings() -> None:
    """The general form, so this class cannot return for any future marker."""
    from gtm_core.design_lint.catalog import matches_marker

    assert matches_marker("see the figure below", frozenset({"figure"}))
    assert not matches_marker("a configured route", frozenset({"figure"}))
    assert not matches_marker("reconfiguring things", frozenset({"figure"}))
    # symbolic markers cannot carry a word boundary and stay literal
    assert matches_marker("```mermaid\ngraph TD", frozenset({"```mermaid"}))


def test_sd12_counts_the_list_that_follows_not_the_section() -> None:
    """Two lists in one section: only the one the phrase introduces is its denominator."""
    section = (
        "## A8. Limits\n\nThree limits worth stating:\n\n- one\n- two\n- three\n\n"
        "Some prose between the lists.\n\n- unrelated\n- also unrelated\n"
    )
    assert "SD12" not in tiers(design(section))


def test_sd12_quiet_when_the_list_is_longer_than_stated() -> None:
    """ "mapped to the three requirements" above a four-item list is prose about an
    external set, not a drift. Only a SHORTER list is the cut this rule exists for."""
    section = (
        "## Spine\n\nWalked leg by leg, mapped to the three requirements:\n\n- a\n- b\n- c\n- d\n"
    )
    assert "SD12" not in tiers(design(section))


def test_sd12_quiet_on_one_used_as_a_pronoun() -> None:
    """ "which one realises each leg:" is not a count of one."""
    section = "## Spine\n\nHere is which one realises each leg:\n\n- a\n- b\n- c\n- d\n"
    assert "SD12" not in tiers(design(section))


# ── document kind: the mandated three-file split ──────────────────────────────────────


def test_an_appendix_fragment_is_not_missing_the_tiers_it_never_had() -> None:
    from gtm_core.design_lint.rules_coverage import document_kind

    fragment = "\n\n".join(f"### A{n}. Section {n}\n\nbody text here." for n in range(1, 6))
    assert document_kind(parse_sections(fragment)) == "fragment"
    assert "SD1" not in tiers(fragment)


def test_a_complete_design_is_not_a_fragment_because_it_has_an_appendix() -> None:
    """A whole design carries a full A1…A8 and its own omit-from-customer-copy banner.

    Both were read as fragment evidence by earlier cuts of this function; the calibration
    harness caught it by classifying 5 of 11 real documents as fragments when only 2 are.
    """
    from gtm_core.design_lint.rules_coverage import document_kind

    body = design("### A8. Internal appendix\n\nTechnical detail — omit from customer copy.\n")
    assert document_kind(parse_sections(body)) == "whole"


def test_a_split_main_file_is_not_missing_the_appendix_it_links_to() -> None:
    headings = [h for h in COMPLETE_HEADINGS if "Tier 2" not in h and not h.startswith("### A")]
    body = design(
        "## Appendix\n\nThe technical appendix ships as solution-design-acme-2026-01-01-appendix.md\n",
        headings=headings,
    )
    found = rules(body)
    assert "SD1 tier missing" not in found
    assert not [r for r in found if "COV-05" in r or "COV-11" in r]


# ── SD11 — contradictory guardrail (`--skill` mode) ───────────────────────────────────

_CONTRADICTION = """
- **Never ship a version log.** Designs revise silently.

### On a revision — carry a Version log

If this is a revision, append a Version log as the last section.
"""


def test_sd11_flags_a_guardrail_pair_that_contradicts() -> None:
    found = lint_skill(_CONTRADICTION)
    assert found and all(f.tier == "SD11" for f in found)
    assert all(f.severity == "error" for f in found)


def test_sd11_names_both_line_numbers_so_a_human_can_adjudicate() -> None:
    excerpt = lint_skill(_CONTRADICTION)[0].excerpt
    assert "line 2" in excerpt and "forbids" in excerpt


def test_sd11_quiet_on_a_body_that_merely_repeats_a_rule() -> None:
    assert not lint_skill("- Always carry a version log.\n\nRevisions carry a version log.\n")


def test_sd11_quiet_when_the_prohibition_is_scoped_to_another_case() -> None:
    """ "Never write one profile's events into another's" is not contradicted by "write …"."""
    body = (
        "- **One spreadsheet per profile** — never write one profile's events into another's.\n\n"
        "Write the extracted events to the per-profile spreadsheet.\n"
    )
    assert not lint_skill(body)


def test_sd11_quiet_when_the_verbs_are_unrelated_actions() -> None:
    """ "Never write" does not contradict "use" — a contradiction needs the same action."""
    body = "- The news source is read-only — never attempt writes.\n\nUse the news source to read rows.\n"
    assert not lint_skill(body)


def test_sd11_reads_negation_after_the_verb_not_only_before_it() -> None:
    """ "carries no changelog" is a prohibition, not a requirement.

    Found the hard way: the first fix for the version-log contradiction was written as
    "A revision carries no changelog" and SD11 flagged it as contradicting the rule it
    was restating, because the scan only looked backwards from the verb.
    """
    body = "- Never ship a version log.\n\nA revision carries no changelog.\n"
    assert not lint_skill(body)


def test_sd11_needs_two_shared_content_words_not_one() -> None:
    body = "- Never ship a version log.\n\nCarry a signed manifest.\n"
    assert not lint_skill(body)


# ── the real regression: the body this rule was written against ───────────────────────


def test_the_shipped_solution_design_body_has_no_contradiction() -> None:
    """SA3's regression test.

    Before SA3 this body carried the version-log contradiction and this test was the
    negative control that proved SD11 could see it. After SA3 it is the gate that keeps it
    resolved. Skipped where the skill is not present (the OSS carve stubs it).
    """
    from pathlib import Path

    body = Path(__file__).resolve().parents[2] / "plugin/skills/solution-design/body_template.md"
    if not body.exists():  # pragma: no cover - carve stub
        pytest.skip("solution-design body not present")
    found = lint_skill(body.read_text(encoding="utf-8"))
    assert not found, "\n".join(f"{f.excerpt}\n  {f.fix}" for f in found)


# ── the real-artifact gate ────────────────────────────────────────────────────────────


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_conformant_designs_have_no_errors() -> None:
    """A correct document must lint clean. This is the ERROR bar, as a test.

    content/ is per-tenant and not shipped in every checkout, so this globs for whatever
    real design happens to be present rather than naming a tenant (§R9) — the convention
    `tests/lint/test_deck_lint.py` states for the same reason.
    """
    from gtm_core.design_lint.calibrate import conformant_designs

    designs = conformant_designs(_repo_root())
    if not designs:
        pytest.skip("no conformant solution designs present in this checkout")
    # The locator is an INDEX, never the filename, and the finding is its (tier, rule, section)
    # — never the excerpt. A design's filename carries the customer's name and its excerpt is
    # verbatim customer prose, and a failing assertion lands in a terminal and in CI logs, i.e.
    # outside profiles/ and content/ (§R9). `calibrate.render` already holds exactly this
    # property for the same corpus; this test stated it in a docstring and then broke it.
    offenders = []
    for n, path in enumerate(designs, start=1):
        for f in lint(path.read_text(encoding="utf-8")):
            if f.severity == "error":
                offenders.append(f"design #{n}: [{f.tier} {f.rule}] at section {f.section}")
    assert not offenders, (
        "a conformant design carries an error — the ERROR bar is 0. Re-run\n"
        "  uv run python -m gtm_core.design_lint --calibrate "
        "'content/*/accounts/*/solution-design-*.md'\n"
        "and open the numbered design locally; the names stay out of this message on purpose.\n"
        + "\n".join(offenders)
    )


def test_the_conformant_glob_is_non_vacuous() -> None:
    """The instrument check — without it the gate above can silently never run.

    `test_deck_lint.py`'s `_real_theme()` carries the lesson: before 2026-08-29 it always
    skipped, so "the regression this docstring claims to hold never actually ran in CI".
    A skip-when-absent test that is always skipped is worse than no test, because it reads
    as coverage. If this checkout has designs at all, the conformant stratum must be
    non-empty — an empty one means the filter broke, not that the corpus is clean.
    """
    from gtm_core.design_lint.calibrate import conformant_designs

    root = _repo_root()
    all_designs = sorted(root.glob("content/*/accounts/*/solution-design-*.md"))
    if not all_designs:
        pytest.skip("no solution designs present in this checkout")
    assert conformant_designs(root), (
        f"{len(all_designs)} designs on disk but the conformant stratum is empty — "
        "the stratification filter is broken, not the corpus"
    )


# ── surviving-mutant regressions ──────────────────────────────────────────────────────
#
# Each test below was written because a mutation pass killed nothing with it absent: the
# guard it covers could be deleted and the suite stayed green. Scope resolved with
# `uv run python tests/lint/affected_tests.py gtm_core/design_lint/*.py`, never hand-picked
# (hand-picking a test file is what produces a false "0 surviving mutants" result), and
# `__pycache__` cleared between mutants, because a same-length edit reuses stale bytecode.


def test_sd1_does_not_report_one_heading_as_out_of_order_with_itself() -> None:
    """A heading naming both tiers resolves to the same index for both.

    `i > j` and `i >= j` differ only here, and only here does the difference matter: under
    `>=` a document whose single heading opens both tiers accuses itself of being out of
    order, which is a finding an author cannot act on.
    """
    headings = [h for h in COMPLETE_HEADINGS if not h.startswith("## Tier")]
    headings.insert(2, "## Tier 1 and Tier 2 — the whole read")
    assert "SD1 tier order" not in rules(design(headings=headings))
    assert "SD1 tier missing" not in rules(design(headings=headings))


def test_the_omit_banner_deep_in_a_document_does_not_make_it_a_fragment() -> None:
    """The banner is fragment evidence only in the opening, and this is the case that proves it.

    A whole design carries the same banner in its own internal-notes section. Matching it
    anywhere misclassified 5 of 11 real documents; the early return on an executive summary
    hides that for most of them, so the case that isolates the `sections[:2]` window is a
    document with no front matter at all — a legacy design, of which this workspace has five.
    """
    from gtm_core.design_lint.rules_coverage import document_kind

    body = (
        design(
            headings=[
                "# Design note",
                "## Background",
                "## What we would build",
                "## Internal notes",
            ]
        )
        + "\n\nOmit from customer copy: the margin on this is thin.\n"
    )
    assert document_kind(parse_sections(body)) == "whole"
    assert "SD1" in tiers(body), "a whole document must still be held to the three-tier read"


def test_severity_is_read_from_the_taxonomy_and_not_hardcoded() -> None:
    """SD3 once carried ERROR in its own body, so downgrading COV-10 changed nothing.

    The instrument check comes with it: the taxonomy must declare BOTH severities, or this
    test passes against a file where every answer is the same and proves nothing.
    """
    from gtm_core.design_lint.rules_coverage import _severity_of

    declared = {d.guid: d.severity for d in dimensions()}
    assert set(declared.values()) == {"error", "warn"}
    for guid, severity in declared.items():
        assert _severity_of(guid) == severity


def test_the_count_vocabulary_excludes_one() -> None:
    """ "one" is a pronoun far more often than a quantifier.

    Pinned as a table rather than as behaviour, and deliberately: since SD12 narrowed to
    `listed < stated`, a stated count of one can no longer produce a finding either way, so
    no document exercises this filter. It stays because the narrowing could be revisited and
    the pronoun problem would come back with it — a decision worth keeping visible.
    """
    from gtm_core.design_lint.rules_integrity import _COUNTABLE

    assert "one" not in _COUNTABLE
    assert set(_COUNTABLE) | {"one"} == set(NUMBER_WORDS)
    assert all(_COUNTABLE[w] == NUMBER_WORDS[w] for w in _COUNTABLE)


def test_sd12_does_not_count_a_nested_item_towards_the_stated_number() -> None:
    """Three top-level limits under "Four limits" is a drift, however many sub-bullets they carry."""
    section = (
        "## A8. Limits\n\nFour limits worth stating:\n\n"
        "- the first\n  - a detail of the first\n"
        "- the second\n  - a detail of the second\n"
        "- the third\n"
    )
    assert "SD12" in tiers(design(section))


def test_sd12_treats_a_blank_line_inside_a_list_as_part_of_it() -> None:
    """A paragraph break between bullets is formatting, not the end of the list."""
    section = (
        "## A8. Limits\n\nFour limits worth stating:\n\n"
        "- the first\n- the second\n\n- the third\n- the fourth\n"
    )
    assert "SD12" not in tiers(design(section))


# ── SD11 suppression: the documented escape hatch has to exist ────────────────────────


SKILL_CONTRADICTION = (
    "# Fictional skill\n\n## Step 1 — write\n\n"
    "Never ship a version log with a design revision.\n\n"
    "## Step 2 — revise\n\n"
    "A revision must append a version log as its final section.\n"
)


def test_sd11_fires_before_any_suppression() -> None:
    """The baseline. Without it the three tests below could pass on a rule that fires never."""
    assert [f.tier for f in lint_skill(SKILL_CONTRADICTION)] == ["SD11"]


def test_sd11_is_suppressible_from_the_section_holding_either_line() -> None:
    """`lint()` filtered findings through section suppressions and `lint_skill()` never did, so
    the one documented way to adjudicate an SD11 false positive silently did nothing. SD11 is
    the rule that most needs it: its own docstring records a residual FP class."""
    suppressed = SKILL_CONTRADICTION.replace(
        "## Step 2 — revise\n",
        "## Step 2 — revise\n\n<!-- lint-ok SD11: the two rules agree; one restates the other -->\n",
    )
    assert lint_skill(suppressed) == []


def test_sd11_is_not_suppressed_by_a_comment_naming_another_tier() -> None:
    """The exemption is per-rule. A blanket `lint-ok` would retire the gate, not one finding."""
    other = SKILL_CONTRADICTION.replace(
        "## Step 2 — revise\n", "## Step 2 — revise\n\n<!-- lint-ok SD1: different rule -->\n"
    )
    assert [f.tier for f in lint_skill(other)] == ["SD11"]


def test_sd11_suppression_in_an_unrelated_section_does_not_reach_the_pair() -> None:
    """Scope is the section, as it is for `lint()`. A comment three headings away is not an
    adjudication of this pair — it would be a document-wide off switch wearing a reason."""
    elsewhere = SKILL_CONTRADICTION + (
        "\n## Step 3 — unrelated\n\n<!-- lint-ok SD11: about something else entirely -->\n"
    )
    assert [f.tier for f in lint_skill(elsewhere)] == ["SD11"]


def test_a_marker_matches_through_markdown_emphasis() -> None:
    """`\\b` treats `_` as a word character, and this is markdown.

    The house style writes a walkthrough as `_How to read this:_`, so `\\bhow to read\\b` does not
    match it — there is no boundary between `_` and `h`. SD13 fired on the skill's own blank
    template for that reason: the same boundary family as `con**figure**d`, one step over, and
    the only place it showed up was a document written in the style the skill prescribes.
    """
    from gtm_core.design_lint.catalog import WALKTHROUGH_MARKERS, matches_marker

    assert matches_marker("_How to read this:_ the request enters left.", WALKTHROUGH_MARKERS)
    assert matches_marker("**How to read this** — start at the left.", WALKTHROUGH_MARKERS)
    assert matches_marker("*walkthrough*", WALKTHROUGH_MARKERS)
    # Still not a substring match: emphasis is a separator, not a licence.
    assert not matches_marker("a re_configured_ route", frozenset({"figure"}))


def test_sd13_quiet_when_the_walkthrough_is_italicised() -> None:
    """The end-to-end version of the rule above, through `lint()` rather than the matcher."""
    section = (
        "## How it works\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "_How to read this:_ a request enters at A and leaves at B.\n"
    )
    assert "SD13" not in tiers(design(section))


# ── unreadable input refuses; it never crashes and never passes ───────────────────────
#
# `UnparseableDesign` was handled from the start and every other way a document can fail to
# be read was not, so a directory, a non-UTF-8 byte, a vanished file or an absolute glob
# produced a Python traceback out of a gate three skills invoke. An agent reading a traceback
# cannot tell "this document is wrong" from "the linter broke", and only one of those is a
# reason to stop. Found by probing the CLI rather than by any test — which is why these exist.


def _cli(*argv: str) -> tuple[int, str]:
    import contextlib
    import io

    from gtm_core.design_lint.cli import main

    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = main(list(argv))
    return code, err.getvalue()


def test_a_directory_given_as_a_design_refuses_rather_than_crashing() -> None:
    code, err = _cli("gtm_core")
    assert code == 1
    assert "is a directory" in err


def test_a_non_utf8_file_refuses_rather_than_crashing(tmp_path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\x80\x81 not text")
    code, err = _cli(str(bad))
    assert code == 1
    assert "not UTF-8" in err


def test_an_unreadable_skill_body_refuses_rather_than_crashing() -> None:
    code, err = _cli("--skill", "gtm_core")
    assert code == 1
    assert "is a directory" in err


def test_an_absolute_calibrate_glob_refuses_rather_than_crashing() -> None:
    """`Path().glob` raises on an absolute pattern instead of matching it."""
    code, err = _cli("--calibrate", "/nowhere/*.md")
    assert code == 1
    assert "not a relative glob" in err


def test_a_corpus_of_unreadable_documents_does_not_report_pass() -> None:
    """The vacuous-pass case, and the reason `Measurement.holds` exists.

    A glob matching one directory measured "0 errors on the conformant stratum" and printed
    PASS — a bar that holds because nothing was weighed. That is the precise failure this
    harness was built to catch, reproduced by the harness itself.
    """
    from pathlib import Path as _P

    from gtm_core.design_lint.calibrate import UNREADABLE, measure, render

    m = measure([_P("gtm_core/design_lint")])
    assert m.counts.get(UNREADABLE) == 1
    assert m.errors_conformant == 0  # nothing was read, so nothing errored
    assert not m.holds, "an unread corpus is not a clean corpus"
    assert "[FAIL]" in render(m) and "could not be read" in render(m)
    assert _cli("--calibrate", "gtm_core/design_l*")[0] == 1


def test_a_readable_corpus_still_passes() -> None:
    """The other half: `holds` must not simply refuse everything."""
    from gtm_core.design_lint.calibrate import measure

    m = measure([_repo_root() / "tests/tripwire/design_lint/clean.md"])
    assert m.errors_conformant == 0
    assert m.holds


# ── SD6–SD9 — the claim rules, and the severity that blocks nothing ───────────────────
#
# Every test in this block is paired: `_flags_` and `_quiet_on_`, the property the rest of
# this file is built on. It matters more here than anywhere else in the linter, because an
# ADVISORY cannot fail a build — there is no CI signal that notices one has rotted, and
# `--strict` deliberately will not promote it. The tests are the whole instrument.

MATRIX = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Policy evaluation | Enforced | consulted on every call |
| Audit trail | Simulated | shown from fixture data |
| Federation | Design-target | one tenancy today |
"""


def advisories(text: str) -> list[str]:
    return [f"{f.tier} {f.rule}" for f in lint(text) if f.severity == ADVISORY]


def test_an_advisory_is_reported_but_never_blocks() -> None:
    """The contract, asserted directly rather than inferred from the report text.

    Everything else about this severity follows from it: the CLI's exit code, `--strict`
    leaving it alone, and the absence of a `lint-ok` escape hatch it would need if it could
    fail anything.
    """
    text = design(MATRIX.replace("| Federation | Design-target |", "| Federation |  |"))
    assert "SD6 untagged capability" in advisories(text)
    assert not _blocks(lint(text), strict=False)
    assert not _blocks(lint(text), strict=True)


def test_strict_promotes_a_warning_but_not_an_advisory() -> None:
    """A `--strict` that could fail on a judgement call would hand the decision back to the
    linter, which is the one thing this severity exists to prevent."""
    no_glossary = [h for h in COMPLETE_HEADINGS if "Glossary" not in h]
    warned = design(MATRIX, headings=no_glossary)
    warnings = [f for f in lint(warned) if f.severity == WARN]
    assert warnings, "instrument check: this fixture must produce at least one warning"
    assert _blocks(warnings, strict=True)
    assert not _blocks([f for f in lint(warned) if f.severity == ADVISORY], strict=True)


def test_an_advisory_is_not_suppressible_from_the_document() -> None:
    """There is no `lint-ok SD6`, and that is deliberate.

    The alternative is a linter comment inside a solution design the customer reads,
    recording that our tooling's vocabulary is older than the product. An advisory demands
    nothing, so it needs no escape hatch — and a hatch that existed would be the only way
    to make disagreement cost something.
    """
    blank = MATRIX.replace("| Federation | Design-target |", "| Federation |  |")
    # The comment goes INSIDE the finding's own section. The first version of this test put
    # it above the heading, i.e. in the previous section, where a `lint-ok` would not have
    # applied to a warning either — so it passed without testing anything. A mutant that
    # routed advisories through the suppression filter survived it.
    suppressed = design(
        blank.replace(
            "### A8. Capability coverage\n",
            "### A8. Capability coverage\n\n<!-- lint-ok SD6: tagged in the appendix -->\n",
        )
    )
    assert "lint-ok SD6" in suppressed, "instrument check: the comment must be in the text"
    assert "SD6 untagged capability" in advisories(suppressed)


# ── SD6 — untagged capability ─────────────────────────────────────────────────────────


def test_sd6_flags_a_row_that_leaves_the_status_column_blank() -> None:
    assert "SD6 untagged capability" in advisories(
        design(MATRIX.replace("| Federation | Design-target |", "| Federation |  |"))
    )


def test_sd6_quiet_when_every_row_fills_the_status_column() -> None:
    assert advisories(design(MATRIX)) == []


def test_sd6_quiet_on_a_table_with_no_status_column() -> None:
    """A table that never declared a maturity column is not a capability matrix.

    This is the whole precision story. Three earlier shapes asked "is this sentence a
    capability claim?" from a verb list, and measured against real designs each was mostly
    wrong: these documents are full of capability verbs that promise nothing — a settings
    row, a comparison against another product, a process step whose actor is a human, a
    glossary definition. A declared Status column answers the question instead of guessing.
    """
    inventory = """### A3. Component inventory

| Component | What it does | Who runs it |
|---|---|---|
| Gateway | enforces the rules at write time | us |
| Rule store | holds and validates the rules | us |
| Adapter | provisions the onward write | them |
"""
    # MATRIX rides along so the document tags SOMETHING: without it the document-level
    # branch fires instead, and this test would pass or fail for the wrong reason.
    assert advisories(design(MATRIX, inventory)) == []


def test_sd6_fires_however_few_rows_fill_the_column() -> None:
    """The header declares the convention, not the row count.

    An earlier shape required two filled cells before calling a third row drift, which was
    the right guard while "tagged" was being *inferred* from a verb list. A declared Status
    column needs no inference: the author wrote the column, so a blank cell in it is a
    question the document asked itself and did not answer.
    """
    one = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Policy evaluation | Enforced | consulted on every call |
| Audit trail |  | not built |
| Federation |  | not built |
"""
    assert "SD6 untagged capability" in advisories(design(one))


def test_sd6_quiet_when_the_status_column_is_blank_on_every_row() -> None:
    """Nothing has drifted from anything — the column was never filled in at all.

    That is the document-level finding's business ("claims and no maturity tag anywhere"),
    and reporting it twice would be two findings for one fact. Here the document tags
    elsewhere, so neither fires.
    """
    empty = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Policy evaluation |  | consulted on every call |
| Audit trail |  | not built |
"""
    assert advisories(design(MATRIX, empty)) == []


def test_sd6_accepts_any_filled_status_not_only_the_three_house_tags() -> None:
    """A real Status column's vocabulary is far richer than any tag list.

    Measured against this workspace's corpus: "Out of scope → <lane>", "V2 — not reachable
    in V1", "Conditional — requires <thing>", "App-mediated". Every one is an author
    answering the question, and demanding one of three approved words flagged all of them.
    The only honest claim this rule can make is that a cell is blank while its neighbours
    are not.
    """
    rich = MATRIX.replace("| Federation | Design-target |", "| Federation | V2 — not in V1 |")
    assert advisories(design(rich)) == []


def test_sd6_reports_a_document_that_tags_nothing_once_not_per_sentence() -> None:
    """No matrix means no convention was broken — one was never applied.

    That is a single observation about the document, so it is a single finding. Note this
    branch is exercised here and by the tripwire corpus, NOT by the real corpus: every
    design in this workspace tags something, so its silence there is not evidence it works.
    """
    claims = """### A8. What it does

- The gateway enforces the reconciliation rules at write time.
- The adapter validates every onward write.
- The rule store encrypts each verdict at rest.
"""
    found = [f for f in lint(design(claims)) if f.tier == "SD6"]
    assert len(found) == 1
    assert found[0].section == 0


def test_sd6_does_not_count_an_open_question_as_a_claim() -> None:
    """The document-level branch counts claims, and a question is not one.

    "Whether caller-auth populates the policy input" names a capability verb and promises
    nothing — it is the design saying it does not know yet. Four of these used to be enough
    to report a document as tagging nothing.
    """
    questions = """### A2. Open questions

- Whether the gateway enforces the rule at write time or at read time.
- Whether the adapter validates each onward write, or trusts the caller.
- Whether the rule store encrypts each verdict at rest in this region.
- Whether the policy step supports a second tenancy without a redeploy.
"""
    assert advisories(design(questions)) == []


def test_sd6_does_not_count_a_definition_as_a_claim() -> None:
    """A verb governed by a relative pronoun describes a noun; it asserts nothing.

    Deliberately NOT in a glossary section: the glossary skip would otherwise cover for this
    guard, and a mutant that deleted the relative-clause test survived because every
    definition in the suite happened to sit under a Glossary heading.
    """
    concepts = """### A5. Concepts in play

- A verifiable credential is an operator-signed token that authorises an agent.
- A trust registry is a list which enforces who may issue such a token.
- A policy bundle is a rule set that validates each request against that list.
"""
    assert advisories(design(concepts)) == []


def test_sd6_does_not_count_a_nested_bullet_as_its_own_claim() -> None:
    """A nested item elaborates its parent, and its parent is the thing that carries a tag."""
    nested = """### A5. How the parts fit

- The booking path, end to end:
  - the gateway enforces the reconciliation rule at write time
  - the adapter validates each onward write
  - the rule store encrypts each verdict at rest
"""
    assert advisories(design(nested)) == []


def test_sd6_skips_a_status_column_inside_a_glossary() -> None:
    """The glossary skip has to cover the matrix branch too, not only the claim count.

    A glossary that happens to carry a Status column is still a glossary — SD4 owns whether
    one exists, and a blank cell in a list of definitions is not an untagged capability.
    """
    glossary = """### A7. Glossary

| Term | Status | Meaning |
|---|---|---|
| Refusal | Enforced | a booking the gateway declines |
| Federation |  | two tenancies reconciled against each other |
| Verdict | Enforced | the outcome the gateway recorded |
"""
    assert advisories(design(glossary)) == []


def test_sd6_reads_a_placeholder_cell_as_blank() -> None:
    """ "—", "n/a" and "tbd" are the shapes an author uses to mean "not filled in".

    Treating only a truly empty cell as blank survived every other test here, and would make
    the rule silent on the commonest way of leaving the question open.
    """
    for placeholder in ("—", "n/a", "TBD", "?"):
        dashed = MATRIX.replace("| Federation | Design-target |", f"| Federation | {placeholder} |")
        assert "SD6 untagged capability" in advisories(design(dashed)), placeholder


def test_sd6_quiet_on_a_glossary_definition() -> None:
    """A definition carries a capability verb and promises nothing. SD4 owns the glossary."""
    glossary = """### A7. Glossary

| Term | Meaning |
|---|---|
| VC | An operator-signed credential that authorises an agent |
| Status | Whether a capability is enforced or simulated |
"""
    assert advisories(design(glossary)) == []


# ── SD7 — a design-target written as though it ships ──────────────────────────────────


def test_sd7_flags_prose_that_states_a_design_target_in_the_present_tense() -> None:
    live = "Federation validates every hop between tenancies, in production, today."
    assert "SD7 design-target as live" in advisories(design(MATRIX, live))


def test_sd7_quiet_when_the_prose_keeps_the_conditional() -> None:
    """The phrasing matters, and a surviving mutant is how I found out.

    The first version of this test read "Federation **would validate** every hop" — which is
    quiet whatever the conditional guard does, because the bare stem `validate` is not in
    the capability vocabulary at all (that vocabulary is third-person-singular by design, so
    the noun `support` cannot masquerade as the verb). The test passed for a reason unrelated
    to the thing it claimed to check. A conditional clause in front of an indicative verb is
    the shape that actually exercises the guard.
    """
    conditional = "Once the second tenancy is added, federation validates every hop."
    assert advisories(design(MATRIX, conditional)) == []


def test_sd7_quiet_when_the_prose_carries_the_tag_itself() -> None:
    """A sentence that names its own maturity has already told the reader. The rule is
    about the tag and the sentence disagreeing, not about the sentence alone."""
    tagged = "Federation (**Design-target**) validates every hop between tenancies."
    assert advisories(design(MATRIX, tagged)) == []


def test_sd7_quiet_when_the_matrix_row_is_not_forward_looking() -> None:
    enforced = MATRIX.replace("| Federation | Design-target |", "| Federation | Enforced |")
    live = "Federation validates every hop between tenancies, in production, today."
    assert advisories(design(enforced, live)) == []


def test_sd7_needs_the_whole_capability_name_not_one_word_of_it() -> None:
    """A multi-word capability is identified by its whole name.

    With a one-word name the threshold and "any overlap at all" are the same rule, so this
    case is the only one that can tell them apart — which a surviving mutant demonstrated by
    passing every test with the threshold replaced by `if not shared`.
    """
    matrix = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Cross-tenant federation | Design-target | one tenancy today |
| Policy evaluation | Enforced | live |
| Audit trail | Simulated | fixtures |
"""
    # Shares `federation` and nothing else: one word of a two-word name is not the capability.
    partial = "The federation step validates each hop before the policy call."
    assert advisories(design(matrix, partial)) == []


def test_sd7_fires_on_a_maturity_word_used_in_flowing_prose() -> None:
    """A tag is only a tag in tag position; in a sentence it is a word.

    "Cross-tenant federation is enforced on every hop" contains the most important maturity
    word in the vocabulary and tags nothing — it is a *claim*, and reading it as a tag would
    make SD7 silent on precisely the sentence that overclaims. A mutant that dropped tag
    position survived every other test in this file.
    """
    matrix = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Cross-tenant federation | Design-target | one tenancy today |
| Policy evaluation | Enforced | live |
| Audit trail | Simulated | fixtures |
"""
    live = "Cross-tenant federation is enforced on every hop, in production, today."
    assert "SD7 design-target as live" in advisories(design(matrix, live))


def test_sd7_does_not_pair_on_the_products_own_name() -> None:
    """The false positive that sent this rule back to the drawing board twice.

    Its first working shape compared whole units on shared content words, and fired on
    every conformant design in this workspace — in every case the shared terms were the
    product's own two-word name, which appears in nearly every sentence AND nearly every
    matrix row, so the threshold was met before any comparison happened. Same family as
    `org_token` reading a subdomain as the company, and COV-04's marker being the bare word
    `solution`, which every document titled "Solution design — <account>" satisfies from
    its own H1. The stoplist is now derived from the document in front of it.
    """
    topic = "\n\n".join(
        f"### Section {n}\n\nThe agent gateway routes the agent request through the "
        f"agent gateway policy step, and the agent gateway records it."
        for n in range(12)
    )
    unrelated = "The agent gateway validates each agent request against the agent policy."
    matrix = """### A8. Capability coverage

| Capability | Status | Note |
|---|---|---|
| Agent gateway federation | Design-target | later |
| Agent gateway policy | Enforced | live |
| Agent gateway audit | Simulated | fixtures |
"""
    assert advisories(design(topic, matrix, unrelated)) == []


# ── SD8 — a claim that writes to something the document froze ─────────────────────────

FROZEN = """### A1. Constraints

The clinical record system is read-only to us for the whole engagement.
"""


def test_sd8_flags_a_claim_that_writes_to_a_frozen_system() -> None:
    claim = "### A4. Data flow\n\nThe gateway writes each verdict into the clinical record system."
    assert "SD8 constraint contradiction" in advisories(design(FROZEN, claim))


def test_sd8_quiet_when_the_claim_only_reads_the_frozen_system() -> None:
    """Reading a read-only system obeys the constraint. Only a write breaks it."""
    claim = "### A4. Data flow\n\nThe gateway reads each session from the clinical record system."
    assert advisories(design(FROZEN, claim)) == []


def test_sd8_quiet_on_an_open_question_about_the_frozen_thing() -> None:
    """The clean tripwire fixture's own false positive, kept as a regression.

    "whether the weekend **change** window is long enough" matched the *noun* `change` in a
    mutating-verb list and read as a write to a frozen system — the fourth time this family
    has bitten here, after `con**figure**d`, `figure`-as-a-number and `solution` in every H1.
    """
    question = (
        "### A2. Open questions\n\n"
        "- whether the change window is long enough to migrate the clinical record system"
    )
    assert advisories(design(FROZEN, question)) == []


def test_sd8_quiet_on_a_noun_that_a_mutating_verb_list_would_match() -> None:
    """The other half of that fix, and it needed its own test.

    The docstring above used to claim both halves were asserted "because either alone lets
    this sentence through". That was wrong: the question guard alone silences it, so a mutant
    restoring the bare stems to the verb list survived. This case carries the noun WITHOUT
    the question, so only the vocabulary can be doing the work.
    """
    statement = (
        "### A2. Sequencing\n\n"
        "The change window is the weekend before the clinical record system freeze."
    )
    assert advisories(design(FROZEN, statement)) == []


def test_sd8_needs_two_shared_terms_not_one() -> None:
    """One shared word is a coincidence; a design shares single words everywhere."""
    claim = "### A4. Data flow\n\nThe gateway writes each verdict into the regional system."
    assert advisories(design(FROZEN, claim)) == []


def test_sd8_does_not_pair_a_limit_against_its_own_section() -> None:
    """A Constraints section states limits; it does not make claims against them.

    A constraint often names the very write it forbids ("we do not write to the clinical
    record system"), so reading that section as a source of claims as well as limits makes
    the rule contradict itself on correct input. A mutant that deleted the skip survived
    every other test, because no fixture had both halves in one section.
    """
    both = """### A1. Constraints

The clinical record system is read-only to us for the whole engagement.
Nobody writes to the clinical record system from inside our boundary.
"""
    assert advisories(design(both)) == []


def test_sd8_reads_a_limit_only_from_a_section_that_is_one() -> None:
    """A heading that MENTIONS guardrails is not a Guardrails section.

    The earlier check asked `matches_marker`, which found the word inside a real document's
    own H1 and drew the document's limits from its title. The preamble below carries a freeze
    marker, so under the old behaviour the title became a constraint section and the claim
    two sections later contradicted it.
    """
    titled = "\n\n".join(
        [
            "# Solution design — secure runtime guardrails for a booking gateway",
            "The clinical record system is read-only to us for the whole engagement.",
            "## Executive summary\n\nOne page.",
            "## Tier 1 — Customer overview\n\nThe overview.",
            "## Tier 2 — Technical appendix",
            "### A4. Data flow\n\nThe gateway writes each verdict into the clinical record system.",
        ]
    )
    assert "SD8 constraint contradiction" not in advisories(titled)


def test_sd8_quiet_when_the_limit_and_the_claim_are_about_different_things() -> None:
    claim = "### A4. Data flow\n\nThe gateway writes each verdict into the reporting store."
    assert advisories(design(FROZEN, claim)) == []


# ── SD9 — one action, two actors ──────────────────────────────────────────────────────


def test_sd9_flags_the_same_action_done_by_both_actors() -> None:
    both = (
        "### A4. Flow\n\nThe holder presents the session credential to the gateway.\n\n"
        "### A5. Fallback\n\nWe present the session credential when the wallet is offline."
    )
    assert "SD9 actor drift" in advisories(design(both))


def test_sd9_quiet_when_each_actor_does_its_own_action() -> None:
    """A design describes both sides of an integration and must. Flagging a document for
    containing both families would fire on every correct design ever written."""
    split = (
        "### A4. Flow\n\nThe holder presents the session credential to the gateway.\n\n"
        "### A5. Fallback\n\nWe store the verdict against that credential reference."
    )
    assert advisories(design(split)) == []


def test_sd9_quiet_on_a_perception_verb_shared_by_both_actors() -> None:
    """Only an action can be mis-attributed, because only an action has one rightful owner.
    "The customer sees the dashboard" and "we see the audit log" share a verb and two
    actors and are two parties looking at two things."""
    seeing = (
        "### A4. Flow\n\nThe customer sees the refusal reason immediately.\n\n"
        "### A5. Audit\n\nWe see every refusal in the audit trail."
    )
    assert advisories(design(seeing)) == []


def test_sd9_does_not_read_a_verb_across_a_relative_clause() -> None:
    """In "the holder that we onboard" the verb belongs to *us*, not to the holder.

    The first version of this test had only the clause sentence, and passed whatever the
    guard did — the match that would have been wrong was swallowed by the longer one, so
    neither behaviour produced a second actor family. Pairing the clause with a legitimate
    operator sentence about the same verb is what makes the misreading observable: without
    the guard, `onboard` acquires a principal actor it never had.
    """
    clause = (
        "### A4. Flow\n\nThe holder that we onboard signs the request on our behalf.\n\n"
        "### A5. Setup\n\nWe onboard every holder before the first call."
    )
    assert "SD9 actor drift" not in advisories(design(clause))


def test_sd9_matches_two_spellings_of_one_action() -> None:
    """`presents` and `present` are one action. Two spellings reading as two actions would
    make the rule see drift wherever a subject changed number — and miss it where it is."""
    from gtm_core.design_lint.rules_claims import _lemma

    assert _lemma("presents") == "present"
    assert _lemma("verifies") == "verify"
    assert _lemma("issues") == "issue"
