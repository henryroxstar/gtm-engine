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
