"""One cockpit "process life": run ONE turn through the real SessionStore/AgentSession/CockpitState.

Invoked as a fresh OS process by test_resume_live.py, so "restart the process" is literal. Only the
options builder is thinned (no tenant MCP servers, no profile prompt, haiku) — everything this PRD
changed (state file, session-id capture, ``resume=`` plumbing, failed-resume fallback) is the real code.
Prints one JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from claude_agent_sdk import ClaudeAgentOptions

import agent.session as sm
from agent.cockpit_state import CockpitSettings, CockpitState


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--prompt", required=True)
    ns = ap.parse_args()

    cfg = SimpleNamespace(
        content_root=Path(ns.root),
        profiles_root=Path(ns.root) / "profiles",
        default_profile="alpha",
        persist_sessions=True,
    )

    def thin_options(cfg_, profile, *, resume=None, **_ignored):
        kw = {
            "cwd": ns.cwd,
            "allowed_tools": [],
            "max_turns": 1,
            "model": "claude-haiku-4-5",
            "system_prompt": "You are a terse test assistant. Answer in as few words as possible.",
            **sm._resume_kw(cfg_, resume),  # the real resume plumbing
        }
        return ClaudeAgentOptions(**kw)

    sm.build_agent_options = thin_options
    notices: list[str] = []

    async def sink(_chat: int, text: str) -> None:
        notices.append(text)

    state = CockpitState(Path(ns.root))
    store = sm.SessionStore(cfg, state=state, settings=CockpitSettings(), notice_sink=sink)

    async def go() -> dict:
        parts: list[str] = []
        async for t in store.run(42, ns.prompt):
            parts.append(t)
        sess = store._sessions[42]
        info = {"resumed_from": sess.resumed_from}
        await sess.close()
        return {"reply": "".join(parts), **info}

    out = asyncio.run(go())
    entry = state.session_for(42, "alpha")
    out.update(
        session_id=entry.session_id if entry else None,
        turns=entry.turns if entry else 0,
        notices=notices,
    )
    print("RESULT " + json.dumps(out))


if __name__ == "__main__":
    main()
