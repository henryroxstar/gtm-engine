"""Fast, offline unit tests for the pure pieces of tests/live/_support.py.

Deliberately named with a trailing ``_unit`` module stem so tests/live/conftest.py's
collection-time gate leaves it alone (see its ``_UNGATED_MODULE_SUFFIX``) — these run in the
normal suite with no ``GTM_LIVE_BASE_URL`` and no network access, same as every other test
under tests/. They are what proves the scaffold's parsing/decoding/gating LOGIC is right,
independent of any live stack. The one exception is
``test_read_sse_stream_raises_timeouterror_promptly_on_a_stalled_stream``, which starts a
throwaway local HTTP server on an OS-assigned loopback port for the duration of one test —
never a network call to anything this process didn't itself start.
"""

from __future__ import annotations

import base64
import http.server
import json
import threading
import time

import pytest

from tests.live._support import (
    BILLING_SYNC_SECRET_ENV,
    LiveUser,
    billing_sync_secret,
    decode_jwt_payload,
    error_envelope_problems,
    expired_access_token,
    expired_token_skip_reason,
    fail_node_skip_reason,
    is_loopback,
    live_stack_skip_reason,
    local_fake_skip_reason,
    multi_worker_skip_reason,
    parse_sse_lines,
    read_sse_stream,
    real_executor_skip_reason,
    recreate_cmd_skip_reason,
    truthy,
    validate_run_event,
    validation_input_echoes,
)

# ── billing_sync_secret: never leaks over cleartext http to a non-loopback host ───────────
#
# The service secret can grant a paid entitlement to any workspace (backend/deps.py's
# require_service_auth just compares the raw header value) — see conftest.py's
# grant_entitlement, the only caller. It must never go out as a plain Authorization header
# over http:// to a real network host, whether that's a typo'd GTM_LIVE_BASE_URL or a
# deliberate non-loopback target.


def test_billing_sync_secret_refuses_http_to_a_non_loopback_host(monkeypatch):
    monkeypatch.setenv(BILLING_SYNC_SECRET_ENV, "topsecret")
    assert billing_sync_secret("http://api.example.com") is None


def test_billing_sync_secret_allows_https_to_a_non_loopback_host(monkeypatch):
    monkeypatch.setenv(BILLING_SYNC_SECRET_ENV, "topsecret")
    assert billing_sync_secret("https://api.example.com") == "topsecret"


def test_billing_sync_secret_allows_http_to_loopback(monkeypatch):
    monkeypatch.setenv(BILLING_SYNC_SECRET_ENV, "topsecret")
    assert billing_sync_secret("http://127.0.0.1:8000") == "topsecret"


def test_billing_sync_secret_reads_the_env_file_fallback_only_for_loopback(monkeypatch, tmp_path):
    # scripts/dev_seed.py (the deploy/.env.dev reader) is a dev-only convenience: it never
    # ships in the OSS carve (scripts/ is off the export allowlist), same as deploy/ itself.
    # billing_sync_secret() already degrades to None without it (see its ModuleNotFoundError
    # guard) — this test exercises the private-repo convenience path, so it skips where that
    # module is absent rather than asserting behavior the carve was never meant to have.
    pytest.importorskip(
        "scripts.dev_seed",
        reason="deploy/.env.dev fallback is a private dev convenience, not shipped in the OSS carve",
    )
    # env var absent + loopback -> falls back to deploy/.env.dev under REPO.
    monkeypatch.delenv(BILLING_SYNC_SECRET_ENV, raising=False)
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    (deploy_dir / ".env.dev").write_text(
        f"{BILLING_SYNC_SECRET_ENV}=file-secret\n", encoding="utf-8"
    )
    monkeypatch.setattr("tests.live._support.REPO", tmp_path)
    assert billing_sync_secret("http://127.0.0.1:8000") == "file-secret"


def test_billing_sync_secret_never_reads_the_env_file_fallback_for_a_non_loopback_host(
    monkeypatch,
):
    # See the sibling loopback test above: scripts/dev_seed.py is private-only and absent
    # from the OSS carve, so this test (which patches it directly) skips there too.
    pytest.importorskip(
        "scripts.dev_seed",
        reason="deploy/.env.dev fallback is a private dev convenience, not shipped in the OSS carve",
    )
    # env var absent + non-loopback -> must return None WITHOUT ever opening deploy/.env.dev.
    monkeypatch.delenv(BILLING_SYNC_SECRET_ENV, raising=False)

    def _must_not_be_called(path):
        raise AssertionError("billing_sync_secret read deploy/.env.dev for a non-loopback URL")

    monkeypatch.setattr("scripts.dev_seed._env_file_values", _must_not_be_called)
    assert billing_sync_secret("https://api.example.com") is None


