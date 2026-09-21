"""p2 acceptance suite for tests/live — the RUN LIFECYCLE, proven over real HTTP.

    cancel a run parked at a gate                      -> canceled, gate cleared, one `done`
    cancel a run while a node is actively running       -> no further node reaches `running`
    the same, on the REAL executor (real_executor)       -> no further node reaches `running`
    a stack recreate while a run is gated                -> the gate is not re-announced
    a duplicate `client_request_id` (sequential)         -> same run_id, listed once
    a duplicate `client_request_id` (truly concurrent)   -> same run_id, listed once
    a fresh workspace with no entitlement grant          -> 402 cost_cap_reached
    N runs dispatched under >1 worker                    -> each: one running, one done

Two of these need something this process cannot itself provide and are gated on an
operator-declared env var rather than a bare flag: ``recreate_cmd`` (GTM_LIVE_RECREATE_CMD —
a shell command this process cannot construct, since it would recreate the very stack it is
talking to) and ``multi_worker`` (GTM_LIVE_WORKERS — a server-side BACKEND_WORKERS setting
this process cannot see). Both predicates live in tests/live/_support.py, unit-tested in
tests/live/test_support_unit.py, and are wired into tests/live/conftest.py's
pytest_collection_modifyitems exactly like real_executor/local_fake.

See DEVELOPMENT.md's "Running the tests/live HTTP acceptance suite" section for the
invocations, including the env vars the recreate/multi-worker tests below are gated on.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import shlex
import subprocess
import time
import uuid

import pytest

from tests.live._support import (
    DEFAULT_PROFILE,
    LIVE_RECREATE_CMD_ENV,
    LiveUser,
    _error_summary,
    error_envelope_problems,
)

pytestmark = pytest.mark.p2

#: Same pack/variant p0/p1 use: prospect/dossier/outreach/quality run gate-free, and
#: `sequence` (email_enroll) is the ONLY gate — so `_await_gate` always lands on it, and
#: approving it always finishes the run. That single-gate shape is what lets the recreate
#: test (below) treat "any awaiting_approval after the watermark" as unambiguously a
#: re-announcement of the SAME gate, never a legitimately later, different one.
PACK, VARIANT = "prospecting", "prospect-outreach"

#: The real-executor cancel test's variant — see that test's docstring for the choice.
REAL_PACK, REAL_VARIANT = "solution-architecture", "solution-architecture"

#: A fictional company for the real-executor test's onboarding (staging has no dev profile).
_REAL_ONBOARD_SOURCE = (
    "Harrowgate Tidewater Supply is a mid-sized distributor of marine hardware based in a "
    "fictional harbour town. It sells deck fittings, winches and mooring gear to boatyards "
    "and small commercial fleets. Its main product, Tidewater Stockline, is a subscription "
    "that keeps a yard's spares shelf restocked automatically based on usage. Customers are "
    "boatyard operations managers and fleet maintenance leads."
)

#: Terminal run statuses (schemas/content-item.schema.json). Copied, not imported — see
#: test_p1_errors.py's identical constant for why (this suite talks HTTP to a stack that
#: may be a different build from this checkout).
_TERMINAL_STATUSES = frozenset({"ok", "failed", "rejected", "canceled"})

_POLL_INTERVAL_S = 0.25


# ── local helpers (small, self-contained copies — see test_p1_errors.py's own note on why
#    each phase file duplicates rather than shares these) ──────────────────────────────────


def _assert_error(r, expected_status: int, expected_code: str | None = None) -> dict:
    problems = error_envelope_problems(
        r, expected_status=expected_status, expected_code=expected_code
    )
    assert not problems, f"{_error_summary(r)}: " + "; ".join(problems)
    return r.json()["error"]


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


def _start_prompt_run(http, user: LiveUser, profile: str, prompt: str, **extra):
    r = http.post(
        "/v1/runs",
        headers=user.auth_header(),
        json={"profile_name": profile, "prompt": prompt, **extra},
    )
    return r


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


def _await_gate(http, user: LiveUser, run_id: str) -> dict:
    detail = _poll_run(
        http, user, run_id, lambda d: d.get("status") == "awaiting_approval", what="its gate"
    )
    assert detail.get("pending_content_sha"), (
        f"run {run_id} is awaiting approval but exposes no pending_content_sha to bind a "
        "decision to"
    )
    return detail


def _await_terminal(http, user: LiveUser, run_id: str) -> dict:
    return _poll_run(
        http, user, run_id, lambda d: d.get("status") in _TERMINAL_STATUSES, what="a terminal state"
    )


def _await_health(http, *, timeout_s: float = 30.0) -> None:
    """Poll `/health` until the recreated container accepts connections again.

    `docker compose up -d --force-recreate` returns as soon as Docker reports the
    container started — the app inside (uvicorn boot, migrations re-check) is not
    necessarily ready to accept a connection yet. Without this, the very next request
    (the post-recreate SSE reconnect) can race a container that is still starting and
    raise a raw `httpx.RemoteProtocolError`/`ConnectError`, which reads as test flakiness
    rather than the thing this test actually checks — it is neither an error the recreate
    guarantee makes about health checks, nor the no-reannounce property under test."""
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


def _approve(http, user: LiveUser, run_id: str, content_sha: str):
    r = http.post(
        f"/v1/runs/{run_id}/gate",
        headers=user.auth_header(),
        json={"decision": "approve", "content_sha": content_sha},
    )
    assert r.status_code == 200, _error_summary(r)


def _full_history(sse_frames, user: LiveUser, run_id: str, *, timeout_s: float = 30.0):
    """The run's complete, authoritative event history, retried until the trailing `done`
    frame carries a durable id (see test_p1_errors.py's `_durable_done_frame` for why a
    plain single reconnect can land on the id-less synthesized terminal frame instead)."""
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"run {run_id}: its `done` event was never durably recorded within {timeout_s}s"
            )
        result = sse_frames(
            user, run_id, since=0, until=lambda f: f.event == "done", timeout_s=max(remaining, 1.0)
        )
        if result.frames[-1].id is not None:
            return result.frames


def _drive_prompt_run_to_ok(http, sse_frames, user: LiveUser, run_id: str) -> None:
    """Approve every gate a scripted prompt run opens (fake.py: one, at `draft`) until it
    reaches `done`. Mirrors test_p0_harness.py's pack-mode equivalent, for prompt mode."""
    since = 0
    while True:
        result = sse_frames(
            user,
            run_id,
            since=since,
            until=lambda f: f.event in ("awaiting_approval", "done"),
            timeout_s=60.0,
        )
        since = result.last_id
        last = result.frames[-1]
        if last.event == "done":
            assert last.data.get("status") == "ok", last.data
            return
        _approve(http, user, run_id, last.data["pending_content_sha"])


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


