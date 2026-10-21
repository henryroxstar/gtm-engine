"""MM8: the first call on an un-onboarded workspace says "finish setup", not "profile not bound".

A developer who has just bought access and points their IDE at the MCP endpoint has a workspace and a key
but NO ``profiles`` row yet — onboarding happens in the portal. Today every profile-scoped tool answers with
``Profile 'x' is not bound to caller workspace`` (which reads like a typo) and ``describe`` answers
``{"packs": []}`` (which reads like "nothing available"). Both are true and neither tells them what to do.

The typed error is ``setup_required``: it raises :class:`SetupRequiredError` (a ``ValueError`` with a stable
leading code, so the IDE shows it as an error) and its text points at the portal URL from a SETTING — never
a hard-coded link, and with no setting no link at all.

What is pinned, and why each piece:

* every tool the server registers is classified — profile-aware (must return the typed error for a workspace
  with no profile) or in the closed ``PROFILE_FREE`` list — enumerated from the live registry, so a tool added
  tomorrow fails here until someone decides which it is;
* a workspace that HAS a profile, asking for the wrong one, keeps the generic "not bound" refusal;
* a workspace that finished onboarding never sees the setup error (the control that stops it being
  returned for everybody).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("mcp")
pytest.importorskip("asyncpg")

from gtm_core.capabilities import Entitlement  # noqa: E402
from mcp_server import server  # noqa: E402
from mcp_server import setup_required as sr  # noqa: E402
from mcp_server.auth import ApiKeyCtx, assert_workspace_profile  # noqa: E402

WS = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"
PORTAL = "https://portal.example.test/setup"


def _run(coro):
    return asyncio.run(coro)


def _pool(profiles: set[str], *, has_default: bool = True):
    """A pool whose ``profiles`` table holds ``profiles`` for this workspace, and nothing else."""
    conn = MagicMock()
    conn.execute = AsyncMock()

    def _fetchrow(query, *args):
        q = " ".join(query.split())
        if "FROM profiles" in q and "profile_name = $2" in q:
            return {"1": 1} if args[1] in profiles else None
        if "FROM profiles" in q and "is_default" in q:
            return {"profile_name": sorted(profiles)[0]} if profiles and has_default else None
        if "FROM profiles" in q:
            return {"1": 1} if profiles else None
        return None

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.fixture
def world(monkeypatch):
    """The MCP server with auth stubbed to one PRO workspace, and a profiles table the test chooses."""
    from backend.callers.principal import Principal

    key = ApiKeyCtx(workspace_id=WS, entitlement=Entitlement.PRO, key_id="k")
    principal = Principal(
        kind="user", subject="u", workspace_id=WS, entitlement=Entitlement.PRO, verifier="jwt"
    )

    async def _key(ctx):
        return key

    async def _principal(ctx, pool):
        return principal

    monkeypatch.setattr(server, "_require_key", _key)
    monkeypatch.setattr("mcp_server.tasks.require_task_principal", _principal)
    monkeypatch.setattr("mcp_server.tasks.list_variants", lambda *_a, **_k: [])
    monkeypatch.setenv(sr.SETUP_URL_ENV, PORTAL)

    def _with(profiles: set[str], *, has_default: bool = True):
        monkeypatch.setattr(server._state, "pool", _pool(profiles, has_default=has_default))

    return SimpleNamespace(use=_with)


#: One recipe per profile-aware tool: the arguments that get past input validation to the profile step.
RECIPES = {
    "radar_check": {"url": "", "title": "a story", "profile": "acme"},
    "profile_context": {"profile": "acme"},
    "draft_post": {"brief": "a brief", "profile": "acme"},
    "draft_outreach": {"brief": "a brief", "profile": "acme"},
    "describe": {},
    "dispatch": {"pack": "prospect"},
}


def _call(name: str, **override):
    kwargs = {**RECIPES[name], **override}
    return getattr(server, name)(ctx=MagicMock(), **kwargs)


# ── the registry is fully classified ─────────────────────────────────────────────────────────────


def test_every_registered_tool_is_classified_one_way_or_the_other():
    registered = {tool.name for tool in _run(server.mcp.list_tools())}
    assert registered == sr.SETUP_AWARE | sr.PROFILE_FREE, (
        "a tool was added or removed — decide whether it needs a profile and classify it"
    )
    assert not sr.SETUP_AWARE & sr.PROFILE_FREE


def test_every_profile_aware_tool_has_a_call_recipe_in_this_test():
    assert set(RECIPES) == sr.SETUP_AWARE


def test_the_profile_free_list_is_the_closed_set_the_plan_decided():
    assert sr.PROFILE_FREE == {
        "status",
        "artifacts",
        "read_artifact",
        "cost",
        "get_prospect_account",
        "list_prospect_accounts",
        "check_suppression",
    }


# ── a workspace with no profile gets the typed error from every profile-aware tool ───────────────


@pytest.mark.parametrize("tool", sorted(RECIPES))
def test_a_workspace_with_no_profile_gets_setup_required(world, tool):
    world.use(set())
    with pytest.raises(sr.SetupRequiredError) as err:
        _run(_call(tool))
    assert err.value.code == "setup_required"
    assert str(err.value).startswith("setup_required:")
    assert PORTAL in str(err.value)


def test_it_is_still_a_value_error_so_every_existing_handler_keeps_working():
    assert issubclass(sr.SetupRequiredError, ValueError)


# ── the controls: it is not handed to everybody ──────────────────────────────────────────────────


@pytest.mark.parametrize("tool", ["radar_check", "profile_context", "draft_post", "draft_outreach"])
def test_a_workspace_with_a_profile_asking_for_the_wrong_one_keeps_the_generic_refusal(world, tool):
    world.use({"acme"})
    with pytest.raises(ValueError, match="Profile 'other-corp' is not bound") as err:
        _run(_call(tool, profile="other-corp"))
    assert not isinstance(err.value, sr.SetupRequiredError)


@pytest.mark.parametrize("tool", ["radar_check", "profile_context"])
def test_a_finished_workspace_asking_for_its_own_profile_is_not_told_to_set_up(
    world, tool, monkeypatch, tmp_path
):
    world.use({"acme"})
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path))
    try:
        _run(_call(tool))
    except sr.SetupRequiredError:
        pytest.fail("a workspace that finished onboarding was told to finish setup")
    except Exception:  # noqa: BLE001 — a missing file is fine here; only the setup error is wrong
        pass


def test_a_finished_workspace_describe_is_not_a_setup_error(world):
    world.use({"acme"})
    assert _run(_call("describe")) == {"packs": []}


def test_a_workspace_with_profiles_but_none_marked_default_gets_the_plain_empty_list(world):
    """The setup error is for a workspace with NO profile. One that has profiles and simply has not
    picked a default is not un-onboarded, and telling it to "finish setup" would be wrong."""
    world.use({"acme"}, has_default=False)
    assert _run(_call("describe")) == {"packs": []}


def test_the_binding_check_distinguishes_the_two_cases_directly():
    with pytest.raises(sr.SetupRequiredError):
        _run(assert_workspace_profile(WS, "acme", _pool(set())))
    with pytest.raises(ValueError, match="not bound") as err:
        _run(assert_workspace_profile(WS, "other-corp", _pool({"acme"})))
    assert not isinstance(err.value, sr.SetupRequiredError)
    _run(assert_workspace_profile(WS, "acme", _pool({"acme"})))  # bound: passes


def test_dispatch_for_a_workspace_with_a_profile_keeps_its_own_errors(world):
    """The setup error replaces a failure only when there is no profile to blame."""
    world.use({"acme"})
    with pytest.raises(ValueError) as err:
        _run(_call("dispatch", pack="no-such-pack"))
    assert not isinstance(err.value, sr.SetupRequiredError)


# ── the text ─────────────────────────────────────────────────────────────────────────────────────


def test_the_text_points_at_the_portal_from_the_setting_and_says_what_to_do(monkeypatch):
    monkeypatch.setenv(sr.SETUP_URL_ENV, PORTAL)
    text = sr.setup_message()
    assert PORTAL in text
    assert "setup" in text.lower() and "retry" in text.lower()


def test_with_no_setting_there_is_no_link_and_a_warning_never_an_invented_one(monkeypatch, caplog):
    monkeypatch.delenv(sr.SETUP_URL_ENV, raising=False)
    with caplog.at_level(logging.WARNING):
        text = sr.setup_message()
    assert "http" not in text.lower()
    assert any(sr.SETUP_URL_ENV in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw", ["", "   ", "javascript:alert(1)", "ftp://x", "not a url"])
def test_a_setting_that_is_not_an_https_link_is_treated_as_absent(monkeypatch, raw):
    """The text is shown to a developer as a place to click: only an http(s) URL is ever echoed."""
    monkeypatch.setenv(sr.SETUP_URL_ENV, raw)
    assert "://" not in sr.setup_message().replace("setup_required", "")


# ── the compose passthrough ──────────────────────────────────────────────────────────────────────


@pytest.mark.private_tree  # reads deploy/, which the public carve does not ship
def test_the_setting_is_passed_to_the_mcp_service_with_an_empty_default():
    from pathlib import Path

    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text()
    )
    env = compose["services"]["mcp"]["environment"]
    env = env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)
    assert env.get(sr.SETUP_URL_ENV) == f"${{{sr.SETUP_URL_ENV}:-}}"


# ── a key with no bound agent: the first-run symptom of the same cause ───────────────────────────────


def _key_pool(profiles: set[str]):
    """``_pool`` plus a resolvable API key that has no bound agent."""
    pool = _pool(profiles)
    conn = pool.acquire.return_value.__aenter__.return_value
    inner = conn.fetchrow.side_effect

    def _fetchrow(query, *args):
        if "resolve_api_key" in query:
            return {"key_id": "k", "workspace_id": WS, "entitlement": "pro", "agent_id": None}
        return inner(query, *args)

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


def _ctx_with_key():
    ctx = MagicMock()
    ctx.request_context.request.headers = {"authorization": "Bearer sk-unbound"}
    return ctx


def test_an_unbound_key_in_a_workspace_with_no_profile_is_told_to_finish_setup():
    from mcp_server.tasks import require_task_principal

    with pytest.raises(sr.SetupRequiredError):
        _run(require_task_principal(_ctx_with_key(), _key_pool(set())))


def test_an_unbound_key_in_a_workspace_that_has_a_profile_keeps_its_own_refusal():
    from mcp_server.tasks import require_task_principal

    with pytest.raises(ValueError, match="api_key_unbound") as err:
        _run(require_task_principal(_ctx_with_key(), _key_pool({"acme"})))
    assert not isinstance(err.value, sr.SetupRequiredError)