# ── SSE line parser ─────────────────────────────────────────────────────────────────────


def test_parses_a_single_frame_with_id_event_and_data():
    lines = ["id: 3", "event: node", 'data: {"node_id":"draft","state":"running"}', ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.id == 3
    assert frame.event == "node"
    assert frame.data == {"node_id": "draft", "state": "running"}


def test_joins_multi_line_data_with_newlines_before_decoding_json():
    # The SSE spec joins multiple `data:` lines with "\n" before the payload is parsed —
    # exercised here with a JSON string value that itself contains a literal newline, split
    # across two `data:` lines the way a server-side line-wrap would produce it.
    lines = ["event: content", 'data: {"text":', 'data: "line one\\nline two"}', ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.data == {"text": "line one\nline two"}


def test_comment_lines_are_skipped_and_carry_no_field():
    lines = [": keep-alive", "event: ping", 'data: {"ts":"2026-01-01T00:00:00Z"}', ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.event == "ping"
    assert frame.id is None  # a ping never carries an id: line


def test_a_ping_frame_has_no_id_and_does_not_disturb_a_later_id():
    lines = [
        "id: 5",
        "event: node",
        'data: {"a":1}',
        "",
        "event: ping",  # no id: line at all
        'data: {"ts":"x"}',
        "",
        "id: 6",
        "event: node",
        'data: {"a":2}',
        "",
    ]
    frames = list(parse_sse_lines(lines))
    assert [f.id for f in frames] == [5, None, 6]
    assert [f.event for f in frames] == ["node", "ping", "node"]


def test_a_bare_retry_block_with_no_event_or_data_dispatches_nothing():
    # This server's very first written line for every stream: `yield "retry: 3000\n\n"`.
    lines = ["retry: 3000", "", "event: status", 'data: {"status":"running"}', ""]
    frames = list(parse_sse_lines(lines))
    assert len(frames) == 1
    assert frames[0].event == "status"


def test_a_field_line_with_no_colon_names_an_empty_value():
    lines = ["event", 'data: {"a":1}', ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.event == "message"  # empty `event` value -> the SSE default


def test_an_unterminated_trailing_block_is_not_dispatched():
    lines = ["event: node", 'data: {"a":1}']  # no closing blank line
    assert list(parse_sse_lines(lines)) == []


def test_empty_data_decodes_to_an_empty_dict():
    lines = ["event: ping", ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.data == {}


def test_event_without_data_still_dispatches_unlike_strict_whatwg():
    # The documented deviation from the spec's dispatch algorithm: real WHATWG SSE would
    # dispatch NOTHING here (empty data buffer), but this parser dispatches on `event:` alone.
    lines = ["event: ping", ""]
    (frame,) = list(parse_sse_lines(lines))
    assert frame.event == "ping"
    assert frame.data == {}


# ── JWT payload decoding ────────────────────────────────────────────────────────────────


def _fake_jwt(payload: dict) -> str:
    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps(payload).encode())
    signature = _b64(b"not-a-real-signature")
    return f"{header}.{body}.{signature}"


def test_decodes_the_payload_segment_without_verifying_the_signature():
    token = _fake_jwt({"sub": "user-1", "workspace_id": "ws-1", "type": "access"})
    assert decode_jwt_payload(token) == {"sub": "user-1", "workspace_id": "ws-1", "type": "access"}


def test_decodes_a_payload_whose_base64_needs_padding():
    # Deliberately pick a payload whose unpadded base64url length isn't a multiple of 4.
    token = _fake_jwt({"sub": "x"})
    assert decode_jwt_payload(token)["sub"] == "x"


@pytest.mark.parametrize("token", ["not-a-jwt", "only.two", "a.b.c.d"])
def test_rejects_a_token_that_is_not_three_dot_separated_segments(token):
    with pytest.raises(ValueError, match="not a JWT"):
        decode_jwt_payload(token)


# ── truthy / is_loopback ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " true ", "yes", "on"])
def test_truthy_accepts_common_truthy_spellings(value):
    assert truthy(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "  "])
def test_truthy_rejects_everything_else(value):
    assert truthy(value) is False


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000", "https://localhost"],
)
def test_is_loopback_accepts_loopback_hosts(url):
    assert is_loopback(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://192.0.2.10:8000",  # RFC 5737 TEST-NET-1: routable-shaped, still not loopback
        "not-a-url",
        "",
        "ftp://127.0.0.1",  # unsupported scheme
    ],
)
def test_is_loopback_rejects_non_loopback_or_malformed_urls(url):
    assert is_loopback(url) is False


# ── collection-time skip predicates (real_executor / local_fake / no base url) ───────────


def test_live_stack_skip_reason_is_set_when_the_base_url_is_unset_or_blank():
    assert live_stack_skip_reason("") is not None
    assert live_stack_skip_reason("   ") is not None


def test_live_stack_skip_reason_is_none_once_a_base_url_is_set():
    assert live_stack_skip_reason("http://127.0.0.1:8000") is None


def test_real_executor_is_skipped_by_default():
    assert real_executor_skip_reason(None) is not None
    assert real_executor_skip_reason("") is not None
    assert real_executor_skip_reason("0") is not None


def test_real_executor_runs_only_on_the_explicit_env_opt_in():
    assert real_executor_skip_reason("1") is None
    assert real_executor_skip_reason("true") is None


def test_local_fake_needs_both_a_loopback_url_and_the_fake_runs_flag():
    assert local_fake_skip_reason("http://127.0.0.1:8000", "1") is None
    assert local_fake_skip_reason("http://127.0.0.1:8000", "0") is not None  # flag off
    assert local_fake_skip_reason("http://127.0.0.1:8000", None) is not None  # flag unset
    assert local_fake_skip_reason("https://api.example.com", "1") is not None  # not loopback


def test_recreate_cmd_skip_reason_needs_a_declared_command():
    assert recreate_cmd_skip_reason(None) is not None
    assert recreate_cmd_skip_reason("") is not None
    assert recreate_cmd_skip_reason("   ") is not None


def test_recreate_cmd_skip_reason_is_none_once_a_command_is_declared():
    assert recreate_cmd_skip_reason("docker compose up -d --force-recreate api") is None


def test_multi_worker_skip_reason_needs_the_env_var():
    assert multi_worker_skip_reason(None) is not None
    assert multi_worker_skip_reason("") is not None


def test_multi_worker_skip_reason_rejects_a_non_integer():
    reason = multi_worker_skip_reason("two")
    assert reason is not None and "not an integer" in reason


def test_multi_worker_skip_reason_rejects_fewer_than_two_workers():
    assert multi_worker_skip_reason("0") is not None
    assert multi_worker_skip_reason("1") is not None


def test_multi_worker_skip_reason_allows_two_or_more_workers():
    assert multi_worker_skip_reason("2") is None
    assert multi_worker_skip_reason(" 5 ") is None


# ── LiveUser: secrets never leak into a repr (and so never into pytest's assertion output) ──


def test_live_user_repr_never_contains_password_or_tokens():
    user = LiveUser(
        email="live-test@example.com",
        password="super-secret-password",  # noqa: S105 — a fixture value, not a real credential
        user_id="u1",
        workspace_id="w1",
        access_token="the-access-token",
        refresh_token="the-refresh-token",
    )
    r = repr(user)
    assert "super-secret-password" not in r
    assert "the-access-token" not in r
    assert "the-refresh-token" not in r
    assert "live-test@example.com" in r  # non-secret fields stay visible for debugging


# ── run-event schema validation ──────────────────────────────────────────────────────────


def test_validate_run_event_accepts_a_well_formed_frame():
    assert validate_run_event("status", {"run_id": "r1", "status": "running"}) == []


def test_validate_run_event_flags_an_unknown_event_type():
    errors = validate_run_event("not-a-real-event", {})
    assert errors and "unknown event type" in errors[0]


def test_validate_run_event_flags_a_type_error():
    # `status` must be a string per the schema; an int should not validate.
    errors = validate_run_event("status", {"run_id": "r1", "status": 123})
    assert errors


@pytest.mark.parametrize(
    "event",
    [
        "snapshot",
        "status",
        "awaiting_approval",
        "chunk",
        "done",
        "ping",
        "node",
        "content",
        "gate_resolved",
    ],
)
def test_validate_run_event_loads_a_branch_for_every_protocol_event_name(event):
    # Any dict is fine here — the point is that a branch exists for every event name the
    # schema declares (an empty dict would still fail its own required-field checks, but
    # never with "unknown event type").
    errors = validate_run_event(event, {})
    assert not any("unknown event type" in e for e in errors)


# ── read_sse_stream: bounded by timeout_s even when the connection just hangs ─────────────


class _StallingSSEHandler(http.server.BaseHTTPRequestHandler):
    """Sends one legal SSE preamble line, then holds the connection open and NEVER sends
    another byte — reproducing a genuinely stalled stream (the failure mode a hung worker or
    a dropped connection looks like from the client's side)."""

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's naming convention
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"retry: 3000\n\n")
        self.wfile.flush()
        time.sleep(5)  # far longer than any timeout_s this test uses

    def log_message(self, *args):  # silence the default stderr access log during the test
        pass