def _onboard_profile(http, user: LiveUser) -> str:
    """Onboard a profile over the API (ingest -> diff -> promote) and return its slug — the
    real-executor path's stand-in for `provision_local_profile`, which only reaches a local
    stack. The confirmed company name is read from the staged PROFILE.md's `company:` line,
    since the ingest response does not expose the name the model extracted. A copy of
    test_p3_onboarding.py's helpers, per the per-file duplication note above."""
    r = http.post(
        "/v1/onboard",
        headers=user.auth_header(),
        json={"source_type": "text", "source": _REAL_ONBOARD_SOURCE},
    )
    result = _await_onboard_job(http, user, r)
    draft_id, slug = result["draft_id"], result["slug"]

    r = http.get(f"/v1/onboard/{draft_id}/diff", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    profile_md = r.json()["diffs"]["PROFILE.md"]["new"]
    match = re.search(r"^company:\s+(.+?)\s*$", profile_md, re.MULTILINE)
    assert match, "staged PROFILE.md carries no `company:` line"

    r = http.post(
        f"/v1/onboard/{draft_id}/promote",
        headers=user.auth_header(),
        json={"confirmed_company_name": match.group(1)},
    )
    assert r.status_code == 200, _error_summary(r)
    return slug


# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_workspace(new_user, grant_entitlement, provision_local_profile):
    """A throwaway workspace with a pro entitlement and the dev profile provisioned — a
    FRESH one per test (see test_p1_errors.py's identical fixture for why not `live_user`)."""
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=5.0)
    return user, provision_local_profile(user)


@pytest.fixture
def track_run(http):
    """`track_run(user, run_id) -> run_id`. Every tracked run is cancelled on teardown — a
    run left parked at a gate holds a concurrency slot for 24h otherwise. See
    test_p1_errors.py's identical fixture."""
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


# ── 1: cancel a run parked at a gate ─────────────────────────────────────────────────────


