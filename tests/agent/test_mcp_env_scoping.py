"""MCP worker env is scoped to the run's filesystem tree (2026-07-05 review follow-up).

The in-repo stdio workers (worker/vision/gemini_image/tts/rocketreach/higgsfield_video)
each write their OWN cost record to ``<content_root>/<profile>/costs.jsonl`` via
``Config.from_env()``. A stdio MCP child does NOT inherit arbitrary env from the CLI
subprocess (only a safe allowlist + its explicit config ``env``), so if
``GTM_CONTENT_ROOT``/``GTM_PROFILES_ROOT`` are not passed in each server's env block, a
backend run's per-workspace scoping (P3) never reaches the worker and two tenants sharing
a profile name collide their cost ledgers. These tests assert build_mcp_servers threads
the scoped roots into every such worker.
"""

from __future__ import annotations

import dataclasses

from agent import mcp_config
from agent.config import Config

# Workers that resolve content_root via Config.from_env() to write costs.jsonl — each
# MUST carry the scoped roots. Maps server key -> the cfg field that gates it on.
_SCOPED_WORKERS = {
    "worker": "deepseek_api_key",
    "vision": "anthropic_api_key",
    "gemini_image": "gemini_api_key",
    "tts": "elevenlabs_api_key",
    "rocketreach": "rocketreach_api_key",
    "higgsfield_video": "higgsfield_api_key",
}


def _scoped_cfg(tmp_path):
    """A Config whose content/profile roots are pinned to a per-workspace tree, with
    every cost-writing worker's gating secret set so its server is emitted."""
    base = Config.from_env(repo_root=tmp_path)
    content = tmp_path / "data" / "workspaces" / "ws-abc" / "content"
    profiles = tmp_path / "data" / "workspaces" / "ws-abc" / "profiles"
    overrides = {
        "content_root": content,
        "profiles_root": profiles,
        "deepseek_api_key": "dsk-test",
        "anthropic_api_key": "sk-ant-test",
        "gemini_api_key": "gem-test",
        "elevenlabs_api_key": "el-test",
        "elevenlabs_voice_id": "voice-test",
        "rocketreach_api_key": "rr-test",
        "higgsfield_api_key": "hf-key",
        "higgsfield_api_secret": "hf-secret",
    }
    # Only set fields that actually exist on this Config revision (fail-soft on drift).
    valid = {k: v for k, v in overrides.items() if hasattr(base, k)}
    return dataclasses.replace(base, **valid), content, profiles


def test_all_cost_writing_workers_carry_scoped_roots(tmp_path):
    cfg, content, profiles = _scoped_cfg(tmp_path)
    servers = mcp_config.build_mcp_servers(cfg, "example2")
    for key, gate_field in _SCOPED_WORKERS.items():
        if not hasattr(cfg, gate_field):  # worker not present on this revision
            continue
        assert key in servers, f"{key} server should be emitted when its key is set"
        env = servers[key]["env"]
        assert env.get("GTM_CONTENT_ROOT") == str(content), f"{key} content root not scoped"
        assert env.get("GTM_PROFILES_ROOT") == str(profiles), f"{key} profiles root not scoped"
        assert env.get("GTM_PROFILE") == "example2", f"{key} profile not passed"


def test_worker_scoped_root_is_not_the_default_tree(tmp_path):
    """The worker must see the WORKSPACE tree, not the repo-default content root —
    otherwise the P3 isolation the env pass exists for is silently a no-op."""
    cfg, content, _ = _scoped_cfg(tmp_path)
    default_content = Config.from_env(repo_root=tmp_path).content_root
    assert content != default_content  # sanity: the scoped root really differs
    servers = mcp_config.build_mcp_servers(cfg, "example2")
    assert servers["worker"]["env"]["GTM_CONTENT_ROOT"] == str(content)
    assert servers["worker"]["env"]["GTM_CONTENT_ROOT"] != str(default_content)
