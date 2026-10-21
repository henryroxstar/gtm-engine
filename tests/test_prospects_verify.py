"""Tests for gtm_core.prospects_verify — read-only reconciliation across prospect stores.

PRD-2026-09-28 Phases 4a/4b.
"""

from __future__ import annotations

import csv
import json

from gtm_core import prospects_verify as pv
from gtm_core.prospect_paths import latest_json, run_state_json, suppression_ledger
from gtm_core.prospects_consolidate.paths import ready_to_load_path
from gtm_core.run_state import RunState, save_run_state


def _write_latest(root, profile, items):
    p = latest_json(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"kind": "prospects", "profile": profile, "items": items}))
    return p


def _write_ready(root, profile, account_ids):
    p = ready_to_load_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["account_id", "email"])
        w.writeheader()
        for aid in account_ids:
            w.writerow({"account_id": aid, "email": f"{aid}@example.com"})
    return p


def _profile_md(profiles_root, profile):
    p = profiles_root / profile
    p.mkdir(parents=True, exist_ok=True)
    (p / "PROFILE.md").write_text("# test profile")


def test_clean_run_reports_ok_across_six_checks(tmp_path):
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    _profile_md(profiles_root, "acme")
    _write_latest(
        content_root,
        "acme",
        [{"account_id": "a-1", "company": "Northwind", "verdict": "send", "email": "a@x.com"}],
    )
    _write_ready(content_root, "acme", ["a-1"])

    report = pv.verify("acme", content_root=content_root, profiles_root=profiles_root)
    assert report.all_findings == []
    assert report.unreadable == []
    assert not report.failed

    line = pv.render(report)
    assert line.startswith("OK — 0 findings across 6 checks")
    assert "send-list drift" in line
    assert line.endswith("Exit 0.")


def test_send_list_drift_is_detected(tmp_path):
    """Gap 1: a ledger account marked verdict=send with no row in ready-to-load.csv."""
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [{"account_id": "a-1", "company": "Northwind", "verdict": "send"}],
    )
    _write_ready(content_root, "acme", [])  # empty — consolidate hasn't run since

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["send-list drift"]
    assert len(findings) == 1
    assert findings[0].startswith("send-list-drift:")
    assert "Northwind" in findings[0]
    assert not report.failed  # drift is not send-blocking by itself


def test_duplicate_account_collapses_to_one_finding_not_two_drift_mismatches(tmp_path):
    """PRD §4.B: a known duplicate-account pair must surface as at most one finding, never
    two independent unexplained send-list-drift mismatches."""
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [
            {"account_id": "a-1", "company": "Acme Robotics (row 1)", "verdict": "send"},
            {"account_id": "a-1", "company": "Acme Robotics (row 2)", "verdict": "re-angle"},
        ],
    )
    _write_ready(content_root, "acme", [])

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["send-list drift"]
    assert len(findings) == 1
    assert findings[0].startswith("duplicate-account:")


def test_duplicate_account_is_caught_even_when_neither_row_has_an_account_id(tmp_path):
    """Round-2 red-team finding: grouping by account_id alone missed a duplicate pair that
    predates or bypassed id-stamping (the known real-world shape) — both rows here share a
    domain instead, and must still collapse to one finding, not zero."""
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [
            {"company": "Acme Robotics", "domain": "acme.example", "verdict": "send"},
            {"company": "Acme Robotics", "domain": "acme.example", "verdict": "re-angle"},
        ],
    )
    _write_ready(content_root, "acme", [])

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["send-list drift"]
    assert len(findings) == 1
    assert findings[0].startswith("duplicate-account:")


def test_send_list_drift_matches_by_domain_when_account_id_is_absent(tmp_path):
    """A ledger row with no account_id must still be checked against ready-to-load.csv via
    its domain, not silently skipped."""
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [{"company": "Acme Robotics", "domain": "acme.example", "verdict": "send"}],
    )
    _write_ready(content_root, "acme", [])  # empty — drift

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["send-list drift"]
    assert len(findings) == 1
    assert findings[0].startswith("send-list-drift:")


