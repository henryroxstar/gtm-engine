"""The operator-vocabulary lint, plus its negative control (§R18).

A lint that only ever reports "clean" is indistinguishable from one that matches
nothing, so this suite proves the scanner FIRES on real pipeline jargon as well as
staying quiet on `gtm_core.prospect_status`'s own translated text.
"""

from __future__ import annotations

import operator_vocabulary as ov
import pytest

from gtm_core.lanes.model import EXCLUDE_ORDER, HOLD_ORDER


def test_prospect_status_own_labels_and_next_step_are_clean():
    """PS7's own six-word vocabulary must be free of the jargon it exists to replace."""
    assert ov.findings() == []


def test_it_fires_on_lane_colon_generic():
    """The exact negative control the PRD names: `"lane: generic"` must fail the lint."""
    hits = ov.findings(text="the account sat in lane: generic for a week")
    assert any(h[2].lower() == "lane" for h in hits)


def test_it_does_not_false_positive_on_a_substring():
    """`landing`/`airplane` contain the letters `lane` but are not the word `lane`."""
    assert ov.findings(text="the landing page needs work") == []
    assert ov.findings(text="book the airplane tickets") == []


@pytest.mark.parametrize("trigger", sorted({*HOLD_ORDER, *EXCLUDE_ORDER}))
def test_every_hold_and_exclude_trigger_id_is_banned(trigger):
    hits = ov.findings(text=f"the trigger was {trigger} on this row")
    assert any(h[2].lower() == trigger.lower() for h in hits), trigger


def test_verdict_and_re_angle_and_master_cols_and_lane_reason_and_signal_clause_are_banned():
    for token in ("verdict", "re-angle", "MASTER_COLS", "lane_reason", "signal_clause"):
        hits = ov.findings(text=f"see the {token} for this account")
        assert any(h[2].lower() == token.lower() for h in hits), token


def test_gtm_core_is_banned():
    assert ov.findings(text="run gtm_core.prospects status") != []


def test_file_extensions_are_banned():
    hits = ov.findings(text="open lanes-state.jsonl or ready-to-load.csv")
    tokens = {h[2].lower() for h in hits}
    assert ".jsonl" in tokens
    assert ".csv" in tokens


def test_mcp_tool_names_are_banned():
    hits = ov.findings(text="call mcp__judge__score_emails on the row")
    assert any(h[2].lower().startswith("mcp__") for h in hits)


def test_identity_key_shapes_are_banned():
    hits = ov.findings(text="the account key is d:vertex.example for this row")
    assert any(h[3].find("d:vertex.example") != -1 or "d:" in h[2] for h in hits)
    hits2 = ov.findings(text="matched under a:acct-9f2 for this row")
    assert any("a:" in h[2] for h in hits2)


def test_identity_key_requires_no_space_after_colon():
    """`a: yes` is ordinary prose punctuation, not the identity-key shape `a:<value>`."""
    assert ov.findings(text="Q&a: yes that's right") == []


def test_glob_scan_over_a_root_with_no_markers_checks_the_whole_file(tmp_path):
    (tmp_path / "skill.md").write_text(
        "# Skill\n\nThis routes the row into the generic lane.\n", encoding="utf-8"
    )
    hits = ov.findings(root=tmp_path, globs=["*.md"])
    assert any(h[2].lower() == "lane" for h in hits)


def test_glob_scan_with_markers_checks_only_inside_them(tmp_path):
    (tmp_path / "skill.md").write_text(
        "# Skill\n\n"
        "Internal note: this uses gtm_core.lanes under the hood.\n\n"
        "<!-- operator -->\nYour account is waiting on you.\n<!-- /operator -->\n\n"
        "Another internal note about the lane router.\n",
        encoding="utf-8",
    )
    hits = ov.findings(root=tmp_path, globs=["*.md"])
    assert hits == [], f"unmarked prose leaked into the scan: {hits}"


