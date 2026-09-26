"""The lane router — every pooled row ends in exactly one lane, never stranded.

Fictional fixtures only (§R9). Each refusal ships with a positive control.
"""

from __future__ import annotations

import csv
import datetime
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from gtm_core import lanes
from gtm_core.account_integrity import CompetitorHit
from gtm_core.adjudication import Adjudication
from gtm_core.lanes import decisions as dec
from gtm_core.lanes.context import RouterContext
from gtm_core.merge_hygiene import row_signal_freshness
from gtm_core.prospects_consolidate.confidence import org_token

AS_OF = datetime.date(2026, 9, 3)


def _row(**kw) -> dict:
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": "jordan.vance@vertex.example",
        "title": "Chief Information Security Officer",
        "company": "Vertex Systems",
        "company_domain": "vertex.example",
        "segment": "enterprise",
        "tier": "B",
        "score": "80",
        "why_now": "Vertex Systems opened an AI governance program covering autonomous agents",
        "signal_observed": "2026-08-20",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
        "account_id": "",
        "suppression": "",
        "hook_cell": "enterprise|generic",
    }
    base.update(kw)
    return base


def _rec(email: str, verdict: str, **kw) -> Adjudication:
    base = {"score": 3, "repair_attempt": 0, "body_hash": "h1", "touch": 1}
    base.update(kw)
    return Adjudication(email=email, verdict=verdict, **base)


def _ctx(**kw) -> RouterContext:
    ctx = RouterContext(profile="acme", as_of=AS_OF)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def _lane(row, recs=None, ctx=None, **kw) -> lanes.Routed:
    return lanes.route_row(row, ctx or _ctx(), recs, **kw)


# --------------------------------------------------------- partition + precedence


def test_every_row_lands_in_exactly_one_lane():
    """The property the whole design rests on: no row in two lanes, none in zero."""
    rng = random.Random(7)
    verdicts = ["send", "re-angle", "drop", ""]
    judges = [None, "send", "re-angle", "drop"]
    classes = ["", "fact-creates-problem", "wrong-entity-type", "wrong-person", "brand-new-class"]
    rows, recs = [], []
    for i in range(300):
        email = f"p{i}@acct{i % 40}.example"
        rows.append(
            _row(
                email=email,
                company=f"Acct {i % 40}",
                company_domain=f"acct{i % 40}.example",
                verdict=rng.choice(verdicts),
                tier=rng.choice(["A", "B", "C", ""]),
                signal_observed=rng.choice(["2026-08-20", "2025-01-01", ""]),
                why_now=rng.choice([_row()["why_now"], ""]),
            )
        )
        jv = rng.choice(judges)
        if jv:
            recs.append(
                _rec(email, jv, defect_class=rng.choice(classes), repair_attempt=rng.choice([0, 3]))
            )
    ctx = _ctx(competitors={org_token("acct3.example", "Acct 3"): CompetitorHit("adjacent", "x")})
    ctx.prior_emails = {"p5@acct5.example"}
    result = lanes.route(rows, recs, ctx)
    assert sum(result.counts.values()) == len(rows)
    assert all(r.lane in lanes.LANES for r in result.routed)
    assert len({r.email for r in result.routed}) == len(rows)


def test_lane_csvs_are_disjoint_and_cover_the_input(tmp_path):
    rows = [
        _row(email="a@x.example", company="X", company_domain="x.example"),
        _row(email="b@y.example", company="Y", company_domain="y.example", verdict="re-angle"),
        _row(email="c@z.example", company="Z", company_domain="z.example", verdict="drop"),
        _row(
            email="d@w.example",
            company="W",
            company_domain="w.example",
            suppression="out-of-market",
        ),
    ]
    recs = [
        _rec("a@x.example", "send"),
        _rec("b@y.example", "re-angle", defect_class="fact-creates-problem"),
    ]
    result = lanes.route(rows, recs, _ctx(), calibrated=True)
    paths = lanes.write_lanes(result, tmp_path, "2026-09-03")
    seen: dict[str, str] = {}
    for lane, path in paths.items():
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = reader.fieldnames or []
            assert set(lanes.LANE_COLUMNS) <= set(cols)
            assert ("signal_clause" in cols) == (lane in ("personalised", "repair")), lane
            for r in reader:
                assert r["email"] not in seen, f"{r['email']} in both {seen[r['email']]} and {lane}"
                seen[r["email"]] = lane
                assert r["lane"] == lane and r["lane_reason"].startswith(lane)
    assert set(seen) == {r["email"] for r in rows}
    assert seen == {
        "a@x.example": "personalised",
        "b@y.example": "repair",
        "c@z.example": "hold",
        "d@w.example": "excluded",
    }


def test_lane_csv_headers_have_no_duplicate_columns(tmp_path):
    """PS4 regression: `MASTER_COLS` already carries `lane`/`lane_reason`/`judge_defect_class`
    (the last via `JUDGE_COLUMNS`), so a naive `[*MASTER_COLS, *LANE_COLUMNS]` wrote every
    lane CSV's header twice for all three. `test_lane_csvs_are_disjoint_and_cover_the_input`
    only checks `LANE_COLUMNS` is a SUBSET of the header, which a duplicated superset still
    satisfies — it cannot catch this."""
    rows = [
        _row(email="a@x.example", company="X", company_domain="x.example"),
        _row(email="b@y.example", company="Y", company_domain="y.example", verdict="re-angle"),
    ]
    recs = [_rec("a@x.example", "send")]
    result = lanes.route(rows, recs, _ctx())
    paths = lanes.write_lanes(result, tmp_path, "2026-09-03")
    for lane, path in paths.items():
        with path.open(newline="", encoding="utf-8") as fh:
            header = csv.DictReader(fh).fieldnames or []
        assert len(header) == len(set(header)), f"{lane}: duplicate columns in {header}"


def test_hold_beats_every_other_lane():
    """A perfect personalised candidate on the adjacent-competitor list still holds."""
    ctx = _ctx(
        competitors={
            org_token("vertex.example", "Vertex Systems"): CompetitorHit(
                "adjacent", "Vertex (adjacent)"
            )
        }
    )
    r = _lane(_row(), [_rec("jordan.vance@vertex.example", "send")], ctx)
    assert r.lane == "hold" and r.trigger == "competitor-adjacent"


def test_excluded_beats_hold():
    ctx = _ctx(
        competitors={org_token("vertex.example", "Vertex Systems"): CompetitorHit("adjacent", "x")}
    )
    r = _lane(_row(suppression="out-of-market"), None, ctx)
    assert r.lane == "excluded" and r.trigger == "suppressed"
    r = _lane(
        _row(),
        None,
        _ctx(
            competitors={
                org_token("vertex.example", "Vertex Systems"): CompetitorHit("direct", "x")
            }
        ),
    )
    assert r.lane == "excluded" and r.trigger == "competitor-direct"


def test_repair_cap_falls_to_generic_not_hold():
    r = _lane(_row(), [_rec("jordan.vance@vertex.example", "re-angle", repair_attempt=3)])
    assert r.lane == "generic" and "cap" in r.detail
    r = _lane(_row(), [_rec("jordan.vance@vertex.example", "re-angle", repair_attempt=1)])
    assert r.lane == "repair"


# --------------------------------------------------------- scope routing


def test_account_scope_judge_drop_holds_and_argument_scope_repairs():
    held = _lane(
        _row(),
        [
            _rec(
                "jordan.vance@vertex.example",
                "drop",
                defect_class="wrong-entity-type",
                note="sells this",
            )
        ],
    )
    assert (
        held.lane == "hold"
        and held.trigger == "judge-account-scope"
        and held.judge_note == "sells this"
    )
    repaired = _lane(
        _row(),
        [_rec("jordan.vance@vertex.example", "drop", defect_class="fact-creates-no-problem")],
    )
    assert repaired.lane == "repair" and repaired.judge_defect_class == "fact_earns_its_place"
    contact = _lane(
        _row(), [_rec("jordan.vance@vertex.example", "drop", defect_class="Wrong-Person")]
    )
    assert contact.lane == "repair" and contact.judge_scope == "contact"


