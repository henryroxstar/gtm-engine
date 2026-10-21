"""W1 against a REAL Postgres: idempotency, the lease, RLS, crashes between commits, dedupe (PRD 2026-10-19a).

Test plan P1-1 .. P1-15. A mock cannot prove any of these — admission, RLS, transaction scope, lease takeover
and concurrency are properties of the database — so they run in the ``dbtest`` tier, as the non-owner
``gtm_api`` role with FORCE RLS in force (``admin`` is the owner connection, used only to arrange state a
tenant could not write for itself).

What is real: the migration, the state machine, the onboarding job and its staging directory, the profile
promote, the agent and key writes, the readiness checker. What is scripted: the model call inside onboarding
(``GTM_FAKE_RUNS``, the repo's own dev-only switch, which keeps ingest/stage/promote real) and the provider-key
probes (patched; fixture keys are ``test_``-prefixed and nothing leaves the machine).

    GTM_TEST_PG_ADMIN_DSN=postgres://gtm_app:postgres@localhost:55433/gtm_backend \\
      uv run pytest tests/backend/test_provision_live.py -q -o addopts=""
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.dbtest
asyncpg = pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402

from backend.database import create_pool, workspace_scope  # noqa: E402
from backend.deps import WorkspaceCtx  # noqa: E402
from backend.routers.provision import workspace_cfg  # noqa: E402
from backend.services import onboard_jobs  # noqa: E402
from backend.services import provision as svc  # noqa: E402
from backend.services.integrations_verify import VerifyResult  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402
from gtm_core.paths import workspace_profiles_root  # noqa: E402
from tests.backend._live_helpers import seed_workspace  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class Crash(BaseException):
    """A process dying at a chosen point. BaseException so no ``except Exception`` in the code under test absorbs it."""


class World:
    def __init__(self, admin, pool, base_cfg, cfg, ws_id: str, entitlement: Entitlement):
        self.admin, self.pool, self.base_cfg, self.cfg = admin, pool, base_cfg, cfg
        self.ws_id = ws_id
        self.ws = WorkspaceCtx(user_id="u", workspace_id=ws_id, entitlement=entitlement)

    async def other_workspace(self, email: str) -> World:
        wid = await seed_workspace(self.admin, email, credits=100.0)
        await self.admin.execute(
            "UPDATE subscriptions SET entitlement = 'pro', status = 'active' WHERE workspace_id = $1::uuid",
            wid,
        )
        cfg = await workspace_cfg(self.pool, self.base_cfg, wid)
        return World(self.admin, self.pool, self.base_cfg, cfg, wid, Entitlement.PRO)

    async def add_profile(
        self, name: str = "fixtureco", packs: tuple[str, ...] = ("marketing",)
    ) -> None:
        root = workspace_profiles_root(self.ws_id, REPO) / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "PROFILE.md").write_text("# Fixtureco\n", encoding="utf-8")
        (root / "packs.toml").write_text(f"active = {json.dumps(list(packs))}\n", encoding="utf-8")
        await self.admin.execute(
            "INSERT INTO profiles(workspace_id, profile_name) VALUES($1::uuid, $2) ON CONFLICT DO NOTHING",
            self.ws_id,
            name,
        )

    def req(self, **kw) -> svc.ProvisionRequest:
        base = {"name": "Fixture Agent", "profile_name": "fixtureco", "key_label": "ci"}
        return svc.ProvisionRequest(**{**base, **kw})

    async def go(self, key: str, **kw):
        return await svc.provision(
            self.pool, self.cfg, self.ws, idempotency_key=key, req=self.req(**kw)
        )

    async def n(self, table: str, where: str = "true", *args) -> int:
        return await self.admin.fetchval(
            f"SELECT count(*) FROM {table} WHERE workspace_id = $1::uuid AND {where}",  # noqa: S608
            self.ws_id,
            *args,
        )

    async def expire_lease(self) -> None:
        await self.admin.execute(
            "UPDATE provisioning_jobs SET lease_expires_at = now() - interval '1 second' WHERE workspace_id = $1::uuid",
            self.ws_id,
        )

    async def job_row(self, job_id: str) -> dict:
        return dict(
            await self.admin.fetchrow("SELECT * FROM provisioning_jobs WHERE id = $1::uuid", job_id)
        )


def run(clean_db, tmp_path, monkeypatch, body, *, entitlement=Entitlement.PRO, credits=100.0):
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("VAULT_KEK", secrets.token_hex(32))
    monkeypatch.setenv("GTM_FAKE_ONBOARD_DELAY_S", "0")

    async def main():
        from agent.config import Config

        admin = await asyncpg.connect(clean_db["admin_dsn"])
        pool = await create_pool(clean_db["api_dsn"], min_size=1, max_size=6)
        try:
            wid = await seed_workspace(admin, "w1@example.com", credits=credits)
            await admin.execute(
                "UPDATE subscriptions SET entitlement = $2, status = 'active' WHERE workspace_id = $1::uuid",
                wid,
                entitlement.value,
            )
            base = Config.from_env(repo_root=REPO)
            cfg = await workspace_cfg(pool, base, wid)
            return await body(World(admin, pool, base, cfg, wid, entitlement))
        finally:
            await pool.close()
            await admin.close()

    return asyncio.run(main())


def fake_onboarding(monkeypatch):
    """Use the repo's dev-only scripted extraction: ingest, stage, promote and the profile row stay real."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")


