"""A1 — reachability → tool-layer enforcement (SECURITY-SELF-ASSESSMENT #11/#12).

Two levers, both tested: the SDK `skills=` allowlist (primary — unlisted skills
hidden + rejected by the Skill tool) and the `can_use_tool` `allowed_skills`
branch (defence-in-depth). Fail-closed probe rule: every denial has its allowed
positive-control twin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent.permissions import classify_tool, make_headless_can_use_tool

REPO = Path(__file__).resolve().parents[2]
SCOPE = frozenset({"content-radar", "content-plan"})


# ── classify_tool: the pure policy branch ─────────────────────────────────────


def test_skill_in_scope_allowed_and_out_of_scope_denied():
    assert classify_tool("Skill", {"skill": "content-plan"}, allowed_skills=SCOPE) == "allow"
    assert classify_tool("Skill", {"skill": "prospect"}, allowed_skills=SCOPE) == "deny"


def test_plugin_prefixed_skill_name_resolves_to_bare_name():
    assert classify_tool("Skill", {"skill": "gtm:content-radar"}, allowed_skills=SCOPE) == "allow"
    assert classify_tool("Skill", {"skill": "gtm:prospect"}, allowed_skills=SCOPE) == "deny"


def test_missing_or_empty_skill_name_fails_closed():
    assert classify_tool("Skill", {}, allowed_skills=SCOPE) == "deny"
    assert classify_tool("Skill", None, allowed_skills=SCOPE) == "deny"
    assert classify_tool("Skill", {"skill": ""}, allowed_skills=SCOPE) == "deny"


def test_no_scope_allows_every_skill():
    """allowed_skills=None ⇒ Skill is ALLOWED (the unscoped VPS/cockpit paths).

    This assertion was inverted (``== "escalate"``) when the reachability lever landed, to prove
    Track A left unscoped paths byte-identical. It did — but the behaviour it pinned was broken:
    ``Skill`` is absent from ``_ALWAYS_ALLOW``, so with no scope it hit the catch-all ``escalate``,
    which the cockpit renders as a deny plus a "Blocked a tool the brain tried to use" notice. The
    CLI routes EVERY packaged skill through this tool, so every skill run on the VPS/cockpit path
    was denied. The Track A guarantee that actually matters — the scope branch is Skill-only and
    changes nothing else — is still covered by ``test_scope_does_not_touch_other_tools``.
    """
    assert classify_tool("Skill", {"skill": "content-plan"}) == "allow"
    assert classify_tool("Skill", {"skill": "anything"}, allowed_skills=None) == "allow"
    # The scoped path is untouched: an out-of-pack skill is still denied.
    assert classify_tool("Skill", {"skill": "prospect"}, allowed_skills=SCOPE) == "deny"


def test_scope_does_not_touch_other_tools():
    """The reachability branch is Skill-only — Read stays allowed, Bash policy holds."""
    assert classify_tool("Read", {"file_path": "README.md"}, allowed_skills=SCOPE) == "allow"
    assert classify_tool("Bash", {"command": "npm install"}, allowed_skills=SCOPE) == "deny"


# ── callback level: the deny carries the pack-scope message ───────────────────


def test_headless_callback_denies_out_of_pack_skill_with_specific_message():
    cb = make_headless_can_use_tool(allowed_skills=SCOPE)

    async def _go():
        denied = await cb("Skill", {"skill": "prospect"}, None)
        allowed = await cb("Skill", {"skill": "content-radar"}, None)  # positive control
        return denied, allowed

    denied, allowed = asyncio.run(_go())
    assert type(denied).__name__ == "PermissionResultDeny"
    assert "active packs" in denied.message
    assert type(allowed).__name__ == "PermissionResultAllow"


# ── SDK option level: build_agent_options sets skills= ────────────────────────


def _options_with(monkeypatch, allowed):
    from agent import session as session_mod

    class _Spec:
        role = "brain_plan"
        provider = "anthropic"
        model = "claude-test"
        base_url = ""
        supports_adaptive_thinking = False
        supports_effort = False

        def api_key(self):
            return ""

    monkeypatch.setattr("agent.session.resolve_model", lambda *a, **k: _Spec())
    monkeypatch.setattr("agent.mcp_config.build_mcp_servers", lambda *a, **k: {})
    monkeypatch.setattr("agent.profiles.system_prompt_for", lambda *a, **k: "")

    from types import SimpleNamespace

    cfg = SimpleNamespace(
        repo_root=REPO,
        plugin_path=REPO / "plugin",
        content_root=REPO / "content",
        profiles_root=REPO / "profiles",
        per_run_cap_usd=1.0,
    )
    return session_mod.build_agent_options(cfg, "example", allowed_skills=allowed)


def test_build_agent_options_sets_sdk_skills_allowlist(monkeypatch):
    options = _options_with(monkeypatch, frozenset({"b-skill", "a-skill"}))
    assert options.skills == ["a-skill", "b-skill"]  # sorted, deterministic


def test_build_agent_options_without_scope_leaves_skills_unset(monkeypatch):
    options = _options_with(monkeypatch, None)
    assert getattr(options, "skills", None) in (None, "all") or options.skills is None


# ── prompt mode: the COMMERCIAL scope (2026-08-25) ────────────────────────────
#
# Pack mode narrows to active packs ∩ entitlement. Prompt mode has no pack and no
# graph — so the scope is entitlement alone, over the whole skill registry. Before
# this landed, prompt mode passed NO scope at all and every registered skill was
# reachable with an ordinary tenant JWT, which made gtm_core/gating.toml's per-skill
# floors unenforceable on the free-text path.


def test_entitled_skills_drops_skills_above_the_plan():
    from gtm_core.gating import entitled_skills

    free = entitled_skills("free")
    pro = entitled_skills("pro")
    pro_plus = entitled_skills("pro_plus")

    # The three named in the report: all three are out of reach for free…
    for paid in ("content-radar", "carousel-visuals", "case-study"):
        assert paid not in free
    # …and the ladder is monotone, not all-or-nothing: pro buys visual renders,
    # pro_plus buys the marketing/content chain and video suite.
    assert "carousel-visuals" in pro and "content-radar" not in pro and "case-study" not in pro
    assert {"content-radar", "case-study", "video-render"} <= pro_plus
    assert free < pro < pro_plus  # strict subsets

    # Positive control: a free workspace is not scoped to nothing — the free-floor
    # skills stay reachable (a scope that denies everything is not a boundary, it is
    # an outage).
    assert {"account-dossier", "prospect", "airq-scan"} <= free


def test_entitled_skills_fails_closed_on_an_unknown_entitlement():
    from gtm_core.gating import entitled_skills

    assert entitled_skills("enterprise-max") == entitled_skills("free")


def test_execute_run_passes_the_entitlement_scope_to_the_session():
    """The wiring the hole was: _execute_run must hand the session a scope.

    Drives the real _execute_run with a fake pool/session store (test_cost_guard's
    shape) and asserts the scope reaching sessions.run is the caller's entitlement
    set — a free workspace cannot get `content-radar` into its own session.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from backend.routers import runs as runs_router
    from gtm_core.gating import entitled_skills
    from tests.backend._protocol1 import (
        BUDGET_MODULES,
        DONE_PUSH_MODULES,
        SCOPE_MODULES,
        patch_everywhere,
    )

    ws_id = "00000000-0000-0000-0000-000000000001"
    run_id = "00000000-0000-0000-0000-0000000000a1"

    class _Conn:
        async def execute(self, sql, *args):
            return None

        async def fetchrow(self, sql, *args):
            # RL-03: start_run now guards its UPDATE with `status NOT IN (...)` and
            # reads the match back via `RETURNING id` instead of a bare `execute`.
            return {"id": args[0]} if args else None

    class _Sessions:
        def __init__(self):
            self.kwargs = []

        def run(self, *args, **kwargs):
            self.kwargs.append(kwargs)

            async def _agen():
                return
                yield ""  # pragma: no cover — makes this an async generator

            return _agen()

    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield _Conn()

    def _drive(entitlement):
        sessions = _Sessions()

        async def _go():
            with (
                patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope),
                patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=True)),
                patch_everywhere(
                    DONE_PUSH_MODULES, "send_run_done_push", AsyncMock(return_value=0)
                ),
            ):
                await runs_router._execute_run(
                    MagicMock(),
                    sessions,
                    ws_id,
                    run_id,
                    "example2",
                    "write me a radar brief",
                    False,
                    entitlement=entitlement,
                )

        asyncio.run(_go())
        return sessions.kwargs[0]["allowed_skills"]

    free_scope = _drive("free")
    assert free_scope == entitled_skills("free")
    assert "content-radar" not in free_scope
    # Positive control: the same call on pro_plus DOES carry it.
    assert "content-radar" in _drive("pro_plus")


