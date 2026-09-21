"""ON-05 — API onboarding must render the ``_template`` knowledge starters, exactly like
``agent/onboard_cli.py`` does for the CLI path, so an API-onboarded profile doesn't
silently degrade the packs that read those starters (content-priority, hook-matrix,
social-tuning, inbound-triage-rubric).

Every paid path is stubbed to fail loudly: no network, no brain call, no model key.
"""

from __future__ import annotations

import importlib
import json
import time
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
from backend.services import onboard_jobs

_WS = uuid.UUID("22222222-2222-2222-2222-222222222222")
_WS_B = uuid.UUID("33333333-3333-3333-3333-333333333333")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT = importlib.import_module("agent.onboard.extract")
_TEMPLATE_STARTERS = ("knowledge/content-priority.md", "knowledge/hook-matrix.md")


def _draft(**overrides) -> dict:
    """A draft the real extractor would accept.

    It used to be a skeleton — empty `voice`/`icp`/`brand`, no `markets`, no product
    `description` — because nothing between the brain and `render()` checked the draft's shape.
    Since 2026-09-17 `_parse_and_validate_draft` validates against profile-draft.schema.json
    (issue #267), so a skeleton here would make every test in this file a 502 about the fixture
    rather than about the route under test.
    """
    draft = {
        "source": {"type": "text", "value": "We move freight for regional carriers."},
        "confidence": "high",
        "company": {
            "name": "Riverbend Logistics",
            "slug": "riverbend-logistics",
            "brand_name": "Riverbend",
            "description": "We move freight for regional carriers.",
            "markets": ["United States"],
            "social_handle": "",
        },
        "voice": {
            "tone": "Plain and operational.",
            "principles": ["Be concrete.", "Lead with the load.", "No jargon."],
            "ban_list": [],
            "examples": [],
        },
        "icp": {
            "personas": [
                {
                    "title": "VP Operations",
                    "pain_points": ["Empty backhauls"],
                    "goals": ["Higher trailer utilisation"],
                }
            ],
            "verticals": [],
            "company_size": "",
        },
        "competitors": [],
        "pillars": ["Freight operations", "Carrier networks"],
        "products": [
            {
                "slug": "widget",
                "name": "Widget",
                "description": "Load-matching for regional carriers.",
                "capabilities": [],
                "use_cases": ["Fill a backhaul"],
                "references": [],
            }
        ],
        "brand": {"palette": ["#000000"]},
        "gaps": [],
    }
    draft.update(overrides)
    return draft


def _re_extracted_product(**overrides) -> dict:
    """What the brain returns from a product re-extract.

    `extract_product` validates the MERGED draft against profile-draft.schema.json (issue #267) —
    the merged draft is what `render()` is handed — so a bare `{slug, name, capabilities}` stub
    would be refused for the same missing keys as any other product.
    """
    product = {**_draft()["products"][0], **overrides}
    return product


class _Settled:
    """A finished onboarding job, shaped like the synchronous response it replaced: a
    succeeded job is a 200 with the draft; a failed one is the error status and envelope."""

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


def settle(client, res, *, timeout_s: float = 10.0):
    """Follow a 202 onboarding job to its end. Any other response (a refusal made before a
    job exists: 404, a validation 422) is returned as is."""
    if res.status_code != 202:
        return res
    job = res.json()
    deadline = time.monotonic() + timeout_s
    while job["status"] in ("pending", "running"):
        assert time.monotonic() < deadline, f"onboarding job never finished: {job}"
        time.sleep(0.01)
        polled = client.get(f"/onboard/jobs/{job['job_id']}")
        assert polled.status_code == 200, polled.text
        job = polled.json()
    if job["status"] == "succeeded":
        assert job["error"] is None
        return _Settled(200, job["result"])
    error = job["error"]
    assert job["result"] is None
    return _Settled(
        error["status"], {"error": {"code": error["code"], "message": error["message"]}}
    )