def test_unknown_defect_scope_repairs_never_holds():
    r = _lane(
        _row(),
        [_rec("jordan.vance@vertex.example", "drop", defect_class="something-nobody-has-seen")],
    )
    assert r.lane == "repair" and r.judge_scope == "unknown"


def test_grounding_not_clean_routes_repair():
    r = _lane(_row(), [_rec("jordan.vance@vertex.example", "send", grounding="research=2")])
    assert r.lane == "repair" and "grounding" in r.detail


def test_untraceable_number_holds():
    r = _lane(_row(), [_rec("jordan.vance@vertex.example", "send", grounding="untraceable=97%,3x")])
    assert r.lane == "hold" and r.trigger == "untraceable-number" and "97%" in r.detail


# --------------------------------------------------------- verdict lanes


def test_stale_clause_send_row_goes_generic_and_says_so():
    r = _lane(_row(signal_observed="2025-01-01"), [_rec("jordan.vance@vertex.example", "send")])
    assert r.lane == "generic" and "stale" in r.detail
    fresh = _lane(_row(), [_rec("jordan.vance@vertex.example", "send")])
    assert fresh.lane == "personalised"


def test_judge_source_is_records_not_csv_columns():
    """The pool CSV's judge_* columns are one run stale; only the JSONL routes."""
    r = _lane(_row(judge_verdict="drop", judge_calibrated="true"), None)
    assert r.lane == "generic" and "no judge verdict" in r.detail.lower()
    r = _lane(
        _row(judge_verdict="drop", judge_calibrated="true"),
        [_rec("jordan.vance@vertex.example", "send")],
    )
    assert r.lane == "personalised"


def test_empty_and_re_angle_research_verdicts_go_generic():
    assert _lane(_row(verdict="")).lane == "generic"
    assert _lane(_row(verdict="re-angle")).lane == "generic"
    assert _lane(_row(verdict="drop")).lane == "hold"


def test_duplicate_email_across_cells_is_flagged_ambiguous():
    recs = [
        _rec("jordan.vance@vertex.example", "send", body_hash="h1"),
        _rec("jordan.vance@vertex.example", "re-angle", body_hash="h2"),
    ]
    r = _lane(_row(), recs)
    assert "ambiguous-judge" in r.flags and r.judge_verdict == "re-angle", "worst verdict wins"


def test_router_and_splitter_share_one_freshness_predicate():
    from gtm_core.prospects_consolidate import queues

    assert queues.row_signal_freshness is row_signal_freshness
    clause, fresh = row_signal_freshness(_row(), as_of=AS_OF)
    assert clause and fresh
    assert row_signal_freshness(_row(signal_observed="2025-01-01"), as_of=AS_OF)[1] is False
    assert row_signal_freshness(_row(why_now=""), as_of=AS_OF)[0] == ""


# --------------------------------------------------------- stickiness / contested


def test_contested_verdict_routes_generic_never_personalised():
    previous = {
        "jordan.vance@vertex.example": {
            "lane": "repair",
            "judge_verdict": "re-angle",
            "body_hash": "h1",
        }
    }
    r = _lane(
        _row(), [_rec("jordan.vance@vertex.example", "send", body_hash="h1")], previous=previous
    )
    assert r.lane == "generic" and "contested" in r.flags
    # A changed body is a fresh judgement, not a flip.
    r = _lane(
        _row(), [_rec("jordan.vance@vertex.example", "send", body_hash="h2")], previous=previous
    )
    assert r.lane == "personalised" and "contested" not in r.flags


# --------------------------------------------------------- PS5: stable reason codes


def test_verdict_lane_reason_codes_cover_every_branch():
    """Every branch of `_verdict_lane` (plus the stickiness override) gets its own stable,
    kebab-case code — see `Routed.stable_reason`. `trigger` is empty in every one of these
    cases, so `stable_reason` falls through to `reason_code`."""
    cases = [
        ({}, [_rec("jordan.vance@vertex.example", "send")], "personalised", "researcher-send"),
        (
            {},
            [_rec("jordan.vance@vertex.example", "re-angle", repair_attempt=3)],
            "generic",
            "repair-cap",
        ),
        (
            {},
            [_rec("jordan.vance@vertex.example", "re-angle", repair_attempt=0)],
            "repair",
            "judge-verdict",
        ),
        (
            {},
            [_rec("jordan.vance@vertex.example", "send", grounding="research=2")],
            "repair",
            "grounding",
        ),
        ({}, None, "generic", "no-judge-verdict"),
        (
            {"signal_observed": "2025-01-01"},
            [_rec("jordan.vance@vertex.example", "send")],
            "generic",
            "stale-clause",
        ),
        (
            {"why_now": ""},
            [_rec("jordan.vance@vertex.example", "send")],
            "generic",
            "no-signal-clause",
        ),
        ({"verdict": "re-angle"}, [], "generic", "research-verdict"),
        ({"verdict": ""}, [], "generic", "research-verdict"),
    ]
    for kw, recs, lane, code in cases:
        r = _lane(_row(**kw), recs)
        assert (r.lane, r.reason_code) == (lane, code), (kw, r.lane, r.reason_code)
        assert r.stable_reason == code, "trigger is empty here, so stable_reason == reason_code"


def test_contested_stickiness_sets_the_contested_judge_reason_code():
    previous = {
        "jordan.vance@vertex.example": {
            "lane": "repair",
            "judge_verdict": "re-angle",
            "body_hash": "h1",
        }
    }
    r = _lane(
        _row(), [_rec("jordan.vance@vertex.example", "send", body_hash="h1")], previous=previous
    )
    assert r.lane == "generic" and r.reason_code == "contested-judge"
    assert r.stable_reason == "contested-judge"


def test_stable_reason_is_the_trigger_for_hold_and_excluded_rows():
    """A trigger fired — the second-pass triggers too (PS12's `tier-a-generic`) — so
    `stable_reason` reads the trigger id itself, never a `_verdict_lane` code."""
    r = _lane(_row(verdict="drop"))
    assert r.lane == "hold" and r.trigger == "researcher-drop"
    assert r.stable_reason == "researcher-drop"
    r = _lane(_row(suppression="out-of-market"))
    assert r.lane == "excluded" and r.trigger == "suppressed"
    assert r.stable_reason == "suppressed"
    rows = [_row(tier="A", verdict="")]
    result = lanes.route(rows, [], _ctx())
    assert result.routed[0].trigger == "tier-a-generic"
    assert result.routed[0].stable_reason == "tier-a-generic"


def test_stable_reason_stamps_the_decision_for_a_decided_row():
    """A row a prior decision or policy answered stamps `<choice>:<trigger>`, so it reads as
    visibly distinct from a row that is freshly held on the same trigger."""
    key = ("prior-contact", lanes.account_key(_row()))
    ctx = _ctx(prior_emails={"jordan.vance@vertex.example"})
    generic = {key: {"decision": "generic", "detail": "this address was already emailed"}}
    r = _lane(_row(), None, ctx, decisions=generic)
    assert r.lane == "generic" and r.stable_reason == "generic:prior-contact"
    salvage = {key: {"decision": "salvage", "detail": "this address was already emailed"}}
    r = _lane(_row(), None, ctx, decisions=salvage)
    assert r.lane == "repair" and r.stable_reason == "salvage:prior-contact"


def test_write_state_carries_reason_additively(tmp_path):
    """PS5: `reason` is a NEW field; the existing `trigger` field is unchanged (`lanes/cli.py`
    stickiness reads it) and stays blank for a verdict-lane row exactly as before."""
    result = lanes.route([_row()], [_rec("jordan.vance@vertex.example", "send")], _ctx())
    path = tmp_path / "lanes-state.jsonl"
    dec.write_state(result, path, "2026-09-03")
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["trigger"] == ""
    assert rec["reason"] == "researcher-send"


