"""Runtime capability probe and capability-tier resolver for gtm_core.

Two distinct concerns live here because both are pure env-probe logic with
no Agent-SDK imports:

1. **Capabilities / probe()** — cheap env-var snapshot of what's technically
   available in the current runtime (mode, connectors, degraded-path flags).
   Used by skills at startup to announce their mode.

2. **resolve_effective()** — capability-tier resolver. Intersects the skill's
   declared `capability_tier` with what the caller is *entitled* to receive and
   what is *technically available*. Returns "allowed", "fallback", or "denied".
   Called at runtime boundaries (never inside skills).

   Decision matrix (enforced by tests/contracts/test_tiers.py):

   Tier       | Runtime  | Entitlement | Connectors | Result
   -----------|----------|-------------|------------|----------
   CORE       | any      | any         | any        | allowed
   PIPELINE   | plugin   | any         | any        | fallback
   PIPELINE   | any      | free/none   | any        | denied
   PIPELINE   | any      | pro/pro_plus| present    | allowed
   PIPELINE   | any      | pro/pro_plus| absent     | fallback
   PRODUCTION | plugin   | any         | any        | denied
   PRODUCTION | mcp      | any         | any        | denied  (no PRODUCTION at MCP launch)
   PRODUCTION | vps/back | not pro_plus| any        | denied
   PRODUCTION | vps/back | pro_plus    | present    | allowed
   PRODUCTION | vps/back | pro_plus    | absent     | denied  (no PRODUCTION fallback)

   Security note (plan fix #5): the plugin tier lock is a *product* boundary,
   not a security control. Real protection comes from PIPELINE/PRODUCTION
   requiring server-side connectors and credentials the free/local user can
   never hold. Enforcement that matters is server-side (Phase D/E).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .skills.base import GTMSkill

from .tiers import Tier

# ── Entitlement ───────────────────────────────────────────────────────────────


class Entitlement(str, Enum):
    """What a caller is entitled to.

    FREE     — plugin user or unauthenticated / unsubscribed backend user.
    PRO      — paid tier 1: CORE + PIPELINE.
    PRO_PLUS — paid tier 2: CORE + PIPELINE + PRODUCTION.
    NONE     — entitlement not yet resolved (treated as FREE by the resolver).
    """

    FREE = "free"
    PRO = "pro"
    PRO_PLUS = "pro_plus"
    NONE = "none"


# ── Entitlement → monthly cost cap (pre-sync FAIL-SAFE only) ──────────────────
#
# Billing moved OUT of the engine to the billing service (see the billing-boundary design doc):
# the real per-plan spend cap now arrives over the wire from the billing service's pricing via
# PUT /v1/entitlement/{workspace_id} and is stored in subscriptions.monthly_cost_cap_usd.
# gtm-engine no longer holds pricing numbers. This mapping is only the FAIL-SAFE used
# before a workspace has been synced: an unresolved / un-synced plan gets a 0 cap (no
# paid spend), never an open-ended one. There are deliberately NO pro/pro_plus dollar
# values here — a paid workspace's cap is whatever the billing service last synced.
DEFAULT_MONTHLY_CAP_USD: dict[Entitlement, float] = {
    Entitlement.FREE: 0.0,  # free tier carries no paid budget — an enforcement invariant
}


def monthly_cap_for(entitlement: Entitlement | str) -> float:
    """Return the FAIL-SAFE monthly cost cap (USD) for an un-synced entitlement.

    The authoritative per-plan cap comes from the billing service's entitlement sync (stored in
    ``subscriptions.monthly_cost_cap_usd``); this is only the fallback before that lands.
    Every value — including ``pro``/``pro_plus`` — resolves to 0 (no paid spend until
    billing confirms a plan), which is the safe default. Accepts the enum or its string.
    """
    if isinstance(entitlement, str):
        try:
            entitlement = Entitlement(entitlement)
        except ValueError:
            return DEFAULT_MONTHLY_CAP_USD[Entitlement.FREE]
    return DEFAULT_MONTHLY_CAP_USD.get(entitlement, DEFAULT_MONTHLY_CAP_USD[Entitlement.FREE])


# ── RuntimeKind ───────────────────────────────────────────────────────────────


class RuntimeKind(str, Enum):
    """Which runtime surface is executing the skill.

    PLUGIN  — Cowork plugin; local, no server-side connectors, BYO Claude key.
    VPS     — Personal VPS; full connectors, always pro_plus in practice.
    BACKEND — Multi-tenant FastAPI (Phase D); entitlement from subscription.
    MCP     — Third-party MCP server (Phase E); read/draft/research only at launch.
    """

    PLUGIN = "plugin"
    VPS = "vps"
    BACKEND = "backend"
    MCP = "mcp"


# ── ConnectorSet ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectorSet:
    """Which connectors are technically available in this runtime.

    Built from a Capabilities snapshot via from_capabilities(), or constructed
    directly in tests and enforcement adapters.

    Pipeline connectors (needed for Tier.PIPELINE skills):
      has_news_db  — Postgres discovery_items; content-radar primary path.
      has_publish  — Hermes webhook + cockpit; content-publish primary path.

    Compute connectors (needed for Tier.PRODUCTION skills):
      has_elevenlabs — TTS / podcast generation.
      has_higgsfield — AI video / image generation (carousel-visuals, carousel-auto).
    """

    has_news_db: bool = False
    has_publish: bool = False
    has_elevenlabs: bool = False
    has_higgsfield: bool = False
    has_gemini: bool = False

    @classmethod
    def from_capabilities(cls, cap: Capabilities) -> ConnectorSet:
        return cls(
            has_news_db=cap.has_news_db,
            has_publish=cap.has_publish,
            has_elevenlabs=cap.has_elevenlabs,
            has_higgsfield=cap.has_higgsfield,
            has_gemini=cap.has_gemini,
        )

    def has_pipeline_connector(self) -> bool:
        """True if at least one PIPELINE-tier connector is live."""
        return self.has_news_db or self.has_publish

    def has_compute_connector(self) -> bool:
        """True if at least one PRODUCTION-tier compute connector is live."""
        return self.has_elevenlabs or self.has_higgsfield or self.has_gemini


# ── RuntimeContext ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuntimeContext:
    """The full context the resolver needs to gate a skill invocation.

    Construct per-request in the runtime boundary code (never inside a skill).
    For convenience factories see: runtime_context_for_plugin(),
    runtime_context_for_vps().
    """

    runtime_kind: RuntimeKind
    entitlement: Entitlement
    connectors: ConnectorSet


# ── Capabilities (probe snapshot) ────────────────────────────────────────────


@dataclass
class Capabilities:
    """Snapshot of what's available in the current runtime."""

    mode: str  # "vps" | "local"
    content_root: str  # resolved content root path (for display)
    profiles_root: str  # resolved profiles root path (for display)

    # Connectors
    has_news_db: bool  # NEWS_DB_DSN set → can query discovery_items
    has_firecrawl: bool  # FIRECRAWL_API_KEY set
    has_vibe: bool  # VIBE_PROSPECTING_* connector present
    has_google: bool  # GOOGLE_OAUTH_* set
    has_publish: bool  # HERMES_PUBLISH_URL + ENABLED=true
    has_telegram: bool  # TELEGRAM_BOT_TOKEN set (cockpit running)
    has_elevenlabs: bool  # ELEVENLABS_API_KEY set
    has_higgsfield: bool  # HIGGSFIELD_API_KEY set (DoP Standard headless video)
    has_gemini: bool  # GEMINI_API_KEY set (gemini-3-pro-image-preview headless image)

    # Degraded paths active
    radar_via_web: bool  # no news DB → radar falls back to web sweep
    prospect_via_web: bool  # no Vibe → prospect via web-search path
    events_via_browser: bool  # no Firecrawl → events via browser fallback
    publish_is_manual: bool  # no cockpit/webhook → operator posts by hand

    def banner(self) -> str:
        """One-line mode banner for skill output."""
        connectors = []
        if self.has_news_db:
            connectors.append("news-db")
        if self.has_firecrawl:
            connectors.append("firecrawl")
        if self.has_vibe:
            connectors.append("vibe")
        if self.has_google:
            connectors.append("google")
        if self.has_publish:
            connectors.append("publish-webhook")
        if self.has_telegram:
            connectors.append("telegram")
        if self.has_elevenlabs:
            connectors.append("elevenlabs")
        if self.has_higgsfield:
            connectors.append("higgsfield")
        if self.has_gemini:
            connectors.append("gemini-image")
        degraded = []
        if self.radar_via_web:
            degraded.append("radar→web")
        if self.prospect_via_web:
            degraded.append("prospect→web")
        if self.events_via_browser:
            degraded.append("events→browser")
        if self.publish_is_manual:
            degraded.append("publish→manual")
        parts = [f"[{self.mode.upper()} MODE]", f"content: {self.content_root}"]
        if connectors:
            parts.append("connectors: " + ", ".join(connectors))
        if degraded:
            parts.append("degraded: " + ", ".join(degraded))
        return " | ".join(parts)

    def connector_set(self) -> ConnectorSet:
        """Extract a ConnectorSet for use in resolve_effective()."""
        return ConnectorSet.from_capabilities(self)

    def runtime_context(self, entitlement: Entitlement = Entitlement.FREE) -> RuntimeContext:
        """Build a RuntimeContext from this probe snapshot.

        The runtime kind is inferred from mode; entitlement must be supplied by
        the caller (it comes from the subscription or session, not env probing).
        """
        kind = RuntimeKind.VPS if self.mode == "vps" else RuntimeKind.PLUGIN
        return RuntimeContext(
            runtime_kind=kind,
            entitlement=entitlement,
            connectors=self.connector_set(),
        )


