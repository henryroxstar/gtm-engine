"""Tests for gtm_core.prospect_paths — the one home for shared pipeline locations.

The defect these pin: the eval writeback appended exclusions to
``prospects/suppression.csv`` while every gate that enforces them reads
``prospects/sequences/.pool/suppression.csv``. Both files existed, both looked
right in isolation, and a disqualified person stayed sendable.
"""

from __future__ import annotations

import pytest

from gtm_core import prospect_paths as pp


def test_suppression_ledger_lives_beside_the_lists_it_protects(tmp_path):
    p = pp.suppression_ledger("acme", content_root=tmp_path)
    assert p == tmp_path / "acme" / "prospects" / "sequences" / ".pool" / "suppression.csv"


def test_the_ledger_is_not_directly_under_prospects(tmp_path):
    """The exact wrong spelling that shipped — pinned so it cannot come back."""
    wrong = pp.prospects_dir("acme", content_root=tmp_path) / "suppression.csv"
    assert pp.suppression_ledger("acme", content_root=tmp_path) != wrong


def test_legacy_ledger_spellings_are_readable_but_not_canonical(tmp_path):
    legacy = pp.legacy_suppression_ledgers("acme", content_root=tmp_path)
    assert pp.prospects_dir("acme", content_root=tmp_path) / "suppression.csv" in legacy
    assert pp.suppression_ledger("acme", content_root=tmp_path) not in legacy


def test_adjudication_records_land_with_the_evals(tmp_path):
    p = pp.adjudication_records("acme", "2026-08-27", content_root=tmp_path)
    assert p.parent == pp.evals_dir("acme", content_root=tmp_path)
    assert p.name == "adjudication-2026-08-27.jsonl"


def test_adjudications_are_not_written_beside_the_send_lists(tmp_path):
    assert pp.adjudications_dir("acme", content_root=tmp_path) != pp.sequences_dir(
        "acme", content_root=tmp_path
    )


def test_every_path_derives_from_the_given_content_root(tmp_path):
    for fn in (
        pp.prospects_dir,
        pp.sequences_dir,
        pp.pool_dir,
        pp.evals_dir,
        pp.latest_json,
        pp.master_list,
        pp.ready_to_load,
        pp.needs_verification,
        pp.suppression_ledger,
        pp.outcomes_jsonl,
    ):
        assert tmp_path in fn("acme", content_root=tmp_path).parents, fn.__name__


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "."])
def test_a_traversal_shaped_profile_fails_closed(bad, tmp_path):
    with pytest.raises(ValueError):
        pp.suppression_ledger(bad, content_root=tmp_path)


def test_a_traversal_shaped_date_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        pp.adjudication_records("acme", "../../etc/passwd", content_root=tmp_path)


def test_ready_to_load_is_the_only_visible_file_in_sequences(tmp_path):
    """Everything else is pooled; this is the contract the skill text relies on."""
    seq = pp.sequences_dir("acme", content_root=tmp_path)
    assert pp.ready_to_load("acme", content_root=tmp_path).parent == seq
    for pooled in (pp.master_list, pp.needs_verification, pp.suppression_ledger):
        assert pooled("acme", content_root=tmp_path).parent == seq / ".pool"


def test_unexpanded_env_placeholders_fall_back_to_defaults(monkeypatch, tmp_path):
    import pytest

    from agent.config import Config
    from gtm_core import paths

    # Test all placeholder variants: ${VAR}, $VAR, $(VAR), %VAR%, quoted "${VAR}"
    for placeholder in (
        "${GTM_CONTENT_ROOT}",
        "$GTM_CONTENT_ROOT",
        "$(GTM_CONTENT_ROOT)",
        "%GTM_CONTENT_ROOT%",
        '"${GTM_CONTENT_ROOT}"',
        "'${GTM_CONTENT_ROOT}'",
        "",
        "   ",
    ):
        monkeypatch.setenv("GTM_CONTENT_ROOT", placeholder)
        monkeypatch.setenv("GTM_PROFILES_ROOT", placeholder)
        monkeypatch.setenv("ACTIVE_PROFILE", placeholder)
        monkeypatch.setenv("GTM_PROFILE", placeholder)

        assert paths.resolve_content_root(tmp_path) == tmp_path / "content"
        assert paths.resolve_profiles_root(tmp_path) == tmp_path / "profiles"
        cfg = paths.PathConfig.from_env(tmp_path)
        assert cfg.default_profile == "template"

        agent_cfg = Config.from_env(repo_root=tmp_path)
        assert agent_cfg.default_profile == "template"
        assert agent_cfg.content_root == tmp_path / "content"
        assert agent_cfg.profiles_root == tmp_path / "profiles"

    # Verify _safe_segment raises on unexpanded placeholders
    for bad_seg in ("${GTM_PROFILE}", "$PROFILE", "%USERPROFILE%"):
        with pytest.raises(ValueError, match="unsafe profile"):
            paths._safe_segment(bad_seg, "profile")