def test_vocabulary_integrity_is_send_blocking(tmp_path):
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [{"account_id": "a-1", "company": "Northwind", "verdict": "prospect"}],
    )
    _write_ready(content_root, "acme", [])

    report = pv.verify("acme", content_root=content_root)
    assert report.findings["vocabulary integrity"]
    assert report.failed  # this check IS send-blocking


def test_duplicate_stores_detected(tmp_path):
    from gtm_core.outcomes import outcomes_path as profile_outcomes_path
    from gtm_core.prospect_paths import outcomes_jsonl

    content_root = tmp_path
    _write_latest(content_root, "acme", [])
    _write_ready(content_root, "acme", [])
    p1 = outcomes_jsonl("acme", content_root)
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text("")
    p2 = profile_outcomes_path(content_root, "acme")
    p2.parent.mkdir(parents=True, exist_ok=True)
    p2.write_text("")

    report = pv.verify("acme", content_root=content_root)
    assert report.findings["duplicate stores"]
    assert not report.failed  # duplicate-store is not send-blocking


def test_ledger_suppression_verdict_leak_and_unsuppressed_drop(tmp_path):
    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [
            {
                "account_id": "a-1",
                "company": "Northwind",
                "verdict": "drop",
                "email": "notsuppressed@example.com",
                "why_now": "dated public why-now not found for this account",
            }
        ],
    )
    _write_ready(content_root, "acme", [])

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["ledger suppression"]
    assert any(f.startswith("verdict-leak:") for f in findings)
    assert any(f.startswith("unsuppressed-drop:") for f in findings)
    assert report.failed  # send-blocking


def test_ledger_suppression_clean_when_drop_account_is_suppressed(tmp_path):
    from gtm_core.suppression import Suppression, append

    content_root = tmp_path
    _write_latest(
        content_root,
        "acme",
        [
            {
                "account_id": "a-1",
                "company": "Northwind",
                "verdict": "drop",
                "email": "suppressed@example.com",
            }
        ],
    )
    _write_ready(content_root, "acme", [])
    append(
        suppression_ledger("acme", content_root),
        [Suppression(email="suppressed@example.com", reason="out-of-market")],
    )

    report = pv.verify("acme", content_root=content_root)
    assert report.findings.get("ledger suppression", []) == []


def test_run_state_staleness_flags_a_stage_that_died_midway(tmp_path):
    content_root = tmp_path
    _write_latest(content_root, "acme", [])
    _write_ready(content_root, "acme", [])
    state = RunState.new(profile="acme")
    state.start_stage("signal_hunt")  # never completed — the process "died" here
    save_run_state(state, run_state_json("acme", content_root))

    report = pv.verify("acme", content_root=content_root)
    findings = report.findings["run-state staleness"]
    assert any("signal_hunt" in f for f in findings)
    assert not report.failed  # staleness alone is not send-blocking


def test_profile_guard_flags_a_content_dir_with_no_profile_md(tmp_path):
    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"
    (content_root / "ghost-tenant").mkdir(parents=True)
    _write_latest(content_root, "ghost-tenant", [])
    _write_ready(content_root, "ghost-tenant", [])
    # No profiles/ghost-tenant/PROFILE.md written.

    report = pv.verify("ghost-tenant", content_root=content_root, profiles_root=profiles_root)
    assert report.findings["profile-guard"]


