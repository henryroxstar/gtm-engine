"""LinkedIn publish — the ONLY outbound-posting capability in Hermes.

Architecture (locked; do not widen):
  - Hermes NEVER holds the PostForMe API key and NEVER calls PostForMe directly.
  - Hermes POSTs to ONE dedicated n8n webhook that is **write-only** and has the
    target LinkedIn account **pinned server-side**. The request body Hermes sends
    is EXACTLY ``{"post": <text>, "media_urls": [<https url>, ...]}`` — it carries
    NO account id, NO route selector, NO user id, NO PostForMe field. The server
    chooses the destination; the agent cannot. There is no code path here that can
    add a destination field — :func:`build_payload` is the single payload builder
    and it has no parameter for one. That absence IS the security boundary: even a
    fully prompt-injected agent can only cause one effect — text posted to the one
    pinned account.

Where the secret lives:
  - ``HERMES_PUBLISH_URL`` / ``HERMES_PUBLISH_SECRET`` are read from the env
    (Doppler-injected) by :meth:`PublishSettings.from_env` and held ONLY by the
    cockpit's publisher instance. They are deliberately NOT threaded through
    :class:`agent.config.Config` (which feeds the SDK options builder), keeping the
    publish secret out of the brain's config object. The secret travels only in the
    ``Authorization: Bearer`` header and is never logged, never echoed into chat,
    never written to history/transcript files.
  - This module NEVER accepts or stores a PostForMe API key or a general n8n
    secret. Least privilege: the only secret it knows is the dedicated Hermes one.

Guards (all enforced before any byte leaves the process):
  1. Kill switch  — ``HERMES_PUBLISH_ENABLED`` must be truthy, else no call.
  2. Misconfig    — URL + secret must be present, else no call.
  3. Transport    — URL must be ``https://``; 10s timeout; bearer auth; NO retry.
  4. Validation   — post non-empty & within length; every media url ``https://``.
  5. Idempotency  — a given content hash publishes at most once (optimistic record
                    + rollback on failure ⇒ a double-click cannot double-post).
  6. Rate limit   — at most N publishes/hour (defense in depth).
  7. Result       — never fire-and-forget: the response is captured and a typed
                    :class:`PublishResult` (ok / status / post_id / error) returns.

Pure stdlib + a lazily-imported ``httpx`` (only inside the default transport, so
the module imports — and the unit tests run — without httpx present). No
``datetime.now``/``random`` at import time.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from gtm_core.publish_hash import content_hash as _content_hash

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The ONLY keys that may ever appear in the outbound body. Asserted by tests so a
#: future edit that smuggles in an account/route/user field fails CI loudly.
_ALLOWED_PAYLOAD_KEYS = frozenset({"post", "media_urls"})

#: Field names that MUST NEVER appear in the payload (account/route selectors). The
#: server pins the account; if any of these leak in, that is the vulnerability.
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "account",
        "account_id",
        "social_account",
        "social_account_id",
        "social_accounts",
        "route",
        "user_id",
        "userId",
        "uid",
        "destination",
        "target",
        "platform",
    }
)

_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_PER_HOUR = 5
_DEFAULT_MAX_CHARS = 3000  # LinkedIn post hard cap ≈ 3000.
_RATE_WINDOW_S = 3600.0

# Control sentinels the cockpit uses to delimit a publish draft. Any of these
# appearing INSIDE post text are stripped before hashing/sending so scraped
# content cannot forge or nest a gate block.
_CONTROL_SENTINEL_RE = re.compile(r"⟦/?(?:GATE:publish|POST|MEDIA)⟧")


def _truthy(raw: str | None) -> bool:
    """Strict opt-in parse: only ``true``/``1``/``yes``/``on`` (any case) enable."""
    return (raw or "").strip().lower() in {"true", "1", "yes", "on"}


def _https_ok(url: str | None) -> bool:
    """True iff ``url`` is a clean ``https://`` URL with no embedded credentials.

    Stricter than a bare ``startswith`` so the guard is consistent and audit-clean:
      - tolerant of stray whitespace (stripped before checking);
      - scheme compared case-insensitively (``Https://`` is still rejected as
        non-canonical, never silently accepted — fail closed);
      - rejects ``user:pass@host`` URLs so a misconfigured endpoint can't smuggle
        credentials that might leak via a crash dump (auth is bearer-only).
    """
    u = (url or "").strip()
    if not u.lower().startswith("https://"):
        return False
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False  # require exact lowercase scheme + a real host
    if parsed.username or parsed.password:
        return False  # no embedded credentials — bearer header only
    return True


# --------------------------------------------------------------------------- #
# Settings (env-sourced; secret stays here, not in agent.config.Config)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PublishSettings:
    """Immutable publish configuration, read once from the environment.

    ``enabled`` defaults to **False** (the kill switch is closed unless a human
    explicitly opens it). A missing URL or secret leaves the capability inert —
    :meth:`LinkedInPublisher.publish` returns ``misconfigured`` and makes no call.
    """

    url: str | None = None
    secret: str | None = None
    enabled: bool = False
    timeout_s: float = _DEFAULT_TIMEOUT_S
    max_per_hour: int = _DEFAULT_MAX_PER_HOUR
    max_chars: int = _DEFAULT_MAX_CHARS

    @classmethod
    def from_env(cls) -> PublishSettings:
        """Build settings from ``HERMES_PUBLISH_*`` env vars (Doppler-injected)."""

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, "").strip() or default)
            except ValueError:
                return default

        return cls(
            url=(os.getenv("HERMES_PUBLISH_URL") or "").strip() or None,
            secret=os.getenv("HERMES_PUBLISH_SECRET") or None,
            enabled=_truthy(os.getenv("HERMES_PUBLISH_ENABLED")),
            timeout_s=_DEFAULT_TIMEOUT_S,
            max_per_hour=_int("HERMES_PUBLISH_MAX_PER_HOUR", _DEFAULT_MAX_PER_HOUR),
            max_chars=_int("HERMES_PUBLISH_MAX_CHARS", _DEFAULT_MAX_CHARS),
        )


# --------------------------------------------------------------------------- #
# Draft parsing + content hashing (pure; shared by the cockpit and tests)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PublishDraft:
    """A parsed publish request: the exact post text + optional https media urls."""

    post: str
    media_urls: tuple[str, ...] = ()


_POST_RE = re.compile(r"⟦POST⟧(.*?)⟦/POST⟧", re.DOTALL)
_MEDIA_RE = re.compile(r"⟦MEDIA⟧(.*?)⟦/MEDIA⟧", re.DOTALL)
_PUBLISH_GATE = "⟦GATE:publish⟧"


def parse_publish_block(raw: str) -> PublishDraft | None:
    """Extract a publish draft from a skill turn, or ``None`` if absent/malformed.

    The skill ends its turn with::

        ⟦GATE:publish⟧
        ⟦POST⟧
        <exact post text>
        ⟦/POST⟧
        ⟦MEDIA⟧            (optional)
        https://…
        ⟦/MEDIA⟧

    Only the FIRST ``⟦POST⟧…⟦/POST⟧`` and ``⟦MEDIA⟧…⟦/MEDIA⟧`` are honored, and any
    nested control sentinels inside the post are stripped — so scraped/model text
    cannot forge a second gate or smuggle a destination. The destination is not
    representable here at all; there is no field for it.
    """
    if _PUBLISH_GATE not in raw:
        return None
    m = _POST_RE.search(raw)
    if not m:
        return None
    post = _CONTROL_SENTINEL_RE.sub("", m.group(1)).strip()
    if not post:
        return None

    media: list[str] = []
    mm = _MEDIA_RE.search(raw)
    if mm:
        for line in mm.group(1).splitlines():
            url = _CONTROL_SENTINEL_RE.sub("", line).strip()
            if url:
                media.append(url)
    return PublishDraft(post=post, media_urls=tuple(media))


# content_hash is the idempotency key over a publish's exact bytes. Its canonical
# definition lives in gtm_core (the shared engine layer) so the manual-publish
# recorder (gtm_core.ledger_cli) computes the SAME hash without importing agent.
# Re-exported here so agent.publish.content_hash is unchanged for existing callers.
content_hash = _content_hash


# --------------------------------------------------------------------------- #
# Payload + validation (pure; the single place the body is built)
# --------------------------------------------------------------------------- #


def build_payload(post: str, media_urls: tuple[str, ...] | list[str]) -> dict:
    """Construct the EXACT outbound body — and nothing else.

    Returns ``{"post": post}`` plus ``"media_urls"`` only when media is present.
    There is deliberately no parameter for an account / route / user id, so this
    function structurally cannot emit one. Tests assert the key set is a subset of
    :data:`_ALLOWED_PAYLOAD_KEYS` and disjoint from :data:`_FORBIDDEN_PAYLOAD_KEYS`.
    """
    payload: dict = {"post": post}
    if media_urls:
        payload["media_urls"] = list(media_urls)
    return payload


def validate_post(post: str, media_urls: tuple[str, ...] | list[str], max_chars: int) -> str | None:
    """Return a human-readable rejection reason, or ``None`` if the draft is valid.

    Treats post/media as untrusted data: a blank post is rejected (covers the
    "missing/blank approval has nothing to publish" case) and every media url must
    be ``https://`` (no ``http``, ``data:``, ``file:``, or relative).
    """
    if not post or not post.strip():
        return "empty post — nothing to publish"
    if len(post) > max_chars:
        return f"post is {len(post)} chars (max {max_chars})"
    for url in media_urls:
        if not _https_ok(url):
            return f"media url is not a clean https url: {url.strip()[:60]!r}"
    return None


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PublishResult:
    """Typed outcome of a publish attempt — never fire-and-forget."""

    ok: bool
    status: str  # published | queued | disabled | misconfigured | invalid | duplicate | rate_limited | error
    post_id: str | None = None
    error: str | None = None

    def operator_line(self) -> str:
        """A short, secret-free line to show the operator in Telegram."""
        if self.ok:
            if self.status == "queued":
                return "✅ Queued for LinkedIn — publishing in the background. Verify the post appears on LinkedIn within ~3 minutes."
            pid = f" (post id: {self.post_id})" if self.post_id else ""
            return f"✅ Published to LinkedIn{pid}."
        reasons = {
            "disabled": "🚫 Publishing is disabled (kill switch HERMES_PUBLISH_ENABLED=false).",
            "misconfigured": "⚠️ Publish endpoint/secret not configured — nothing sent.",
            "invalid": f"⚠️ Not published — {self.error}.",
            "duplicate": "↩️ Already published (idempotent) — not sent again.",
            "rate_limited": "⏳ Rate limit reached — not sent. Try later.",
            "error": f"❌ Publish failed — {self.error}. Not retried.",
        }
        return reasons.get(self.status, f"❌ Publish failed — {self.error}.")


# Transport: (url, headers, json, timeout) -> (status_code, body). Injectable for tests.
Transport = Callable[..., Awaitable[tuple[int, object]]]


async def _httpx_transport(
    url: str, *, headers: dict, json: dict, timeout: float
) -> tuple[int, object]:
    """Default transport: a single hardened httpx POST (imported lazily).

    ``follow_redirects=False`` is set explicitly (it is also httpx's default since
    0.20): a 3xx from a compromised/misconfigured endpoint must NOT re-send the
    body or the bearer header to a redirect target — it surfaces as a non-2xx
    error instead. Stated explicitly so the guarantee survives a client swap.
    """
    import httpx  # lazy — keeps module + unit tests import-light

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        resp = await client.post(url, headers=headers, json=json)
        try:
            body: object = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON body is still a usable signal
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


# --------------------------------------------------------------------------- #
# Publisher
# --------------------------------------------------------------------------- #


@dataclass
class LinkedInPublisher:
    """Stateful, process-lifetime publisher enforcing every guard.

    Idempotency + rate-limit state is in-memory (resets on container restart). That
    is acceptable: the human approval gate and the server-side account pin are the
    real controls, and a restart-induced re-publish would still require a fresh
    human approval. Construction is side-effect-free (no loop, no I/O), so the
    cockpit can build one at startup and tests can build many.
    """

    settings: PublishSettings
    transport: Transport = _httpx_transport
    _monotonic: Callable[[], float] = time.monotonic
    _published: set[str] = field(default_factory=set)
    _window: list[float] = field(default_factory=list)

    def _prune_window(self, now: float) -> None:
        cutoff = now - _RATE_WINDOW_S
        self._window[:] = [t for t in self._window if t > cutoff]

    async def publish(
        self,
        post: str,
        media_urls: tuple[str, ...] | list[str] = (),
        *,
        is_published: Callable[[str], bool] | None = None,
    ) -> PublishResult:
        """Run all guards, then (if clear) POST exactly ``{post[, media_urls]}``.

        The duplicate/rate records are written SYNCHRONOUSLY before the ``await`` and
        rolled back on any non-2xx or transport error — so two concurrent approvals
        cannot both pass the duplicate check, yet a genuine failure frees the slot
        for a re-approval. There is no retry: a failure is surfaced, not hidden.

        ``is_published`` is an optional DURABLE idempotency predicate (the cockpit passes
        one backed by ``content/<profile>/history.jsonl`` via
        :meth:`agent.ledgers.Ledgers.published_content_hashes`). The in-memory ``_published``
        set only dedupes within a process lifetime; consulting the ledger makes
        "publish at most once" survive a restart/redeploy. It is checked but never written
        here — the cockpit records the durable ``published`` event after a success.
        """
        s = self.settings
        media = tuple(media_urls)

        # (1) Kill switch — closed unless explicitly enabled.
        if not s.enabled:
            return PublishResult(ok=False, status="disabled")
        # (2) Misconfiguration — never call a blank endpoint.
        if not s.url or not s.secret:
            return PublishResult(ok=False, status="misconfigured")
        # (3) Transport hardening — refuse anything but a clean https endpoint.
        if not _https_ok(s.url):
            return PublishResult(
                ok=False, status="invalid", error="publish URL is not a clean https url"
            )
        # (4) Validation — untrusted post/media.
        reason = validate_post(post, media, s.max_chars)
        if reason:
            return PublishResult(ok=False, status="invalid", error=reason)

        key = content_hash(post, media)

        # (5) Idempotency — same approved content publishes at most once.
        #     In-memory set guards within this process; the durable ledger predicate
        #     guards across restarts (it survives the in-memory set being cleared).
        if key in self._published or (is_published is not None and is_published(key)):
            return PublishResult(ok=False, status="duplicate")

        # (6) Rate limit — at most N/hour.
        now = self._monotonic()
        self._prune_window(now)
        if len(self._window) >= s.max_per_hour:
            return PublishResult(ok=False, status="rate_limited")

        # Optimistic record (synchronous — no await in between → race-free).
        self._published.add(key)
        self._window.append(now)

        payload = build_payload(post, media)
        headers = {
            "Authorization": f"Bearer {s.secret}",
            "Content-Type": "application/json",
            "Idempotency-Key": key,  # stable across retries → server may dedupe too
        }
        try:
            status_code, body = await self.transport(
                s.url, headers=headers, json=payload, timeout=s.timeout_s
            )
        except Exception as exc:  # noqa: BLE001 — timeout/connect/etc.; surface, do not retry
            self._rollback(key, now)
            return PublishResult(ok=False, status="error", error=f"{type(exc).__name__}")

        if 200 <= status_code < 300:
            # 202 = async/queued (webhook responded before PostForMe completed)
            st = "queued" if status_code == 202 else "published"
            return PublishResult(ok=True, status=st, post_id=_extract_post_id(body))

        # Non-2xx: free the idempotency/rate slot and surface the error (no retry).
        self._rollback(key, now)
        return PublishResult(ok=False, status="error", error=f"HTTP {status_code}")

    def _rollback(self, key: str, ts: float) -> None:
        """Undo the optimistic idempotency + rate-limit record after a failure."""
        self._published.discard(key)
        try:
            self._window.remove(ts)
        except ValueError:
            pass


def _extract_post_id(body: object) -> str | None:
    """Best-effort post-id pull from a JSON-ish response body (never raises)."""
    if not isinstance(body, dict):
        return None
    for k in ("post_id", "id", "postId"):
        v = body.get(k)
        if isinstance(v, (str, int)):
            return str(v)
    data = body.get("data")
    if isinstance(data, dict):
        return _extract_post_id(data)
    return None
