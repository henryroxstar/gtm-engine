# GTM Engine

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg)](https://www.python.org/)
[![Built with Claude Agent SDK](https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg)](https://docs.anthropic.com/en/api/agent-sdk/overview)
[![MCP-first](https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg)](https://modelcontextprotocol.io/)

**GTM Engine is a tool that makes sales, pre-sales, and marketing teams faster and more effective at
their day jobs** — cold prospecting, call prep, account plans, decks, market scans, and on-brand,
multi-platform content (e.g. LinkedIn post, blog article, podcast, pictures) — driven by real news
signal and kept safe behind permanent human approval gates.

**It runs in two modes:**

1. **Claude Cowork (default).** Download this repo and open the folder in **Claude Cowork**. Run ad
   hoc prompts — `"run my prospecting"`, `"prep me for my call with [company]"` — directly in chat.
   Everything runs **locally on your machine**, with optional third-party connectors (see
   [Tools & keys](#tools--keys)). This is what most people want.
2. **Advanced — self-hosted AI agent.** Deploy an autonomous AI agent (e.g. **Hermes**) — locally or
   on your own **virtual private server (VPS)** — that runs the workflow graph on your behalf, 24/7,
   unattended, pausing only at the two human approval gates.

See [Two ways to run](#two-ways-to-run) for the full comparison.

> **Reading the code?** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the technical companion to
> this README — the layering, the four runtimes, the data contracts, and the invariants the system
> is built to hold.

---

## How it works

GTM Engine runs GTM work as a **workflow graph**: the *engine* executes it, a *pack* defines it, and
your *profile* feeds it. That three-layer split is the whole design — a shared, domain-agnostic
engine, declarative domain workflows on top, and your company data underneath.

```
  THE DEFAULT WORKFLOW GRAPH   (the "marketing / linkedin-post" pack)

      radar ──▶ plan ──▶ research ──▶ studio ──▶ publish
                  ▲                                  ▲
               Gate 1                             Gate 2
          you approve the plan         you approve the exact bytes


  HOW EACH NODE RUNS

      your profile ──▶ [ Claude — the brain ] ──▶ [ MCP servers — the hands ]
     (brand·ICP·voice)   plans · reviews             the ONLY path outside
                         every node                  (web · scrape · render · publish)
```

**The three layers:**

- **Engine** *(domain-agnostic — all governance lives here).* Executes any graph: it walks the
  *frontier* of runnable nodes (a node runs once its dependencies are done), so a graph resumes from
  failure and can fan out in parallel. The engine — not any workflow — owns the two human gates, the
  per-node budget cap, the model registry, MCP egress, and the audit ledgers.
- **Pack** *(declarative workflow — data, not code).* A graph of **nodes** (`depends_on` edges, an
  optional gate, a model role each) wiring together shared **skills** into one domain workflow.
  Four ship in-repo — **planning · marketing · prospecting · solution-architecture** — all on the
  *same* unmodified engine. A pack references skills; it never owns them, so `build-deck` can appear in
  several packs at once. Pack graphs are versioned config: they name *which* skills run in *what*
  order under *which* gates — they **cannot** name a destination, an egress path, or a credential.
- **Tenant** *(your data — never logic).* Your profile: settings in `PROFILE.md` and a knowledge
  corpus in `knowledge/` (the "second brain"). A tenant can make a workflow **stricter** — add a
  node or turn on a gate — but can **never** remove a safety gate. That's enforced at load, by
  construction, not by trusting intent.

### Why it's built this way

1. **Claude is the brain; MCP servers are the only hands.** The agent never makes a raw HTTP call —
   every scrape, lookup, render, and publish goes through an MCP tool. *Why:* least privilege by
   construction. Credentials live with the tools, not in the model's context, so a bad instruction
   in a scraped page can't exfiltrate a key or reach an endpoint the tool surface doesn't expose.
2. **Everything is profile-driven — zero hardcoded company facts.** Brand, ICP, personas, voice,
   markets, and budget all load from the *active profile* at runtime (CI-gated: no company strings
   in code). *Why:* one engine serves many companies, and the highest-risk error in GTM automation
   — *right content, wrong company* — becomes structurally impossible to make silently.
3. **Two human gates are permanent; nothing publishes itself.** Every workflow pauses at **Gate 1**
   (approve the plan) and **Gate 2** (approve the exact bytes before they go out); `autopublish` is
   `false` everywhere, and the publish destination is pinned server-side where the agent can't touch
   it. *Why:* GTM output carries your name and your customers' data — a human always approves the
   exact text.
4. **Tenants are isolated by construction.** Each company is a separate profile with its own state,
   ledgers, and customer data; the path-resolution spine binds every read and write to the active
   tenant, so one company can never see another's prospects, drafts, or spend. *Why:* the same engine
   serves many companies without their data ever mixing.
5. **New workflows are data, not code.** Adding a workflow means writing a pack — a graph of nodes
   wiring existing skills — which the engine validates and runs unchanged; no engine edits, no new
   deploy. *Why:* the domain logic you'll change most often lives in reviewable, versioned config,
   while the governance you must never break stays fixed in the engine.
6. **Onboarding your knowledge is a first-class step.** Say `"set me up"` and the engine ingests your
   materials — docs, URLs, notes — condensing them into a structured "second brain" (company, ICP,
   voice, case studies) that every skill retrieves from; a readiness check flags what's missing or
   stale before a run leans on it. *Why:* good GTM output needs your real context, and keeping that
   context fresh shouldn't be manual bookkeeping.

**In advanced mode** (the self-hosted agent), a cheap worker model (DeepSeek) handles bulk first
drafts to keep costs down, but **Claude always reviews** anything before it's shown or shipped, and
every gate-critical or PII-handling node stays on Claude by construction. Model choice resolves
through a committed registry (`gtm_core/models.toml`). Cowork mode always talks to Claude directly —
this tiering only applies when a self-hosted agent is running the workflow unattended.

---

## The packs (what it does out of the box)

Four packs ship in-repo, each a wired **workflow graph** on the same engine — a pack is just which
skills run, in what order, under which gates. Marketing, prospecting, and solution-architecture are
sequential chains; planning is a **batch** of three independent nodes that run side by side, proving
a pack is a *graph*, not necessarily a pipeline. All 31 skills are shared, so a pack composes
existing skills rather than owning them.

### Planning — set your GTM direction before you execute
Runs three independent planning deliverables (no forced order between them): a **quarterly GTM plan**
(market focus, ICP weighting, targets), a **strategic account plan** for a target company
(buying-committee map, entry strategy, matched proof stories, a 5-step action plan with owners and
dates), and **event planning** — a weekly scan of conferences/meetups in your category, filtered by
geography, with per-event cost computed against your profile's **travel policy** (an optional field —
event planning runs fine
without one; it just skips the cost check). Documents/spreadsheets only — no external gate, since
nothing is published or sent.

### Marketing — news signal → on-brand content
Turns a real "why now" signal into on-brand content for a channel: **LinkedIn post, long-form blog,
X thread, podcast, or builder-in-public update**. The studio step picks the asset format per run —
plain text, carousel, data infographic, or handwritten-style infographic — so one workflow covers
many formats. Both gates apply: you approve the plan (Gate 1), then the exact bytes before anything
publishes (Gate 2).

### Prospecting — reach the right prospect, at the right time, with the right message
**Sources, enriches, and scores leads** so your outreach lands where it should — every account
scored against **your company's ideal customer profile**, not a generic list. Discovery,
firmographics, and company-level buyer-intent come from Vibe Prospecting; **verified** contact
email/phone, news & hiring triggers, and job-change timing come from RocketReach — fused into a
"why now" heat signal (free web search is the fallback when neither is connected). The run outputs a
scored brief, contact-ready outreach packs, and a HubSpot-ready CSV; **email drafts follow
best-practice sequence structure** (a real signal as the hook, a matched case study, a clear single
ask) and cite only public signals — intent data times the touch, it never appears in the copy.

### Solution architecture — use case → technical solution (for pre-sales / SA)
Turns a use case into a technical solution across four steps — **discovery question bank → solution
design → setup runbook → deck** — either mapped onto your flagship product (product-led) or
synthesised as a bespoke custom build. It profiles the account's stack, produces architecture
diagrams and a design doc, and hands off to the deck or Word skills. Produces documents only — no
external gate, since nothing is published.

---

## Content craft — the details that make output land

An AI that writes "on-brand" text is table stakes. What actually determines whether content gets
opened, read, and shared is a set of specific, opinionated techniques — refined over real usage and
enforced as hard gates, not just prompted for and hoped:

- **Virality engineering — write for what's *felt*, not just what's useful.** Every post is composed
  against an explicit **emotional-trigger system** ([`docs/virality-engineering.md`](docs/virality-engineering.md)):
  six triggers (identity validation, status signal, tribal belonging, productive discomfort,
  curiosity gap, aspiration) that are **stacked, not checklisted** — a single trigger fired alone is
  mild; two or three combined compound. The feed rewards engagement velocity, and velocity comes from
  emotional intensity, so the system optimizes toward the deeper actions (a reply and a share beat a
  like) and every draft must pass a **felt test** — "useful but not felt" gets rewritten — before it
  ships. It's deliberately **B2B-recalibrated**: productive discomfort and aspiration over shock
  value, tribal lines drawn on *how well you do the work* (never against a named competitor), and
  every aspirational claim paired with real proof from the research. *Why it matters:* this is the
  thing almost no AI writing tool does — most optimize for *informative*, which is exactly why it
  scrolls past; engineering the *feeling* is what actually earns reach, and doing it on a B2B buyer
  without sounding like a hype-merchant is the hard part we've tuned for.
- **Hook optimization.** Every opening line is drawn from a named library of **9 hook archetypes**
  (a shipped artifact, a counterintuitive decision, a named number, a status-quo fault-line, and
  others) — never a generic template — and must be **zero-context self-contained** and traceable to a
  real fact in that run's research. At the plan gate you're offered **3 candidates from 3 different
  archetypes**, so you're choosing the angle, not just approving a single draft. *Why it matters:*
  most AI content reads the same because it starts from a generic prompt instead of a considered
  rhetorical structure — the hook is what earns the first three seconds.
- **Content quality & structure enforcement.** Every draft is checked by an automated linter against
  exact, per-format rules **before you ever see it** — a LinkedIn post needs a ≤140-character hook and
  a 1,300–2,500-character body; an X thread needs 5–9 tweets with the first standing alone, no link;
  a carousel needs 8–12 slides at ≤50 words each with a re-hook partway through. *Why it matters:*
  this isn't a style suggestion the model might follow — it's a hard gate a malformed draft fails
  before it reaches you, so "looks fine" is a floor, not a hope.
- **News hijacking, done safely.** The content radar scores every real news item, then layers two
  judgment calls on top that never distort the underlying score: a **fault-line** check (does this
  story have a genuine, arguable angle — never naming a competitor, and skipped outright if the angle
  can't be taken safely, e.g. a tragedy) and a **velocity** check (is attention still rising, or
  already peaked — a peaked story is down-weighted even with a high base score). *Why it matters:*
  reacting to news is where brands either look tone-deaf or three days late — timing the "why now"
  correctly is most of the reason a content radar exists at all.
- **Platform optimization, not reformatting.** Each platform gets its own shape from one shared
  research pack — you never re-research per channel, only re-shape for it: LinkedIn text runs
  1,300–2,500 characters with the link moved to the first comment; an X thread opens with a
  stand-alone tweet; a Facebook post caps near 480 characters before the fold; an Instagram reel
  scripts its hook for the first 1–2 seconds. *Why it matters:* what earns reach on LinkedIn actively
  hurts it on X — shrinking one draft to fit every channel is one of the most common GTM content
  mistakes.
- **Best-practice outreach sequencing (prospecting).** Covered above — email drafts follow
  proven sequence structure (a real signal as the hook, a matched case study, one clear ask) rather
  than a generic cold-email template.

---

## Two ways to run

**1 · Cowork mode (default — no infrastructure).**
Download this repo and open the folder in **Claude Cowork**, then say `"set me up"`. All 32 GTM
skills run locally, against your profile, in your voice, driven by ad hoc prompts you type turn by
turn. No VPS, no Docker, no database, and no standing agent — you're the one calling each skill. This
is what most people want. → [Getting started](#getting-started-cowork-mode)

**2 · Advanced mode — self-hosted AI agent.**
Deploy an autonomous AI agent (e.g. **Hermes**) — locally or on your own **VPS** — that runs the
workflow graph on your behalf: it works news → plan → research → studio → publish 24/7 as
containerized services, pausing only at the two human approval gates in Telegram. Needs Docker and a
secret manager. → [`docs/DEPLOY.md`](docs/DEPLOY.md)

Mode 1 runs entirely on your machine and never talks to a deployed server — you drive every run.
Mode 2 is an independent self-hosting path where an agent drives the run unattended, on your behalf.

---

## Getting started (Cowork mode)

**Prerequisites:** Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). In Cowork the agent installs
these for you in Step 1 — you don't run anything by hand.

**Step 1 — Bootstrap the engine and your profile.**
Say `"set me up"`. The `setup` skill runs `bash scripts/bootstrap.sh` (installs `uv` if missing →
`uv sync` → environment self-check), then interviews you and scaffolds your company profile from the
`_template` bundle. *(Manual equivalent: `bash scripts/bootstrap.sh`.)*

**Step 2 — Configure your tools and keys (required — don't skip).**
`"set me up"` scaffolds your profile but it **does not add your API keys for you** — that's a manual
step, and it's where most of the value comes from. Decide which tools your work needs (see
[Tools & keys](#tools--keys) for what each one powers and why), then connect them:

- **Metered data connectors** (Vibe Prospecting, RocketReach) — connect the OAuth connector or set
  the key when `setup` prompts you. These unlock real ICP discovery and *verified* contact
  email/phone; without them, prospecting falls back to unverified public web search.
- **Environment-variable keys** (`FIRECRAWL_API_KEY`, `DEEPSEEK_API_KEY`, and `ANTHROPIC_API_KEY`
  *only* if you'll run the pipeline programmatically) — copy `.env.example` → `.env` and fill in the
  ones you want. The repo config references only placeholders; **no key is ever written into it.**
- **Set your budget caps.** `setup` records a monthly and per-run cap so a metered tool can never
  quietly overspend — every paid call is checked against the cap *before* it runs.

Verify everything resolved with `uv run python -m gtm_core.check_env`.

**Step 3 — Use the skills.**
`"run my prospecting"` · `"prep me for my call with [company]"` · `"run a market scan"` ·
`"build an account plan for [company]"` · `"run the content radar"` ·
`"draft my LinkedIn post about [topic]"` · `"open the command deck"`.

> **What works with just a Claude plan:** all 31 skills run on your Claude subscription alone (Cowork
> auth — no `ANTHROPIC_API_KEY` needed). Every external tool is *optional with a keyless fallback*,
> but Step 2 is what turns "runs" into "runs well" — connect the tools your skills actually depend on.

---

## Tools & keys

Every external tool is optional and falls back to keyless web search — but connecting the ones your
work depends on is what makes the output strong. Setup handles the connection; nothing is hardcoded.

| Tool | Powers | What it needs | Needed for | If you skip it |
|---|---|---|---|---|
| **Claude plan** | the brain — orchestration, judgement, review, all 31 skills | your Claude subscription (Cowork auth) | **Option 1** (Cowork mode) | — required for Option 1 |
| `ANTHROPIC_API_KEY` | the self-hosted agent's headless pipeline runs | API key in `.env` | **Option 2** (advanced mode) | not needed for Cowork mode |
| **Vibe Prospecting** | cold ICP company discovery, firmographics, company-level buyer-intent + events (`prospect`, `market-scan`, `events-tracker`) | OAuth connector (credit packs) — no key stored | Both | web search discovers instead |
| **RocketReach** | verified contact email/phone, news & hiring triggers, job-change timing, company intent (`prospect`, `call-prep`, `draft-outreach`) | `ROCKETREACH_API_KEY` (Doppler-injected; never in a file) | Both | Vibe enrichment → public web (unverified) |
| **Firecrawl** | structured, JS-rendered web scraping (`content-radar`, `deck-research`, `events-tracker`) | `FIRECRAWL_API_KEY` | Both | built-in web tools |
| **DeepSeek** | cheap bulk first drafts (Claude always reviews) | `DEEPSEEK_API_KEY` | **Option 2** (advanced mode) | a Claude worker drafts instead |
| **Media** (image · video · voice) | headless carousels, video, podcast TTS | Gemini · Higgsfield · ElevenLabs keys | **Option 2** (advanced mode) | not used in Cowork mode |

Metered skills **estimate cost and check your cap before every paid call** — see
[`plugin/skills/prospect/references/discovery-and-budget.md`](plugin/skills/prospect/references/discovery-and-budget.md)
for the prospecting budget model.

---

## GTM skill suite (31 skills — all profile-driven)

Every skill is **company and product agnostic** — brand, voice, ICP, markets, and product all load
from the active profile bundle. Zero hardcoded company strings (CI-gated by `debrand_check.sh`).

| Category | Skills |
|---|---|
| **Prospecting** | `prospect`, `market-scan`, `events-tracker`, `draft-outreach` |
| **Account & call prep** | `call-prep`, `account-plan`, `account-dossier`, `deck-research`, `build-deck` |
| **Content pipeline** | `content-radar`, `content-plan`, `content-research`, `content-studio`, `content-publish` |
| **Engagement** | `linkedin-engagers`, `linkedin-reply` |
| **Carousels & infographics** | `carousel-pdf`, `carousel-visuals`, `carousel-auto`, `infographic-data`, `infographic-handwritten` |
| **GTM planning** | `gtm-planning`, `solution-discovery`, `solution-design`, `gateway-runbook` |
| **Risk assessment** | `airq-scan` |
| **Founder journey** | `builder-radar`, `builder-evidence`, `builder-studio` |
| **Operations** | `setup`, `profile-onboard` |

Switch profiles to run any skill for a different company: `"switch to <profile>"` → all skills now
target that profile's brand, ICP, and product.

---

## Profiles (multi-company)

Each company is a **profile bundle** under `profiles/<slug>/` — the *tenant* layer, pure data:

```
profiles/
  _template/       starting point — copied to create a new company
  <your-company>/  your bundle:
    PROFILE.md     settings — markets, budget caps, cadence, voice toggle, output
    knowledge/     the "second brain" corpus — company · product · icp-personas · voice · case-studies
    products/      per-product knowledge overrides (resolved product-first)
```

The active profile is whatever the agent resolves at runtime. Switch with `"switch to <profile>"` /
`"use my <company> profile"`. Every skill reads brand, product, markets, and voice from the active
profile — never from `plugin/`. Create your own with `"set me up"`.

---

## Repo layout

```
plugin/        gtm-engine plugin — 31 skills, loaded by the Agent SDK
  skills/      one folder per skill; each has SKILL.md + references/
  .claude-plugin/plugin.json
profiles/      per-company bundles (the tenant layer — data only)
packs/         declarative workflow graphs (planning · marketing · prospecting · solution-architecture)
gtm_core/      shared engine library (skills, graph runner, packs, profiles, ledgers, check_env)
agent/         Agent SDK app — brain, graph runner, session store, ledgers, publish, radar
cockpit/       Telegram bot + human-gate handlers (self-hosting only)
backend/       FastAPI multi-tenant backend (auth, runs, gate, billing) + schema/ migrations
mcp_server/    MCP server runtime — curated gtm_core tools, API-key auth, metering
deploy/        Docker Compose + tunnel config (self-hosting)
scripts/       ops + dashboard scripts (incl. bootstrap.sh)
schemas/       JSON Schemas for the data contracts
tests/         content linter + contract tests + fixtures
docs/          DEPLOY guide + enforced rules (RULES.md) + content-craft playbooks
.env.example   every variable the system reads, grouped by tier (real .env is gitignored)
```

Runtime state (`content/<profile>/…`, ledgers) is **gitignored** and lives wherever the agent runs.

---

## Self-hosting & publishing (advanced mode)

Everything in this section is **Option 2 / advanced mode** — not needed for Cowork mode. The
autonomous pipeline, Docker Compose stack, secret management, Telegram cockpit, and the LinkedIn
publish gate (Gate 2) are documented in **[`docs/DEPLOY.md`](docs/DEPLOY.md)**.

Security posture (least-privilege tool surface, permanent gates, tenant boundary, publish-gate
design): [`CLAUDE.md`](CLAUDE.md) and [`docs/RULES.md`](docs/RULES.md).

---

## Development

Working in the repo — dev setup, the CI gates your change must pass, and the invariants you must
not break: **[`CONTRIBUTING.md`](CONTRIBUTING.md)**. The enforced Python rules (§R1–§R8, each
CI-gated) live in [`docs/RULES.md`](docs/RULES.md).

CI runs the shell lint gates and `pytest tests/` on pull requests; use `uv run …` for everything
(`uv run pytest tests/ -q`, `uv run ruff check .`) — never bare `python`. Contributions are accepted
under the project's Apache-2.0 license (below).

---

## License

Licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE). Permissive use with an express
patent grant; you may use, modify, and distribute this software provided you retain the copyright
and license notices.
