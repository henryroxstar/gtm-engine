"""tests/live — HTTP acceptance suite against a RUNNING backend stack (D1 scaffold).

Every test under this directory is collected and then SKIPPED unless ``GTM_LIVE_BASE_URL``
is set, so the normal suite (``python -m pytest tests/ -q -n auto``) sees this whole tree
with no network access and no import-time failures — see
:func:`pytest_collection_modifyitems` below, which is the ONLY place that decides who runs
and who skips (the pure predicates it calls live in ``tests/live/_support.py`` so they're
unit-testable without a live stack — see ``tests/live/test_support_unit.py``).

The exact commands (local dev stack, staging) live in ONE place — DEVELOPMENT.md's
"Running the tests/live HTTP acceptance suite" section — not duplicated here.

Fixtures:
    base_url             — GTM_LIVE_BASE_URL, trailing slash stripped. Session-scoped.
    http                 — an httpx.Client(base_url=base_url) with sane timeouts. Session-scoped.
    new_user()           — factory: registers a throwaway user, always deleted on teardown
                            (even on failure). Prefer `live_user` when a test can share one —
                            register/login/exchange share a 10/minute per-IP limit.
    live_user            — ONE session-scoped throwaway user, shared across tests that don't
                            need isolation from each other.
    grant_entitlement    — grant_entitlement(user, entitlement="pro", cap_usd=...): PUT
                            /v1/entitlement/{workspace_id} with the service secret. Skips (with
                            a reason) if no secret is available, and refuses any workspace_id
                            this session didn't itself create via new_user/live_user.
    sse_frames           — sse_frames(user, run_id, since=None, until=None, timeout_s=...,
                            validate=True): a bearer-authenticated read of
                            GET /v1/runs/{id}/stream, schema-validating each frame by default.
    provision_local_profile — provision_local_profile(user, profile_name=...) (local_fake
                            only): writes scripts/dev_seed.py's pack-mode profile fixtures to
                            disk and registers + activates the profile over the API.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from tests.live._support import (  # noqa: E402
    LIVE_BASE_URL_ENV,
    LIVE_FAKE_RUNS_ENV,
    LIVE_REAL_EXECUTOR_ENV,
    LIVE_RECREATE_CMD_ENV,
    LIVE_WORKERS_ENV,
    REPO,
    LiveUser,
    billing_sync_secret,
    delete_user,
    live_stack_skip_reason,
    local_fake_skip_reason,
    multi_worker_skip_reason,
    read_sse_stream,
    real_executor_skip_reason,
    recreate_cmd_skip_reason,
    register_user,
)

#: A module ending in `_unit` (test_support_unit.py) exercises the pure pieces above directly
#: (no live stack needed) and must run in the normal fast suite — never auto-marked/gated
#: like everything else here. A suffix rule, not a fixed set of names, so a future unit
#: module added under tests/live isn't silently swept into the gate by an outdated allowlist.
_UNGATED_MODULE_SUFFIX = "_unit"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """The one place that decides which tests/live items run.

    A conftest.py's ``pytest_collection_modifyitems`` hook fires for the WHOLE session's
    collected items once this file is loaded (which happens as soon as anything under
    tests/live is collected) — not only for items in this directory — so every item is
    filtered to ``tests/live`` (minus the ungated unit-test module(s)) before anything below
    applies.
    """
    live_dir = Path(__file__).resolve().parent
    base_url_raw = os.environ.get(LIVE_BASE_URL_ENV, "")
    fake_runs_raw = os.environ.get(LIVE_FAKE_RUNS_ENV)
    real_executor_raw = os.environ.get(LIVE_REAL_EXECUTOR_ENV)
    recreate_cmd_raw = os.environ.get(LIVE_RECREATE_CMD_ENV)
    workers_raw = os.environ.get(LIVE_WORKERS_ENV)

    live_skip = live_stack_skip_reason(base_url_raw)
    real_executor_skip = real_executor_skip_reason(real_executor_raw)
    local_fake_skip = local_fake_skip_reason(base_url_raw, fake_runs_raw)
    recreate_cmd_skip = recreate_cmd_skip_reason(recreate_cmd_raw)
    multi_worker_skip = multi_worker_skip_reason(workers_raw)

    for item in items:
        item_path = item.path.resolve()
        if live_dir not in item_path.parents:
            continue
        if item_path.stem.endswith(_UNGATED_MODULE_SUFFIX):
            continue

        item.add_marker(pytest.mark.live_stack)
        if live_skip is not None:
            item.add_marker(pytest.mark.skip(reason=live_skip))
            continue  # nothing else can matter — the base URL isn't even set
        if "real_executor" in item.keywords and real_executor_skip is not None:
            item.add_marker(pytest.mark.skip(reason=real_executor_skip))
        if "local_fake" in item.keywords and local_fake_skip is not None:
            item.add_marker(pytest.mark.skip(reason=local_fake_skip))
        if "recreate_cmd" in item.keywords and recreate_cmd_skip is not None:
            item.add_marker(pytest.mark.skip(reason=recreate_cmd_skip))
        if "multi_worker" in item.keywords and multi_worker_skip is not None:
            item.add_marker(pytest.mark.skip(reason=multi_worker_skip))


# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def base_url() -> str:
    raw = os.environ.get(LIVE_BASE_URL_ENV, "").strip()
    if not raw:
        pytest.skip(live_stack_skip_reason(raw))
    return raw.rstrip("/")


@pytest.fixture(scope="session")
def http(base_url):
    """One shared client for the session — timeouts generous enough for a cold fake-run
    node, short enough that a genuinely hung stack still fails a test instead of the whole
    invocation. `trust_env=False`: `grant_entitlement` carries the raw billing-sync secret in
    a header, and no proxy env var may see it, loopback base_url or not."""
    with httpx.Client(
        base_url=base_url, timeout=httpx.Timeout(30.0, connect=10.0), trust_env=False
    ) as client:
        yield client


@pytest.fixture(scope="session")
def _live_workspace_ids() -> set[str]:
    """Every workspace_id this SESSION created via register_user — the allow-list
    grant_entitlement refuses to act outside of. Blast-radius guard: against staging, a bug
    (or a copy-pasted workspace_id) must never be able to grant entitlement to, or spend
    against, a REAL tenant's workspace — only ones this run created and will delete itself."""
    return set()