def test_unreadable_latest_json_is_a_finding_not_a_silent_empty_report(tmp_path):
    content_root = tmp_path
    p = latest_json("acme", content_root=content_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")
    _write_ready(content_root, "acme", [])

    report = pv.verify("acme", content_root=content_root)
    assert any(u.startswith("unreadable:") for u in report.unreadable)
    assert report.failed  # an unreadable store is treated as a blocking uncertainty


def test_unreadable_run_state_is_a_finding(tmp_path):
    content_root = tmp_path
    _write_latest(content_root, "acme", [])
    _write_ready(content_root, "acme", [])
    p = run_state_json("acme", content_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")

    report = pv.verify("acme", content_root=content_root)
    assert any(u.startswith("unreadable:") for u in report.unreadable)


def test_finding_budget_boundary_16_truncates_15_does_not(tmp_path):
    """T2: gtm_core/finding_budget.py's WARN_BUDGET is 15, not ~10 (an earlier draft of this
    PRD and its test plan both said "~10"). Verified directly against render_budget's real
    shape (account_integrity.py's own usage, the house pattern this module mirrors): over
    budget, exemplars are capped at 3 and a "... +N more" truncation line is appended; at or
    under budget, no truncation line appears and every class's full count is shown, not cut."""
    content_root = tmp_path
    profiles_root = tmp_path / "profiles"
    _profile_md(profiles_root, "acme16")
    _profile_md(profiles_root, "acme15")

    items_16 = [
        {"account_id": f"a-{i}", "company": f"Company{i}", "verdict": "prospect"} for i in range(16)
    ]
    _write_latest(content_root, "acme16", items_16)
    _write_ready(content_root, "acme16", [])
    line16 = pv.render(pv.verify("acme16", content_root=content_root, profiles_root=profiles_root))
    assert "more in this class" in line16  # over budget: truncated
    assert "Company15" not in line16  # only 3 exemplars shown, not all 16

    items_15 = [
        {"account_id": f"b-{i}", "company": f"Company{i}", "verdict": "prospect"} for i in range(15)
    ]
    _write_latest(content_root, "acme15", items_15)
    _write_ready(content_root, "acme15", [])
    line15 = pv.render(pv.verify("acme15", content_root=content_root, profiles_root=profiles_root))
    assert "more in this class" not in line15  # at budget: not truncated
    assert "15/15" in line15  # the full count is shown, not cut short


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _write_latest(tmp_path, "clean", [])
    _write_ready(tmp_path, "clean", [])
    assert pv.main(["--profile", "clean"]) == 0

    _write_latest(tmp_path, "dirty", [{"account_id": "a-1", "company": "X", "verdict": "prospect"}])
    _write_ready(tmp_path, "dirty", [])
    assert pv.main(["--profile", "dirty"]) == 1
    assert pv.main(["--profile", "dirty", "--warn-only"]) == 0


def test_strict_exit_code_matches_the_printed_report_line(tmp_path, monkeypatch, capsys):
    """Round-2 red-team finding: --strict's returned exit code used to be computed separately
    from what render() printed, so a --strict run with only a non-blocking finding printed
    'Exit 0.' while the process actually returned 1. They must always agree."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    from gtm_core.outcomes import outcomes_path as profile_outcomes_path
    from gtm_core.prospect_paths import outcomes_jsonl

    _write_latest(tmp_path, "strict-case", [])
    _write_ready(tmp_path, "strict-case", [])
    p1 = outcomes_jsonl("strict-case", tmp_path)
    p1.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text("")
    p2 = profile_outcomes_path(tmp_path, "strict-case")
    p2.parent.mkdir(parents=True, exist_ok=True)
    p2.write_text("")  # a duplicate-store finding: real, but not send-blocking

    rc = pv.main(["--profile", "strict-case", "--strict"])
    printed = capsys.readouterr().out
    assert rc == 1
    assert "Exit 1." in printed  # must match, never "Exit 0." while rc == 1

    rc_non_strict = pv.main(["--profile", "strict-case"])
    assert rc_non_strict == 0  # the same finding, without --strict, does not block


def test_prospects_verify_never_calls_a_write_function() -> None:
    """§4 item 1 (destructive reachability), by code inspection: no --apply/--write flag,
    and no path in this module ever opens a file for writing."""
    import inspect

    from gtm_core import prospects_verify as module

    source = inspect.getsource(module)
    assert "--apply" not in source
    assert "--write" not in source
    for banned in ("_atomic_write", "open(", "write_text", "enforce_drop_suppression("):
        assert banned not in source, f"{banned} would make this module a writer"