# --------------------------------------------------------- hold triggers, each with a positive control


def test_competitor_and_partner_hold():
    ctx = _ctx(
        competitors={
            org_token("vertex.example", "Vertex Systems"): CompetitorHit("si-channel", "x")
        }
    )
    assert _lane(_row(), None, ctx).trigger == "competitor-adjacent"
    assert _lane(_row(category_relation="partner")).trigger == "partner"
    assert _lane(_row()).lane != "hold"


def test_engaged_account_holds():
    ctx = _ctx(statuses={"d:vertex.example": "engaged"})
    assert _lane(_row(), None, ctx).trigger == "engaged-account"
    assert _lane(_row(), None, _ctx(statuses={"d:vertex.example": "new"})).lane != "hold"


def test_prior_contact_holds():
    assert (
        _lane(_row(), None, _ctx(prior_emails={"jordan.vance@vertex.example"})).trigger
        == "prior-contact"
    )
    assert _lane(_row(), None, _ctx(prior_emails={"someone.else@vertex.example"})).lane != "hold"


def test_negative_reply_holds():
    assert (
        _lane(_row(), None, _ctx(negative={"jordan.vance@vertex.example"})).trigger
        == "negative-reply"
    )


def test_regulated_domain_holds():
    r = _lane(_row(company_domain="treasury.gov", company="Treasury"))
    assert r.trigger == "regulator"
    assert _lane(_row(category_relation="regulator")).trigger == "regulator"
    ctx = _ctx(
        regulated_industries=("commercial banking",),
        industries={"d:vertex.example": "commercial banking"},
    )
    assert _lane(_row(), None, ctx).trigger == "regulator"


def test_strategic_account_holds_only_when_listed():
    tok = org_token("vertex.example", "Vertex Systems")
    assert _lane(_row(), None, _ctx(strategic={tok})).trigger == "strategic-account"
    assert _lane(_row(), None, _ctx(strategic=set())).lane != "hold"


def test_tier_a_generic_holds():
    rows = [_row(tier="A", verdict="")]
    result = lanes.route(rows, [], _ctx())
    assert result.routed[0].lane == "hold" and result.routed[0].trigger == "tier-a-generic"
    result = lanes.route([_row(tier="B", verdict="")], [], _ctx())
    assert result.routed[0].lane == "generic"


def test_second_contact_per_account_per_wave_holds():
    rows = [
        _row(email="a@vertex.example", score="90"),
        _row(email="b@vertex.example", first="Sam", score="50"),
    ]
    recs = [_rec("a@vertex.example", "send"), _rec("b@vertex.example", "send")]
    result = lanes.route(rows, recs, _ctx())
    by = {r.email: r for r in result.routed}
    assert by["a@vertex.example"].lane == "personalised"
    assert (
        by["b@vertex.example"].lane == "hold"
        and by["b@vertex.example"].trigger == "duplicate-contact"
    )


def test_already_enrolled_is_excluded_not_held():
    r = _lane(
        _row(), None, _ctx(enrolled={"jordan.vance@vertex.example": "enterprise:security:v2"})
    )
    assert r.lane == "excluded" and r.trigger == "already-enrolled"


def test_optional_lists_absent_are_reported_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "content" / "acme" / "prospects" / "sequences").mkdir(parents=True)
    ctx = lanes.load_context("acme", as_of=AS_OF)
    notes = "\n".join(ctx.notes)
    for missing in ("strategic", "lane-policy", "competitors", "history"):
        assert missing in notes, f"a missing {missing} list must be reported"


def test_policy_never_suppresses(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "content" / "acme" / "prospects" / "sequences").mkdir(parents=True)
    k = tmp_path / "profiles" / "acme" / "knowledge"
    k.mkdir(parents=True)
    (k / "lane-policy.toml").write_text(
        '[auto]\ntier-a-generic = "generic"\nprior-contact = "suppress"\n', encoding="utf-8"
    )
    ctx = lanes.load_context("acme", as_of=AS_OF)
    assert ctx.policy_auto == {"tier-a-generic": "generic"}
    assert any("REFUSED" in n and "suppress" in n for n in ctx.notes)
    result = lanes.route([_row(tier="A", verdict="")], [], ctx)
    assert result.routed[0].lane == "generic" and result.routed[0].decided.startswith("policy:")


# --------------------------------------------------------- decisions ledger


def test_decisions_ledger_is_honoured_on_the_next_routing_run():
    key = ("prior-contact", lanes.account_key(_row()))
    ctx = _ctx(prior_emails={"jordan.vance@vertex.example"})
    decided = {key: {"decision": "generic", "detail": "this address was already emailed"}}
    r = _lane(_row(), None, ctx, decisions=decided)
    assert r.lane == "generic" and r.decided == "decided:generic:prior-contact"
    salvaged = {key: {"decision": "salvage", "detail": "this address was already emailed"}}
    assert _lane(_row(), None, ctx, decisions=salvaged).lane == "repair"
    suppressed = {key: {"decision": "suppress", "detail": "this address was already emailed"}}
    assert _lane(_row(), None, ctx, decisions=suppressed).lane == "excluded"


def test_decided_pair_is_reasked_when_evidence_changed():
    key = ("prior-contact", lanes.account_key(_row()))
    ctx = _ctx(prior_emails={"jordan.vance@vertex.example"})
    stale = {key: {"decision": "generic", "detail": "a different reason last time"}}
    assert _lane(_row(), None, ctx, decisions=stale).lane == "hold"


def test_hold_csv_has_no_preselected_decision(tmp_path):
    result = lanes.route([_row(verdict="drop")], [], _ctx())
    path = dec.write_hold_csv(result.lane("hold"), tmp_path / "hold.csv", {})
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert (
        rows[0]["decision"] == ""
        and rows[0]["salvage_kind"] == ""
        and rows[0]["trigger"] == "researcher-drop"
    )


def _entry(**kw) -> dec.DecisionEntry:
    base = {
        "email": "a@x.example",
        "trigger": "prior-contact",
        "account_key": "d:x.example",
        "decision": "",
        "detail": "d",
    }
    base.update(kw)
    return dec.DecisionEntry(**base)


def test_hold_apply_suppress_generic_salvage_blank():
    plan = dec.plan_apply(
        [
            _entry(decision="suppress"),
            _entry(email="b@y.example", account_key="d:y.example", decision="generic"),
            _entry(
                email="c@z.example",
                account_key="d:z.example",
                decision="salvage",
                salvage_kind="different-fact",
            ),
            _entry(email="d@w.example", account_key="d:w.example"),
        ],
        {},
    )
    assert [e.email for e in plan.suppress] == ["a@x.example"]
    assert [e.email for e in plan.generic] == ["b@y.example"]
    assert [e.email for e in plan.salvage] == ["c@z.example"]
    assert [e.email for e in plan.held] == ["d@w.example"] and not plan.refused


def test_unknown_decision_or_chip_is_refused_not_guessed():
    plan = dec.plan_apply(
        [
            _entry(decision="yes"),
            _entry(email="c@z.example", decision="salvage", salvage_kind="make-it-better"),
        ],
        {},
    )
    assert len(plan.refused) == 2 and len(plan.held) == 2
    assert not plan.suppress and not plan.salvage


def test_conflicting_decisions_are_reported_not_resolved():
    prior = {
        ("prior-contact", "d:x.example"): {
            "decision": "generic",
            "stamp": "2026-08-27",
            "detail": "d",
        }
    }
    plan = dec.plan_apply([_entry(decision="suppress")], prior)
    assert plan.conflicts and "generic" in plan.conflicts[0]
    same = dec.plan_apply([_entry(decision="generic")], prior)
    assert same.already and not same.generic, "an identical decision is idempotent"


