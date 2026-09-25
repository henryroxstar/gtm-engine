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

from datetime import UTC, datetime

import pytest

from gtm_core.connector_categories import BOUND, CATEGORIES, preflight
from gtm_core.email_campaign_dashboard import health
from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS
from gtm_core.lane_verdicts import LANE_VERDICTS
from gtm_core.prospect_lede import go_live
from gtm_core.prospects_item import new_account_defaults
from gtm_core.retention_campaign_gate import FINISHED_STATUSES, _status_of
from gtm_core.scorecard import Categorised, Scored, parse, score_row
from gtm_core.scorecard.evidence import GRANTING_VALUES as SCORECARD_GRANTING
from gtm_core.scorecard.evidence import Evidence
from gtm_core.scorecard.evidence import classify as scorecard_classify
from gtm_core.sequencers import _grants
from tests.contracts.test_dashboard_colour_reasons import reason_violations
from tests.test_email_campaign_dashboard import _page, _seed

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


# ── PS20 Task 13 / TP §4.2: go-live evidence, figures age, colour reasons ─────────────
#
# Three more refuse-on-absence decisions from the campaign-page restructure, each paired with
# a deliberately permissive stand-in that shows the decision would have granted under the
# naive/older shape of the rule — so a passing test here is never vacuous.


#: `go_live`'s `contacted` argument (PS20 P1.10): missing, blank, unparseable text, a
#: numeric-LOOKING string, a float, and the bool trap — `isinstance(True, int)` is True in
#: Python, so a bare truthy check would grant `started` on a value that was never a count.
GO_LIVE_ABSENT_CONTACTED: tuple[object, ...] = (
    None,
    "",
    "   ",
    "wharrgarbl",
    "5",
    5.0,
    True,
    False,
)


@pytest.mark.parametrize("contacted", GO_LIVE_ABSENT_CONTACTED)
def test_go_live_never_reads_started_without_a_real_positive_count(contacted: object) -> None:
    """`started` is the dashboard's one claim that a real person has actually been emailed
    (`GO_LIVE_WORDS["started"]` = "started, people have been contacted"). `contacted` must be
    a genuine `int > 0`; anything else — missing, blank, a non-numeric word, a numeric-LOOKING
    string, a float, or a bare bool — is not evidence and must fall through exactly as no
    evidence at all would."""
    assert go_live([], contacted, True, readable=True) != "started"


def test_go_live_is_unknown_whenever_the_snapshot_is_unreadable() -> None:
    """`readable=False` must win over every other input — a torn snapshot must never be graded
    on whatever evidence happens to already sit in its (unreliable) parsed fields."""
    for statuses, contacted, on_record in (
        ([], 0, False),
        (["active"], 50, True),
        ([], None, True),
        (["paused"], 9, True),
    ):
        assert go_live(statuses, contacted, on_record, readable=False) == "unknown"


def test_a_real_positive_count_still_reads_started() -> None:
    """The other half: the rule must not simply refuse everything."""
    assert go_live([], 5, True, readable=True) == "started"


def test_a_naive_truthy_check_is_the_permissive_bug_this_entry_refuses() -> None:
    """The instrument check. A stand-in that grants on ANY truthy `contacted` — the shape of
    bug this registry entry exists to catch — DOES read a non-numeric string and a bare `True`
    as `started`. The real rule refuses both, which is what proves the parametrized refusal
    above is exercising a real branch rather than passing vacuously."""

    def naive(contacted: object) -> str:
        return "started" if contacted else "staged"

    assert naive("wharrgarbl") == "started"
    assert naive(True) == "started"
    assert go_live([], "wharrgarbl", True, readable=True) != "started"
    assert go_live([], True, True, readable=True) != "started"


#: `health.figures_age_days`'s `fetched` argument (PS20 T1.11): missing, blank, unparseable
#: text, and the wrong TYPE entirely (an int or a list can arrive if a producer's schema ever
#: drifts) — none of these may read as "no age constraint", because that is exactly what would
#: let a page show sending numbers from a snapshot nobody can date as if they were current.
FIGURES_AGE_ABSENT: tuple[object, ...] = (None, "", "   ", "wharrgarbl", 12345, ["2026-09-01"])

_NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("fetched", FIGURES_AGE_ABSENT)
def test_an_unparseable_fetched_with_rows_present_counts_as_old_never_fresh(
    fetched: object,
) -> None:
    status = {"sequences": [{"id": "S1"}], "snapshot": {"fetched": fetched, "unreadable": False}}
    assert "figures-old" in health.page_warnings(status, {"ok": True}, True, _NOW)


def test_a_fresh_readable_fetched_raises_no_warning() -> None:
    """The other half: a genuinely fresh, parseable `fetched` must not be swept into the
    refusal by an over-eager rule."""
    status = {
        "sequences": [{"id": "S1"}],
        "snapshot": {"fetched": "2026-09-25", "unreadable": False},
    }
    assert health.page_warnings(status, {"ok": True}, True, _NOW) == []


def test_a_naive_none_is_fresh_rule_is_the_permissive_bug_this_entry_refuses() -> None:
    """The instrument check. Treating "the age could not be computed" as "no age problem"
    (``age is None or age <= MAX`` — `None` short-circuits the `or` as true) is the permissive
    shape this entry refuses: it WOULD call a garbage `fetched` fresh. The real
    `page_warnings` reads that same `None` as too old to trust."""

    def naive_is_fresh(fetched: object, now: datetime) -> bool:
        age = health.figures_age_days(fetched, now)
        return age is None or age <= FIGURES_MAX_AGE_DAYS

    assert naive_is_fresh("wharrgarbl", _NOW) is True
    status = {
        "sequences": [{"id": "S1"}],
        "snapshot": {"fetched": "wharrgarbl", "unreadable": False},
    }
    assert "figures-old" in health.page_warnings(status, {"ok": True}, True, _NOW)


def test_a_warn_or_risk_class_with_no_listed_reason_is_a_violation(tmp_path) -> None:
    """T1.5's refusal, registered here rather than re-derived: a coloured element that names
    no reason — or one outside the closed `WARN_REASONS`/`RISK_REASONS` vocabulary — is a
    violation. `reason_violations` (tests/contracts/test_dashboard_colour_reasons.py) is the
    one checker every warn/risk site on the real page is judged by; reused here rather than
    re-implemented, so this entry and that contract can never quietly disagree. This shape
    doesn't fit the file's pure-function pattern above — it needs a rendered page — so it is
    the one entry in this section that takes `tmp_path`."""
    page = _page(tmp_path, _seed(tmp_path))
    assert reason_violations(page) == []  # the real page: nothing coloured without a reason

    # The permissive stand-in: a warn-painted element that names NO reason at all.
    missing = page.replace("</style>", ".zz{color:var(--warn)}</style>").replace(
        "</body>", '<span class="zz">x</span></body>'
    )
    assert reason_violations(missing) != []

    # ...and one that names a reason outside the closed vocabulary.
    unlisted = page.replace("</style>", ".zz{color:var(--risk)}</style>").replace(
        "</body>", '<span class="zz" data-risk="not-a-real-reason">x</span></body>'
    )
    assert reason_violations(unlisted) != []
