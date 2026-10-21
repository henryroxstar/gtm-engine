"""How a setup job is told to the caller: state, words, next move (W1; see ``provision.py``)."""

from __future__ import annotations

import os
from typing import Any

from .provision_model import RETRY_AFTER_S, jload

_HTTP_FOR_STATE = {
    "started": 202,
    "onboarding_running": 202,
    "awaiting_profile_confirmation": 202,
    "agent_created": 202,
}


def _next_steps(job: dict) -> dict:
    return {
        "mcp_bridge": {
            "command": "npx",
            # The npm name is deployment config, never an engine literal.
            "args": [
                "-y",
                os.environ.get("GTM_MCP_BRIDGE_PACKAGE", "<the MCP bridge package>"),
                "--endpoint",
                "<your gateway>/mcp",
            ],
            "env": {"GTM_API_KEY": "<the key returned once by the call that finished setup>"},
        },
        "first_run": {
            "method": "POST",
            "path": "/v1/runs",
            "body": {
                "agent_id": job["agent_id"],
                "pack": "<a pack from GET /v1/packs?agent_id=...>",
            },
        },
    }


def _view(
    job: dict,
    *,
    raw_key: str | None = None,
    warnings: list[str] | None = None,
    draft_id: str | None = None,
) -> tuple[int, dict]:
    state = job["state"]
    blockers, errors = jload(job["blockers"]), jload(job["errors"])
    body: dict[str, Any] = {
        "job_id": job["id"],
        "state": state,
        "ready": state == "ready",
        "step": job["step"],
        "blockers": blockers,
        "errors": errors,
        "warnings": warnings or [],
        "expires_at": job["expires_at"].isoformat() if job.get("expires_at") else None,
    }
    if job.get("agent_id"):
        body["agent"] = {
            "agent_id": job["agent_id"],
            "name": job["agent_name"],
            "profile_name": job["profile_slug"],
        }
    if job.get("key_id"):
        key: dict[str, Any] = {"id": job["key_id"], "prefix": job.get("key_prefix")}
        if raw_key is not None:
            key["raw_key"] = raw_key
            key["note"] = (
                "Shown once. Store it now; if this response is lost, repeat with reissue_key."
            )
        body["key"] = key

    if state == "started" and errors:
        body["message"] = (
            "Some provider keys were not accepted (the valid ones were saved). Fix the ones listed "
            "in errors and repeat the original request with the same Idempotency-Key."
        )
        body["next_action"] = {
            "kind": "fix_provider_keys",
            "then": "repeat with the same Idempotency-Key",
        }
    elif state in ("started", "onboarding_running"):
        body["message"] = (
            "Reading your site and drafting the profile. Repeat this request to check progress."
        )
        body["next_action"] = {"kind": "wait", "retry_after_s": RETRY_AFTER_S}
    elif state == "awaiting_profile_confirmation":
        body["message"] = (
            "Your profile draft is ready. Review it and confirm it, then repeat this request "
            "with the same Idempotency-Key to finish setup."
        )
        body["next_action"] = {
            "kind": "confirm_profile",
            "draft_id": draft_id,
            "profile_name": job["profile_slug"],
            "then": "call POST /v1/onboard/{draft_id}/promote, then repeat this request",
        }
    elif state == "agent_created":
        body["message"] = "The agent is created; finishing setup. Repeat this request."
        body["next_action"] = {"kind": "wait", "retry_after_s": 1}
    elif state == "ready":
        body["message"] = "Your agent is ready."
        body["next_steps"] = _next_steps(job)
    elif state == "ready_with_blockers":
        n = len(blockers)
        body["message"] = (
            f"Your agent was created, but {n} thing{'s' if n != 1 else ''} must be fixed before it "
            "can run. Each one says how; repeat this request after fixing them."
        )
        body["next_steps"] = _next_steps(job)
    elif state == "failed":
        why = errors[0].get("message") if errors else "an earlier step failed"
        body["message"] = f"Setup stopped: {why} Start again with a new Idempotency-Key."
        body["next_action"] = {"kind": "start_again", "then": "send a new Idempotency-Key"}
    else:  # abandoned
        body["message"] = (
            "This setup was cancelled or expired. Start again with a new Idempotency-Key."
        )
        body["next_action"] = {"kind": "start_again", "then": "send a new Idempotency-Key"}
    return _HTTP_FOR_STATE.get(state, 200), body