@pytest.mark.local_fake
def test_cancel_at_gate_clears_the_gate_and_the_sse_replay_has_exactly_one_done(
    http, fake_workspace, track_run, sse_frames
):
    """Cancelling a gated run must clear the gate on the poll surface (M-12: same fields on
    the list row) AND leave exactly one durable `done` in the SSE history — a second one
    would mean the cancel path and the executor's own completion path both terminated the
    run (ST-15's regression, proven here for cancel rather than reject)."""
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    detail = _await_gate(http, user, run_id)
    assert detail.get("gate") == "email_enroll", (
        f"expected prospect-outreach's only gate, got gate={detail.get('gate')!r}"
    )

    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    assert r.json() == {"run_id": run_id, "status": "canceled"}

    terminal = _await_terminal(http, user, run_id)
    assert terminal["status"] == "canceled"
    assert terminal.get("gate") is None, "cancel must clear the gate on the poll surface"
    assert terminal.get("pending_node_id") is None
    assert terminal.get("pending_content") is None
    assert terminal.get("pending_content_sha") is None

    # M-12: GET /v1/runs's list row carries the same gate/pending_node_id fields.
    listed = http.get("/v1/runs", headers=user.auth_header(), params={"limit": 20})
    assert listed.status_code == 200, _error_summary(listed)
    (row,) = [row for row in listed.json() if row["run_id"] == run_id]
    assert row.get("gate") is None
    assert row.get("pending_node_id") is None

    full = _full_history(sse_frames, user, run_id)
    done_frames = [f for f in full if f.event == "done"]
    assert len(done_frames) == 1, (
        f"expected exactly one `done` frame in the replay, got {len(done_frames)}"
    )
    assert done_frames[0].data.get("status") == "canceled"


# ── 2: cancel while a node is actively running (not yet at a gate) ──────────────────────