def test_hold_apply_plan_writes_nothing_and_apply_records(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    evals = tmp_path / "acme" / "prospects" / "evals"
    evals.mkdir(parents=True)
    filled = evals / "hold-2026-09-03.csv"
    with filled.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=dec.HOLD_COLUMNS)
        w.writeheader()
        w.writerow(
            {
                "email": "b@y.example",
                "company": "Y",
                "company_domain": "y.example",
                "trigger": "tier-a-generic",
                "lane_reason": "hold:tier-a-generic — tier A",
                "decision": "generic",
                "note": "fine for them",
            }
        )
    assert lanes.main(["hold-apply", "--profile", "acme", "--decisions", str(filled)]) == 0
    assert not dec.decisions_path("acme").exists(), "plan mode must write nothing"
    assert (
        lanes.main(
            [
                "hold-apply",
                "--profile",
                "acme",
                "--decisions",
                str(filled),
                "--apply",
                "--stamp",
                "2026-09-03",
            ]
        )
        == 0
    )
    ledger = dec.read_decisions(dec.decisions_path("acme"))
    assert ledger[("tier-a-generic", "d:y.example")]["decision"] == "generic"
    feedback = [
        json.loads(line)
        for line in dec.feedback_path("acme").read_text(encoding="utf-8").splitlines()
    ]
    assert feedback and feedback[0]["note"] == "fine for them"
    # Idempotent: applying the same file again records nothing new.
    lanes.main(
        [
            "hold-apply",
            "--profile",
            "acme",
            "--decisions",
            str(filled),
            "--apply",
            "--stamp",
            "2026-09-04",
        ]
    )
    assert len(dec._read_jsonl(dec.decisions_path("acme"))) == 1


def test_filled_hold_csv_decision_is_honoured_on_the_next_real_route(tmp_path, monkeypatch):
    """The actual round trip a human uses: route -> write_hold_csv -> fill the CSV's
    `decision` column -> hold-apply --apply -> route again.

    Regression for a bug fixed 2026-09-03: `write_hold_csv` puts the COMPOSED
    `f"{lane}:{trigger} — {detail}"` string in `lane_reason`; `_entry_from` used to read
    that column back as if it WERE the raw `detail`, so it could never match the raw
    `detail` a later `route()` computes and `generic`/`salvage` decisions silently stayed
    held forever — only `suppress` worked, because its branch skips the detail check. This
    test fills a REAL hold CSV (not a hand-built dict, which the older
    `test_decisions_ledger_is_honoured_on_the_next_routing_run` used and so never caught
    the bug) and asserts the row actually changes lane on the next route.
    """
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    ctx = _ctx(prior_emails={"jordan.vance@vertex.example"})
    row = _row()

    first = lanes.route([row], [], ctx)
    held = first.lane("hold")
    assert len(held) == 1 and held[0].trigger == "prior-contact"

    hold_csv = tmp_path / "acme" / "prospects" / "evals" / "hold-2026-09-03.csv"
    dec.write_hold_csv(held, hold_csv, {})
    rows = list(csv.DictReader(hold_csv.open(encoding="utf-8")))
    rows[0]["decision"] = "generic"
    rows[0]["note"] = "fine, no negative reply on file"
    with hold_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=dec.HOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    assert (
        lanes.main(
            [
                "hold-apply",
                "--profile",
                "acme",
                "--decisions",
                str(hold_csv),
                "--apply",
                "--stamp",
                "2026-09-03",
            ]
        )
        == 0
    )

    decisions = dec.read_decisions(dec.decisions_path("acme"))
    second = lanes.route([row], [], ctx, decisions=decisions)
    assert (
        second.lane("generic")
        and second.lane("generic")[0].decided == "decided:generic:prior-contact"
    )
    assert not second.lane("hold")


