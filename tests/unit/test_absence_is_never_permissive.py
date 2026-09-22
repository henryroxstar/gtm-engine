"""A value that is absent must never be read as permission.

Three of the 2026-09-21 prospecting defects were one shape: a field nobody filled in fell through
to the *granting* branch. A row the scorer had rejected reached the send list because ``finalize``
defaulted a missing ``verdict`` to ``send``. An unscored row was handed tier ``B``, which reads
downstream as "scored, and publishable". A campaign still in ``draft`` counted as finished,
because the retention gate blocked only ``active`` and let everything else through.

None of them looked like a bug in review. Each was one reasonable-looking default.

The registry below is the standing check. **When a change adds a decision — send/hold,
ready/blocked, finished/running, export/exclude — add it here**, and give it the four absent
inputs every real corpus produces: nothing at all, empty, whitespace, and a word nobody
anticipated. The last one matters most: it is how an open-ended rule ("anything that isn't
``active``") differs from a closed list of granting values. Only the closed list is safe to be
wrong about.
"""

from __future__ import annotations

import pytest

from gtm_core.connector_categories import BOUND, CATEGORIES, preflight
from gtm_core.lane_verdicts import LANE_VERDICTS
from gtm_core.prospects_item import new_account_defaults
from gtm_core.retention_campaign_gate import FINISHED_STATUSES, _status_of
from gtm_core.scorecard import Categorised, Scored, parse, score_row
from gtm_core.scorecard.evidence import GRANTING_VALUES as SCORECARD_GRANTING
from gtm_core.scorecard.evidence import Evidence
from gtm_core.scorecard.evidence import classify as scorecard_classify
from gtm_core.sequencers import _grants

#: Nothing at all, empty, whitespace, and a word the vocabulary has never heard.
ABSENT: tuple[object, ...] = (None, "", "   ", "wharrgarbl")


@pytest.mark.parametrize("value", ABSENT)
def test_a_campaign_with_no_usable_status_is_not_finished(value: object) -> None:
    """Not finished => the sweep refuses => the operator's data stays. Blocking is the safe way
    to be wrong, because the other way round deletes the only record of a do-not-contact."""
    manifest = {} if value is None else {"status": value}
    _label, finished = _status_of(manifest)
    assert not finished


def test_the_finished_vocabulary_is_a_closed_list() -> None:
    """The instrument check. If this set ever empties, every test above passes vacuously."""
    assert FINISHED_STATUSES
    assert "" not in FINISHED_STATUSES
    assert all(_status_of({"status": w})[1] for w in FINISHED_STATUSES)


@pytest.mark.parametrize("value", ABSENT[:3])
def test_an_unscored_row_is_given_no_tier(value: object) -> None:
    """A tier is derived from a score the caller SUPPLIED. No score, no tier — `B` would read
    downstream as "scored, and publishable", which is how an unscored row reached the list."""
    raw = {"company": "Northwind Ferries", "domain": "northwind-ferries.example"}
    if value is not None:
        raw["score"] = value
    assert new_account_defaults(raw)["tier"] == ""
    assert new_account_defaults(raw)["priority"] != "high"


def test_a_scored_row_still_gets_its_tier() -> None:
    """The other half: the rule above must not simply refuse everything."""
    scored = new_account_defaults({"company": "Northwind Ferries", "score": 9})
    assert scored["tier"] == "A"


def test_a_blank_verdict_admits_only_where_it_is_documented() -> None:
    """``generic`` admits an empty verdict on purpose — a seat-scoped generic email makes no
    per-recipient claim, so "the account is right and the argument isn't" is exactly its row.
    Every OTHER lane must refuse blank. A tier-B row at a dropped account carried a blank verdict
    and routed to ``generic``, which is how a competitor reached the send list; the fix belongs at
    the account level, and this test pins the blast radius so blank never spreads to a lane that
    does make a per-recipient claim."""
    admits_blank = {lane for lane, ok in LANE_VERDICTS.items() if "" in ok}
    assert admits_blank == {"generic"}


