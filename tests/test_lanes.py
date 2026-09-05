"""The lane router — every pooled row ends in exactly one lane, never stranded.

Fictional fixtures only (§R9). Each refusal ships with a positive control.
"""

from __future__ import annotations

import csv
import datetime
import json
import random
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
    result = lanes.route(rows, recs, _ctx())
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
    prior = {("prior-contact", "d:x.example"): {"decision": "generic", "stamp": "2026-08-27"}}
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


def test_hold_groups_key_on_reason_and_seat_and_are_risk_ordered():
    groups, rows = lanes.build_sheet_payload(_hold_rows())
    assert [g["trigger"] for g in groups] == ["competitor-adjacent", "tier-a-generic"], "risk order"
    tier = next(g for g in groups if g["trigger"] == "tier-a-generic")
    assert tier["count"] == 2 and sum(1 for r in rows if r["group"] == tier["key"]) == 2
    assert tier["meaning"]["suppress"] and tier["meaning"]["generic"] and tier["meaning"]["salvage"]
    assert all(g["title"] for g in groups)


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