@pytest.fixture
def new_user(http, _live_workspace_ids):
    """Factory: new_user(password=None) -> LiveUser. Every user this factory creates is
    deleted on teardown — even if the test raised, and even if registration succeeded but a
    later step in this fixture raised — and a teardown failure is a warning, not a masking
    exception (the test's own outcome must still be reported)."""
    created: list[LiveUser] = []

    def _make(*, password: str | None = None) -> LiveUser:
        user = register_user(http, password=password, on_registered=created.append)
        _live_workspace_ids.add(user.workspace_id)
        return user

    yield _make

    for user in created:
        delete_user(http, user)


@pytest.fixture(scope="session")
def live_user(http, _live_workspace_ids):
    """ONE user shared across every test in the session that requests this fixture, so a
    test suite that doesn't need per-test isolation stays well under the 10/minute
    register-rate limit. Deleted once, at session end."""
    created: list[LiveUser] = []
    user = register_user(http, on_registered=created.append)
    _live_workspace_ids.add(user.workspace_id)
    yield user
    for u in created:
        delete_user(http, u)


@pytest.fixture
def grant_entitlement(http, base_url, _live_workspace_ids):
    def _grant(
        user: LiveUser, *, entitlement: str = "pro", cap_usd: float = 5.0, status: str = "active"
    ) -> dict:
        if user.workspace_id not in _live_workspace_ids:
            raise RuntimeError(
                "grant_entitlement: refusing to act on a workspace this session did not "
                "create via new_user/live_user (blast-radius guard)"
            )
        secret = billing_sync_secret(base_url)
        if secret is None:
            pytest.skip(
                "grant_entitlement: no BILLING_SYNC_SECRET available (set the env var, or "
                f"point a loopback {LIVE_BASE_URL_ENV} at a checkout with deploy/.env.dev)"
            )
        body = {
            "entitlement": entitlement,
            "cap_usd": cap_usd,
            "sync_id": f"live-test-{uuid.uuid4().hex}",
            "version": int(time.time() * 1000),
            "status": status,
        }
        # Raw secret, no "Bearer" — require_service_auth compares the whole header value
        # (backend/deps.py). Never put `secret` in an assertion message: pytest's assertion
        # introspection can print local variables, and this is the one function in this
        # fixture set that ever holds it.
        r = http.put(
            f"/v1/entitlement/{user.workspace_id}", headers={"Authorization": secret}, json=body
        )
        assert r.status_code == 200, f"PUT /v1/entitlement -> {r.status_code}"
        data = r.json()
        assert data.get("applied") is True, (
            f"entitlement sync not applied (outcome={data.get('outcome')!r})"
        )
        return data

    return _grant


