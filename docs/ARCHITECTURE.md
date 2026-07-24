# Architecture

How GTM Engine is put together, for someone reading the code. The [README](../README.md) is
orientation — start there. This document is the deeper technical companion: the layering, the
runtimes, the data contracts, and the invariants the whole system is built to hold. The enforced
Python rules (§R1–§R8) referenced throughout live in [`docs/RULES.md`](RULES.md).

The one-line version: **the model is the brain, a deterministic Python runner is the skeleton, and
your company data is swapped in at runtime.** Everything below is a consequence of taking those three
things and refusing to let them bleed into each other.

---

## 1. Three layers: engine · pack · tenant

The whole design is one separation, applied strictly.

```
   TENANT      profiles/<you>/         your data — brand, ICP, voice, product knowledge
                     │                 (never logic; CI-gated to hold zero engine code)
   PACK        packs/<domain>/         a workflow as versioned TOML — which skills run,
                     │                 in what order, under which gates (data, not code)
   ENGINE      gtm_core/ + agent/      executes any graph; owns every governance guarantee
                                       (the gates, budget cap, model registry, MCP egress, ledgers)
```

- **Engine** — domain-agnostic. It walks the *frontier* of a workflow graph (a node becomes
  runnable once its dependencies complete), so a run resumes from failure and can fan out in
  parallel. All governance lives here, never in a workflow: the two human gates, the per-node budget
  cap ([§R2](RULES.md)), the model registry, MCP-only egress ([§R6](RULES.md)), and the audit
  ledgers. See [`agent/graph.py`](../agent/graph.py) and [`agent/pipeline.py`](../agent/pipeline.py).
- **Pack** — a graph of **nodes** (`depends_on` edges, an optional gate, one model role each) wiring
  shared **skills** into a domain workflow. A pack *references* skills; it never owns them, so one
  skill appears in several packs. Pack graphs are versioned config — they name *which* skills run in
  *what* order under *which* gates, and they **cannot** name a destination, an egress path, or a
  credential. The loader ([`gtm_core/packs/loader.py`](../gtm_core/packs/loader.py)) is fail-closed:
  an unknown skill, a `depends_on` cycle, an unbounded revision edge, or a gate on anything but the
  publish mechanism is rejected at load, before any run.
- **Tenant** — your `profiles/<name>/` bundle: settings in `PROFILE.md` plus a knowledge corpus in
  `knowledge/`. A tenant can make a workflow **stricter** — add a node, or turn on a gate that was
  off — but can **never** remove a node, drop a prerequisite, or turn a gate off. That is enforced at
  merge time by construction (`gate = base_gate OR override_gate`, an append-only `depends_on`
  union), not by trusting the override's intent, and the merged graph is re-validated as a whole.

Why this shape: the domain logic you change most often lives in reviewable, versioned data, while
the governance you must never break stays fixed in the engine and cannot be edited by a workflow or a
tenant.

---

## 2. One engine, four runtimes

Every skill is written **once** in `gtm_core` as a pure-Python library. Thin runtimes wrap it. A new
skill is available on every surface without touching runtime code.

```
 CLIENTS:    Plugin (local)      self-hosted agent       3rd-party agents        mobile / web app
                  │                     │                      │                       │
 RUNTIMES:   Plugin Runtime        Agent Runtime          MCP Server            Backend API
             (Agent SDK)         (headless SDK +         (curated tools,        (FastAPI, JWT,
                                  a chat cockpit)         API-key auth)          workspace RLS)
                  └──────────────────────┴──────────────────────┴───────────────────────┘
                                              │
 ENGINE:                              gtm_core  (pure Python)
                          skills · pipeline · profiles · schemas · ledgers · capabilities
                                              │
 EXEC PLANE (all via MCP):   web scrape · worker LLM · media render · news store · publish
```

Four hard constraints hold across every runtime:

1. `gtm_core` has **zero** runtime/Agent-SDK imports — runtimes import it, never the reverse. This is
   contract-tested; it's what keeps a skill portable across all four surfaces.
2. Capability-tier enforcement lives at the **runtime boundary**, never inside a skill.
3. `workspace_id` is on **every** entity in the multi-tenant data model.
4. The publish path is identical everywhere: content is *proposed*; a human approves the exact bytes;
   the code calls out. No runtime holds publishing as a capability it can exercise on its own.

The four runtime directories — [`plugin/`](../plugin), [`agent/`](../agent),
[`mcp_server/`](../mcp_server), [`backend/`](../backend) — are all in this repo and all wrap the same
engine.