def test_suggest_rules_needs_ten_unanimous_and_never_proposes_suppress(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    path = dec.decisions_path("acme")
    path.parent.mkdir(parents=True)
    rows = [
        {"trigger": "tier-a-generic", "account_key": f"d:{i}.example", "decision": "generic"}
        for i in range(10)
    ]
    rows += [
        {"trigger": "prior-contact", "account_key": f"d:p{i}.example", "decision": "suppress"}
        for i in range(12)
    ]
    rows += [
        {"trigger": "partner", "account_key": f"d:q{i}.example", "decision": "generic"}
        for i in range(9)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert lanes.main(["suggest-rules", "--profile", "acme"]) == 0
    out = capsys.readouterr().out
    assert 'tier-a-generic = "generic"' in out
    assert "partner" not in out.split("[auto]")[-1], "nine decisions is below the threshold"
    assert 'prior-contact = "suppress"' not in out and "never proposed" in out
    assert not (tmp_path / "profiles").exists(), (
        "the tool proposes; it never writes the tenant file"
    )


def test_auto_policy_refuses_protective_hold_triggers(tmp_path):
    """PS-R I5: [auto] policy must never automate protective hold triggers."""
    from gtm_core.lanes.context import RouterContext, _load_policy
    from gtm_core.lanes.model import PROTECTIVE_HOLD_TRIGGERS

    policy_file = tmp_path / "lane-policy.toml"
    content = "[auto]\n" + "\n".join(f'{t} = "generic"' for t in PROTECTIVE_HOLD_TRIGGERS)
    policy_file.write_text(content, encoding="utf-8")

    ctx = RouterContext(profile="acme", as_of=AS_OF)
    _load_policy(ctx, policy_file)
    for t in PROTECTIVE_HOLD_TRIGGERS:
        assert t not in ctx.policy_auto
        assert any(t in note and "REFUSED" in note for note in ctx.notes)


def test_suggest_rules_never_proposes_protective_triggers(tmp_path, monkeypatch, capsys):
    """PS-R I5: suggest-rules must never propose protective hold triggers."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    path = dec.decisions_path("acme")
    path.parent.mkdir(parents=True)
    rows = [
        {"trigger": "engaged-account", "account_key": f"d:ea{i}.example", "decision": "generic"}
        for i in range(15)
    ]
    rows += [
        {"trigger": "prior-contact", "account_key": f"d:pc{i}.example", "decision": "salvage"}
        for i in range(15)
    ]
    rows += [
        {"trigger": "untraceable-number", "account_key": f"d:un{i}.example", "decision": "generic"}
        for i in range(10)
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert lanes.main(["suggest-rules", "--profile", "acme"]) == 0
    out = capsys.readouterr().out
    assert 'untraceable-number = "generic"' in out
    assert "engaged-account" not in out.split("[auto]")[-1]
    assert "prior-contact" not in out.split("[auto]")[-1]
    assert "protective hold triggers are never proposed" in out


def test_duplicate_contact_pass_does_not_overwrite_decided_protective_row(tmp_path):
    """PS-R I6: When a contact with a protective trigger (prior-contact) has been
    decided (salvaged to repair), the second pass (duplicate-contact) must not
    overwrite its trigger or downgrade its decision."""
    ctx = _ctx()
    ctx.prior_emails.add("prior@vertex.example")

    row1 = _row(email="prior@vertex.example", company="Vertex", company_domain="vertex.example")
    row2 = _row(email="new@vertex.example", company="Vertex", company_domain="vertex.example")

    decisions = {
        ("prior-contact", "d:vertex.example"): {
            "decision": "salvage",
            "detail": "this address was already emailed",
            "trigger": "prior-contact",
        }
    }
    result = lanes.route([row1, row2], [], ctx, decisions=decisions)

    r1 = next(r for r in result.routed if r.email == "prior@vertex.example")
    r2 = next(r for r in result.routed if r.email == "new@vertex.example")

    # r1 must retain its protective trigger and repair lane
    assert r1.trigger == "prior-contact"
    assert r1.lane == "repair"
    # r2 is the duplicate contact and is held on duplicate-contact
    assert r2.trigger == "duplicate-contact"
    assert r2.lane == "hold"


# --------------------------------------------------------- CLI route


def _pool(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "ready-to-load.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return p


def test_records_older_than_max_age_are_refused(tmp_path, monkeypatch, capsys):
    from gtm_core.adjudication import write_records

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "content" / "acme" / "prospects" / "sequences").mkdir(parents=True)
    pool = _pool(tmp_path, [_row()])
    old = tmp_path / "sweep-normal-2026-01-01.jsonl"
    write_records([_rec("jordan.vance@vertex.example", "send")], old)
    base = [
        "route",
        "--profile",
        "acme",
        "--csv",
        str(pool),
        "--records",
        str(old),
        "--as-of",
        "2026-09-03",
        "--dry-run",
    ]
    assert lanes.main(base) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert lanes.main([*base, "--allow-stale-records"]) == 0
    assert "personalised 1" in capsys.readouterr().out


def test_route_writes_lanes_hold_queue_and_state(tmp_path, monkeypatch, capsys):
    from gtm_core.adjudication import write_records

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    pool = _pool(
        tmp_path,
        [
            _row(),
            _row(
                email="x@drop.example",
                company="Drop Co",
                company_domain="drop.example",
                verdict="drop",
            ),
        ],
    )
    recs = tmp_path / "sweep-normal-2026-09-01.jsonl"
    write_records([_rec("jordan.vance@vertex.example", "send")], recs)
    rc = lanes.main(
        [
            "route",
            "--profile",
            "acme",
            "--csv",
            str(pool),
            "--records",
            str(recs),
            "--as-of",
            "2026-09-03",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "personalised 1" in out and "hold 1" in out
    assert (seq / "ready-to-load-personalised-2026-09-03.csv").is_file()
    hold = dec.hold_path("acme", "2026-09-03")
    assert hold.is_file() and "researcher-drop" in hold.read_text(encoding="utf-8")
    state = dec.read_state(dec.state_path("acme"))
    assert state["jordan.vance@vertex.example"]["lane"] == "personalised"
    # PS17: the hold-lane CSV is a pool artifact, hidden under `.pool/lanes/` — never
    # written into the visible `sequences/` folder alongside personalised/generic.
    assert (seq / ".pool" / "lanes" / "ready-to-load-hold-2026-09-03.csv").is_file()
    assert not (seq / "ready-to-load-hold-2026-09-03.csv").is_file()


# --------------------------------------------------------- PS17: pool-vs-visible file placement


def test_write_lanes_hides_repair_hold_excluded_in_the_pool(tmp_path):
    rows = [
        _row(email="a@x.example", company="X", company_domain="x.example"),
        _row(email="b@y.example", company="Y", company_domain="y.example", verdict="re-angle"),
        _row(email="c@z.example", company="Z", company_domain="z.example", verdict="drop"),
        _row(
            email="d@w.example",
            company="W",
            company_domain="w.example",
            suppression="out-of-market",
        ),
    ]
    recs = [_rec("a@x.example", "send")]
    result = lanes.route(rows, recs, _ctx())
    paths = lanes.write_lanes(result, tmp_path, "2026-09-03")
    assert paths["personalised"].parent == tmp_path
    assert paths["generic"].parent == tmp_path
    for lane in ("repair", "hold", "excluded"):
        assert paths[lane].parent == tmp_path / ".pool" / "lanes"
        assert not (tmp_path / paths[lane].name).exists(), f"{lane} must not also sit visibly"


def test_write_lanes_supersedes_the_prior_stamp_never_deletes(tmp_path):
    rows = [_row(email="a@x.example", company="X", company_domain="x.example")]
    result = lanes.route(rows, [_rec("a@x.example", "send")], _ctx())
    first = lanes.write_lanes(result, tmp_path, "2026-09-03")
    assert first["personalised"].is_file()
    second = lanes.write_lanes(result, tmp_path, "2026-09-04")
    assert second["personalised"].is_file()
    # The 09-03 stamp is gone from sequences/ — moved, not deleted — and lands in
    # .pool/.superseded/ instead.
    assert not first["personalised"].exists()
    superseded = tmp_path / ".pool" / ".superseded" / "ready-to-load-personalised-2026-09-03.csv"
    assert superseded.is_file()


def test_write_lanes_supersedes_a_pre_ps17_visible_hold_stamp(tmp_path):
    """A hold/repair/excluded stamp written before PS17 sits VISIBLY in `seq_dir` (the old
    layout). The next route must still find and archive it, even though the new stamp for
    that lane now lands in `.pool/lanes/`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    legacy = tmp_path / "ready-to-load-hold-2026-09-01.csv"
    legacy.write_text("email\n", encoding="utf-8")
    rows = [_row(verdict="drop")]
    result = lanes.route(rows, [], _ctx())
    lanes.write_lanes(result, tmp_path, "2026-09-03")
    assert not legacy.exists()
    assert (tmp_path / ".pool" / ".superseded" / "ready-to-load-hold-2026-09-01.csv").is_file()


def test_write_lanes_pool_path_matches_prospects_consolidate_pool_dir(tmp_path):
    """`write_lanes` used to hardcode `.pool`/`.pool/lanes`/`.pool/.superseded` as three
    separate literal path segments, rather than building on the SAME `.pool` convention
    `prospects_consolidate.paths._pool_dir` uses for `master-list.csv`/
    `needs-verification.csv` (and now `queues.split_by_signal`'s two lists too). Pin that
    the two resolve to the literal same directory today, so a future divergence between
    the two conventions — one keyed by profile, one keyed by an already-resolved
    `seq_dir` — is caught immediately instead of silently."""
    from gtm_core.prospects_consolidate.paths import _pool_dir, _sequences_dir

    content_root = tmp_path / "content"
    seq_dir = _sequences_dir("acme", content_root)
    rows = [_row(verdict="drop")]
    result = lanes.route(rows, [], _ctx())
    paths = lanes.write_lanes(result, seq_dir, "2026-09-03")

    router_pool_dir = paths["hold"].parent.parent  # .../.pool/lanes -> .../.pool
    assert router_pool_dir == _pool_dir("acme", content_root) == seq_dir / ".pool"


# --------------------------------------------------------- PS2: ready-to-load.csv tracks the last route


def test_route_restamps_ready_to_load_csv_immediately(tmp_path, monkeypatch):
    """`lanes route` must not leave `ready-to-load.csv` stale until the next `consolidate`
    sweep notices the new state file — on live data this was seen 5 days stale."""
    from gtm_core.adjudication import write_records
    from gtm_core.prospects_consolidate.columns import MASTER_COLS
    from gtm_core.prospects_consolidate.io import _atomic_write_csv, _load_master
    from gtm_core.prospects_consolidate.paths import ready_to_load_path

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)

    # Simulate a prior `consolidate` sweep that already wrote ready-to-load.csv with a
    # now-stale lane (as if from an earlier route).
    stale_row = dict.fromkeys(MASTER_COLS, "")
    stale_row.update(
        email="jordan.vance@vertex.example",
        first="Jordan",
        last="Vance",
        company="Vertex Systems",
        company_domain="vertex.example",
        lane="generic",
        lane_reason="stale from a prior run",
    )
    _atomic_write_csv(ready_to_load_path("acme", tmp_path / "content"), [stale_row])

    pool = _pool(tmp_path, [_row()])
    recs = tmp_path / "sweep-normal-2026-09-01.jsonl"
    write_records([_rec("jordan.vance@vertex.example", "send")], recs)
    rc = lanes.main(
        [
            "route",
            "--profile",
            "acme",
            "--csv",
            str(pool),
            "--records",
            str(recs),
            "--as-of",
            "2026-09-03",
        ]
    )
    assert rc == 0

    rows = _load_master(ready_to_load_path("acme", tmp_path / "content"))
    row = next(r for r in rows if r["email"] == "jordan.vance@vertex.example")
    assert row["lane"] == "personalised"
    assert row["lane_reason"] == "researcher-send"


def test_ready_to_load_lane_totals_match_lanes_state_after_route_and_diverge_when_hand_edited(
    tmp_path, monkeypatch
):
    """Contract: after a route, `ready-to-load.csv`'s lane totals (a `Counter` over the
    `lane` column) exactly match `lanes-state.jsonl`'s. A positive control alone can't prove
    the check discriminates (§R18) — this also hand-edits the CSV afterward and asserts the
    SAME comparison then fails."""
    from gtm_core.adjudication import write_records
    from gtm_core.prospects_consolidate.columns import MASTER_COLS
    from gtm_core.prospects_consolidate.io import _atomic_write_csv, _load_master
    from gtm_core.prospects_consolidate.paths import ready_to_load_path

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)

    def _seed(email, company, domain):
        row = dict.fromkeys(MASTER_COLS, "")
        row.update(email=email, first="Dana", last="Doe", company=company, company_domain=domain)
        return row

    ready_path = ready_to_load_path("acme", tmp_path / "content")
    _atomic_write_csv(
        ready_path,
        [_seed("a@x.example", "X", "x.example"), _seed("b@y.example", "Y", "y.example")],
    )

    pool = _pool(
        tmp_path,
        [
            _row(email="a@x.example", company="X", company_domain="x.example"),
            _row(
                email="b@y.example",
                company="Y",
                company_domain="y.example",
                verdict="drop",
            ),
        ],
    )
    recs = tmp_path / "sweep-normal-2026-09-01.jsonl"
    write_records([_rec("a@x.example", "send")], recs)
    assert (
        lanes.main(
            [
                "route",
                "--profile",
                "acme",
                "--csv",
                str(pool),
                "--records",
                str(recs),
                "--as-of",
                "2026-09-03",
            ]
        )
        == 0
    )

    from gtm_core.enrollment_gate import _refuse_lane_state_mismatch

    ready_rows = _load_master(ready_path)
    ready_counts = Counter(r["lane"] for r in ready_rows if r["lane"])
    state = dec.read_state(dec.state_path("acme"))
    # Only the emails ready-to-load.csv itself carries — `state` covers every routed row,
    # which is a superset the moment a hold/excluded row also came through this pool.
    ready_emails = {r["email"] for r in ready_rows}
    state_counts = Counter(v["lane"] for e, v in state.items() if e in ready_emails)
    assert ready_counts == state_counts
    assert (
        _refuse_lane_state_mismatch(ready_rows, "acme", content_root=tmp_path / "content") is None
    )

    # Negative control: hand-diverge one row's lane, then re-check the SAME comparison
    # AND verify that the lane-state agreement check refuses the tampered list (§R18).
    ready_rows[0]["lane"] = "generic"  # diverged from "personalised" in state
    _atomic_write_csv(ready_path, ready_rows)
    tampered_rows = _load_master(ready_path)
    tampered_counts = Counter(r["lane"] for r in tampered_rows if r["lane"])
    assert tampered_counts != state_counts
    refusal = _refuse_lane_state_mismatch(tampered_rows, "acme", content_root=tmp_path / "content")
    assert refusal is not None
    assert "REFUSED" in refusal
    assert "lanes-state.jsonl" in refusal


