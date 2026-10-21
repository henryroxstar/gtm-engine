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
    "signal_agent_kind": "ai",  # PH15: the word is derived from the record, which must be there
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
    del row["signal_agent_kind"]  # with no record, nothing supplied can stand in for one
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


@pytest.mark.parametrize("value", ABSENT + ("prospects",))
def test_a_bounce_source_other_than_emails_yields_no_rate(value: object) -> None:
    """PS20 T3.6 / TP §4.2: Bounce rate must only be derived from the per-email status block
    (`bounce_source == "emails"`). Any absent value, or the prospect-level fallback
    (`"prospects"`), or an unknown word, yields 'not available' rather than computing a rate."""
    from gtm_core.email_campaign_dashboard.views_results import _seq_bounce_rate

    live = {"bounce_source": value, "bounced": 5, "delivered": 95}
    assert _seq_bounce_rate(live) == '<span class="muted">not available</span>'


def test_the_emails_bounce_source_is_the_one_granting_value() -> None:
    """The instrument check for bounce_source: 'emails' DOES compute the rate, and enforces
    the strictly-above-BOUNCE_RISK_PCT threshold for the risk pill."""
    from gtm_core.email_campaign_dashboard.views_results import _seq_bounce_rate

    # denominator zero -> em dash
    assert (
        _seq_bounce_rate({"bounce_source": "emails", "bounced": 0, "delivered": 0})
        == '<span class="muted">—</span>'
    )

    # genuine 0 bounced -> 0.0%
    assert _seq_bounce_rate({"bounce_source": "emails", "bounced": 0, "delivered": 100}) == "0.0%"

    # 2.9% -> no pill
    assert _seq_bounce_rate({"bounce_source": "emails", "bounced": 29, "delivered": 971}) == "2.9%"

    # exactly 3.0% -> no pill (strictly greater than)
    assert _seq_bounce_rate({"bounce_source": "emails", "bounced": 30, "delivered": 970}) == "3.0%"

    # 3.1% -> pill
    assert (
        _seq_bounce_rate({"bounce_source": "emails", "bounced": 31, "delivered": 969})
        == "<span class='pill risk' data-risk='bounce-rate'>3.1%</span>"
    )


# R1.2 Missing File Degradation for AI Vocab
def test_missing_ai_vocabulary_file_refuses_to_initialize(monkeypatch):
    import pytest

    from gtm_core.web_sweep_hits import _determine_agent_kind

    def mock_resolve(profiles_root, p, filename, **kwargs):
        from pathlib import Path

        return Path("/does/not/exist")

    import gtm_core.web_sweep_hits as module

    monkeypatch.setattr(module, "resolve_knowledge_file", mock_resolve)

    with pytest.raises(FileNotFoundError):
        _determine_agent_kind("We are building agentic finance solutions.", profile="test")


# PRD-2026-09-28 Phase 2: closed-vocabulary write refusal on all three latest.json writers.
#
# This registry inverts the file's usual polarity on purpose. Everywhere else, an absent
# value must fall to the *non-granting* branch because "no opinion" was being read as
# permission. Here, blank/whitespace IS the safe, no-op branch already (a brand-new,
# not-yet-scored account legitimately has no verdict yet — see prospects_item.py's
# new_account_defaults, which never fills verdict/lane) — so ABSENT[1:3] ("", "   ") must
# pass through untouched, and only ABSENT[3] ("wharrgarbl", a word the vocabulary has never
# heard) must refuse. Refusing blank too would make ordinary merges of new accounts
# impossible; not refusing "wharrgarbl" is the exact 41-cell verdict-corruption incident.
from gtm_core import prospects_state as _prospects_state  # noqa: E402
from gtm_core.prospects_item import VocabularyRefusal, check_vocabulary  # noqa: E402


@pytest.mark.parametrize("field", ["verdict", "lane", "signal_agent_kind", "category_relation"])
@pytest.mark.parametrize("value", ABSENT[1:3])
def test_check_vocabulary_treats_blank_as_a_safe_no_op_not_a_grant(field: str, value: str) -> None:
    check_vocabulary(field, value, where="test")  # must not raise


@pytest.mark.parametrize("field", ["verdict", "lane", "signal_agent_kind", "category_relation"])
def test_check_vocabulary_refuses_the_unheard_of_word(field: str) -> None:
    with pytest.raises(VocabularyRefusal):
        check_vocabulary(field, ABSENT[3], where="test")


def test_mutate_account_absence_registry(tmp_path) -> None:
    """mutate_account: the CLI's `--set field=value` route into latest.json."""
    latest = _prospects_state.latest_path("acme", content_root=tmp_path)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(
        '{"kind": "prospects", "profile": "acme", '
        '"items": [{"account_id": "a-1", "company": "Northwind"}]}',
        encoding="utf-8",
    )
    for value in ABSENT[1:3]:
        _prospects_state.mutate_account(
            "acme", "a-1", {"verdict": value}, content_root=tmp_path
        )  # must not raise
    with pytest.raises(VocabularyRefusal):
        _prospects_state.mutate_account(
            "acme", "a-1", {"verdict": ABSENT[3]}, content_root=tmp_path
        )