def _brain_returns(monkeypatch, text: str) -> None:
    async def _reply(prompt, cfg):
        return text

    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _reply)


def _workspace_cfg(tmp_path: Path, workspace_id: uuid.UUID) -> SimpleNamespace:
    """The shape ``_get_cfg`` returns: roots under ``data/workspaces/<ws>/``, as production."""
    ws_root = tmp_path / "data" / "workspaces" / str(workspace_id)
    return SimpleNamespace(
        content_root=ws_root / "content",
        profiles_root=ws_root / "profiles",
        plugin_path=_REPO_ROOT / "plugin",
        repo_root=_REPO_ROOT,
        firecrawl_api_key=None,
        onboarding_cap_usd=None,
        model=None,
    )


@pytest.fixture
def cfg(tmp_path):
    return _workspace_cfg(tmp_path, _WS)


@pytest.fixture
def cfg_b(tmp_path):
    return _workspace_cfg(tmp_path, _WS_B)


@pytest.fixture
def acting():
    """Which workspace the next request authenticates as; tests flip ``workspace_id``."""
    return SimpleNamespace(workspace_id=_WS)


class _FakeConn:
    """Records every statement; ``fetchval`` answers the is_default read-back."""

    def __init__(self, scope: _FakeScope) -> None:
        self._scope = scope

    async def execute(self, sql, *args):
        self._scope.statements.append((" ".join(sql.split()), args))
        return "OK"

    async def fetchval(self, sql, *args):
        self._scope.statements.append((" ".join(sql.split()), args))
        return self._scope.is_default

    @asynccontextmanager
    async def transaction(self):
        yield


