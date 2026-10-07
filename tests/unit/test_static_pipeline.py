"""Tests for static-email mode pipeline."""

from __future__ import annotations

from gtm_core.static_pipeline import (
    partition_by_region,
    validate_static_sequence_steps,
)
from gtm_core.static_windows import SendWindows, parse_send_windows


def test_send_windows_resolution():
    toml = """
    schema = 1
    default_schedule = "sched_default"
    [countries]
    US = "sched_us"
    CA = "sched_us"
    """
    w = parse_send_windows(toml)
    assert w.schedule_for("US") == "sched_us"
    assert w.schedule_for("ca") == "sched_us"
    assert w.schedule_for("SG") == "sched_default"
    assert w.schedule_for("") == "sched_default"


def test_validate_static_steps():
    # Valid steps: step 1 has subject, step 2 has new thread subject + 5 days wait
    valid_steps = [
        {"subject": "Hello", "body": "Body 1", "wait_days": 0},
        {"subject": "Follow up thread", "body": "Body 2", "wait_days": 5},
    ]
    res = validate_static_sequence_steps(valid_steps)
    assert res.valid is True
    assert not res.errors

    # Invalid step 2: blank subject (same thread) and 3 days wait
    invalid_steps = [
        {"subject": "Hello", "body": "Body 1", "wait_days": 0},
        {"subject": "", "body": "Body 2", "wait_days": 3},
    ]
    res2 = validate_static_sequence_steps(invalid_steps)
    assert res2.valid is False
    assert len(res2.errors) == 2

    # Waived same-thread and short-gap
    res3 = validate_static_sequence_steps(
        invalid_steps, waived_rules=["same-thread-step2", "short-gap-followup"]
    )
    assert res3.valid is True


def test_partition_by_region():
    windows = SendWindows(
        default_schedule="sched_sg",
        countries={"US": "sched_us"},
    )
    rows = [{"email": f"us{i}@example.com", "country": "US"} for i in range(30)] + [
        {"email": f"sg{i}@example.com", "country": "SG"} for i in range(10)
    ]
    res = partition_by_region(rows, windows, pilot_size=25)
    assert "sched_us" in res
    assert len(res["sched_us"].pilot) == 25
    assert len(res["sched_us"].rest) == 5

    assert "sched_sg" in res
    assert len(res["sched_sg"].pilot) == 10
    assert len(res["sched_sg"].rest) == 0


def test_send_windows_country_alias_normalization():
    toml = """
    schema = 1
    default_schedule = "sched_default"
    [countries]
    US = "sched_us"
    CA = "sched_ca"
    GB = "sched_uk"
    SG = "sched_sg"
    """
    w = parse_send_windows(toml)
    assert w.schedule_for("United States") == "sched_us"
    assert w.schedule_for("USA") == "sched_us"
    assert w.schedule_for("U.S.A.") == "sched_us"
    assert w.schedule_for("Canada") == "sched_ca"
    assert w.schedule_for("United Kingdom") == "sched_uk"
    assert w.schedule_for("UK") == "sched_uk"
    assert w.schedule_for("Singapore") == "sched_sg"


def test_filter_static_audience_suppression_and_dedupe(tmp_path):
    from gtm_core.prospect_paths import suppression_ledger
    from gtm_core.static_pipeline import filter_static_audience

    content_root = tmp_path / "content"
    supp_file = suppression_ledger("test_profile", content_root=content_root)
    supp_file.parent.mkdir(parents=True, exist_ok=True)
    supp_file.write_text(
        "email,reason,date,company_domain\n"
        "suppressed@a.example,unsubscribed,2026-08-01,blockme.example\n",
        encoding="utf-8",
    )

    rows = [
        {"email": "suppressed@a.example", "company": "Co A", "company_domain": "a.example"},
        {
            "email": "other@blockme.example",
            "company": "Block Corp",
            "company_domain": "blockme.example",
        },
        {
            "email": "person1@valid.example",
            "company": "Valid Corp",
            "company_domain": "valid.example",
        },
        {
            "email": "person2@valid.example",
            "company": "Valid Corp",
            "company_domain": "valid.example",
        },
    ]

    res = filter_static_audience(rows, "test_profile", content_root=content_root)
    assert len(res.kept) == 1
    assert res.kept[0]["email"] == "person1@valid.example"
    assert res.counts_by_reason.get("suppressed") == 2
    assert res.counts_by_reason.get("duplicate-company") == 1


# --- existing customers / case-study companies -----------------------------------------------
# A paying pilot sat in a cold static sequence and only surfaced because the linter's
# named-case-study rule happened to see the name rendered. The filter must catch it by identity.


def _customer_setup(tmp_path, roster: bytes | None):
    from gtm_core.account_relation import Regulators, RelationIndex

    profiles_root = tmp_path / "profiles"
    knowledge = profiles_root / "acme" / "knowledge"
    knowledge.mkdir(parents=True)
    if roster is not None:
        (knowledge / "outreach-case-studies.txt").write_bytes(roster)
    return {
        "content_root": tmp_path / "content",
        "profiles_root": profiles_root,
        "index": RelationIndex(Regulators()),
    }


