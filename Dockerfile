# ─────────────────────────────────────────────────────────────────────────────
# gtm-engine — GTM Content OS runtime image
#
# The "brain" is headless Claude Code driven through the Python claude-agent-sdk.
# The SDK works by spawning the Claude Code CLI as a child process, so this image
# MUST contain BOTH:
#   1. Node.js + the `@anthropic-ai/claude-code` CLI (the thing the SDK spawns), and
#   2. Python 3.11 + this project (the agent/ + cockpit/ packages).
#
# Source-vs-state discipline:
#   - SOURCE  (plugin/, profiles/) is read from the git checkout, bind-mounted
#     READ-ONLY by docker-compose. It is NOT copied into the image so that a
#     `git pull` on the host is the source deploy — no stale baked-in copy.
#   - STATE   (content/<profile>/…) is a READ-WRITE volume, also mounted by compose.
# The image therefore only needs the *code* of this project (pyproject + packages),
# not the mounted source/state trees.
#
# Secrets: NEVER baked in. Doppler injects env at `docker compose up` runtime.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# ----- Build-time knobs ------------------------------------------------------
# Pin Node to the current LTS major. The Agent SDK shells out to the Claude Code
# CLI which is a Node program; LTS keeps it on a supported runtime.
ARG NODE_MAJOR=20
# Pin the CLI so a surprise upstream bump can't change brain behaviour silently.
# Bump deliberately (matches the project's dependency-pinning discipline).
ARG CLAUDE_CODE_VERSION=latest

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Where the bind-mounted source / state trees land inside the container.
    # agent/config.py derives plugin_path/profiles_root/content_root from this.
    APP_HOME=/app \
    # NodeSource globals land here; set early so the smoke-check RUN and runtime
    # both resolve require('docx') without an explicit node_modules lookup.
    NODE_PATH=/usr/lib/node_modules

# ----- OS deps: curl + git (clone/pull awareness) + Node.js LTS + Claude CLI --
# We install Node from NodeSource so we get a real LTS, then install the Claude
# Code CLI globally so `claude` is on PATH for the Agent SDK to spawn.
#
# Document-rendering toolchain (account-dossier Step 5/7, via the docx skill):
#   - libreoffice-writer  → `soffice --convert-to pdf` for .docx → PDF (the docx
#     skill's scripts/office/soffice.py wrapper shells out to it). Writer-only keeps
#     the footprint far smaller than full `libreoffice` on a disk-tight VPS.
#   - poppler-utils        → `pdftoppm` to rasterise the PDF for the page-count check.
#   - `docx` (npm, global)  → docx-js, required by the committed scripts/build_dossier.js
#     renderer. NODE_PATH (set above) makes `require('docx')` resolve the global module.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        git \
        libreoffice-writer \
        poppler-utils; \
    # NodeSource setup for the chosen LTS major
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    # The CLI the Agent SDK spawns + docx-js for the dossier renderer + the stdio MCP
    # servers the brain spawns (news = read-only Postgres, firecrawl = web research).
    # All global. The two MCP servers are BAKED (not fetched by `npx -y` at runtime):
    # in an ephemeral `docker compose run --rm` pipeline container the npm cache is empty,
    # so `npx -y` cold-downloads each server on every spawn; two starting at once blew past
    # the SDK's MCP-init window and surfaced as "Stream closed", silently breaking the news
    # radar (2026-06-27). Pre-installing the pinned versions makes the SDK spawn them from
    # the installed bin instantly with no registry round-trip — and is strictly better
    # supply-chain posture (no live fetch; OWASP ASI04 / NIST SR-3,SR-4). Versions pinned;
    # bump deliberately. agent/mcp_config.py invokes the bins (mcp-server-postgres /
    # firecrawl-mcp) directly, NOT via npx.
    npm install -g \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        docx \
        "@modelcontextprotocol/server-postgres@0.6.2" \
        "firecrawl-mcp@3.21.0"; \
    # Sanity-check the toolchain is actually present at build time (fail fast).
    node --version; \
    npm --version; \
    claude --version || true; \
    soffice --version; \
    pdftoppm -v || true; \
    node -e "require('docx'); console.log('docx-js OK')"; \
    command -v mcp-server-postgres; \
    command -v firecrawl-mcp; \
    # Trim apt + npm caches to keep the image small (VPS is tight on disk).
    npm cache clean --force; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR ${APP_HOME}

# ----- Python project install ------------------------------------------------
# Copy only what's needed to install the project, in cache-friendly order:
# pyproject first (deps layer), then the actual packages.
COPY pyproject.toml ./
# Pre-create the source/state mount points so the bind/volume mounts have a
# target even before compose attaches them (and so a bare `docker run` doesn't
# explode looking for these paths). They are OVERLAID by the compose mounts.
# tests/linter is mounted read-only at runtime (the content linter the studio
# skill gates every asset through).
RUN mkdir -p plugin profiles content tests/linter

# Bring in the runtime packages, then install. `pip install .` reads pyproject
# and pulls claude-agent-sdk + python-telegram-bot. Editable not needed: the
# packages are baked, only plugin/profiles/content are mounted.
COPY agent/ ./agent/
COPY cockpit/ ./cockpit/
COPY gtm_core/ ./gtm_core/
COPY backend/ ./backend/
COPY mcp_server/ ./mcp_server/
RUN pip install .

# ----- Non-root user ---------------------------------------------------------
# Run as an unprivileged user. The content/ volume must be writable by this uid;
# compose/host should chown the named volume to match (documented in the runbook).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin gtm \
    && chown -R gtm:gtm ${APP_HOME}
USER gtm

# Claude Code CLI keeps its config under $HOME; ensure it points at the gtm user.
ENV HOME=/home/gtm

# ----- Healthcheck -----------------------------------------------------------
# Liveness only: confirm the Python runtime + the cockpit package import cleanly.
# A real serving-layer check (Telegram round-trip) is done out-of-band per the
# plan's "verify at the serving layer, not docker ps green" rule.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import cockpit.bot" || exit 1

# ----- Entrypoint ------------------------------------------------------------
# The cockpit (Telegram bot) is the long-running process. It owns the per-chat
# session store and drives the Agent SDK. Cron/headless skill runs use the
# `python -m agent …` CLI instead (invoked ad hoc / by timers, not by CMD).
CMD ["python", "-m", "cockpit.bot"]
