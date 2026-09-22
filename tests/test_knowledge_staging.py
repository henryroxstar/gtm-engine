"""Tests for the Phase 3a refresh core: gtm_core.knowledge_refresh (due selection) +
gtm_core.knowledge_staging (stage → diff → promote), plus knowledge_meta.upsert_frontmatter.

All deterministic, files-only — the safety-critical property under test is that promote is the only
write into profiles/ and that it re-stamps refreshed: to today.
"""

from __future__ import annotations

import tomllib
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


def test_live_path_routes_a_product_topic_out_of_knowledge(tmp_path):
    """A ``products/``-prefixed topic promotes onto profiles/<p>/products/, not knowledge/."""
    root = tmp_path / "profiles"
    assert ks.live_path(root, "acme", "products/vta/PRODUCT.md") == (
        root / "acme" / "products" / "vta" / "PRODUCT.md"
    )
    assert ks.live_path(root, "acme", "industry/cx-ai.md") == (
        root / "acme" / "knowledge" / "industry" / "cx-ai.md"
    )


def test_live_path_guards_traversal_through_the_products_prefix(tmp_path):
    """The prefix must not become an escape hatch — the guard runs on the whole topic."""
    with pytest.raises(ValueError):
        ks.live_path(tmp_path, "acme", "products/../../etc/passwd")


def test_topic_path_rejects_traversal():
    for bad in ("../escape", "/etc/passwd", "a/../../b"):
        with pytest.raises(ValueError):
            ks._safe_topic_relpath(bad)
    # a legitimate subdir topic is allowed
    assert ks._safe_topic_relpath("guidance/01-nist").as_posix() == "guidance/01-nist.md"


# --- IC7: .toml topics (the machine-readable targeting files) -----------------
#
# The rubric, the hook bank and the persona vocabulary are TOML, not prose. Before IC7 the suffix
# logic appended .md to anything lacking it, so `icp-scoring.toml` staged as `icp-scoring.toml.md`
# and those three files had NO staged-review path — they had to be hand-edited in profiles/, which
# this module's docstring calls read-only at runtime.

_RUBRIC = "[weights]\nseniority = 3\n\n[thresholds]\ntier_a = 8\n"


def test_a_toml_topic_keeps_its_extension():
    assert ks._safe_topic_relpath("icp-scoring.toml").as_posix() == "icp-scoring.toml"
    assert ks._safe_topic_relpath("role-vocabulary.toml").as_posix() == "role-vocabulary.toml"


def test_a_bare_topic_still_defaults_to_md():
    """NEGATIVE CONTROL: today's behaviour must be byte-identical for every .md caller."""
    assert ks._safe_topic_relpath("icp-personas").as_posix() == "icp-personas.md"
    assert ks._safe_topic_relpath("guidance/01-nist").as_posix() == "guidance/01-nist.md"


def test_an_unknown_extension_is_refused_never_coerced():
    with pytest.raises(ValueError, match="extension"):
        ks._safe_topic_relpath("promote_candidates.json")


def test_a_toml_round_trips_stage_diff_promote(tmp_path):
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "icp-scoring.toml")
    assert live == profiles_root / "acme" / "knowledge" / "icp-scoring.toml"
    _write(live, _RUBRIC)

    candidate = _RUBRIC.replace("seniority = 3", "seniority = 5")
    staged = ks.stage(content_root, "acme", "icp-scoring.toml", candidate)
    assert staged.name == "icp-scoring.toml"  # not icp-scoring.toml.md
    assert ks.list_staged(content_root, "acme") == ["icp-scoring.toml"]

    d = ks.diff(profiles_root, content_root, "acme", "icp-scoring.toml")
    assert "seniority = 5" in d and "seniority = 3" in d

    target = ks.promote(profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY)
    assert target == live
    assert tomllib.loads(live.read_text())["weights"]["seniority"] == 5
    assert ks.list_staged(content_root, "acme") == []  # staging cleared


def test_a_malformed_staged_toml_is_refused_and_the_live_file_is_untouched(tmp_path):
    """The load-bearing test: promote is the ONLY writer of profiles/, so a candidate that does
    not parse must never replace a rubric that does — load_rubric would raise and stop prospecting."""
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "icp-scoring.toml")
    _write(live, _RUBRIC)
    before = live.read_bytes()

    ks.stage(content_root, "acme", "icp-scoring.toml", "[weights\nseniority = ")
    with pytest.raises(ValueError, match="TOML"):
        ks.promote(profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY)

    assert live.read_bytes() == before  # the working rubric survived, byte for byte
    assert ks.list_staged(content_root, "acme") == ["icp-scoring.toml"]  # candidate kept for repair


def test_a_toml_that_parses_but_is_empty_does_not_bypass_the_gate(tmp_path):
    """A zero-byte or comment-only file is VALID TOML — parsing alone would silently blank it."""
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "icp-scoring.toml")
    _write(live, _RUBRIC)

    for blank in ("", "# everything got commented out\n"):
        assert tomllib.loads(blank) == {}  # the gate a bare parse would wave through
        ks.stage(content_root, "acme", "icp-scoring.toml", blank)
        with pytest.raises(ValueError, match="empty"):
            ks.promote(profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY)
        assert live.read_text() == _RUBRIC


def test_a_toml_promote_records_provenance_as_a_comment_header(tmp_path):
    """upsert_frontmatter is .md-only (YAML frontmatter would corrupt TOML), so a .toml promote
    stamps its provenance as a TOML comment instead — and the result must still parse."""
    profiles_root, content_root = _profile(tmp_path)
    ks.stage(content_root, "acme", "icp-scoring.toml", _RUBRIC)
    live = ks.promote(
        profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY, source="https://x"
    )

    text = live.read_text()
    first = text.splitlines()[0]
    assert first.startswith("#")
    assert TODAY.isoformat() in first and "https://x" in first
    assert "---" not in text  # never YAML frontmatter
    assert tomllib.loads(text)["weights"]["seniority"] == 3  # still parses, content intact

    # the stamp is an UPSERT, like refreshed: in frontmatter — it replaces, never accumulates
    ks.stage(content_root, "acme", "icp-scoring.toml", text)
    again = ks.promote(
        profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY
    ).read_text()
    assert len([ln for ln in again.splitlines() if ln.startswith("# refreshed:")]) == 1
    assert tomllib.loads(again)["weights"]["seniority"] == 3


# --- §R18 negative controls: each gate must be shown able to go red -----------


def test_dropping_toml_from_the_allowlist_breaks_the_round_trip(monkeypatch):
    """Proves the closed allowlist is what admits .toml — not an incidental code path."""
    monkeypatch.setattr(ks, "_ALLOWED_SUFFIXES", frozenset({".md"}))
    with pytest.raises(ValueError, match="extension"):
        ks._safe_topic_relpath("icp-scoring.toml")


def test_removing_the_parse_gate_lets_a_malformed_rubric_through(tmp_path, monkeypatch):
    """Without this, 'the gate holds' and 'there is no gate' look identical."""
    profiles_root, content_root = _profile(tmp_path)
    live = ks.live_path(profiles_root, "acme", "icp-scoring.toml")
    _write(live, _RUBRIC)
    broken = "[weights\nseniority = "

    monkeypatch.setattr(ks, "_require_parsable_toml", lambda text, topic: None)
    ks.stage(content_root, "acme", "icp-scoring.toml", broken)
    ks.promote(profiles_root, content_root, "acme", "icp-scoring.toml", today=TODAY)

    assert broken in live.read_text()  # the defect the gate exists to prevent
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads(live.read_text())


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
