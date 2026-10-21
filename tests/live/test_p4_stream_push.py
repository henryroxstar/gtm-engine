"""p4 acceptance suite for tests/live — the SSE stream contract (ST-01 and `?since=`
resume), proven over real HTTP against the local fake-run stack. ST-16's resync needs a
failed database write, which HTTP cannot trigger, so it is proven in-process in
tests/backend/test_stream_protocol2.py.

    a reconnect's snapshot carries the open gate's identity (gate/pending_node_id/
    pending_content_sha) — not just the legacy pending_gate sentinel
    `?since=` resumes exactly where a client left off, replaying only what it missed

Push delivery itself (ST-02/ST-03/ST-13) is proven in tests/backend/test_push_fcm_v1.py
with an injected transport — there is no FCM service account on this stack (PENDING.md
FL11: deferred pending the app's bundle IDs), so A4d's live credential check is not
reachable here; see PENDING.md for what that leaves unverified.
"""

from __future__ import annotations

import time

import pytest

from tests.live._support import LiveUser, _error_summary

pytestmark = pytest.mark.p4

#: Same pack/variant test_p2_lifecycle.py uses: `sequence` (email_enroll) is its only
#: gate, so a fresh run always lands on exactly one predictable pause.
PACK, VARIANT = "prospecting", "prospect-outreach"

_POLL_INTERVAL_S = 0.25


def _start_run(http, user: LiveUser, profile: str) -> str:
    r = http.post(
        "/v1/runs",
        headers=user.auth_header(),
        json={"profile_name": profile, "pack": PACK, "variant": VARIANT, "inputs": {}},
    )
    assert r.status_code == 202, _error_summary(r)
    return r.json()["run_id"]


def _await_gate(http, user: LiveUser, run_id: str, *, timeout_s: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        r = http.get(f"/v1/runs/{run_id}", headers=user.auth_header())
        assert r.status_code == 200, _error_summary(r)
        detail = r.json()
        if detail.get("status") == "awaiting_approval":
            assert detail.get("pending_content_sha"), (
                f"run {run_id} is awaiting approval but exposes no pending_content_sha"
            )
            return detail
        if time.monotonic() > deadline:
            raise TimeoutError(f"run {run_id} never reached its gate within {timeout_s}s")
        time.sleep(_POLL_INTERVAL_S)


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
    run left parked at a gate holds a concurrency slot for 24h otherwise."""
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


@pytest.mark.local_fake
def test_reconnect_snapshot_carries_the_open_gates_identity(
    http, fake_workspace, track_run, sse_frames
):
    """ST-01: before this fix, a client that (re)connected while a gate was open saw only
    the legacy `pending_gate` sentinel on the snapshot frame and had to make a
    supplementary GET to learn the kind/node — this proves the snapshot now carries
    exactly what GET /v1/runs/{id} does."""
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    polled = _await_gate(http, user, run_id)

    # A fresh connect (no `?since=`) always opens on the snapshot path.
    result = sse_frames(user, run_id, until=lambda f: f.event == "snapshot", timeout_s=30.0)
    snapshot = result.frames[0]
    assert snapshot.event == "snapshot"
    assert snapshot.data.get("gate") == polled["gate"] == "email_enroll"
    assert snapshot.data.get("pending_node_id") == polled["pending_node_id"]
    assert snapshot.data.get("pending_content_sha") == polled["pending_content_sha"]


@pytest.mark.local_fake
def test_since_resumes_exactly_where_the_client_left_off(
    http, fake_workspace, track_run, sse_frames
):
    """A client that connects while the run is ALREADY gated (the common case — it polled
    or was pushed, then opened the stream) lands on the snapshot path, not a live
    `awaiting_approval` event: that event already fired before this connection existed, so
    it is never replayed on a fresh connect — only reflected in the snapshot's `gate`
    fields (ST-01). Disconnecting there and reconnecting with `?since=<the snapshot's
    id>` must see no SECOND snapshot and no synthesised re-announcement of the same gate —
    only what happened after, once the operator decides it."""
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    _await_gate(http, user, run_id)

    opening = sse_frames(user, run_id, until=lambda f: f.event == "snapshot", timeout_s=30.0)
    snapshot = opening.frames[0]
    assert snapshot.id is not None, "the snapshot's id is the resume watermark"
    assert snapshot.data.get("gate") == "email_enroll"

    sha = snapshot.data["pending_content_sha"]
    r = http.post(
        f"/v1/runs/{run_id}/gate",
        headers=user.auth_header(),
        json={"decision": "approve", "content_sha": sha},
    )
    assert r.status_code == 200, _error_summary(r)

    resumed = sse_frames(
        user, run_id, since=snapshot.id, until=lambda f: f.event == "done", timeout_s=60.0
    )
    events = [f.event for f in resumed.frames]
    assert "snapshot" not in events, (
        "?since= must never re-snapshot — that would replay stale state"
    )
    assert "awaiting_approval" not in events, "the already-seen gate must not be re-delivered"
    assert events[-1] == "done"
    assert resumed.frames[-1].data.get("status") == "ok"