class _FakeScope:
    """Stands in for ``workspace_scope``: one transaction that commits on a clean exit
    and rolls back when the body raises — the property promote's atomicity rests on."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.scoped_to: list[str] = []
        self.outcomes: list[str] = []
        self.is_default = True

    @asynccontextmanager
    async def __call__(self, pool, workspace_id):
        self.scoped_to.append(workspace_id)
        try:
            yield _FakeConn(self)
        except BaseException:
            self.outcomes.append("rolled_back")
            raise
        self.outcomes.append("committed")


@pytest.fixture
def db_scope(monkeypatch):
    scope = _FakeScope()
    monkeypatch.setattr(onboard, "workspace_scope", scope)
    return scope


@pytest.fixture
def client(cfg, cfg_b, acting, monkeypatch, db_scope):
    cfgs = {_WS: cfg, _WS_B: cfg_b}

    def _ws():
        ws = MagicMock()
        ws.workspace_id = acting.workspace_id
        return ws

    async def _cfg(request, ws):
        # Scoped by the authenticated workspace, exactly as the real _get_cfg is.
        return cfgs[ws.workspace_id]

    def _no_network(*args, **kwargs):
        raise AssertionError("onboarding test reached the network")

    async def _no_brain(prompt, cfg):
        raise AssertionError("onboarding test reached the brain")

    monkeypatch.setattr(onboard, "_get_cfg", _cfg)
    monkeypatch.setattr("httpx.Client", _no_network)
    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _no_brain)
    monkeypatch.setattr(onboard, "_drafts", {})

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(onboard.router)
    app.dependency_overrides[require_auth] = _ws
    app.state.pool = object()
    # One portal for the whole test, so a job's background task outlives its POST.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ── POST /onboard ─────────────────────────────────────────────────────────────


def test_ingest_stages_the_template_knowledge_starters(client, cfg, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft()))
    res = settle(
        client, client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    )

    assert res.status_code == 200
    body = res.json()
    for starter in _TEMPLATE_STARTERS:
        assert starter in body["staged_files"]

    staged_root = cfg.profiles_root / ".staging" / "riverbend-logistics"
    for starter in _TEMPLATE_STARTERS:
        assert (staged_root / starter).exists()


# ── POST /onboard/{draft_id}/product/{slug}/extract ───────────────────────────


def test_re_extract_product_restages_the_template_knowledge_starters(client, cfg, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft()))
    ingest_res = settle(
        client, client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    )
    assert ingest_res.status_code == 200
    draft_id = ingest_res.json()["draft_id"]

    updated_product = _re_extracted_product()
    _brain_returns(monkeypatch, json.dumps(updated_product))
    res = settle(
        client,
        client.post(
            f"/onboard/{draft_id}/product/widget/extract",
            json={"source_type": "text", "source": "Widget now ships an audit log."},
        ),
    )

    assert res.status_code == 200
    body = res.json()
    for starter in _TEMPLATE_STARTERS:
        assert starter in body["staged_files"]

    staged_root = cfg.profiles_root / ".staging" / "riverbend-logistics"
    for starter in _TEMPLATE_STARTERS:
        assert (staged_root / starter).exists()


# ── POST /onboard/{draft_id}/promote ──────────────────────────────────────────


def _ingest(client, monkeypatch) -> str:
    _brain_returns(monkeypatch, json.dumps(_draft()))
    res = settle(
        client, client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    )
    assert res.status_code == 200
    return res.json()["draft_id"]


def _profile_inserts(scope: _FakeScope) -> list[tuple]:
    return [args for sql, args in scope.statements if sql.startswith("INSERT INTO profiles")]


def test_promote_registers_the_profile_row_in_the_callers_workspace(
    client, cfg, db_scope, monkeypatch
):
    draft_id = _ingest(client, monkeypatch)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "riverbend-logistics"
    assert body["status"] == "promoted"
    assert body["is_default"] is True
    assert (cfg.profiles_root / "riverbend-logistics").is_dir()
    assert db_scope.scoped_to == [str(_WS)]
    assert _profile_inserts(db_scope) == [(str(_WS), "riverbend-logistics")]
    assert db_scope.outcomes == ["committed"]


def test_promote_echoes_is_default_as_read_back_from_the_database(client, db_scope, monkeypatch):
    db_scope.is_default = False
    draft_id = _ingest(client, monkeypatch)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )

    assert res.status_code == 200
    assert res.json()["is_default"] is False


def test_a_conflicting_promote_rolls_the_profile_row_back(client, cfg, db_scope, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    (cfg.profiles_root / "riverbend-logistics").mkdir(parents=True)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "profile_already_exists"
    assert db_scope.outcomes == ["rolled_back"]
    assert "committed" not in db_scope.outcomes


# ── M-10: a draft outlives the process that staged it ─────────────────────────
#
# ``_drafts`` is process memory. Clearing it is what an API restart or redeploy does to it;
# the staged tree on disk is what survives, so every endpoint must resolve a draft from there.


def _restart() -> None:
    onboard._drafts.clear()


def _staged_root(cfg) -> Path:
    return cfg.profiles_root / ".staging" / "riverbend-logistics"


def test_ingest_persists_the_draft_beside_the_staged_tree(client, cfg, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft()))
    res = settle(
        client, client.post("/onboard", json={"source_type": "text", "source": "We build widgets."})
    )
    assert res.status_code == 200

    persisted = _staged_root(cfg) / ".draft.json"
    assert persisted.is_file()
    assert json.loads(persisted.read_text(encoding="utf-8"))["company"]["name"] == (
        "Riverbend Logistics"
    )
    assert ".draft.json" not in res.json()["staged_files"]

    diff = client.get(f"/onboard/{res.json()['draft_id']}/diff")
    assert diff.status_code == 200
    assert ".draft.json" not in diff.json()["diffs"]
    assert ".onboard-meta.json" not in diff.json()["diffs"]


def test_diff_resolves_a_draft_after_a_restart(client, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    _restart()

    res = client.get(f"/onboard/{draft_id}/diff")

    assert res.status_code == 200
    assert res.json()["slug"] == "riverbend-logistics"
    assert res.json()["draft_id"] == draft_id
    assert "PROFILE.md" in res.json()["diffs"]


def test_promote_resolves_a_draft_after_a_restart(client, cfg, db_scope, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    _restart()

    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )

    assert res.status_code == 200
    assert res.json()["slug"] == "riverbend-logistics"
    live = cfg.profiles_root / "riverbend-logistics"
    assert (live / "PROFILE.md").is_file()
    assert not (live / ".draft.json").exists()
    assert not (live / ".onboard-meta.json").exists()
    assert not _staged_root(cfg).exists()
    assert _profile_inserts(db_scope) == [(str(_WS), "riverbend-logistics")]


def test_promote_after_a_restart_still_checks_the_confirmed_name(client, cfg, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    _restart()

    res = client.post(f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Other Co"})

    assert res.status_code == 422
    assert _staged_root(cfg).is_dir()


def test_cancel_resolves_a_draft_after_a_restart(client, cfg, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    _restart()

    res = client.delete(f"/onboard/{draft_id}")

    assert res.status_code == 204
    assert not _staged_root(cfg).exists()
    assert client.get(f"/onboard/{draft_id}/diff").status_code == 404


def test_re_extract_resolves_a_draft_after_a_restart_and_persists_the_new_one(
    client, cfg, monkeypatch
):
    draft_id = _ingest(client, monkeypatch)
    _restart()

    updated_product = _re_extracted_product()
    _brain_returns(monkeypatch, json.dumps(updated_product))
    res = settle(
        client,
        client.post(
            f"/onboard/{draft_id}/product/widget/extract",
            json={"source_type": "text", "source": "Widget now ships an audit log."},
        ),
    )
    assert res.status_code == 200
    new_id = res.json()["draft_id"]
    assert new_id != draft_id
    assert (_staged_root(cfg) / ".draft.json").is_file()

    _restart()
    assert client.get(f"/onboard/{new_id}/diff").status_code == 200
    assert client.get(f"/onboard/{draft_id}/diff").status_code == 404


def _persisted_missing(path: Path) -> None:
    path.unlink()


def _persisted_corrupt(path: Path) -> None:
    path.write_text("{not json", encoding="utf-8")


def _persisted_not_a_dict(path: Path) -> None:
    path.write_text(json.dumps(["Riverbend Logistics"]), encoding="utf-8")


def _persisted_without_a_company_name(path: Path) -> None:
    path.write_text(json.dumps(_draft(company={"slug": "riverbend-logistics"})), encoding="utf-8")


def _persisted_with_a_blank_company_name(path: Path) -> None:
    path.write_text(json.dumps(_draft(company={"name": "  "})), encoding="utf-8")


def _persisted_unreadable_bytes(path: Path) -> None:
    path.write_bytes(b"\xff\xfe\x00not utf-8")


@pytest.mark.parametrize(
    "damage",
    [
        _persisted_missing,
        _persisted_corrupt,
        _persisted_not_a_dict,
        _persisted_without_a_company_name,
        _persisted_with_a_blank_company_name,
        _persisted_unreadable_bytes,
    ],
)
def test_a_staged_dir_without_a_usable_draft_is_not_found_after_a_restart(
    client, cfg, monkeypatch, damage
):
    draft_id = _ingest(client, monkeypatch)
    damage(_staged_root(cfg) / ".draft.json")
    _restart()

    for res in (
        client.get(f"/onboard/{draft_id}/diff"),
        client.post(
            f"/onboard/{draft_id}/promote",
            json={"confirmed_company_name": "Riverbend Logistics"},
        ),
        client.delete(f"/onboard/{draft_id}"),
    ):
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "draft_not_found"
    assert (_staged_root(cfg) / "PROFILE.md").is_file()


def test_a_staged_dir_without_its_meta_is_not_found_after_a_restart(client, cfg, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    (_staged_root(cfg) / ".onboard-meta.json").unlink()
    _restart()

    res = client.get(f"/onboard/{draft_id}/diff")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "draft_not_found"


def test_an_unknown_draft_id_is_not_found(client, monkeypatch):
    _ingest(client, monkeypatch)
    _restart()

    res = client.get(f"/onboard/{uuid.uuid4()}/diff")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "draft_not_found"


def _plant_staged_dir(staging: Path, name: str, draft_id: str) -> Path:
    planted = staging / name
    planted.mkdir(parents=True)
    (planted / ".onboard-meta.json").write_text(json.dumps({"draft_id": draft_id}))
    (planted / ".draft.json").write_text(json.dumps(_draft()))
    (planted / "PROFILE.md").write_text("# planted\n")
    return planted


@pytest.mark.parametrize("name", ["Riverbend Logistics", "_system", "riverbend--logistics"])
def test_a_staged_dir_whose_name_is_not_a_slug_is_not_found(client, cfg, name):
    draft_id = str(uuid.uuid4())
    planted = _plant_staged_dir(cfg.profiles_root / ".staging", name, draft_id)

    assert client.get(f"/onboard/{draft_id}/diff").status_code == 404
    assert (
        client.post(
            f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
        ).status_code
        == 404
    )
    assert client.delete(f"/onboard/{draft_id}").status_code == 404
    assert (planted / "PROFILE.md").is_file()


def test_a_staged_dir_symlinked_out_of_staging_is_not_found(client, cfg, tmp_path):
    draft_id = str(uuid.uuid4())
    outside = _plant_staged_dir(tmp_path / "elsewhere", "riverbend-logistics", draft_id)
    staging = cfg.profiles_root / ".staging"
    staging.mkdir(parents=True)
    (staging / "riverbend-logistics").symlink_to(outside, target_is_directory=True)

    assert client.get(f"/onboard/{draft_id}/diff").status_code == 404
    assert client.delete(f"/onboard/{draft_id}").status_code == 404
    assert (outside / "PROFILE.md").is_file()


# ── TN-03: another workspace's draft is a 404 on both lookup paths ────────────
#
# "memory": the draft is in the process cache, owned by A. "disk": the cache was cleared (a
# restart), so the lookup has to go to the staging tree — which must be the CALLER's own.


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _attempt_diff(client, draft_id, monkeypatch):
    return client.get(f"/onboard/{draft_id}/diff")


def _attempt_promote(client, draft_id, monkeypatch):
    return client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )


def _attempt_cancel(client, draft_id, monkeypatch):
    return client.delete(f"/onboard/{draft_id}")


def _attempt_re_extract(client, draft_id, monkeypatch):
    # A brain that answers, so an unchecked lookup would go on to re-stage the draft.
    _brain_returns(monkeypatch, json.dumps(_re_extracted_product()))
    return client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "text", "source": "Widget now ships an audit log."},
    )


_ATTEMPTS = [_attempt_diff, _attempt_promote, _attempt_cancel, _attempt_re_extract]


def _assert_a_is_untouched_and_can_promote(client, cfg, cfg_b, acting, draft_id, before, db_scope):
    assert _tree(_staged_root(cfg)) == before
    assert not (cfg.profiles_root / "riverbend-logistics").exists()
    assert not (cfg_b.profiles_root / "riverbend-logistics").exists()
    assert _profile_inserts(db_scope) == []

    acting.workspace_id = _WS
    res = _attempt_promote(client, draft_id, None)
    assert res.status_code == 200
    assert (cfg.profiles_root / "riverbend-logistics" / "PROFILE.md").is_file()
    assert _profile_inserts(db_scope) == [(str(_WS), "riverbend-logistics")]


@pytest.mark.parametrize("attempt", _ATTEMPTS)
def test_another_workspace_cannot_reach_a_cached_draft(
    client, cfg, cfg_b, acting, db_scope, monkeypatch, attempt
):
    draft_id = _ingest(client, monkeypatch)
    before = _tree(_staged_root(cfg))
    assert onboard._drafts[draft_id]["workspace_id"] == _WS

    acting.workspace_id = _WS_B
    res = attempt(client, draft_id, monkeypatch)

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "draft_not_found"
    assert onboard._drafts[draft_id]["workspace_id"] == _WS
    _assert_a_is_untouched_and_can_promote(client, cfg, cfg_b, acting, draft_id, before, db_scope)


@pytest.mark.parametrize("attempt", _ATTEMPTS)
def test_another_workspace_cannot_reach_a_draft_from_disk(
    client, cfg, cfg_b, acting, db_scope, monkeypatch, attempt
):
    draft_id = _ingest(client, monkeypatch)
    before = _tree(_staged_root(cfg))
    _restart()

    acting.workspace_id = _WS_B
    res = attempt(client, draft_id, monkeypatch)

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "draft_not_found"
    assert draft_id not in onboard._drafts
    _assert_a_is_untouched_and_can_promote(client, cfg, cfg_b, acting, draft_id, before, db_scope)


def test_a_cached_draft_of_another_workspace_never_falls_through_to_the_callers_disk(
    client, cfg, cfg_b, acting, db_scope, monkeypatch
):
    """B has no cache entry of its own for the id, but A does — and B's own tree holds a
    staged dir carrying the same draft_id. The cache hit decides: B gets 404, and A's entry is
    not replaced by B's, which would hand A's next request B's files (or a 404)."""
    draft_id = _ingest(client, monkeypatch)
    before = _tree(_staged_root(cfg))
    planted = _plant_staged_dir(cfg_b.profiles_root / ".staging", "riverbend-logistics", draft_id)
    planted_before = _tree(planted)

    acting.workspace_id = _WS_B
    for attempt in _ATTEMPTS:
        res = attempt(client, draft_id, monkeypatch)
        assert res.status_code == 404, attempt.__name__
        assert onboard._drafts[draft_id]["workspace_id"] == _WS
        assert onboard._drafts[draft_id]["staged_root"] == _staged_root(cfg)
    assert _tree(planted) == planted_before

    _assert_a_is_untouched_and_can_promote(client, cfg, cfg_b, acting, draft_id, before, db_scope)