def test_the_lanes_verb_is_registered():
    from gtm_core import prospects

    assert prospects.VERBS["lanes"] == "gtm_core.lanes"
    with pytest.raises(SystemExit):
        lanes.main(["route", "--help"])


# --------------------------------------------------------- coverage alarms


def test_draft_cells_do_not_count_as_enrolled(tmp_path, monkeypatch):
    """A DRAFT-* entry was never staged; its rows are enrolled nowhere."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    for name in ("live.csv", "draft.csv"):
        (seq / name).write_text(
            f"email,title,segment\n{name.split('.')[0]}@x.example,CISO,enterprise\n",
            encoding="utf-8",
        )
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "Abc123"\ncsv = "live.csv"\nspec = "spec-a-2026-09-01.md"\n'
        '[[sequence]]\nid = "DRAFT-recut"\ncsv = "draft.csv"\nspec = "spec-b-2026-09-01.md"\n',
        encoding="utf-8",
    )
    ctx = lanes.load_context("acme", as_of=AS_OF)
    assert ctx.enrolled == {"live@x.example": "Abc123"}
    assert any("DRAFT" in n for n in ctx.notes)


def test_summary_reports_judge_coverage_and_alarms_when_unjudged():
    rows = [_row(), _row(email="b@y.example", company="Y", company_domain="y.example")]
    result = lanes.route(rows, [], _ctx())
    text = lanes.summary(result)
    assert "judge records on file for 0/2" in text
    assert "cover NONE" in text, "an unjudged list must not read as 'nothing qualifies'"
    result = lanes.route(rows, [_rec("jordan.vance@vertex.example", "send")], _ctx())
    assert result.judged == 1 and result.unjudged_sendable == 1
    assert "fell to generic" in lanes.summary(result)


# --------------------------------------------------------- the hold sheet (structure)


def _hold_rows():
    return [
        {
            "email": "a@x.example",
            "first": "Ada",
            "last": "Lin",
            "title": "CISO",
            "company": "X",
            "company_domain": "x.example",
            "tier": "A",
            "trigger": "tier-a-generic",
            "lane_reason": "hold:tier-a-generic — tier A",
            "evidence": "",
            "prior_decision": "generic (2026-08-27)",
            "judge_defect_class": "",
        },
        {
            "email": "b@y.example",
            "first": "Bo",
            "last": "Ray",
            "title": "CISO",
            "company": "Y",
            "company_domain": "y.example",
            "tier": "A",
            "trigger": "tier-a-generic",
            "lane_reason": "hold:tier-a-generic — tier A",
            "evidence": "",
            "prior_decision": "",
            "judge_defect_class": "",
        },
        {
            "email": "c@z.example",
            "first": "Cy",
            "last": "Om",
            "title": "CTO",
            "company": "Z",
            "company_domain": "z.example",
            "tier": "B",
            "trigger": "competitor-adjacent",
            "lane_reason": "hold:competitor-adjacent — Z (adjacent)",
            "evidence": "Z (adjacent)",
            "prior_decision": "",
            "judge_defect_class": "",
        },
    ]


def test_hold_groups_key_on_question_and_seat_and_are_risk_ordered():
    """PS12: grouping moved from (trigger, seat) to (question, seat) — several triggers with
    near-identical meanings collapse onto one question (`competitor-adjacent` is one of four
    triggers mapped to `account-off-limits`). This fixture's `competitor-adjacent` row is the
    only row on its question, so the group count is unchanged; the risk order is unchanged
    too, because a question's sort position is its earliest (riskiest) member trigger's."""
    groups, rows = lanes.build_sheet_payload(_hold_rows())
    assert [g["question"] for g in groups] == [
        "account-off-limits",
        "tier-a-would-get-generic",
    ], "risk order"
    tier = next(g for g in groups if g["question"] == "tier-a-would-get-generic")
    assert tier["count"] == 2 and sum(1 for r in rows if r["group"] == tier["key"]) == 2
    assert tier["meaning"]["suppress"] and tier["meaning"]["generic"] and tier["meaning"]["salvage"]
    assert all(g["title"] for g in groups)
    # Each row still carries its OWN raw trigger — presentation-only grouping, per PS12.
    off_limits_row = next(r for r in rows if r["email"] == "c@z.example")
    assert off_limits_row["trigger"] == "competitor-adjacent"


def test_every_hold_trigger_maps_to_a_documented_question():
    assert "send" in lanes.DECISIONS
    for trigger in lanes.HOLD_ORDER:
        question = lanes.HOLD_QUESTION[trigger]
        if question == "champion-missing":
            assert lanes.QUESTION_COPY[question][1].keys() == {"send", "salvage", "suppress"}
        else:
            assert lanes.QUESTION_COPY[question][1].keys() == {"suppress", "generic", "salvage"}


def test_r7_6_send_decision_releases_hold_and_takes_earned_lane(tmp_path, monkeypatch):
    """R7.6: `send` decision releases hold and row takes its earned lane; suppress drops contact only."""
    from dataclasses import replace
    from unittest.mock import patch

    from gtm_core.lanes import decisions as dec
    from gtm_core.lanes.model import ACCOUNT_SCOPED_SUPPRESS
    from gtm_core.role_vocabulary import DEFAULT_VOCABULARY

    assert "champion-missing" not in ACCOUNT_SCOPED_SUPPRESS

    row = _row(
        title="Software Engineer",
        segment="enterprise",
        company="Acme Corp",
        domain="acme.example",
        email="eng@acme.example",
    )
    vocab = replace(DEFAULT_VOCABULARY, wedge_seats={"enterprise": ("security",)})
    ctx = _ctx()
    key = ("champion-missing", lanes.account_key(row))
    send_dec = {
        key: {"decision": "send", "detail": "account has no champion in wedge seats ('security',)"}
    }
    with patch("gtm_core.role_vocabulary.load", return_value=vocab):
        result = lanes.route([row], [], ctx, decisions=send_dec)
        r = result.routed[0]
        assert r.lane in ("generic", "personalised")
        assert r.decided == "decided:send:champion-missing"

    # Test hold-apply accepts send
    plan = dec.plan_apply(
        [_entry(email="eng@acme.example", trigger="champion-missing", decision="send")], {}
    )
    assert len(plan.send) == 1
    assert not plan.refused


def test_hold_sheet_groups_by_the_tenant_seat_when_a_profile_is_passed(tmp_path, monkeypatch):
    """PH13: the hold sheet's own grouping resolves seat via ``gtm_core.cells.seat_of`` —
    which must be handed the active profile so it reads the tenant's
    ``role-vocabulary.toml`` instead of the built-in default. "Kiln Warden" is a title only
    a tenant vocabulary can place; without ``profile`` it stays "unresolved"."""
    from gtm_core.role_vocabulary import clear_cache

    profile = "acme"
    profiles_root = tmp_path / "profiles"
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (profiles_root / profile / "knowledge" / "role-vocabulary.toml").write_text(
        """\