async def refusal(coro) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        await coro
    return e.value


def code_of(exc: HTTPException) -> str:
    return exc.detail["code"] if isinstance(exc.detail, dict) else ""


# ── P1-1 / P1-2: repeat is safe; the raw key exists once ──────────────────────


def test_p1_1_a_repeat_returns_the_same_agent_and_never_a_second_key(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        s1, first = await w.go("k-one")
        s2, second = await w.go("k-one")
        assert s1 == s2 == 200 and first["state"] in ("ready", "ready_with_blockers")
        assert first["key"]["raw_key"].startswith("sk-")
        assert "raw_key" not in second["key"]
        assert (
            second["key"]["id"] == first["key"]["id"]
            and second["key"]["prefix"] == first["key"]["prefix"]
        )
        assert second["agent"]["agent_id"] == first["agent"]["agent_id"]
        assert await w.n("agents", "name = $2", "Fixture Agent") == 1
        assert await w.n("api_keys") == 1
        assert await w.n("provisioning_jobs") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_2_the_raw_key_is_in_exactly_one_response_and_nowhere_else(
    clean_db, tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)

    async def body(w):
        await w.add_profile()
        _, first = await w.go("k-two")
        raw = first["key"]["raw_key"]
        _, second = await w.go("k-two")
        _, third = await w.go("k-two", reissue_key=False)
        view = await svc.get_job_view(w.pool, w.ws_id, first["job_id"])
        row = await w.job_row(first["job_id"])
        dump = await w.admin.fetch("SELECT * FROM api_keys WHERE workspace_id = $1::uuid", w.ws_id)

        for text in (json.dumps(second), json.dumps(third), json.dumps(view[1], default=str)):
            assert raw not in text
        assert raw not in json.dumps(row, default=str)  # no provisioning_jobs column holds it
        assert raw not in json.dumps(
            [dict(r) for r in dump], default=str
        )  # only the hash is stored
        assert hashlib.sha256(raw.encode()).hexdigest() in {r["key_hash"] for r in dump}
        assert raw not in caplog.text and raw not in repr(first["key"].get("prefix"))

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-5: the checks that come before any spend, every call ───────────────────


def _count_spend(monkeypatch):
    counts = {"onboarding": 0, "probes": 0}
    from backend.routers import onboard as onboard_router

    real_ingest = onboard_router._ingest

    async def counting_ingest(*a, **k):
        counts["onboarding"] += 1
        return await real_ingest(*a, **k)

    async def counting_verify(provider, key, **k):
        counts["probes"] += 1
        return VerifyResult(status="valid")

    monkeypatch.setattr(onboard_router, "_ingest", counting_ingest)
    monkeypatch.setattr(svc, "verify_provider_credential", counting_verify)
    return counts


@pytest.mark.parametrize("case", ["free", "no_wallet", "zero_credit", "cap_zero"])
def test_p1_5_precheck_refuses_before_any_spend(clean_db, tmp_path, monkeypatch, case):
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def body(w):
        await w.add_profile()
        if case == "no_wallet":
            await w.admin.execute(
                "DELETE FROM workspace_wallets WHERE workspace_id = $1::uuid", w.ws_id
            )
        if case == "zero_credit":
            await w.admin.execute(
                "UPDATE workspace_wallets SET balance_credits = 0, balance_usd = 0 WHERE workspace_id = $1::uuid",
                w.ws_id,
            )
        if case == "cap_zero":
            await w.admin.execute(
                "UPDATE subscriptions SET monthly_cost_cap_usd = 0 WHERE workspace_id = $1::uuid",
                w.ws_id,
            )
        exc = await refusal(
            w.go(
                "k-pre",
                source_url="https://fixtureco.example",
                profile_name=None,
                integrations={"apollo": "test_x"},
            )
        )
        want = {
            "free": (403, "entitlement_required"),
            "no_wallet": (402, "insufficient_credit"),
            "zero_credit": (402, "insufficient_credit"),
            "cap_zero": (402, "cost_cap_reached"),
        }[case]
        assert (exc.status_code, code_of(exc)) == want
        assert counts == {"onboarding": 0, "probes": 0}
        assert (
            await w.n("provisioning_jobs") == 0
            and await w.n("agents") == 0
            and await w.n("api_keys") == 0
        )

    run(
        clean_db,
        tmp_path,
        monkeypatch,
        body,
        entitlement=Entitlement.FREE if case == "free" else Entitlement.PRO,
    )


def test_p1_5_an_entitlement_that_lapses_during_a_park_is_refused_on_the_resume(
    clean_db, tmp_path, monkeypatch
):
    fake_onboarding(monkeypatch)

    async def body(w):
        status, out = await park(w, "k-lapse")
        assert out["state"] == "awaiting_profile_confirmation"
        await w.admin.execute(
            "UPDATE workspace_wallets SET balance_credits = 0, balance_usd = 0 WHERE workspace_id = $1::uuid",
            w.ws_id,
        )
        exc = await refusal(
            w.go("k-lapse", source_url="https://fixtureco.example", profile_name=None)
        )
        assert (exc.status_code, code_of(exc)) == (402, "insufficient_credit")
        assert (await w.job_row(out["job_id"]))[
            "state"
        ] == "awaiting_profile_confirmation"  # not advanced

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-6: provider keys first, every failure together, valid ones kept ────────


def test_p1_6_integrations_report_every_failure_together_and_keep_the_valid_key(
    clean_db, tmp_path, monkeypatch
):
    counts = {"onboarding": 0}
    bad = {"apollo", "rocketreach", "saleshandy"}

    async def verify(provider, key, **k):
        if provider in bad:
            return VerifyResult(status="invalid_key", reason=f"{provider} rejected the key")
        return VerifyResult(status="valid")

    monkeypatch.setattr(svc, "verify_provider_credential", verify)
    from backend.routers import onboard as onboard_router

    async def no_ingest(*a, **k):  # pragma: no cover - reaching it is the failure
        counts["onboarding"] += 1

    monkeypatch.setattr(onboard_router, "_ingest", no_ingest)
    keys = {p: f"test_fixture_{p}_key" for p in ("apollo", "rocketreach", "saleshandy", "syften")}

    async def body(w):
        await w.add_profile()
        exc = await refusal(w.go("k-int", integrations=keys))
        assert exc.status_code == 422 and code_of(exc) == "provider_keys_invalid"
        errors = exc.detail["errors"]
        assert {e["provider"] for e in errors} == bad
        assert all(e["how_to_fix"].strip() and e["reason"].strip() for e in errors)
        assert await w.admin.fetchval(
            "SELECT array_agg(provider ORDER BY provider) FROM encrypted_credentials WHERE workspace_id = $1::uuid",
            w.ws_id,
        ) == ["syften"]
        assert counts["onboarding"] == 0 and await w.n("agents") == 0
        row = await w.job_row(exc.detail["job_id"])
        assert row["integrations"] == ["syften"] and row["state"] == "started"
        assert not any(
            v in json.dumps(row, default=str) for v in keys.values()
        )  # names and reasons only

        # corrected values under the SAME idempotency key are allowed (values are not in the request hash)
        bad.clear()
        status, out = await w.go("k-int", integrations={p: v + "_fixed" for p, v in keys.items()})
        assert status == 200 and out["state"] in ("ready", "ready_with_blockers")
        stored = await w.admin.fetchval(
            "SELECT count(*) FROM encrypted_credentials WHERE workspace_id = $1::uuid", w.ws_id
        )
        assert stored == 4

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-7: park and resume through the REAL onboarding job ─────────────────────


async def park(w, key: str, url: str = "https://fixtureco.example", **kw):
    """Drive the first part of a URL setup until it is parked for the profile review."""
    out = None
    for _ in range(200):
        status, out = await w.go(key, source_url=url, profile_name=None, **kw)
        if out["state"] != "onboarding_running":
            return status, out
        await asyncio.sleep(0.05)
    raise AssertionError(f"never reached a parked state, last: {out}")


async def promote(w, draft_id: str, company: str = "Fixtureco"):
    from backend.routers import onboard as ob
    from backend.schemas import OnboardPromoteRequest

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(pool=w.pool, cfg=w.base_cfg))
    )
    return await ob.promote_endpoint(
        draft_id, OnboardPromoteRequest(confirmed_company_name=company), w.ws, request
    )


