"""MCP server wiring for the brain (claude-agent-sdk ``mcp_servers`` config).

The plugin already ships its own ``.mcp.json`` (Vibe Prospecting, Firecrawl).
This module adds the **runtime/infra** MCP servers that depend on secrets the
plugin cannot know about, and it includes each server ONLY when its env is
present — so a partial environment yields a valid, smaller config rather than a
broken one.

Five gated servers:

  - ``news``           — read-only Postgres over ``discovery_items`` (gated on
                         ``cfg.news_db_dsn``). The DSN points at a least-privilege role
                         (``SELECT`` on ``public.discovery_items`` only).
  - ``google``         — self-hosted Google Workspace CLI-MCP on localhost (gated on
                         ``cfg.google_oauth``).
  - ``worker``         — the DeepSeek worker wrapper (gated on ``cfg.deepseek_api_key``).
                         DeepSeek is a downstream worker called as a tool — NEVER the SDK
                         brain model.
  - ``gemini_image``   — Gemini 3 Pro Image worker (headless image path; gated on
                         ``cfg.gemini_api_key``). Pinned to ``gemini-3-pro-image-preview``.
  - ``higgsfield_video`` — Higgsfield DoP Standard worker (headless video path; gated on
                         both ``cfg.higgsfield_api_key`` + ``cfg.higgsfield_api_secret``).

NOTE: the exact off-the-shelf server packages/commands below are PLACEHOLDERS;
they are finalized in step 0.14 (MCP wiring). The *gating logic* here is real and
final — only the command/args/url strings are subject to the 0.14 decision.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-hint only; no runtime import cycle
    from .config import Config


def build_mcp_servers(cfg: Config, profile: str) -> dict[str, dict]:
    """Return the claude-agent-sdk ``mcp_servers`` dict for ``(cfg, profile)``.

    Each entry is a server-config dict (``{"type": "stdio", ...}`` or
    ``{"type": "http", ...}``). A server is included ONLY when its backing env is
    present, so callers can rely on key membership to know what's wired.

    ``profile`` is accepted for forward-compatibility (per-profile MCP scoping is
    possible later); today the infra servers are agent-global, so it is unused
    beyond signalling intent.
    """
    servers: dict[str, dict] = {}

    # --- news: read-only Postgres over discovery_items ------------------
    # Read-only Postgres MCP (stdio, npx), connecting directly to the prod DB
    # container on the internal Docker net (bypassing Kong) using the
    # least-privilege DSN. The DSN is the single positional arg per that server's
    # CLI contract. Read-only is enforced at the DB-role level (gtm_ro can only
    # SELECT discovery_items), not just by the server.
    #
    # Supply chain (OWASP ASI04 / NIST SR-3,SR-4): the server is BAKED into the image at a
    # PINNED version (Dockerfile `npm install -g …server-postgres@0.6.2`) and invoked by its
    # installed bin — NOT `npx -y`. `npx -y` cold-downloads the package on every spawn in an
    # ephemeral `docker compose run --rm` pipeline container (empty npm cache); two MCP servers
    # cold-starting at once exceeded the SDK's MCP-init window and surfaced to the brain as
    # "Stream closed", silently breaking the radar (2026-06-27). The pre-installed bin spawns
    # instantly with no registry round-trip. `@modelcontextprotocol/server-postgres@0.6.2` is the
    # last published version and is DEPRECATED upstream; the maintained, env-reading replacement
    # (which also moves the DSN out of argv) is tracked in PENDING.md / SECURITY-SELF-ASSESSMENT.md.
    if cfg.news_db_dsn:
        servers["news"] = {
            "type": "stdio",
            "command": "mcp-server-postgres",
            "args": [cfg.news_db_dsn],
            "env": {},
        }

    # --- google: self-hosted Google Workspace CLI-MCP (localhost) -------------
    # Chosen approach (0.14): a self-hosted Google Workspace CLI-MCP bound to
    # localhost, fed the OAuth client_id/secret/refresh_token via env. Least
    # privilege — scopes are granted only where send/write is needed. Runs as a
    # stdio child of the brain (no inbound network surface).
    if cfg.google_oauth:
        env: dict[str, str] = {}
        if cfg.google_oauth.get("client_id"):
            env["GOOGLE_OAUTH_CLIENT_ID"] = cfg.google_oauth["client_id"]
        if cfg.google_oauth.get("client_secret"):
            env["GOOGLE_OAUTH_CLIENT_SECRET"] = cfg.google_oauth["client_secret"]
        if cfg.google_oauth.get("refresh_token"):
            env["GOOGLE_OAUTH_REFRESH_TOKEN"] = cfg.google_oauth["refresh_token"]
        servers["google"] = {
            "type": "stdio",
            # PLACEHOLDER command — the exact CLI-MCP binary is finalized in 0.14.
            "command": "google-workspace-mcp",
            "args": ["--transport", "stdio", "--host", "127.0.0.1"],
            "env": env,
        }

    # --- worker: DeepSeek worker (called as a tool, never the brain model) ----
    # The wrapper is in-repo: agent/mcp/worker (a thin FastMCP stdio server over the
    # DeepSeek REST API, pinned to model `deepseek-chat`). It exposes `summarize` /
    # `draft` for bulk first-draft work; Claude (the brain) always reviews the output.
    # We spawn it with THIS interpreter (`sys.executable -m agent.mcp.worker`) so it
    # uses the same Python/venv as the brain and resolves whether the project is
    # pip-installed (container) or run from the checkout (local dev). The API key is
    # passed via env, never on the cmdline.
    if cfg.deepseek_api_key:
        servers["worker"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.worker", "--transport", "stdio"],
            "env": {
                "DEEPSEEK_API_KEY": cfg.deepseek_api_key,
                # Bill the worker's token cost to the right tenant. The worker writes its
                # OWN cost record to <content_root>/<profile>/costs.jsonl after each call, so
                # cost metering does not depend on the LLM logging it (NIST AU-12). The scoped
                # roots are passed EXPLICITLY, not inherited: a stdio MCP child receives only a
                # safe env allowlist + this dict, so GTM_CONTENT_ROOT/GTM_PROFILES_ROOT set on
                # the CLI subprocess do NOT reach it. Without these, a backend run's
                # per-workspace tree (P3) would not scope the ledger and two tenants sharing a
                # profile name would collide costs.jsonl — cross-tenant billing PII.
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- vision: Haiku vision worker (cheap image→text OCR, never the brain) ---
    # In-repo wrapper agent/mcp/vision: a thin FastMCP stdio server over the Anthropic
    # Messages API, pinned to model `claude-haiku-4-5` (the cheapest vision-capable
    # Claude). The brain calls `extract_text(image_path)` to read a screenshot — e.g.
    # a LinkedIn reactions list — so the expensive brain model never spends vision
    # tokens; it still reasons over and reviews the returned text. Reuses the platform
    # ANTHROPIC_API_KEY (the brain already needs it), passed via env, never on the
    # cmdline. The worker writes its OWN cost record per call (NIST AU-12).
    if cfg.anthropic_api_key:
        servers["vision"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.vision", "--transport", "stdio"],
            "env": {
                "ANTHROPIC_API_KEY": cfg.anthropic_api_key,
                # Scope the worker's costs.jsonl to the run's tree — passed explicitly
                # (stdio MCP children don't inherit these; see the worker block above).
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- gemini_image: Gemini 3 Pro Image worker (headless image path) ----------
    # In-repo: agent/mcp/gemini_image — FastMCP stdio server over the Google
    # Generative Language REST API, pinned to `gemini-3-pro-image-preview` (= Nano
    # Banana Pro). Exposes `generate_image(prompt, output_path, resolution, aspect_ratio)`.
    # Flat per-image billing; cost logged after each successful call (NIST AU-12).
    if cfg.gemini_api_key:
        servers["gemini_image"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.gemini_image", "--transport", "stdio"],
            "env": {
                "GEMINI_API_KEY": cfg.gemini_api_key,
                # Scope the worker's costs.jsonl to the run's tree — passed explicitly
                # (stdio MCP children don't inherit these; see the worker block above).
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- higgsfield_video: Higgsfield DoP Standard worker (headless video path) -
    # In-repo: agent/mcp/higgsfield_video — FastMCP stdio server over the Higgsfield
    # developer REST API (platform.higgsfield.ai), pinned to
    # `higgsfield-ai/dop/standard`. Exposes `generate_video(...)` (non-blocking,
    # returns request_id) and `check_video_status(request_id)`. Both API_KEY and
    # API_SECRET are required; either missing disables this server — fail-closed.
    if cfg.higgsfield_api_key and cfg.higgsfield_api_secret:
        servers["higgsfield_video"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.higgsfield_video", "--transport", "stdio"],
            "env": {
                "HIGGSFIELD_API_KEY": cfg.higgsfield_api_key,
                "HIGGSFIELD_API_SECRET": cfg.higgsfield_api_secret,
                # Scope the worker's costs.jsonl to the run's tree — passed explicitly
                # (stdio MCP children don't inherit these; see the worker block above).
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- tts: ElevenLabs podcast-audio render worker (headless studio→audio) ----
    # In-repo: agent/mcp/tts — FastMCP stdio server over the ElevenLabs TTS REST
    # API. Exposes `render_podcast(script_path, output_path, voice_id)`: strips a
    # podcast-script.md to its spoken prose, chunks it, synthesizes each chunk,
    # concatenates the MP3, and writes one audio file beside the script. Metered
    # per character (NIST AU-12). Operator-gated by UX contract — the brain renders
    # only after the operator approves the script. Gated on the same key the
    # cockpit voice already uses; missing key → server absent (fail-closed).
    if cfg.elevenlabs_api_key:
        servers["tts"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.tts", "--transport", "stdio"],
            "env": {
                "ELEVENLABS_API_KEY": cfg.elevenlabs_api_key,
                "ELEVENLABS_VOICE_ID": cfg.elevenlabs_voice_id,
                # Scope the worker's costs.jsonl to the run's tree — passed explicitly
                # (stdio MCP children don't inherit these; see the worker block above).
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- deck: Slidev/Playwright/Chromium export sidecar (via mcp__deck__export_deck) -
    # In-repo bridge: agent/mcp/deck — FastMCP stdio server that validates paths and
    # proxies to the deck-renderer HTTP sidecar. The sidecar runs node/npm/npx; the
    # brain does not — _DANGEROUS_PROGRAMS hard-deny floor unchanged (§R8/ASI05).
    # The brain composes slides.md (studio); the sidecar renders it against the
    # Default deck-theme baked into its image — no workspace mount, no compose step.
    # Gated on deck_renderer_url so an environment without the sidecar simply omits
    # the tool and the Phase 1 graceful-fail hand-off in the deck skills takes over.
    if cfg.deck_renderer_url:
        servers["deck"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.deck", "--transport", "stdio"],
            "env": {
                "DECK_RENDERER_URL": cfg.deck_renderer_url,
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILE": profile,
            },
        }

    # --- firecrawl: web crawling for onboarding ingest + deep product research ---
    # Also the radar's web-sweep fallback (market-scan) when the news DB is stale/empty —
    # so its reliability is on the radar's critical path. BAKED into the image at a PINNED
    # version (Dockerfile `npm install -g firecrawl-mcp@3.21.0`) and invoked by its installed
    # bin, NOT `npx -y`, for the same cold-start reason as the news server above (the firecrawl
    # npx spawn also hit "Stream closed" on 2026-06-27). Supply chain: pinned, no live fetch
    # (OWASP ASI04 / NIST SR-3,SR-4). Keep this version in sync with plugin/.mcp.json.
    if cfg.firecrawl_api_key:
        servers["firecrawl"] = {
            "type": "stdio",
            "command": "firecrawl-mcp",
            "args": [],
            "env": {"FIRECRAWL_API_KEY": cfg.firecrawl_api_key},
        }

    # --- rocketreach: contact resolution (verified email/phone + person intent) --
    # In-repo wrapper agent/mcp/rocketreach: a thin FastMCP stdio server over the
    # RocketReach REST API (person/lookup). The `prospect` skill calls it to resolve
    # a scored finalist's verified email + direct phone — the enrichment counterpart
    # to Vibe (discovery + company intent). New egress endpoint api.rocketreach.co
    # (documented in SECURITY-SELF-ASSESSMENT.md §DR/egress); the brain never sees the
    # key. Gated on ROCKETREACH_API_KEY — absent ⇒ server omitted (fail-closed) and the
    # skill falls back to Vibe/web. The worker writes its OWN cost record (finite
    # lookup/"export" count) per resolving call (NIST AU-12).
    if cfg.rocketreach_api_key:
        servers["rocketreach"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "agent.mcp.rocketreach", "--transport", "stdio"],
            "env": {
                "ROCKETREACH_API_KEY": cfg.rocketreach_api_key,
                # Scope the worker's costs.jsonl to the run's tree — passed explicitly
                # (stdio MCP children don't inherit these; see the worker block above).
                "GTM_CONTENT_ROOT": str(cfg.content_root),
                "GTM_PROFILES_ROOT": str(cfg.profiles_root),
                "GTM_PROFILE": profile,
            },
        }

    return servers
