"""W2 / P2-3: metering still lands with the launcher on (PRD 2026-10-19a, decision D2).

D2 keeps the audit ledgers in the run's own tree *because* the MCP children (``worker_draft``, ``vision``, …)
append ``costs.jsonl`` as the workspace's own user. Moving the ledger to a directory the run cannot write would
have silenced exactly this spend. These tests run the real children's ``_meter`` functions with the launcher flag
on and assert the two readers that matter see the rows: the §R2 budget total (``workspace_jsonl_month_total``)
and the per-run settlement total (``workspace_jsonl_run_total``).

What this CANNOT show on a laptop: the children running as a pool uid with the backend reading their rows
through POSIX ACLs. That is the staging step in the launcher rollout runbook.

Note, found while writing this: ``GET /v1/ledger/usage`` reads Postgres ``cost_records`` only, so worker spend
(JSONL) is not in that report with or without the launcher. The cap and the wallet settlement do read it.
"""

from __future__ import annotations

import pytest

from agent.mcp.vision import server as vision
from agent.mcp.worker import server as worker
from gtm_core.metering import workspace_jsonl_month_total, workspace_jsonl_run_total
from gtm_core.paths import workspace_content_root

WS = "ws-fixture-one"
OTHER = "ws-fixture-two"


@pytest.fixture
def launcher_run(tmp_path, monkeypatch):
    """The environment a launched run's MCP child has: its profile, its workspace's content root, its run id."""
    root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("GTM_TENANT_LAUNCHER", "1")
    monkeypatch.setenv("GTM_PROFILE", "fixtureco")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(workspace_content_root(WS)))
    monkeypatch.setenv("GTM_RUN_ID", "run_fixture_1")
    workspace_content_root(WS).mkdir(parents=True)
    workspace_content_root(OTHER).mkdir(parents=True)
    return root


def test_worker_and_vision_spend_reaches_the_cap_and_the_settlement(launcher_run):
    worker._meter("draft", {"prompt_tokens": 2000, "completion_tokens": 1000})
    vision._meter({"input_tokens": 1500, "output_tokens": 300})

    month = workspace_jsonl_month_total(WS, strict=True)
    run = workspace_jsonl_run_total(WS, "run_fixture_1")
    assert month > 0, "the §R2 cap cannot see the worker/vision spend of a launched run"
    assert run == pytest.approx(month), (
        "settlement and the cap read different totals for the same rows"
    )


def test_each_row_is_stamped_with_the_run_it_belongs_to(launcher_run, monkeypatch):
    worker._meter("draft", {"prompt_tokens": 1000, "completion_tokens": 0})
    monkeypatch.setenv("GTM_RUN_ID", "run_fixture_2")
    vision._meter({"input_tokens": 1000, "output_tokens": 0})

    one = workspace_jsonl_run_total(WS, "run_fixture_1")
    two = workspace_jsonl_run_total(WS, "run_fixture_2")
    assert one > 0 and two > 0
    assert one + two == pytest.approx(workspace_jsonl_month_total(WS, strict=True))


def test_one_workspaces_spend_is_not_in_anothers_total(launcher_run):
    worker._meter("draft", {"prompt_tokens": 5000, "completion_tokens": 5000})
    assert workspace_jsonl_month_total(OTHER, strict=True) == 0.0
    assert workspace_jsonl_run_total(OTHER, "run_fixture_1") == 0.0


def test_the_launcher_flag_does_not_change_what_gets_metered(launcher_run, monkeypatch):
    worker._meter("draft", {"prompt_tokens": 2000, "completion_tokens": 1000})
    with_flag = workspace_jsonl_month_total(WS, strict=True)

    monkeypatch.delenv("GTM_TENANT_LAUNCHER")
    # a fresh workspace tree, so the second reading is not the first plus another row
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(workspace_content_root(OTHER)))
    worker._meter("draft", {"prompt_tokens": 2000, "completion_tokens": 1000})
    assert workspace_jsonl_month_total(OTHER, strict=True) == pytest.approx(with_flag)