@pytest.mark.local_fake
def test_cancel_mid_node_stops_further_dispatch(http, fake_workspace, track_run, sse_frames):
    """Cancel raised while a NODE (not a gate) is in flight. `backend/services/runs/fake.py`
    checks `_cancelled_runs` only BETWEEN nodes — an in-flight node's own sleep/completion
    runs to completion, mirroring the real pack runner's "an in-flight batch runs to
    completion" contract — so the property to prove is narrower than "nothing happens after
    cancel": no node ever reaches `running` (data.state == "running", per the `node` event
    branch of schemas/run-event.schema.json) again once cancelled. `prospect` (the first of
    prospect-outreach's five nodes) holds no gate, so a run freshly started is reliably
    mid-node, not mid-gate, the moment its first `node running` frame arrives.

    This assumes the stack under test was started with a big enough GTM_FAKE_RUN_DELAY_S
    (backend/services/runs/fake.py's DELAY_ENV) that a node dispatch stays in flight long
    enough for this process's cancel POST to land before the run would otherwise move on to
    the next node on its own — a server-side setting this test can only rely on, never set.
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))

    first_running = sse_frames(
        user,
        run_id,
        since=0,
        until=lambda f: f.event == "node" and f.data.get("state") == "running",
        timeout_s=30.0,
    )
    watermark = first_running.last_id

    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)

    after = sse_frames(
        user, run_id, since=watermark, until=lambda f: f.event == "done", timeout_s=30.0
    )
    newly_running = [
        f for f in after.frames if f.event == "node" and f.data.get("state") == "running"
    ]
    assert newly_running == [], (
        f"a node reached 'running' after cancel: {[f.data.get('node_id') for f in newly_running]}"
    )
    assert after.frames[-1].event == "done"
    assert after.frames[-1].data.get("status") == "canceled"


@pytest.mark.real_executor
def test_real_executor_cancel_mid_node_stops_further_dispatch(
    http, new_user, grant_entitlement, track_run, sse_frames
):
    """The local_fake test above, on the REAL executor: once a real node is in flight, a
    cancel must stop the runner from dispatching the next one. The in-flight node may run to
    completion (the pack runner's contract), so the property is the same narrow one: no node
    reaches `running` after the cancel, and the run ends `canceled`.

    Variant: `solution-architecture/solution-architecture`. It is a strict chain of four
    nodes (discovery -> design -> runbook -> deck), so "the next node" is well defined and a
    concurrent fan-out cannot put two nodes in flight before the cancel; its floor is `free`
    and it asks for no settings, so a freshly onboarded profile runs it unblocked; and its
    first node, `solution-discovery`, runs on free paths by default and reaches its metered
    tools (Firecrawl, Vibe) only on an explicit `deep` opt-in this run never gives — no
    RocketReach/Vibe/Apollo/crawl spend. Every other multi-node chain a pro workspace may run
    starts on a paid provider (`prospect`) or a subscription feed (`community-signal-analysis`
    on Syften); `planning` is a concurrent fan-out.

    Expected spend: one onboarding extraction (cents) plus one brain-plan node, well under
    the 1.0 USD cap, which bounds it regardless. A real node can take minutes, hence the
    300s waits. If the stack sets COST_RESERVATION_ENABLED, its per-run estimate
    (RESERVATION_ESTIMATE_USD, default 2.0) exceeds this cap and the run fails at dispatch
    before any node runs — a stack-configuration mismatch, not a cancel defect.
    """
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=1.0)
    profile = _onboard_profile(http, user)
    run_id = track_run(user, _start_run(http, user, profile, pack=REAL_PACK, variant=REAL_VARIANT))

    first_running = sse_frames(
        user,
        run_id,
        since=0,
        until=lambda f: f.event == "node" and f.data.get("state") == "running",
        timeout_s=300.0,
    )
    watermark = first_running.last_id

    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)

    after = sse_frames(
        user, run_id, since=watermark, until=lambda f: f.event == "done", timeout_s=300.0
    )
    newly_running = [
        f for f in after.frames if f.event == "node" and f.data.get("state") == "running"
    ]
    assert newly_running == [], (
        f"a node reached 'running' after cancel: {[f.data.get('node_id') for f in newly_running]}"
    )
    assert after.frames[-1].event == "done"
    assert after.frames[-1].data.get("status") == "canceled"
    assert _await_terminal(http, user, run_id)["status"] == "canceled"


# ── 3: a stack recreate while a run is gated does not re-announce the gate ──────────────


@pytest.mark.recreate_cmd
@pytest.mark.local_fake
def test_recreate_while_gated_does_not_reannounce_the_gate(
    http, fake_workspace, track_run, sse_frames
):
    """FL13/ST-06+RL-13: a boot reconcile landing on a gate already open on the exact bytes
    it was last shown must not re-announce it. This is the acceptance-level proof, over real
    HTTP, of what test_p1_errors.py's `test_cancelling_an_already_terminal_run_is_409...`
    docstring notes is already true on this branch for the reconcile path itself — here
    proven for the client-visible SSE surface across an actual process recreate.
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))

    opened = sse_frames(
        user, run_id, since=0, until=lambda f: f.event == "awaiting_approval", timeout_s=60.0
    )
    gate_frame = opened.frames[-1]
    watermark = opened.last_id
    content_sha = gate_frame.data["pending_content_sha"]

    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args -- argv list, never a shell (`shell=False` is the default, and `shlex.split` only tokenises), so there is no shell for a metacharacter to reach. The value is GTM_LIVE_RECREATE_CMD, set by the operator invoking pytest — the same person who could run the command directly; it is never tenant, request, or network input, and `recreate_cmd_skip_reason` collection-skips this test unless they set it. No privilege boundary is crossed.
    subprocess.run(shlex.split(os.environ[LIVE_RECREATE_CMD_ENV]), check=True, timeout=120)
    _await_health(http)

    # No SECOND awaiting_approval for this run within a bounded window after the recreate —
    # a TimeoutError (nothing matched) is the PASSING outcome; an actual match is the bug.
    try:
        reannounced = sse_frames(
            user,
            run_id,
            since=watermark,
            until=lambda f: f.event == "awaiting_approval",
            timeout_s=15.0,
        )
    except TimeoutError:
        pass
    else:
        pytest.fail(f"the gate was re-announced after recreate (seq={reannounced.frames[-1].id})")

    _approve(http, user, run_id, content_sha)
    terminal = _await_terminal(http, user, run_id)
    assert terminal["status"] == "ok", (
        f"expected the run to reach ok after the post-recreate approval, got {terminal['status']!r}"
    )


# ── 4: a duplicate client_request_id is idempotent, sequential and concurrent ───────────


@pytest.mark.local_fake
def test_a_duplicate_client_request_id_returns_the_same_run_and_is_listed_once(
    http, fake_workspace, track_run
):
    """RT-04's fast-path replay check: a retry of an already-admitted request (the realistic
    case — a lost 202 on a mobile network) must return the ORIGINAL run, never a second one
    that would burn a second concurrency slot and spend budget twice."""
    user, profile = fake_workspace
    cid = f"live-test-{uuid.uuid4().hex}"
    body = {
        "profile_name": profile,
        "pack": PACK,
        "variant": VARIANT,
        "inputs": {},
        "client_request_id": cid,
    }

    r1 = http.post("/v1/runs", headers=user.auth_header(), json=body)
    assert r1.status_code == 202, _error_summary(r1)
    run_id = track_run(user, r1.json()["run_id"])

    r2 = http.post("/v1/runs", headers=user.auth_header(), json=body)
    assert r2.status_code == 202, _error_summary(r2)
    assert r2.json()["run_id"] == run_id, "a duplicate client_request_id must return the SAME run"

    listed = http.get("/v1/runs", headers=user.auth_header(), params={"limit": 50})
    assert listed.status_code == 200, _error_summary(listed)
    matches = [row for row in listed.json() if row["run_id"] == run_id]
    assert len(matches) == 1, f"expected exactly one row for {run_id}, found {len(matches)}"


