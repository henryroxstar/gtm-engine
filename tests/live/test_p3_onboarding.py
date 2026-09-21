"""p3 acceptance suite for tests/live — ONBOARDING, proven over real HTTP.

    ingest -> recreate -> diff -> promote -> the profile runs   -> survives a restart (M-10)
    ingest -> promote -> default profile -> packs -> a run       -> onboarding yields a runnable default
    workspace B touching workspace A's draft                     -> 404 on every path (TN-03)
    the same flow against the REAL extractor                     -> real_executor only

The local tests rely on the stack's scripted extractor (``GTM_FAKE_RUNS=1`` with
``ENV=development`` — ``backend/services/onboard_fake.py``): the company name is the first
non-blank line of the source text, so the slug is known in advance. The real-executor test
cannot know the name the model will extract, so it reads it back from the staged
``PROFILE.md`` in the diff response (the ingest response does not expose it) and confirms
exactly that.

See DEVELOPMENT.md's "Running the tests/live HTTP acceptance suite" section for the
invocations and the env vars the gated tests below need.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
import uuid

import pytest

from tests.live._support import LIVE_RECREATE_CMD_ENV, LiveUser, _error_summary

pytestmark = pytest.mark.p3

#: A pro entitlement admits it, it asks for no settings, and a freshly onboarded profile's
#: default packs.toml activates it with nothing blocked — the same variant p0/p2 drive.
PACK, VARIANT = "prospecting", "prospect-outreach"

#: For the real-executor test: free-tier floor, no required ask-settings, a linear chain
#: whose first node (`solution-discovery`) uses metered tools only on an explicit opt-in.
#: The run is cancelled right after its 202 regardless, so at most one node's worth of
#: model spend is at stake.
REAL_PACK, REAL_VARIANT = "solution-architecture", "solution-architecture"

#: Copied, not imported — see test_p1_errors.py's identical constant for why.
_TERMINAL_STATUSES = frozenset({"ok", "failed", "rejected", "canceled"})

_POLL_INTERVAL_S = 0.25

#: The local tests' source text after its first line (the company name, from `_company`).
_ABOUT = "We refurbish and lease marine winches.\nFamily-run, three yards on the south coast.\n"

_REAL_SOURCE = (
    "Harrowgate Tidewater Supply is a mid-sized distributor of marine hardware based in a "
    "fictional harbour town. It sells deck fittings, winches and mooring gear to boatyards "
    "and small commercial fleets. Its main product, Tidewater Stockline, is a subscription "
    "that keeps a yard's spares shelf restocked automatically based on usage. Customers are "
    "boatyard operations managers and fleet maintenance leads who are tired of downtime "
    "caused by missing parts. Competitors are general chandlers and large catalogue "
    "wholesalers. The company writes in a plain, practical, no-nonsense voice."
)


# ── local helpers (small, self-contained copies — see test_p1_errors.py's own note on why
#    each phase file duplicates rather than shares these) ──────────────────────────────────


def _company() -> tuple[str, str]:
    """A unique fictional company name and the slug the scripted extractor derives from it —
    unique per call, so repeated runs against one stack never collide on a slug."""
    tag = uuid.uuid4().hex[:8]
    return f"Quillfield Winch Works {tag}", f"quillfield-winch-works-{tag}"


def _await_onboard_job(http, user: LiveUser, r, *, timeout_s: float = 300.0) -> dict:
    """Follow a 202 onboarding job (issue #259) to success and return its result."""
    assert r.status_code == 202, _error_summary(r)
    job = r.json()
    deadline = time.monotonic() + timeout_s
    while job["status"] in ("pending", "running"):
        assert time.monotonic() < deadline, f"onboarding job never finished: {job}"
        time.sleep(0.5)
        r = http.get(f"/v1/onboard/jobs/{job['job_id']}", headers=user.auth_header())
        assert r.status_code == 200, _error_summary(r)
        job = r.json()
    assert job["status"] == "succeeded", f"onboarding job failed: {job['error']}"
    return job["result"]


def _ingest(http, user: LiveUser, source: str) -> dict:
    r = http.post(
        "/v1/onboard",
        headers=user.auth_header(),
        json={"source_type": "text", "source": source},
    )
    body = _await_onboard_job(http, user, r)
    assert body["draft_id"] and body["slug"], "ingest returned no draft_id/slug"
    return body


def _diff(http, user: LiveUser, draft_id: str):
    return http.get(f"/v1/onboard/{draft_id}/diff", headers=user.auth_header())


def _promote(http, user: LiveUser, draft_id: str, company_name: str):
    return http.post(
        f"/v1/onboard/{draft_id}/promote",
        headers=user.auth_header(),
        json={"confirmed_company_name": company_name},
    )


def _staged_company_name(diff_body: dict) -> str:
    """The company name the draft was staged under, read from the staged PROFILE.md's
    `company:` line (agent/onboard/render_profile.py) — the ingest response has no field
    for it."""
    profile_md = diff_body["diffs"]["PROFILE.md"]["new"]
    match = re.search(r"^company:\s+(.+?)\s*$", profile_md, re.MULTILINE)
    assert match, "staged PROFILE.md carries no `company:` line"
    return match.group(1)


def _start_run(http, user: LiveUser, profile: str, *, pack=PACK, variant=VARIANT, inputs=None):
    r = http.post(
        "/v1/runs",
        headers=user.auth_header(),
        json={
            "profile_name": profile,
            "pack": pack,
            "variant": variant,
            "inputs": dict(inputs or {}),
        },
    )
    assert r.status_code == 202, _error_summary(r)
    return r.json()["run_id"]


def _poll_run(http, user: LiveUser, run_id: str, ready, *, what: str, timeout_s: float = 90.0):
    deadline = time.monotonic() + timeout_s
    while True:
        r = http.get(f"/v1/runs/{run_id}", headers=user.auth_header())
        assert r.status_code == 200, _error_summary(r)
        detail = r.json()
        if ready(detail):
            return detail
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"run {run_id} never reached {what} within {timeout_s}s "
                f"(status={detail.get('status')!r})"
            )
        time.sleep(_POLL_INTERVAL_S)


