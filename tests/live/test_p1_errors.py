"""p1 acceptance suite for tests/live — the ERROR CONTRACT, proven over real HTTP.

Phase 1 replaced "a status code plus prose" with a typed envelope a client can branch on:
``{"error": {"code", "message", "details"}, "detail": ...}``. The mocked suites prove each
raise site in isolation; what this suite proves is that the codes are on the WIRE, reached
through the flows a client actually meets them in — a cap hit by starting real runs, a stale
gate decision produced by actually deciding one twice.

    GET /v1/runs/{bad}, /v1/runs/{bad}/stream, /v1/agents/{bad} -> 422 validation_error
    POST /v1/runs with a body RunRequest's validator rejects    -> 422 validation_error
    a request with no bearer token                              -> 401 token_missing
    a garbage bearer token                                      -> 401 token_invalid
    an already-expired access token                             -> 401 token_expired
    a 4th concurrent run in one workspace                       -> 429 too_many_concurrent_runs
    a 6th open SSE stream in one workspace                      -> 429 too_many_streams
    a wrong content_sha at an open gate                         -> 409 content_sha_mismatch
    a decision for a gate the run has already passed            -> 409 gate_not_open
    cancelling an already-terminal run                          -> 409 run_already_terminal
    an enroll-gate edit that is not an enroll draft             -> failed, error_code draft_invalid
    a node the stack was told to fail at                        -> failed, error_code node_failed

Two rules hold throughout. Every error response is checked as an ENVELOPE
(``error_envelope_problems`` — status, snake_case code, string message, well-shaped
details), never as a bare status code, because a right status with a drifted code is the
regression this phase exists to prevent. And no assertion message ever carries a response
BODY: a 422's details describe a rejected field, and a gate's pending_content carries
fictional-but-realistic prospect rows — status plus ``error.code`` (``_error_summary``) is
the whole vocabulary available for failure messages here.

See DEVELOPMENT.md's "Running the tests/live HTTP acceptance suite" section for the
invocations, including the two optional env vars the last two tests below are gated on.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
import uuid
import warnings

import pytest

from tests.live._support import (
    LIVE_FAIL_NODE_ENV,
    LIVE_JWT_SECRET_ENV,
    LiveUser,
    _error_summary,
    error_envelope_problems,
    expired_access_token,
    expired_token_skip_reason,
    fail_node_skip_reason,
    validation_input_echoes,
)

pytestmark = pytest.mark.p1

#: prospect-outreach holds exactly ONE gate — `sequence`, kind `email_enroll` (p0's harness
#: asserts that pairing). Its pending bytes are an enroll DRAFT, and that is what makes a
#: deterministic run failure reachable on an ordinary fake stack with no extra flags: see
#: test_an_enroll_gate_edit_that_is_not_an_enroll_draft_fails_the_run.
PACK, VARIANT = "prospecting", "prospect-outreach"

#: The variant the injected-failure test runs. marketing/linkedin-post deliberately, because
#: none of its node ids collides with prospect-outreach's (prospect/dossier/outreach/quality/
#: sequence): GTM_FAKE_RUN_FAIL_NODE is a STACK-WIDE flag, and a value naming no node of a
#: given run's script is ignored with a warning rather than failing that run
#: (backend/services/runs/fake_script.py:_fail_node). So one stack started with
#: GTM_FAKE_RUN_FAIL_NODE=radar serves the failure test AND leaves every gate test above it
#: untouched — which a shared node id would not.
FAIL_PACK, FAIL_VARIANT = "marketing", "linkedin-post"
#: Only linkedin-post's nodes BEFORE its first gate (`plan`) are accepted. A fake run fails at
#: its fail-node before it would gate there, but it still has to REACH it — and a node after
#: `plan` is only reached once that gate is approved, which this test deliberately never does.
#: Naming one would park the run until the 24h gate timeout instead of failing it.
FAIL_VARIANT_PREGATE_NODES = ("radar",)
#: packs/marketing/inputs.toml declares one required `ask` setting; a run without it is
#: refused at admission (422 missing_settings) long before it could reach any node. Fictional.
FAIL_VARIANT_INPUTS = {"brand_name": "Example Widgets Ltd"}

#: Local mirrors of the server-side caps (backend/services/runs/state.py's
#: _MAX_CONCURRENT_RUNS_PER_WORKSPACE / _MAX_STREAMS_PER_WORKSPACE). Deliberately a COPY and
#: not an import: this suite talks HTTP to a stack that may be a different build from this
#: checkout, so the numbers it asserts must be the published contract, not whatever the local
#: source happens to say — an import would make the test agree with a drifted server forever.
_MAX_CONCURRENT_RUNS = 3
_MAX_STREAMS = 5

#: Terminal run statuses (schemas/content-item.schema.json; the server's own
#: _TERMINAL_STATUSES). Same copy-not-import reasoning as the caps above.
_TERMINAL_STATUSES = frozenset({"ok", "failed", "rejected", "canceled"})

_POLL_INTERVAL_S = 0.25

#: A syntactically valid sha256 that is not the hash of anything any gate of these runs holds.
_WRONG_CONTENT_SHA = hashlib.sha256(
    b"tests/live p1: bytes no gate of this run ever held"
).hexdigest()

#: Bytes that are not an enroll draft in any reading — agent.gate_actions.parse_enroll_draft
#: refuses them at the very first step (not valid JSON), which is what fails the run.
_NOT_AN_ENROLL_DRAFT = "This is prose, not an enrollment draft. The parser must refuse it."


# ── assertions and helpers ──────────────────────────────────────────────────────────────


def _assert_error(r, expected_status: int, expected_code: str | None = None) -> dict:
    """Assert ``r`` is this backend's error envelope with that status and code; return the
    ``error`` object so a caller can go on to check ``details``."""
    problems = error_envelope_problems(
        r, expected_status=expected_status, expected_code=expected_code
    )
    assert not problems, f"{_error_summary(r)}: " + "; ".join(problems)
    return r.json()["error"]


def _details(error: dict) -> dict:
    """``error.details`` as the object a coded refusal is contracted to carry."""
    details = error.get("details")
    assert isinstance(details, dict), (
        f"error.code={error.get('code')!r} carries details of type "
        f"{type(details).__name__}, expected an object"
    )
    return details


def _assert_no_input_echo(error: dict) -> None:
    """A ``validation_error`` must not hand the rejected value back (ER-09)."""
    echoes = validation_input_echoes(error.get("details"))
    assert not echoes, "; ".join(echoes)


def _assert_stage_error_code_agrees(detail: dict) -> None:
    """``stages[0]`` mirrors the run row's output/error (backend/services/runs/queries.py's
    fetch_run_detail), so WHEN it carries ``error_code`` at all it must be the same code the
    top level reports — one fact with two spellings is how a client ends up branching on the
    stale one. Conditional because ``stages`` is the legacy shape clients are told to prefer
    the top-level fields over, so its absence is not itself a failure."""
    stages = detail.get("stages") or []
    if stages and isinstance(stages[0], dict) and "error_code" in stages[0]:
        assert stages[0]["error_code"] == detail.get("error_code"), (
            f"stages[0].error_code {stages[0]['error_code']!r} disagrees with the run's "
            f"top-level {detail.get('error_code')!r}"
        )


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
    """Poll ``GET /v1/runs/{run_id}`` until ``ready(detail)``; return that detail.

    Polling, not streaming, on purpose: what these tests need is the run ROW's state at the
    moment they act on it (the gate route reads exactly that), and a poll cannot be ahead of
    or behind the row the way a live frame briefly can. The timeout message names the status
    and nothing else — a gated run's detail carries its pending_content.
    """
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
        http,
        user,
        run_id,
        lambda d: d.get("status") in _TERMINAL_STATUSES,
        what="a terminal state",
    )


def _durable_done_frame(sse_frames, user: LiveUser, run_id: str, *, timeout_s: float = 30.0):
    """The run's ``done`` frame AS PUBLISHED — retried until the one observed carries a
    durable id.

    A ``done`` read right after the run row went terminal can instead be the id-less frame
    ``backend/services/runs/stream.py:_terminal_frame`` synthesises from the row when the
    relay has not durably recorded the real event yet. Only the id-bearing one is the event a
    mid-run streaming client actually receives, so that is the one worth asserting a new
    field on. (tests/live/test_p0_harness.py has a same-shaped helper for the same reason;
    kept separate so the p0 sanity suite and this one never constrain each other's edits.)
    """
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
        done = result.frames[-1]
        if done.id is not None:
            return done


# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_workspace(new_user, grant_entitlement, provision_local_profile):
    """A throwaway workspace with a pro entitlement and the dev profile provisioned.

    A FRESH one per test, not the session-scoped `live_user`: the concurrency and open-stream
    caps these tests hit are both per-WORKSPACE, so sharing one would make each test's
    arithmetic depend on what the previous test left in flight.
    """
    user = new_user()
    grant_entitlement(user, entitlement="pro", cap_usd=5.0)
    return user, provision_local_profile(user)


@pytest.fixture
def track_run(http):
    """``track_run(user, run_id) -> run_id``. Every tracked run is cancelled on teardown.

    A run parked at a gate holds one of its workspace's three concurrency slots for the full
    24h gate timeout, so a test that leaves one behind would poison the next — and against a
    stack shared with a developer, would sit there all day. A cancel that 409s is the run
    already being terminal, which is the expected case for a test that drove one to the end;
    teardown never raises past the test's own result either way.
    """
    started: list[tuple[LiveUser, str]] = []

    def _track(user: LiveUser, run_id: str) -> str:
        started.append((user, run_id))
        return run_id

    yield _track

    for user, run_id in started:
        try:
            http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
        except Exception as exc:  # noqa: BLE001 — teardown must not mask the test result
            warnings.warn(
                f"tests/live teardown: cancel of run {run_id} raised {exc!r}", stacklevel=2
            )


# ── 422: a malformed id, and a body the model validator rejects ─────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/v1/runs/not-a-uuid", "/v1/runs/not-a-uuid/stream", "/v1/agents/not-a-uuid"],
)
def test_a_malformed_uuid_in_a_path_is_a_validation_error(http, live_user, path):
    """A path id that is not a UUID is a REQUEST-shaped fault, and must read as one.

    These three routes declare `uuid.UUID` path params, so FastAPI rejects the id at the
    boundary and the run/agent is never looked up — no existence oracle, and no 500 from a
    cast failing deeper in. The stream route is included because it is the one a client is
    most likely to hit with a stale or hand-built id, and a `text/event-stream` route
    returning a JSON envelope on refusal is the property worth pinning.
    """
    r = http.get(path, headers=live_user.auth_header())
    error = _assert_error(r, 422, "validation_error")
    _assert_no_input_echo(error)


def test_a_run_request_body_the_model_validator_rejects_is_a_validation_error(http, live_user):
    """Pack mode with no `variant` fails RunRequest's `_exactly_one_mode` model validator.

    A MODEL-level rejection, not a field-level one: a different pydantic path from the
    malformed-id 422s above, reached by the ordinary client mistake of POSTing a half-filled
    run request. It must arrive as the same typed envelope — and must still not echo the
    submitted body back, which for this route would be the whole request object.
    """
    r = http.post(
        "/v1/runs",
        headers=live_user.auth_header(),
        json={"profile_name": "example-widgets", "pack": PACK},
    )
    error = _assert_error(r, 422, "validation_error")
    _assert_no_input_echo(error)


# ── 401: the three token faults a client must tell apart ────────────────────────────────


def test_a_request_with_no_bearer_token_is_401_token_missing(http):
    """No credentials at all. Distinct from a bad one because the client's reaction differs:
    sign in, rather than refresh or re-authenticate. The `WWW-Authenticate: Bearer`
    challenge is what makes the 401 a standards-shaped one rather than a bare status (M-01).
    """
    r = http.get("/v1/workspace")
    _assert_error(r, 401, "token_missing")
    challenge = r.headers.get("WWW-Authenticate", "")
    assert challenge.lower().startswith("bearer"), (
        f"expected a Bearer challenge on the 401, got {challenge!r}"
    )


def test_a_garbage_bearer_token_is_401_token_invalid(http):
    """A token that is not a token. `error="invalid_token"` in the challenge is the RFC 6750
    parameter a client keys off, and the code separates "this credential is broken, sign in
    again" from the merely-expired case below, which a silent refresh fixes."""
    r = http.get("/v1/workspace", headers={"Authorization": "Bearer not.a.valid.jwt"})
    _assert_error(r, 401, "token_invalid")
    challenge = r.headers.get("WWW-Authenticate", "")
    assert 'error="invalid_token"' in challenge, (
        f"expected an invalid_token challenge on the 401, got {challenge!r}"
    )


def test_an_expired_access_token_is_401_token_expired(http, base_url):
    """The 401 a mobile client sees most often, and the one it must NOT treat as a sign-out.

    This is the only assertion in the suite that cannot be made with a token the API itself
    issued: an access token's TTL is 60 minutes by default, so the token has to be minted
    already expired, which needs the stack's own HS256 signing secret. Gated on
    GTM_LIVE_JWT_SECRET and refused outright for anything but a loopback base URL — a token
    is never minted against staging (`expired_token_skip_reason`). The secret is read once
    and never reaches an assertion message, a log, or the request beyond the signature.
    """
    secret = os.environ.get(LIVE_JWT_SECRET_ENV)
    reason = expired_token_skip_reason(base_url, secret)
    if reason is not None:
        pytest.skip(reason)

    # Ids of a workspace that does not exist: decoding fails on `exp` before require_auth
    # ever reaches the database, so this test creates and touches no state at all.
    token = expired_access_token(secret, user_id=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()))
    r = http.get("/v1/workspace", headers={"Authorization": f"Bearer {token}"})

    _assert_error(r, 401, "token_expired")
    challenge = r.headers.get("WWW-Authenticate", "")
    assert 'error="invalid_token"' in challenge, (
        f"expected an invalid_token challenge on the 401, got {challenge!r}"
    )
    assert 'error_description="token expired"' in challenge, (
        f"expected the expiry to be named in the challenge, got {challenge!r}"
    )


