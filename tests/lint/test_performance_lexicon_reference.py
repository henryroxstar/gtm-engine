"""The performance lexicon: its standing, its scope, and its examples against the real linter.

A reference that teaches a prompt grammar has a failure mode a citation test cannot see — the
examples drift out of agreement with the rule that enforces them, and the file starts teaching
something the tree refuses. So the fenced examples here are not illustrations: every ```expression
block is run through `_lint_expression` and `_lint_negation` and must come back clean, and every
```expression-bad block must fire. The prose and the code are checked against each other, in the
direction that matters — the file cannot recommend what the linter would reject.

The rest is the standing the file has to keep to ship in the public carve: no company, no named
practitioner, no contact detail, and a provenance note that says how strong each claim is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core import shots_lint as sl

REPO = Path(__file__).resolve().parents[2]
REF = REPO / "plugin" / "skills" / "creator-brief" / "references" / "performance-lexicon.md"
BODY = REPO / "plugin" / "skills" / "creator-brief" / "body_template.md"

if not REF.is_file():
    pytest.skip(
        "creator-brief references not present in this distribution (paid-tier stub)",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def text() -> str:
    return REF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def flat(text: str) -> str:
    """The file with its line wrapping collapsed. Prose assertions run against this: where a
    sentence happens to break is an editing artifact, and a test that pins it fails on a reflow
    that changed nothing, which is how a suite teaches people to stop reading it."""
    return re.sub(r"\s+", " ", text)


def _fenced(text: str, tag: str) -> list[str]:
    """Every line inside ```<tag> blocks — one direction per line, blanks dropped."""
    out: list[str] = []
    for block in re.findall(rf"^```{tag}\n(.*?)^```", text, re.MULTILINE | re.DOTALL):
        out.extend(line.strip() for line in block.splitlines() if line.strip())
    return out


def _findings(expression: str) -> list[str]:
    shot = {
        "duration_s": 5,
        "camera": "static, eye level",
        "visual": "a woman at a kitchen table",
        "motion_prompt": "she turns the cup a quarter turn",
        "expression": expression,
    }
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_expression(shot, "shot[1]", errors, warnings)
    sl._lint_negation(expression, "shot[1].expression", errors)
    return errors + warnings


# ── T1 · the file exists, is cited, and is loaded on the right condition ───────────────


def test_the_reference_exists_and_is_cited_by_its_skill():
    """An uncited reference is never loaded and reads as enforced anyway."""
    assert REF.is_file()
    assert "performance-lexicon.md" in BODY.read_text(encoding="utf-8")


def test_the_load_condition_is_any_person_in_frame_and_not_the_protagonist_flag(flat: str):
    """The eight stacked values that motivated this file sat on b-roll shots in a film whose
    presenter shots carried no expression at all. A condition keyed to the presenter role, or to
    `brief.protagonist`, would have been blind to the defect it was written for."""
    window = flat[: flat.index("## The defect")]
    assert "Any shot with a person in frame" in window
    assert "not* gated on `brief.protagonist`" in window


def test_it_declares_itself_company_neutral(flat: str):
    assert "**Company-neutral.**" in flat


# ── T2 · the examples agree with the linter ───────────────────────────────────────────


def test_the_file_carries_examples_in_both_directions(text: str):
    """A self-test over an empty set passes and proves nothing."""
    assert len(_fenced(text, "expression")) >= 6
    assert len(_fenced(text, "expression-bad")) >= 4


@pytest.mark.parametrize("example", _fenced(REF.read_text(encoding="utf-8"), "expression"))
def test_every_recommended_example_passes_the_linter(example: str):
    """The file may not recommend a direction the tree would refuse."""
    assert _findings(example) == [], f"the lexicon recommends {example!r}, and the linter objects"


@pytest.mark.parametrize("example", _fenced(REF.read_text(encoding="utf-8"), "expression-bad"))
def test_every_anti_example_fires(example: str):
    """An anti-example nothing catches is a claim the linter does not back."""
    assert _findings(example), f"the lexicon calls {example!r} wrong and nothing enforces that"


def test_the_shipped_stack_is_among_the_anti_examples(text: str):
    """The value that motivated the whole change is quoted, so the reason survives an edit."""
    bad = " ".join(_fenced(text, "expression-bad"))
    assert "jaw loose" in bad and "chin steady" in bad


# ── T3 · the grammar, and the four lenses ─────────────────────────────────────────────


def test_the_grammar_is_stated_as_a_form(flat: str):
    """A grammar described in prose is a preference; written as a form it is copyable."""
    assert "expression :=" in flat
    for slot in ("<one region>", "<what holds still>", "<gaze>"):
        assert slot in flat


@pytest.mark.parametrize(
    "rule",
    [
        "One primary moving region",  # the stack
        "magnitude word is mandatory",  # the unstated size
        "Say what holds still, and say it positively",  # the negation rule this field already has
        "Gaze is its own clause",
        "never stand alone",  # the register label
        "the change IS the performance",  # video timing
        "One channel per cue",  # face vs body vs voice
    ],
)
def test_each_grammar_rule_is_present(flat: str, rule: str):
    assert rule in flat


@pytest.mark.parametrize(
    "lens",
    [
        "The psychologist",
        "The behavioural psychologist",
        "The nonverbal analyst",
        "The screen actor",
    ],
)
def test_all_four_lenses_are_present(flat: str, lens: str):
    assert lens in flat


def test_the_low_arousal_correction_is_present(flat: str):
    """The single most load-bearing claim: this work lives in the low-arousal half, and models
    default to the high one. Without it the file is a list of muscles with no bias."""
    assert "low-arousal half" in flat
    assert "models" in flat and "default to high" in flat


def test_suppression_over_release_survives(flat: str):
    """Inherited from the story graph, and the reason the stop list is release-forms only."""
    assert "Suppression plays stronger than release" in flat


# ── T4 · the modulators are declared, never inferred ───────────────────────────────────


def test_the_modulators_are_declared_by_the_person(flat: str):
    """Reading culture, age or gender off a name or a photograph is the error this file must not
    teach. It names the tendencies so a stereotype is recognisable when it arrives in a prompt."""
    assert "never inferred from a name or a photograph" in flat
    assert "**Never write to these defaults.**" in flat
    assert "declared rather than assumed" in flat


def test_the_cultural_modulator_cuts_both_ways(flat: str):
    """It governs the performer AND how an audience reads it; only naming the first is half a rule."""
    assert "how an audience reads it" in flat


# ── T5 · the numbers and the vocabulary have one home ─────────────────────────────────


def test_the_ceiling_is_cited_and_never_restated(flat: str):
    """The region ceiling is the module's constant. A copy of it here is how the two disagree."""
    assert "shots_lint --rules" in flat
    assert f"ceiling of {sl._EXPRESSION_MAX_REGIONS}" not in flat
    assert f"more than {sl._EXPRESSION_MAX_REGIONS} regions" not in flat


def test_the_stop_list_is_cited_and_never_restated(flat: str):
    """Two copies of a closed list is one copy too many; the message names what it caught."""
    section = flat[flat.index("## 7. The refused vocabulary") :]
    banned_in_prose = [
        w for w in ("beaming", "sobbing", "hysterical", "ecstatic", "enraged") if w in section
    ]
    assert banned_in_prose == [], f"the refused list is restated in prose: {banned_in_prose}"


def test_the_four_landing_surfaces_are_named(flat: str):
    """A grammar that does not say which field each cue lands in is why body cues end up on faces."""
    for field in ("`expression`", "`motion_prompt`", "`visual`", "`instruction`"):
        assert field in flat


# ── T6 · carve standing ───────────────────────────────────────────────────────────────


def test_it_names_no_person_channel_or_company(text: str):
    """§R9. A reference derived from other people's published work names the TRADITION, never a
    practitioner — and this file ships in the public carve."""
    assert "@" not in text
    assert "http" not in text
    assert not re.search(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+'s\s+(?:method|model|system|technique)", text)


def test_it_cites_no_withheld_document(text: str):
    """A reference may not point at `docs/prds/` or `docs/archive/`: neither reaches the carve."""
    assert "docs/prds/" not in text
    assert "docs/archive/" not in text


def test_it_carries_a_provenance_note_with_claim_strength(flat: str):
    """Which lens is replicated science and which is practitioner observation is the difference
    between a reference and an assertion."""
    tail = flat[flat.index("*Provenance:") :]
    assert "replicated" in tail
    assert "practitioner-derived" in tail
    assert "population-level" in tail
    assert "No number on this page" in tail


def test_it_emits_no_gate_marker(text: str):
    """A reference that looks like it can refuse something teaches people it does."""
    assert "⟦GATE" not in text


def test_it_says_what_it_does_not_do(flat: str):
    """Including, specifically, that it is not a score — the non-goal the story PRD already set."""
    section = flat[flat.index("## What this file does not do") :]
    assert "does not score a face" in section
    assert "predictor of emotional response" in section
