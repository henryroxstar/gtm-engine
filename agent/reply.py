"""Inbound-reply gate — the reply analogue of :mod:`agent.publish`.

The ``inbound-triage`` skill (docs/prds/2026-07-20-scheduling-triage-signals.md §3.3)
classifies an inbound reply and, for anything worth answering, ends its turn with a
``⟦GATE:reply⟧`` block carrying the EXACT reply text. The cockpit re-displays those
bytes for the operator to approve — identical to the publish gate. This module is the
pure engine: parse the block, validate it, hash it (approval-binding + idempotency),
and — only if a reply transport is *deliberately* configured — send it.

CRITICAL — sending is not a capability the brain holds, and by default not a capability
this system holds either:

  * The brain NEVER sends. It only emits a ``⟦GATE:reply⟧`` draft; the send (if any)
    happens here in Python after a human approves the exact bytes — never via a tool the
    brain can call. This mirrors :mod:`agent.mcp.saleshandy` exposing no send/reply tool.
  * The send transport is **inert by default**. With no ``REPLY_SEND_URL`` /
    ``REPLY_SEND_SECRET`` / ``REPLY_SEND_ENABLED`` configured, :meth:`ReplySender.send`
    returns ``staged`` — the operator approved the text, the system logs a ``reply``
    outcome, and the human sends it from their own inbox/sequencer. This is the safe v1
    (the in-repo Saleshandy wrapper has no reply-send endpoint by design). A real
    transport can be wired later without changing the gate or the brain surface.

  * The reply body is **untrusted data** end to end (RULES.md §R5): it was drafted in
    response to an inbound message the skill treated as data, and it is re-shown verbatim
    for a human to approve — never acted on as an instruction.

Pure stdlib + a lazily-imported ``httpx`` (only inside the default transport), so the
module imports and unit-tests run without httpx present. No ``datetime.now``/``random``.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_PER_HOUR = 20
_DEFAULT_MAX_CHARS = 5000  # an email reply is longer than a LinkedIn post
_RATE_WINDOW_S = 3600.0

# Control sentinels the cockpit uses to delimit a reply draft. Any appearing INSIDE the
# reply text are stripped before hashing/sending so an inbound (untrusted) reply body
# quoted into the draft cannot forge or nest a gate block.
_CONTROL_SENTINEL_RE = re.compile(r"⟦/?(?:GATE:reply|REPLY|THREAD|TO)⟧")

_REPLY_RE = re.compile(r"⟦REPLY⟧(.*?)⟦/REPLY⟧", re.DOTALL)
_THREAD_RE = re.compile(r"⟦THREAD⟧(.*?)⟦/THREAD⟧", re.DOTALL)
_TO_RE = re.compile(r"⟦TO⟧(.*?)⟦/TO⟧", re.DOTALL)
_REPLY_GATE = "⟦GATE:reply⟧"


def _truthy(raw: str | None) -> bool:
    """Strict opt-in parse: only ``true``/``1``/``yes``/``on`` (any case) enable."""
    return (raw or "").strip().lower() in {"true", "1", "yes", "on"}


def _https_ok(url: str | None) -> bool:
    """True iff ``url`` is a clean ``https://`` URL with no embedded credentials."""
    u = (url or "").strip()
    if not u.lower().startswith("https://"):
        return False
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def content_hash(body: str, thread_id: str) -> str:
    """Stable sha256 over the exact reply bytes — binds an approval to its draft and
    is the idempotency key (same reply to the same thread ⇒ sent at most once).

    The ``inbound-reply-v1`` prefix is a version tag distinct from the publish hash;
    do not change it without bumping (a change re-hashes every prior reply)."""
    h = hashlib.sha256()
    h.update(b"inbound-reply-v1\n")
    h.update(body.encode("utf-8"))
    h.update(b"\n--thread--\n")
    h.update(thread_id.encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class ReplyDraft:
    """A parsed reply request: the exact reply text + the thread it answers.

    ``thread_id`` is the inbound thread the reply belongs to (used for send routing
    when a transport is configured, and for outcome attribution). ``to`` is a
    human-readable context line for the operator's approval preview — never a routing
    field. Both are optional; a reply with no thread id is still stageable (it logs and
    the human sends it), it just cannot be auto-sent.
    """

    body: str
    thread_id: str = ""
    to: str = ""


def parse_reply_block(raw: str) -> ReplyDraft | None:
    """Extract a reply draft from a skill turn, or ``None`` if absent/malformed.

    The skill ends its turn with::

        ⟦GATE:reply⟧
        ⟦REPLY⟧
        <exact reply text>
        ⟦/REPLY⟧
        ⟦THREAD⟧<thread-id>⟦/THREAD⟧   (optional)
        ⟦TO⟧<who / company>⟦/TO⟧       (optional, display only)

    Only the FIRST of each block is honored, and any nested control sentinels inside a
    field are stripped — so an inbound reply body quoted back into the draft cannot forge
    a second gate or smuggle a routing marker.
    """
    if _REPLY_GATE not in raw:
        return None
    m = _REPLY_RE.search(raw)
    if not m:
        return None
    body = _CONTROL_SENTINEL_RE.sub("", m.group(1)).strip()
    if not body:
        return None

    def _field(rx: re.Pattern[str]) -> str:
        fm = rx.search(raw)
        return _CONTROL_SENTINEL_RE.sub("", fm.group(1)).strip() if fm else ""

    return ReplyDraft(body=body, thread_id=_field(_THREAD_RE), to=_field(_TO_RE))


def validate_reply(body: str, max_chars: int) -> str | None:
    """Return a human-readable rejection reason, or ``None`` if the draft is valid."""
    if not body or not body.strip():
        return "empty reply — nothing to send"
    if len(body) > max_chars:
        return f"reply is {len(body)} chars (max {max_chars})"
    return None


@dataclass(frozen=True)
class ReplySettings:
    """Immutable reply-send configuration, read once from the environment.

    ``enabled`` defaults to **False**: with no transport configured the gate stages the
    reply for manual send (the safe v1). A transport is only used when a human has set
    ``REPLY_SEND_ENABLED`` truthy AND a clean ``https`` URL + secret are present.
    """

    url: str | None = None
    secret: str | None = None
    enabled: bool = False
    timeout_s: float = _DEFAULT_TIMEOUT_S
    max_per_hour: int = _DEFAULT_MAX_PER_HOUR
    max_chars: int = _DEFAULT_MAX_CHARS

    @classmethod
    def from_env(cls) -> ReplySettings:
        """Build settings from ``REPLY_SEND_*`` env vars (Doppler-injected)."""

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, "").strip() or default)
            except ValueError:
                return default

        return cls(
            url=(os.getenv("REPLY_SEND_URL") or "").strip() or None,
            secret=os.getenv("REPLY_SEND_SECRET") or None,
            enabled=_truthy(os.getenv("REPLY_SEND_ENABLED")),
            timeout_s=_DEFAULT_TIMEOUT_S,
            max_per_hour=_int("REPLY_SEND_MAX_PER_HOUR", _DEFAULT_MAX_PER_HOUR),
            max_chars=_int("REPLY_SEND_MAX_CHARS", _DEFAULT_MAX_CHARS),
        )