# ── Onboarding jobs (issue #259) ──────────────────────────────────────────────


def _jobs_dir(cfg) -> Path:
    return cfg.profiles_root / ".staging" / onboard_jobs.JOBS_DIR


def _submit(client, source: str = "We build widgets."):
    return client.post("/onboard", json={"source_type": "text", "source": source})


@pytest.fixture
def held_brain(monkeypatch):
    """A brain that answers only once released, so a job can be observed in flight."""
    import asyncio
    import threading

    release = threading.Event()
    calls: list[str] = []

    async def _reply(prompt, cfg):
        calls.append(prompt)
        await asyncio.to_thread(release.wait, 10)
        return json.dumps(_draft())

    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _reply)
    return SimpleNamespace(release=release, calls=calls)


def test_ingest_answers_202_with_a_pending_job_before_the_work_finishes(client, cfg, held_brain):
    res = _submit(client)

    assert res.status_code == 202
    job = res.json()
    assert job["kind"] == "ingest"
    assert job["status"] == "pending"
    assert job["result"] is None and job["error"] is None
    assert (_jobs_dir(cfg) / f"{job['job_id']}.json").is_file()
    assert not (cfg.profiles_root / ".staging" / "riverbend-logistics").exists()

    held_brain.release.set()
    done = settle(client, res)
    assert done.status_code == 200
    assert done.json()["slug"] == "riverbend-logistics"
    polled = client.get(f"/onboard/jobs/{job['job_id']}").json()
    assert polled["status"] == "succeeded"
    assert polled["result"]["draft_id"] == done.json()["draft_id"]