---

## 3. Control plane vs execution plane

Within the self-hosted Agent Runtime, work splits into two planes:

- **Control plane (the brain — light, reliability-critical).** Headless Claude via the Agent SDK,
  loading the skill plugin. It orchestrates, reasons, reviews, and owns the two gates. A chat cockpit
  ([`cockpit/`](../cockpit)) maps a conversation to a persistent agent session and renders the
  Approve / Edit / Reject buttons at each gate. A scheduler fires cadence runs.
- **Execution plane (the hands — heavy, unattended).** Web scraping, a worker LLM for bulk drafts,
  media rendering, the news store, and the publish service. **The brain never touches these
  directly** — every one sits behind an MCP tool (§5).

The capacity principle that makes a modest VPS (≈4 vCPU / 8 GB) sufficient: **keep inference off the
box.** The orchestrator, the worker model, and TTS are all API calls — no model weights run locally.
The host only orchestrates and does light I/O; the one real spike is media rendering, which is bursty
and schedulable.

---

## 4. The pipeline

The default workflow graph — the marketing `linkedin-post` pack — is a straight chain:

```
   radar ──▶ plan ──▶ research ──▶ studio ──▶ publish
               ▲                                  ▲
            Gate 1                             Gate 2
       approve the plan                approve the exact bytes
```

| Stage | Does | Gate |
|---|---|---|
| **radar** | reads a news/signal store (or a keyless web sweep as fallback), dedupes against the history ledger, clusters into stories, scores each | — |
| **plan** | proposes a themed set of ideas, each tied to a story and a content pillar | **Gate 1** — you steer |
| **research** | per item: primary sources, verifiable facts, quotable stats, and a *claims-to-avoid* list | — |
| **studio** | produces the platform-native asset (text / carousel / infographic / thread), linted before you ever see it | budget gate |
| **publish** | builds the payload and emits it for approval; on approval the code posts and logs the result | **Gate 2** |

The runner is graph-shaped, not hardcoded-linear — the chain above is just the default graph. A
frontier with more than one runnable node (a fan-out, e.g. a batch of independent planning
deliverables) is dispatched concurrently: every node in the batch is marked running and persisted
*before* any of them dispatch, the batch always runs to completion, and it all happens under one
profile-lock acquisition for the run.

The packs that ship — under [`packs/`](../packs) — prove the engine special-cases none of them:
sequential chains (marketing, prospecting, solution-architecture) and a multi-root batch (planning)
all run on the same unmodified runner.

---

## 5. Connectivity: MCP-first

**Every external capability is an MCP tool.** The agent makes no raw HTTP calls and does not "know
about" the backend behind a tool. Where a service does the real work, it sits *behind* an MCP server.

Why it's built this way ([§R6](RULES.md)):

- **One model-native tool interface.** The brain sees a uniform tool surface, not a grab-bag of APIs.
- **Least privilege by construction.** Credentials live with the tools, not in the model's context —
  so a hostile instruction inside a scraped page can't exfiltrate a key or reach an endpoint the tool
  surface doesn't expose. A denied tool call is *by design* ([§R3](RULES.md)): the agent does the
  work another way or surfaces the blocker; it never routes around it with a shell or raw HTTP.
- **Swappable backends.** The service behind a tool can change without touching the agent.
- **Per-tenant credential injection** happens at the tool boundary, so the publish tool only ever
  receives the *active* workspace's credentials.

Heavy or async jobs (render, batch TTS, publish) follow a trigger/callback pattern behind the MCP
tool — the tool kicks off the job and returns a handle; the agent is never blocked on a long render.

---

## 6. Model discipline

Model selection resolves through a committed registry
([`gtm_core/models.toml`](../gtm_core/models.toml) via `resolve_model`) — the single source of truth
for which model each logical *role* uses, replacing hardcoded ids. The registry is non-secret config:
a provider stores the *name* of an env var holding its key, never the key itself, and the resolver
refuses a secret-shaped value.

The invariant is a **per-stage split, not one brain model**:

- **Judgment, the two gates, and all PII-bearing stages run on Claude, by construction.** The brain
  orchestrating any external or publish action is always the strong model.
- **Mechanical stages over untrusted public text** (news intake, research extraction) may run on a
  registry-approved *worker* model, and a cheap worker also does bulk first drafts — but **the brain
  always reviews the worker's output** before anything is shown or shipped. Registry capability flags
  gate request params so a swapped model can't send a param it doesn't support.

