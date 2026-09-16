"""Pure helpers for tests/live — the HTTP acceptance suite (D1 scaffold).

No network calls at import time and nothing here requires ``GTM_LIVE_BASE_URL`` to be set:
that is what makes the pure pieces (the SSE line parser, JWT payload decoding, the gating
predicates) unit-testable in the normal fast suite (``tests/live/test_support_unit.py``).
``tests/live/conftest.py`` wires these into fixtures and the collection-time skip logic.
"""

from __future__ import annotations

import base64
import functools
import ipaddress
import json
import os
import re
import time
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]

LIVE_BASE_URL_ENV = "GTM_LIVE_BASE_URL"
#: Distinct from the SERVER-side ``GTM_FAKE_RUNS`` (deploy/docker-compose.dev.yml) — this is
#: how the TEST PROCESS is told the stack it's pointed at was started with that flag on, since
#: it has no other way to know.
LIVE_FAKE_RUNS_ENV = "GTM_LIVE_FAKE_RUNS"
#: Explicit opt-in only — real_executor tests spend money, so no `-m` expression alone may
#: enable them (a marker-substring check is unsafe: `-m "p3 or not real_executor"` still
#: selects them). See real_executor_skip_reason.
LIVE_REAL_EXECUTOR_ENV = "GTM_LIVE_REAL_EXECUTOR"
BILLING_SYNC_SECRET_ENV = "BILLING_SYNC_SECRET"  # nosec B105 — an env-var name, not a credential
#: The stack's own HS256 signing secret (its ``BACKEND_JWT_SECRET``), declared to the TEST
#: PROCESS so it can mint an ALREADY-EXPIRED access token — the one 401 branch that cannot be
#: provoked with a token the API itself issued (an access token's TTL is 60 minutes). Loopback
#: only, and refused for anything else: see :func:`expired_token_skip_reason`.
LIVE_JWT_SECRET_ENV = "GTM_LIVE_JWT_SECRET"  # nosec B105 — an env-var name, not a credential
#: How the TEST PROCESS is told the stack was started with the server-side
#: ``GTM_FAKE_RUN_FAIL_NODE`` knob, and at which node id — the same test-process/server split
#: as :data:`LIVE_FAKE_RUNS_ENV` above, for the same reason: the test has no other way to know.
LIVE_FAIL_NODE_ENV = "GTM_LIVE_FAKE_RUN_FAIL_NODE"
#: A shell command string (e.g. a ``docker compose ... up -d --force-recreate`` invocation)
#: the operator supplies to prove a gated run survives a mid-flight recreate — this process
#: cannot recreate the stack it is itself talking to, so it can only be TOLD the command,
#: never construct one. See :func:`recreate_cmd_skip_reason`.
LIVE_RECREATE_CMD_ENV = "GTM_LIVE_RECREATE_CMD"
#: How many workers the stack under test was started with — declared to the TEST PROCESS the
#: same way :data:`LIVE_FAIL_NODE_ENV` is, since ``BACKEND_WORKERS`` is a server-side setting
#: this process cannot see. See :func:`multi_worker_skip_reason`.
LIVE_WORKERS_ENV = "GTM_LIVE_WORKERS"

try:  # scripts/dev_seed.py is absent from the OSS carve (deploy/ is withheld there too).
    from scripts.dev_seed import DEFAULT_PROFILE
except ModuleNotFoundError:  # pragma: no cover — exercised only in the public carve
    DEFAULT_PROFILE = "example-widgets"


# ── SSE frame parsing ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SseFrame:
    """One reassembled ``text/event-stream`` frame."""

    id: int | None
    event: str
    data: dict