def test_an_in_flight_duplicate_reuses_the_job_and_pays_once(client, held_brain):
    first = _submit(client).json()
    second = _submit(client).json()
    other = _submit(client, "A different company entirely.").json()

    assert second["job_id"] == first["job_id"]
    assert other["job_id"] != first["job_id"]

    held_brain.release.set()
    settle(client, _FakeResponse(first))
    settle(client, _FakeResponse(other))
    assert len(held_brain.calls) == 2


def test_a_finished_job_is_never_reused(client, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft()))
    first = _submit(client)
    first_id = first.json()["job_id"]
    draft_id = settle(client, first).json()["draft_id"]
    assert client.delete(f"/onboard/{draft_id}").status_code == 204

    again = _submit(client)
    assert again.json()["job_id"] != first_id
    assert settle(client, again).status_code == 200


class _FakeResponse:
    status_code = 202

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def test_another_workspaces_job_is_not_found(client, acting, monkeypatch):
    _brain_returns(monkeypatch, json.dumps(_draft()))
    job_id = _submit(client).json()["job_id"]

    acting.workspace_id = _WS_B
    res = client.get(f"/onboard/jobs/{job_id}")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    acting.workspace_id = _WS
    assert client.get(f"/onboard/jobs/{job_id}").status_code == 200