def test_drop_enrols_nowhere() -> None:
    assert not [lane for lane, ok in LANE_VERDICTS.items() if "drop" in ok]


#: Sequencer capability registry (SC5): a provider fact that is absent, false, unparsed, or
#: mis-typed must never be read as "the vendor supports this". Nothing at all, empty, whitespace,
#: a word nobody anticipated, the STRING "true" (not the literal boolean), and an int/list — every
#: shape a TOML author or a live-read response could actually produce that isn't `True` itself.
SEQUENCER_ABSENT: tuple[object, ...] = (None, "", "   ", "wharrgarbl", "true", 1, ["true"])


@pytest.mark.parametrize("value", SEQUENCER_ABSENT)
def test_a_capability_with_no_usable_supported_value_is_not_granted(value: object) -> None:
    """A capability row exists only if a vendor source was read (sequencers.toml rule 2); this
    is the runtime half of that rule — anything short of the literal boolean True refuses,
    exactly as a missing row does. `False` is covered separately below (it is a legitimate,
    citeable 'the vendor does not support this', not an absence)."""
    assert _grants(value) is False


def test_the_granting_value_is_a_closed_list_of_exactly_one() -> None:
    """The instrument check. If `_grants` ever widened to accept truthiness, every test above
    would pass vacuously — a wrongly-typed row would grant right along with a correct one."""
    assert _grants(True) is True
    assert _grants(False) is False
    for value in SEQUENCER_ABSENT:
        assert _grants(value) is False


#: Scorecard engine (PRD 2026-09-22): four decisions, each of which fell to the permissive branch
#: in the rubric this replaces. The engine's rule is that a row with a missing input gets a
#: CATEGORY naming the unlock — never a low score, because once both are an integer nobody can
#: tell "we never researched this" from "we researched this and it is weak".
SCORECARD_ABSENT: tuple[object, ...] = (None, "", "   ", "wharrgarbl")

#: A minimal card, inline so this registry reads standalone and needs no tenant data.
_SCORECARD_TOML = """
scorecard_version = "2026-01-01"
source = "knowledge/fixture.md#rubric"
ceiling = 30
tiers = { A = 25 }
bottom_tier = "B"
required_inputs = ["tier_label", "in_region", "researched", "agent_evidence"]

[category]
tier_label = "Unscored — no label"
in_region = "Blocked — outside the regions"
researched = "Unscored — no research on file"
agent_evidence = "Unscored — agent activity not assessed"

[[axis]]
name = "fit"
max = 10
input = "tier_label"
weights = { T1 = 10 }

[[axis]]
name = "evidence"
max = 16
input = "agent_evidence"
weights = { present = 16, industry_only = 6, absent = 2 }

[[axis]]
name = "timing"
max = 4
components = { in_region = 4 }
requires = { in_region = "researched" }
"""

_SCORECARD_ROW: dict[str, object] = {
    "tier_label": "T1",
    "in_region": True,
    "researched": True,
    "agent_evidence": "present",
}


def _scorecard():
    return parse(_SCORECARD_TOML, "absence-registry-fixture.toml")


@pytest.mark.parametrize("value", SCORECARD_ABSENT)
def test_an_unrecognised_agent_evidence_word_is_never_read_as_evidence(value: object) -> None:
    """The 35-row defect. The old guard was ``"no signal" not in text`` — an open-ended rule, so
    a negative research finding phrased any other way granted full credit."""
    assert scorecard_classify(value) == Evidence.NOT_ASSESSED


@pytest.mark.parametrize("value", ["Present", "PRESENT", " present ", "yes", "AGENT"])
def test_a_near_miss_is_not_a_classification(value: str) -> None:
    """The unknown-word case the registry's header calls the one that matters most. A case
    variant or a synonym is not the token; coercing it would reopen the open-ended rule."""
    assert scorecard_classify(value) == Evidence.NOT_ASSESSED


