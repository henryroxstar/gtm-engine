# Self-hosting gtm-engine (autonomous pipeline)

This guide covers running the **full content pipeline** as long-lived services with a
Telegram cockpit for approvals. **None of this is required for the Claude Code / Cowork
use case** — for that, see the README "Getting started" section (just `ANTHROPIC_API_KEY`).

> This document is intentionally **generic**. Deployment specifics for a particular host
> (provider, hostnames, networks, secret-manager wiring) belong in your own gitignored
> config — `docker-compose.override.yml`, a private runbook, or your secret manager —
> never in a committed file.

---

## What it runs

One long-lived service (see [`docker-compose.yml`](../docker-compose.yml)):

- **`gtm-agent`** — the Claude brain (Agent SDK) + the Telegram cockpit (`cockpit/bot.py`).

An optional **`deck-renderer`** sidecar (Slidev/Playwright/Chromium) handles headless deck export.

---

## Prerequisites

- A small always-on Linux host (≈4 vCPU / 8 GB is enough — inference stays off the box;
  Claude and other model calls are remote APIs).
- Docker + Docker Compose.
- A secret manager (or a gitignored `.env`) to inject the variables below. **Secrets are
  never committed** — `.env.example` holds only generic placeholders.
- Your own credentials for whatever tiers you enable (see `.env.example`):
  - **TIER 0:** `ANTHROPIC_API_KEY` (required).
  - **TIER 1:** `DEEPSEEK_API_KEY`, `FIRECRAWL_API_KEY` (optional; fallbacks exist).
  - **TIER 2:** Telegram bot, news DB, publish webhook, media keys, Google Workspace.

---

## Source vs. state discipline (the #1 deploy trap)

- **SOURCE** (`./plugin`, `./profiles`, `./docs`, `./tests/linter`, `./.git`) is bind-mounted
  **read-only** from the git checkout. `git pull` on the host **is** the source deploy — there
  is no baked or seed-once copy to go stale.
- **STATE** (`./content`) is the **only** writable mount: per-profile ledgers / runs. It is
  gitignored and lives on the host.

One-time host prep (the container runs as non-root uid 10001):

```bash
mkdir -p content && sudo chown -R 10001:10001 content
```

---

## Configuration & secrets

The committed `docker-compose.yml` is **generic and self-contained** — it does not assume any
particular external network or database host. Provide secrets one of two ways:

1. **Sourced env file** (simplest):
   ```bash
   cp .env.example .env        # gitignored — fill in your real values
   set -a; source .env; set +a
   docker compose up -d --build
   ```
2. **A secret manager** that injects the variables into the `docker compose` process
   environment (the compose file substitutes them via `${VAR}` refs).

### Host-specific overrides (keep your real infra out of git)

Anything specific to *your* deployment — attaching to an existing database network, a real
`NEWS_DB_DSN`, extra mounts — goes in a **gitignored** [`docker-compose.override.yml`](../docker-compose.override.yml.example),
which Docker Compose auto-merges on top of the committed base. Start from the committed
`docker-compose.override.yml.example` template, copy it to `docker-compose.override.yml`, and
fill in your real values. This keeps the committed compose file clean and shareable while your
host details stay private.

```bash
cp docker-compose.override.yml.example docker-compose.override.yml   # then edit; it is gitignored
```

### Compose ops gotchas

- `docker compose restart` does **not** re-read the compose file or env. To pick up changes:
  ```bash
  docker compose up -d --no-deps --force-recreate gtm-agent
  ```
- Every invocation needs the env present (sourced `.env` or your secret manager), or all
  `${VAR}` resolve to empty strings and the agent starts broken.

---

## The two permanent human gates

Nothing external happens without an explicit operator approval:

- **Gate 1 (plan):** you approve the week's content plan.
- **Gate 2 (publish):** the agent emits the exact post inside a `⟦GATE:publish⟧` block; the
  cockpit re-displays it and fires the call **only** after you press *Approve & publish*.

`autopublish: false` everywhere.

---

## LinkedIn publish gate (Gate 2)

The `content-publish` skill can post a **LinkedIn text post** (with optional `https` media URLs)
to **one pre-authorized account** — the only outbound-posting capability the engine has. It is
least-privilege by construction:

- **The engine never holds the upstream posting key and never calls the posting API directly.**
  It POSTs to one dedicated webhook (`HERMES_PUBLISH_URL`) that is **write-only** with the target
  account **pinned server-side**.
- **The request body is exactly `{ "post": "<text>", "media_urls": ["<https…>"] }`** — no account
  id, no route, no user id. `agent/publish.py::build_payload` is the single payload builder and
  has no destination parameter (even a prompt-injected agent can't add one). That absence *is* the
  security boundary.
- **The brain never makes the HTTP call and never sees the secret.** Approval is bound to a content
  hash — approving one draft cannot publish a different one.

**Guards** (all in `agent/publish.py`, unit-tested in `tests/agent/test_publish.py`): kill switch
(`HERMES_PUBLISH_ENABLED`, default **off**) · https-only endpoint + media · 10s timeout, no retry ·
idempotency (a content hash publishes at most once) · client-side rate limit (default 5/hour).

| Var | Meaning |
|---|---|
| `HERMES_PUBLISH_URL` | the dedicated, account-pinned webhook (`https://…`) |
| `HERMES_PUBLISH_SECRET` | a **dedicated** bearer token (`openssl rand -hex 32`) — not a shared secret |
| `HERMES_PUBLISH_ENABLED` | kill switch; `false` (default) makes the capability inert |
| `HERMES_PUBLISH_MAX_PER_HOUR` / `HERMES_PUBLISH_MAX_CHARS` | optional caps (default 5 / 3000) |

---

## Capacity note

It fits on a modest box because **inference stays off the host** (model calls are remote APIs).
Before loading the full system, prune the Docker builder cache and watch swap — if swap climbs
off zero under load, move to a larger instance.

---

See [`PENDING.md`](../PENDING.md) for live deploy blockers and open work, and
[`docs/SECURITY-SELF-ASSESSMENT.md`](SECURITY-SELF-ASSESSMENT.md) for the threat model.