class MagicCfg:
    """Minimal Config stand-in for connect() (no workspace ⇒ no scoping rewrite)."""

    repo_root = REPO
    content_root = REPO / "content"
    profiles_root = REPO / "profiles"


def test_backend_session_arms_both_levers_with_the_scope(monkeypatch):
    """connect() must feed the scope to the permission callback AS WELL AS the SDK
    option — the backend passes an explicit can_use_tool, so build_agent_options'
    default-callback branch (which would otherwise carry it) never runs."""
    import agent.permissions as permissions_mod
    import agent.session as agent_session_mod
    from agent.config import Config
    from backend.session import _BackendSession

    seen: dict = {}

    def _fake_headless(on_deny=None, *, allowed_skills=None):
        seen["callback_scope"] = allowed_skills
        return "cb"

    class _FakeAgentSession:
        def __init__(self, cfg, profile, can_use_tool=None, usage_sink=None, **kw):
            seen["session_scope"] = kw.get("allowed_skills")

        async def connect(self):
            return None

    monkeypatch.setattr(permissions_mod, "make_headless_can_use_tool", _fake_headless)
    monkeypatch.setattr(agent_session_mod, "AgentSession", _FakeAgentSession)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls, **kw: MagicCfg()))

    scope = frozenset({"account-dossier"})
    asyncio.run(_BackendSession(REPO, "example", allowed_skills=scope).connect())

    assert seen["callback_scope"] == scope  # defence-in-depth lever
    assert seen["session_scope"] == scope  # SDK skills= lever


