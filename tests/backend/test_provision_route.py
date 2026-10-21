"""W1 route shell and pure helpers (PRD 2026-10-19a; test plan P1-3, P1-4, the P1-8 hash rules, and the
"message is never empty" contract).

No database: the pool is a mock that must stay untouched. Everything that needs rows (idempotency, the lease,
RLS, crashes, the real onboarding job) is in ``test_provision_live.py``, because a mock cannot prove it.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import agents as agents_router  # noqa: E402
from backend.routers import provision as provision_router  # noqa: E402
from backend.services import provision as svc  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402

WS = "00000000-0000-0000-0000-0000000000aa"
GOOD = {"name": "Fixture Agent", "profile_name": "fixtureco", "key_label": "ci"}


def _app(*, authed: bool) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(provision_router.router, prefix="/v1")
    app.include_router(agents_router.router, prefix="/v1")
    app.state.pool = MagicMock(name="pool-must-stay-untouched")
    app.state.cfg = SimpleNamespace(repo_root=".")
    if authed:
        app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(
            user_id="u", workspace_id=WS, entitlement=Entitlement.PRO
        )
    return app


def _call(app: FastAPI, method: str, path: str, **kw) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, path, **kw)

    return asyncio.run(go())


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("GTM_AGENT_PROVISION_ENABLED", "1")


# ── P1-3: the kill switch and who may call ────────────────────────────────────


def test_a_closed_route_is_404_for_every_verb_even_without_a_token(monkeypatch):
    monkeypatch.delenv("GTM_AGENT_PROVISION_ENABLED", raising=False)
    app = _app(authed=False)
    jid = "11111111-1111-1111-1111-111111111111"
    assert _call(app, "POST", "/v1/agents/provision", json=GOOD).status_code == 404
    assert _call(app, "GET", f"/v1/agents/provision/{jid}").status_code == 404
    assert _call(app, "DELETE", f"/v1/agents/provision/{jid}").status_code == 404


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "ture", "2", "enabled"])
def test_only_an_unambiguous_on_opens_the_route(monkeypatch, value):
    monkeypatch.setenv("GTM_AGENT_PROVISION_ENABLED", value)
    assert provision_router.provision_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_the_unambiguous_on_values_open_it(monkeypatch, value):
    monkeypatch.setenv("GTM_AGENT_PROVISION_ENABLED", value)
    assert provision_router.provision_enabled() is True


def test_the_individual_routes_still_exist_when_the_switch_is_shut(monkeypatch):
    monkeypatch.delenv("GTM_AGENT_PROVISION_ENABLED", raising=False)
    app = _app(authed=False)
    # 401 (needs a token), not 404 (no such route): POST /v1/agents is untouched by the switch.
    assert _call(app, "POST", "/v1/agents", json={}).status_code == 401


def test_no_token_is_401_when_open(flag_on):
    r = _call(_app(authed=False), "POST", "/v1/agents/provision", json=GOOD)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "token_missing"


def test_a_service_principals_sk_token_is_401_not_admitted(flag_on):
    """The route is deliberately not on the require_principal allowlist: an ``sk-`` key fails the user-JWT
    decode, so a machine cannot mint itself a key."""
    r = _call(
        _app(authed=False),
        "POST",
        "/v1/agents/provision",
        json=GOOD,
        headers={"Authorization": "Bearer sk-test_fixture_service_key", "Idempotency-Key": "k1"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "token_invalid"


def test_the_route_is_not_on_the_principal_allowlist():
    from backend.callers import rest

    names = {
        getattr(dep.call, "__name__", "")
        for route in provision_router.router.routes
        for dep in route.dependant.dependencies
    }
    assert "require_auth" in names and "require_principal" not in names
    assert (
        rest.require_principal is not None
    )  # the allowlist exists; this route is simply not on it


# ── P1-4: input shape (422 before any database work) ──────────────────────────


@pytest.mark.parametrize(
    "extra",
    [
        {"profile_name": None},  # neither
        {"source_url": "https://fixtureco.example"},  # both (profile_name is in GOOD)
        {"profile_name": None, "source_url": "ftp://fixtureco.example"},  # not http(s)
        {"profile_name": None, "source_url": "not a url"},
        {"profile_name": "   "},  # blank
    ],
)
def test_neither_or_both_or_a_bad_source_is_422_provision_input_invalid(flag_on, extra):
    app = _app(authed=True)
    r = _call(
        app,
        "POST",
        "/v1/agents/provision",
        json={**GOOD, **extra},
        headers={"Idempotency-Key": "k1"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "provision_input_invalid"
    assert app.state.pool.mock_calls == []  # nothing touched the database


@pytest.mark.parametrize("header", [None, "", "has space", "x" * 129, "bad/slash"])
def test_a_missing_or_malformed_idempotency_key_is_422(flag_on, header):
    headers = {} if header is None else {"Idempotency-Key": header}
    app = _app(authed=True)
    r = _call(app, "POST", "/v1/agents/provision", json=GOOD, headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_invalid"
    assert app.state.pool.mock_calls == []


def test_an_unknown_provider_is_a_validation_error_that_does_not_echo_the_key(flag_on):
    body = {**GOOD, "integrations": {"not_a_provider": "test_fixture_secret_value"}}
    r = _call(
        _app(authed=True),
        "POST",
        "/v1/agents/provision",
        json=body,
        headers={"Idempotency-Key": "k"},
    )
    assert r.status_code == 422
    assert "test_fixture_secret_value" not in r.text


# ── P1-8: what an idempotency key is bound to ─────────────────────────────────


def _req(**kw) -> svc.ProvisionRequest:
    return svc.ProvisionRequest(**{**GOOD, **kw})


def _hash(**kw) -> str:
    req = _req(**kw)
    return svc.request_hash(req, svc.normalise_source(req))


def test_a_reordered_packs_list_hashes_equal():
    assert _hash(packs=["b", "a"]) == _hash(packs=["a", "b"])


def test_none_packs_and_an_empty_list_are_different_requests():
    assert _hash(packs=None) != _hash(packs=[])  # "every pack" is not "no pack"


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Other Agent"},
        {"packs": ["x"]},
        {"monthly_budget_usd": 5.0},
        {"daily_dispatch_cap": 3},
        {"profile_name": "otherco"},
        {"profile_name": None, "source_url": "https://fixtureco.example"},
        {"key_label": "other"},
        {"integrations": {"apollo": "test_fixture_key_1"}},
    ],
)
def test_a_different_request_hashes_differently(change):
    assert _hash(**change) != _hash()


def test_a_corrected_integration_key_value_and_reissue_do_not_change_the_hash():
    a = _hash(integrations={"apollo": "test_fixture_key_1"})
    assert a == _hash(integrations={"apollo": "test_fixture_key_2"})  # a corrected value is allowed
    assert a == _hash(integrations={"apollo": "test_fixture_key_1"}, reissue_key=True)


def test_the_provider_set_is_in_the_hash():
    assert _hash(integrations={"apollo": "test_x"}) != _hash(
        integrations={"apollo": "test_x", "syften": "test_y"}
    )


# ── P1-15 (pure half): the source identity ────────────────────────────────────


@pytest.mark.parametrize(
    "variant",
    [
        "https://Fixtureco.example/About",
        "http://fixtureco.example/about/",
        "https://fixtureco.example/about//",
        "HTTPS://FIXTURECO.EXAMPLE/ABOUT",
    ],
)
def test_url_case_scheme_and_trailing_slash_are_one_source(variant):
    canonical = svc.normalise_source(
        _req(profile_name=None, source_url="https://fixtureco.example/about")
    )
    assert svc.normalise_source(_req(profile_name=None, source_url=variant)) == canonical


def test_a_query_string_is_part_of_the_source():
    a = svc.normalise_source(_req(profile_name=None, source_url="https://fixtureco.example/p?id=1"))
    b = svc.normalise_source(_req(profile_name=None, source_url="https://fixtureco.example/p?id=2"))
    assert a != b


def test_a_profile_named_like_a_domain_does_not_collide_with_that_url():
    p = svc.normalise_source(_req(profile_name="fixtureco.example"))
    u = svc.normalise_source(_req(profile_name=None, source_url="https://fixtureco.example"))
    assert p != u


# ── secrets ───────────────────────────────────────────────────────────────────


def test_integration_values_never_appear_in_a_repr_or_dump():
    req = _req(integrations={"apollo": "test_fixture_secret_value"})
    for text in (repr(req), str(req), req.model_dump_json(), str(req.model_dump())):
        assert "test_fixture_secret_value" not in text


# ── the response contract ─────────────────────────────────────────────────────


def _job(state: str, **kw) -> dict:
    from datetime import UTC, datetime

    base = {
        "id": "22222222-2222-2222-2222-222222222222",
        "state": state,
        "step": "precheck",
        "blockers": "[]",
        "errors": "[]",
        "expires_at": datetime.now(UTC),
        "agent_id": "33333333-3333-3333-3333-333333333333",
        "agent_name": "Fixture Agent",
        "profile_slug": "fixtureco",
        "key_id": None,
        "key_prefix": None,
    }
    return {**base, **kw}


@pytest.mark.parametrize("state", sorted(svc.ALL_STATES))
def test_every_state_answers_in_words_with_a_next_move(state):
    extra = {}
    if state == "ready_with_blockers":
        extra = {"blockers": '[{"pack":"p","how_to_fix":"do x"}]'}
    if state == "failed":
        extra = {"errors": '[{"code":"c","message":"It broke."}]'}
    code, body = svc._view(_job(state, **extra), draft_id="44444444-4444-4444-4444-444444444444")
    assert body["message"].strip()
    assert body.get("next_action") or body.get("next_steps"), (
        f"{state} leaves the caller without a next move"
    )
    assert code == (202 if state in svc.ACTIVE else 200)
    assert body["ready"] is (state == "ready")


def test_a_parked_job_names_the_draft_and_the_promote_step():
    _, body = svc._view(
        _job("awaiting_profile_confirmation"), draft_id="44444444-4444-4444-4444-444444444444"
    )
    action = body["next_action"]
    assert action["kind"] == "confirm_profile"
    assert action["draft_id"] == "44444444-4444-4444-4444-444444444444"
    assert "promote" in action["then"]


def test_the_raw_key_is_in_the_view_only_when_this_call_minted_it():
    job = _job("ready", key_id="55555555-5555-5555-5555-555555555555", key_prefix="sk-abcd12")
    assert "raw_key" not in svc._view(job)[1]["key"]
    assert (
        svc._view(job, raw_key="sk-test_fixture_raw")[1]["key"]["raw_key"] == "sk-test_fixture_raw"
    )


def test_an_unknown_state_is_refused_not_treated_as_fine():
    with pytest.raises(RuntimeError, match="unknown provisioning state"):
        asyncio.run(
            svc._advance(
                MagicMock(),
                SimpleNamespace(),
                WorkspaceCtx("u", WS, Entitlement.PRO),
                _job("bogus"),
                _req(),
                None,
            )
        )