def parse_sse_lines(lines: Iterable[str]) -> Iterator[SseFrame]:
    """Reassemble raw SSE lines (no trailing newline, e.g. from ``httpx.Response.iter_lines``)
    into :class:`SseFrame` objects.

    Follows the WHATWG ``text/event-stream`` FIELD grammar (id/event/data/comment lines,
    multiple ``data:`` lines joined with ``\\n`` before JSON-decoding, a line with no ``:``
    naming a field with an empty value) but deliberately NOT the spec's dispatch algorithm
    verbatim: per spec, a block whose data buffer is empty never dispatches, even if it set an
    ``event:`` type. This parser dispatches whenever EITHER `event:` or `data:` was seen — more
    permissive than the spec, so a block like `event: ping` with an accidentally-blank `data:`
    surfaces as an empty-dict frame rather than being silently swallowed. A block with NEITHER
    (this server's `retry: 3000` preamble) dispatches nothing either way. ``id`` is left
    ``None`` when the block never set one (this server's ping frames, and a `done` synthesised
    on a snapshot/replay connect before the relay durably records it —
    schemas/run-event.schema.json's RECONNECT CONTRACT).
    """
    field_id: str | None = None
    field_event: str | None = None
    data_lines: list[str] = []

    def _dispatch() -> SseFrame | None:
        if not data_lines and field_event is None:
            return None
        text = "\n".join(data_lines)
        data = json.loads(text) if text else {}
        return SseFrame(
            id=int(field_id) if field_id is not None else None,
            event=field_event or "message",
            data=data,
        )

    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            frame = _dispatch()
            field_id, field_event, data_lines = None, None, []
            if frame is not None:
                yield frame
            continue
        if line.startswith(":"):
            continue  # comment — carries no field
        if ":" in line:
            name, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
        else:
            name, value = line, ""
        if name == "id":
            field_id = value
        elif name == "event":
            field_event = value
        elif name == "data":
            data_lines.append(value)
        # "retry" and any other field name: not needed by this test tree, ignored.
    # An unterminated trailing block (stream cut off mid-frame, no closing blank line) is
    # deliberately NOT dispatched — the spec dispatches only on the blank line, and this
    # server always closes every frame with one.


# ── JWT payload decoding (no verification — this test minted the token itself) ────────────


