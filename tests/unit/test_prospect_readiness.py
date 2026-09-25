"""PS15 — how many can go out, and if none, why (`gtm_core.prospect_readiness`).

The status block used to re-run the audit itself and keep only a number (``_checked_count``).
A batch that failed its audit contributed zero, so a refused batch and an unchecked list both
read "0", and the reasons the audit had just computed were thrown away. The answer now lives in
the check report, carries its reasons, and every row on the list has exactly one fate.

The first eight tests are the retired ``_checked_count`` tests, ported: each property they
guarded (laned audit, all-or-nothing per batch, suppressed excluded, absent is not zero,
unreadable refuses) now holds on the report. Every fixture writes a sorted list
(``lanes-state.jsonl``), because the real gate refuses any row that is not in it — a check the
retired count skipped, which let it say a row could go when the gate would refuse it.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from gtm_core import preflight_report as pr
from gtm_core import prospect_lede as ld
from gtm_core import prospect_readiness as rd
from tests.test_preflight_report import (
    _AS_OF,
    _FORBIDDEN_IMPORTS,
    _HEADER,
    REPO,
    _dossier,
    _imported_names,
    _row,
)

PROFILE = "acme"
_HEADER_LANED = [*_HEADER, "lane", "suppression"]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path))
    # The fixtures' research is dated for `_AS_OF`; an answer is current only on its own day.
    monkeypatch.setattr(rd, "_today", lambda: _AS_OF)
    return tmp_path


def _stage(root: Path, rows: list[dict], *, state: bool = True, header=_HEADER_LANED) -> Path:
    """The send list AND the sorted state behind it, as `lanes route` + `consolidate` leave them.

    The header carries `lane` and `suppression` on purpose: the shared `_HEADER` has neither,
    and a lane assertion made against a blank lane passes while testing nothing.
    """
    seq = root / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    out = seq / "ready-to-load.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    if state:
        evals = root / PROFILE / "prospects" / "evals"
        evals.mkdir(parents=True, exist_ok=True)
        (evals / "lanes-state.jsonl").write_text(
            "".join(
                json.dumps({"email": r["email"], "lane": r.get("lane", ""), "reason": "fixture"})
                + "\n"
                for r in rows
            ),
            encoding="utf-8",
        )
    return out


def _check(root: Path) -> rd.Readiness:
    """Run the check report as the skill does, then read it back as the status block does."""
    rep = pr.run_preflight(PROFILE, content_root=root, profiles_root=root, as_of=_AS_OF)
    pr.write_report(rep, content_root=root)
    return rd.load_readiness(PROFILE, root)


def _person(n: int, **over) -> dict:
    return _row(first=f"P{n}", email=f"p{n}@acme.example", **over)


# --- ported from the retired `_checked_count` ------------------------------------------


def test_no_list_on_disk_is_unknown_not_zero(root):
    """`None`, not 0. The checks have not seen a list that does not exist, and a confident 0
    would read as 'everything was refused'."""
    r = _check(root)
    assert r.state == "none" and r.admitted is None


def test_a_clean_send_row_is_counted(root):
    _stage(root, [_row(lane="signal")])
    _dossier(root, PROFILE)
    r = _check(root)
    assert r.state == "ok" and r.admitted == 1


def test_a_row_the_gate_would_refuse_is_not_counted(root):
    """`verdict=hold` never reaches enrollment, so it must not reach this count either."""
    _stage(root, [_row(lane="signal", verdict="hold")])
    _dossier(root, PROFILE)
    r = _check(root)
    assert r.admitted == 0 and r.fates["not_admitted"] == 1


def test_a_generic_lane_row_is_not_judged_by_signal_rules(root):
    """THE lane-awareness guard. `no-dossier` is an ERROR in the personalised batch and
    advisory in the general one; paired with the next test so the two batches must DISAGREE,
    which no unlaned implementation can satisfy (§R18)."""
    _stage(root, [_row(lane="generic")])  # deliberately no dossier
    assert _check(root).admitted == 1


def test_the_same_row_in_the_signal_lane_is_refused(root):
    _stage(root, [_row(lane="signal")])  # deliberately no dossier
    r = _check(root)
    assert r.admitted == 0 and r.fates["refused"] == 1
    ((batch, held, classes),) = r.refusals
    assert (batch, held) == ("signal", 1)
    ((rule, _count, unit),) = classes
    assert rule == "no-dossier"
    assert unit == "account"  # a dossier finding is per account, not per row


def test_a_blocked_lane_contributes_zero_not_its_candidates(root):
    """All-or-nothing per batch — the gate has no per-row pass result, so nothing in a
    refused batch may be reported as able to go."""
    _stage(root, [_person(1, lane="signal"), _person(2, lane="signal")])  # no dossier
    r = _check(root)
    assert r.admitted == 0 and r.fates["refused"] == 2


def test_a_suppressed_row_is_excluded(root):
    _stage(root, [_row(lane="signal", suppression="opted-out")])
    _dossier(root, PROFILE)
    r = _check(root)
    assert r.admitted == 0 and r.fates["suppressed"] == 1


def test_an_unreadable_report_is_unknown_not_a_smaller_number(root):
    """A smaller number and a correct one look identical. Refuse to give one."""
    _stage(root, [_row(lane="signal")])
    _dossier(root, PROFILE)
    _check(root)
    path = rd.report_path(PROFILE, root)
    path.write_text(path.read_text(encoding="utf-8")[:40], encoding="utf-8")  # truncated
    r = rd.load_readiness(PROFILE, root)
    assert r.state == "unreadable" and r.admitted is None and "latest.json" in r.problem


# --- conservation: no row leaves the count ---------------------------------------------


def _mixed(root: Path) -> list[dict]:
    rows = [
        _person(1, lane="signal"),  # admitted
        _person(2, lane="signal", verdict=""),  # not scored
        _person(3, lane="signal", verdict="re-angle"),  # scored for another kind of email
        _person(4, lane="signal", suppression="opted-out"),  # suppressed
        _person(5, lane="hold"),  # waiting on the operator
        _person(6, lane="excluded"),  # deliberately not emailed
        _person(7, lane=""),  # not yet sorted
        _person(8, lane="generic", verdict=""),  # admitted: the general email needs no score
    ]
    _stage(root, rows)
    _dossier(root, PROFILE)
    return rows


def test_every_row_has_exactly_one_fate(root):
    rows = _mixed(root)
    r = _check(root)
    assert r.state == "ok"
    assert r.rows == len(rows) == sum(r.fates.values())
    assert r.fates == {
        "admitted": 2,
        "refused": 0,
        "not_scored": 1,
        "not_admitted": 1,
        "judge_dropped": 0,
        "set_aside": 2,
        "suppressed": 1,
        "not_sorted": 1,
    }


def test_a_row_the_count_loses_is_an_error_not_a_smaller_answer(root, monkeypatch):
    """Negative control: make the verdict filter silently drop one more row. The count must
    raise, and the report must carry the error — never a total that quietly shrank."""
    from gtm_core import account_integrity

    real = account_integrity.filter_by_verdict

    def lossy(rows, want, *, lane=""):
        kept, stats = real(rows, want, lane=lane)
        if kept:
            kept = kept[1:]
            stats.kept -= 1
        return kept, stats

    monkeypatch.setattr(account_integrity, "filter_by_verdict", lossy)
    _mixed(root)
    r = _check(root)
    assert r.state == "unreadable" and "conservation" in r.problem


def test_conservation_is_checked_per_batch_too():
    block = {
        "rows": 2,
        "fates": {**dict.fromkeys(rd.FATES, 0), "admitted": 2},
        "lanes": [
            {"lane": "signal", "rows": 1, "fates": {**dict.fromkeys(rd.FATES, 0), "admitted": 2}},
            {"lane": "generic", "rows": 1, "fates": dict.fromkeys(rd.FATES, 0)},
        ],
    }
    with pytest.raises(ValueError, match="batch 'signal'"):
        rd.verify_readiness_conservation(block)


# --- the report agrees with the gate that spends money ---------------------------------


def _gate(root: Path, rows: list[dict], lane: str) -> int:
    """Run the REAL enrollment gate on one batch, as the email-sequence skill does."""
    from gtm_core import account_integrity

    batch = root / PROFILE / "prospects" / "sequences" / f"ready-to-load-{lane}.csv"
    with batch.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_HEADER_LANED)
        w.writeheader()
        for r in rows:
            if r.get("lane") == lane:
                w.writerow({k: r.get(k, "") for k in _HEADER_LANED})
    return account_integrity.main(
        [
            "--csv",
            str(batch),
            "--profile",
            PROFILE,
            "--require-verdict",
            "send",
            "--lane",
            lane,
            "--as-of",
            _AS_OF.isoformat(),
        ]
    )


@pytest.mark.parametrize("dossier", [True, False], ids=["gate-passes", "gate-refuses"])
def test_the_report_and_the_gate_agree_on_every_batch(root, dossier, capsys):
    rows = [
        _person(1, lane="signal"),
        _person(2, lane="signal", verdict=""),
        _person(3, lane="generic", verdict=""),
    ]
    _stage(root, rows)
    if dossier:
        _dossier(root, PROFILE)
    rep = pr.run_preflight(PROFILE, content_root=root, profiles_root=root, as_of=_AS_OF)
    by_lane = {b["lane"]: b for b in rep.readiness["lanes"]}
    for lane in ("signal", "generic"):
        admitted = by_lane[lane]["fates"]["admitted"] > 0
        refused = by_lane[lane]["fates"]["refused"] > 0
        exit_code = _gate(root, rows, lane)
        assert (exit_code == 0) == (not refused), (lane, exit_code, by_lane[lane])
        assert admitted == (exit_code == 0 and by_lane[lane]["fates"]["admitted"] > 0)
    capsys.readouterr()


def test_a_contact_on_an_excluded_account_refuses_its_whole_batch(root):
    """The gate's account-status join refuses the whole list, not the row — so the report
    must say the batch is held and why, in the operator's words."""
    rows = [_person(1, lane="generic", verdict=""), _person(2, lane="generic", verdict="")]
    _stage(root, rows)
    (root / PROFILE / "prospects" / "latest.json").write_text(
        json.dumps(
            {
                "kind": "prospects",
                "items": [
                    {
                        "company": "Acme Logistics",
                        "domain": "acme.example",
                        "status": "do-not-contact",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = _check(root)
    assert r.admitted == 0 and r.fates["refused"] == 2
    assert r.refusals == [("generic", 2, [(rd.ACCOUNT_STATUS, 2, "row")])]


def test_a_row_the_sorted_list_does_not_know_refuses_its_batch_in_both(root, capsys):
    """The retired count skipped this gate check, so it could say a row may go when the
    gate would refuse it. Paired with the real gate so the two must agree."""
    rows = [_row(lane="generic", verdict="")]
    _stage(root, rows, state=False)
    r = _check(root)
    assert r.admitted == 0 and r.refusals[0][2][0][0] == rd.LANE_STATE
    evals = root / PROFILE / "prospects" / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "lanes-state.jsonl").write_text(
        json.dumps({"email": "someone-else@acme.example", "lane": "generic"}) + "\n"
    )
    assert _gate(root, rows, "generic") != 0
    capsys.readouterr()


# --- staleness: a pass is only a pass for the list it measured -------------------------


@pytest.mark.parametrize("which", ["list", "sorted", "ledger", "suppression"])
def test_a_change_to_any_file_the_gate_reads_makes_the_answer_stale(root, which):
    _stage(root, [_row(lane="signal")])
    _dossier(root, PROFILE)
    assert _check(root).state == "ok"
    path = rd.input_paths(PROFILE, root)[which]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    r = rd.load_readiness(PROFILE, root)
    assert r.state == "stale" and r.admitted is None


def test_an_answer_from_another_day_is_stale(root, monkeypatch):
    """A reason to write ages out day by day, and no file changes when it does."""
    import datetime

    _stage(root, [_row(lane="signal")])
    _dossier(root, PROFILE)
    assert _check(root).state == "ok"
    monkeypatch.setattr(rd, "_today", lambda: _AS_OF + datetime.timedelta(days=1))
    r = rd.load_readiness(PROFILE, root)
    assert r.state == "stale" and r.admitted is None


def test_the_report_path_refuses_a_profile_that_is_not_one_segment(tmp_path):
    with pytest.raises(ValueError):
        rd.report_path("../other-tenant", tmp_path)


def test_a_report_from_before_this_change_is_unknown_not_zero(root):
    path = rd.report_path(PROFILE, root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ran_at": "2026-09-01T00:00:00+00:00", "checks": []}))
    assert rd.load_readiness(PROFILE, root).state == "none"


@pytest.mark.parametrize(
    "block",
    [
        {"rows": 1, "fates": {"admitted": 1}, "lanes": []},  # fates missing keys
        {"rows": -1, "fates": dict.fromkeys(rd.FATES, 0), "lanes": []},  # negative
        {"rows": 1, "fates": dict.fromkeys(rd.FATES, 0), "lanes": []},  # does not add up
        {"rows": 0, "fates": dict.fromkeys(rd.FATES, 0), "lanes": "x"},  # wrong type
        "not an object",
    ],
)
def test_a_wrong_shape_is_unreadable_never_a_count(root, block):
    path = rd.report_path(PROFILE, root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ran_at": "2026-09-01T00:00:00+00:00", "readiness": block}))
    r = rd.load_readiness(PROFILE, root)
    assert r.state == "unreadable" and r.admitted is None


# --- the lede: words, not ids; observed states only ------------------------------------


def _ready(**over) -> rd.Readiness:
    base = {
        "state": "ok",
        "ran_at": "2026-09-24T10:31:07+00:00",
        "rows": 10,
        "fates": {**dict.fromkeys(rd.FATES, 0), "admitted": 6, "not_scored": 4},
    }
    return rd.Readiness(**{**base, **over})


def _lede(r: rd.Readiness, **over) -> str:
    kw = {
        "counts": {"waiting_on_you": 0, "in_sending_tool": 3},
        "buckets": {"failed_enrichment": 5, "failed_intent": 0, "not_routed": 2},
        "sheet": None,
        "now": "2026-09-24 11:00 UTC",
    }
    return "\n".join(ld.compose_lede(r, **{**kw, **over}))


def test_a_pass_names_the_count_and_accounts_for_the_rest():
    out = _lede(_ready())
    assert "Today: 6 of the 10 people on the list can go out. The rest: 4 not yet scored." in out
    assert "The checks last ran 2026-09-24 10:31 UTC." in out


@pytest.mark.parametrize("state", ["none", "stale", "unreadable"])
def test_no_number_is_shown_when_the_answer_cannot_be_trusted(state):
    out = _lede(rd.Readiness(state=state))
    assert "Today: unknown" in out and "can go out" not in out


def test_a_refusal_reads_as_a_reason_and_a_next_step():
    r = _ready(
        fates={**dict.fromkeys(rd.FATES, 0), "refused": 10},
        refusals=[("signal", 10, [("no-dossier", 3, "account")])],
    )
    out = _lede(r)
    assert "Today: none of the 10 people on the list can go out yet." in out
    assert "  Why 10 are held back — the checks refused the personalised batch for:" in out
    assert (
        "    3 accounts with no research file for the company. To fix: run the account research."
        in out
    )


def test_every_blocking_reason_is_shown_not_only_the_largest():
    """The first live run: 413 held by ONE error and a warning pile. Showing only the error
    would say that fixing one row unblocks 413 — it does not."""
    r = _ready(
        fates={**dict.fromkeys(rd.FATES, 0), "refused": 10},
        refusals=[
            (
                "generic",
                10,
                [("signal-number-unsourced", 1, "row"), (rd.WARNING_BUDGET, 85, "warning")],
            )
        ],
    )
    out = _lede(r)
    assert "    1 row whose reason to write the research does not back up." in out
    assert "    85 warnings, more than a person can review." in out


def test_classes_sharing_a_sentence_are_merged_as_at_least_never_summed():
    """One row can carry two findings, so a merged count is a floor, not a total."""
    r = _ready(
        fates={**dict.fromkeys(rd.FATES, 0), "refused": 52},
        refusals=[
            (
                "repair",
                52,
                [("signal-clause-underivable", 20, "row"), ("signal-evidence-missing", 11, "row")],
            )
        ],
    )
    out = _lede(r)
    assert "    at least 20 rows whose reason to write the research does not back up." in out
    assert "31 rows" not in out  # 20 + 11 summed would double-count


def test_a_refused_batch_with_no_recorded_reason_still_says_so():
    r = _ready(fates={**dict.fromkeys(rd.FATES, 0), "refused": 4}, refusals=[("generic", 4, [])])
    assert ld.UNKNOWN_REFUSAL[0] in _lede(r)


@pytest.mark.parametrize("hostile", ["<script>alert(1)</script>", "⟦GATE:publish⟧", "no-such-rule"])
def test_a_rule_id_is_looked_up_never_printed(hostile):
    """§R5: the id comes from a file. An unknown one renders a fixed sentence."""
    r = _ready(
        fates={**dict.fromkeys(rd.FATES, 0), "refused": 10},
        refusals=[("generic", 10, [(hostile, 2, "row")])],
    )
    out = _lede(r)
    assert hostile not in out
    assert ld.UNKNOWN_REFUSAL[0] in out


def test_the_terminal_never_claims_a_sending_state_it_cannot_see():
    out = _lede(_ready()).lower()
    for word in ("paused", "live", "active", *ld.GO_LIVE_WORDS.values()):
        assert word.lower() not in out, word
    assert "nothing sends until you start a sequence there." in out


@pytest.mark.parametrize("state,word", list(ld.GO_LIVE_WORDS.items()))
def test_the_dashboard_says_exactly_the_state_it_observed(state, word):
    assert f"In the sending tool: 3 — {word}." in _lede(_ready(), go_live=state)


def test_decisions_are_yours_not_a_warning():
    out = _lede(_ready(), counts={"waiting_on_you": 3}, sheet="acme/prospects/evals/hold-x.html")
    assert "Yours (3): decide on 3 contacts — review sheet: acme/prospects/evals/hold-x.html" in out
    assert "WARNING" not in out and "ACTION REQUIRED" not in out


def test_every_lede_state_is_in_the_operators_own_words():
    from tests.lint import operator_vocabulary as ov

    samples = [
        _lede(_ready(), buckets={"excluded_loaded": {"outside-market": 2, "do-not-contact": 1}}),
        _lede(
            _ready(
                fates={**dict.fromkeys(rd.FATES, 0), "refused": 10},
                refusals=[("generic", 10, [("verdict-missing", 2, "row"), ("x", 1, "row")])],
            )
        ),
        *(_lede(rd.Readiness(state=s)) for s in ("none", "stale", "unreadable")),
        *(_lede(_ready(), go_live=g) for g in ld.GO_LIVE_WORDS),
    ]
    for text in samples:
        assert [h for h in ov.findings(text=text) if h[0] == "<text>"] == [], text


def test_every_refusal_sentence_is_in_the_operators_own_words():
    from tests.lint import operator_vocabulary as ov

    copies = [
        *ld.REFUSAL_COPY.values(),
        *(c for _p, c in ld.REFUSAL_FAMILIES),
        ld.UNKNOWN_REFUSAL,
        ld.WARNING_PILE,
    ]
    text = "\n".join(f"{m}. {u}." for m, u in copies)
    assert [h for h in ov.findings(text=text) if h[0] == "<text>"] == []


def _emitted_rule_ids() -> set[str]:
    """Every rule id the content audit can refuse on: the `rule:` prefixes the account audit
    writes, plus every quoted rule-shaped literal in the research-record audit."""
    import re

    ids: set[str] = set()
    ai = (REPO / "gtm_core" / "account_integrity.py").read_text(encoding="utf-8")
    ids |= set(re.findall(r'f?"([a-z]+(?:-[a-z]+)+): ', ai))
    sr = (REPO / "gtm_core" / "signal_record.py").read_text(encoding="utf-8")
    ids |= {
        m
        for m in re.findall(r'"([a-z]+(?:-[a-z]+)+)"', sr)
        if m.split("-")[0] in {"signal", "verdict", "relation", "agent"}
    }
    return ids


def test_every_rule_the_audit_can_refuse_on_has_its_own_sentence():
    """Totality. A new rule without copy would render the fallback — true, but useless."""
    ids = _emitted_rule_ids()
    assert len(ids) > 20, "the scan found too few ids to be a real enumeration"
    missing = sorted(i for i in ids if ld.refusal_copy(i) is ld.UNKNOWN_REFUSAL)
    assert missing == []


# --- nothing leaves a file; nothing reaches the network --------------------------------


def test_no_row_leaves_any_file(root, capsys):
    """The operator's actual fear, as a test: running the checks, the status block and the
    dashboard changes nothing under the prospects tree except the check report itself."""
    from gtm_core import prospect_status_cli

    _mixed(root)
    (root / PROFILE / "prospects" / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": []}), encoding="utf-8"
    )

    def snapshot() -> dict[str, tuple[int, int]]:
        out = {}
        for p in (root / PROFILE).rglob("*"):
            if p.is_file() and "preflight" not in p.parts:
                st = p.stat()
                out[str(p)] = (st.st_mtime_ns, st.st_size)
        return out

    before = snapshot()
    pr.write_report(
        pr.run_preflight(PROFILE, content_root=root, profiles_root=root, as_of=_AS_OF),
        content_root=root,
    )
    assert prospect_status_cli.main(["--profile", PROFILE]) == 0
    assert snapshot() == before
    written = {p.name for p in (root / PROFILE / "preflight").iterdir()}
    assert "latest.json" in written and len(written) == 2
    assert all(n == "latest.json" or n.startswith("report-") for n in written)
    capsys.readouterr()


@pytest.mark.parametrize("module", ["prospect_readiness.py", "prospect_lede.py"])
def test_the_readiness_modules_reach_no_model_and_no_network(module):
    path = REPO / "gtm_core" / module
    assert not (_imported_names(path) & _FORBIDDEN_IMPORTS)
    assert "resolve_model" not in path.read_text(encoding="utf-8")


def test_the_status_block_opens_with_the_lede_and_keeps_the_record(root, capsys):
    from gtm_core import prospect_status_cli

    _stage(root, [_row(lane="signal")])
    _dossier(root, PROFILE)
    (root / PROFILE / "prospects" / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": []}), encoding="utf-8"
    )
    _check(root)
    assert prospect_status_cli.main(["--profile", PROFILE]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("As of ")
    assert out[1] == "Today: 1 of the 1 person on the list can go out."
    record = out.index(f"{ld.LEDE_TAIL} — the detail behind the lines above:")
    assert any(ln.startswith("Accounts — where each stands") for ln in out[record:])
    assert not any("ACTION REQUIRED" in ln for ln in out)
    assert os.environ["GTM_CONTENT_ROOT"] == str(root)


# --- PS15 operator decision: automatic, and only genuine risks are named -----------------


def test_the_hold_rule_names_each_case_and_fails_closed():
    from gtm_core.account_exclusion_keys import account_hold_reason

    assert account_hold_reason({"tier": "C", "score": 51}) is None
    assert account_hold_reason({"tier": "D"}) is None  # not decided here; unchanged
    assert account_hold_reason({"verdict": "drop"}) == "drop"
    assert (
        account_hold_reason(
            {"tier": "unscored", "score_missing_inputs": ["research_on_file", "in_target_market"]}
        )
        == "outside-market"
    ), "the market block wins over the research gap whatever order the inputs are listed in"
    assert (
        account_hold_reason({"tier": "Unscored", "score_missing_input": "agent_evidence"})
        == "needs-research"
    )
    assert account_hold_reason({"tier": "unscored"}) == "needs-research", (
        "no recorded input: fail closed"
    )


def test_the_gate_refuses_a_list_holding_an_account_the_build_would_have_removed(root):
    """Defence in depth: a list built before a rescore is refused, not sent."""
    from gtm_core.enrollment_gate import check_account_status

    (root / PROFILE / "prospects").mkdir(parents=True, exist_ok=True)
    (root / PROFILE / "prospects" / "latest.json").write_text(json.dumps({"kind": "prospects", "items": [
        {"company": "Acme Logistics", "domain": "acme.example", "tier": "unscored",
         "score_missing_inputs": ["in_target_market"]}]}))  # fmt: skip
    refusal = check_account_status([_row()], PROFILE, root)
    assert refusal and "outside target markets" in refusal

    (root / PROFILE / "prospects" / "latest.json").write_text(json.dumps({"kind": "prospects", "items": [
        {"company": "Acme Logistics", "domain": "acme.example", "tier": "C", "score": 55}]}))  # fmt: skip
    assert check_account_status([_row()], PROFILE, root) is None


def test_tier_c_is_a_fit_and_unscored_research_is_the_machines_work():
    from gtm_core import prospect_status_receipt as R

    c = {"company": "Acme Logistics", "domain": "acme.example", "tier": "C"}
    market = {"company": "Beta Freight", "domain": "beta.example", "tier": "unscored",
              "score_missing_inputs": ["in_target_market"]}  # fmt: skip
    research = {"company": "Gamma Ports", "domain": "gamma.example", "tier": "unscored",
                "score_missing_input": "research_on_file"}  # fmt: skip
    assert R.fit_failure_reason(c) is None
    assert R.fit_failure_reason(market) == "outside-market"
    assert R.fit_failure_reason(research) is None and R._fails_intent(research)

    def contact(email, domain, status):
        return {"email": email, "company_domain": domain, "status": status}

    routed = [
        contact("a@acme.example", "acme.example", "ready_to_send"),
        contact("b@beta.example", "beta.example", "in_sending_tool"),
        contact("c@gamma.example", "gamma.example", "ready_to_send"),
    ]
    rec = R.compute_attrition_receipt([c, market, research], routed)
    assert (rec.ready, rec.failed_fit, rec.failed_intent) == (1, 1, 1)
    assert rec.excluded_loaded == {"outside-market": 1}


def test_a_loaded_contact_at_a_closed_company_is_named_to_the_person_in_plain_words():
    out = _lede(_ready(), buckets={"excluded_loaded": {"outside-market": 2, "do-not-contact": 1}})
    assert (
        "Yours, before you start a sequence: take 3 people out of the sending tool: 2 at "
        "companies outside your target markets, 1 on the do-not-contact list." in out
    )
    one_reason = _lede(_ready(), buckets={"excluded_loaded": {"outside-market": 2}})
    assert (
        "take 2 people out of the sending tool, all at companies outside your target markets."
        in one_reason
    )


def test_an_unknown_risk_reason_still_reads_as_plain_words():
    out = _lede(_ready(), buckets={"excluded_loaded": {"<script>": 1}})
    assert "<script>" not in out and "at companies closed to sending" in out


def test_the_lede_carries_no_counting_discrepancies():
    """Operator direction: internal workings stay out of the lede."""
    out = _lede(_ready())
    assert "Check:" not in out and "maintains your setup" not in out