@pytest.mark.parametrize("value", SCORECARD_ABSENT)
def test_a_row_missing_a_required_input_categorises_rather_than_scoring(value: object) -> None:
    row = dict(_SCORECARD_ROW)
    row["agent_evidence"] = value
    result = score_row(_scorecard(), row)
    assert isinstance(result, Categorised)
    assert result.missing_input == "agent_evidence"


@pytest.mark.parametrize("value", (None, "", "   ", "true", "yes", 1, "wharrgarbl", False))
def test_a_boolean_gate_grants_on_the_literal_True_alone(value: object) -> None:
    """``in_target_market`` is an allow/deny. ``bool("false")`` is ``True``, which is how a
    string became a fact elsewhere in this repo — so the granting set here is a closed list of
    exactly one."""
    result = score_row(_scorecard(), dict(_SCORECARD_ROW) | {"in_region": value})
    assert isinstance(result, Categorised)
    assert result.missing_input == "in_region"


def test_the_scorecard_granting_vocabulary_is_a_closed_list() -> None:
    """The instrument check. If these sets emptied, every scorecard test above would pass while
    the engine refused valid input too."""
    assert SCORECARD_GRANTING == {"present", "industry_only", "absent"}
    for token in SCORECARD_GRANTING:
        assert scorecard_classify(token) == token
    assert isinstance(score_row(_scorecard(), _SCORECARD_ROW), Scored)


# ── connector categories: an unbound category is not a licence to improvise (SA4) ─────


@pytest.mark.parametrize("value", ABSENT)
def test_a_category_that_is_absent_or_unrecognised_refuses(value: object) -> None:
    """Five skills address a connector by category and none of the three is bound here.

    ``wharrgarbl`` is the case that matters, and it is why this is a closed list of granting
    values rather than a test for a blocking one: "we have never heard of this category" is not
    evidence that it is wired. Without the closed list, a typo in a skill body — `~~CRMs` — reads
    as a category nobody blocked, and the skill produces a deal-hygiene report over nothing that
    looks exactly like one built from real pipeline data.
    """
    verdict = preflight(value)
    assert not verdict.granted
    assert "refused" in verdict.message


@pytest.mark.parametrize("category", sorted(CATEGORIES))
def test_every_known_category_is_unbound_and_refuses_today(category: str) -> None:
    """The measured state, pinned. When a CRM is actually wired this test is what fails, which
    is the moment to check that ``agent/mcp_config.py`` wires it too — a category named here and
    not wired there refuses nothing while claiming to."""
    verdict = preflight(category)
    assert not verdict.granted
    assert category in verdict.message


def test_the_category_vocabulary_is_a_closed_non_empty_list() -> None:
    """The instrument check. An empty ``CATEGORIES`` makes every test above pass vacuously — and
    it would also make every real category unrecognised, which refuses for the wrong reason."""
    assert CATEGORIES
    assert "" not in CATEGORIES
    assert set(BOUND) == set(CATEGORIES), "a category with no BOUND row cannot ever be granted"


def test_a_bound_category_is_granted() -> None:
    """The other half: the rule must not simply refuse everything.

    Exercised through the injected table because every real category is unbound — a granting
    branch nothing runs is indistinguishable from one that does not work, and this one has to
    work the day a CRM is wired.
    """
    verdict = preflight("crm", bound={**BOUND, "crm": ("hubspot",)})
    assert verdict.granted
    assert verdict.connectors == ("hubspot",)


@pytest.mark.parametrize("value", ("", "   ", None))
def test_a_blank_connector_name_does_not_bind_a_category(value: object) -> None:
    """A half-written binding is absence, not permission: `{"crm": ("",)}` is a row somebody
    started and did not finish, and truthiness on the tuple alone would read it as wired."""
    assert not preflight("crm", bound={**BOUND, "crm": (value,)}).granted  # type: ignore[dict-item]