def test_upsert_latest_absence_registry(tmp_path) -> None:
    """upsert_latest: the `merge --items <file>` route into latest.json.

    Unlike mutate_account (one account per call), a single upsert_latest call already IS a
    batch of many items (the real callers — `merge --items <file>`, signal_backfill.py's
    --promote — pass many at once), so a bad field is blanked and reported in
    `vocab_refused`, never raised: raising here would abort the whole unattended batch over
    one bad row, which is exactly what R7 (PRD-2026-09-28 §2.5) forbids."""
    for value in ABSENT[1:3]:
        summary = _prospects_state.upsert_latest(
            "acme",
            [{"company": "Fabrikam", "domain": "fabrikam.example", "verdict": value}],
            "run-1",
            content_root=tmp_path,
        )
        assert summary["vocab_refused"] == []
    summary = _prospects_state.upsert_latest(
        "acme",
        [{"company": "Litware", "domain": "litware.example", "verdict": ABSENT[3]}],
        "run-1",
        content_root=tmp_path,
    )
    assert len(summary["vocab_refused"]) == 1
    items = {
        i["domain"]: i for i in _prospects_state.load_latest("acme", content_root=tmp_path)["items"]
    }
    assert items["litware.example"].get("verdict", "") == ""


# --- the run scope: a product that is absent, unknown, ambiguous or half-built is never the default --


def _scope(profiles_root, **kw):
    from gtm_core import run_scope

    return run_scope.resolve("realshape", interactive=False, profiles_root=profiles_root, **kw)


def _assert_refused_and_not_the_default(result, code: str | None = None) -> None:
    from gtm_core import run_scope

    assert isinstance(result, run_scope.Refusal), f"resolved to {result!r}"
    if code:
        assert result.code == code


@pytest.mark.parametrize("value", ABSENT)
def test_a_product_that_is_absent_or_unknown_is_never_resolved_to_the_default(
    one_product_profiles, value: object
) -> None:
    """Nothing, empty, whitespace and an unheard-of word all refuse on a company with a second
    product. Only a closed list of real products resolves; a fall-through to the default product is
    the rev-1 bug (a Stream run reading Gateway's arguments)."""
    result = _scope(one_product_profiles, product=value)
    code = {None: "product-required", "wharrgarbl": "product-unknown"}.get(value)  # type: ignore[arg-type]
    _assert_refused_and_not_the_default(result, code)  # blank words refuse too, under any code


def test_an_interactive_run_with_no_product_asks_and_does_not_pick(one_product_profiles) -> None:
    from gtm_core import run_scope

    asked = run_scope.resolve("realshape", interactive=True, profiles_root=one_product_profiles)
    assert isinstance(asked, run_scope.Ask)


def test_two_products_that_normalise_to_the_same_name_are_ambiguous_not_the_first(
    one_product_profiles,
) -> None:
    profile = one_product_profiles / "realshape" / "PROFILE.md"
    text = profile.read_text(encoding="utf-8").replace(
        "{ slug: beta, name: Beta Ledger, capabilities: [beta] }",
        "{ slug: beta, name: Alpha-Relay, capabilities: [beta] }",
    )
    profile.write_text(text, encoding="utf-8")
    _assert_refused_and_not_the_default(
        _scope(one_product_profiles, product="alpha relay"), "product-ambiguous"
    )


def test_a_second_product_missing_a_required_file_is_not_offered_and_does_not_fall_back(
    one_product_profiles,
) -> None:
    (one_product_profiles / "realshape" / "products" / "beta" / "proof.toml").unlink()
    _assert_refused_and_not_the_default(
        _scope(one_product_profiles, product="beta"), "product-not-ready"
    )
    # Not offered either: with beta unfinished the company has ONE selectable product, so a run
    # with no product is the default product's, exactly as for a single-product company.
    from gtm_core import run_scope

    bare = run_scope.resolve("realshape", interactive=True, profiles_root=one_product_profiles)
    assert isinstance(bare, run_scope.RunScope) and not bare.is_second_product


def test_a_second_product_with_its_own_copy_of_a_company_wide_file_is_refused(
    one_product_profiles,
) -> None:
    (one_product_profiles / "realshape" / "products" / "beta" / "competitors.toml").write_text(
        '[[competitor]]\nname = "Elsewhere"\n', encoding="utf-8"
    )
    _assert_refused_and_not_the_default(
        _scope(one_product_profiles, product="beta"), "product-overrides-tenant-fact"
    )


def test_a_second_product_writing_fit_to_the_shared_ledger_is_refused(
    one_product_profiles, tmp_path, monkeypatch
) -> None:
    from gtm_core import prospects_state as ps

    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    with pytest.raises(ps.LedgerFitRefused):
        ps.upsert_latest(
            "realshape",
            [{"company": "Fictional Delta Co", "domain": "delta-fictional.example", "tier": "A"}],
            "run",
            product="beta",
        )


def test_a_sequence_the_map_cannot_place_is_unknown_and_never_the_default(
    one_product_profiles, tmp_path, monkeypatch
) -> None:
    """The outcome side of the same rule: a reply is credited to a product only when a campaign map
    or manifest says so. An unplaced sequence must not be counted as the default product's."""
    from gtm_core import campaign_products as cp

    content = tmp_path / "content"
    seq = content / "realshape" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "seq-x"\ncsv = "x.csv"\nspec = "x.md"\ncampaign = "unmapped-wave"\n'
    )
    (content / "realshape" / "plans" / "campaigns").mkdir(parents=True)
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    att = cp.attribute("realshape")
    assert att.by_sequence == {"seq-x": cp.UNKNOWN} and att.gaps