@dataclass(frozen=True)
class ReplyResult:
    """Typed outcome of a reply gate approval — never fire-and-forget."""

    ok: bool
    # staged | sent | queued | invalid | duplicate | rate_limited | error
    status: str
    error: str | None = None

    def operator_line(self) -> str:
        """A short, secret-free line to show the operator in Telegram."""
        if self.status == "staged":
            return (
                "📝 Reply approved and logged — copy the exact text above and send it from "
                "your inbox/sequencer. (No auto-send transport is configured.)"
            )
        if self.ok:
            if self.status == "queued":
                return "✅ Reply queued — verify it lands in the thread shortly."
            return "✅ Reply sent."
        reasons = {
            "invalid": f"⚠️ Not sent — {self.error}.",
            "duplicate": "↩️ That exact reply was already handled — not sent again.",
            "rate_limited": "⏳ Reply rate limit reached — not sent. Try later.",
            "error": f"❌ Reply send failed — {self.error}. Not retried.",
        }
        return reasons.get(self.status, f"❌ Reply failed — {self.error}.")


# Transport: (url, headers, json, timeout) -> (status_code, body). Injectable for tests.
Transport = Callable[..., Awaitable[tuple[int, object]]]


async def _httpx_transport(
    url: str, *, headers: dict, json: dict, timeout: float
) -> tuple[int, object]:
    """Default transport: a single hardened httpx POST (imported lazily)."""
    import httpx  # lazy — keeps module + unit tests import-light

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.post(url, headers=headers, json=json)
        try:
            body: object = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON body is still a usable signal
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


