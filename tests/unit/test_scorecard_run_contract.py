"""§3 of the test plan: the claims it says to ASSERT rather than assume.

"This feature adds no pack node and no external_effect. Assert that, don't assume it." — so
these are the cheap structural facts that would otherwise be believed because they sound true.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gtm_core.ledger_verify import verify_chain
from gtm_core.ledgers import Ledgers

ROOT = Path(__file__).resolve().parents[2]
#: Whichever tenant has a card, DISCOVERED rather than named (§R9) — this file ships, and any
#: committed card exercises the CLI equally well. A literal slug would only add a real company's
#: name to the carve.
_CARDS = sorted(ROOT.glob("profiles/*/knowledge/scorecard.toml"))
CARD = _CARDS[0] if _CARDS else ROOT / "profiles" / "_absent" / "knowledge" / "scorecard.toml"
PROFILE = CARD.parent.parent.name

pytestmark = pytest.mark.skipif(
    not CARD.is_file(), reason="needs a committed tenant card to exercise the CLI end to end"
)

ROW = {
    "id": "aldermoor-labs",
    "company": "Aldermoor Labs",
    "icp_label": "ICP3",
    "in_target_market": True,
    "research_on_file": True,
    "agent_evidence": "present",
    "intent_reading": "probed_no_surge",
    "dated_why_now": True,
}


def _run(items: list[dict], tmp_path: Path) -> dict:
    path = tmp_path / "items.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.scorecard",
            "score",
            "--profile",
            PROFILE,
            "--items",
            str(path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# --- Pack integrity: asserted, not assumed --------------------------------------------------


def test_this_feature_adds_no_pack_node_and_no_external_effect() -> None:
    """A gate with an `external_effect` pairs with a Python-only dispatcher and a tool-level
    denial. The scorecard has neither because it needs neither — it never reaches egress."""
    hits = [
        p for p in (ROOT / "packs").rglob("*.toml") if "scorecard" in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"packs reference the scorecard: {hits}"


# --- Budget & loop guards -------------------------------------------------------------------


def test_scoring_makes_no_metered_call_and_writes_no_cost_row(tmp_path, monkeypatch) -> None:
    """Scoring is free. Pinned by pointing the content root at an empty tmp dir: if anything
    logged a cost, a costs.jsonl would appear under it."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _run([ROW], tmp_path)
    assert not list(tmp_path.rglob("costs.jsonl"))


def test_a_large_input_does_not_fan_out(tmp_path) -> None:
    """Row-bounded by construction: N rows in, N results out, one process, no subagents."""
    result = _run([dict(ROW, id=f"fixture-{n}") for n in range(2000)], tmp_path)
    assert result["summary"]["input_count"] == 2000
    assert result["summary"]["scored"] + result["summary"]["categorised"] == 2000


# --- Telemetry: the scorecard_run event -----------------------------------------------------


def test_the_run_event_carries_the_rubric_version(tmp_path) -> None:
    """Without it, `outcomes-sync` cannot attribute a reply-rate change to a rubric change —
    which is the entire loop this feature exists to enable."""
    event = _run([ROW, {"company": "Pallister Freight"}], tmp_path)["event"]
    assert event["event"] == "scorecard_run"
    assert event["rubric_version"] and event["rubric_source"]
    assert event["scored"] == 1 and event["categorised"] == 1
    assert event["input_count"] == 2


def test_appending_the_run_event_leaves_the_ledger_chain_intact(tmp_path) -> None:
    """The prev_sha256 chain must survive a new event type. It is a plain append with no schema,
    so this is about the CHAIN, not the payload."""
    event = _run([ROW], tmp_path)["event"]
    ledger = Ledgers(SimpleNamespace(content_root=tmp_path), "fixtureco")
    ledger.append_history({"event": "prospect_run", "total": 1})
    ledger.append_history(event)
    ledger.append_history({"event": "prospect_run", "total": 2})

    result = verify_chain(tmp_path / "fixtureco" / "history.jsonl")
    assert result.ok, result.breaks
    assert result.total_lines == 3

    rows = [
        json.loads(line)
        for line in (tmp_path / "fixtureco" / "history.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert rows[1]["event"] == "scorecard_run"
    assert rows[1]["rubric_version"]


# --- Derivation legibility (§5 UAT, the machine-checkable half) ------------------------------


def test_the_derivation_is_a_sentence_not_a_json_blob(tmp_path) -> None:
    """Phone-readable is the bar. A derivation an operator cannot read is a score they cannot
    challenge, and a score nobody can challenge is one nobody corrects."""
    item = _run([ROW], tmp_path)["items"][0]
    parts = item["score_derivation"]
    assert parts and all(isinstance(p, str) for p in parts)
    assert all("{" not in p and "[" not in p for p in parts)
    for part in parts:
        assert " of " in part, f"no axis denominator in {part!r}"
        assert len(part) < 120, f"too long to read on a phone: {part!r}"


def test_a_categorised_item_says_which_input_it_is_waiting_on(tmp_path) -> None:
    """§5B: "can the operator state the next action without asking?" """
    item = _run([{"company": "Pallister Freight"}], tmp_path)["items"][0]
    assert item["score_category"] == "Unscored — no ICP label"
    assert item["score_missing_input"] == "icp_label"
    assert "score" not in item


# --- The run-header line survives Telegram's HTML escaping (§3D, machine half) --------------


def test_the_summary_line_survives_html_escaping(tmp_path) -> None:
    """The run header is prose the brain pastes; if it lands in a Telegram HTML message it goes
    through `html.escape` (`agent/gate_notify.py`). That escapes `& < > "` and nothing else, so
    the two characters this line leans on — the `·` separator (U+00B7) and the em-dash in a
    category (U+2014) — pass through unchanged. Asserted rather than assumed, because a summary
    that renders as `&middot;` reads as corruption and an operator stops trusting the line.
    """
    import html

    line = (
        "scored 445 · categorised 558 · rubric "
        "knowledge/icp-personas.md#gates-scoring-rubric-thresholds@2026-09-22 "
        "· tiers A 45/B 155/C 208/D 37"
    )
    assert html.escape(line) == line, "the summary line is altered by HTML escaping"

    category = "Unscored — no buyer-intent reading"
    assert html.escape(category) == category
    assert "·" in html.escape(line) and "—" in html.escape(category)


def test_the_escape_check_is_not_vacuous() -> None:
    """Negative control: `html.escape` must actually be capable of changing a string, or the
    assertion above holds for any input at all."""
    import html

    assert html.escape("tiers A<45 & B>155") != "tiers A<45 & B>155"


def test_a_real_run_header_line_is_escape_safe(tmp_path) -> None:
    """The same property on the line the CLI actually emits, not a hand-typed copy of it."""
    import html
    import subprocess

    path = tmp_path / "items.json"
    path.write_text(json.dumps([ROW, {"company": "Pallister Freight"}]), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.scorecard",
            "score",
            "--profile",
            PROFILE,
            "--items",
            str(path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    header = next(ln for ln in proc.stderr.splitlines() if ln.startswith("scored "))
    assert html.escape(header) == header, f"run header altered by escaping: {header!r}"
    assert " · " in header