# --- the page-freshness check (status page freshness and provenance, 2026-10-02) ---------------
#
# `figures_stale_clause` and `verify_inventory` decide whether a status page may be called
# fresh. Each decision below has a granting branch ("nothing wrong was found") that an absent or
# malformed record must never reach.


def _meta_report(page, **meta):
    from gtm_core.page_inputs import Report

    return Report(page, meta=dict(meta), meta_present=True)


def test_a_page_with_no_inventory_is_stale_not_fresh(tmp_path) -> None:
    """Missing inventory -> stale. A page nobody recorded the inputs for cannot be shown current."""
    from gtm_core.page_inputs import verify_inventory

    page = tmp_path / "email_campaign_status.html"
    page.write_text("<html></html>", encoding="utf-8")
    rep = verify_inventory(page, tmp_path)
    assert rep.no_inventory and not rep.ok


def test_an_inventory_with_no_meta_is_stale_not_fresh(tmp_path) -> None:
    """Missing `meta` -> stale: "we cannot tell how old the figures were" is not "they were fine"."""
    from gtm_core.email_campaign_dashboard import freshness
    from gtm_core.page_inputs import Report

    rep = Report(tmp_path / "p.html")  # meta_present defaults to False
    clause = freshness.figures_stale_clause(rep, _NOW)
    assert clause and "predates figure tracking" in clause


@pytest.mark.parametrize("present", [None, "yes", "false", 1, 0, [], {}])
def test_a_meta_whose_figures_present_is_not_a_boolean_is_never_read_as_nothing_to_judge(
    tmp_path, present
) -> None:
    """`meta.figures_present` absent, or any type but a bool, used to fall into `not value ->
    nothing to judge -> fresh` (red team F11): a truncated or hand-built `meta` read fresh on any
    age. Only a recorded `False` means a tenant with no figures yet.
    Catches: restoring `if not rep.meta.get("figures_present")` in `freshness.figures_stale_clause`."""
    from gtm_core.email_campaign_dashboard import freshness

    meta = {"figures_fetched": "2025-01-01"}
    if present is not None:
        meta["figures_present"] = present
    clause = freshness.figures_stale_clause(_meta_report(tmp_path / "p.html", **meta), _NOW)
    assert clause and "predates figure tracking" in clause


def test_a_recorded_no_figures_is_the_one_value_that_is_nothing_to_judge(tmp_path) -> None:
    """The other half, so the refusal above is not "convict everything": a tenant that has never
    refreshed recorded `figures_present: false` and has no figures to be stale."""
    from gtm_core.email_campaign_dashboard import freshness

    rep = _meta_report(tmp_path / "p.html", figures_present=False, figures_fetched=None)
    assert freshness.figures_stale_clause(rep, _NOW) is None


@pytest.mark.parametrize(
    "fetched",
    ["2027-01-01", "2026-09-27T12:00:00Z", None, "", 12345, ["2026-09-25"], "wharrgarbl"],
)
def test_a_future_or_unusable_fetched_at_check_time_is_unknown_age_never_fresh(
    tmp_path, fetched
) -> None:
    """A `fetched` more than a day after the CHECK's own clock is as untrustworthy as one that
    does not parse: the age is unknown, and unknown counts as too old (§4.2). The raw value is
    never echoed into the sentence.
    Catches: widening the future tolerance in `health._figures_age_exact_days`, or treating a
    `None` age as fresh in `figures_stale_clause`."""
    from gtm_core.email_campaign_dashboard import freshness

    rep = _meta_report(tmp_path / "p.html", figures_present=True, figures_fetched=fetched)
    clause = freshness.figures_stale_clause(rep, _NOW)
    assert clause and "carry no usable date" in clause
    assert "wharrgarbl" not in clause


# --- the sidecar is untrusted: absence of a tracking key is not "nothing to track" (round 2, I3) ---


def _hand_inventory(tmp_path):
    from gtm_core import page_inputs

    root = tmp_path / "root"
    root.mkdir()
    (root / "a-hubspot.csv").write_text("x\n", encoding="utf-8")
    page = root / "p.html"
    page.write_text("<html></html>", encoding="utf-8")
    inv = page_inputs.write_inventory(page, (root, ["*-hubspot.csv"]), scope="all")
    return root, page, inv


@pytest.mark.parametrize("value", ABSENT)
@pytest.mark.parametrize("key", ["inputs", "globs", "page_sha256"])
def test_a_tracking_key_that_is_absent_or_unusable_never_reads_as_nothing_to_track(
    tmp_path, key, value
) -> None:
    """`inv.get("inputs", [])` / `inv.get("globs", [])` / `inv.get("page_sha256") and ...` turned a
    missing key into the empty case — the granting branch. The four absent inputs every corpus
    produces (nothing, empty, whitespace, an unheard-of word) are all stale for all three keys.
    Catches: restoring any of the three defaults in `page_inputs.verify_inventory`."""
    import json

    from gtm_core import page_inputs

    root, page, inv = _hand_inventory(tmp_path)
    assert page_inputs.verify_inventory(page, root).ok, "the control: unmodified is fresh"
    rec = json.loads(inv.read_text(encoding="utf-8"))
    if value is None:
        del rec[key]
    else:
        rec[key] = value
    inv.write_text(json.dumps(rec), encoding="utf-8")
    assert not page_inputs.verify_inventory(page, root).ok