def probe(repo_root=None) -> Capabilities:
    """Probe the current runtime and return a Capabilities snapshot."""
    from .paths import resolve_content_root, resolve_profiles_root

    content_root = resolve_content_root(repo_root)
    profiles_root = resolve_profiles_root(repo_root)

    has_news_db = bool(os.getenv("NEWS_DB_DSN"))
    has_firecrawl = bool(os.getenv("FIRECRAWL_API_KEY"))
    has_vibe = bool(os.getenv("VIBE_PROSPECTING_API_KEY") or os.getenv("VIBE_API_KEY"))
    has_google = bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID"))
    has_publish = (
        bool(os.getenv("HERMES_PUBLISH_URL"))
        and os.getenv("HERMES_PUBLISH_ENABLED", "false").lower() == "true"
    )
    has_telegram = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    has_elevenlabs = bool(os.getenv("ELEVENLABS_API_KEY"))
    has_higgsfield = bool(os.getenv("HIGGSFIELD_API_KEY"))
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))

    # Mode: VPS if Telegram cockpit is wired up and news DB or publish is present
    mode = "vps" if (has_telegram and (has_news_db or has_publish)) else "local"

    return Capabilities(
        mode=mode,
        content_root=str(content_root),
        profiles_root=str(profiles_root),
        has_news_db=has_news_db,
        has_firecrawl=has_firecrawl,
        has_vibe=has_vibe,
        has_google=has_google,
        has_publish=has_publish,
        has_telegram=has_telegram,
        has_elevenlabs=has_elevenlabs,
        has_higgsfield=has_higgsfield,
        has_gemini=has_gemini,
        radar_via_web=not has_news_db,
        prospect_via_web=not has_vibe,
        events_via_browser=not has_firecrawl,
        publish_is_manual=not has_publish,
    )