def test_glob_scan_still_fires_on_a_banned_token_inside_the_markers(tmp_path):
    (tmp_path / "skill.md").write_text(
        "<!-- operator -->\nThis account sat in lane: generic.\n<!-- /operator -->\n",
        encoding="utf-8",
    )
    hits = ov.findings(root=tmp_path, globs=["*.md"])
    assert any(h[2].lower() == "lane" for h in hits)


def test_main_returns_nonzero_and_prints_on_a_dirty_glob(tmp_path, capsys):
    (tmp_path / "skill.md").write_text("routed to the generic lane\n", encoding="utf-8")
    rc = ov.main(["--root", str(tmp_path), "--glob", "*.md"])
    assert rc != 0
    out = capsys.readouterr().out
    assert "lane" in out.lower()


def test_unbalanced_markers_fail(tmp_path):
    (tmp_path / "broken.md").write_text(
        "<!-- operator -->\nSome text without close marker\n", encoding="utf-8"
    )
    hits = ov.findings(root=tmp_path, globs=["broken.md"])
    assert any("unbalanced" in h[2] or "unbalanced" in h[3] for h in hits)


def test_near_miss_markers_fail(tmp_path):
    (tmp_path / "typo.md").write_text(
        "<!-- operater -->\nSome text\n<!-- /operater -->\n", encoding="utf-8"
    )
    hits = ov.findings(root=tmp_path, globs=["typo.md"])
    assert any("near-miss" in h[3] for h in hits)


def test_skills_and_body_templates_are_clean():
    hits = ov.findings(globs=ov.DEFAULT_GLOBS)
    assert hits == []


def test_cli_block_on_fixture_is_clean():
    from gtm_core.prospect_status_cli import _format_report

    counts = {
        "ready": 10,
        "waiting_on_us": 20,
        "waiting_on_them": 30,
        "waiting_on_you": 40,
        "do_not_contact": 5,
    }
    report = _format_report(counts, needs_address_count=15)
    hits = ov.findings(text=report)
    assert hits == []


def test_dashboard_status_card_strings_are_clean():
    from gtm_core.email_campaign_dashboard.views_overview import (
        _contacts_block,
        _needs_address_block,
    )

    # Test with available data
    model_avail = {
        "campaigns": {"campaigns": []},
        "prospect_status": {
            "available": True,
            "counts": {
                "ready": 10,
                "waiting_on_us": 20,
                "waiting_on_them": 30,
                "waiting_on_you": 40,
                "do_not_contact": 5,
            },
            "total": 105,
            "unmapped": 0,
            "needs_address": 15,
        },
    }
    for block in (_contacts_block(model_avail), _needs_address_block(model_avail)):
        hits = ov.findings(text=block)
        assert hits == []

    # Test when unavailable
    model_unavail = {
        "campaigns": {"campaigns": []},
        "prospect_status": {"available": False},
    }
    for block in (_contacts_block(model_unavail), _needs_address_block(model_unavail)):
        hits = ov.findings(text=block)
        assert hits == []


def test_py_file_refusal_literals_are_scanned(tmp_path):
    py_code = """
from gtm_core.refusal_copy import Refusal

def stop_fn():
    r = Refusal(
        what="I stopped",
        why="evals/lanes-state.jsonl is missing",
        next_step="run lanes route",
    )
    return r.render()
"""
    (tmp_path / "stop.py").write_text(py_code, encoding="utf-8")
    hits = ov.findings(root=tmp_path, globs=["stop.py"])
    tokens = {h[2].lower() for h in hits}
    assert ".jsonl" in tokens
    assert "lanes" in tokens


def test_py_file_technical_is_exempt(tmp_path):
    py_code = """
from gtm_core.refusal_copy import Refusal

def stop_fn():
    r = Refusal(
        what="I stopped before sending",
        why="the list needs to be sorted first",
        next_step="say 'sort my list' and I will do it",
        technical="evals/lanes-state.jsonl is missing — run `lanes route`",
    )
    return r.render()
"""
    (tmp_path / "stop.py").write_text(py_code, encoding="utf-8")
    hits = ov.findings(root=tmp_path, globs=["stop.py"])
    assert hits == []
