"""An enrollment gate that cannot check must refuse — "could not read it" is not "no objection".

PSK-030 (2026-09-21): `check_account_status` wrapped its ledger read in
`except Exception: return None`, so a truncated `latest.json` let a do-not-contact account
through to a third-party sequencer. Each refusal here ships with its positive control (§R18).
Fictional fixtures only (§R9).
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core import enrollment_gate
from gtm_core.prospect_paths import evals_dir
from gtm_core.prospects_state import latest_path

PROFILE = "qa-sandbox"
ROWS = [
    {
        "email": "rowan.pike@contosofreight.example",
        "company": "Contoso Freight",
        "company_domain": "contosofreight.example",
    }
]


def _write_ledger(root: Path, text: str) -> None:
    path = latest_path(PROFILE, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ledger(items: list) -> str:
    return json.dumps({"kind": "prospects", "items": items})


def test_a_readable_ledger_with_no_blocked_account_raises_no_objection(tmp_path):
    _write_ledger(
        tmp_path,
        _ledger(
            [{"company": "Contoso Freight", "domain": "contosofreight.example", "status": "new"}]
        ),
    )
    assert enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path) is None


def test_a_do_not_contact_account_is_refused(tmp_path):
    _write_ledger(
        tmp_path,
        _ledger(
            [
                {
                    "company": "Contoso Freight",
                    "domain": "contosofreight.example",
                    "status": "do-not-contact",
                }
            ]
        ),
    )
    refusal = enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path)
    assert refusal and "do-not-contact: 1" in refusal


def test_an_absent_ledger_means_no_exclusions(tmp_path):
    """The ledger's own contract (`prospects_state.load_latest`): absent is an empty ledger,
    present-but-unreadable is an error. The gate follows it rather than inventing a third rule."""
    assert enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path) is None


def test_a_truncated_ledger_is_refused_with_a_reason(tmp_path):
    _write_ledger(tmp_path, '{"items": [{"company": "Contoso Freight", "status": "do-not-con')
    refusal = enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path)
    assert refusal and refusal.startswith("REFUSED: account ledger unreadable")
    assert "do-not-contact" in refusal


def test_a_wrong_shaped_ledger_is_refused(tmp_path):
    for text in ("[]", '{"items": {"a": 1}}', _ledger(["not-an-object"])):
        _write_ledger(tmp_path, text)
        refusal = enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path)
        assert refusal and "account ledger unreadable" in refusal, text


def test_a_ledger_path_that_is_not_a_file_is_refused(tmp_path):
    latest_path(PROFILE, tmp_path).mkdir(parents=True)
    refusal = enrollment_gate.check_account_status(ROWS, PROFILE, tmp_path)
    assert refusal and "account ledger unreadable" in refusal


def _write_state(root: Path, text: str) -> None:
    path = evals_dir(PROFILE, root) / "lanes-state.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_state_line_that_is_not_an_object_refuses_the_lane_check(tmp_path):
    good = json.dumps({"email": ROWS[0]["email"], "lane": "generic"})
    _write_state(tmp_path, good + "\n")
    rows = [dict(ROWS[0])]
    refusal, lane = enrollment_gate.check_enrollment_lanes(
        rows, PROFILE, "", ["email"], content_root=tmp_path
    )
    assert (refusal, lane) == (None, "generic")  # positive control

    _write_state(tmp_path, good + "\n[1, 2]\n")
    refusal, _lane = enrollment_gate.check_enrollment_lanes(
        [dict(ROWS[0])], PROFILE, "", ["email"], content_root=tmp_path
    )
    assert refusal and "not a JSON object" in refusal