def test_p1_7_park_then_resume_after_the_human_confirms(clean_db, tmp_path, monkeypatch):
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def body(w):
        status, first = await w.go(
            "k-park", source_url="https://fixtureco.example", profile_name=None
        )
        assert status == 202 and first["state"] == "onboarding_running"
        assert first["next_action"]["kind"] == "wait" and first["message"].strip()

        status, parked = await park(w, "k-park")
        assert status == 202 and parked["state"] == "awaiting_profile_confirmation"
        action = parked["next_action"]
        assert (
            action["kind"] == "confirm_profile"
            and action["draft_id"]
            and "promote" in action["then"]
        )
        assert await w.n("agents") == 0 and await w.n("api_keys") == 0
        await asyncio.sleep(0.2)
        assert not onboard_jobs._tasks, "something still runs in the background while parked"

        # repeating before the human acts re-reports the park and starts nothing new
        _, again = await w.go("k-park", source_url="https://fixtureco.example", profile_name=None)
        assert again["state"] == "awaiting_profile_confirmation"
        assert counts["onboarding"] == 1

        await promote(w, action["draft_id"])
        status, done = await w.go(
            "k-park", source_url="https://fixtureco.example", profile_name=None
        )
        assert status == 200 and done["state"] in ("ready", "ready_with_blockers")
        assert done["agent"]["profile_name"] == "fixtureco"
        assert done["key"]["raw_key"].startswith("sk-")
        assert await w.n("agents", "name = $2", "Fixture Agent") == 1 and await w.n("api_keys") == 1
        assert counts["onboarding"] == 1

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-8: what a key is bound to; concurrent calls ────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Another Name"},
        {"packs": ["marketing"]},
        {"monthly_budget_usd": 3.0},
        {"daily_dispatch_cap": 2},
        {"profile_name": "otherco"},
    ],
)
def test_p1_8_the_same_key_with_a_different_request_is_refused(
    clean_db, tmp_path, monkeypatch, change
):
    async def body(w):
        await w.add_profile()
        await w.go("k-hash")
        exc = await refusal(w.go("k-hash", **change))
        assert (exc.status_code, code_of(exc)) == (409, "idempotency_key_reused")
        assert await w.n("agents") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_8_a_reordered_packs_list_is_the_same_request(clean_db, tmp_path, monkeypatch):
    async def body(w):
        await w.add_profile(packs=("marketing", "planning"))
        _, a = await w.go("k-order", packs=["marketing", "planning"])
        _, b = await w.go("k-order", packs=["planning", "marketing"])
        assert a["agent"]["agent_id"] == b["agent"]["agent_id"]

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_8_two_simultaneous_calls_with_one_key_make_one_agent_and_one_key(
    clean_db, tmp_path, monkeypatch
):
    real = svc.compute_blockers

    async def slow(*a, **k):
        await asyncio.sleep(0.5)  # holds the lease while the second call arrives
        return await real(*a, **k)

    monkeypatch.setattr(svc, "compute_blockers", slow)

    async def body(w):
        await w.add_profile()
        results = await asyncio.gather(w.go("k-race"), w.go("k-race"), return_exceptions=True)
        refused = [r for r in results if isinstance(r, HTTPException)]
        ok = [r for r in results if not isinstance(r, BaseException)]
        assert len(ok) == 1 and len(refused) == 1
        assert (refused[0].status_code, code_of(refused[0])) == (409, "provision_in_progress")
        assert int(refused[0].headers["Retry-After"]) >= 1
        assert (
            await w.n("agents") == 1
            and await w.n("api_keys") == 1
            and await w.n("provisioning_jobs") == 1
        )

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-9: names are exact-match ───────────────────────────────────────────────


