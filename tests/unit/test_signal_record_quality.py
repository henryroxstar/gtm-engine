from gtm_core.signal_record import (
    RECORD_COLUMNS,
    SIGNAL_FIT_COLUMN,
    SIGNAL_VIRALITY_COLUMN,
    check_record,
)


def test_quality_columns_not_in_record_columns() -> None:
    assert SIGNAL_FIT_COLUMN not in RECORD_COLUMNS
    assert SIGNAL_VIRALITY_COLUMN not in RECORD_COLUMNS
    assert SIGNAL_FIT_COLUMN == "signal_fit"
    assert SIGNAL_VIRALITY_COLUMN == "signal_virality"


def test_check_record_valid_quality_values() -> None:
    row = {
        "verdict": "send",
        "category_relation": "prospect",
        "signal_fit": 3,
        "signal_virality": 2,
    }
    findings = check_record(row)
    assert not [f for f in findings if f.level == "block"]


def test_check_record_string_integer_quality_values() -> None:
    row = {
        "verdict": "send",
        "category_relation": "prospect",
        "signal_fit": "2",
        "signal_virality": "1",
    }
    findings = check_record(row)
    assert not [f for f in findings if f.level == "block"]


def test_check_record_invalid_signal_fit_range() -> None:
    for bad_val in (4, -1, "5"):
        row = {
            "verdict": "send",
            "category_relation": "prospect",
            "signal_fit": bad_val,
        }
        findings = check_record(row)
        blocks = [f for f in findings if f.level == "block"]
        assert any(f.rule == "signal-fit-invalid" and f.field == "signal_fit" for f in blocks)


def test_check_record_invalid_signal_virality_non_int() -> None:
    row = {
        "verdict": "send",
        "category_relation": "prospect",
        "signal_virality": "high",
    }
    findings = check_record(row)
    blocks = [f for f in findings if f.level == "block"]
    assert any(f.rule == "signal-virality-invalid" and f.field == "signal_virality" for f in blocks)


def test_check_record_missing_quality_columns_no_findings() -> None:
    row = {
        "verdict": "send",
        "category_relation": "prospect",
    }
    findings = check_record(row)
    assert not [f for f in findings if f.level == "block"]
