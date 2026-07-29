"""Tests for the Phase 4 closed loop: gtm_core.outcomes (capture) + gtm_core.gtm_distill (learnings).

Deterministic, files-only. Proves the loop: append outcomes → summarize by tag → distill a learnings
note with a Promote? section → optionally stage a learnings candidate for the Phase-3 promote flow.
"""

from __future__ import annotations

from datetime import date

from gtm_core import gtm_distill as gd
from gtm_core import knowledge_staging as ks
from gtm_core import outcomes as oc

# aggregate rows (value = count): tag A outperforms, tag B underperforms the 20% baseline
ROWS = [
    {"channel": "email", "outcome": "sent", "value": 10, "tags": ["myth-bust"]},
    {"channel": "email", "outcome": "reply", "value": 3, "tags": ["myth-bust"]},
    {"channel": "email", "outcome": "sent", "value": 10, "tags": ["feature-list"]},
    {"channel": "email", "outcome": "reply", "value": 1, "tags": ["feature-list"]},
]


def _seed(content_root, profile="acme", rows=ROWS):
    for r in rows:
        oc.append_outcome(content_root, profile, r, now="2026-06-15T00:00:00Z")


# --- capture ------------------------------------------------------------------


def test_append_and_read_roundtrip(tmp_path):
    oc.append_outcome(tmp_path, "acme", {"channel": "email", "outcome": "reply"})
    oc.append_outcome(
        tmp_path, "acme", {"channel": "email", "outcome": "sent"}, now="2026-01-02T00:00:00Z"
    )
    rows = oc.read_outcomes(tmp_path, "acme")
    assert len(rows) == 2
    assert all("ts" in r for r in rows)  # auto-stamped
    assert oc.read_outcomes(tmp_path, "acme", since_month="2026-01") == [rows[1]]


def test_read_skips_malformed_lines(tmp_path):
    p = oc.outcomes_path(tmp_path, "acme")
    p.parent.mkdir(parents=True)
    p.write_text('{"channel":"email","outcome":"reply"}\nnot json\n\n', encoding="utf-8")
    assert len(oc.read_outcomes(tmp_path, "acme")) == 1


# --- summarize ----------------------------------------------------------------


def test_summarize_rates_by_tag(tmp_path):
    s = oc.summarize(ROWS)
    assert s["totals"]["sent"] == 20 and s["totals"]["replies"] == 4
    assert s["totals"]["reply_rate"] == 0.2
    assert s["by_tag"]["myth-bust"]["reply_rate"] == 0.3
    assert s["by_tag"]["feature-list"]["reply_rate"] == 0.1


def test_promote_candidates_flags_out_and_under_performers():
    cands = {c["tag"]: c["direction"] for c in gd.promote_candidates(oc.summarize(ROWS))}
    assert cands == {"myth-bust": "outperforms", "feature-list": "underperforms"}


def test_promote_candidates_need_enough_observations():
    thin = [
        {"channel": "email", "outcome": "sent", "value": 2, "tags": ["x"]},
        {"channel": "email", "outcome": "reply", "value": 2, "tags": ["x"]},
    ]
    assert gd.promote_candidates(oc.summarize(thin)) == []  # below DEFAULT_MIN_SENT


# --- distill ------------------------------------------------------------------


def test_distill_writes_note_with_promote_section(tmp_path):
    _seed(tmp_path)
    path = gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30))
    assert path == tmp_path / "acme" / "learnings" / "2026-06.md"
    text = path.read_text()
    assert "## Promote?" in text
    assert "myth-bust" in text and "outperforms" in text
    assert "feature-list" in text and "underperforms" in text


def test_distill_stage_option_stages_learnings_candidate(tmp_path):
    _seed(tmp_path)
    gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30), stage=True)
    assert "learnings" in ks.list_staged(tmp_path, "acme")


def test_distill_empty_is_quiet(tmp_path):
    (tmp_path / "acme").mkdir(parents=True)
    path = gd.distill(tmp_path, "acme", period="2026-06", today=date(2026, 6, 30))
    assert "No signal yet" in path.read_text()


def test_outcomes_loop_pack_wiring():
    from pathlib import Path

    from gtm_core.packs.loader import load_pack_graph
    from gtm_core.skills.registry import all_skills

    repo = Path(__file__).resolve().parents[1]
    graph = load_pack_graph(repo / "packs" / "outcomes-loop" / "graphs" / "outcomes-loop.toml")
    node = graph.nodes[0]
    assert node.skill == "outcomes-sync"
    # read-only loop: no gate, no external effect (it never sends/publishes)
    assert node.gate is False and node.external_effect is None
    assert node.model_role == "brain_plan"  # outcome rows carry account PII → stays on Claude
    assert "outcomes-sync" in {s.name for s in all_skills()}
