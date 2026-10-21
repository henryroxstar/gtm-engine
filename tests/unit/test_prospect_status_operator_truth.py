"""What the operator is TOLD must agree with itself, and a safety gate must not pass on "could not check".

The status block is pasted verbatim into every prospect run (Steps 1, 12, 13), so a number it prints
is a claim made to a person. On 2026-09-21 the live block read "ACTION REQUIRED: 705 accounts
require routing decisions" above "Waiting on you 102", and `[Ready] 14` above "Ready to send 275".
`tests/test_prospects_status.py` could not see it: its "ready" fixture uses a status outside the
ledger vocabulary. Fictional data. The three defects first pinned here as strict xfails
(PSK-028, PSK-029, PSK-030 from the 2026-09-21 prospect skill E2E audit) are fixed, so these are
ordinary regression tests now.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from gtm_core import enrollment_gate, prospect_status
from gtm_core.prospects_state import latest_path

REPO = Path(__file__).resolve().parents[2]
PROFILE = "qa-sandbox"


def test_the_unattended_generic_trigger_is_known_to_the_status_model() -> None:
    assert prospect_status.status_of("hold", "unattended-generic") == "waiting_on_you"


def test_every_trigger_the_unattended_router_writes_is_known_to_the_status_model() -> None:
    """An unattended route holds generic AND repair candidates; the pack runs unattended, so an
    unmapped one ended the mandatory status step in a traceback whenever a repair candidate
    existed. (The triggers are now one shared constant — `lanes.model.UNATTENDED_TRIGGERS`.)"""
    for lane in ("generic", "repair"):
        prospect_status.status_of("hold", f"unattended-{lane}")


def test_the_account_status_gate_refuses_when_the_ledger_is_unreadable(tmp_path: Path) -> None:
    path = latest_path(PROFILE, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"items": [{"company": "Contoso Freight", "status": "do-not-con', encoding="utf-8"
    )
    rows = [{"email": "rowan.pike@contosofreight.example", "company": "Contoso Freight"}]
    assert enrollment_gate.check_account_status(rows, PROFILE, tmp_path) is not None


def test_no_action_required_banner_when_nobody_is_waiting_on_the_operator(tmp_path: Path) -> None:
    """Two researched, judge-approved accounts route to `personalised`: zero decisions are pending."""
    (tmp_path / "content").mkdir()
    shutil.copytree(REPO / "profiles" / "_template", tmp_path / "profiles" / PROFILE)
    prof = tmp_path / "profiles" / PROFILE / "PROFILE.md"
    prof.write_text(
        "\n".join(
            "target_markets:  [Singapore]" if ln.startswith("target_markets:") else ln
            for ln in prof.read_text(encoding="utf-8").splitlines()
        ),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        GTM_CONTENT_ROOT=str(tmp_path / "content"),
        GTM_PROFILES_ROOT=str(tmp_path / "profiles"),
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", *args],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    items = tmp_path / "items.json"
    researched = {
        "segment": "startup", "market": "Singapore", "country": "Singapore", "score": 8, "tier": "A",
        "contact_title": "Founder", "email_status": "verified", "verdict": "send",
        "signal_observed": "2026-09-10", "signal_agent_kind": "ai", "category_relation": "prospect",
        # A row carrying a SCORE must name the rubric that produced it, or `finalize` refuses
        # before writing anything (`prospects_import.require_rubric_provenance`). Supplied here
        # rather than switched off, so this fixture keeps the shape a real scored row has.
        "rubric_source": "knowledge/fixture.md#rubric", "rubric_version": "2026-01-01",
    }  # fmt: skip
    rows = [
        ("Litware Pay", "litwarepay.example", "Oma Reyes", "oma.reyes@litwarepay.example"),
        ("Lucerne Media", "lucernemedia.example", "Ira Bloom", "ira.bloom@lucernemedia.example"),
    ]
    items.write_text(
        json.dumps(
            [
                {
                    **researched,
                    "company": co,
                    "domain": dom,
                    "contact_name": name,
                    "contact_email": email,
                    "why_now": f"{co} opened its agent platform to partner organisations.",
                    "signal_evidence": f"{co} opened its agent platform to partner organisations.",
                    "signal_source_url": f"https://{dom}/news/partner-agents",
                    "signal_subject": co,
                }  # fmt: skip
                for co, dom, name, email in rows
            ]
        ),
        encoding="utf-8",
    )
    prospects = tmp_path / "content" / PROFILE / "prospects"
    assert run("gtm_core.prospects_import", "finalize", "--profile", PROFILE, "--items", str(items),
               "--source-run", "qa-run-1", "--standard").returncode == 0  # fmt: skip
    assert (
        run("gtm_core.prospects_consolidate", "consolidate", "--profile", PROFILE).returncode == 0
    )
    records = prospects / "evals" / "judge" / "none.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(
        "\n".join(
            json.dumps(
                {
                    "email": email,
                    "verdict": "send",
                    "score": 4,
                    "touch": 1,
                    "body_hash": f"h-{email}",
                    "repair_attempt": 0,
                    "defect_class": "",
                    "calibrated": False,
                }
            )  # fmt: skip
            for _co, _dom, _name, email in rows
        ),
        encoding="utf-8",
    )
    assert run("gtm_core.lanes", "route", "--profile", PROFILE, "--csv", str(prospects / "sequences" / "ready-to-load.csv"),
               "--records", str(records)).returncode == 0  # fmt: skip

    out = run("gtm_core.prospects", "status", "--profile", PROFILE).stdout
    waiting = re.search(r"Waiting on you\s+(\d+)", out)
    assert waiting and waiting.group(1) == "0", out
    assert "ACTION REQUIRED" not in out
