"""F3-3 against the REAL SDK: `/stop` (``SessionStore.interrupt``) ends a running turn within 5 seconds.

The test plan lists "SDK ``interrupt()`` stopping a turn within 5 s" as unverified until run live — a fake
client can only prove we CALL interrupt, never that the turn actually stops. Positive control: the same prompt
left alone produces far more output than the interrupted one.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from claude_agent_sdk import ClaudeAgentOptions

import agent.session as sm
from agent.cockpit_state import CockpitSettings, CockpitState

LONG = (
    "Write 3000 words of detailed prose about the history of the printing press. "
    "Do not stop early and do not summarise."
)  # a generation of roughly a minute: the interrupt lands mid-generation, not after it


def _store(tmp_path, cwd):
    cfg = SimpleNamespace(
        content_root=tmp_path,
        profiles_root=tmp_path / "profiles",
        default_profile="alpha",
        persist_sessions=True,
    )

    def thin(cfg_, profile, **_ignored):
        return ClaudeAgentOptions(
            cwd=str(cwd),
            allowed_tools=[],
            max_turns=1,
            model="claude-haiku-4-5",
            system_prompt="You follow instructions exactly.",
            extra_args={"no-session-persistence": None},  # leave no transcript behind
        )

    sm.build_agent_options = thin
    return sm.SessionStore(
        cfg, state=CockpitState(tmp_path), settings=CockpitSettings(session_resume=False)
    )


def test_f3_3_interrupt_ends_a_running_turn_within_five_seconds(tmp_path):
    cwd = tmp_path / "proj"
    cwd.mkdir()

    async def scenario():
        store = _store(tmp_path, cwd)
        chunks: list[str] = []

        async def turn():
            async for text in store.run(42, LONG):
                chunks.append(text)

        task = asyncio.create_task(turn())
        await asyncio.sleep(
            3.0
        )  # fixed delay: the model is mid-generation (a TextBlock arrives only when whole)
        assert not task.done(), (
            "the turn finished on its own before /stop — the test proves nothing"
        )
        t0 = time.monotonic()
        assert await store.interrupt(42) is True
        await asyncio.wait_for(task, timeout=5)  # the plan's bound
        elapsed = time.monotonic() - t0
        stopped_len = len("".join(chunks))
        await store.reset(42)

        # positive control: the SAME prompt, left alone, runs to completion and produces far more
        control = _store(tmp_path, cwd)
        full = "".join([t async for t in control.run(43, LONG)])
        await control.reset(43)
        return elapsed, stopped_len, len(full)

    elapsed, stopped_len, full_len = asyncio.run(scenario())
    assert elapsed < 5, f"interrupt took {elapsed:.1f}s"
    assert full_len > 5000, f"the control did not produce a long answer ({full_len} chars)"
    assert stopped_len < full_len / 2, (
        "the turn was not actually cut short (interrupt had no effect)"
    )