# ── Convenience factories ─────────────────────────────────────────────────────


def runtime_context_for_plugin() -> RuntimeContext:
    """RuntimeContext for the free Cowork plugin. Always FREE + no connectors."""
    return RuntimeContext(
        runtime_kind=RuntimeKind.PLUGIN,
        entitlement=Entitlement.FREE,
        connectors=ConnectorSet(),
    )


def runtime_context_for_vps(cap: Capabilities | None = None) -> RuntimeContext:
    """RuntimeContext for the personal VPS. Always PRO_PLUS + full connector set."""
    if cap is None:
        cap = probe()
    return RuntimeContext(
        runtime_kind=RuntimeKind.VPS,
        entitlement=Entitlement.PRO_PLUS,
        connectors=ConnectorSet.from_capabilities(cap),
    )


# ── Tier resolver ─────────────────────────────────────────────────────────────


def resolve_effective(
    skill: GTMSkill,
    ctx: RuntimeContext,
) -> Literal["allowed", "fallback", "denied"]:
    """Resolve whether a skill may run given the runtime context.

    Returns:
      "allowed"  — run at full capability.
      "fallback" — run in degraded mode (emit skill.fallback_note into the prompt
                   for SDK runtimes, or call skill.fallback() for pure-Python paths).
      "denied"   — do not run; surface an upgrade prompt or an access-denied error.

    This function is the single authoritative decision point. Call it at each
    runtime boundary; never inside a skill.
    """
    tier = skill.capability_tier

    if tier == Tier.CORE:
        return "allowed"

    if tier == Tier.PIPELINE:
        if ctx.runtime_kind == RuntimeKind.PLUGIN:
            # Plugin has no server-side connectors — always degrades gracefully.
            return "fallback"
        if ctx.entitlement not in (Entitlement.PRO, Entitlement.PRO_PLUS):
            # FREE and NONE both lack PIPELINE access.
            return "denied"
        # Paid + VPS/BACKEND/MCP: allow if connectors are present, else fallback.
        return "allowed" if ctx.connectors.has_pipeline_connector() else "fallback"

    # Tier.PRODUCTION
    if ctx.runtime_kind in (RuntimeKind.PLUGIN, RuntimeKind.MCP):
        # Plugin: hard lock (no fallback for heavy compute in a free/local runtime).
        # MCP: no PRODUCTION tools at launch (plan Phase E constraint).
        return "denied"
    if ctx.entitlement != Entitlement.PRO_PLUS:
        return "denied"
    # PRO_PLUS + VPS/BACKEND: require compute connectors; no PRODUCTION fallback.
    return "allowed" if ctx.connectors.has_compute_connector() else "denied"