# ── 429: two caps that are not the per-IP rate limit ────────────────────────────────────


@pytest.mark.local_fake
def test_a_fourth_concurrent_run_in_one_workspace_is_429_too_many_concurrent_runs(
    http, fake_workspace, track_run
):
    """The per-workspace cap is three IN-FLIGHT runs, and a run parked at a gate counts.

    That last part is the trap: `_reserve_cap` counts queued|running|awaiting_approval
    (backend/services/runs/admission.py), so three runs a human has not decided yet are
    indistinguishable from three busy ones, and the right client reaction is to decide or
    cancel one — never a retry timer, which is what a bare 429 invites (M-02, RT-04).
    Parking all three at their gate BEFORE the fourth POST is what makes `in_flight`
    deterministically 3 rather than whatever the executor happened to have finished.
    """
    user, profile = fake_workspace
    for _ in range(_MAX_CONCURRENT_RUNS):
        _await_gate(http, user, track_run(user, _start_run(http, user, profile)))

    r = http.post(
        "/v1/runs",
        headers=user.auth_header(),
        json={"profile_name": profile, "pack": PACK, "variant": VARIANT, "inputs": {}},
    )

    error = _assert_error(r, 429, "too_many_concurrent_runs")
    details = _details(error)
    assert details.get("max") == _MAX_CONCURRENT_RUNS, (
        f"expected the published cap {_MAX_CONCURRENT_RUNS}, got {details.get('max')!r}"
    )
    assert details.get("in_flight") == _MAX_CONCURRENT_RUNS, (
        f"three runs are parked at a gate, so in_flight must be {_MAX_CONCURRENT_RUNS}, "
        f"got {details.get('in_flight')!r}"
    )


