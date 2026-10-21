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

   Two gates, in order. The COMMERCIAL gate ("has this workspace paid to reach
   this skill?") is owned entirely by ``gtm_core/gating.toml`` and read through
   ``gtm_core.gating.commercial_floor()`` — this module carries no second copy of
   that ladder. The TECHNICAL gate ("can this runtime actually provide it?") is
   the ``capability_tier`` logic below, and stays in code.

   Decision matrix (enforced by tests/contracts/test_tiers.py):

   Tier       | Runtime  | Entitlement    | Connectors | Result
   -----------|----------|----------------|------------|----------
   CORE       | plugin   | not consulted  | any        | allowed
   PIPELINE   | plugin   | not consulted  | any        | fallback
   PRODUCTION | plugin   | not consulted  | any        | denied
   any        | non-plug | < skill floor  | any        | denied
   CORE       | non-plug | >= skill floor | any        | allowed
   PIPELINE   | non-plug | >= skill floor | present    | allowed
   PIPELINE   | non-plug | >= skill floor | absent     | fallback
   PRODUCTION | mcp      | >= skill floor | any        | denied  (no PRODUCTION at MCP launch)
   PRODUCTION | vps/back | >= skill floor | present    | allowed
   PRODUCTION | vps/back | >= skill floor | absent     | denied  (no PRODUCTION fallback)

   "skill floor" is ``gating.commercial_floor(skill.name)``, NOT a fixed rung per
   tier. The two axes are independent by design: ``airq-scan`` is technically
   PRODUCTION and commercially free, ``prospect`` is technically CORE and
   commercially "pro". A hardcoded per-tier ladder here silently disagreed with
   gating.toml on all three of those cases until 2026-08-25.

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


# Tier ordering for min_entitlement comparisons (pack API). NONE deliberately maps to
# FREE's rank — "not yet resolved" must never unlock more than the free tier. This is
# the ONLY ordered view of the enum; keep comparisons going through entitlement_meets()
# so the ordering lives in exactly one place.
_ENTITLEMENT_RANK: dict[Entitlement, int] = {
    Entitlement.NONE: 0,
    Entitlement.FREE: 0,
    Entitlement.PRO: 1,
    Entitlement.PRO_PLUS: 2,
}


def entitlement_meets(current: Entitlement | str, minimum: Entitlement | str) -> bool:
    """True when ``current`` satisfies a variant's ``min_entitlement`` of ``minimum``.

    Fail-closed on unknown strings: an unrecognized ``current`` ranks as FREE, an
    unrecognized ``minimum`` is unsatisfiable (a typo in a pack TOML must never grant
    access — though the loader rejects those at load time anyway).
    """
    try:
        cur = Entitlement(current) if isinstance(current, str) else current
    except ValueError:
        cur = Entitlement.FREE
    try:
        mini = Entitlement(minimum) if isinstance(minimum, str) else minimum
    except ValueError:
        return False
    return _ENTITLEMENT_RANK[cur] >= _ENTITLEMENT_RANK[mini]


def entitlement_rank(entitlement: Entitlement | str) -> int:
    """Public rank accessor (0/1/2) — for callers that need an actual ordering (max of
    two entitlements), not just a meets/doesn't-meet check. ``entitlement_meets()``
    stays the right call for a simple gate; this exists for ``gtm_core.gating``, which
    computes the *maximum* of a graph's node floors and any policy override. Fail-closed
    on an unrecognized string, same as ``entitlement_meets()``: ranks as FREE (0).
    """
    if isinstance(entitlement, str):
        try:
            entitlement = Entitlement(entitlement)
        except ValueError:
            return _ENTITLEMENT_RANK[Entitlement.FREE]
    return _ENTITLEMENT_RANK[entitlement]


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
    has_vibe: bool  # VIBE_PROSPECTING_CONNECTED=true (manual — it's an OAuth MCP connector, no key)
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
    # Vibe Prospecting is an OAuth MCP connector (plugin/.mcp.json) — it has no API
    # key, so connection state isn't visible to this process's environment. This is
    # a manual flag: set VIBE_PROSPECTING_CONNECTED=true once the connector is
    # authorized, or the probe always reports the prospect-via-web fallback active.
    has_vibe = os.getenv("VIBE_PROSPECTING_CONNECTED", "false").strip().lower() == "true"
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


FOUNDER_WORDS: dict[str, tuple[str, str]] = {
    "Vibe Prospecting": (
        "Finds companies that fit your ICP",
        "Settings → Connectors → Add custom connector",
    ),
    "RocketReach": (
        "Finds verified buyer email and phone numbers",
        "Settings → Connectors → Add custom connector",
    ),
    "Firecrawl": (
        "Deep web research and scraping",
        "Settings → Connectors → Add custom connector",
    ),
    "Saleshandy": (
        "Cold email sending and sequencing",
        "configured on server",
    ),
    "Syften": (
        "Community social listening across Reddit, X, and forums",
        "Settings → Connectors → Add custom connector",
    ),
    "Google": (
        "Reads email and calendar context",
        "Settings → Connectors",
    ),
    "Telegram Cockpit": (
        "Mobile approval gates and notifications",
        "configured on server",
    ),
    "ElevenLabs": (
        "Voice synthesis and spoken audio",
        "Settings → Connectors → Add custom connector",
    ),
    "Higgsfield": (
        "AI video and motion visuals",
        "Settings → Connectors → Add custom connector",
    ),
    "Gemini": (
        "Image rendering and visual analysis",
        "Settings → Connectors → Add custom connector",
    ),
}


def _probe_tool(name: str) -> bool:  # noqa: PLR0911 — one early return per connector probe
    if name == "Vibe Prospecting":
        return os.getenv("VIBE_PROSPECTING_CONNECTED", "false").strip().lower() == "true"
    if name == "RocketReach":
        return bool(os.getenv("ROCKETREACH_API_KEY"))
    if name == "Firecrawl":
        return bool(os.getenv("FIRECRAWL_API_KEY"))
    if name == "Saleshandy":
        return bool(os.getenv("SALESHANDY_API_KEY"))
    if name == "Syften":
        return bool(os.getenv("SYFTEN_API_KEY"))
    if name == "Google":
        return bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID"))
    if name == "Telegram Cockpit":
        return bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    if name == "ElevenLabs":
        return bool(os.getenv("ELEVENLABS_API_KEY"))
    if name == "Higgsfield":
        return bool(os.getenv("HIGGSFIELD_API_KEY"))
    if name == "Gemini":
        return bool(os.getenv("GEMINI_API_KEY"))
    return False


def main(argv: list[str] | None = None) -> int:
    for tool_name, (what, where) in FOUNDER_WORDS.items():
        try:
            connected = _probe_tool(tool_name)
            if connected:
                print(f"{tool_name} — {what} — connected")
            else:
                print(f"{tool_name} — {what} — not connected ({where})")
        except Exception as exc:
            print(f"{tool_name} — {what} — couldn't check ({exc})")
    return 0


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

    The entitlement question is answered by ``gtm_core.gating.commercial_floor()``
    (i.e. by ``gtm_core/gating.toml``), never by a per-tier ladder written here.
    Before 2026-08-25 this function hardcoded "PIPELINE needs pro, PRODUCTION needs
    pro_plus", which disagreed with the policy file the run path actually enforces
    (``backend/routers/runs.py`` -> ``resolve_graph_entitlement``,
    ``gtm_core.packs.reachability.entitled_skills_for_profile``) in both directions:
    it denied a PRO workspace the creator pack's PRODUCTION skills that gating.toml
    prices at "pro", and it denied a FREE workspace the commercially-free
    ``airq-scan`` / ``knowledge-refresh`` / ``outcomes-sync``.
    """
    tier = skill.capability_tier

    # ── PLUGIN: the free, local, bring-your-own-key runtime. No entitlement gate at
    # all — there is no workspace and no billing relationship to gate on, so higher
    # tiers degrade (PIPELINE) or hard-lock (PRODUCTION) purely on missing
    # server-side capability. This is the "free everywhere" contract in tiers.py.
    if ctx.runtime_kind == RuntimeKind.PLUGIN:
        if tier == Tier.CORE:
            return "allowed"
        if tier == Tier.PIPELINE:
            # Plugin has no server-side connectors — always degrades gracefully.
            return "fallback"
        # PRODUCTION: hard lock (no fallback for heavy compute in a free/local runtime).
        return "denied"

    # ── Commercial gate (paid runtimes only). Lazy import: gtm_core.gating imports
    # this module at module scope, so the dependency has to run the other way here.
    from . import gating

    if not entitlement_meets(ctx.entitlement, gating.commercial_floor(skill.name)):
        return "denied"

    # ── Technical gate: what this runtime can actually provide.
    if tier == Tier.CORE:
        return "allowed"

    if tier == Tier.PIPELINE:
        # Entitled + VPS/BACKEND/MCP: allow if connectors are present, else fallback.
        return "allowed" if ctx.connectors.has_pipeline_connector() else "fallback"

    # Tier.PRODUCTION
    if ctx.runtime_kind == RuntimeKind.MCP:
        # No PRODUCTION tools at MCP launch (plan Phase E constraint).
        return "denied"
    # VPS/BACKEND: require compute connectors; no PRODUCTION fallback.
    return "allowed" if ctx.connectors.has_compute_connector() else "denied"


if __name__ == "__main__":
    import sys

    sys.exit(main())
