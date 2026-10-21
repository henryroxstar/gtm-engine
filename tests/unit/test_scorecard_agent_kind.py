"""PH15: the agent-evidence points come from the row's own signal record, never beside it.

The 2026-09-23 rescore took ``agent_evidence`` as a separate input and never looked at the
``signal_agent_kind`` the same row carried. The ledger then said "company-specific agent evidence
(30 of 30)" on rows whose record said the clause involved no agents at all, and "no agent evidence
found (4 of 30)" on rows whose record said ``ai``. Two claims about one account, contradicting
each other on the same row, and nothing checked.

So the engine now derives the word from the record and refuses a caller who says otherwise:

* ``ai`` -> ``present``; ``none`` / ``human`` -> ``absent`` (``industry_only`` may refine it);
* ``unclear`` or no kind at all -> **not assessed**: the row is categorised, never given the 30;
* a kind outside the closed set -> an error, not a default;
* a supplied word that contradicts the kind -> an error naming the row.

Fixtures are fictional per §R9.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.scorecard import Categorised, ScoreCardError, Scored, parse, score_row, score_rows
from gtm_core.scorecard.cli import main
from gtm_core.scorecard.record import COMPATIBLE, KIND_TO_EVIDENCE, agent_evidence_from_record
from gtm_core.signal_record import AgentKind

CARD_TOML = """
scorecard_version = "2026-01-01"
source = "knowledge/fixture.md#rubric"
ceiling = 50
tiers = { A = 40, B = 25 }
bottom_tier = "C"
required_inputs = ["tier_label", "agent_evidence"]

[category]
tier_label = "Unscored — no label"
agent_evidence = "Unscored — agent activity not assessed"

[[axis]]
name = "fit"
max = 20
input = "tier_label"
weights = { T1 = 20 }

[[axis]]
name = "evidence"
max = 30
input = "agent_evidence"
weights = { present = 30, industry_only = 12, absent = 4 }

[axis.phrases]
present = "company-specific agent evidence on the row"
industry_only = "agent activity in the sector but nothing company-specific"
absent = "researched and no agent evidence found"
"""

#: A card with no agent-evidence axis at all: the record is not its business.
NO_AGENT_CARD_TOML = """
scorecard_version = "2026-01-01"
source = "knowledge/fixture.md#rubric"
ceiling = 20
tiers = { A = 15 }
bottom_tier = "B"
required_inputs = ["tier_label"]

[category]
tier_label = "Unscored — no label"