@dataclass
class ReplySender:
    """Stateful, process-lifetime reply sender enforcing every guard.

    Default posture (no transport configured): :meth:`send` validates + dedupes and
    returns ``staged`` — the operator approved the bytes, the caller logs a ``reply``
    outcome, and the human sends it. When a transport is deliberately enabled, the same
    guards run and it POSTs ``{"reply": body, "thread_id": thread_id}`` to the pinned
    reply endpoint (the server pins the sending identity; the thread selects the
    conversation). No retry — a failure is surfaced, not hidden.
    """

    settings: ReplySettings
    transport: Transport = _httpx_transport
    _monotonic: Callable[[], float] = time.monotonic
    _handled: set[str] = field(default_factory=set)
    _window: list[float] = field(default_factory=list)

    def _prune_window(self, now: float) -> None:
        cutoff = now - _RATE_WINDOW_S
        self._window[:] = [t for t in self._window if t > cutoff]

    async def send(
        self,
        body: str,
        thread_id: str = "",
        *,
        is_handled: Callable[[str], bool] | None = None,
    ) -> ReplyResult:
        """Run all guards, then stage (default) or send the reply.

        ``is_handled`` is an optional durable idempotency predicate (the cockpit can back
        it with the history ledger) so "handle this reply at most once" survives a
        restart. It is checked but never written here — the caller records the outcome.
        """
        s = self.settings
        key = content_hash(body, thread_id)

        # (1) Validation — untrusted reply text.
        reason = validate_reply(body, s.max_chars)
        if reason:
            return ReplyResult(ok=False, status="invalid", error=reason)

        # (2) Idempotency — same approved reply to the same thread is handled once.
        if key in self._handled or (is_handled is not None and is_handled(key)):
            return ReplyResult(ok=False, status="duplicate")

        # (3) No transport configured → stage for manual send (the safe default). We
        #     still mark it handled so a double-approval doesn't double-log.
        if not s.enabled or not s.url or not s.secret or not _https_ok(s.url):
            self._handled.add(key)
            return ReplyResult(ok=True, status="staged")

        # (4) Rate limit — at most N/hour (defense in depth on the real send path).
        now = self._monotonic()
        self._prune_window(now)
        if len(self._window) >= s.max_per_hour:
            return ReplyResult(ok=False, status="rate_limited")

        # Optimistic record (synchronous — no await in between → race-free).
        self._handled.add(key)
        self._window.append(now)

        payload = {"reply": body, "thread_id": thread_id}
        headers = {
            "Authorization": f"Bearer {s.secret}",
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        }
        try:
            status_code, _body = await self.transport(
                s.url, headers=headers, json=payload, timeout=s.timeout_s
            )
        except Exception as exc:  # noqa: BLE001 — timeout/connect/etc.; surface, do not retry
            self._rollback(key, now)
            return ReplyResult(ok=False, status="error", error=f"{type(exc).__name__}")

        if 200 <= status_code < 300:
            st = "queued" if status_code == 202 else "sent"
            return ReplyResult(ok=True, status=st)

        self._rollback(key, now)
        return ReplyResult(ok=False, status="error", error=f"HTTP {status_code}")

    def _rollback(self, key: str, ts: float) -> None:
        """Undo the optimistic idempotency + rate-limit record after a failure."""
        self._handled.discard(key)
        try:
            self._window.remove(ts)
        except ValueError:
            pass
