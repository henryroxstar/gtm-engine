"""ON-06 — onboarding errors tell "fix your input" (422) from "the service can't do this
right now" (503) and "the model or upstream returned something unusable" (502) by
``error.code``; a promote conflict carries its own code, and anything else is a 500.

The real producers run (``agent.onboard.ingest`` / ``extract`` / ``extract_product`` /
``promote``) so the status is bound to the exception each raise site actually throws.
Every paid path is stubbed to fail loudly: no network, no brain call, no model key.

Ingest and re-extract run as background jobs (issue #259): the status and envelope asserted
here are the ones the finished job records, which a client reads from
``GET /onboard/jobs/{job_id}``.
"""

from __future__ import annotations

import importlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.errors import register_error_handlers
from backend.routers import onboard
from tests.backend.test_onboard_http import settle

_WS = uuid.UUID("22222222-2222-2222-2222-222222222222")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT = importlib.import_module("agent.onboard.extract")
_OPERATOR_TOKENS = ("FIRECRAWL_API_KEY", "GTM_ONBOARDING_CAP_USD", "Doppler", ".env", "costs.jsonl")
_REGISTRY_TOKENS = ("content-creation", "content-radar", "Known:")
_UNKNOWN_CAPABILITY = "orbital-teleportation"


def _draft(**overrides) -> dict:
    draft = {
        "source": "text",
        "confidence": "high",
        "company": {"name": "Riverbend Logistics"},
        "voice": {},
        "icp": {},
        "competitors": [],
        "pillars": [],
        "products": [{"slug": "widget", "name": "Widget", "capabilities": []}],
        "brand": {},
        "gaps": [],
    }
    draft.update(overrides)
    return draft


def _brain_returns(monkeypatch, text: str) -> None:
    async def _reply(prompt, cfg):
        return text

    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _reply)


def _ws():
    ws = MagicMock()
    ws.workspace_id = _WS
    return ws


@pytest.fixture
def cfg(tmp_path):
    return SimpleNamespace(
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
        plugin_path=_REPO_ROOT / "plugin",
        repo_root=_REPO_ROOT,
        firecrawl_api_key=None,
        onboarding_cap_usd=None,
        model=None,
    )


class _NoDbConn:
    """Promote's profiles-row write is not what these tests pin — accept it silently."""

    async def execute(self, sql, *args):
        return "OK"

    async def fetchval(self, sql, *args):
        return False

    @asynccontextmanager
    async def transaction(self):
        yield


@asynccontextmanager
async def _no_db_scope(pool, workspace_id):
    yield _NoDbConn()