default_persona = "kiln-warden"
segments = ["enterprise", "unspecified"]
security_only = []
non_buyer_cues = []
ceo_title_cues = []

[[persona]]
name = "kiln-warden"
cues = ["kiln warden"]

[[seat]]
name = "operations"
personas = ["kiln-warden"]
stakes = ["throughput"]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))
    clear_cache()

    rows = [
        {
            "email": "kw@x.example",
            "title": "Kiln Warden",
            "company": "X",
            "trigger": "tier-a-generic",
        }
    ]

    groups_default, _ = lanes.build_sheet_payload(rows)
    assert groups_default[0]["seat"] == "unresolved", (
        "control: the default vocabulary must not know this title"
    )

    groups_tenant, _ = lanes.build_sheet_payload(rows, profile=profile)
    assert groups_tenant[0]["seat"] == "operations", (
        f"tenant seat 'operations' not resolved — groups={groups_tenant}"
    )


def test_triggers_sharing_a_question_group_together_within_one_seat():
    """The whole point of PS12: two DIFFERENT triggers that ask the same question and share a
    seat land in one group, not two."""
    rows = [
        {
            "email": "a@x.example",
            "first": "Ada",
            "last": "Lin",
            "title": "CISO",
            "company": "X",
            "company_domain": "x.example",
            "tier": "B",
            "trigger": "competitor-adjacent",
            "lane_reason": "hold:competitor-adjacent — x",
            "evidence": "",
            "prior_decision": "",
            "judge_defect_class": "",
        },
        {
            "email": "b@y.example",
            "first": "Bo",
            "last": "Ray",
            "title": "CISO",
            "company": "Y",
            "company_domain": "y.example",
            "tier": "B",
            "trigger": "partner",
            "lane_reason": "hold:partner — y",
            "evidence": "",
            "prior_decision": "",
            "judge_defect_class": "",
        },
    ]
    groups, out_rows = lanes.build_sheet_payload(rows)
    assert len(groups) == 1 and groups[0]["question"] == "account-off-limits"
    assert {r["email"] for r in out_rows} == {"a@x.example", "b@y.example"}
    assert {r["trigger"] for r in out_rows} == {"competitor-adjacent", "partner"}


def test_shared_body_rendered_once_per_group_and_rows_carry_only_deltas():
    groups, rows = lanes.build_sheet_payload(
        _hold_rows(), bodies={"*": "Hi {{First Name}}, shared body"}
    )
    assert all(g["body"] == "Hi {{First Name}}, shared body" for g in groups)
    assert "body" not in rows[0]
    html = lanes.render_sheet(_hold_rows(), stamp="2026-09-03", bodies={"*": "shared body"})
    assert html.count("shared body") == len(groups), "injected once per GROUP, never per row"


def test_hold_sheet_has_no_preselected_decision_and_prior_is_text():
    html = lanes.render_sheet(_hold_rows(), stamp="2026-09-03")
    # The template renders radios from state, which starts empty; no server-side "checked".
    assert "checked" not in html.split("<script>")[0]
    assert "checked: s.decision === d" in html, (
        "the only checked state comes from the operator's own saved choice"
    )
    assert "generic (2026-08-27)" in html
    assert '"prior": "generic (2026-08-27)"' in html
    assert "No radio is ever pre-checked" in html


def test_hold_sheet_exports_every_row_with_blank_decisions_kept():
    html = lanes.render_sheet(_hold_rows(), stamp="2026-09-03")
    assert 'decision: s.decision || ""' in html
    assert "ROWS.map(r =>" in html, "export iterates every row, not only decided ones"
    assert "hold-decisions-2026-09-03.jsonl" in html


def test_group_decide_writes_every_row_and_per_reason_meanings_are_present():
    html = lanes.render_sheet(_hold_rows(), stamp="2026-09-03")
    assert '"data-group-decide": d' in html and '["suppress", "Suppress all"]' in html
    assert "for (const r of rows) { const s = st(r.row_id); s.decision = d;" in html
    for trigger in lanes.HOLD_ORDER:
        if trigger == "champion-missing":
            assert lanes.HOLD_COPY[trigger][1].keys() == {"send", "salvage", "suppress"}, trigger
        else:
            assert lanes.HOLD_COPY[trigger][1].keys() == {"suppress", "generic", "salvage"}, trigger


def test_salvage_chips_are_the_canonical_kinds_and_keys_are_inert_in_fields():
    html = lanes.render_sheet(_hold_rows(), stamp="2026-09-03")
    assert json.dumps(list(lanes.SALVAGE_KINDS)) in html
    assert 't.tagName === "INPUT"' in html and "return;" in html.split("keydown")[1][:400]


def test_script_breakout_is_escaped():
    rows = _hold_rows()
    rows[0]["evidence"] = "</script><script>alert(1)</script>"
    html = lanes.render_sheet(rows, stamp="2026-09-03")
    assert "</script><script>alert" not in html and "<\\/script>" in html


def test_route_writes_the_hold_sheet_and_hold_sheet_rebuilds_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "content" / "acme" / "prospects" / "sequences").mkdir(parents=True)
    pool = _pool(tmp_path, [_row(verdict="drop")])
    recs = tmp_path / "sweep-2026-09-01.jsonl"
    recs.write_text("", encoding="utf-8")
    assert (
        lanes.main(
            [
                "route",
                "--profile",
                "acme",
                "--csv",
                str(pool),
                "--records",
                str(recs),
                "--as-of",
                "2026-09-03",
            ]
        )
        == 0
    )
    sheet = dec.hold_path("acme", "2026-09-03").with_suffix(".html")
    assert sheet.is_file() and "researcher-drop" in sheet.read_text(encoding="utf-8")
    sheet.unlink()
    assert (
        lanes.main(
            ["hold-sheet", "--profile", "acme", "--hold", str(dec.hold_path("acme", "2026-09-03"))]
        )
        == 0
    )
    assert sheet.is_file()


