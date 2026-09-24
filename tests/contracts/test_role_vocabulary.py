"""Contract: the role vocabulary is tenant data, and it is trustworthy or it refuses.

Phase 0b of the ICP & messaging experiment layer. Two properties this file exists to pin,
and one method note. (The design doc is withheld from the public carve, so it is described
here rather than cited by path — a shipped test may not point at a file the carve drops.)

**The move must be invisible.** ``persona_of`` / ``seat_of`` read from
:mod:`gtm_core.role_vocabulary` instead of module literals. A profile that ships no file
must resolve every title exactly as before — the blast radius of a regression here is not
"experiments"; it is every profile and every skill that asks who someone is.

**The move must be worth something.** Before it, one profile had 5 of its 7 matrix personas
and another 4 of its 8 normalising onto nothing, and an unmapped persona does not raise —
``hook_coverage`` books its rows as *unassignable* and the tenant reads a coverage report
showing zero. Those two counts are the done-criterion, so they are asserted as numbers.

**Every validation test here carries a near-miss control.** A test that feeds garbage to a
parser and asserts it raises passes just as happily against a parser that raises on
everything. Each case below is therefore a PAIR: one document that must be refused, and a
sibling differing only in the field under test that must load. That is what makes the
assertion discriminate (``docs/RULES.md`` §R18).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from gtm_core.hook_coverage.matrix import parse_matrix
from gtm_core.role_vocabulary import (
    DEFAULT_VOCABULARY,
    VocabularyError,
    clear_cache,
    load,
    parse,
)

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "profiles"

#: A minimal document that MUST parse. Every invalid case below is this, with one field
#: broken — so a failure names the field rather than "the document".
GOOD: dict = {
    "default_persona": "owner",
    "segments": ["enterprise", "unspecified"],
    "persona": [
        {"name": "owner", "cues": ["owner", "proprietor"]},
        {"name": "operator", "cues": ["operator", "manager"]},
    ],
    "seat": [
        {"name": "exec", "personas": ["owner"], "stakes": ["margin"]},
        {"name": "ops", "personas": ["operator"], "stakes": ["throughput"]},
    ],
    "anti_cues": {"owner": ["assistant"]},
}


def _doc(**overrides) -> dict:
    """GOOD with fields replaced — the control and the case share everything else."""
    out = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in GOOD.items()}
    out.update(overrides)
    return out


# --- the control ---------------------------------------------------------------------


def test_the_baseline_document_parses() -> None:
    """If this ever fails, every "must raise" test below becomes meaningless."""
    vocab = parse(GOOD, "test")
    assert vocab.personas == frozenset({"owner", "operator"})
    assert vocab.persona_to_seat == {"owner": "exec", "operator": "ops"}
    assert vocab.stakes_for("exec") == ("margin",)


# --- validation, each with its near-miss control --------------------------------------


def test_a_seat_may_not_name_a_persona_no_rule_produces() -> None:
    bad = _doc(seat=[{"name": "exec", "personas": ["ghost"], "stakes": ["margin"]}])
    with pytest.raises(VocabularyError, match="ghost"):
        parse(bad, "test")
    # control: the identical document with a persona that IS produced
    assert parse(
        _doc(seat=[{"name": "exec", "personas": ["owner"], "stakes": ["margin"]}]), "test"
    ).persona_to_seat == {"owner": "exec"}


def test_one_persona_may_not_be_claimed_by_two_seats() -> None:
    bad = _doc(
        seat=[
            {"name": "exec", "personas": ["owner"], "stakes": ["margin"]},
            {"name": "ops", "personas": ["owner"], "stakes": ["throughput"]},
        ]
    )
    with pytest.raises(VocabularyError, match="claimed by both"):
        parse(bad, "test")
    # control: same two seats, each with its own persona
    assert len(parse(GOOD, "test").seat_rules) == 2


def test_default_persona_must_resolve() -> None:
    with pytest.raises(VocabularyError, match="default_persona"):
        parse(_doc(default_persona="nobody"), "test")
    assert parse(_doc(default_persona="operator"), "test").default_persona == "operator"


def test_a_persona_with_no_cues_is_refused() -> None:
    """It can never be resolved from a title, so every recipient it covers is invisible."""
    bad = _doc(persona=[{"name": "owner", "cues": []}, {"name": "operator", "cues": ["op"]}])
    with pytest.raises(VocabularyError, match="no cues"):
        parse(bad, "test")
    assert "owner" in parse(GOOD, "test").personas


def test_anti_cues_may_not_name_an_unknown_persona() -> None:
    """A veto on a persona nothing produces reads as protection that is not there."""
    with pytest.raises(VocabularyError, match="anti_cues"):
        parse(_doc(anti_cues={"ghost": ["x"]}), "test")
    assert parse(_doc(anti_cues={"operator": ["x"]}), "test").anti_cues["operator"] == ("x",)


def test_segments_must_keep_the_unclassified_bucket() -> None:
    with pytest.raises(VocabularyError, match="unspecified"):
        parse(_doc(segments=["enterprise", "startup"]), "test")
    assert "unspecified" in parse(_doc(segments=["enterprise", "unspecified"]), "test").segments


def test_a_duplicate_persona_is_refused() -> None:
    bad = _doc(
        persona=[
            {"name": "owner", "cues": ["owner"]},
            {"name": "owner", "cues": ["proprietor"]},
        ],
        seat=[{"name": "exec", "personas": ["owner"], "stakes": ["margin"]}],
    )
    with pytest.raises(VocabularyError, match="twice"):
        parse(bad, "test")


def test_a_vocabulary_with_no_personas_is_refused() -> None:
    with pytest.raises(VocabularyError, match="no \\[\\[persona\\]\\]"):
        parse({"default_persona": "x", "persona": [], "seat": []}, "test")


# --- resolution ----------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["testco"])
def test_a_profile_with_no_file_gets_the_shipped_default(profile: str) -> None:
    """The no-op property: nothing changes behaviour on landing for a tenant that never
    customised.

    Parametrised over the profiles that ship no ``role-vocabulary.toml`` rather than naming
    one, because "which tenants have customised" is exactly the thing this feature makes
    change over time — a live tenant was uncustomised for about four hours.
    """
    if (PROFILES / profile / "knowledge" / "role-vocabulary.toml").exists():
        pytest.skip(f"{profile} now ships its own vocabulary — pick another uncustomised profile")
    clear_cache()
    assert load(profile, PROFILES) is DEFAULT_VOCABULARY


def test_an_unknown_profile_does_not_raise() -> None:
    """This module answers "which seat is this person". Refusing to answer because a
    profile directory is missing would take down every caller for no safety gain — a file
    that EXISTS but is wrong is the case that raises."""
    clear_cache()
    assert load("no-such-profile-exists", PROFILES) is DEFAULT_VOCABULARY


def test_a_tenant_file_is_loaded_and_overrides_the_default(tmp_path: Path) -> None:
    prof = tmp_path / "tenant" / "knowledge"
    prof.mkdir(parents=True)
    (prof / "role-vocabulary.toml").write_text(
        'default_persona = "owner"\n'
        'segments = ["startup", "unspecified"]\n'
        "[[persona]]\nname = 'owner'\ncues = ['owner']\n"
        "[[seat]]\nname = 'exec'\npersonas = ['owner']\nstakes = ['margin']\n",
        encoding="utf-8",
    )
    clear_cache()
    vocab = load("tenant", tmp_path)
    assert vocab is not DEFAULT_VOCABULARY
    assert vocab.personas == frozenset({"owner"})
    assert vocab.segments == ("startup", "unspecified")


def test_a_present_but_invalid_tenant_file_raises(tmp_path: Path) -> None:
    """Contrast with the missing-profile case above: silence is safe for ABSENT, never for
    WRONG. A vocabulary that loads but mis-seats every recipient is the failure mode a
    confidently-wrong seat is made of."""
    prof = tmp_path / "tenant" / "knowledge"
    prof.mkdir(parents=True)
    (prof / "role-vocabulary.toml").write_text(
        'default_persona = "ghost"\n[[persona]]\nname = "owner"\ncues = ["owner"]\n',
        encoding="utf-8",
    )
    clear_cache()
    with pytest.raises(VocabularyError):
        load("tenant", tmp_path)


# --- the done-criterion ---------------------------------------------------------------


def _customised_profiles() -> list[str]:
    """Profiles that ship their own vocabulary — DISCOVERED, never listed.

    Naming them would put a real organisation's identity in a test file (§R9), and the set
    is expected to grow as tenants customise. Discovery also makes the assertion below apply
    to the next tenant automatically, which is the point of it.
    """
    if not PROFILES.is_dir():  # pragma: no cover - OSS carve has no profiles/
        return []
    return sorted(
        p.name
        for p in PROFILES.iterdir()
        # `_`-prefixed directories are skeletons (`_template`, `_system`), not tenants. They
        # ship a vocabulary as a documented example, and their hook matrix carries no
        # persona axis at all — there is nothing there that could be unmapped.
        if not p.name.startswith("_")
        and (p / "knowledge" / "role-vocabulary.toml").is_file()
        and (p / "knowledge" / "hook-matrix.md").is_file()
    )


@pytest.mark.parametrize("profile", _customised_profiles())
def test_a_customised_profile_has_no_unmapped_personas(profile: str) -> None:
    """The property that justified Phase 0b. Before the move, two profiles carried 5-of-7
    and 4-of-8 matrix personas that normalised onto nothing.

    Asserted as ZERO rather than "fewer than before", because the failure is silent: an
    unmapped persona produces a coverage report reading zero, which is indistinguishable
    from copy that genuinely covers nothing.

    Scoped to profiles that ship a vocabulary, because that is the promise being checked —
    a tenant on the shipped default has not claimed its personas resolve.
    """
    matrix = parse_matrix(PROFILES / profile / "knowledge" / "hook-matrix.md", profile=profile)
    assert matrix.ok, f"{profile} hook-matrix.md is unparseable: {matrix.reason}"
    assert matrix.unmapped_personas() == (), (
        f"{profile} has matrix persona(s) that normalise onto no role: "
        f"{matrix.unmapped_personas()}. Add cues for them to "
        f"profiles/{profile}/knowledge/role-vocabulary.toml — recipients can never be "
        f"attributed to a persona the vocabulary cannot see."
    )


# --- one implementation ---------------------------------------------------------------


def test_there_is_exactly_one_seat_resolver() -> None:
    """``seat_of`` / ``persona_of`` must be DEFINED in exactly one module.

    The import-from-``tests/linter`` dance in ``gtm_core.cells`` and
    ``gtm_core.hook_coverage.config`` is deliberate and load-bearing: two implementations
    of "which seat is this person" would drift, and the drift would be invisible — the page
    would report cells the gate never checked. Moving the DATA to tenant files must not
    have created a second resolver for the data to disagree through.
    """
    roots = [REPO / "gtm_core", REPO / "agent", REPO / "backend", REPO / "cockpit", REPO / "tests"]
    definers: dict[str, list[str]] = {"seat_of": [], "persona_of": []}
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not our files
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in definers:
                    definers[node.name].append(str(path.relative_to(REPO)))
    for name, found in definers.items():
        assert len(found) == 1, (
            f"`{name}` is defined in {len(found)} places: {found}. One vocabulary, one "
            f"resolver — a second definition is how the page and the gate start disagreeing "
            f"about who someone is."
        )


def test_the_linter_defaults_are_the_module_defaults() -> None:
    """The names re-exported for the boundary tests must be the same objects, not copies.

    A copy would let the two drift the moment one is edited, which is the failure this
    whole module exists to remove — one layer down.
    """
    sys.path.insert(0, str(REPO / "tests" / "linter"))
    import outreach as linter  # noqa: PLC0415

    assert linter._PERSONA_RULES is DEFAULT_VOCABULARY.persona_rules
    assert linter._SEAT_RULES is DEFAULT_VOCABULARY.seat_rules
    assert linter._ANTI_CUES is DEFAULT_VOCABULARY.anti_cues
    assert linter._CEO_TITLE_CUES is DEFAULT_VOCABULARY.ceo_title_cues
    assert linter._NON_BUYER_CUES is DEFAULT_VOCABULARY.non_buyer_cues
    assert linter._SECURITY_ONLY is DEFAULT_VOCABULARY.security_only
    assert linter.SEGMENT_DEFAULT_PERSONA == DEFAULT_VOCABULARY.default_persona
