# Hosting the GTM backend (API + MCP server) on a VPS or private cloud

This guide runs the **backend stack** in Docker Compose on a host you control: the FastAPI
service, a queue worker, Postgres, Redis and the MCP server. You need it only if you are
serving your own client application or MCP clients over the network.

> **Optional.** The Claude Code / Cowork / Cursor / Codex use of this repo is serverless and
> needs none of this. For the single-container Telegram pipeline, see [`DEPLOY.md`](DEPLOY.md).
> To try the backend on your laptop first, run `./scripts/stack.sh start` (no credentials, fake
> run execution); everything below is the production-shaped version of that.

> This guide is intentionally **generic**. Provider, hostnames, firewall rules and secret-manager
> wiring for your particular host belong in your own gitignored config, never in a committed file.

---

## What runs

All services are defined in [`deploy/docker-compose.yml`](../deploy/docker-compose.yml).

| Service | What it is | Reachable from |
|---|---|---|
| `api` | FastAPI app (`python -m backend.main`), port 8000. Applies database migrations at startup, then answers `/health`. | compose network only, unless you add an ingress |
| `worker` | Queue worker (`python -m backend.worker`). Claims runs from Postgres and executes them. | nothing |
| `postgres` | Postgres 18, two roles (see below). | compose network only |
| `redis` | Redis 8, cache only (rate-limit counters, run-stream fan-out). No persistence. | compose network only |
| `mcp` | MCP server (`python -m mcp_server`), streamable HTTP, port 8001. | compose network only, unless you add an ingress |
| `cloudflared` | Optional Cloudflare Tunnel sidecar. Off unless you pass `--profile tunnel`. | outbound only |