def decode_jwt_payload(token: str) -> dict:
    """Decode a JWT's payload segment WITHOUT verifying the signature — base64 only, no
    secret. Only ever call this on a token this test process itself just obtained from
    ``POST /v1/auth/register`` — it proves nothing about a token's authenticity."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(f"not a JWT: expected 3 dot-separated segments, got {len(parts)}")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# ── env / URL predicates ────────────────────────────────────────────────────────────────


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def is_loopback(base_url: str) -> bool:
    """True if ``base_url``'s host is this machine (127.0.0.1 / ::1 / localhost).

    Mirrors ``scripts.dev_seed.require_loopback``'s host check (kept as a small, self-
    contained duplicate rather than importing that module here, so this predicate — used by
    the collection-time gate on EVERY test under tests/live — works even in a distribution
    that withholds scripts/dev_seed.py, e.g. the OSS carve).
    """
    parts = urlsplit(base_url or "")
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _billing_secret_transport_ok(base_url: str) -> bool:
    """True if it's safe to put the billing-sync secret in a plain ``Authorization`` header
    for this ``base_url``: loopback (any scheme — the loopback interface is itself the trust
    boundary) or ``https://`` for a non-loopback host. Never cleartext ``http://`` to a real
    network host — a typo'd or mistyped ``GTM_LIVE_BASE_URL`` must not put this secret on the
    wire in the clear."""
    if is_loopback(base_url):
        return True
    return urlsplit(base_url or "").scheme == "https"


def billing_sync_secret(base_url: str) -> str | None:
    """``BILLING_SYNC_SECRET`` from the environment; else, ONLY for a loopback ``base_url``,
    read from ``deploy/.env.dev`` — reusing ``scripts.dev_seed``'s tiny KEY=VALUE reader
    rather than a second parser. Never reads the file for a non-loopback target: a local
    secret would not authenticate against staging anyway, and this keeps the "only touch
    loopback with this secret" posture ``scripts/dev_seed.py`` itself enforces.

    Refuses to return the secret at all — even one already sitting in the environment —
    unless ``base_url`` is loopback or ``https://``: this secret can grant a paid entitlement
    to any workspace (``backend/deps.py:require_service_auth``), so it must never go out as a
    plain header over cleartext ``http://`` to a real network host, whether that's a typo'd
    ``GTM_LIVE_BASE_URL`` or a deliberate non-loopback target.
    """
    if not _billing_secret_transport_ok(base_url):
        return None
    value = os.environ.get(BILLING_SYNC_SECRET_ENV, "").strip()
    if value:
        return value
    if not is_loopback(base_url):
        return None
    try:
        from scripts.dev_seed import _env_file_values
    except ModuleNotFoundError:
        return None
    env_file = REPO / "deploy" / ".env.dev"
    if not env_file.is_file():
        return None
    return _env_file_values(env_file).get(BILLING_SYNC_SECRET_ENV) or None


# ── collection-time skip predicates (pure, so test_support_unit.py can check them directly) ─


def live_stack_skip_reason(base_url_raw: str) -> str | None:
    """None if ``tests/live`` should run at all; else the skip reason."""
    if not (base_url_raw or "").strip():
        return f"tests/live: set {LIVE_BASE_URL_ENV} to run"
    return None


def real_executor_skip_reason(env_value: str | None) -> str | None:
    """``real_executor`` tests spend money — they run ONLY on an explicit env opt-in, never
    from a `-m` expression: a markexpr substring check is unsafe (`-m "p3 or not
    real_executor"` would still select them — pytest ORs/NOTs terms, it doesn't parse
    intent)."""
    if truthy(env_value):
        return None
    return f"real_executor tests spend money — opt in explicitly with {LIVE_REAL_EXECUTOR_ENV}=1"


def local_fake_skip_reason(base_url_raw: str, fake_runs_raw: str | None) -> str | None:
    """``local_fake`` tests need the local Docker dev stack, started with the fake executor."""
    if is_loopback(base_url_raw) and truthy(fake_runs_raw):
        return None
    return f"local_fake needs a loopback {LIVE_BASE_URL_ENV} and {LIVE_FAKE_RUNS_ENV}=1"


def expired_token_skip_reason(base_url_raw: str, secret_raw: str | None) -> str | None:
    """None if this process may mint an already-expired access token for ``base_url_raw``.

    The loopback check comes FIRST and refuses on its own, before the secret is even looked
    at: signing a token for a real deployment means holding that deployment's
    ``BACKEND_JWT_SECRET``, and a test run must never do that even if the operator exported
    one anyway. The refusal is therefore a property of the TARGET, not of whether a secret
    happens to be present — a typo'd or deliberately-staging ``GTM_LIVE_BASE_URL`` can never
    turn into a minted token.
    """
    if not is_loopback(base_url_raw):
        return (
            f"refusing to mint a token for a non-loopback {LIVE_BASE_URL_ENV} — "
            f"{LIVE_JWT_SECRET_ENV} is a local-stack-only affordance"
        )
    if not (secret_raw or "").strip():
        return (
            f"needs the local stack's own HS256 signing secret as {LIVE_JWT_SECRET_ENV} "
            "(the value it runs BACKEND_JWT_SECRET with) to sign an already-expired token"
        )
    return None


def fail_node_skip_reason(fail_node_raw: str | None, script_nodes: Iterable[str]) -> str | None:
    """None if the stack's injected-failure node is one this test's variant actually runs.

    ``GTM_FAKE_RUN_FAIL_NODE`` is a STACK-WIDE server flag, so a test can only be told about
    it, never set it — and a value naming no node of a given run's own script is ignored with
    a warning rather than failing that run, so a test asserting a failure has to check the
    declared node is in ITS variant's script before it can expect one.
    """
    nodes = tuple(script_nodes)
    node = (fail_node_raw or "").strip()
    if not node:
        return (
            "needs a stack started with GTM_FAKE_RUN_FAIL_NODE=<node id>, declared to this "
            f"process as {LIVE_FAIL_NODE_ENV}=<the same node id>"
        )
    if node not in nodes:
        return (
            f"{LIVE_FAIL_NODE_ENV}={node!r} names no node this test's variant fails at "
            f"({', '.join(nodes)}) — a fake run only fails at a node its own script reaches"
        )
    return None


def recreate_cmd_skip_reason(env_value: str | None) -> str | None:
    """None if a recreate command has been declared via ``GTM_LIVE_RECREATE_CMD``.

    This process is talking to the very stack a recreate would restart, so it can only be
    TOLD the command to run (typically a ``docker compose ... up -d --force-recreate``
    invocation) — never opt itself in the way ``real_executor``/``local_fake`` do from a
    bare flag."""
    if (env_value or "").strip():
        return None
    return (
        f"needs a recreate command declared via {LIVE_RECREATE_CMD_ENV} (e.g. a "
        "`docker compose ... up -d --force-recreate --no-build api` invocation) — this "
        "process cannot recreate the stack it is itself talking to"
    )


def multi_worker_skip_reason(env_value: str | None) -> str | None:
    """None if the stack under test was declared to be running more than one worker.

    ``BACKEND_WORKERS`` is a server-side setting this process cannot see or set — the same
    test-process/server split as :data:`LIVE_FAIL_NODE_ENV` — so it is told the count via
    ``GTM_LIVE_WORKERS`` instead. A value below 2 is refused too: with one worker there is
    no second dispatcher for an exactly-once assertion to rule out."""
    raw = (env_value or "").strip()
    if not raw:
        return (
            f"needs {LIVE_WORKERS_ENV}=<worker count> (e.g. 2), naming a stack started with "
            "BACKEND_WORKERS > 1"
        )
    try:
        count = int(raw)
    except ValueError:
        return f"{LIVE_WORKERS_ENV}={raw!r} is not an integer worker count"
    if count < 2:
        return (
            f"{LIVE_WORKERS_ENV}={count} names fewer than 2 workers — nothing to prove "
            "exactly-once dispatch across"
        )
    return None


# ── minting an expired token (loopback only — see expired_token_skip_reason) ──────────────


def expired_access_token(
    secret: str, *, user_id: str, workspace_id: str, age_s: float = 3600.0
) -> str:
    """An ``access`` JWT in this backend's own claim shape — ``sub`` / ``workspace_id`` /
    ``type`` / ``iat`` / ``exp``, HS256 (``backend/auth.py:_make_token``) — signed with
    ``secret`` and already expired by ``age_s`` seconds.

    Only ever reached for a loopback target the operator runs themselves
    (:func:`expired_token_skip_reason` is the gate). ``secret`` is never logged, echoed, or
    put into an assertion message: it is the whole of that stack's authentication.
    """
    import jwt

    expires_at = int(time.time()) - int(age_s)
    return jwt.encode(
        {
            "sub": user_id,
            "workspace_id": workspace_id,
            "type": "access",
            "iat": expires_at - 60,  # issued before it expired, as a real token always is
            "exp": expires_at,
        },
        secret,
        algorithm="HS256",
    )


# ── run-event schema validation (reuses gtm_core's own stdlib-only validator — no new dep) ──

_SCHEMA_PATH = REPO / "schemas" / "run-event.schema.json"


def _inline_refs(node, defs, expansions: int = 0):
    if isinstance(node, dict):
        if "$ref" in node:
            if expansions >= 4:  # beyond tested depth: validate permissively
                return {}
            name = node["$ref"].rsplit("/", 1)[-1]
            return _inline_refs(defs[name], defs, expansions + 1)
        return {k: _inline_refs(v, defs, expansions) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs, expansions) for v in node]
    return node


@functools.lru_cache(maxsize=1)
def _run_event_branches() -> dict[str, dict]:
    """``{event const: inlined data-schema}`` — computed once from
    schemas/run-event.schema.json using gtm_core's own stdlib-only JSON-Schema subset
    validator. Both are first-party, always-present parts of this repo (gtm_core is a hard
    runtime dependency, not optional), so unlike ``scripts.dev_seed`` above there is no
    distribution where this import legitimately fails — no except/fallback here."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    return {
        branch["properties"]["event"]["const"]: _inline_refs(branch["properties"]["data"], defs)
        for branch in schema["oneOf"]
    }


