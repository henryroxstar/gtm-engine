"""p0 sanity suite for tests/live — proves the D1 scaffold (fixtures, gating, SSE parsing)
actually works end to end against a running stack, before p1..p6 build on it.

    GET /health                                                           -> test_health
    register -> GET /v1/workspace -> DELETE /v1/account -> unreachable     -> account lifecycle
    grant_entitlement(pro) -> GET /v1/workspace/subscriptions/me          -> entitlement sync
    provision profile -> GET /v1/packs -> pack-mode run -> gate -> ok     -> local_fake pack run
    reconnect with ?since=<mid id> replays the same frames, in order      -> sse reconnect

See DEVELOPMENT.md's "Running the tests/live HTTP acceptance suite" section for the
local/staging invocations.
"""

from __future__ import annotations

import pytest

from tests.live._support import _error_summary

pytestmark = pytest.mark.p0

#: prospect-outreach is the one shipped variant whose gate is `email_enroll` (the
#: `sequence` node — CLAUDE.md's email-sequencing gate), and it is one of the packs
#: scripts/dev_seed.py's DEV_PROFILE_FIXTURES activates (packs.toml: marketing,
#: prospecting, creator), so provision_local_profile's fixtures are sufficient readiness
#: for it with no extra provisioning here.
PACK, VARIANT = "prospecting", "prospect-outreach"


def test_health(http):
    r = http.get("/health")
    assert r.status_code == 200, _error_summary(r)


def test_account_lifecycle_register_read_delete_then_unreachable(http, new_user):
    user = new_user()

    r = http.get("/v1/workspace", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    assert r.json()["id"] == user.workspace_id

    r = http.request(
        "DELETE",
        "/v1/account",
        json={"current_password": user.password},
        headers=user.auth_header(),
    )
    assert r.status_code == 200, _error_summary(r)
    user.deleted = True  # tell new_user's teardown this account is already gone

    # Known gap (session revocation backlog): require_auth only decodes the still-
    # cryptographically-valid JWT and fetches entitlement (defaulting to FREE when the
    # subscriptions row is gone) — it never itself checks that the workspace still exists.
    # get_workspace happens to do that check (backend/routers/workspaces.py: `if row is None:
    # raise HTTPException(404, ...)`), so a deleted account's DATA is unreachable either way —
    # but the token is not rejected at the auth layer the way a revoked/expired one would be,
    # so the status code a given route returns is not itself part of the contract here. See
    # the paired xfail below.
    r = http.get("/v1/workspace", headers=user.auth_header())
    assert r.status_code in (401, 404), _error_summary(r)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known gap: require_auth still accepts a deleted account's unexpired access token "
        "(session revocation backlog)"
    ),
)
def test_a_deleted_accounts_token_is_rejected_outright(http, new_user):
    """Documents the target behavior once session revocation lands: a deleted account's
    access token should 401 at the auth layer itself (require_auth), not merely 404
    downstream because a particular route happens to check the workspace still exists.
    `strict=True` so this fails loudly (XPASS) the moment that ships, as a reminder to
    delete the xfail marker and fold this assertion into the test above."""
    user = new_user()
    r = http.request(
        "DELETE",
        "/v1/account",
        json={"current_password": user.password},
        headers=user.auth_header(),
    )
    assert r.status_code == 200, _error_summary(r)
    user.deleted = True

    r = http.get("/v1/workspace", headers=user.auth_header())
    assert r.status_code == 401, _error_summary(r)