def test_p1_9_a_taken_name_is_a_conflict_and_case_makes_a_different_agent(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        await w.go("k-a", name="Acme")
        exc = await refusal(w.go("k-b", name="Acme"))
        assert (exc.status_code, code_of(exc)) == (409, "agent_name_taken")
        _, lower = await w.go("k-c", name="acme")  # documents today's behaviour: no case folding
        assert lower["agent"]["name"] == "acme"
        assert await w.n("agents", "name IN ('Acme','acme')") == 2

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_9_a_taken_name_is_found_before_any_paid_onboarding(clean_db, tmp_path, monkeypatch):
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def body(w):
        await w.add_profile()
        await w.go("k-first", name="Acme")
        exc = await refusal(
            w.go("k-url", name="Acme", source_url="https://other.example", profile_name=None)
        )
        assert code_of(exc) == "agent_name_taken" and counts["onboarding"] == 0

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-10: the blockers are the readiness route's own answer ──────────────────


def test_p1_10_blockers_agree_with_the_readiness_route_and_say_how_to_fix(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        from backend.callers.principal import Principal
        from backend.routers import packs as packs_router

        await (
            w.add_profile()
        )  # a profile with no knowledge: marketing's required topics are missing
        _, out = await w.go("k-ready")
        assert out["state"] == "ready_with_blockers" and out["ready"] is False and out["blockers"]
        assert all(
            b["how_to_fix"].strip() and b["what_is_missing"].strip() for b in out["blockers"]
        )

        principal = Principal(
            kind="user",
            subject="u",
            workspace_id=w.ws_id,
            entitlement=Entitlement.PRO,
            verifier="test",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(pool=w.pool, cfg=SimpleNamespace(repo_root=REPO))
            ),
            url=SimpleNamespace(path="/"),
        )
        from backend.pack_catalog import list_variants

        profiles_root = workspace_profiles_root(w.ws_id, REPO)
        route_blocked = set()
        for resolved in list_variants(REPO, profiles_root, "fixtureco"):
            detail = await packs_router.variant_readiness_detail(
                resolved.graph.pack, resolved.graph.variant, principal, request, None, "fixtureco"
            )
            for item in detail["readiness"]["items"]:
                if item["status"] == "blocked":
                    route_blocked.add(
                        (resolved.graph.pack, resolved.graph.variant, item["kind"], item["name"])
                    )
        ours = {(b["pack"], b["variant"], b["kind"], b["name"]) for b in out["blockers"]}
        assert ours == route_blocked and ours

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-11: RLS ────────────────────────────────────────────────────────────────


def test_p1_11_jobs_are_invisible_across_workspaces_and_unique_indexes_are_per_workspace(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        _, mine = await w.go("k-shared")
        other = await w.other_workspace("w2@example.com")
        await other.add_profile()

        async with workspace_scope(w.pool, other.ws_id) as conn:
            assert await conn.fetchval("SELECT count(*) FROM provisioning_jobs") == 0
            assert (
                await conn.fetchrow(
                    "SELECT 1 FROM provisioning_jobs WHERE id = $1::uuid", mine["job_id"]
                )
            ) is None
        assert (
            await svc.get_job_view(w.pool, other.ws_id, mine["job_id"]) is None
        )  # existence not leaked

        # the same idempotency key and the same source are legal in another workspace
        _, theirs = await other.go("k-shared")
        assert theirs["job_id"] != mine["job_id"]

        # a row written under another workspace's scope violates the policy
        async with workspace_scope(w.pool, other.ws_id) as conn:
            with pytest.raises(asyncpg.exceptions.PostgresError):
                await conn.execute(
                    "INSERT INTO provisioning_jobs(workspace_id, idempotency_key, request_hash, source_key, "
                    "agent_name, state, expires_at) VALUES($1::uuid, 'x', 'h', 's', 'n', 'started', now())",
                    w.ws_id,
                )

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_11_the_runtime_role_cannot_delete_a_job_and_the_state_check_holds(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        _, out = await w.go("k-del")
        async with workspace_scope(w.pool, w.ws_id) as conn:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute(
                    "DELETE FROM provisioning_jobs WHERE id = $1::uuid", out["job_id"]
                )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await w.admin.execute(
                "UPDATE provisioning_jobs SET state = 'bogus' WHERE id = $1::uuid", out["job_id"]
            )

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-12: every state, the lease, the 7-day expiry ───────────────────────────


def test_p1_12_each_state_is_reachable_and_reported(clean_db, tmp_path, monkeypatch):
    fake_onboarding(monkeypatch)
    seen: dict[str, str] = {}

    async def body(w):
        # onboarding_running -> awaiting_profile_confirmation -> ready_with_blockers
        _, first = await w.go("k-s1", source_url="https://fixtureco.example", profile_name=None)
        seen[first["state"]] = first["job_id"]
        _, parked = await park(w, "k-s1")
        seen[parked["state"]] = parked["job_id"]
        await promote(w, parked["next_action"]["draft_id"])
        _, done = await w.go("k-s1", source_url="https://fixtureco.example", profile_name=None)
        seen[done["state"]] = done["job_id"]

        # ready: a profile whose required inputs exist
        await w.add_profile("fullco")
        knowledge = workspace_profiles_root(w.ws_id, REPO) / "fullco" / "knowledge"
        knowledge.mkdir(exist_ok=True)
        for topic in ("voice", "icp-personas"):
            (knowledge / f"{topic}.md").write_text(f"# {topic}\nfixture text\n", encoding="utf-8")
        _, full = await w.go("k-s2", name="Full Agent", profile_name="fullco")
        seen[full["state"]] = full["job_id"]
        await w.add_profile("bareco")  # no knowledge at all: required topics are missing
        _, bare = await w.go("k-s5", name="Bare Agent", profile_name="bareco")
        seen[bare["state"]] = bare["job_id"]

        # abandoned (DELETE of a parked job) and failed (the onboarding job fails)
        _, parked2 = await park(w, "k-s3", "https://parkco.example", name="Third Agent")
        code, gone = await svc.abandon(w.pool, w.ws_id, parked2["job_id"])
        seen[gone["state"]] = gone["job_id"]

        from backend.routers import onboard as ob

        async def failing(*a, **k):
            raise HTTPException(
                503, {"code": "url_ingest_unavailable", "message": "Importing is unavailable."}
            )

        monkeypatch.setattr(ob, "_ingest", failing)
        for _ in range(100):
            _, failed = await w.go(
                "k-s4", name="Fourth Agent", source_url="https://failco.example", profile_name=None
            )
            if failed["state"] != "onboarding_running":
                break
            await asyncio.sleep(0.05)
        seen[failed["state"]] = failed["job_id"]
        assert failed["errors"][0]["code"] == "url_ingest_unavailable"
        _, replay = await w.go(
            "k-s4", name="Fourth Agent", source_url="https://failco.example", profile_name=None
        )
        assert replay["state"] == "failed"  # a failed job replays; it does not retry itself

    run(clean_db, tmp_path, monkeypatch, body)
    assert {
        "onboarding_running",
        "awaiting_profile_confirmation",
        "ready_with_blockers",
        "ready",
        "abandoned",
        "failed",
    } <= set(seen), sorted(seen)


def test_p1_12_a_dead_callers_lease_blocks_until_it_lapses_then_is_taken_over(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        real_release = svc._release_lease

        async def never_released(*a, **k):
            return None  # a process that died holds its lease until it lapses

        def die(name):
            def checkpoint(at):
                if at == name:
                    raise Crash(at)

            return checkpoint

        monkeypatch.setattr(svc, "_release_lease", never_released)
        monkeypatch.setattr(svc, "_checkpoint", die("agent_committed"))
        with pytest.raises(Crash):
            await w.go("k-lease")

        monkeypatch.setattr(svc, "_checkpoint", lambda at: None)
        exc = await refusal(w.go("k-lease"))  # the lease is still held
        assert (exc.status_code, code_of(exc)) == (409, "provision_in_progress")

        await w.expire_lease()
        monkeypatch.setattr(svc, "_release_lease", real_release)
        status, out = await w.go("k-lease")  # taken over from the recorded step
        assert status == 200 and out["key"]["raw_key"].startswith("sk-")
        assert await w.n("agents") == 1 and await w.n("api_keys") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_12_a_parked_job_expires_after_seven_days_and_frees_its_source(
    clean_db, tmp_path, monkeypatch
):
    fake_onboarding(monkeypatch)

    async def body(w):
        _, parked = await park(w, "k-old")
        await w.admin.execute(
            "UPDATE provisioning_jobs SET expires_at = now() - interval '1 minute' WHERE id = $1::uuid",
            parked["job_id"],
        )
        _, replay = await w.go("k-old", source_url="https://fixtureco.example", profile_name=None)
        assert replay["state"] == "abandoned"
        _, fresh = await w.go("k-new", source_url="https://fixtureco.example", profile_name=None)
        assert fresh["job_id"] != parked["job_id"] and fresh["state"] != "abandoned"

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-13 / P1-14: a crash between the two transactions; reissue ──────────────


def _dying_at(monkeypatch, name: str):
    def checkpoint(at):
        if at == name:
            raise Crash(at)

    monkeypatch.setattr(svc, "_checkpoint", checkpoint)

    async def never_released(*a, **k):
        return None

    monkeypatch.setattr(svc, "_release_lease", never_released)


def test_p1_13_a_crash_after_the_agent_and_before_the_key_ends_with_one_agent_and_one_key(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        real_release = svc._release_lease
        _dying_at(monkeypatch, "agent_committed")
        with pytest.raises(Crash):
            await w.go("k-c1")
        assert await w.n("agents") == 1 and await w.n("api_keys") == 0
        monkeypatch.setattr(svc, "_checkpoint", lambda at: None)
        monkeypatch.setattr(svc, "_release_lease", real_release)
        await w.expire_lease()
        _, out = await w.go("k-c1")
        assert out["key"]["raw_key"].startswith("sk-")
        assert await w.n("agents") == 1 and await w.n("api_keys", "revoked_at IS NULL") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_13_a_crash_after_the_key_before_the_response_returns_the_prefix_only(
    clean_db, tmp_path, monkeypatch
):
    async def body(w):
        await w.add_profile()
        real_release = svc._release_lease
        _dying_at(monkeypatch, "key_committed")
        with pytest.raises(Crash):
            await w.go("k-c2")
        assert await w.n("api_keys", "revoked_at IS NULL") == 1  # minted, and the response was lost
        monkeypatch.setattr(svc, "_checkpoint", lambda at: None)
        monkeypatch.setattr(svc, "_release_lease", real_release)
        await w.expire_lease()
        _, out = await w.go("k-c2")
        assert out["key"]["id"] and out["key"]["prefix"] and "raw_key" not in out["key"]
        assert await w.n("api_keys", "revoked_at IS NULL") == 1 and await w.n("agents") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_14_reissue_replaces_only_the_key_this_job_minted(clean_db, tmp_path, monkeypatch):
    async def body(w):
        from backend.services.api_keys import insert_api_key

        await w.add_profile()
        _, first = await w.go("k-r")
        agent_id = first["agent"]["agent_id"]
        async with workspace_scope(w.pool, w.ws_id) as conn:  # two keys this job did NOT mint
            other_on_agent, _ = await insert_api_key(
                conn,
                w.ws_id,
                label="other",
                requested_entitlement="pro",
                workspace_entitlement="pro",
                agent_id=agent_id,
            )
            unbound, _ = await insert_api_key(
                conn,
                w.ws_id,
                label="unbound",
                requested_entitlement="pro",
                workspace_entitlement="pro",
                agent_id=None,
            )

        _, again = await w.go("k-r", reissue_key=True)
        new_raw = again["key"]["raw_key"]
        assert new_raw.startswith("sk-") and new_raw != first["key"]["raw_key"]
        assert again["key"]["id"] != first["key"]["id"]

        live = {
            r["id"]
            for r in await w.admin.fetch(
                "SELECT id::text FROM api_keys WHERE workspace_id = $1::uuid AND revoked_at IS NULL",
                w.ws_id,
            )
        }
        assert first["key"]["id"] not in live  # this job's first key is revoked
        assert {
            again["key"]["id"],
            other_on_agent["id"],
            unbound["id"],
        } == live  # nothing else changed
        _, plain = await w.go("k-r")
        assert "raw_key" not in plain["key"] and plain["key"]["id"] == again["key"]["id"]

    run(clean_db, tmp_path, monkeypatch, body)


# ── P1-15: one paid onboarding per source; abandon ────────────────────────────


@pytest.mark.parametrize(
    "variant",
    ["https://Fixtureco.example/", "http://fixtureco.example", "HTTPS://FIXTURECO.EXAMPLE//"],
)
def test_p1_15_a_new_key_for_the_same_source_starts_nothing_new(
    clean_db, tmp_path, monkeypatch, variant
):
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def body(w):
        _, a = await w.go("k-d1", source_url="https://fixtureco.example", profile_name=None)
        _, b = await w.go("k-d2", source_url=variant, profile_name=None)
        assert b["job_id"] == a["job_id"]
        await asyncio.sleep(0.2)
        assert (
            counts["onboarding"] == 1
            and len(list(onboard_jobs._jobs_dir(w.cfg).glob("*.json"))) == 1
        )
        assert await w.n("provisioning_jobs") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_15_two_keys_racing_for_one_source_still_start_one_onboarding(
    clean_db, tmp_path, monkeypatch
):
    """Both calls can pass the lookup before either inserts; the partial unique index is the backstop."""
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def body(w):
        a, b = await asyncio.gather(
            w.go("k-race-1", source_url="https://fixtureco.example", profile_name=None),
            w.go("k-race-2", source_url="https://fixtureco.example", profile_name=None),
        )
        assert a[1]["job_id"] == b[1]["job_id"]
        await asyncio.sleep(0.2)
        assert counts["onboarding"] == 1 and await w.n("provisioning_jobs") == 1

    run(clean_db, tmp_path, monkeypatch, body)


def test_p1_15_abandon_frees_the_slot_and_is_refused_once_a_key_exists(
    clean_db, tmp_path, monkeypatch
):
    fake_onboarding(monkeypatch)

    async def body(w):
        _, parked = await park(w, "k-ab1")
        _, gone = await svc.abandon(w.pool, w.ws_id, parked["job_id"])
        assert gone["state"] == "abandoned"
        _, fresh = await park(w, "k-ab2", name="Second Agent")
        assert fresh["job_id"] != parked["job_id"]

        await w.add_profile()
        _, done = await w.go("k-ab3", name="Third Agent")
        exc = await refusal(svc.abandon(w.pool, w.ws_id, done["job_id"]))
        assert (exc.status_code, code_of(exc)) == (409, "provision_key_issued")
        assert await svc.abandon(w.pool, w.ws_id, "99999999-9999-9999-9999-999999999999") is None

    run(clean_db, tmp_path, monkeypatch, body)


# ── budget and loop guards ────────────────────────────────────────────────────


def test_a_failing_provision_repeated_twenty_times_spends_nothing_and_makes_one_job(
    clean_db, tmp_path, monkeypatch
):
    fake_onboarding(monkeypatch)
    counts = _count_spend(monkeypatch)

    async def verify(provider, key, **k):
        counts["probes"] += 1
        return VerifyResult(status="invalid_key", reason="rejected")

    monkeypatch.setattr(svc, "verify_provider_credential", verify)

    async def body(w):
        for _ in range(20):
            exc = await refusal(
                w.go(
                    "k-loop",
                    source_url="https://fixtureco.example",
                    profile_name=None,
                    integrations={"apollo": "test_x"},
                )
            )
            assert code_of(exc) == "provider_keys_invalid"
        assert counts["onboarding"] == 0
        assert await w.n("provisioning_jobs") == 1 and await w.n("agents") == 0

    run(clean_db, tmp_path, monkeypatch, body)


# ── the CLI, end to end against the same database ─────────────────────────────


def test_the_cli_runs_the_same_service_and_a_rerun_resumes_instead_of_repeating(
    clean_db, tmp_path, monkeypatch, capsys
):
    from backend import provision as cli

    monkeypatch.setenv("GTM_AGENT_PROVISION_ENABLED", "1")

    async def body(w):
        await w.add_profile()
        argv = [
            "--workspace",
            w.ws_id,
            "--name",
            "Cli Agent",
            "--profile",
            "fixtureco",
            "--state-dir",
            str(tmp_path / "state"),
        ]
        env = {"DATABASE_URL": clean_db["api_dsn"]}
        assert await cli.drive(cli.parse_args(argv), env) in (0, 3)
        first = json.loads(capsys.readouterr().out)
        assert first["key"]["raw_key"].startswith("sk-")
        assert await cli.drive(cli.parse_args(argv), env) in (0, 3)
        second = json.loads(capsys.readouterr().out)
        assert second["job_id"] == first["job_id"] and "raw_key" not in second["key"]
        assert await w.n("agents", "name = $2", "Cli Agent") == 1 and await w.n("api_keys") == 1

    run(clean_db, tmp_path, monkeypatch, body)