@pytest.mark.local_fake
def test_a_sixth_open_stream_in_one_workspace_is_429_too_many_streams(
    http, fake_workspace, track_run
):
    """The per-workspace open-SSE-stream cap, which shares a status code with the
    concurrent-run cap and the per-IP window and means something else again: close a stream,
    do not cancel a run and do not back off (M-02).

    All five held streams are on ONE run, parked at its gate — a terminal run's stream
    returns immediately (snapshot, done, close) and would free its slot before the next
    connect, so the cap could never be reached that way.
    """
    import httpx

    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    _await_gate(http, user, run_id)
    timeout = httpx.Timeout(20.0, connect=10.0)

    with contextlib.ExitStack() as held:
        held_line_iters = []  # kept alive: an abandoned iter_lines() generator is closed by
        # CPython's refcounting GC as soon as this loop's local goes out of scope, which
        # tears down httpx's read side of the connection — the server sees that as a
        # disconnect and cancels the stream, undoing the very slot this loop just claimed.
        for _ in range(_MAX_STREAMS):
            resp = held.enter_context(
                http.stream(
                    "GET",
                    f"/v1/runs/{run_id}/stream",
                    headers=user.auth_header(),
                    timeout=timeout,
                )
            )
            assert resp.status_code == 200, f"holding a stream open -> {resp.status_code}"
            # The per-workspace count is incremented INSIDE the generator
            # (backend/services/runs/stream.py), which only starts running when the response
            # BODY does — a connection whose headers have arrived is not yet holding a slot.
            # Reading the opening `retry:` line is what makes this loop's arithmetic true.
            line_iter = resp.iter_lines()
            held_line_iters.append(line_iter)
            assert next(line_iter, None) is not None, (
                "a held stream produced no opening frame, so it may hold no slot yet"
            )

        with http.stream(
            "GET", f"/v1/runs/{run_id}/stream", headers=user.auth_header(), timeout=timeout
        ) as over:
            over.read()
            error = _assert_error(over, 429, "too_many_streams")
            details = _details(error)
            assert details.get("max") == _MAX_STREAMS, (
                f"expected the published cap {_MAX_STREAMS}, got {details.get('max')!r}"
            )