@pytest.fixture
def stalling_sse_server():
    import socketserver

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _StallingSSEHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_read_sse_stream_raises_timeouterror_promptly_on_a_stalled_stream(stalling_sse_server):
    httpx = pytest.importorskip("httpx")

    user = LiveUser(
        email="live-test@example.com",
        password="x",  # noqa: S105
        user_id="u1",
        workspace_id="w1",
        access_token="a",
        refresh_token="r",
    )
    with httpx.Client(
        base_url=stalling_sse_server, timeout=httpx.Timeout(30.0, connect=5.0)
    ) as http:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            read_sse_stream(http, user, "fake-run", timeout_s=0.5, validate=False)
        elapsed = time.monotonic() - started

    # The bug this regression-tests: timeout_s=0.5 previously took ~15.5s (httpx's own
    # ReadTimeout, set to timeout_s + 15.0, firing instead of this function's own deadline).
    # A generous margin over 0.5s keeps this from flaking on a loaded CI runner while still
    # failing hard if the old behavior ever comes back.
    assert elapsed < 5.0


# ── p1 error-contract helpers ───────────────────────────────────────────────────────────
#
# The pure pieces tests/live/test_p1_errors.py asserts THROUGH, exercised here directly so a
# bug in the checker itself cannot masquerade as a passing acceptance test (§R18: a check
# that cannot discriminate is not a check).