@pytest.mark.parametrize("value", ABSENT)
@pytest.mark.parametrize("field", ["scope", "slugs"])
@pytest.mark.parametrize(
    "page", ["email_campaign_status.html", "campaign-open.html", "campaign-x.html"]
)
def test_a_sidecar_with_no_usable_scope_or_slugs_never_retires_its_page(page, field, value) -> None:
    """Retirement exempts a page from the check, so it is the most permissive verdict there is and
    may not be reached from an absent field (red team C1). With NO manifests at all — the
    condition under which a sidecar naming a campaign reads "orphaned" — none of the absent
    inputs retires any page, the rollup least of all.
    Catches: returning RETIRED from `pages.scope_of` without the file-name agreement."""
    from gtm_core.email_campaign_dashboard import pages

    rec = {"scope": "campaign", "slugs": ["x"]}
    if value is None:
        del rec[field]
    else:
        rec[field] = value
    found = pages.scope_of(rec, page, pages.Manifests([], ()))
    assert found.kind != pages.RETIRED


@pytest.mark.parametrize("value", (*ABSENT, "yes", "true", False))
def test_a_sequencer_unsubscribe_flag_that_is_not_exactly_yes_records_no_opt_out(
    value: object, tmp_path
) -> None:
    """An unsubscribe row becomes an open opt-out that can reach a permanent, add-only DNC list, so
    the granting value is a closed list of one (2026-10-06) and anything else records nothing."""
    from gtm_core import sequencer_unsubscribes as su

    seq = tmp_path / "p" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "S1"\ncsv = "a.csv"\nspec = "a.md"\n', encoding="utf-8"
    )
    row = {"Sequence Id": "S1", "Step Number": 1, "Recipient Email": "a@example.test"}
    if value is not None:
        row["Unsubscribed"] = value
    assert su.plan("p", [row], tmp_path).new == []
    # the instrument: the same row with the one granting value IS recorded, so the test above
    # cannot pass because plan() records nothing at all
    row["Unsubscribed"] = "Yes"
    assert [c.email for c in su.plan("p", [row], tmp_path).new] == ["a@example.test"]


# ── the outreach log (2026-10-08): who was emailed, and what is known about their sequence ─────


def _outreach_world(tmp_path):
    from tests.unit._outreach_world import NOW, PROFILE, make_world

    root, seq = make_world(tmp_path)
    return root, seq, NOW, PROFILE


def _patch_row(root, profile, sid: str, edit) -> None:
    import json

    stats = root / profile / "prospects" / "sequences" / ".pool" / "sequence-stats.json"
    doc = json.loads(stats.read_text(encoding="utf-8"))
    for row in doc["sequences"]:
        if row["sequenceId"] == sid:
            edit(row)
    stats.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("value", ABSENT)
def test_an_outreach_log_person_is_emailed_no_only_from_a_present_zero_counter(
    value: object, tmp_path
) -> None:
    """``emailed = no`` is a promise that this person was not emailed. A counter that is missing,
    blank or unreadable must give 'unknown', never 'no'.
    Catches: `emailed = NO if not counter else ...` (a falsy test instead of `== 0`)."""
    from gtm_core.outreach_people import build_view
    from tests.unit._outreach_world import STAGED, P

    root, _seq, now, profile = _outreach_world(tmp_path)
    _patch_row(root, profile, STAGED, lambda r: r["prospects"][0].__setitem__("contacted", value))
    ida = [r for r in build_view(profile, root, now=now).rows if r["email_address"] == P["ida"]][0]
    assert ida["emailed"] == "unknown"
    # the instrument: with the real zero it IS 'no', so the line above cannot pass vacuously
    _patch_row(root, profile, STAGED, lambda r: r["prospects"][0].__setitem__("contacted", "0"))
    ida = [r for r in build_view(profile, root, now=now).rows if r["email_address"] == P["ida"]][0]
    assert ida["emailed"] == "no"


@pytest.mark.parametrize("value", ABSENT)
def test_an_outreach_log_sequence_with_no_usable_flag_is_status_unknown(
    value: object, tmp_path
) -> None:
    """The provider's flag is the only thing that says a sequence is sending. Missing, blank or
    unrecognised is 'Status unknown' — never 'Paused', never 'Loaded, not started'.
    Catches: a final `else: PAUSED` in the state decision."""
    from gtm_core.outreach_model import state_words
    from gtm_core.outreach_people import build_view
    from tests.unit._outreach_world import PAUSED, P

    root, _seq, now, profile = _outreach_world(tmp_path)
    _patch_row(root, profile, PAUSED, lambda r: r.__setitem__("status", value))
    eli = [r for r in build_view(profile, root, now=now).rows if r["email_address"] == P["eli"]][0]
    assert eli["sequence_state"] == state_words()["unknown"]