def test_a_job_record_naming_another_workspace_is_not_found(client, cfg, monkeypatch):
    """Even in the caller's own tree, the record's own workspace field must match."""
    _brain_returns(monkeypatch, json.dumps(_draft()))
    res = _submit(client)
    job_id = res.json()["job_id"]
    settle(client, res)
    path = _jobs_dir(cfg) / f"{job_id}.json"
    record = json.loads(path.read_text())
    record["workspace_id"] = str(_WS_B)
    path.write_text(json.dumps(record))

    assert client.get(f"/onboard/jobs/{job_id}").status_code == 404


def test_an_unknown_or_malformed_job_id(client):
    assert client.get(f"/onboard/jobs/{uuid.uuid4()}").status_code == 404
    assert client.get("/onboard/jobs/not-a-uuid").status_code == 422


def _plant_job(cfg, **fields) -> str:
    job_id = str(uuid.uuid4())
    record = {
        "job_id": job_id,
        "workspace_id": str(_WS),
        "kind": "ingest",
        "source_key": "k",
        "status": "running",
        "result": None,
        "error": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "heartbeat_at": "2026-01-01T00:00:00+00:00",
        **fields,
    }
    _jobs_dir(cfg).mkdir(parents=True, exist_ok=True)
    (_jobs_dir(cfg) / f"{job_id}.json").write_text(json.dumps(record))
    return job_id


