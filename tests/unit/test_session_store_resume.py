"""F1 — SessionStore resume wiring (always-on hardening PRD, test plan F1-3, F1-6, F1-8 half).

The SDK itself is NOT what these tests are about (that is the live test in tests/live/), so the
AgentSession is a fake that records how the store constructed it. The store, the state file and the
resume decision are real.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import agent.session as session_mod
from agent.cockpit_state import (
    NOTICE_EXPIRED_SESSION,
    NOTICE_FAILED_RESUME,
    CockpitSettings,
    CockpitState,
)


def sync(fn):
    """Run an async test body on a fresh loop (the repo has no pytest-asyncio)."""

    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class FakeSession:
    instances: list[FakeSession] = []
    fail_resume_with: str | None = None

    def __init__(
        self,
        cfg,
        profile,
        can_use_tool=None,
        usage_sink=None,
        *,
        allowed_skills=None,
        resume=None,
        on_session_id=None,
    ):
        self.profile = profile
        self.resume = resume
        self.resumed_from = resume
        self._on_session_id = on_session_id
        self.closed = False
        FakeSession.instances.append(self)

    async def run(self, prompt):
        if self.resume and FakeSession.fail_resume_with:
            raise RuntimeError(FakeSession.fail_resume_with)
        if self._on_session_id:
            self._on_session_id(f"sess-{self.profile}-{len(FakeSession.instances)}")
        yield f"echo:{prompt}"

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    FakeSession.instances = []
    FakeSession.fail_resume_with = None
    monkeypatch.setattr(session_mod, "AgentSession", FakeSession)


def _cfg(tmp_path):
    for name in ("alpha", "beta"):
        (tmp_path / "profiles" / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / "profiles" / name / "PROFILE.md").write_text("# fictional\n")
    return SimpleNamespace(
        content_root=tmp_path, profiles_root=tmp_path / "profiles", default_profile="alpha"
    )


def _store(tmp_path, *, now=NOW, resume=True, notices=None):
    state = CockpitState(tmp_path, clock=lambda: now)

    async def sink(chat_id, text):
        notices.append((chat_id, text))

    settings = CockpitSettings(session_resume=resume)
    store = session_mod.SessionStore(
        _cfg(tmp_path),
        state=state,
        settings=settings,
        notice_sink=sink if notices is not None else None,
    )
    return store, state


async def _drain(store, chat, prompt="hi"):
    return [t async for t in store.run(chat, prompt)]


@sync
async def test_second_process_resumes_the_recorded_session_for_the_same_profile(tmp_path):
    store, _ = _store(tmp_path)
    await _drain(store, 42)
    first_id = FakeSession.instances[0]._on_session_id  # callback captured the id
    assert first_id is not None
    store2, _ = _store(tmp_path)  # "restart": brand new store + state objects
    await _drain(store2, 42)
    assert FakeSession.instances[-1].resume == "sess-alpha-1"


@sync
async def test_f1_3_a_session_for_profile_a_is_never_resumed_into_profile_b(tmp_path):
    store, state = _store(tmp_path)
    await _drain(store, 42)  # records a session for alpha
    await store.switch_profile(42, "beta")
    await _drain(store, 42)
    assert FakeSession.instances[-1].profile == "beta"
    assert FakeSession.instances[-1].resume is None  # beta has no session of its own
    await store.switch_profile(42, "alpha")
    await _drain(store, 42)
    assert FakeSession.instances[-1].resume == "sess-alpha-1"  # alpha's own, not beta's


@sync
async def test_profile_binding_survives_a_restart(tmp_path):
    store, _ = _store(tmp_path)
    await store.switch_profile(42, "beta")
    store2, _ = _store(tmp_path)
    assert store2.active_profile(42) == "beta"
    assert store2.active_profile(99) == "alpha"  # default for an unseen chat


@sync
async def test_f1_6_expired_session_starts_fresh_with_the_notice(tmp_path):
    notices: list = []
    store, state = _store(tmp_path, notices=notices)
    await _drain(store, 42)
    late, _ = _store(tmp_path, now=NOW + timedelta(days=8), notices=notices)
    await _drain(late, 42)
    assert FakeSession.instances[-1].resume is None
    assert (42, NOTICE_EXPIRED_SESSION) in notices


@sync
async def test_f1_6_reset_forgets_the_session(tmp_path):
    store, state = _store(tmp_path)
    await _drain(store, 42)
    await store.reset(42)
    assert state.session_for(42, "alpha") is None
    await _drain(store, 42)
    assert FakeSession.instances[-1].resume is None


@sync
async def test_f1_6_failed_resume_logs_once_starts_fresh_and_replays_the_prompt(tmp_path):
    notices: list = []
    store, state = _store(tmp_path, notices=notices)
    await _drain(store, 42)
    store2, state2 = _store(tmp_path, notices=notices)
    FakeSession.fail_resume_with = "No conversation found with session ID: x"
    out = await _drain(store2, 42, "hello")
    assert out == ["echo:hello"]  # the operator's message was not lost
    assert (42, NOTICE_FAILED_RESUME) in notices
    assert FakeSession.instances[-1].resume is None
    # the dead id was forgotten, replaced by the fresh session's own id
    assert state2.session_for(42, "alpha").session_id != "sess-alpha-1"


@sync
async def test_an_unrelated_connect_error_does_not_forget_a_good_session(tmp_path):
    store, state = _store(tmp_path)
    await _drain(store, 42)
    store2, state2 = _store(tmp_path)
    FakeSession.fail_resume_with = "network is unreachable"
    with pytest.raises(RuntimeError):
        await _drain(store2, 42)
    assert state2.session_for(42, "alpha").session_id == "sess-alpha-1"  # kept for next time


@sync
async def test_kill_switch_session_resume_off_never_passes_resume(tmp_path):
    store, _ = _store(tmp_path)
    await _drain(store, 42)
    off, _ = _store(tmp_path, resume=False)
    await _drain(off, 42)
    assert FakeSession.instances[-1].resume is None


@sync
async def test_store_without_state_behaves_exactly_as_before(tmp_path):
    store = session_mod.SessionStore(_cfg(tmp_path))
    assert await _drain(store, 7) == ["echo:hi"]
    assert FakeSession.instances[-1].resume is None