class _FakeResponse:
    """The two attributes error_envelope_problems reads off an httpx.Response."""

    def __init__(self, status_code: int, body, *, json_decodable: bool = True):
        self.status_code = status_code
        self._body = body
        self._decodable = json_decodable

    def json(self):
        if not self._decodable:
            raise ValueError("not JSON")
        return self._body


def _envelope(code: str = "gate_not_open", message: str = "Gate is not open", details=None):
    return {"error": {"code": code, "message": message, "details": details}, "detail": message}


def test_error_envelope_problems_accepts_a_well_formed_envelope():
    r = _FakeResponse(409, _envelope())
    assert error_envelope_problems(r, expected_status=409, expected_code="gate_not_open") == []


def test_error_envelope_problems_accepts_object_and_array_details():
    for details in ({"max": 3, "in_flight": 3}, [{"loc": ["body"], "msg": "nope"}]):
        r = _FakeResponse(429, _envelope(code="too_many_concurrent_runs", details=details))
        assert error_envelope_problems(r, expected_status=429) == []


def test_error_envelope_problems_reports_a_wrong_status():
    r = _FakeResponse(409, _envelope())
    problems = error_envelope_problems(r, expected_status=422, expected_code="gate_not_open")
    assert any("expected HTTP 422" in p for p in problems)


def test_error_envelope_problems_reports_a_wrong_code():
    r = _FakeResponse(409, _envelope(code="conflict"))
    problems = error_envelope_problems(r, expected_status=409, expected_code="gate_not_open")
    assert any("gate_not_open" in p for p in problems)


def test_error_envelope_problems_rejects_a_code_that_is_not_snake_case():
    # The regression this guards: a status-derived code drifting back into prose or CamelCase.
    for code in ("Run not found", "GateNotOpen", "gate-not-open", "_leading", "trailing_"):
        r = _FakeResponse(409, _envelope(code=code))
        problems = error_envelope_problems(r, expected_status=409)
        assert any("snake_case" in p for p in problems), code


def test_error_envelope_problems_reports_a_missing_error_object():
    r = _FakeResponse(409, {"detail": "Run is already terminal"})
    assert "envelope has no 'error' object" in error_envelope_problems(r, expected_status=409)


def test_error_envelope_problems_reports_a_missing_detail_alias():
    r = _FakeResponse(409, {"error": {"code": "conflict", "message": "m", "details": None}})
    problems = error_envelope_problems(r, expected_status=409)
    assert any("back-compat alias" in p for p in problems)