@pytest.mark.parametrize("status", ["pending", "running"])
def test_a_job_whose_heartbeat_stopped_reads_as_interrupted(client, cfg, status):
    job_id = _plant_job(cfg, status=status)

    job = client.get(f"/onboard/jobs/{job_id}").json()

    assert job["status"] == "failed"
    assert job["error"]["code"] == "onboarding_interrupted"


def test_a_job_with_a_live_heartbeat_is_still_running(client, cfg):
    from datetime import UTC, datetime

    job_id = _plant_job(cfg, heartbeat_at=datetime.now(UTC).isoformat())

    assert client.get(f"/onboard/jobs/{job_id}").json()["status"] == "running"


def test_an_interrupted_job_is_not_reused(client, cfg, monkeypatch):
    key = onboard_jobs.source_key("ingest", "text", "We build widgets.")
    stale = _plant_job(cfg, source_key=key)
    _brain_returns(monkeypatch, json.dumps(_draft()))

    res = _submit(client)

    assert res.json()["job_id"] != stale
    assert settle(client, res).status_code == 200


def test_expired_finished_jobs_are_pruned_and_live_ones_kept(client, cfg, monkeypatch):
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    expired = _plant_job(cfg, status="succeeded")
    recent = _plant_job(cfg, status="failed", updated_at=now)
    live = _plant_job(cfg, heartbeat_at=now)
    _brain_returns(monkeypatch, json.dumps(_draft()))

    settle(client, _submit(client))

    assert not (_jobs_dir(cfg) / f"{expired}.json").exists()
    assert (_jobs_dir(cfg) / f"{recent}.json").exists()
    assert (_jobs_dir(cfg) / f"{live}.json").exists()