# ── 409: three refusals at the gate that used to be one code ────────────────────────────


@pytest.mark.local_fake
def test_a_wrong_content_sha_at_an_open_gate_is_409_content_sha_mismatch(
    http, fake_workspace, track_run
):
    """H9 binds a decision to the EXACT bytes the operator saw.

    `decide_gate` compares `content_sha` against the hash of the run's current
    `pending_content` (backend/services/runs/decisions.py:content_matches) and refuses
    anything else, which is what stops a decision prepared for one gate from resolving a
    later gate of the same run. The right client reaction — refetch and re-render the gate —
    differs from every other 409 on this route, and all of them used to normalise to
    `conflict` (RL-06).
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    detail = _await_gate(http, user, run_id)
    # Negative control: the sha below must actually be the wrong one for this gate.
    assert detail["pending_content_sha"] != _WRONG_CONTENT_SHA

    r = http.post(
        f"/v1/runs/{run_id}/gate",
        headers=user.auth_header(),
        json={"decision": "approve", "content_sha": _WRONG_CONTENT_SHA},
    )
    _assert_error(r, 409, "content_sha_mismatch")

    # A refused decision consumes nothing: the gate is still there to be decided properly.
    still = http.get(f"/v1/runs/{run_id}", headers=user.auth_header())
    assert still.status_code == 200, _error_summary(still)
    assert still.json()["status"] == "awaiting_approval"


@pytest.mark.local_fake
def test_a_decision_for_a_gate_the_run_has_already_passed_is_409_gate_not_open(
    http, fake_workspace, track_run
):
    """Re-sending an approval the run has already consumed — a retry after a lost response.

    `decide_gate` checks in a fixed order (backend/routers/runs.py): the run's STATUS first,
    the content_sha second, and whether a decision is already recorded LAST. So which 409 a
    duplicate approval gets depends on whether the executor has moved the run off
    `awaiting_approval` yet — `gate_already_decided` in the instant before it does,
    `gate_not_open` afterwards. That instant is a race no client can observe or rely on, so
    this waits for the run to LEAVE awaiting_approval and asserts the outcome that is then
    deterministic; `gate_already_decided` is reachable only inside that window and is
    deliberately left unasserted rather than asserted loosely.
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    content_sha = _await_gate(http, user, run_id)["pending_content_sha"]
    decision = {"decision": "approve", "content_sha": content_sha}

    r = http.post(f"/v1/runs/{run_id}/gate", headers=user.auth_header(), json=decision)
    assert r.status_code == 200, _error_summary(r)
    _poll_run(
        http,
        user,
        run_id,
        lambda d: d.get("status") != "awaiting_approval",
        what="a post-gate state",
    )

    r = http.post(f"/v1/runs/{run_id}/gate", headers=user.auth_header(), json=decision)
    _assert_error(r, 409, "gate_not_open")