Nothing is published to a host port by the base file. That is deliberate: you choose exactly one
way in (see [Ingress](#ingress)).

Postgres uses **two roles**. `POSTGRES_USER` (default `gtm_app`) owns the tables and runs
migrations. The API and MCP server connect as a separate non-owner role, `gtm_api`, so row-level
security is actually enforced (an owner or superuser bypasses it). Give them **different**
strong passwords.

---

## Prerequisites

- A Linux host with Docker Engine and the Compose plugin (`docker compose version`). About
  4 vCPU / 8 GB is comfortable; model inference is a remote API, not on the box.
- A DNS name you control for the API (and one for the MCP server if you expose it).
- Something that terminates TLS in front of the stack: a reverse proxy on the host (Caddy, nginx,
  Traefik) or the Cloudflare Tunnel sidecar. The services speak plain HTTP.
- Your own `ANTHROPIC_API_KEY`, plus any connector keys you intend to enable.

---

## 1. Get the code and create the env file

```bash
git clone <your-fork-or-checkout> gtm-engine && cd gtm-engine
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

Edit `deploy/.env`. Compose reads it automatically because `deploy/` is the project directory.
Replace every `CHANGE_ME` value and generate each secret with:

```bash
openssl rand -hex 32
```

The values you must set:

| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | Password of the table-owning role (migrations). |
| `POSTGRES_GTMAPI_PASSWORD` | Password of the runtime role. Different from the one above. Hex only, as it sits inside a connection string. |
| `BACKEND_JWT_SECRET` | Signs access and refresh tokens. |
| `VAULT_KEK` | 64 hex characters. Encrypts the third-party keys a workspace stores. Empty means the vault refuses to store anything (HTTP 503), never a plaintext fallback. **Back it up separately from the database; lose it and every stored key is unreadable.** |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. With `ENV=production` an unset or `*` value stops the API from booting. |
| `ANTHROPIC_API_KEY` | What runs call the model with. |

Everything else is optional and off while empty. Prefer your secret manager over a file on disk
if you have one: export the same variable names into the shell that runs `docker compose`.

---

## 2. Start it

Pick an ingress (next section), then:

```bash
export DC="docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.loopback.yml"
$DC up -d --build
$DC ps
```

The first build takes a few minutes. The API reports healthy only after migrations have
applied, and the worker and MCP server wait for that, so a cold start settles on its own.

```bash
curl -fsS http://127.0.0.1:8000/health
```

---

## Ingress

Use **one** of these.

### Option A: reverse proxy on the same host

[`deploy/docker-compose.loopback.yml`](../deploy/docker-compose.loopback.yml) publishes the API
on `127.0.0.1:8000` and the MCP server on `127.0.0.1:8001`. Nothing else is reachable from the
network until your proxy forwards to it. Binding to loopback also matters because Docker
publishes ports around host firewalls such as `ufw`; a `0.0.0.0` mapping would be public even
with the firewall "closed".

Caddy example (it obtains and renews the certificate itself):

```
api.example.com {
    reverse_proxy 127.0.0.1:8000
}

mcp.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

Proxy only what you intend to expose; skip the MCP block if you do not serve MCP clients.
MCP clients connect to `https://mcp.example.com/mcp` (streamable HTTP).

### Option B: Cloudflare Tunnel

Create a tunnel in the Cloudflare dashboard, point its public hostname at `http://api:8000`
(and a second one at `http://mcp:8001` for MCP), put the token in `CLOUDFLARE_TUNNEL_TOKEN`,
and start with the profile. Do not combine it with the loopback file:

```bash
docker compose -f deploy/docker-compose.yml --profile tunnel up -d --build
```

The tunnel connects outbound, so the host needs no open inbound web ports.

### Rate limiting behind a proxy

The rate limiter buckets clients by the address your proxy forwards (`CF-Connecting-IP`, then
the first `X-Forwarded-For` hop). Anyone who can reach the origin directly could forge those
headers. To close that, set `RATELIMIT_PROXY_SECRET` and have your proxy add the request header
`X-GTM-Proxy-Secret: <same value>`; the limiter then trusts the address headers only on requests
that carry it. Leave it empty only if the proxy is verifiably the sole way in.

### MCP gateway secret

If `MCP_GATEWAY_SECRET` is set, the MCP server rejects any request that does not carry the same
value in an `X-Gateway-Secret` header. That is for a proxy you control that injects it. Leave it
empty when clients connect to the MCP server directly with their own API keys.

---

## 3. Create the first account

Registration is open by default so the first account can be created:

```bash
curl -fsS -X POST https://api.example.com/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a-long-unique-passphrase"}'
```

It returns a token pair and creates the workspace. Once your accounts exist, set
`ALLOW_NATIVE_REGISTRATION=false` in `deploy/.env` and apply it (see
[Applying config changes](#applying-config-changes)) so an internet-reachable API does not accept
strangers. If you sign users in through your own identity provider, configure `TRUSTED_ISSUERS`
instead and keep native registration off.

---

## Data and backups

State lives in named volumes. Back these up; everything else is rebuildable from the repo.

| Volume | Holds | Back up |
|---|---|---|
| `pgdata` | The database: users, workspaces, run queue and event log, wallets. | Yes, with `pg_dump` (below), not by copying files from a running container. |
| `content` | Per-profile ledgers and run records. | Yes. |
| `profiles` | Profile knowledge. | Yes. |
| `workspaces` | Per-workspace trees. | Yes. |
| Redis | Rate-limit counters only. | No. |

Compose prefixes volume names with the project name (`deploy_pgdata`, ...); `docker volume ls`
shows them.

```bash
# database (custom format) and the cluster-level roles it depends on
$DC exec -T postgres pg_dump -U gtm_app -Fc gtm_backend > "gtm-$(date +%F).dump"
$DC exec -T postgres pg_dumpall -U gtm_app --globals-only > "gtm-globals-$(date +%F).sql"

# a file volume
docker run --rm -v deploy_workspaces:/v:ro -v "$PWD":/b alpine \
  tar czf /b/workspaces-$(date +%F).tgz -C /v .
```

A dump of one database does not include roles such as `gtm_api`, which is why the second command
exists. **Rehearse a restore on a scratch host before you depend on it.** Also keep `deploy/.env`
(or your secret manager's copy) and the `VAULT_KEK` somewhere that is not the same disk.

---

## Updating

```bash
git pull
$DC up -d --build
```

Compose recreates only what changed. The API applies new migrations itself at startup before it
reports healthy, so there is no separate migrate step. `docker compose stop` gives the worker up
to 180 seconds to finish in-flight runs, so expect a stop to take a while under load.

### Applying config changes

After editing `deploy/.env` or a compose file:

```bash
$DC up -d --force-recreate
```

`docker compose restart` does **not** re-read the env file or the compose file; it silently
keeps running the old configuration.

### Postgres major versions

The data directory is version-specific. Changing the `postgres:` image tag across a major
version on a volume that already holds data will not start. Upgrade by dumping with the old
version and restoring into a fresh volume on the new one.

---

## Optional hardening (all off by default)

These raise the isolation between workspaces. Read the code comments they point to before
enabling any of them, and test on a non-production host first.

- **`GTM_RUN_ISOLATION=container`** runs each tenant run in its own short-lived container built
  from `GTM_RUNNER_IMAGE`, with the memory, CPU and process limits in `.env`. It needs
  `GTM_DOCKER_HOST` to point at a scoped Docker API proxy that you run; the worker refuses to use
  the raw Docker socket unless you explicitly override it, because handing a process the socket
  is root on the host. See `agent/container_runner.py`.
- **`GTM_TENANT_LAUNCHER=1`** starts each run's CLI as an unprivileged OS user dedicated to that
  workspace, through the small setcap `gtm-launch` binary built into the image (source:
  `deploy/launcher/gtm_launch.c`, whose header explains exactly what it can and cannot do). The
  API refuses to boot with it on if the box cannot actually launch tenant runs.
- **Resource ceilings** (`GTM_RLIMIT_*`, `GTM_CONTAINER_*`, `GTM_WORKER_MAX_CONCURRENT_RUNS`)
  bound what one run can consume.

The backend never publishes anything on its own: posting, reply-sending and email enrollment sit
behind the human approval gates described in [`RULES.md`](RULES.md), and their connector switches
(`HERMES_PUBLISH_ENABLED`, `HERMES_SCHEDULE_ENABLED`, `REPLY_SEND_ENABLED`) default to `false`.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `api` never becomes healthy; log says `CORS_ORIGINS must list explicit origins` | `ENV=production` and `CORS_ORIGINS` is unset or `*`. List your real origins. |
| `docker compose` refuses with `set POSTGRES_PASSWORD in deploy/.env` | `POSTGRES_PASSWORD`, `POSTGRES_GTMAPI_PASSWORD` and `BACKEND_JWT_SECRET` are required and checked on every command. Fill them in `deploy/.env`. |
| API log says a volume is not writable and prints a `docker run ... chown` command | The volume was created by a root-owned container. Run the printed one-time `chown` (the image runs as uid 10001). |
| API exits at boot with `BACKEND_WORKERS` above 1 | More than one uvicorn worker needs a reachable Redis. Check the `redis` service and `REDIS_URL`. |
| Saving a workspace key returns HTTP 503 | `VAULT_KEK` is empty. Set it and recreate. |
| A change to `.env` has no effect | You used `restart`. Use `up -d --force-recreate`. |
| Postgres will not start after an image bump | A major-version change on an existing volume; see [Postgres major versions](#postgres-major-versions). |
| Everything is up but the public hostname gives 502 | The proxy cannot reach `127.0.0.1:8000`. Check that you included the loopback file and that `$DC ps` shows the port mapping. |

Logs: `$DC logs -f api worker`.

---

## What this does not cover

Host patching, the firewall, TLS certificate lifecycle if you do not use Caddy or a tunnel,
monitoring and alerting, and log retention are yours to run. The compose file gives you a sound
default shape (no published database or cache, a non-owner runtime role, secrets from the
environment, an explicit CORS allow-list in production); it is not a managed service.