@pytest.mark.local_fake
def test_a_truly_concurrent_duplicate_client_request_id_still_returns_one_run(
    http, fake_workspace, track_run
):
    """The sequential test above proves the fast-path replay check; this fires both POSTs
    from separate threads so they race for real over the wire, proving the actual
    `ON CONFLICT (workspace_id, client_request_id) ... DO NOTHING` backstop
    (`insert_run_row`'s own docstring) — the case the fast path cannot cover because both
    requests can reach admission before either has committed a row.
    """
    user, profile = fake_workspace
    cid = f"live-test-{uuid.uuid4().hex}"
    body = {
        "profile_name": profile,
        "pack": PACK,
        "variant": VARIANT,
        "inputs": {},
        "client_request_id": cid,
    }

    def _post():
        return http.post("/v1/runs", headers=user.auth_header(), json=body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_post), pool.submit(_post)]
        r1, r2 = (f.result() for f in futures)

    assert r1.status_code == 202, _error_summary(r1)
    assert r2.status_code == 202, _error_summary(r2)
    run_id_1, run_id_2 = r1.json()["run_id"], r2.json()["run_id"]
    assert run_id_1 == run_id_2, "two truly concurrent duplicate requests must agree on one run_id"
    track_run(user, run_id_1)

    listed = http.get("/v1/runs", headers=user.auth_header(), params={"limit": 50})
    assert listed.status_code == 200, _error_summary(listed)
    matches = [row for row in listed.json() if row["run_id"] == run_id_1]
    assert len(matches) == 1, f"expected exactly one row for {run_id_1}, found {len(matches)}"


# ── 5: a fresh workspace with no entitlement grant gets 402, not a run ──────────────────


def test_a_fresh_workspace_with_no_entitlement_grant_gets_402_cost_cap_reached(http, new_user):
    """A free-plan workspace is clamped to a 0 cost cap by construction (CLAUDE.md's billing
    boundary) — the very first run request must refuse synchronously with the SAME
    `admits()` check RL-08/M-06 added before any row is written, never silently queue a run
    nobody can afford. Prompt mode deliberately: it skips `resolve_pack_for_run` entirely
    (no pack requested), so this is a clean probe of the budget check alone, unclouded by
    pack-activation/entitlement-for-packs refusals that would 403/404 first.
    """
    user = new_user()
    r = _start_prompt_run(http, user, DEFAULT_PROFILE, "Draft a short market update.")
    _assert_error(r, 402, "cost_cap_reached")


# ── 6: N runs across >1 worker are each dispatched and completed exactly once ────────────


@pytest.mark.multi_worker
def test_multi_worker_exactly_once_dispatch_and_completion(
    http, new_user, grant_entitlement, sse_frames
):
    """Five runs across two workspaces, driven to `ok`; each run's full durable history
    must show exactly one running-equivalent status transition and exactly one `done` — the
    signature a run dispatched by TWO workers under FOR UPDATE SKIP LOCKED would leave
    behind if the claim were not exclusive. This is the honest limit of what the HTTP/SSE
    surface alone can prove (per CLAUDE.md's `verification-before-completion` discipline):
    it cannot see which worker process claimed which row, only the durable event log every
    worker's dispatch is required to write to — "no double green light, no double done" is
    exactly that log's property, and is what this asserts.
    """
    workspaces = []
    for _ in range(2):
        user = new_user()
        grant_entitlement(user, entitlement="pro", cap_usd=5.0)
        workspaces.append(user)

    runs: list[tuple[LiveUser, str]] = []
    for i in range(5):
        user = workspaces[i % 2]
        r = _start_prompt_run(http, user, DEFAULT_PROFILE, f"Draft update number {i}.")
        assert r.status_code == 202, _error_summary(r)
        runs.append((user, r.json()["run_id"]))

    for user, run_id in runs:
        _drive_prompt_run_to_ok(http, sse_frames, user, run_id)

    for user, run_id in runs:
        full = _full_history(sse_frames, user, run_id)
        running = [f for f in full if f.event == "status" and f.data.get("status") == "running"]
        done = [f for f in full if f.event == "done"]
        assert len(running) == 1, (
            f"run {run_id}: expected exactly one running transition, saw {len(running)}"
        )
        assert len(done) == 1, f"run {run_id}: expected exactly one done frame, saw {len(done)}"