@pytest.mark.parametrize("value", ("", "   ", "wharrgarbl"))
def test_an_outreach_log_suppression_row_with_no_usable_reason_still_suppresses(
    value: str, tmp_path
) -> None:
    """Only a closed three-reason bookkeeping set leaves a person alone. A ledger row whose reason
    is blank, or a word nobody anticipated, is a person someone chose to exclude.
    Catches: treating an unrecognised reason as harmless."""
    from gtm_core.outreach_people import build_view
    from tests.unit._outreach_world import P

    root, seq, now, profile = _outreach_world(tmp_path)
    ledger = seq / ".pool" / "suppression.csv"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + f"{P['ada']},,,{value},2026-10-08,\n", encoding="utf-8"
    )
    ada = [r for r in build_view(profile, root, now=now).rows if r["email_address"] == P["ada"]][0]
    assert ada["person_status"] == "suppressed"


@pytest.mark.parametrize("value", ABSENT)
def test_an_outreach_log_closed_out_row_with_no_usable_outcome_is_still_closed_out(
    value: object, tmp_path
) -> None:
    """A person in a closed-out file WAS contacted. An outcome nobody can read must not turn them
    back into a fresh prospect.
    Catches: dropping or defaulting a row whose outcome cell is not a known word."""
    from gtm_core.outreach_people import build_view
    from tests.unit._outreach_world import P

    root, seq, now, profile = _outreach_world(tmp_path)
    path = seq / "contacted-2026-10-06.csv"
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"{P['ned']},Ned,southfield.example,,1,{'' if value is None else value},manual\n",
        encoding="utf-8",
    )
    ned = [r for r in build_view(profile, root, now=now).rows if r["email_address"] == P["ned"]][0]
    assert (ned["person_status"], ned["emailed"], ned["outcome"]) == (
        "closed-out",
        "yes",
        "unclassified",
    )


@pytest.mark.parametrize("value", ABSENT)
def test_an_outreach_log_sequence_with_no_usable_counter_is_counted_not_silent(
    value: object, tmp_path
) -> None:
    """The close-out warning is silent for a healthy sequence, so silence could mean 'checked and
    fine' or 'could not check'. A sequence it cannot check must be COUNTED.
    Catches: `if not emailed: continue` before the not-checkable count."""
    from gtm_core.outreach_people import build_view
    from tests.unit._outreach_world import PAUSED

    root, _seq, now, profile = _outreach_world(tmp_path)
    base = build_view(profile, root, now=now)
    _patch_row(root, profile, PAUSED, lambda r: r["prospects"][0].__setitem__("contacted", value))
    after = build_view(profile, root, now=now)
    assert PAUSED not in {f.sequence_id for f in after.findings}  # nothing to say ...
    assert after.not_checkable == base.not_checkable + 1  # ... and it says it could not look


@pytest.mark.parametrize("value", ABSENT)
def test_a_revenuecat_product_with_no_usable_id_credits_nothing(value: object) -> None:
    """The live top-up webhook turns a product id into dollars. An id that is absent, blank, or a
    word nobody listed must be worth nothing -- it used to fall through to the event's
    ``price_in_purchased_currency``, so a 1000-yen purchase credited $1000 (MM15)."""
    from backend.routers.webhooks import _map_revenuecat_product_credits

    assert _map_revenuecat_product_credits(value) == 0.0  # type: ignore[arg-type]


def test_a_revenuecat_credit_pack_is_still_worth_its_price() -> None:
    """The instrument for the test above: the mapper is not simply returning zero."""
    from backend.routers.webhooks import _map_revenuecat_product_credits

    assert _map_revenuecat_product_credits("credits_25_usd") == 25.0


# ── MM3: a failed debit refuses the NEXT call ─────────────────────────────────────────────
#
# `workspace_wallets.debit_failed_at` is NULL while every charge has been recorded, and set when a
# metered call ran but its debit could not be written. The gate is a closed list of GRANTING
# values of exactly one — NULL — so a value nobody anticipated refuses rather than slips through.


def _wallet_with_marker(marker: object) -> dict[str, object]:
    return {"balance_usd": 10.0, "balance_credits": 10_000.0, "debit_failed_at": marker}


#: Everything a marker column could hold that is not NULL: a timestamp, and the absent-looking
#: shapes (an empty string, whitespace, a word nobody listed, 0, False) that a truthiness test
#: would read as "no failure".
FAILED_DEBIT_MARKERS: tuple[object, ...] = (
    datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
    "",
    "   ",
    "wharrgarbl",
    0,
    False,
)


def _mcp_guard(wallet_row: dict[str, object]) -> bool:
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from mcp_server.meter import check_budget

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=wallet_row)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="OK")
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return asyncio.run(check_budget("33333333-3333-4333-8333-333333333333", pool))


@pytest.mark.parametrize("marker", FAILED_DEBIT_MARKERS)
def test_a_wallet_with_any_failed_debit_marker_refuses_the_next_call(marker: object) -> None:
    """Refuse on ANYTHING but NULL — even with a $10 balance — and refuse with the billing error,
    not the "top up" one. ``0`` and ``False`` matter most: ``if marker:`` would call them clean."""
    from gtm_core.metering import BillingUnavailableError

    with pytest.raises(BillingUnavailableError):
        _mcp_guard(_wallet_with_marker(marker))


def test_a_wallet_with_no_failed_debit_marker_is_still_admitted() -> None:
    """The instrument check: the refusals above are not simply refusing every wallet."""
    assert _mcp_guard(_wallet_with_marker(None)) is True