def test_the_jobs_dir_is_invisible_to_the_draft_lookup(client, cfg, monkeypatch):
    """.staging/.jobs sits beside the staged slug dirs; a draft still resolves from disk."""
    draft_id = _ingest(client, monkeypatch)
    assert _jobs_dir(cfg).is_dir()
    _restart()

    assert client.get(f"/onboard/{draft_id}/diff").status_code == 200


def test_re_extract_answers_202_and_its_result_is_the_new_draft(client, monkeypatch):
    draft_id = _ingest(client, monkeypatch)
    _brain_returns(monkeypatch, json.dumps(_re_extracted_product()))

    res = client.post(
        f"/onboard/{draft_id}/product/widget/extract",
        json={"source_type": "text", "source": "Widget now ships an audit log."},
    )

    assert res.status_code == 202
    assert res.json()["kind"] == "product_extract"
    done = settle(client, res)
    assert done.status_code == 200
    assert done.json()["draft_id"] != draft_id


def test_an_unexpected_failure_is_an_internal_error_without_its_detail(client, monkeypatch):
    def _broken(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    _brain_returns(monkeypatch, json.dumps(_draft()))
    monkeypatch.setattr("agent.onboard.stage", _broken)

    res = settle(client, _submit(client))

    assert res.status_code == 500
    assert res.json()["error"]["code"] == "internal_error"
    assert "secret internal detail" not in res.text


# ── issue #267: the shape that 500'd staging ──────────────────────────────────


def test_the_draft_with_dict_pillars_is_coerced_and_onboards(client, monkeypatch):
    """When a model returns pillars as dictionaries, they are coerced to strings and onboard succeeds."""
    draft_with_dict_pillars = _draft(
        pillars=[
            {"name": "Freight operations", "description": "How loads actually move."},
            {"name": "Carrier networks", "description": "Who hauls what, where."},
        ]
    )
    _brain_returns(monkeypatch, json.dumps(draft_with_dict_pillars))

    res = settle(client, _submit(client))

    assert res.status_code == 200
    assert res.json()["slug"] == "riverbend-logistics"


def test_a_malformed_draft_missing_required_fields_is_a_502_not_a_500(client, monkeypatch):
    bad = _draft()
    del bad["company"]["name"]
    _brain_returns(monkeypatch, json.dumps(bad))

    res = settle(client, _submit(client))

    assert res.status_code == 502
    assert res.json()["error"]["code"] == "onboarding_extract_failed"
    assert "TypeError" not in res.text


def test_url_source_with_non_url_text_is_a_422_not_a_500(client):
    """When non-URL text is submitted with source_type: 'url', it returns 422, not 500."""
    res = settle(
        client,
        client.post(
            "/onboard",
            json={
                "source_type": "url",
                "source": "Acme Corp is an automated freight broker.\nWe dispatch loads.",
            },
        ),
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "onboarding_input_invalid"


def test_promote_auto_syncs_workspace_display_name_when_default_email(
    client, db_scope, monkeypatch
):
    """On first profile promote, if workspace display_name contains '@', it is synced to company name."""
    draft_id = _ingest(client, monkeypatch)
    res = client.post(
        f"/onboard/{draft_id}/promote", json={"confirmed_company_name": "Riverbend Logistics"}
    )
    assert res.status_code == 200
    workspace_updates = [
        args for sql, args in db_scope.statements if sql.startswith("UPDATE workspaces")
    ]
    assert workspace_updates == [("Riverbend Logistics", str(_WS))]


def test_a_well_formed_draft_still_onboards(client, monkeypatch):
    """Negative control for the test above: the check refuses a bad shape, not every shape."""
    _brain_returns(monkeypatch, json.dumps(_draft()))

    res = settle(client, _submit(client))

    assert res.status_code == 200
    assert res.json()["slug"] == "riverbend-logistics"