A pack cannot place a gate-bearing or PII-bearing node on a worker model — `model_role` resolves
through the registry, and the loader rejects the combination.

---

## 7. Profiles & tenant isolation

The highest-risk error in GTM automation is **right content, wrong company**. Everything about
profiles is built to make that structurally impossible rather than merely unlikely.

- **A profile is a bundle, not a config value.** `profiles/<name>/` holds `PROFILE.md` (settings:
  markets, budget caps, cadence, voice) and `knowledge/` (the second brain: company, product, ICP
  personas, voice, case studies). Per-product knowledge resolves **product-first, profile-fallback**
  through a single helper — skills never hardcode a knowledge path; they read whatever the resolver
  prints. Filenames and product slugs are guarded (bare names only: no `/`, `..`, or NUL) — directory
  traversal here is the highest-risk tenant error.
- **Skills carry zero company facts.** Brand, ICP, voice, markets, and product all load from the
  active profile at runtime. This is CI-gated ([§R5 sibling checks](RULES.md)): a company token
  inside a skill body fails the build.
- **Separate state trees.** The only writable state is the resolved content root for the active
  profile (`content/<name>/`), with its own append-only ledgers. One company's content can never
  bleed into another's plan or feed. The resolver is the source of truth for the path; nothing writes
  outside it.
- **The profile is bound per session.** The agent does not switch it itself — the operator does.

The `_template` profile in this repo is the starting point: copy it, fill in your facts, and every
skill retargets to your brand.

---

## 8. Capability tiers

Each skill declares a `capability_tier`; access is gated at the runtime boundary, never in the skill.

| Tier | Name | Intent |
|---|---|---|
| **0** | Core | available on every runtime |
| **1** | Pipeline | richer data paths; a free runtime auto-runs a degraded fallback |
| **2** | Production | heavier generation (long-form media, render) |

The mechanism matters more than any particular plan mapping: because tiers are enforced at the
boundary and a lower-tier runtime falls back gracefully (e.g. a news-store lookup degrades to a
keyless web sweep), the same skill code runs everywhere and each fallback is a graceful-degradation
path rather than a hard failure. See [`gtm_core/capabilities.py`](../gtm_core/capabilities.py) and
[`gtm_core/tiers.py`](../gtm_core/tiers.py).

---

## 9. Data contracts

Stages hand off through typed contracts, so any stage can run or re-run alone. The JSON Schemas live
in [`schemas/`](../schemas) and are validated in CI.

| Contract | Carries |
|---|---|
| **NewsItem** | a single scored signal — title, url, source, published_at, summary, topics |
| **StoryCluster** | a scored, pillar-tagged cluster of items with angle seeds and platform fit |
| **ContentItem** | one planned asset — pillar, story, platform, format, locale, status, refs |
| **Transcript** / **EpisodeBundle** | long-form production handoffs (script → rendered bundle) |
| **RunManifest** | per-run stage status for resume — `status` includes `awaiting_approval`; `depends_on` carries each stage's graph edges |
| **MetricRecord** | post-publish performance, fed back into the scoring ledgers |

---

## 10. Ledgers — the audit + budget contract

Under `content/<active>/`, append-only:

- **`history.jsonl`** — every coverage / stage / publish event; also the radar dedup source.
- **`costs.jsonl`** — every metered call's cost, checked against the profile's cap **before** any
  paid call ([§R2](RULES.md)). Cost is logged by the code that makes the call, not by the model.
- **`runs/<run_id>.json`** — per-run stage status, for resume-from-failure.

---

## 11. Multi-tenancy & the data spine

For the network-facing Backend runtime, tenant isolation is enforced in **three independent layers**
— defense in depth, so no single bug crosses a tenant boundary:

```
   User → Workspace → Profile → { Session · Ledgers · Content }
              (workspace_id is on every row from day one)
```

1. **Auth** — a bearer token (JWT HS256) resolves `workspace_id`; no request crosses workspaces. See
   [`backend/deps.py`](../backend/deps.py).
2. **Database** — every query runs inside a `workspace_scope()` transaction that sets a
   `SET LOCAL app.current_workspace_id`, and Postgres **row-level security** policies enforce the same
   scope at the DB layer. The two reinforcements are independent: the runtime connects as a
   non-owner role with `FORCE ROW LEVEL SECURITY` on every tenant table, so RLS is not bypassed by an
   owner/superuser, and the runtime pool asserts it is not a superuser at boot. See
   [`backend/database.py`](../backend/database.py).