def test_c1_reheld_row_clears_decided_stamp_and_status_of_succeeds():
    """C1: _hold_or_decide must clear an earlier decided stamp when a row lands in hold."""
    from gtm_core import prospect_status as ps

    ctx = _ctx(policy_auto={"tier-a-generic": "salvage"})
    rows = [
        _row(
            email="alex@brightpath.example",
            company="Wavelet Corp",
            company_domain="waveletcorp.example",
            tier="A",
            score="90",
            verdict="re-angle",
        ),
        _row(
            email="avery@brightpath.example",
            company="Wavelet Corp",
            company_domain="waveletcorp.example",
            tier="A",
            score="85",
            verdict="re-angle",
        ),
    ]
    result = lanes.route(rows, [], ctx)
    by_email = {r.email: r for r in result.routed}
    r1 = by_email["alex@brightpath.example"]
    r2 = by_email["avery@brightpath.example"]
    assert r1.lane == "repair"
    assert r2.lane == "hold"
    assert r2.trigger == "duplicate-contact"
    assert r2.decided == ""
    assert r2.stable_reason == "duplicate-contact"
    # status_of must not raise UnmappedStatus
    assert ps.status_of(r2.lane, r2.stable_reason) == "waiting_on_you"


def test_c1_contract_route_write_state_through_cli_and_page_model(tmp_path, monkeypatch, capsys):
    """End-to-end contract: real route + write_state with policy auto-decisions and re-held rows
    passes through both the CLI and the page model without UnmappedStatus.
    """
    from gtm_core import prospect_status_cli
    from gtm_core.email_campaign_dashboard import model as dash_model

    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))

    prospects_dir = content_root / "acme" / "prospects"
    seq_dir = prospects_dir / "sequences"
    evals_dir = prospects_dir / "evals"
    seq_dir.mkdir(parents=True)
    evals_dir.mkdir(parents=True)

    # Initialize latest.json
    (prospects_dir / "latest.json").write_text(
        json.dumps({"items": [{"company": "Wavelet Corp", "account_id": "a:123"}]}),
        encoding="utf-8",
    )

    ctx = _ctx(profile="acme", policy_auto={"tier-a-generic": "salvage"})
    rows = [
        _row(
            email="alex@brightpath.example",
            company="Wavelet Corp",
            company_domain="waveletcorp.example",
            tier="A",
            score="90",
            verdict="re-angle",
        ),
        _row(
            email="avery@brightpath.example",
            company="Wavelet Corp",
            company_domain="waveletcorp.example",
            tier="A",
            score="85",
            verdict="re-angle",
        ),
    ]

    result = lanes.route(rows, [], ctx)
    lanes.write_state(result, evals_dir / "lanes-state.jsonl", "2026-09-11")

    # CLI must exit 0 and render cleanly
    assert prospect_status_cli.main(["--profile", "acme"]) == 0
    out = capsys.readouterr().out
    assert "Waiting on you" in out
    assert "Being reworked" in out

    # Dashboard model must parse with 0 unmapped
    status_model = dash_model.prospect_status_model("acme")
    assert status_model["unmapped"] == 0
    assert status_model["total"] == 2
    assert status_model["counts"]["waiting_on_you"] == 1
    assert status_model["counts"]["being_fixed"] == 1


def test_c3_archiving_preserves_registered_enrolled_lists_and_exact_stamp_shape(tmp_path):
    """C3: write_lanes must never archive a file registered in cells.toml,
    must only match the exact ready-to-load-<lane>-YYYY-MM-DD.csv stamp shape,
    and _load_enrolled must emit a note when a registered list is missing on disk.
    """
    from gtm_core.lanes import context as ctx_module
    from gtm_core.lanes import router as router_module

    content_root = tmp_path / "content"
    seq_dir = content_root / "acme" / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)

    # 1. Create a registered enrolled list with a date stamp
    reg_csv = seq_dir / "ready-to-load-personalised-2026-09-09.csv"
    reg_csv.write_text(
        "email,first,company\nalex@brightpath.example,Alex,Wavelet Corp\n", encoding="utf-8"
    )

    # 2. Create an unregistered older stamped file
    unreg_csv = seq_dir / "ready-to-load-personalised-2026-09-08.csv"
    unreg_csv.write_text(
        "email,first,company\nold@brightpath.example,Old,Wavelet Corp\n", encoding="utf-8"
    )

    # 3. Create a custom-suffix file that does not match YYYY-MM-DD
    custom_csv = seq_dir / "ready-to-load-personalised-special.csv"
    custom_csv.write_text(
        "email,first,company\nspecial@brightpath.example,Special,Wavelet Corp\n", encoding="utf-8"
    )

    # Write cells.toml registering reg_csv and a missing file
    cells_toml = seq_dir / "cells.toml"
    cells_toml.write_text(
        "[[sequence]]\n"
        'id = "SEQ-001"\n'
        'lane = "personalised"\n'
        'csv = "ready-to-load-personalised-2026-09-09.csv"\n'
        'spec = "spec-run.md"\n\n'
        "[[sequence]]\n"
        'id = "SEQ-MISSING"\n'
        'lane = "generic"\n'
        'csv = "missing-list-20260909.csv"\n'
        'spec = "spec-gen.md"\n',
        encoding="utf-8",
    )

    result = router_module.RoutingResult()
    router_module.write_lanes(result, seq_dir, "2026-09-10")

    # Unregistered stamped file MUST be archived to .pool/.superseded/
    superseded = seq_dir / ".pool" / ".superseded"
    assert not unreg_csv.is_file(), "Unregistered stamped file should have been superseded"
    assert (superseded / "ready-to-load-personalised-2026-09-08.csv").is_file()

    # Registered file MUST NOT be moved
    assert reg_csv.is_file(), "File registered in cells.toml must NOT be moved by archiving"

    # Custom-named file MUST NOT be moved (does not match YYYY-MM-DD)
    assert custom_csv.is_file(), "Non-YYYY-MM-DD file must NOT be moved by archiving"

    # Test _load_enrolled behavior
    ctx = ctx_module.RouterContext(profile="acme", as_of=AS_OF)
    ctx_module._load_enrolled(ctx, "acme", content_root, seq_dir)
    # The registered enrolled email is preserved in ctx.enrolled
    assert "alex@brightpath.example" in ctx.enrolled
    # Missing registered list produces a note
    assert any("missing-list-20260909.csv" in note for note in ctx.notes)


def test_a_list_whose_sequence_history_records_as_deleted_is_not_enrolled(tmp_path, monkeypatch):
    """`cells.toml` keeps the row (it is the reply-attribution join); history says the
    sequence is gone. Both deletion shapes: `sequence_deleted` (one id) and
    `sequence_cleanup` (a list) — the latter is how the four 2026-08-18 seat-split sequences
    were reconciled on 2026-08-31, and on 2026-09-24 those four were still excluding 85 of
    96 "already-enrolled" pooled rows."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    for name in ("live", "gone", "swept"):
        (seq / f"{name}.csv").write_text(
            f"email,title,segment\n{name}@x.example,CISO,enterprise\n", encoding="utf-8"
        )
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "Live01"\ncsv = "live.csv"\nspec = "spec-a-2026-09-01.md"\n'
        '[[sequence]]\nid = "Gone01"\ncsv = "gone.csv"\nspec = "spec-b-2026-09-01.md"\n'
        '[[sequence]]\nid = "Swept1"\ncsv = "swept.csv"\nspec = "spec-c-2026-09-01.md"\n',
        encoding="utf-8",
    )
    history = tmp_path / "content" / "acme" / "history.jsonl"
    history.write_text(
        json.dumps({"event": "sequence_deleted", "sequence_id": "Gone01"})
        + "\n"
        + json.dumps({"event": "sequence_cleanup", "sequences_deleted": [{"id": "Swept1"}]})
        + "\n",
        encoding="utf-8",
    )
    ctx = lanes.load_context("acme", as_of=AS_OF)
    assert ctx.enrolled == {"live@x.example": "Live01"}
    assert any("deleted from the provider" in n for n in ctx.notes)

    # Negative control: with nothing on record, all three registered lists count.
    history.write_text("", encoding="utf-8")
    ctx = lanes.load_context("acme", as_of=AS_OF)
    assert set(ctx.enrolled) == {"live@x.example", "gone@x.example", "swept@x.example"}