[[axis]]
name = "fit"
max = 20
input = "tier_label"
weights = { T1 = 20 }
"""

BASE = {"id": "aldermoor-labs", "company": "Aldermoor Labs", "tier_label": "T1"}


@pytest.fixture
def card():
    return parse(CARD_TOML, "fixture.toml")


def _agent_points(result) -> str:
    assert isinstance(result, Scored), result
    return result.derivation[1]


# --- 1. The kind decides the points ----------------------------------------------------------


def test_kind_ai_with_nothing_supplied_scores_present(card) -> None:
    result = score_row(card, BASE | {"signal_agent_kind": "ai"})
    assert _agent_points(result) == "company-specific agent evidence on the row (30 of 30)"
    assert result.score == 50


def test_kind_none_with_nothing_supplied_scores_absent(card) -> None:
    result = score_row(card, BASE | {"signal_agent_kind": "none"})
    assert _agent_points(result) == "researched and no agent evidence found (4 of 30)"
    assert result.score == 24


def test_kind_human_scores_absent_because_the_agents_are_people(card) -> None:
    result = score_row(card, BASE | {"signal_agent_kind": "human"})
    assert _agent_points(result) == "researched and no agent evidence found (4 of 30)"


def test_a_supplied_word_that_agrees_with_the_kind_is_accepted(card) -> None:
    agreed = score_row(card, BASE | {"signal_agent_kind": "ai", "agent_evidence": "present"})
    derived = score_row(card, BASE | {"signal_agent_kind": "ai"})
    assert agreed == derived


def test_industry_only_may_refine_a_kind_that_names_no_company_agent(card) -> None:
    """``none`` says THIS clause holds no company-specific agent fact; sector-level activity found
    elsewhere is a refinement of that, not a contradiction. Without this the card's
    ``industry_only`` weight would be unreachable from any record."""
    result = score_row(
        card, BASE | {"signal_agent_kind": "none", "agent_evidence": "industry_only"}
    )
    assert _agent_points(result) == (
        "agent activity in the sector but nothing company-specific (12 of 30)"
    )


# --- 2. A contradiction is refused, naming the row -------------------------------------------


@pytest.mark.parametrize(
    "kind,supplied",
    [
        ("none", "present"),  # the 15 ledger rows scored 30 with kind `none`
        ("human", "present"),
        ("ai", "absent"),  # the 9 ledger rows scored 4 with kind `ai`
        ("ai", "industry_only"),
    ],
)
def test_a_supplied_word_that_contradicts_the_kind_is_refused(card, kind, supplied) -> None:
    row = BASE | {"signal_agent_kind": kind, "agent_evidence": supplied}
    with pytest.raises(ScoreCardError) as exc:
        score_rows(card, [BASE | {"signal_agent_kind": "ai"}, row])
    message = str(exc.value)
    assert "row 2" in message
    assert "aldermoor-labs" in message
    assert kind in message and supplied in message


def test_the_cli_refuses_a_contradiction_with_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "profiles" / "fixture-co" / "knowledge"
    profile.mkdir(parents=True)
    (profile / "scorecard.toml").write_text(CARD_TOML, encoding="utf-8")
    items = tmp_path / "items.json"
    items.write_text(
        json.dumps([BASE | {"signal_agent_kind": "none", "agent_evidence": "present"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))

    code = main(["score", "--profile", "fixture-co", "--items", str(items), "--json"])

    captured = capsys.readouterr()
    assert code == 1
    assert "aldermoor-labs" in captured.err
    assert captured.out == ""  # nothing half-scored reaches the caller


# --- 3. No resolved kind is not assessed: categorised, never the 30 ---------------------------


@pytest.mark.parametrize("kind", [None, "", "unclear"])
@pytest.mark.parametrize("supplied", [None, "present", "absent"])
def test_an_unresolved_kind_categorises_whatever_was_supplied(card, kind, supplied) -> None:
    row = dict(BASE)
    if kind is not None:
        row["signal_agent_kind"] = kind
    if supplied is not None:
        row["agent_evidence"] = supplied
    result = score_row(card, row)
    assert isinstance(result, Categorised)
    assert result.missing_input == "agent_evidence"
    assert result.category == "Unscored — agent activity not assessed"


# --- 4. The kind vocabulary is closed ---------------------------------------------------------


@pytest.mark.parametrize("kind", ["robot", "AI", " ai", "   ", 1, True])
def test_a_kind_outside_the_closed_set_is_an_error_not_a_default(card, kind) -> None:
    with pytest.raises(ScoreCardError) as exc:
        score_row(card, BASE | {"signal_agent_kind": kind})
    assert "signal_agent_kind" in str(exc.value)
    assert "aldermoor-labs" in str(exc.value)


def test_the_mapping_is_exhaustive_over_the_record_vocabulary() -> None:
    """If ``AgentKind`` grows a member, this goes red until someone decides what it grants."""
    kinds = {v for k, v in vars(AgentKind).items() if not k.startswith("_")}
    assert set(KIND_TO_EVIDENCE) == kinds
    assert kinds == {"ai", "human", "none", "unclear"}


def test_the_derivation_is_callable_on_its_own() -> None:
    assert agent_evidence_from_record({"signal_agent_kind": "ai"}) == "present"
    assert agent_evidence_from_record({"signal_agent_kind": "none"}) == "absent"
    assert agent_evidence_from_record({}) == "not_assessed"


# --- 5. Scope ----------------------------------------------------------------------------------


def test_a_card_without_an_agent_axis_ignores_the_record() -> None:
    card = parse(NO_AGENT_CARD_TOML, "no-agent.toml")
    result = score_row(card, BASE | {"signal_agent_kind": "robot", "agent_evidence": "present"})
    assert isinstance(result, Scored)
    assert result.score == 20


def test_an_excluded_row_is_not_asked_about_its_record() -> None:
    card = parse(CARD_TOML + '\n[exclusion]\npartner = "Partner"\n', "excl.toml")
    result = score_row(card, BASE | {"exclusion": "partner", "signal_agent_kind": "robot"})
    assert isinstance(result, Categorised)
    assert result.category == "Partner"


def test_both_tables_cover_the_same_kinds() -> None:
    assert set(COMPATIBLE) == set(KIND_TO_EVIDENCE)


# --- 6. A caller is told where the axis comes from (review M1) --------------------------------


def test_explain_names_signal_agent_kind_as_the_source_of_the_axis(
    tmp_path, monkeypatch, capsys
) -> None:
    profile = tmp_path / "profiles" / "fixture-co" / "knowledge"
    profile.mkdir(parents=True)
    (profile / "scorecard.toml").write_text(CARD_TOML, encoding="utf-8")
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))

    assert main(["explain", "--profile", "fixture-co"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    derived = payload["derived_inputs"]["agent_evidence"]
    assert derived["from"] == "signal_agent_kind"
    assert derived["supply_only"] == ["industry_only"]
    assert "signal_agent_kind" in captured.err


def test_explain_says_nothing_is_derived_for_a_card_without_the_axis(
    tmp_path, monkeypatch, capsys
) -> None:
    profile = tmp_path / "profiles" / "fixture-co" / "knowledge"
    profile.mkdir(parents=True)
    (profile / "scorecard.toml").write_text(NO_AGENT_CARD_TOML, encoding="utf-8")
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))

    assert main(["explain", "--profile", "fixture-co", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["derived_inputs"] == {}
