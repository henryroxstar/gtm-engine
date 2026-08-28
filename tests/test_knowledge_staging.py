"""Tests for the Phase 3a refresh core: gtm_core.knowledge_refresh (due selection) +
gtm_core.knowledge_staging (stage → diff → promote), plus knowledge_meta.upsert_frontmatter.

All deterministic, files-only — the safety-critical property under test is that promote is the only
write into profiles/ and that it re-stamps refreshed: to today.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from gtm_core import knowledge_meta as km
from gtm_core import knowledge_refresh as kr
from gtm_core import knowledge_staging as ks

TODAY = date(2026, 6, 1)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fm(refreshed: str, review: str, body="body\n") -> str:
    return f"---\nsource: manual\nrefreshed: {refreshed}\nreview: {review}\n---\n# T\n{body}"


def _profile(tmp_path, name="acme"):
    prof = tmp_path / "profiles" / name
    _write(prof / "PROFILE.md", "name: Acme\n")
    return tmp_path / "profiles", tmp_path / "content"


# --- upsert_frontmatter -------------------------------------------------------


def test_upsert_creates_and_merges():
    created = km.upsert_frontmatter("# No fm\ntext\n", {"source": "x", "refreshed": "2026-01-01"})
    assert created.startswith("---\nsource: x\nrefreshed: 2026-01-01\n---\n")
    assert created.endswith("# No fm\ntext\n")

    merged = km.upsert_frontmatter(_fm("2026-01-01", "90d"), {"refreshed": "2026-06-01"})
    meta, _ = km.parse_frontmatter(merged)
    assert meta["refreshed"] == "2026-06-01"  # updated
    assert meta["review"] == "90d"  # preserved
    assert meta["source"] == "manual"  # preserved


# --- due selection ------------------------------------------------------------


def test_due_topics_selects_overdue_and_due_soon(tmp_path):
    profiles_root, _ = _profile(tmp_path)
    kdir = profiles_root / "acme" / "knowledge"
    _write(kdir / "fresh.md", _fm((TODAY - timedelta(days=10)).isoformat(), "90d"))
    _write(kdir / "overdue.md", _fm((TODAY - timedelta(days=100)).isoformat(), "90d"))
    _write(kdir / "soon.md", _fm((TODAY - timedelta(days=83)).isoformat(), "90d"))
    _write(kdir / "ever.md", _fm("2000-01-01", "evergreen"))
    _write(kdir / "bare.md", "# no frontmatter\n")

    due = {m.relpath for m, _ in kr.due_topics(profiles_root, "acme", TODAY)}
    assert due == {"overdue.md", "soon.md"}

    with_unknown = {
        m.relpath for m, _ in kr.due_topics(profiles_root, "acme", TODAY, include_unknown=True)
    }
    assert with_unknown == {"overdue.md", "soon.md", "bare.md"}


# --- stage → diff → promote ---------------------------------------------------


def test_stage_list_diff_promote_roundtrip(tmp_path):
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "company")
    _write(live, _fm("2026-01-01", "90d", body="old facts\n"))

    # stage a refreshed candidate
    candidate = _fm("2026-01-01", "90d", body="NEW facts\n")
    ks.stage(content_root, "acme", "company", candidate)
    assert ks.list_staged(content_root, "acme") == ["company"]

    d = ks.diff(profiles_root, content_root, "acme", "company")
    assert "NEW facts" in d and "old facts" in d

    # promote — the operator gate: writes profiles/, re-stamps refreshed, clears staging
    target = ks.promote(profiles_root, content_root, "acme", "company", today=TODAY)
    assert target == live
    meta = km.read_meta(live, profiles_root / "acme" / "knowledge")
    assert meta.errors == ()
    assert meta.refreshed == TODAY  # re-stamped to promotion date
    assert "NEW facts" in live.read_text()
    assert ks.list_staged(content_root, "acme") == []  # staging cleared


def test_promote_seeds_missing_metadata(tmp_path):
    profiles_root, content_root = _profile(tmp_path)
    ks.stage(content_root, "acme", "company", "# Company\nfacts, no frontmatter\n")
    ks.promote(profiles_root, content_root, "acme", "company", today=TODAY, source="https://x")
    meta = km.read_meta(
        ks.live_path(profiles_root, "acme", "company"), profiles_root / "acme" / "knowledge"
    )
    assert meta.errors == ()
    assert meta.source == "https://x"
    assert meta.review == "90d"  # default_review('company')
    assert meta.refreshed == TODAY


def test_promote_without_candidate_raises(tmp_path):
    profiles_root, content_root = _profile(tmp_path)
    with pytest.raises(FileNotFoundError):
        ks.promote(profiles_root, content_root, "acme", "company", today=TODAY)


def test_topic_path_rejects_traversal():
    for bad in ("../escape", "/etc/passwd", "a/../../b"):
        with pytest.raises(ValueError):
            ks._safe_topic_relpath(bad)
    # a legitimate subdir topic is allowed
    assert ks._safe_topic_relpath("guidance/01-nist").as_posix() == "guidance/01-nist.md"


# --- Phase 3b: the refresh pack wires to a registered, ungated, side-effect-free skill ---


def test_knowledge_refresh_pack_wiring():
    from pathlib import Path

    from gtm_core.packs.loader import load_pack_graph
    from gtm_core.skills.registry import all_skills

    repo = Path(__file__).resolve().parents[1]
    graph = load_pack_graph(
        repo / "packs" / "knowledge-refresh" / "graphs" / "knowledge-refresh.toml"
    )
    node = graph.nodes[0]
    assert [n.id for n in graph.nodes] == ["refresh"]
    assert node.skill == "knowledge-refresh"
    # staging-only: no gate, no external effect (promotion is an out-of-band operator command)
    assert node.gate is False and node.external_effect is None
    assert node.model_role == "brain_plan"  # re-condensing company knowledge stays on Claude
    assert "knowledge-refresh" in {s.name for s in all_skills()}
