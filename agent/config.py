"""Runtime configuration for the Content OS brain.

`Config` is the single, immutable source of truth for paths and secrets. It is
built once per process via `Config.from_env()` (Doppler injects the env at
runtime — secrets are NEVER hardcoded here). The dataclass is frozen so that no
component can mutate shared config after construction.

Path layout (locked by the architecture):
  - SOURCE  (read from the git checkout): ``plugin/``, ``profiles/``
  - STATE   (a gitignored volume):        ``content/<profile>/``

Defensive contract: every optional secret is guarded — a missing env var yields
``None`` (or an empty set / empty dict), never a crash. Nothing here calls
``datetime.now()`` or ``os.urandom()`` at import time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from gtm_core.paths import resolve_content_root, resolve_profiles_root

# Single source of truth for the default ElevenLabs voice (was duplicated across
# config.py, web.py, and .env.example).
DEFAULT_VOICE_ID = "Fahco4VZzobUeiPqni1S"  # Archer — Conversational (male, calm, British)


def _repo_root_default() -> Path:
    """Best-effort repo root: the parent of the ``agent/`` package directory.

    ``__file__`` is ``<repo>/agent/config.py`` → ``parents[1]`` is ``<repo>``.
    Callers may override by passing ``repo_root`` to :meth:`Config.from_env`.
    """
    return Path(__file__).resolve().parents[1]


def _parse_chat_ids(raw: str | None) -> set[int]:
    """Parse a comma-separated allow-list of Telegram chat ids into ``set[int]``.

    Tolerant of whitespace, trailing commas, and blank entries. Non-integer
    tokens are skipped rather than raising — a malformed env var must not crash
    the bot at startup (it would just leave the allow-list smaller).
    """
    if not raw:
        return set()
    ids: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            # Ignore garbage tokens; an empty/partial allow-list is safer than a crash.
            continue
    return ids


def _google_oauth_from_env() -> dict:
    """Collect Google Workspace OAuth credentials into a dict, omitting absent keys.

    Returns ``{}`` when no Google env is present so :func:`mcp_config.build_mcp_servers`
    can gate the ``google`` server purely on truthiness.
    """
    mapping = {
        "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN"),
    }
    # Drop empty/None values — the gate treats an empty dict as "not configured".
    return {k: v for k, v in mapping.items() if v}


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration.

    Frozen-ish per the shared interface: constructed once, never mutated. Paths
    are derived from ``repo_root`` so the whole tree moves together (checkout vs.
    container volume). Secrets are optional and default to ``None``.
    """

    repo_root: Path
    plugin_path: Path
    profiles_root: Path
    content_root: Path
    default_profile: str

    # --- Cockpit (Telegram) ---
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: set[int] = field(default_factory=set)

    # --- News (PROD discovery_items, read-only Postgres) ---
    news_db_dsn: str | None = None

    # --- Worker model (DeepSeek behind MCP — never the SDK brain model) ---
    deepseek_api_key: str | None = None

    # --- Vision worker (Haiku behind MCP — cheap image→text OCR, never the brain) ---
    # Reuses the platform ANTHROPIC_API_KEY; the worker pins model `claude-haiku-4-5`
    # so the expensive brain model never spends vision tokens on screenshot extraction.
    anthropic_api_key: str | None = None

    # --- Google Workspace (self-hosted CLI-MCP) ---
    google_oauth: dict = field(default_factory=dict)

    # --- Gemini image worker (headless image path — pinned gemini-3-pro-image-preview) ---
    # Per-image billing; see agent/mcp/gemini_image/server.py for pricing constants.
    gemini_api_key: str | None = None

    # --- Higgsfield video worker (headless video path — pinned DoP Standard) ---
    # Both key + secret are required; either missing disables the server.
    higgsfield_api_key: str | None = None
    higgsfield_api_secret: str | None = None

    # --- Deck renderer sidecar (Slidev/Playwright/Chromium export via mcp__deck__export_deck) ---
    # Set to the sidecar's HTTP base URL (e.g. http://deck-renderer:3000) to enable
    # headless deck export on the VPS. When None the deck MCP server is not registered
    # and the skills fall through to the Phase 1 graceful-fail hand-off.
    deck_renderer_url: str | None = None

    # --- Firecrawl (web crawling for onboarding ingest) -----------------------
    # Wired as an env-gated MCP server for the brain AND called directly by
    # gtm_core/ingest.py via httpx (outside the §R6 agent/ boundary). When absent,
    # URL ingest raises RuntimeError.
    firecrawl_api_key: str | None = None

    # --- RocketReach (contact resolution: verified email/phone + person intent) ---
    # Env-gated MCP server (agent/mcp/rocketreach). When absent the server is not
    # spawned and the prospect skill falls back to Vibe/web for contacts (§R6:
    # external I/O only via MCP). Doppler-injected; never inlined or echoed.
    rocketreach_api_key: str | None = None

    # --- Saleshandy (email sequencing — in-repo wrapper agent/mcp/saleshandy) ---
    # Env-gated MCP server. When absent the server is not spawned and the
    # email-sequence skill falls back to its manual, paste-ready plan (§R6:
    # external I/O only via MCP). Doppler-injected; never inlined or echoed.
    # The wrapper exposes STAGING + READ tools only — no activate/resume/send
    # tool exists, so the headless brain cannot make Saleshandy send email by
    # construction (docs/prds/2026-07-13-email-sequence.md).
    saleshandy_api_key: str | None = None

    # --- Syften (community social-listening — in-repo wrapper agent/mcp/syften) ---
    # Env-gated MCP server. When absent the server is not spawned and the
    # community-signal-analysis skill falls back to a manual CSV drop (§R6:
    # external I/O only via MCP). Doppler-injected; never inlined or echoed.
    # The wrapper exposes READ tools only — there is no filter-set/write tool, so the
    # brain cannot change Syften configuration by construction (filter changes stay a
    # human, dashboard action; the skill only recommends corrected filters).
    syften_api_key: str | None = None

    # --- Onboarding cost guard (§R2 — cost-cap-before-paid-calls) ------------
    # Max USD spend for a single onboarding run (Firecrawl crawl + extract call).
    # Checked against content/_system/costs.jsonl before the URL crawl.
    # None = uncapped (only appropriate in dev).
    onboarding_cap_usd: float | None = None

    # --- Voice (ElevenLabs TTS — Telegram cockpit + podcast voices) ---
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = DEFAULT_VOICE_ID

    # --- Brain model + cost controls ---
    # model: Claude model alias or full ID. Defaults to "claude-sonnet-4-6" in
    # build_agent_options(). Override via HERMES_MODEL env var without a redeploy.
    model: str | None = None
    # per_run_cap_usd: hard SDK-level stop per run (enforced via max_budget_usd).
    # Mirrors the PROFILE per_run_cap_usd field but is enforced at the process level.
    per_run_cap_usd: float = 10.0

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> Config:
        """Build a :class:`Config` from process environment variables.

        Args:
            repo_root: Override the repo root. Defaults to the parent of the
                ``agent/`` package (the git checkout root).

        Every secret is read defensively — absence yields ``None`` / empty
        containers, so the brain can start with a partial environment (e.g. only
        ``ANTHROPIC_API_KEY`` set for a pure-Claude dry run).
        """
        root = (repo_root or _repo_root_default()).resolve()
        return cls(
            repo_root=root,
            plugin_path=root / "plugin",
            profiles_root=resolve_profiles_root(root),
            content_root=resolve_content_root(root),
            # ACTIVE_PROFILE is the DEFAULT only; each Telegram chat binds its own
            # profile in the SessionStore (no global mutable ACTIVE_PROFILE).
            default_profile=os.getenv("ACTIVE_PROFILE", "template").strip() or "template",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_allowed_chat_ids=_parse_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_ID")),
            news_db_dsn=os.getenv("NEWS_DB_DSN") or None,
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            google_oauth=_google_oauth_from_env(),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            higgsfield_api_key=os.getenv("HIGGSFIELD_API_KEY") or None,
            higgsfield_api_secret=os.getenv("HIGGSFIELD_API_SECRET") or None,
            deck_renderer_url=os.getenv("DECK_RENDERER_URL") or None,
            firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY") or None,
            rocketreach_api_key=os.getenv("ROCKETREACH_API_KEY") or None,
            saleshandy_api_key=os.getenv("SALESHANDY_API_KEY") or None,
            syften_api_key=os.getenv("SYFTEN_API_KEY") or None,
            onboarding_cap_usd=(
                float(os.getenv("GTM_ONBOARDING_CAP_USD"))
                if os.getenv("GTM_ONBOARDING_CAP_USD")
                else None
            ),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY") or None,
            elevenlabs_voice_id=(
                os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID).strip() or DEFAULT_VOICE_ID
            ),
            model=os.getenv("HERMES_MODEL") or None,
            per_run_cap_usd=float(os.getenv("PER_RUN_CAP_USD") or 10.0),
        )