# ── MM1: an UNPRICED provider refuses; only the exact "byok" waives a price ───────────────────
#
# Before 2026-10-15 a provider with no configured price was billed at $0.00 (an env default of
# "0"), which is the whole defect of this file in one line. Two decisions here: is this call
# priced, and is this call free. Priced needs a finite number > 0 in the committed registry;
# FREE is a closed list of exactly one — the literal key source "byok".


def _registry_with_price(tmp_path, value: object):
    """A one-provider registry whose ``usd_per_unit`` is ``value`` (``None`` ⇒ key omitted)."""
    price_line = "" if value is None else f'usd_per_unit = "{value}"\n'
    path = tmp_path / "provider_costs.toml"
    path.write_text(
        '[provider.rocketreach]\nclass = "data"\nunit = "lookup"\n'
        f'status = "estimate"\nsource = "fixture"\n{price_line}',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("value", ABSENT)
def test_a_provider_with_no_usable_price_is_unpriced_never_free(value: object, tmp_path) -> None:
    from gtm_core.provider_registry import UnpricedProviderError, unit_price

    with pytest.raises(UnpricedProviderError):
        unit_price("rocketreach", "lookup", registry_path=_registry_with_price(tmp_path, value))


def test_a_provider_with_a_real_price_is_priced() -> None:
    """The instrument check: the refusals above are not simply refusing every provider."""
    from gtm_core.provider_registry import unit_price

    assert unit_price("rocketreach", "lookup") > 0
    assert unit_price("apollo", "credit") > 0


@pytest.mark.parametrize("value", ABSENT)
def test_an_absent_key_source_never_waives_the_price(value: object) -> None:
    """Unset / blank is the single-tenant VPS and is PRICED; a word nobody anticipated is
    refused. Neither may come out as 0.0 — only the literal "byok" does."""
    from gtm_core.provider_registry import ProviderRegistryError, usd_per_unit_for

    try:
        cost = usd_per_unit_for("rocketreach", "lookup", value)  # type: ignore[arg-type]
    except ProviderRegistryError:
        return
    assert cost > 0


def test_the_waiver_is_a_closed_list_of_exactly_one() -> None:
    from gtm_core.provider_registry import KEY_SOURCES, usd_per_unit_for

    waived = [s for s in KEY_SOURCES if usd_per_unit_for("rocketreach", "lookup", s) == 0.0]
    assert waived == ["byok"]


# ── DS-SCHEMA-01: [design_system] policy enums & booleans never fail open on absence ──────────

_VALID_DESIGN_SYSTEM: dict[str, object] = {
    "archetype": "mission-control",
    "panel_style": "glass-hairline",
    "product_override_policy": "suite_gradient_and_logo_only",
    "background_plates_in_ui": False,
    "require_2d_telemetry": True,
    "forbid_gradient_fill_buttons": True,
    "denied_fonts": ["Legacy Sans"],
    "denied_hexes": ["#112233"],
}


@pytest.mark.parametrize(
    "field_name",
    [
        "archetype",
        "panel_style",
        "product_override_policy",
    ],
)
@pytest.mark.parametrize("value", ABSENT)
def test_design_system_enum_field_with_absent_or_unknown_value_is_refused(
    field_name: str, value: object
) -> None:
    from gtm_core.brandkit import validate_design_system_policy

    ds = dict(_VALID_DESIGN_SYSTEM)
    if value is None:
        del ds[field_name]
    else:
        ds[field_name] = value
    ok, errors = validate_design_system_policy({"design_system": ds})
    assert not ok
    assert any("DS-SCHEMA-01" in e and field_name in e for e in errors)


@pytest.mark.parametrize(
    "bool_field",
    [
        "background_plates_in_ui",
        "require_2d_telemetry",
        "forbid_gradient_fill_buttons",
    ],
)
@pytest.mark.parametrize("value", (None, "", "   ", "wharrgarbl", "false", "true", 0, 1))
def test_design_system_boolean_fields_require_strict_bool(bool_field: str, value: object) -> None:
    from gtm_core.brandkit import validate_design_system_policy

    ds = dict(_VALID_DESIGN_SYSTEM)
    if value is None:
        del ds[bool_field]
    else:
        ds[bool_field] = value
    ok, errors = validate_design_system_policy({"design_system": ds})
    assert not ok
    assert any("DS-SCHEMA-01" in e and bool_field in e for e in errors)


def test_design_system_valid_policy_is_admitted() -> None:
    from gtm_core.brandkit import validate_design_system_policy

    ok, errors = validate_design_system_policy({"design_system": dict(_VALID_DESIGN_SYSTEM)})
    assert ok
    assert errors == []


# ── Video craft (PRD 2026-10-19 R2/R4/R5/R13): a missing picture plan is never a pass ─────────
#
# Four decisions: is this cut's kind of picture known, does its scene have a measured motion
# class, is an unplanned list "checked", and does a shot with no duration count toward the still
# share. `gtm_core.video_craft` is withheld from the public cut, so each test skips itself there;
# the motion table and the brand kit are pinned so nothing reads the live profile or the table
# being re-measured.


def _craft(monkeypatch):
    pytest.importorskip("gtm_core.video_craft")
    from tests.unit import video_craft_fixtures as fx

    fx.isolate(monkeypatch)
    return fx


@pytest.mark.parametrize("value", ABSENT + (["diagram"], {"bad": "class"}, 123))
def test_a_cut_with_no_usable_asset_class_is_refused_when_craft_is_required(
    monkeypatch, value: object
) -> None:
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import hook

    shots = [fx.shot(1, "presenter", 3.0, role="presenter"), fx.shot(2, "photo_broll", 3.0)]
    shots.append(fx.shot(3, None, 3.0))
    shots[2]["asset"] = {} if value is None else {"class": value}
    findings, _ = hook.craft_findings(fx.doc(shots), require_craft=True)
    refused = {(f.rule_id, f.cut) for f in findings if f.severity == "error"}
    assert refused & {(hook.ASSET_MISSING, 3), (hook.ASSET_UNKNOWN, 3)}


def test_a_list_that_plans_some_cuts_and_not_others_is_refused_naming_them(monkeypatch) -> None:
    """No flag needed: once a list has a picture plan, a cut without one is an error."""
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import hook

    shots = [fx.shot(1, "presenter", 3.0, role="presenter"), fx.shot(2, None, 3.0)]
    shots.append(fx.shot(3, None, 3.0))
    findings, _ = hook.craft_findings(fx.doc(shots), require_craft=False)
    assert {(f.rule_id, f.cut) for f in findings if f.severity == "error"} == {
        (hook.ASSET_MISSING, 2),
        (hook.ASSET_MISSING, 3),
    }


def test_every_known_asset_class_is_still_admitted(monkeypatch) -> None:
    """The instrument check: the refusals above are not refusing every class."""
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import hook
    from gtm_core.video_craft.classes import ASSET_CLASS_NAMES

    shots = [fx.shot(i, kind, 3.0) for i, kind in enumerate(ASSET_CLASS_NAMES, 1)]
    findings = hook.asset_findings(shots)
    assert findings == []


@pytest.mark.parametrize("scene", (*ABSENT[1:], "Still-Push", " still-push", "still-push "))
def test_a_scene_with_no_recorded_motion_class_is_refused(monkeypatch, scene: str) -> None:
    """Exact key only: case and whitespace variants of a measured scene do not match it."""
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import hook, motion

    shot = fx.shot(1, "ui_mock", 3.0, scene=scene)
    assert motion.derive(shot).cls is None
    findings, _ = hook.craft_findings(fx.doc([shot]), require_craft=True)
    assert (hook.MOTION_UNCLASSIFIED, 1) in {(f.rule_id, f.cut) for f in findings}


def test_a_screen_ui_with_no_scene_counts_as_still_never_as_moving(monkeypatch) -> None:
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import motion

    shot = fx.shot(1, "ui_mock", 3.0, scene="still-push")
    shot["production"]["screen_ui"] = {}
    assert motion.derive(shot).cls == "static"


def test_a_measured_scene_still_derives_its_class(monkeypatch) -> None:
    """The instrument check: the stub table is being read, so the refusals above are real."""
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import motion

    assert motion.derive(fx.shot(1, "ui_mock", 3.0, scene="still-push")).cls == "static"
    moving = fx.shot(1, "diagram", 3.0, scene="diagram-reveal", backend="html")
    assert motion.derive(moving).cls == "moving"


@pytest.mark.parametrize("runtime", (None, "", "   ", "wharrgarbl", "vps", "desktop"))
def test_an_unplanned_list_outside_the_headless_brain_is_not_checked_never_a_pass(
    monkeypatch, runtime: object
) -> None:
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import craft_verdict as cv
    from gtm_core.video_craft import hook

    if runtime is not None:
        monkeypatch.setenv("GTM_RUNTIME", runtime)
    bare = fx.doc([fx.shot(n, None, 3.0) for n in (1, 2, 3)])
    findings, not_checked = hook.craft_findings(bare, require_craft=False)
    assert findings == []
    assert any(n.startswith("craft") for n in not_checked)
    verdict = cv.craft_verdict(bare, require_craft=False)
    assert "pass" not in {line.status for line in verdict.lines}


def test_an_unplanned_list_under_the_headless_brain_is_refused_without_the_flag(
    monkeypatch,
) -> None:
    fx = _craft(monkeypatch)
    from gtm_core.video_craft import hook

    monkeypatch.setenv("GTM_RUNTIME", "headless")
    bare = fx.doc([fx.shot(n, None, 3.0) for n in (1, 2, 3)])
    findings, _ = hook.craft_findings(bare, require_craft=False)
    assert {(f.rule_id, f.severity) for f in findings} == {(hook.ASSET_MISSING, "error")}


@pytest.mark.parametrize("value", (*ABSENT, 0, -1.0, False, True))
def test_a_shot_with_no_usable_duration_is_never_counted_as_zero_seconds(value: object) -> None:
    """Neither 0 s still (which would lower the share) nor 0 s moving: the share is unjudgeable."""
    pytest.importorskip("gtm_core.video_craft")
    from gtm_core.video_craft import share

    shots = [{"n": n, "duration_s": 3.0} for n in (1, 2, 3)]
    if value is None:
        del shots[0]["duration_s"]
    else:
        shots[0]["duration_s"] = value
    result = share.planned_static_share(shots, ["static", "moving", "moving"])
    assert result.share is None
    assert result.reason
    assert not result.warns


def test_a_shot_with_a_real_duration_is_counted() -> None:
    """The instrument check: valid durations produce a share."""
    pytest.importorskip("gtm_core.video_craft")
    from gtm_core.video_craft import share

    shots = [{"n": n, "duration_s": 3.0} for n in (1, 2, 3)]
    result = share.planned_static_share(shots, ["static", "moving", "moving"])
    assert result.share == pytest.approx(1 / 3)


# ── always-on cockpit hardening (2026-10-19): three more "absent is not permission" decisions ──────
# (the unknown-update-age case needs real Update objects, so it lives in tests/cockpit/test_guard.py
# ::test_f2_2_unknown_age_is_stale; the unreadable-state cases in tests/unit/test_cockpit_state.py.)


@pytest.mark.parametrize("value", ABSENT)
def test_a_button_with_no_usable_epoch_is_never_this_boots(value: object) -> None:
    """Accept/refuse a gate button: an absent, empty, blank or unheard-of epoch must read as 'from before
    a restart' (refused), never as the current boot."""
    from cockpit import epoch

    epoch.set_epoch("abc123")
    try:
        assert epoch.split(str(value or ""))[1] != epoch.current()
    finally:
        epoch.set_epoch("")


def test_a_missing_heartbeat_is_never_healthy(tmp_path) -> None:
    """Healthy/unhealthy: no heartbeat file is unhealthy (docker's start_period covers boot)."""
    from cockpit import health

    assert health.age_s(health.heartbeat_path(tmp_path)) is None
    # and the process-level check agrees: exit code 1
    import os
    import subprocess
    import sys

    rc = subprocess.run(
        [sys.executable, "-m", "cockpit.health"],
        env={**os.environ, "GTM_CONTENT_ROOT": str(tmp_path)},
        capture_output=True,
    ).returncode
    assert rc == 1


@pytest.mark.parametrize("value", ("", "   ", "wharrgarbl"))
def test_a_dispatch_state_nobody_anticipated_blocks_a_repeat_send(value: str) -> None:
    """May a batch go: only 'no record' and the closed word 'released' grant. A blank or unheard-of state is
    a row we cannot read, and a send that cannot prove it is not a duplicate must not guess."""
    from gtm_core.dispatch_intents import decide

    assert not decide({"a@x.example": value}, ["a@x.example"]).ok
    assert decide(
        {}, ["a@x.example"]
    ).ok  # nothing recorded at all is a first send (the only open door)
    assert decide({"a@x.example": "released"}, ["a@x.example"]).ok


def test_an_unrecorded_outcome_reads_as_unknown_and_blocks(tmp_path) -> None:
    """A claim with no outcome row (the worker died between the intent and the outcome) blocks."""
    import asyncio
    from types import SimpleNamespace

    from gtm_core.dispatch_intents import FileIntentStore
    from gtm_core.ledgers import Ledgers

    store = FileIntentStore(Ledgers(SimpleNamespace(content_root=tmp_path), "example"))
    assert asyncio.run(store.claim("seq", ["a@x.example"], tool="t")).ok
    assert not asyncio.run(store.claim("seq", ["a@x.example"], tool="t")).ok


def test_an_unreadable_intent_ledger_refuses_rather_than_reads_as_empty(tmp_path) -> None:
    import asyncio
    from types import SimpleNamespace

    import pytest as _pytest

    from gtm_core.dispatch_intents import FileIntentStore, IntentLedgerUnreadable
    from gtm_core.ledgers import Ledgers

    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "dispatch-intents.jsonl").write_text(
        "[1, 2]\n"
    )  # valid JSON, not a row
    store = FileIntentStore(Ledgers(SimpleNamespace(content_root=tmp_path), "example"))
    with _pytest.raises(IntentLedgerUnreadable):
        asyncio.run(store.claim("seq", ["a@x.example"], tool="t"))