def test_entitlement_grant_reflects_on_subscriptions_me(http, new_user, grant_entitlement):
    user = new_user()

    grant_entitlement(user, entitlement="pro", cap_usd=1)

    r = http.get("/v1/workspace/subscriptions/me", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    body = r.json()
    assert body["entitlement"] == "pro"
    assert body["monthly_cost_cap_usd"] == 1


def _drive_pack_run_to_completion(http, sse_frames, user, profile: str):
    """POST a prospect-outreach pack run, approve every gate it opens in order (recording
    (node_id, gate kind) pairs seen), and return (run_id, every frame across the whole run in
    id order, gates_seen).

    The FIRST connect uses ``since=0``, never ``since=None`` (a fresh snapshot): a snapshot
    frame carries none of protocol 1's `gate`/`node_id`/`pending_content_sha` fields, only the
    raw `pending_gate`/`pending_content` — so if `hold_gate` writes the run row's
    `awaiting_approval` status before the relay durably records the matching event (the relay
    assigns the durable id asynchronously — see backend/services/runs/events.py), the live
    `awaiting_approval` EVENT can arrive at a seq already <= the snapshot's watermark and get
    silently folded in (`_live_frames`'s dedup) — never matching `until`. `since=0` sidesteps
    this: per `_read_opening` (backend/services/runs/stream.py), `0 <= since <= head` is
    always true, so the FIRST connect ALSO takes the replay path (`run_events WHERE id > 0`)
    and gets the real, fielded event object — no snapshot involved at all. Later reconnects
    use ?since=<last id>, so the returned frame list has no duplicated history either.
    """
    r = http.post(
        "/v1/runs",
        headers=user.auth_header(),
        json={"profile_name": profile, "pack": PACK, "variant": VARIANT, "inputs": {}},
    )
    assert r.status_code == 202, _error_summary(r)
    run_id = r.json()["run_id"]

    all_frames = []
    gates_seen: list[tuple[str, str]] = []
    since = 0
    while True:
        result = sse_frames(
            user,
            run_id,
            since=since,
            until=lambda f: f.event in ("awaiting_approval", "done"),
            timeout_s=60.0,
        )
        all_frames.extend(result.frames)
        since = result.last_id
        last = result.frames[-1]

        if last.event == "done":
            assert last.data["status"] == "ok", last.data
            break

        gate_kind, node_id = last.data["gate"], last.data["node_id"]
        gates_seen.append((node_id, gate_kind))

        r = http.get(f"/v1/runs/{run_id}", headers=user.auth_header())
        assert r.status_code == 200, _error_summary(r)
        detail = r.json()
        assert detail["gate"] == gate_kind
        assert detail["pending_node_id"] == node_id
        assert detail["pending_content_sha"] == last.data["pending_content_sha"]

        r = http.post(
            f"/v1/runs/{run_id}/gate",
            headers=user.auth_header(),
            json={"decision": "approve", "content_sha": detail["pending_content_sha"]},
        )
        assert r.status_code == 200, _error_summary(r)

    return run_id, all_frames, gates_seen


@pytest.mark.local_fake
def test_local_fake_prospect_outreach_run_holds_the_email_enroll_gate_and_completes(
    http, new_user, grant_entitlement, provision_local_profile, sse_frames
):
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=5.0)
    profile = provision_local_profile(user)

    r = http.get("/v1/packs", headers=user.auth_header(), params={"profile_name": profile})
    assert r.status_code == 200, _error_summary(r)
    packs = r.json()
    (descriptor,) = [p for p in packs if p["pack"] == PACK and p["variant"] == VARIANT]
    assert descriptor["available"] is True, descriptor

    _run_id, _frames, gates_seen = _drive_pack_run_to_completion(http, sse_frames, user, profile)

    assert ("sequence", "email_enroll") in gates_seen


def _full_history_with_durable_done(sse_frames, user, run_id: str, *, timeout_s: float = 30.0):
    """The run's complete, authoritative event history: reconnect with ``since=0`` (a full
    replay — no snapshot involved, per stream.py's `_read_opening`) until the trailing `done`
    frame itself carries a durable id.

    A plain single reconnect can land in the gap between `runs.status` flipping to a terminal
    value (written synchronously by complete_run/`_fail_run`) and the relay durably recording
    the matching `done` EVENT (published async) — `_read_opening` then falls through to
    `_terminal_frame`'s synthesised, id-less `done` even on a `since=` reconnect, because the
    replay it read simply doesn't contain "done" yet. Retrying until the observed `done` has a
    real id is what makes the returned list usable as ground truth for a reconnect-replay
    assertion (which needs every entry, including the last, to carry the same id both times).
    """
    import time

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


@pytest.mark.local_fake
def test_sse_reconnect_replays_frames_after_since_in_order(
    http, new_user, grant_entitlement, provision_local_profile, sse_frames
):
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=5.0)
    profile = provision_local_profile(user)

    run_id, _all_frames, _gates_seen = _drive_pack_run_to_completion(
        http, sse_frames, user, profile
    )

    full = _full_history_with_durable_done(sse_frames, user, run_id)
    mid = full[len(full) // 2].id
    assert mid is not None
    expected = [(f.id, f.event, f.data) for f in full if f.id is not None and f.id > mid]
    assert expected, "not enough durable history after the midpoint to prove a replay"

    replay = sse_frames(user, run_id, since=mid, until=lambda f: f.event == "done", timeout_s=30.0)
    replayed = [(f.id, f.event, f.data) for f in replay.frames]

    assert replayed == expected
