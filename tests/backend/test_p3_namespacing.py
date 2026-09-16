"""Phase 3 — per-workspace filesystem namespacing (B3). Unit guards, no live DB.

Proves two workspaces resolve DISJOINT, isolated {profiles,content} trees, that the
backend's per-run Config is pinned to the calling workspace's subtree (so a run can
never read/write another tenant's files), and that build_agent_options carries those
roots into the SDK subprocess env (scoping skills too).
"""

from __future__ import annotations

import dataclasses

import pytest

from gtm_core import paths


def test_workspace_trees_are_disjoint_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    a = paths.workspace_profiles_root("ws-aaaa")
    b = paths.workspace_profiles_root("ws-bbbb")
    assert a == tmp_path / "ws-aaaa" / "profiles"
    assert b == tmp_path / "ws-bbbb" / "profiles"
    assert a != b
    assert paths.workspace_content_root("ws-aaaa") == tmp_path / "ws-aaaa" / "content"
    # neither workspace's root is an ancestor of the other's profiles dir
    assert (tmp_path / "ws-bbbb") not in a.parents


def test_workspace_id_traversal_is_blocked():
    for bad in ("../secrets", "a/b", "..", "x\x00y", ""):
        with pytest.raises(ValueError):
            paths.workspace_tree(bad)


def test_backend_config_is_pinned_to_the_workspace_subtree(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    from agent.config import Config
    from backend.session import _workspace_scoped_config

    base = Config.from_env(repo_root=tmp_path)
    ca = _workspace_scoped_config(base, "ws-aaaa", tmp_path)
    cb = _workspace_scoped_config(base, "ws-bbbb", tmp_path)

    assert ca.content_root == tmp_path / "ws-aaaa" / "content"
    assert ca.profiles_root == tmp_path / "ws-aaaa" / "profiles"
    # the two runs share NO content/profile path
    assert ca.content_root != cb.content_root
    assert ca.profiles_root != cb.profiles_root
    # dirs are materialized so run writes don't fail
    assert ca.content_root.is_dir() and ca.profiles_root.is_dir()
    # unrelated fields (repo_root, plugin_path) are preserved from the base config
    assert ca.repo_root == base.repo_root
    assert ca.plugin_path == base.plugin_path


def test_build_agent_options_scopes_subprocess_env_to_config_roots(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    from agent.config import Config
    from agent.session import build_agent_options

    base = Config.from_env(repo_root=tmp_path)
    prof = tmp_path / "ws-aaaa" / "profiles" / "template"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "PROFILE.md").write_text("# Template\n", encoding="utf-8")
    cfg = dataclasses.replace(
        base,
        content_root=tmp_path / "ws-aaaa" / "content",
        profiles_root=tmp_path / "ws-aaaa" / "profiles",
    )
    opts = build_agent_options(cfg, "template")
    # the SDK merges options.env over os.environ for the CLI subprocess only, so
    # skills resolve under THIS workspace's roots — never the shared/ambient ones.
    assert opts.env["GTM_CONTENT_ROOT"] == str(cfg.content_root)
    assert opts.env["GTM_PROFILES_ROOT"] == str(cfg.profiles_root)


def test_byok_provider_keys_fail_closed_never_inherit_the_platform_key(tmp_path, monkeypatch):
    """A workspace with no BYOK key for a provider must NOT silently fall back to the
    operator's own platform-level key (SALESHANDY_API_KEY etc., meant for the
    single-tenant VPS) — that would run one tenant's outreach through another
    party's real provider account. The field must be None, so
    agent/mcp_config.py's `if cfg.<provider>_api_key:` omits the tool entirely."""
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    monkeypatch.setenv("SALESHANDY_API_KEY", "operators-own-platform-key")
    monkeypatch.setenv("APOLLO_API_KEY", "operators-own-platform-key")
    from agent.config import Config
    from backend.session import _workspace_scoped_config

    base = Config.from_env(repo_root=tmp_path)
    assert base.saleshandy_api_key == "operators-own-platform-key"  # sanity: base DOES carry it

    # No credentials at all (e.g. onboarding, before any BYOK call) — every provider closed.
    no_creds = _workspace_scoped_config(base, "ws-aaaa", tmp_path)
    assert no_creds.saleshandy_api_key is None
    assert no_creds.apollo_api_key is None
    assert no_creds.rocketreach_api_key is None
    assert no_creds.syften_api_key is None

    # Partial BYOK — only the configured provider's key comes through; the others
    # stay closed rather than inheriting the platform key.
    partial = _workspace_scoped_config(
        base, "ws-bbbb", tmp_path, credentials={"saleshandy": "workspace-own-key"}
    )
    assert partial.saleshandy_api_key == "workspace-own-key"
    assert partial.apollo_api_key is None