def validate_run_event(event: str, data: dict) -> list[str]:
    """[] when ``(event, data)`` is a valid RunEvent frame per
    schemas/run-event.schema.json — validated with gtm_core's own stdlib-only JSON-Schema
    subset validator (already a first-party dependency; no ``jsonschema`` package needed)."""
    from gtm_core.minischema import validate

    branch = _run_event_branches().get(event)
    if branch is None:
        return [f"unknown event type {event!r}"]
    return validate(data, branch)


# ── error summaries (never the raw response body) ──────────────────────────────────────


def _error_summary(r) -> str:
    """A safe-to-log summary of an HTTP error response: status code + the backend's error
    envelope ``code`` field (schemas: ``{"error": {"code": ..., ...}, "detail": ...}``) —
    NEVER the raw body. FastAPI's default 422 handler echoes the submitted field VALUE back
    in ``detail[].input`` (e.g. the password just POSTed), so ``r.text``/``r.json()`` must
    never land in an assertion message, a raised exception, or a warning — pytest's assertion
    introspection can print locals, and a CI log is not a safe place for a password either."""
    code = None
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
    return f"{r.status_code}" + (f" (code={code})" if code else "")


def _is_backend_error_envelope(r) -> bool:
    """True if ``r``'s body is this backend's own error envelope (schemas.ErrorResponse:
    ``{"error": {"code": str, ...}, ...}``) — used to treat a 404 on DELETE /v1/account as
    "already deleted" only when it demonstrably came from this API, not from some other
    service (a misrouted base_url, a proxy's generic 404 page) that merely shares the status
    code."""
    try:
        body = r.json()
    except ValueError:
        return False
    return (
        isinstance(body, dict)
        and isinstance(body.get("error"), dict)
        and isinstance(body["error"].get("code"), str)
    )


