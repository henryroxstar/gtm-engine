"""MM2 (D1-A): worker spend is tied to the run that caused it, in the ledger the workers already write.

The MCP workers are stdio subprocesses that meter into ``<content_root>/<profile>/costs.jsonl`` with no
database credentials and no knowledge of a run. For the backend to debit a run's wallet by what its
workers spent, each row has to name the run. ``GTM_RUN_ID`` is set once per pack run into every
cost-writing worker's env (``build_mcp_servers``), and stamped onto the row at the single funnel
``Ledgers.append_cost`` — so no worker needs editing and a worker added later is covered.

Absence is neutral here: no ``GTM_RUN_ID`` ⇒ no stamp, so the single-tenant VPS and every non-pack path
write exactly the rows they wrote before.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from agent import mcp_config
from agent.config import Config
from agent.ledgers import Ledgers

PROFILE = "example"


def _rows(cfg) -> list[dict]:
    path = cfg.content_root / PROFILE / "costs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── the stamp, at the funnel ──────────────────────────────────────────────────


def test_append_cost_stamps_the_run_id_from_the_environment(cfg, monkeypatch):
    monkeypatch.setenv("GTM_RUN_ID", "11111111-1111-1111-1111-111111111111")
    Ledgers(cfg, PROFILE).append_cost({"tool": "apollo", "cost_usd": 0.3})
    assert _rows(cfg)[0]["run_id"] == "11111111-1111-1111-1111-111111111111"


def test_append_cost_never_overwrites_a_run_id_the_caller_set(cfg, monkeypatch):
    monkeypatch.setenv("GTM_RUN_ID", "env-run")
    Ledgers(cfg, PROFILE).append_cost({"tool": "brain", "cost_usd": 0.3, "run_id": "caller-run"})
    assert _rows(cfg)[0]["run_id"] == "caller-run"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_absent_or_blank_run_id_stamps_nothing(cfg, monkeypatch, value):
    """The VPS / non-pack path: no GTM_RUN_ID, so the row is byte-identical to before."""
    if value is None:
        monkeypatch.delenv("GTM_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("GTM_RUN_ID", value)
    Ledgers(cfg, PROFILE).append_cost({"tool": "apollo", "cost_usd": 0.3})
    assert "run_id" not in _rows(cfg)[0]


# ── the env, at the worker boundary ───────────────────────────────────────────

_WORKER_GATES = {
    "worker": "deepseek_api_key",
    "vision": "anthropic_api_key",
    "gemini_image": "gemini_api_key",
    "tts": "elevenlabs_api_key",
    "rocketreach": "rocketreach_api_key",
    "higgsfield_video": "higgsfield_api_key",
    "apollo": "apollo_api_key",
}


def _cfg_with_every_worker(tmp_path):
    base = Config.from_env(repo_root=tmp_path)
    overrides = {
        "content_root": tmp_path / "content",
        "profiles_root": tmp_path / "profiles",
        "deepseek_api_key": "dsk",
        "anthropic_api_key": "sk-ant",
        "gemini_api_key": "gem",
        "elevenlabs_api_key": "el",
        "elevenlabs_voice_id": "v",
        "rocketreach_api_key": "rr",
        "higgsfield_api_key": "hf",
        "higgsfield_api_secret": "hs",
        "apollo_api_key": "ap",
    }
    return dataclasses.replace(base, **{k: v for k, v in overrides.items() if hasattr(base, k)})


def test_every_cost_writing_worker_is_told_the_run(tmp_path):
    cfg = _cfg_with_every_worker(tmp_path)
    servers = mcp_config.build_mcp_servers(cfg, PROFILE, run_id="run-xyz")
    present = [k for k in _WORKER_GATES if k in servers]
    assert len(present) == len(_WORKER_GATES)  # positive control: the loop below is not vacuous
    for key in present:
        assert servers[key]["env"].get("GTM_RUN_ID") == "run-xyz", key


def test_no_run_means_no_run_env(tmp_path):
    cfg = _cfg_with_every_worker(tmp_path)
    servers = mcp_config.build_mcp_servers(cfg, PROFILE)
    assert all("GTM_RUN_ID" not in s.get("env", {}) for s in servers.values())


def test_build_agent_options_threads_the_run_to_the_workers(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options

    cfg = _cfg_with_every_worker(tmp_path)
    options = build_agent_options(cfg, PROFILE, run_id="run-abc")
    assert options.mcp_servers["apollo"]["env"]["GTM_RUN_ID"] == "run-abc"
    without = build_agent_options(cfg, PROFILE)
    assert "GTM_RUN_ID" not in without.mcp_servers["apollo"]["env"]