@pytest.fixture
def sse_frames(http):
    def _read(
        user: LiveUser,
        run_id: str,
        *,
        since: int | None = None,
        until=None,
        timeout_s: float = 30.0,
        validate: bool = True,
    ):
        return read_sse_stream(
            http, user, run_id, since=since, until=until, timeout_s=timeout_s, validate=validate
        )

    return _read


@pytest.fixture
def provision_local_profile(http):
    """provision_local_profile(user, profile_name=DEFAULT_PROFILE) -> profile_name.

    Writes scripts/dev_seed.py's synthetic pack-mode profile fixtures (packs.toml,
    PROFILE.md, BRAND.toml, knowledge/*.md — activates marketing + prospecting + creator)
    straight to the HOST's ``data/workspaces/<workspace_id>/profiles/<profile_name>/`` tree,
    then registers + activates the profile over the API exactly as ``scripts/dev_seed.py``
    does (POST /v1/profiles, POST /v1/profiles/{name}/activate).

    ONLY works when this test process runs from the SAME checkout the local Docker stack was
    started from: deploy/docker-compose.dev.yml bind-mounts ``<that checkout>/data/workspaces``
    into the api container (a dev convenience over the base compose's named volume — see
    LD-01), so a write from a different checkout never reaches the running container.
    local_fake-marked tests are only ever collected against a loopback base_url, which is the
    signal that this is plausible at all — it is still the caller's job to run pytest from the
    checkout that started the stack.
    """
    created: list[tuple[str, str]] = []

    def _provision(user: LiveUser, profile_name: str | None = None) -> str:
        from gtm_core.paths import _safe_segment
        from tests.live._support import DEFAULT_PROFILE

        name = profile_name or DEFAULT_PROFILE
        _safe_segment(name, "profile_name")  # raises ValueError on a traversal-shaped name —
        # before any write or rmtree below ever touches a path built from it.

        try:
            from scripts.dev_seed import _provision_profile_files
        except ModuleNotFoundError:
            pytest.skip(
                "provision_local_profile: scripts.dev_seed is not present in this distribution"
            )

        _provision_profile_files(user.workspace_id, name)
        created.append((user.workspace_id, name))

        r = http.post("/v1/profiles", headers=user.auth_header(), json={"profile_name": name})
        assert r.status_code == 201, f"POST /v1/profiles -> {r.status_code}"
        r = http.post(f"/v1/profiles/{name}/activate", headers=user.auth_header())
        assert r.status_code == 200, f"activate profile -> {r.status_code}"
        return name

    yield _provision

    import shutil

    from gtm_core.paths import workspace_profiles_root

    for workspace_id, profile_name in created:
        shutil.rmtree(
            workspace_profiles_root(workspace_id, REPO) / profile_name, ignore_errors=True
        )