#: An error code is a machine token a client branches on, so its SHAPE is part of the
#: contract: lower snake_case, no leading digit, no trailing or doubled underscore. A code
#: that drifts into prose ("Run not found") is the failure the whole envelope exists to end.
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def error_envelope_problems(r, *, expected_status: int, expected_code: str | None = None) -> list:
    """Everything wrong with ``r`` as an instance of this backend's error envelope
    (``backend/errors.py``'s ``UnifiedErrorResponse``): ``{"error": {"code": str, "message":
    str, "details": obj|list|null}, "detail": ...}``. ``[]`` when it is well-formed, carries
    ``expected_status``, and — when one is given — exactly ``expected_code``.

    Returns the problems rather than raising so one assertion can name all of them at once,
    and never includes any of the BODY in what it returns: a 422's ``details`` can describe a
    rejected field and a gate's ``pending_content`` carries prospect data, neither of which
    belongs in a CI log (the same rule ``_error_summary`` exists for).
    """
    problems: list[str] = []
    if r.status_code != expected_status:
        problems.append(f"expected HTTP {expected_status}, got {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return [*problems, "body is not JSON"]
    if not isinstance(body, dict):
        return [*problems, "body is not a JSON object"]
    if "detail" not in body:
        problems.append("envelope is missing the 'detail' back-compat alias")
    error = body.get("error")
    if not isinstance(error, dict):
        return [*problems, "envelope has no 'error' object"]

    code = error.get("code")
    if not isinstance(code, str):
        problems.append("error.code is not a string")
    elif not _ERROR_CODE_RE.match(code):
        problems.append(f"error.code {code!r} is not snake_case")
    elif expected_code is not None and code != expected_code:
        problems.append(f"expected error.code {expected_code!r}, got {code!r}")

    if not isinstance(error.get("message"), str):
        problems.append("error.message is not a string")
    if "details" not in error:
        problems.append("error has no 'details' key")
    elif not isinstance(error["details"], dict | list | type(None)):
        problems.append("error.details is neither an object, an array, nor null")
    return problems


def validation_input_echoes(details) -> list:
    """The ``validation_error`` detail entries that echo the REJECTED VALUE back to the
    client, named by index only — never by value.

    ``backend/errors.py:validation_exception_handler`` strips pydantic's ``input`` key from
    every entry precisely because ``/v1/auth/*`` would otherwise hand a rejected PASSWORD
    back in the 422 body, from where it reaches client logs (ER-09). Anything left in the
    list this returns is that leak, so a non-empty result is a failure whatever the route.
    A ``details`` that is not a list at all is itself a break of the ``validation_error``
    shape, and is reported here rather than passing vacuously.
    """
    if not isinstance(details, list):
        return [f"details is {type(details).__name__}, not the expected list of error entries"]
    return [
        f"details[{i}] echoes the rejected 'input' value back to the client"
        for i, entry in enumerate(details)
        if isinstance(entry, dict) and "input" in entry
    ]


# ── live users ──────────────────────────────────────────────────────────────────────────


@dataclass
class LiveUser:
    email: str
    password: str = field(repr=False)
    user_id: str
    workspace_id: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    deleted: bool = field(default=False)

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


#: register/login/exchange share a 10/minute per-IP limit (backend/ratelimit.py). Against a
#: real (non-loopback) target this throttles successive registrations well under that, so a
#: careless test run can't eat a real developer's own quota on shared infrastructure like
#: staging. A single mutable holder (not a bare module global) so the throttle state is one
#: object callers could reset in a test, though nothing here needs to.
_MIN_REGISTER_INTERVAL_S = 7.0
_last_register_at = [0.0]


def _throttle_registration(base_url: str) -> None:
    if is_loopback(base_url):
        return  # the local dev stack is this operator's own machine — no shared quota to protect
    now = time.monotonic()
    wait = _last_register_at[0] + _MIN_REGISTER_INTERVAL_S - now
    if wait > 0:
        time.sleep(wait)
    _last_register_at[0] = time.monotonic()


def register_user(http, *, password: str | None = None, on_registered=None) -> LiveUser:
    """POST /v1/auth/register a throwaway user with a fictional, unique email.

    ``on_registered(user)``, if given, is called the MOMENT the account exists server-side —
    before the access token's JWT payload is decoded — so a caller that registers it for
    teardown there (rather than only on this function's return) can still clean up the
    account even if the decode itself raises. :class:`LiveUser` is mutable and this same
    object is what's eventually returned, so a caller holding the reference from
    ``on_registered`` sees ``user_id``/``workspace_id`` filled in once decode succeeds.
    """
    import secrets
    import uuid

    _throttle_registration(str(http.base_url))

    email = f"live-test-{uuid.uuid4().hex}@example.com"
    pw = password or secrets.token_urlsafe(16)
    r = http.post(
        "/v1/auth/register", json={"email": email, "password": pw, "display_name": "Live test"}
    )
    if r.status_code == 429:
        raise RuntimeError(
            "POST /v1/auth/register was rate-limited (429) — register/login/exchange share a "
            "10/minute per-IP limit (backend/ratelimit.py). Prefer the session-scoped "
            "`live_user` fixture over calling `new_user()` repeatedly in one test session; "
            "not retried here on purpose (see DEVELOPMENT.md)."
        )
    if r.status_code != 201:
        raise RuntimeError(f"POST /v1/auth/register -> {_error_summary(r)}")
    tokens = r.json()

    # Record for cleanup BEFORE decoding the JWT: the account already exists server-side at
    # this point, and only `password` + `access_token` (both already in hand) are needed to
    # delete it — a decode failure below must not orphan a real account.
    user = LiveUser(
        email=email,
        password=pw,
        user_id="",
        workspace_id="",
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
    if on_registered is not None:
        on_registered(user)

    payload = decode_jwt_payload(tokens["access_token"])
    user.user_id = payload["sub"]
    user.workspace_id = payload["workspace_id"]
    return user


def _delete_account_request(http, user: LiveUser):
    return http.request(
        "DELETE",
        "/v1/account",
        json={"current_password": user.password},
        headers=user.auth_header(),
    )


def _refresh_then_retry_delete(http, user: LiveUser):
    """One retry after a token refresh — a long session (staging, many tests) can outlive the
    access token's TTL (60 min default) between registration and teardown. Returns the
    retried response, or None if the refresh itself failed (the original 401 is then what
    gets reported)."""
    refreshed = http.post("/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    if refreshed.status_code != 200:
        return None
    tokens = refreshed.json()
    user.access_token = tokens["access_token"]
    user.refresh_token = tokens["refresh_token"]
    return _delete_account_request(http, user)


def delete_user(http, user: LiveUser) -> None:
    """DELETE /v1/account for a user created by :func:`register_user`. Idempotent: a no-op
    if already marked deleted, and a 404 that demonstrably came from THIS backend (its own
    error envelope — see :func:`_is_backend_error_envelope`) counts as already gone, not an
    error. Never raises on failure — teardown must not mask the test's own result — a warning
    is emitted instead, with the status/error code only, never the response body."""
    if user.deleted:
        return
    try:
        r = _delete_account_request(http, user)
        if r.status_code == 401:
            retried = _refresh_then_retry_delete(http, user)
            if retried is not None:
                r = retried
    except Exception as exc:  # noqa: BLE001 — teardown must never raise past the test result
        warnings.warn(f"tests/live teardown: DELETE /v1/account raised {exc!r}", stacklevel=2)
        return
    if r.status_code == 200 or (r.status_code == 404 and _is_backend_error_envelope(r)):
        user.deleted = True
        return
    warnings.warn(f"tests/live teardown: DELETE /v1/account -> {_error_summary(r)}", stacklevel=2)


# ── SSE streaming read ──────────────────────────────────────────────────────────────────


@dataclass
class SseStreamResult:
    frames: list[SseFrame]
    last_id: int | None  # the highest frame id seen — pass back as `since=` to resume


def _timed_lines(lines: Iterable[str], deadline: float) -> Iterator[str]:
    """A second, cheaper layer under httpx's own read timeout (see ``read_sse_stream``): this
    catches a budget overrun made of many small on-time reads (no single one trips httpx's
    per-read timeout), by checking the wall-clock deadline between lines."""
    for line in lines:
        if time.monotonic() > deadline:
            raise TimeoutError("sse_frames: per-line deadline exceeded")
        yield line


def read_sse_stream(
    http,
    user: LiveUser,
    run_id: str,
    *,
    since: int | None = None,
    until=None,
    timeout_s: float = 30.0,
    validate: bool = True,
) -> SseStreamResult:
    """Bearer-authenticated read of ``GET /v1/runs/{run_id}/stream`` — httpx streaming, never
    EventSource semantics, the token only ever in the ``Authorization`` header (never the
    query string). Returns as soon as ``until(frame)`` is true; with no ``until`` it returns
    when the stream ends. Raises ``TimeoutError`` within roughly ``timeout_s`` of wall-clock
    time (never httpx's raw ``ReadTimeout`` — see below), and ``RuntimeError`` if ``until``
    was given but the stream ended (a real close, not a timeout) without ever satisfying it —
    a normal close mid-wait is itself a bug worth surfacing distinctly from a hang. Each frame
    is validated against schemas/run-event.schema.json by default (``validate=False`` to
    disable, e.g. when deliberately reading from something that is not this server) — an
    invalid frame raises ``AssertionError`` naming the event type and the schema errors, never
    the frame's payload (which may carry prospect PII in a real run).

    A blocked ``resp.iter_lines()`` read can only be interrupted by httpx's OWN read timeout —
    the per-line check above cannot interrupt a read already in flight — so httpx's timeout is
    set close to ``timeout_s`` itself (capped at 20s, comfortably above the ~15s server
    heartbeat so a normal idle-but-alive stream under a LONGER budget is never mistaken for a
    stall between pings) rather than some much larger value, and ``httpx.TimeoutException`` is
    caught and re-raised as the documented ``TimeoutError``.
    """
    import httpx

    params: dict[str, int] = {}
    if since is not None:
        params["since"] = since
    deadline = time.monotonic() + timeout_s
    collected: list[SseFrame] = []
    last_id = since
    read_timeout = min(max(timeout_s, 0.001), 20.0)

    try:
        with http.stream(
            "GET",
            f"/v1/runs/{run_id}/stream",
            params=params,
            headers=user.auth_header(),
            timeout=httpx.Timeout(read_timeout, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                raise RuntimeError(f"GET /v1/runs/{run_id}/stream -> {_error_summary(resp)}")
            lines = _timed_lines(resp.iter_lines(), deadline)
            for frame in parse_sse_lines(lines):
                if validate:
                    errors = validate_run_event(frame.event, frame.data)
                    if errors:
                        raise AssertionError(
                            f"invalid RunEvent frame: event={frame.event!r} errors={errors}"
                        )
                collected.append(frame)
                if frame.id is not None:
                    last_id = frame.id
                if until is not None and until(frame):
                    return SseStreamResult(frames=collected, last_id=last_id)
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise TimeoutError(
            f"sse_frames: timed out after {timeout_s}s waiting on run {run_id} "
            f"({len(collected)} frame(s) seen)"
        ) from exc

    if until is not None:
        raise RuntimeError(
            f"sse_frames: the stream for run {run_id} ended before `until` matched any of the "
            f"{len(collected)} frame(s) seen"
        )
    return SseStreamResult(frames=collected, last_id=last_id)