def test_filter_excludes_existing_customer_by_name_domain_and_email_domain(tmp_path):
    from gtm_core.static_pipeline import filter_static_audience

    kw = _customer_setup(
        tmp_path, b"# header comment\ncopperline\nwideloop interactive\nnorthwind\n"
    )
    rows = [
        # by company name (case-insensitive, whole word)
        {"email": "a@one.example", "company": "Copperline Analytics Pte Ltd"},
        # by company_domain only
        {"email": "b@two.example", "company": "CL Group", "company_domain": "copperline.example"},
        # by email domain only, under a subdomain
        {"email": "c@mail.copperline.example", "company": "Unlisted Holdings"},
        # a multi-word roster name written as one domain label
        {"email": "d@wideloopinteractive.example", "company": "WI"},
        # ...and hyphenated
        {
            "email": "e@three.example",
            "company": "",
            "company_domain": "wideloop-interactive.example",
        },
        # word-bounded: a longer word that merely starts with a roster name is NOT a customer
        {"email": "f@northwinds.example", "company": "Northwinds Corp"},
        {"email": "g@copperlineage.example", "company": "Copperlineage Co"},
    ]
    res = filter_static_audience(rows, "acme", **kw)

    excluded = {r["email"] for r, why in res.excluded if why == "existing-customer"}
    assert excluded == {
        "a@one.example",
        "b@two.example",
        "c@mail.copperline.example",
        "d@wideloopinteractive.example",
        "e@three.example",
    }
    assert res.counts_by_reason["existing-customer"] == 5
    assert {r["email"] for r in res.kept} == {"f@northwinds.example", "g@copperlineage.example"}


def test_filter_refuses_an_unreadable_customer_roster(tmp_path):
    import pytest

    from gtm_core.static_pipeline import CustomerRosterError, filter_static_audience

    kw = _customer_setup(tmp_path, b"copperline\n\xff\xfe\xfa not utf-8\n")
    with pytest.raises(CustomerRosterError, match="outreach-case-studies.txt"):
        filter_static_audience([{"email": "a@one.example", "company": "Copperline"}], "acme", **kw)


def test_filter_without_a_customer_roster_excludes_nobody_as_customer(tmp_path):
    from gtm_core.static_pipeline import filter_static_audience

    kw = _customer_setup(tmp_path, None)
    res = filter_static_audience(
        [{"email": "a@one.example", "company": "Copperline"}], "acme", **kw
    )
    assert "existing-customer" not in res.counts_by_reason
    assert len(res.kept) == 1


# --- already-enrolled agrees with the lane router ---------------------------------------------
# 2026-10-06: static mode refused 11 people who had never been emailed, held only by cells.toml
# lists registered under DRAFT-* ids and under sequences history.jsonl records as deleted at 0
# sends. The router already stopped counting those lists; static mode read a second copy that
# counted every registered csv. Protection for people a deleted sequence DID email comes from the
# suppression ledger and sequences/contacted-*.csv, not from the cells.toml row.
# Fictional identities from `python -m gtm_core.fictionalize`.

_LIVE = "avery.kraft@lantern.example"
_DRAFT = "casey@riverbend.example"
_GONE = "sam@copperline.example"
_SENT_THEN_GONE = "rowan@eastvale.example"


def _enrolled_setup(tmp_path, *, contacted: bool = True):
    import json

    root = tmp_path / "content"
    seq = root / "acme" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    for name, email in (
        ("live", _LIVE),
        ("draft", _DRAFT),
        ("gone", _GONE),
        ("sent-gone", _SENT_THEN_GONE),
    ):
        (seq / f"{name}.csv").write_text(f"email,company\n{email},Co\n", encoding="utf-8")
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "Live01"\ncsv = "live.csv"\nspec = "s.md"\n'
        '[[sequence]]\nid = "DRAFT-cell-a"\ncsv = "draft.csv"\nspec = "s.md"\n'
        '[[sequence]]\nid = "Gone01"\ncsv = "gone.csv"\nspec = "s.md"\n'
        '[[sequence]]\nid = "Sent01"\ncsv = "sent-gone.csv"\nspec = "s.md"\n',
        encoding="utf-8",
    )
    (root / "acme" / "history.jsonl").write_text(
        json.dumps({"event": "sequence_deleted", "sequence_id": "Gone01"})
        + "\n"
        + json.dumps({"event": "sequence_cleanup", "sequences_deleted": [{"id": "Sent01"}]})
        + "\n",
        encoding="utf-8",
    )
    if contacted:
        (seq / "contacted-2026-10-06.csv").write_text(
            f"email,name\n{_SENT_THEN_GONE},Rowan Vale\n", encoding="utf-8"
        )
    return root