def _cancel_and_await_canceled(http, user: LiveUser, run_id: str, *, timeout_s: float = 90.0):
    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    terminal = _poll_run(
        http,
        user,
        run_id,
        lambda d: d.get("status") in _TERMINAL_STATUSES,
        what="a terminal state",
        timeout_s=timeout_s,
    )
    assert terminal["status"] == "canceled", terminal["status"]


def _await_health(http, *, timeout_s: float = 30.0) -> None:
    """Poll `/health` until the recreated container accepts connections again — see
    test_p2_lifecycle.py's identical helper for why the recreate command returning is not
    enough."""
    import httpx

    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = http.get("/health", timeout=5.0)
        except httpx.TransportError as exc:  # noqa: PERF203 — the retry IS the point
            last_exc = exc
        else:
            if r.status_code == 200:
                return
            last_exc = AssertionError(f"/health -> {r.status_code}")
        time.sleep(0.25)
    raise TimeoutError(f"container did not become healthy within {timeout_s}s: {last_exc!r}")


def _assert_runnable_default(http, user: LiveUser, slug: str, pack: str, variant: str) -> None:
    """The promoted profile is the workspace's only, default, active profile, and the pack
    catalog (computed against the default profile) lists the variant about to be run."""
    r = http.get("/v1/profiles", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    body = r.json()
    assert [(p["profile_name"], p["is_default"]) for p in body["profiles"]] == [(slug, True)]
    assert body["active"] == slug

    r = http.get("/v1/packs", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    listed = {(d["pack"], d["variant"]) for d in r.json()}
    assert (pack, variant) in listed, f"{pack}/{variant} is not listed for the onboarded profile"


def _onboard_and_promote(http, user: LiveUser, source: str, company: str, slug: str) -> str:
    """Ingest a scripted draft, promote it, and check the promote response. Returns draft_id."""
    ingested = _ingest(http, user, source)
    assert ingested["slug"] == slug
    draft_id = ingested["draft_id"]
    r = _promote(http, user, draft_id, company)
    assert r.status_code == 200, _error_summary(r)
    body = r.json()
    assert body["slug"] == slug
    assert body["is_default"] is True
    return draft_id


# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def pro_user(new_user, grant_entitlement):
    """A FRESH throwaway workspace with a pro entitlement and no profile — onboarding is
    what gives it one. `new_user`'s teardown deletes the account, which removes the
    workspace's data tree (staged drafts and the promoted profile) with it."""
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=5.0)
    return user


@pytest.fixture
def track_run(http):
    """`track_run(user, run_id) -> run_id`. Every tracked run is cancelled on teardown — see
    test_p2_lifecycle.py's identical fixture."""
    started: list[tuple[LiveUser, str]] = []

    def _track(user: LiveUser, run_id: str) -> str:
        started.append((user, run_id))
        return run_id

    yield _track

    for user, run_id in started:
        try:
            http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
        except Exception:  # noqa: BLE001 — teardown must not mask the test's own result
            pass


# ── 1: a staged draft survives a stack recreate, then promotes into a runnable profile ──


@pytest.mark.recreate_cmd
@pytest.mark.local_fake
def test_an_onboarded_profile_survives_a_recreate_and_runs(http, pro_user, track_run):
    """M-10 at acceptance level: the draft registry is process memory, so the only thing
    that can carry a draft across a recreate is the staged tree on the data volume. Diff and
    promote after the recreate must both resolve it, and the promoted profile must be the
    workspace's runnable default."""
    user = pro_user
    company, slug = _company()
    ingested = _ingest(http, user, f"{company}\n{_ABOUT}")
    assert ingested["slug"] == slug
    draft_id = ingested["draft_id"]

    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args -- argv list, never a shell (`shell=False` is the default, and `shlex.split` only tokenises), so there is no shell for a metacharacter to reach. The value is GTM_LIVE_RECREATE_CMD, set by the operator invoking pytest — the same person who could run the command directly; it is never tenant, request, or network input, and `recreate_cmd_skip_reason` collection-skips this test unless they set it. No privilege boundary is crossed.
    subprocess.run(shlex.split(os.environ[LIVE_RECREATE_CMD_ENV]), check=True, timeout=120)
    _await_health(http)

    r = _diff(http, user, draft_id)
    assert r.status_code == 200, f"the draft did not survive the recreate: {_error_summary(r)}"
    assert r.json()["slug"] == slug

    r = _promote(http, user, draft_id, company)
    assert r.status_code == 200, _error_summary(r)
    assert r.json()["slug"] == slug
    assert r.json()["is_default"] is True

    assert _diff(http, user, draft_id).status_code == 404, "a promoted draft must be gone"
    _assert_runnable_default(http, user, slug, PACK, VARIANT)

    run_id = track_run(user, _start_run(http, user, slug))
    _cancel_and_await_canceled(http, user, run_id)


# ── 2: the same flow without the recreate ───────────────────────────────────────────────


@pytest.mark.local_fake
def test_onboarding_promote_yields_a_runnable_default_profile(http, pro_user, track_run):
    """Onboarding is the whole setup a new workspace needs: ingest -> promote leaves one
    default profile the pack catalog lists runs for, and a run on it is admitted."""
    user = pro_user
    company, slug = _company()
    draft_id = _onboard_and_promote(http, user, f"{company}\n{_ABOUT}", company, slug)

    assert _diff(http, user, draft_id).status_code == 404, "a promoted draft must be gone"
    _assert_runnable_default(http, user, slug, PACK, VARIANT)

    run_id = track_run(user, _start_run(http, user, slug))
    _cancel_and_await_canceled(http, user, run_id)


# ── 3: another workspace cannot see, promote, or cancel the draft (TN-03) ──────────────


@pytest.mark.local_fake
def test_a_second_workspace_cannot_touch_the_first_workspaces_draft(http, new_user):
    """A draft_id is a bearer-free UUID: knowing it must give another workspace nothing. 404
    (never 403) on every path, so the draft's existence is not leaked either — and none of
    those attempts may disturb the owner's draft."""
    owner, intruder = new_user(), new_user()
    company, slug = _company()
    draft_id = _ingest(http, owner, f"{company}\n{_ABOUT}")["draft_id"]

    assert _diff(http, intruder, draft_id).status_code == 404
    assert _promote(http, intruder, draft_id, company).status_code == 404
    r = http.delete(f"/v1/onboard/{draft_id}", headers=intruder.auth_header())
    assert r.status_code == 404, _error_summary(r)

    r = http.get("/v1/profiles", headers=intruder.auth_header())
    assert r.status_code == 200, _error_summary(r)
    assert r.json()["profiles"] == []

    assert _diff(http, owner, draft_id).status_code == 200
    r = _promote(http, owner, draft_id, company)
    assert r.status_code == 200, _error_summary(r)
    assert r.json()["slug"] == slug


# ── 4: the real extractor (staging) ─────────────────────────────────────────────────────


@pytest.mark.real_executor
def test_real_extraction_onboards_a_runnable_profile(http, new_user, grant_entitlement, track_run):
    """The same flow as test 2 with a model doing the extraction. Only structure is
    asserted — the draft's content is the model's. The confirmed company name is read back
    from the staged PROFILE.md, because that is the name promote checks against.

    Spend: one onboarding extraction (cents) plus whatever the run's first node spends
    before the immediate cancel lands — bounded by the 1.0 USD cap. If the stack sets
    COST_RESERVATION_ENABLED, its per-run estimate (RESERVATION_ESTIMATE_USD, default 2.0)
    exceeds that cap and the run fails ``cost_cap_reached`` at dispatch instead of being
    cancelled — a stack-configuration mismatch, not an onboarding defect.
    """
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=1.0)

    ingested = _ingest(http, user, _REAL_SOURCE)
    draft_id, slug = ingested["draft_id"], ingested["slug"]

    r = _diff(http, user, draft_id)
    assert r.status_code == 200, _error_summary(r)
    company = _staged_company_name(r.json())

    r = _promote(http, user, draft_id, company)
    assert r.status_code == 200, _error_summary(r)
    assert r.json()["slug"] == slug
    assert r.json()["is_default"] is True
    _assert_runnable_default(http, user, slug, REAL_PACK, REAL_VARIANT)

    run_id = track_run(user, _start_run(http, user, slug, pack=REAL_PACK, variant=REAL_VARIANT))
    _cancel_and_await_canceled(http, user, run_id, timeout_s=300.0)