# ── W1: one-call agent setup (PRD 2026-10-19a) ───────────────────────────────


@pytest.mark.parametrize("value", [None, "", "   ", "enabled-ish"])
def test_the_provision_route_stays_shut_unless_unambiguously_on(monkeypatch, value) -> None:
    from backend.routers.provision import SETTING, provision_enabled

    if value is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, value)
    assert provision_enabled() is False


@pytest.mark.parametrize("value", [None, "", "   ", "not a key!"])
def test_a_missing_or_malformed_idempotency_key_is_refused_not_defaulted(value) -> None:
    from fastapi import HTTPException

    from backend.routers.provision import valid_idempotency_key

    with pytest.raises(HTTPException) as e:
        valid_idempotency_key(value)
    assert e.value.status_code == 422


@pytest.mark.parametrize("value", [None, "", "  ", "paused-ish"])
def test_a_provisioning_state_nobody_anticipated_is_never_read_as_fine(value) -> None:
    import asyncio
    from types import SimpleNamespace

    from backend.deps import WorkspaceCtx
    from backend.services import provision as svc
    from gtm_core.capabilities import Entitlement

    req = svc.ProvisionRequest(name="n", profile_name="p", key_label="k")
    ws = WorkspaceCtx("u", "00000000-0000-0000-0000-000000000001", Entitlement.PRO)
    with pytest.raises(RuntimeError):
        asyncio.run(svc._advance(None, SimpleNamespace(), ws, {"state": value}, req, None))