class _SettlingClient:
    """A TestClient whose POSTs follow an onboarding job to its end."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def post(self, *args, **kwargs):
        return settle(self._client, self._client.post(*args, **kwargs))


@pytest.fixture
def client(cfg, monkeypatch):
    async def _cfg(request, ws):
        return cfg

    def _no_network(*args, **kwargs):
        raise AssertionError("onboarding test reached the network")

    async def _no_brain(prompt, cfg):
        raise AssertionError("onboarding test reached the brain")

    monkeypatch.setattr(onboard, "_get_cfg", _cfg)
    monkeypatch.setattr("httpx.Client", _no_network)
    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _no_brain)
    monkeypatch.setattr(onboard, "_drafts", {})
    monkeypatch.setattr(onboard, "workspace_scope", _no_db_scope)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(onboard.router)
    app.dependency_overrides[require_auth] = _ws
    app.state.pool = object()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield _SettlingClient(test_client)


def _register_draft(tmp_path: Path) -> str:
    draft_id = str(uuid.uuid4())
    staged_root = tmp_path / "profiles" / ".staging" / "riverbend-logistics"
    staged_root.mkdir(parents=True)
    (staged_root / ".onboard-meta.json").write_text(json.dumps({"draft_id": draft_id}))
    onboard._drafts[draft_id] = {
        "slug": "riverbend-logistics",
        "staged_root": staged_root,
        "draft": {
            "company": {"name": "Riverbend Logistics"},
            "products": [{"slug": "widget", "name": "Widget"}],
        },
        "workspace_id": _WS,
    }
    return draft_id


def _assert_error(res, status: int, code: str, *leaks: str) -> None:
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
    for token in (*_OPERATOR_TOKENS, *_REGISTRY_TOKENS, *leaks):
        assert token not in res.text


def _assert_unavailable(res, code: str) -> None:
    _assert_error(res, 503, code)


# ── POST /onboard ─────────────────────────────────────────────────────────────


def test_ingest_url_without_platform_key_is_503(client):
    res = client.post("/onboard", json={"source_type": "url", "source": "https://example.com"})
    _assert_unavailable(res, "url_ingest_unavailable")


def test_ingest_url_over_onboarding_cap_is_503(client, cfg):
    cfg.firecrawl_api_key = "fc-test"
    cfg.onboarding_cap_usd = 0.0
    res = client.post("/onboard", json={"source_type": "url", "source": "https://example.com"})
    _assert_unavailable(res, "onboarding_cap_reached")


def test_extract_over_onboarding_cap_is_503(client, cfg):
    cfg.onboarding_cap_usd = 0.0
    res = client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    _assert_unavailable(res, "onboarding_cap_reached")


def test_ingest_unreadable_text_is_422(client):
    res = client.post("/onboard", json={"source_type": "text", "source": "   "})
    _assert_error(res, 422, "onboarding_input_invalid")
    assert "no readable text" in res.json()["error"]["message"]


def test_ingest_unparseable_upstream_response_is_502(client, cfg, monkeypatch):
    import json as _json

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            raise _json.JSONDecodeError("Expecting value", "<html>upstream page</html>", 0)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    cfg.firecrawl_api_key = "fc-test"
    monkeypatch.setattr("httpx.Client", _Client)
    res = client.post("/onboard", json={"source_type": "url", "source": "https://example.com"})
    _assert_error(res, 502, "url_ingest_failed", "Expecting value", "upstream page")


class _CrawlClient:
    """httpx.Client stand-in: the crawl is accepted, then every poll answers ``status``."""

    status = "scraping"

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        return _JsonResponse({"id": "crawl-1"})

    def get(self, *args, **kwargs):
        return _JsonResponse({"status": self.status})


class _JsonResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_ingest_url_crawl_past_its_deadline_is_504(client, cfg, monkeypatch):
    """Running out of polls used to return no pages, which then read as the caller's
    unreadable text (422)."""
    cfg.firecrawl_api_key = "fc-test"
    monkeypatch.setattr("httpx.Client", _CrawlClient)
    monkeypatch.setattr("gtm_core.ingest.CRAWL_DEADLINE_S", 0.0)
    res = client.post("/onboard", json={"source_type": "url", "source": "https://example.com"})
    _assert_error(res, 504, "url_ingest_timeout", "Firecrawl", "crawl-1")


def test_ingest_url_crawl_reported_failed_is_502(client, cfg, monkeypatch):
    class _FailedCrawl(_CrawlClient):
        status = "failed"

    cfg.firecrawl_api_key = "fc-test"
    monkeypatch.setattr("httpx.Client", _FailedCrawl)
    res = client.post("/onboard", json={"source_type": "url", "source": "https://example.com"})
    _assert_error(res, 502, "url_ingest_failed", "Firecrawl", "crawl-1")


def test_extract_non_json_model_output_is_502(client, monkeypatch):
    _brain_returns(monkeypatch, "not a profile")
    res = client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    _assert_error(res, 502, "onboarding_extract_failed", "not a profile", "invalid JSON")


def test_extract_unknown_capability_is_502_without_the_registry(client, monkeypatch):
    product = {"slug": "widget", "name": "Widget", "capabilities": [_UNKNOWN_CAPABILITY]}
    _brain_returns(monkeypatch, json.dumps(_draft(products=[product])))
    res = client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    _assert_error(res, 502, "onboarding_extract_failed", _UNKNOWN_CAPABILITY)


def test_extract_company_name_without_a_slug_is_502(client, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft(company={"name": "*** ***"})))
    res = client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    _assert_error(res, 502, "onboarding_extract_failed", "*** ***", "slugify")


def test_corrupt_spend_ledger_is_500_not_input(client, cfg):
    from gtm_core.ledgers import _current_year_month

    ledger = cfg.content_root / "_system" / "costs.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {"ts": f"{_current_year_month()}-01", "event_type": "onboard.x", "cost_usd": "n/a"}
        )
    )
    cfg.onboarding_cap_usd = 5.0
    res = client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    _assert_error(res, 500, "internal_error", "n/a")


# ── POST /onboard/{draft_id}/product/{slug}/extract ───────────────────────────


def test_re_extract_url_without_platform_key_is_503(client, tmp_path):
    draft_id = _register_draft(tmp_path)
    res = client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "url", "source": "https://example.com/widget"},
    )
    _assert_unavailable(res, "url_ingest_unavailable")


def test_re_extract_url_over_onboarding_cap_is_503(client, cfg, tmp_path):
    cfg.firecrawl_api_key = "fc-test"
    cfg.onboarding_cap_usd = 0.0
    draft_id = _register_draft(tmp_path)
    res = client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "url", "source": "https://example.com/widget"},
    )
    _assert_unavailable(res, "onboarding_cap_reached")


def test_re_extract_product_over_onboarding_cap_is_503(client, cfg, tmp_path):
    cfg.onboarding_cap_usd = 0.0
    draft_id = _register_draft(tmp_path)
    res = client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "text", "source": "Widget now ships an audit log."},
    )
    _assert_unavailable(res, "onboarding_cap_reached")


def test_re_extract_unknown_product_is_422(client, tmp_path):
    draft_id = _register_draft(tmp_path)
    res = client.post(
        f"/onboard/{draft_id}/product/gadget/extract",
        json={"source_type": "text", "source": "Gadget details."},
    )
    _assert_error(res, 422, "onboarding_input_invalid")


def test_re_extract_model_output_for_another_product_is_502(client, tmp_path, monkeypatch):
    draft_id = _register_draft(tmp_path)
    _brain_returns(monkeypatch, json.dumps({"slug": "decoy-product", "name": "Decoy"}))
    res = client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "text", "source": "Widget now ships an audit log."},
    )
    _assert_error(res, 502, "onboarding_extract_failed", "decoy-product")


# ── POST /onboard/{draft_id}/promote ──────────────────────────────────────────


def test_promote_over_existing_profile_is_409(client, cfg, tmp_path):
    draft_id = _register_draft(tmp_path)
    (cfg.profiles_root / "riverbend-logistics").mkdir(parents=True)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "profile_already_exists"
    assert str(tmp_path) not in res.text


def test_promote_of_an_unstaged_draft_is_409(client, tmp_path):
    draft_id = _register_draft(tmp_path)
    (onboard._drafts[draft_id]["staged_root"] / ".onboard-meta.json").unlink()
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "draft_not_staged"
    assert str(tmp_path) not in res.text


def test_promote_other_value_error_is_500(client, tmp_path, monkeypatch):
    def _broken(*args, **kwargs):
        raise ValueError("unexpected promote fault")

    monkeypatch.setattr("agent.onboard.promote", _broken)
    draft_id = _register_draft(tmp_path)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )
    _assert_error(res, 500, "internal_error", "unexpected promote fault")
