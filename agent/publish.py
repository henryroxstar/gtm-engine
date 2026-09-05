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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from gtm_core.publish_hash import approval_hash as _approval_hash
from gtm_core.publish_hash import content_hash as _content_hash

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The ONLY keys that may ever appear in the outbound body. Asserted by tests so a
#: future edit that smuggles in an account/route/user field fails CI loudly.
#:
#: ``scheduled_at`` is a TIME, not a destination — it says *when*, never *where*. The
#: server still chooses the account and the provider; which scheduler ultimately
#: receives the post is deliberately not knowable from this process.
_ALLOWED_PAYLOAD_KEYS = frozenset({"post", "media_urls", "scheduled_at"})

#: Field names that MUST NEVER appear in the payload (account/route selectors). The
#: server pins the account; if any of these leak in, that is the vulnerability.
#:
#: The ``channel*``/``profile*`` names were added when scheduling landed: a scheduler
#: names its destinations ``channelId``/``profileIds``, vocabulary the original
#: LinkedIn-only list did not cover. Scheduling is the feature most likely to tempt
#: someone into "just pass the channel through", so the words are denied by name.
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
        "channel",
        "channel_id",
        "channelId",
        "channel_ids",
        "channelIds",
        "profile_id",
        "profileId",
        "profile_ids",
        "profileIds",
    }
)

_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_PER_HOUR = 5
_DEFAULT_MAX_CHARS = 3000  # LinkedIn post hard cap ≈ 3000.
_RATE_WINDOW_S = 3600.0
#: How far ahead a post may be scheduled. A bound exists so a malformed or injected
#: timestamp cannot park a post years out where no one will review it again.
_DEFAULT_MAX_HORIZON_DAYS = 90
#: Tolerance for "the future" — clock skew between this process and the scheduler.
_SCHEDULE_SKEW_S = 60.0

# Control sentinels the cockpit uses to delimit a publish draft. Any of these
# appearing INSIDE post text are stripped before hashing/sending so scraped
# content cannot forge or nest a gate block.
_CONTROL_SENTINEL_RE = re.compile(r"⟦/?(?:GATE:publish|POST|MEDIA|SCHEDULE|IDENTITY)⟧")


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
    #: Scheduling gets its OWN opt-in, on top of ``enabled``. Enabling immediate
    #: publish is a human saying "send this, now, while I am here"; it is not consent
    #: for posts to fire days later with nobody watching. Closed by default, and a
    #: scheduled draft with this shut is rejected rather than quietly posted now.
    schedule_enabled: bool = False
    max_horizon_days: int = _DEFAULT_MAX_HORIZON_DAYS

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
            schedule_enabled=_truthy(os.getenv("HERMES_SCHEDULE_ENABLED")),
            max_horizon_days=_int("HERMES_SCHEDULE_MAX_HORIZON_DAYS", _DEFAULT_MAX_HORIZON_DAYS),
        )


# --------------------------------------------------------------------------- #
# Draft parsing + content hashing (pure; shared by the cockpit and tests)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PublishDraft:
    """A parsed publish request: the exact post text + optional https media urls.

    ``scheduled_at`` is the optional requested send time (raw, as the brain wrote it —
    validated later by :func:`validate_schedule`, never trusted here).

    ``identity_used`` names which render-identity handles produced the asset this post
    is about (any of ``"soul"``, ``"element"``, ``"voice"``; ``()`` means none) — Phase
    12's disclosure signal, checked by :func:`validate_disclosure`. A trained Soul and a
    cloned voice can legitimately co-occur on the same asset, which is why this is a
    tuple rather than a single enum.
    """

    post: str
    media_urls: tuple[str, ...] = ()
    scheduled_at: str | None = None
    identity_used: tuple[str, ...] = ()