def _filter(root, emails):
    from gtm_core.account_relation import Regulators, RelationIndex
    from gtm_core.static_pipeline import filter_static_audience

    rows = [{"email": e, "company": f"Co {i}", "company_domain": ""} for i, e in enumerate(emails)]
    return filter_static_audience(
        rows,
        "acme",
        content_root=root,
        profiles_root=root.parent / "profiles",
        index=RelationIndex(Regulators()),
    )


def test_draft_and_deleted_sequence_lists_do_not_count_as_enrolled(tmp_path):
    root = _enrolled_setup(tmp_path)
    res = _filter(root, [_LIVE, _DRAFT, _GONE])
    why = {r["email"]: reason for r, reason in res.excluded}
    assert why == {_LIVE: "already-enrolled"}
    assert {r["email"] for r in res.kept} == {_DRAFT, _GONE}


def test_a_deleted_sequence_that_sent_still_protects_its_recipients(tmp_path):
    root = _enrolled_setup(tmp_path)
    res = _filter(root, [_SENT_THEN_GONE])
    assert [reason for _, reason in res.excluded] == ["already-contacted"]
    assert res.kept == []


def test_a_sent_status_row_protects_its_recipient(tmp_path):
    root = _enrolled_setup(tmp_path, contacted=False)
    seq = root / "acme" / "prospects" / "sequences"
    (seq / "manual-sends.csv").write_text(f"email,status\n{_GONE},SENT\n", encoding="utf-8")
    res = _filter(root, [_GONE])
    assert [reason for _, reason in res.excluded] == ["already-contacted"]


def test_static_already_enrolled_set_matches_the_lane_router(tmp_path, monkeypatch):
    from gtm_core import lanes

    root = _enrolled_setup(tmp_path)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    ctx = lanes.load_context("acme")
    res = _filter(root, [_LIVE, _DRAFT, _GONE, _SENT_THEN_GONE])
    static_enrolled = {r["email"] for r, why in res.excluded if why == "already-enrolled"}
    assert static_enrolled == set(ctx.enrolled) == {_LIVE}
    static_contacted = {r["email"] for r, why in res.excluded if why == "already-contacted"}
    assert static_contacted == ctx.prior_emails == {_SENT_THEN_GONE}


# --- account status: opted-out and in-conversation companies ----------------------------------
# A static list is never enrolled with --require-verdict, so the enrollment gate's account-status
# check (opt-out, disqualified, replied, meeting …) never ran on it. Static mode now applies the
# gate's own rule per row — no second copy — so a colleague at a company that opted out or is
# already talking to us is excluded before the list exists.


def _ledger_setup(tmp_path, items_json: str):
    from gtm_core.prospects_state import latest_path

    root = tmp_path / "content"
    path = latest_path("acme", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(items_json, encoding="utf-8")
    return root


def _accounts(*items) -> str:
    import json

    return json.dumps({"kind": "prospects", "items": list(items)})


_OPTED_OUT = {"company": "Lantern Labs", "domain": "lantern.example", "status": "opt-out"}
_TALKING = {"company": "Riverbend Inc", "domain": "riverbend.example", "status": "replied"}
_OPEN = {"company": "Copperline Co", "domain": "copperline.example", "status": "new"}


def test_a_colleague_at_an_opted_out_or_in_conversation_company_is_excluded(tmp_path):
    root = _ledger_setup(tmp_path, _accounts(_OPTED_OUT, _TALKING, _OPEN))
    res = _filter(
        root, ["blair.ito@lantern.example", "casey@riverbend.example", "sam@copperline.example"]
    )
    why = {r["email"]: reason for r, reason in res.excluded}
    assert why == {
        "blair.ito@lantern.example": "account-opt-out",
        "casey@riverbend.example": "account-replied",
    }
    assert [r["email"] for r in res.kept] == ["sam@copperline.example"]


def test_static_account_exclusions_match_the_enrollment_gate(tmp_path):
    from gtm_core.enrollment_gate import check_account_status

    root = _ledger_setup(tmp_path, _accounts(_OPTED_OUT, _TALKING, _OPEN))
    emails = ["blair.ito@lantern.example", "casey@riverbend.example", "sam@copperline.example"]
    res = _filter(root, emails)
    static_blocked = {r["email"] for r, why in res.excluded if why.startswith("account-")}
    gate_blocked = {
        e for e in emails if check_account_status([{"email": e, "company": ""}], "acme", root)
    }
    assert (
        static_blocked
        == gate_blocked
        == {
            "blair.ito@lantern.example",
            "casey@riverbend.example",
        }
    )


def test_an_unreadable_account_ledger_stops_the_static_filter(tmp_path):
    import pytest

    from gtm_core.static_pipeline import AccountLedgerError

    root = _ledger_setup(tmp_path, '{"kind": "prospects", "items": [')
    with pytest.raises(AccountLedgerError, match="latest.json"):
        _filter(root, ["sam@copperline.example"])