def test_session_cache_key_includes_the_scope(monkeypatch):
    """A warm session built under a broader entitlement must never serve a run whose
    entitlement has since narrowed — the scope is part of the cache key."""
    import backend.session as session_mod

    built: list = []

    class _FakeSession:
        def __init__(
            self, repo_root, profile_name, *, pool=None, workspace_id=None, allowed_skills=None
        ):
            self.connected = False
            self.last_used = 0.0
            self.scope = allowed_skills
            built.append(self)

        def touch(self):
            self.last_used = 1.0

        async def connect(self):
            self.connected = True

        async def run(self, prompt, run_id=None):
            yield "chunk"

        async def close(self):
            self.connected = False

    monkeypatch.setattr(session_mod, "_BackendSession", _FakeSession)
    store = session_mod.BackendSessionStore(REPO)
    pro_plus = frozenset({"content-radar", "account-dossier"})
    free = frozenset({"account-dossier"})

    async def _drain(agen):
        return [c async for c in agen]

    async def body():
        await _drain(store.run(None, "ws", "prof", "p", "r1", allowed_skills=pro_plus))
        await _drain(store.run(None, "ws", "prof", "p", "r2", allowed_skills=pro_plus))
        await _drain(store.run(None, "ws", "prof", "p", "r3", allowed_skills=free))

    asyncio.run(body())

    # Same scope reuses the warm session; the narrowed scope builds a new one.
    assert [s.scope for s in built] == [pro_plus, free]