@pytest.mark.local_fake
def test_cancelling_an_already_terminal_run_is_409_run_already_terminal(
    http, fake_workspace, track_run
):
    """Cancel looks idempotent and is not: the second one must say so, and say which terminal
    state it found, so a client can show "already finished" instead of "cancel failed".

    Cancelling TWICE — rather than driving a run to `ok` first — is what makes the reported
    status deterministic: the first cancel writes `canceled` synchronously before it returns
    (backend/services/runs/decisions.py:cancel), so no executor timing is involved.
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    _await_gate(http, user, run_id)

    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    assert r.status_code == 200, _error_summary(r)
    assert _await_terminal(http, user, run_id)["status"] == "canceled"

    r = http.post(f"/v1/runs/{run_id}/cancel", headers=user.auth_header())
    error = _assert_error(r, 409, "run_already_terminal")
    assert _details(error).get("status") == "canceled", (
        f"the refusal must name the terminal status it found, got {_details(error).get('status')!r}"
    )


# ── a failed run carries a machine-readable error_code (M-07) ───────────────────────────


@pytest.mark.local_fake
def test_an_enroll_gate_edit_that_is_not_an_enroll_draft_fails_the_run_with_draft_invalid(
    http, fake_workspace, track_run, sse_frames
):
    """A failed run must say WHY in a code, not only in prose — and on both surfaces.

    This failure is deterministic on an ORDINARY fake stack, with no extra flag. The one
    gate prospect-outreach holds is `sequence`, kind `email_enroll`, whose pending bytes are
    an enroll draft. `decisions.refuses_edit` refuses an edit only at a `review` gate, so the
    edit here is ACCEPTED synchronously (200) — and the fake run then validates the
    substituted bytes with the same parser a real run promotes a draft with
    (backend/services/runs/fake.py:_enroll_edit_error -> agent.gate_actions
    .parse_enroll_draft), which refuses anything that is not JSON, and fails the run.

    So the wire shows the split a client has to handle: the DECISION succeeded, and the RUN
    failed afterwards, asynchronously. Both the poll surface and the `done` frame have to
    carry the code, or a streaming client and a polling client disagree about the same run.
    """
    user, profile = fake_workspace
    run_id = track_run(user, _start_run(http, user, profile))
    detail = _await_gate(http, user, run_id)
    assert detail.get("gate") == "email_enroll", (
        f"expected prospect-outreach's enroll gate, got gate={detail.get('gate')!r}"
    )

    r = http.post(
        f"/v1/runs/{run_id}/gate",
        headers=user.auth_header(),
        json={
            "decision": "edit",
            "content_sha": detail["pending_content_sha"],
            "edited_content": _NOT_AN_ENROLL_DRAFT,
        },
    )
    assert r.status_code == 200, _error_summary(r)

    failed = _await_terminal(http, user, run_id)
    assert failed["status"] == "failed", f"expected a failed run, got {failed['status']!r}"
    assert failed.get("error_code") == "draft_invalid", (
        f"expected error_code 'draft_invalid', got {failed.get('error_code')!r}"
    )
    _assert_stage_error_code_agrees(failed)

    done = _durable_done_frame(sse_frames, user, run_id)
    assert done.data.get("status") == "failed"
    assert done.data.get("error_code") == "draft_invalid", (
        f"the done frame must carry the same code as the poll surface, got "
        f"{done.data.get('error_code')!r}"
    )


@pytest.mark.local_fake
def test_an_injected_node_failure_carries_node_failed(http, fake_workspace, track_run, sse_frames):
    """The other half of M-07: a node that fails mid-run, rather than a refused gate edit.

    GTM_FAKE_RUN_FAIL_NODE is a STACK-WIDE server flag, so this test cannot set it — it is
    TOLD which node the stack was started with (GTM_LIVE_FAKE_RUN_FAIL_NODE) and skips
    otherwise, and it refuses a node its own variant would not fail at before the gate it
    never approves (`fail_node_skip_reason`). It runs marketing/linkedin-post rather than
    prospect-outreach precisely so that the stack carrying this flag still runs every gate
    test above unaffected — see FAIL_PACK's note.
    """
    reason = fail_node_skip_reason(os.environ.get(LIVE_FAIL_NODE_ENV), FAIL_VARIANT_PREGATE_NODES)
    if reason is not None:
        pytest.skip(reason)

    user, profile = fake_workspace
    run_id = track_run(
        user,
        _start_run(
            http,
            user,
            profile,
            pack=FAIL_PACK,
            variant=FAIL_VARIANT,
            inputs=FAIL_VARIANT_INPUTS,
        ),
    )

    failed = _await_terminal(http, user, run_id)
    assert failed["status"] == "failed", f"expected a failed run, got {failed['status']!r}"
    assert failed.get("error_code") == "node_failed", (
        f"expected error_code 'node_failed', got {failed.get('error_code')!r}"
    )
    _assert_stage_error_code_agrees(failed)

    done = _durable_done_frame(sse_frames, user, run_id)
    assert done.data.get("status") == "failed"
    assert done.data.get("error_code") == "node_failed", (
        f"the done frame must carry the same code as the poll surface, got "
        f"{done.data.get('error_code')!r}"
    )