def test_error_envelope_problems_reports_a_non_json_body():
    r = _FakeResponse(500, None, json_decodable=False)
    assert "body is not JSON" in error_envelope_problems(r, expected_status=500)


def test_error_envelope_problems_never_returns_any_of_the_body():
    # No problem string may carry a value from the response — a 422 body can describe a
    # rejected password (ER-09), and these strings land in CI logs via assertion messages.
    secret_ish = "hunter2-should-never-be-logged"
    r = _FakeResponse(422, _envelope(code="validation_error", details=[{"input": secret_ish}]))
    problems = error_envelope_problems(r, expected_status=409, expected_code="nope")
    assert problems  # it must actually have found faults, or this proves nothing
    assert not any(secret_ish in p for p in problems)


def test_validation_input_echoes_is_empty_when_the_handler_stripped_input():
    details = [{"type": "value_error", "loc": ["body"], "msg": "pack mode requires 'variant'"}]
    assert validation_input_echoes(details) == []


def test_validation_input_echoes_flags_an_entry_that_kept_input():
    details = [
        {"type": "missing", "loc": ["body", "a"], "msg": "field required"},
        {"type": "value_error", "loc": ["body", "password"], "msg": "too short", "input": "abc"},
    ]
    (echo,) = validation_input_echoes(details)
    assert "details[1]" in echo
    assert "abc" not in echo  # names the entry, never the rejected value


def test_validation_input_echoes_flags_details_that_are_not_a_list():
    # A validation_error whose details is an object is already off-contract; it must not pass
    # vacuously just because "no entry has `input`".
    assert validation_input_echoes({"input": "x"}) != []


#: A fictional stand-in for a stack's BACKEND_JWT_SECRET, in the shape the docs tell an
#: operator to generate (`openssl rand -hex 32`) — long enough that PyJWT does not warn
#: about a weak HMAC key, and never any real deployment's value.
_FAKE_JWT_SECRET = "0" * 63 + "1"  # noqa: S105 — a fictional test fixture, not a credential


def test_expired_token_skip_reason_refuses_a_non_loopback_target_even_with_a_secret():
    reason = expired_token_skip_reason("https://api.example.com", _FAKE_JWT_SECRET)
    assert reason is not None
    assert "non-loopback" in reason
    assert _FAKE_JWT_SECRET not in reason


def test_expired_token_skip_reason_asks_for_the_secret_on_loopback():
    for raw in (None, "", "   "):
        reason = expired_token_skip_reason("http://127.0.0.1:8100", raw)
        assert reason is not None and "GTM_LIVE_JWT_SECRET" in reason


def test_expired_token_skip_reason_allows_a_loopback_target_with_a_secret():
    assert expired_token_skip_reason("http://127.0.0.1:8100", _FAKE_JWT_SECRET) is None


def test_fail_node_skip_reason_requires_the_env_var():
    reason = fail_node_skip_reason(None, ("radar",))
    assert reason is not None and "GTM_FAKE_RUN_FAIL_NODE" in reason


def test_fail_node_skip_reason_refuses_a_node_this_variant_never_reaches():
    reason = fail_node_skip_reason("sequence", ("radar",))
    assert reason is not None and "radar" in reason


def test_fail_node_skip_reason_allows_a_declared_node_of_this_variant():
    assert fail_node_skip_reason("  radar  ", ("radar", "plan")) is None


def test_expired_access_token_is_already_expired_and_shaped_like_an_access_token():
    payload = decode_jwt_payload(
        expired_access_token(_FAKE_JWT_SECRET, user_id="u1", workspace_id="w1", age_s=600)
    )
    assert payload["sub"] == "u1"
    assert payload["workspace_id"] == "w1"
    assert payload["type"] == "access"
    assert payload["exp"] < time.time()
    assert payload["iat"] < payload["exp"]  # issued before it expired, as a real token is


def test_expired_access_token_verifies_under_the_secret_it_was_signed_with():
    # It must be a REAL signature, or the 401 it provokes would be token_invalid (a bad
    # signature) rather than token_expired — the two codes the p1 test tells apart.
    jwt = pytest.importorskip("jwt")
    token = expired_access_token(_FAKE_JWT_SECRET, user_id="u1", workspace_id="w1")
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, _FAKE_JWT_SECRET, algorithms=["HS256"])