_POST_RE = re.compile(r"⟦POST⟧(.*?)⟦/POST⟧", re.DOTALL)
_MEDIA_RE = re.compile(r"⟦MEDIA⟧(.*?)⟦/MEDIA⟧", re.DOTALL)
_SCHEDULE_RE = re.compile(r"⟦SCHEDULE⟧(.*?)⟦/SCHEDULE⟧", re.DOTALL)
_IDENTITY_RE = re.compile(r"⟦IDENTITY⟧(.*?)⟦/IDENTITY⟧", re.DOTALL)
_PUBLISH_GATE = "⟦GATE:publish⟧"
#: The only values ⟦IDENTITY⟧ may carry — anything else is dropped, not merely
#: ignored, so there is nothing for injected text to smuggle through this field.
#:
#: "generated" marks an AI-generated or AI-restyled asset with NO likeness/voice handle behind it —
#: e.g. a Higgsfield restyle preset applied to real footage. Every render this repo produces is
#: synthetic media under EU AI Act Art. 50, identity handle or not; before this addition
#: `video-restyle` could only emit identity_used=() for such an asset, and an empty tuple reads as
#: "nothing to disclose" to validate_disclosure below — a fail-open, not a choice.
#: `soul`/`element`/`voice` continue to mark the additionally-reinforced
#: deepfake-of-a-real-person duty; `generated` alone still triggers the same disclosure
#: requirement.
_KNOWN_IDENTITY_VALUES = frozenset({"soul", "element", "voice", "generated"})


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
        ⟦SCHEDULE⟧2026-08-20T09:00:00Z⟦/SCHEDULE⟧   (optional)
        ⟦IDENTITY⟧soul,voice⟦/IDENTITY⟧              (optional)

    Only the FIRST of each block is honored, and any nested control sentinels inside
    the post are stripped — so scraped/model text cannot forge a second gate or
    smuggle a destination. The destination is not representable here at all; there is
    no field for it. ``⟦SCHEDULE⟧`` carries a **time and nothing else**: it moves
    *when*, never *where*, and a post with one still goes through the identical
    operator gate — scheduling is publishing with a delay, not a lighter action.
    ``⟦IDENTITY⟧`` is a comma-separated whitelist subset (unknown tokens dropped, never
    surfaced) naming which render-identity handles are behind this asset — absent means
    "none", not "unknown".
    """
    if _PUBLISH_GATE not in raw:
        return None
    m = _POST_RE.search(raw)
    if not m:
        return None
    post = _CONTROL_SENTINEL_RE.sub("", m.group(1)).strip()
    if not post:
        return None

    # Search every OTHER block only OUTSIDE the chosen POST span — never inside it.
    # Without this, a forged, well-formed ⟦MEDIA⟧/⟦SCHEDULE⟧/⟦IDENTITY⟧ block quoted
    # INSIDE the post text (which the post-stripping above only removes the visible
    # sentinel fragments of) would still be found by a plain `raw` search and treated
    # as genuine — attaching a media URL, a send time, or an identity claim the
    # operator never saw in the reviewed post text. Excising the POST span first
    # closes that gap for every field at once, not just the one this comment sits on.
    outside_post = raw[: m.start()] + raw[m.end() :]

    media: list[str] = []
    mm = _MEDIA_RE.search(outside_post)
    if mm:
        for line in mm.group(1).splitlines():
            url = _CONTROL_SENTINEL_RE.sub("", line).strip()
            if url:
                media.append(url)

    scheduled_at: str | None = None
    ms = _SCHEDULE_RE.search(outside_post)
    if ms:
        scheduled_at = _CONTROL_SENTINEL_RE.sub("", ms.group(1)).strip() or None

    identity_used: tuple[str, ...] = ()
    im = _IDENTITY_RE.search(outside_post)
    if im:
        raw_values = _CONTROL_SENTINEL_RE.sub("", im.group(1)).split(",")
        # dict.fromkeys dedupes while preserving first-seen order — ⟦IDENTITY⟧soul,soul⟧
        # should read the same as ⟦IDENTITY⟧soul⟧ to anything that counts/enumerates it.
        identity_used = tuple(
            dict.fromkeys(
                v for v in (val.strip() for val in raw_values) if v in _KNOWN_IDENTITY_VALUES
            )
        )

    return PublishDraft(
        post=post, media_urls=tuple(media), scheduled_at=scheduled_at, identity_used=identity_used
    )


# content_hash is the idempotency key over a publish's exact bytes. Its canonical
# definition lives in gtm_core (the shared engine layer) so the manual-publish
# recorder (gtm_core.ledger_cli) computes the SAME hash without importing agent.
# Re-exported here so agent.publish.content_hash is unchanged for existing callers.
content_hash = _content_hash
approval_hash = _approval_hash


# --------------------------------------------------------------------------- #
# Payload + validation (pure; the single place the body is built)
# --------------------------------------------------------------------------- #


def build_payload(
    post: str,
    media_urls: tuple[str, ...] | list[str],
    scheduled_at: str | None = None,
) -> dict:
    """Construct the EXACT outbound body — and nothing else.

    Returns ``{"post": post}`` plus ``"media_urls"`` when media is present and
    ``"scheduled_at"`` when a (already-validated) send time is present. There is
    deliberately no parameter for an account / channel / route / user id, so this
    function structurally cannot emit one — adding scheduling did not add a way to
    say *where*. Tests assert the key set is a subset of
    :data:`_ALLOWED_PAYLOAD_KEYS` and disjoint from :data:`_FORBIDDEN_PAYLOAD_KEYS`.
    """
    payload: dict = {"post": post}
    if media_urls:
        payload["media_urls"] = list(media_urls)
    if scheduled_at:
        payload["scheduled_at"] = scheduled_at
    return payload


def validate_schedule(
    scheduled_at: str,
    now: datetime,
    max_horizon_days: int = _DEFAULT_MAX_HORIZON_DAYS,
) -> str | None:
    """Return a rejection reason for a requested send time, or ``None`` if valid.

    Treats the timestamp as untrusted. Three guards:

    1. **Explicit UTC only.** ``2026-08-20T09:00:00Z`` or ``+00:00``. A naive
       timestamp is rejected rather than assumed local — "09:00" with no zone is a
       different moment on every host, and silently guessing publishes at the wrong
       hour in a way nobody notices until it is public.
    2. **Future**, within :data:`_SCHEDULE_SKEW_S` tolerance for clock skew. A past
       time means "post immediately" at most schedulers, which is not what an
       operator approving a *scheduled* post agreed to.
    3. **Bounded horizon.** A post cannot be parked years out where no one will
       review it again before it fires.
    """
    raw = (scheduled_at or "").strip()
    if not raw:
        return "empty schedule time"
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"schedule time is not ISO-8601: {raw[:40]!r}"
    if when.tzinfo is None:
        return f"schedule time has no timezone — use explicit UTC (…Z): {raw[:40]!r}"
    when = when.astimezone(UTC)
    if when <= now - timedelta(seconds=_SCHEDULE_SKEW_S):
        return f"schedule time is in the past: {when.isoformat()}"
    if when > now + timedelta(days=max_horizon_days):
        return f"schedule time is more than {max_horizon_days} days out: {when.isoformat()}"
    return None


def validate_disclosure(
    post: str,
    identity_used: tuple[str, ...],
    disclosure_lines: tuple[str, ...] | list[str],
) -> str | None:
    """Return a rejection reason if a synthetic-identity post fails to disclose it, or
    ``None`` if clear. Phase 12 (§6.2) — the EU AI Act Article 50 duty, enforced the same
    way the schedule time is: at STAGING, so the operator is never shown a preview they
    could approve into a refusal.

    ``identity_used`` empty ⇒ nothing to disclose, always clears. Non-empty ⇒ fail
    CLOSED: no configured line at all is a refusal, not a silent pass — an operator who
    never set ``BRAND.toml``'s ``[disclosure].line`` has not opted out of the duty, they
    have not configured how to meet it yet.

    ``disclosure_lines`` accepts more than one candidate on purpose: the caller (the
    cockpit) knows the profile but not which kit — company or a specific product — the
    draft's identity came from, so it passes every configured line it can find and ANY
    exact match clears the draft.
    """
    if not identity_used:
        return None
    lines = [line.strip() for line in disclosure_lines if line and line.strip()]
    if not lines:
        return (
            "synthetic likeness/voice used but no disclosure line is configured "
            "(BRAND.toml [disclosure].line)"
        )
    if not any(line in post for line in lines):
        return (
            "synthetic likeness/voice used but the post does not carry a configured disclosure line"
        )
    return None


def candidate_disclosure_lines(profiles_root: Path, profile: str) -> list[str]:
    """Every configured ``[disclosure].line`` for ``profile`` — the company kit's,
    plus each declared product's (merged, so a product that doesn't override the line
    still contributes the company's own). Shared by every caller of
    :func:`validate_disclosure` (the cockpit at staging, :func:`dispatch_approved
    _publish <agent.publish_dispatch.dispatch_approved_publish>` at dispatch) so the
    disclosure line is resolved exactly one way regardless of which runtime — VPS
    cockpit or backend — is publishing. The caller knows the PROFILE a draft belongs
    to but not which brand kit — company or a specific product — produced its
    identity, so every candidate line is returned and :func:`validate_disclosure`
    accepts any exact match. Never raises: a malformed or absent kit just
    contributes nothing.
    """
    from gtm_core.brandkit import load_brand_kit, lookup

    from .profiles import load_products

    lines: list[str] = []

    def _line_from(product: str | None) -> None:
        try:
            kit = load_brand_kit(profiles_root, profile, product)
            line = lookup(kit, "disclosure.line")
        except (ValueError, KeyError):
            return
        if isinstance(line, str) and line.strip():
            lines.append(line)

    _line_from(None)
    for product in load_products(profiles_root, profile):
        slug = product.get("slug")
        if isinstance(slug, str) and slug:
            _line_from(slug)
    return lines


def validate_post(post: str, media_urls: tuple[str, ...] | list[str], max_chars: int) -> str | None:
    """Return a human-readable rejection reason, or ``None`` if the draft is valid.

    Treats post/media as untrusted data: a blank post is rejected (covers the
    "missing/blank approval has nothing to publish" case) and every media url must
    be ``https://`` (no ``http``, ``data:``, ``file:``, or relative).

    Also refuses raw control sentinels in the post body. ``parse_publish_block``
    already strips these from a well-formed gate block, so this never trips for the
    cockpit's normal path — it exists for callers that hand a post straight to
    ``validate_post``/``LinkedInPublisher.publish`` without parsing it first (the
    backend Gate-2 path historically did this), where an unparsed
    ``⟦GATE:publish⟧``/``⟦POST⟧``/``⟦SCHEDULE⟧`` blob would otherwise be posted
    verbatim as literal text.
    """
    if not post or not post.strip():
        return "empty post — nothing to publish"
    if _CONTROL_SENTINEL_RE.search(post):
        return "post contains control sentinels — refusing to publish raw gate text"
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
    status: str  # published | scheduled | queued | disabled | schedule_disabled | misconfigured | invalid | duplicate | rate_limited | error
    post_id: str | None = None
    error: str | None = None
    scheduled_at: str | None = None

    def operator_line(self) -> str:
        """A short, secret-free line to show the operator in Telegram."""
        if self.ok:
            if self.status == "scheduled":
                pid = f" (id: {self.post_id})" if self.post_id else ""
                return (
                    f"🗓️ Scheduled for {self.scheduled_at}{pid}. It will post without further "
                    "review — cancel it in the scheduler if that changes."
                )
            if self.status == "queued":
                return "✅ Queued for LinkedIn — publishing in the background. Verify the post appears on LinkedIn within ~3 minutes."
            pid = f" (post id: {self.post_id})" if self.post_id else ""
            return f"✅ Published to LinkedIn{pid}."
        reasons = {
            "disabled": "🚫 Publishing is disabled (kill switch HERMES_PUBLISH_ENABLED=false).",
            "schedule_disabled": (
                "🚫 Scheduling is disabled (HERMES_SCHEDULE_ENABLED=false). Nothing was sent — "
                "the post was NOT published immediately instead."
            ),
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
    #: Wall clock, injectable so schedule validation is deterministic under test.
    #: Separate from ``_monotonic`` on purpose: the rate-limit window needs a clock
    #: that cannot jump, a send time needs one that maps to a real calendar moment.
    _now_utc: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
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
        scheduled_at: str | None = None,
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
        # (1b) Scheduling kill switch — its own opt-in, checked BEFORE anything is
        #      sent. Note what this deliberately does NOT do: it never falls back to
        #      publishing now. Downgrading a scheduled post to an immediate one would
        #      turn a disabled feature into an unrequested public post.
        if scheduled_at and not s.schedule_enabled:
            return PublishResult(ok=False, status="schedule_disabled", scheduled_at=scheduled_at)
        # (2) Misconfiguration — never call a blank endpoint.
        if not s.url or not s.secret:
            return PublishResult(ok=False, status="misconfigured")
        # (3) Transport hardening — refuse anything but a clean https endpoint.
        if not _https_ok(s.url):
            return PublishResult(
                ok=False, status="invalid", error="publish URL is not a clean https url"
            )
        # (4) Validation — untrusted post/media/time.
        reason = validate_post(post, media, s.max_chars)
        if reason:
            return PublishResult(ok=False, status="invalid", error=reason)
        if scheduled_at:
            reason = validate_schedule(scheduled_at, self._now_utc(), s.max_horizon_days)
            if reason:
                return PublishResult(ok=False, status="invalid", error=reason)

        # Idempotency is over CONTENT, not content+time: the same bytes scheduled for
        # two different slots is a double-post, and keying on the time would let both
        # through. (Approval binding is the other question, and uses approval_hash.)
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

        payload = build_payload(post, media, scheduled_at)
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
            return PublishResult(
                ok=False, status="error", error=f"{type(exc).__name__}", scheduled_at=scheduled_at
            )

        if 200 <= status_code < 300:
            # A scheduled request that was accepted is "scheduled" whatever the 2xx —
            # reporting it as "published" would tell the operator a post is live when
            # it is only booked, which is the one thing they must not be wrong about.
            # Otherwise 202 = async/queued (webhook responded before the provider did).
            if scheduled_at:
                st = "scheduled"
            else:
                st = "queued" if status_code == 202 else "published"
            return PublishResult(
                ok=True, status=st, post_id=_extract_post_id(body), scheduled_at=scheduled_at
            )

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