3. **Filesystem** — a backend run's content and profile roots are pinned to a per-workspace path and
   propagated into the SDK subprocess env, so skills are scoped too (no shared-env race across
   concurrent runs). Each tree is populated only by that workspace's own onboarding.

---

## 12. The network-facing runtimes

### Backend API ([`backend/`](../backend))

A FastAPI service that wraps the same pipeline, over HTTPS reached through an inbound tunnel (the only
inbound path — no host ports are opened). It is the first network-addressable trust surface, and it
relaxes **none** of the invariants above:

- **`permission_mode="default"` + `can_use_tool`, never `bypassPermissions`** ([§R8](RULES.md)). The
  backend session store imports `build_agent_options` from [`agent/session.py`](../agent/session.py)
  — the permission posture is identical to the self-hosted agent.
- **Gate 2 unchanged.** A gate endpoint resolves the `asyncio.Event` that pauses the background run;
  the publish code calls out only after the authenticated user approves the exact pending bytes.
  Destination pinned server-side; `autopublish: false` everywhere ([§R7](RULES.md)).
- **The layer adds zero new egress.** All external I/O still flows through MCP tools.
- **Entitlement is granted by an upstream billing service, not decided here.** A service-authenticated
  route (a service secret, never a user token) syncs a spend cap in; the backend only *stores and
  enforces* it, and reports usage back. A user token reaching that route would be a self-upgrade, so
  it is gated by a distinct service-auth dependency.

### MCP Server ([`mcp_server/`](../mcp_server))

Exposes a curated subset of engine skills as MCP tools for third-party agents. API-key auth per
caller, validated every call; Core tools free, higher tiers metered per call against the caller's
workspace cap. Stateless — an API key resolves to a workspace; nothing is stored locally. The launch
surface is deliberately narrow and holds **no publish tools**.

---

## 13. Security model

The controls above are not incidental — they are the design. In one place:

- **Untrusted content is data, never instructions** ([§R5](RULES.md)). News rows, web fetches,
  scraped pages, and ledger contents are summarized and reasoned over, never obeyed. Anything that
  looks like a command or a gate marker inside them is data to report, not an instruction to follow.
- **All external I/O via MCP** ([§R6](RULES.md)); the agent never sees outbound credentials and never
  makes raw HTTP calls. Secrets come from a secret manager injected as env — never in source, logs,
  or ledgers.
- **Two human gates are permanent** ([§R7](RULES.md)); `autopublish: false` everywhere; the publish
  destination is pinned where the agent can't represent it.
- **Cost cap before every paid call** ([§R2](RULES.md)).
- **Least-privilege tool surface** ([§R8](RULES.md)) — `can_use_tool`, never `bypassPermissions`, on
  every runtime that runs the agent.
- **Tenant isolation by construction** (§7, §11) — the path-resolution spine binds every read and
  write to the active tenant.

The runtime security/tenant contract that holds for every profile and every run is
[`CLAUDE.md`](../CLAUDE.md); the enforced, CI-gated Python rules are [`docs/RULES.md`](RULES.md).

---

## 14. Testing

Correctness is enforced by artifacts, not convention:

- **Content linter** — per-format structural rules (hook length, body char-range, no body links,
  slide counts) that a malformed draft *fails* before it reaches you. "Looks fine" is the floor, not
  the hope.
- **Contract/schema tests** — JSON-Schema validation for every §9 contract, on every PR.
- **Contract tests for the layering** — that `gtm_core` imports no runtime, that a pack is a faithful
  data version of the equivalent hand-wired pipeline (byte-identical prompts/roles), and that the
  engine special-cases no pack.
- **Fixtures / golden paths** — canned inputs through a stage, asserting output shape.
- **Fail-closed loaders** — the pack loader and tenant-override merge reject invalid graphs at load,
  before any run.

Run the same integrity gates CI runs:

```bash
bash tests/lint/skill_codegen_sync.sh
bash tests/lint/debrand_check.sh
bash tests/lint/resolve_check.sh
bash tests/smoke/skill_manifest.sh
uv run pytest -q
```

---

## 15. Repo layout

See the [README](../README.md#repo-layout) for the directory map. The load-bearing distinction: the
[`plugin/`](../plugin) skill suite is CI-gated to hold **zero** company facts,
[`profiles/<yours>/`](../profiles) is the only place tenant data lives, and `content/<yours>/` (a
gitignored runtime tree) is the only writable state.
