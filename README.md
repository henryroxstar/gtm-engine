# GTM Engine

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg)](https://www.python.org/)
[![Built with Claude Agent SDK](https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg)](https://docs.anthropic.com/en/api/agent-sdk/overview)
[![MCP-first](https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg)](https://modelcontextprotocol.io/)

*The open-source Go-To-Market agent harness for B2B software startups. Built to 10x early-stage startups across their sales, pre-sales, and field-marketing activities.*

```
      .-"""-.
     /  o o  \        one brain, many careful hands —
     \   ^   /            you approve every reach
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### You're the founder. You're also the entire Go-To-Market (GTM) team.

You wear every hat, you're running lean, and you're still hunting for product-market fit. So closing
deals was never the whole job. You also have to *build* the pipeline that feeds those deals. You have
to carry the voice of the customer back into the building so the product actually bends toward PMF.
You have to sound fluent about a product that isn't fully built and whose docs went stale two sprints
ago — without dragging an engineer into every call. And every week the market moves, so every week
you're testing new messaging, reading the signals, and shipping content to pull the right buyers
toward you.

That's five jobs. The playbook says hire five people. You have a laptop, a Claude subscription, and
this week.

**gtm-engine is the harness that runs those five jobs with you.** Cold prospecting, call prep, account
plans, decks, market scans, and on-brand multi-platform content (LinkedIn posts, blog articles,
podcasts, images) — all driven from a sentence you type, all in your voice, off your real company
knowledge. And it runs as an AI agent that _structurally cannot_ publish, send, or leak on its own.
It doesn't ask for your trust; it's built so it can't overreach.

Three things never change: nothing sends or publishes without your exact sign-off, each company's
data stays isolated in its own profile, and the agent has no raw HTTP or shell access. Most agent
frameworks ask you to trust broad permissions; this one is built so there's nothing broad to trust.
(The [how and why](#why-its-built-this-way) is spelled out further down.)

**You onboard once.** Say `"set me up"` and point it at your website; it reads your site and drafts
your whole company profile (brand, ICP, voice, competitors, products), so every skill after that
already knows who you are and you never paste your company into a prompt again.

> **Not technical?** You never have to open a terminal or type a command.
> [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) is the same setup written for someone who
> sells rather than ships — install to first output, in plain English.

**Then you run it one of two ways** — **Cowork mode** (the default: open this folder in the Claude
desktop app and type prompts in chat, everything local) or an **advanced self-hosted agent**
(autonomous, 24/7, pausing only at the two human gates). [Two ways to run](#two-ways-to-run) has the
full comparison. But first, the fun part:

> **Reading the code?** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the technical companion to
> this README — the layering, the four runtimes, the data contracts, and the invariants the system
> is built to hold.

---

## See it work

No install command, no config file to fill out first. You open the folder and type one sentence.
Watch what a Monday-morning "I should really post something" turns into:

```
You:     "draft my LinkedIn post about [today's news item]"

Engine:  reads the real story → checks it against your content pillars and where
         your buyers sit in their journey → scores the angle (is it arguable? is
         attention still rising, or already peaked?) → engineers the emotional
         triggers that actually get a post shared → pulls 3 hooks from a library
         of 9 archetypes → drafts in YOUR voice → lints the format before you look

Gate 1:  you pick 1 of the 3 hook angles. Nothing is written until you do.
Gate 2:  you read the exact post and approve. Only then does it go out.
```

Sixty seconds ago you had a blank feed and a nagging to-do. Now you have a post that sounds like you
wrote it on a good day, backed by real research, and you signed off on every word before it left your
machine.

That same one-sentence move runs your whole week. Or run it as a routine schedule. Every run quietly
does the work of an analyst, a researcher, and a copywriter before it ever hands you anything:

```
You:     "find prospects in [market]"
Engine:  discovers ICP-fit accounts → scores each against YOUR rubric for fit and
         against live buyer-intent for timing, not a generic list → runs a dated
         "why now" check on each, drawing on your industry + regulatory knowledge
         → skips accounts you worked recently or whose signal has gone stale →
         resolves the named buyer's verified email and phone.
Result:  a scored brief, a HubSpot CSV, and for each hottest account an outreach
         pack: a pre-drafted 5-touch email + LinkedIn sequence in your voice,
         threading the buying committee, not just one inbox. Nothing sends.
         Buyer-intent times the touch and picks the angle; it never hits the copy.

You:     "prep me for my call with [account]"
Engine:  builds an account + buyer dossier (firmographics, funding, leadership,
         regulatory backdrop, your matched proof stories) → then arms you with
         discovery questions, objection rebuttals, and a closing ask, all sequenced
         by an evidence-based, deal-phase method: SPIN, Gong call data, and
         peer-reviewed research, not sales-guru folklore.
Result:  a five-minute brief that assumes you know nothing going in, so you walk in
         cold and sound like you did the homework. Same method every call, so your
         sales motion gets consistent instead of improvised.

You:     "design the solution for [account]"  /  "build a deck for [account]"
Engine:  turns the use case into a customer-facing solution overview → problem,
         current → target architecture, the V1/V2 cut → then a branded deck.
Result:  documents only; nothing leaves your machine.

You:     "build an email sequence from that outreach pack"
Engine:  reuses the account's dossier facts, borrows your industry angle and
         vocabulary, opens on a real dated signal, matches a proof story by shape,
         and keeps it to one clear ask → then stages the whole cadence PAUSED in
         your sequencer, enrolling only clean, verified leads (Bad/Risky and
         do-not-contact addresses filtered out first).
Result:  steps, A/B variants, and a schedule, all built. It never hits send. You
         flip it live yourself, when you're ready.

You:     "run the voice-of-customer brief"
Engine:  reads the field data every run above already generated → separates what
         the market is asking for from where you're aiming sales.
Result:  the raw material your product team needs to find PMF.
```

Here's the part that compounds: these aren't isolated tricks. The dossier you generate becomes the
context for the email. Your industry and regulatory knowledge feed both. The signal that sparked a
post also times a prospect's outreach. Every skill feeds the next, so the more you run, the sharper
every run gets.

**Five jobs. One person. A sentence at a time.**

---

## Start here

**Three ways in. Pick the one that sounds like you:**

| You are… | Go to | Roughly |
|---|---|---|
| **Not technical** — you sell, you don't ship | [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) — install to first output with **no terminal and no commands**, plus a [Sales FAQ](docs/onboarding/SALES-FAQ.md) | 30 min |
| **Comfortable in a repo** — you'll drive it yourself | [Getting started](#getting-started-cowork-mode), just below | 10 min |
| **Evaluating it** — architecture, control flow, security posture | [How it works](#how-it-works) → [For a technical evaluator](#for-a-technical-evaluator) | 10 min |

**The questions everyone asks first:**

| | |
|---|---|
| **What do I need?** | A Claude subscription. That is the whole requirement — every external tool is optional and falls back to keyless web search |
| **What will it cost me?** | Nothing beyond your Claude plan until *you* connect a metered data provider. You set a monthly and per-run cap during setup, and every paid call is checked against it **before** it runs |
| **Can it email or post without me?** | No — and not as a setting you could flip. Sending and publishing are not in the agent's tool surface at all; a human approves the exact bytes. [Why it's built this way](#why-its-built-this-way) |
| **Do I re-explain my company every time?** | No. You onboard once (`"set me up"`, pointed at your website) and every skill reads that profile from then on |
| **What can it actually do?** | [What it does out of the box](#what-it-does-out-of-the-box) for the workflows, [`docs/SKILLS.md`](docs/SKILLS.md) for the generated, always-current list of every skill |
| **Where does my data live?** | On your machine, in your profile. Runtime state is gitignored and never leaves except through a gate you approve |

**Contents** — [See it work](#see-it-work) · [Two ways to run](#two-ways-to-run) ·
[Getting started](#getting-started-cowork-mode) · [Tools & keys](#tools--keys) ·
[What it does out of the box](#what-it-does-out-of-the-box) ·
[GTM skill suite](#gtm-skill-suite-59-skills--all-profile-driven) ·
[Profiles](#profiles-multi-company) ·
[Content craft](#content-craft--the-details-that-make-output-land) ·
[How it works](#how-it-works) · [Repo layout](#repo-layout) ·
[Self-hosting](#self-hosting--publishing-advanced-mode) · [Development](#development)

---

## Two ways to run

**1 · Cowork mode (default — no infrastructure).**
Download this repo and open the folder in the **Claude desktop app** (Claude Code / Cowork — this
guide calls it *Cowork mode* throughout), then say `"set me up"`. All the GTM skills run locally,
against your profile, in your voice, driven by ad hoc prompts you type turn by turn. No VPS, no
Docker, no database, and no standing agent — you're the one calling each skill. This is what most
people want. → [Getting started](#getting-started-cowork-mode)

**2 · Advanced mode — self-hosted AI agent.**
Deploy an autonomous AI agent (e.g. **Hermes**) — locally or on your own **VPS** — that runs the
workflow graph on your behalf: it works news → plan → research → studio → publish 24/7 as
containerized services, pausing only at the two human approval gates in Telegram. Needs Docker and a
secret manager. → [`docs/DEPLOY.md`](docs/DEPLOY.md)

Mode 1 runs entirely on your machine and never talks to a deployed server — you drive every run.
Mode 2 is an independent self-hosting path where an agent drives the run unattended, on your behalf.

---

## Getting started (Cowork mode)

> **This page assumes you're comfortable in a repo.** If you're not — no terminal, no commands —
> follow [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) instead. Same destination, ~30 minutes
> including the install and handing over your materials.

**Prerequisites:** Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). In Cowork the agent installs
these for you in Step 1 — you don't run anything by hand.

**See onboarding work first** — the `"set me up"` run itself takes about two minutes, mostly the
engine doing the reading. Budget longer end to end: gathering the decks, case studies, and sample
posts you hand it afterwards is what makes the profile good, and that part is on you.

```
You:     "set me up" — here's our site: [yourcompany.com]

Engine:  reads your site and drafts your whole profile — company, ICP & personas,
         voice, competitors, content pillars, products, brand
      →  asks only the handful of things a website can't tell it (budget cap, which
         markets are in focus, sender identity for outreach)
      →  stages the full bundle for you to review — nothing goes live until you say
         so — then promotes it and proves it with a real first output.

From then on, every skill inherits that one profile — you never re-explain your company:
  · "find prospects"        already knows your ICP and scores against it
  · "draft my post"         writes in your voice, off your content pillars
  · "prep me for my call"   pulls your personas and matched proof stories
  · "run the VoC brief"     maps the field back to your Pain → Claim → Gain
```

You hand it your website once; brand, ICP, and voice flow from there into every run — so you're
never pasting your company into a fresh prompt again. The steps below are the same flow, in detail.

**Step 1 — Bootstrap the engine and your profile.**
Say `"set me up"`. The `setup` skill runs `bash scripts/bootstrap.sh` (installs `uv` if missing →
`uv sync` → environment self-check), then interviews you and scaffolds your company profile from the
`_template` bundle. *(Manual equivalent: `bash scripts/bootstrap.sh` — on Windows,
`powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1`, which additionally probes that a
real interpreter is reachable rather than the Store-alias stub that shadows it on PATH.)* Onboarding through a different
surface (self-hosted Telegram, or the API)? See
[`docs/onboarding-surfaces.md`](docs/onboarding-surfaces.md) for what's identical and what differs.

**Step 2 — Configure your tools and keys (required — don't skip).**
`"set me up"` scaffolds your profile but it **does not add your API keys for you** — that's a manual
step, and it's where most of the value comes from. Decide which tools your work needs (see
[Tools & keys](#tools--keys) for what each one powers and why), then connect them:

- **Metered data connectors** (Vibe Prospecting, RocketReach, Apollo) — connect the OAuth connector or
  set the key when `setup` prompts you. These unlock real ICP discovery and *verified* contact
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
`"draft my LinkedIn post about [topic]"`.

> **Running outbound?** The step-by-step procedure lives in the skill itself —
> [`plugin/skills/prospect/SKILL.md`](plugin/skills/prospect/SKILL.md) — with the discovery
> filters, credit model, and budget maths in
> [`references/discovery-and-budget.md`](plugin/skills/prospect/references/discovery-and-budget.md).
> The narrative operator's guide that ships with the private build is **not** part of this
> distribution: it cites internal backlog and retrospective docs that are deliberately excluded,
> so shipping it would mean shipping a guide whose links all dead-end.

> **What works with just a Claude plan:** every skill runs on your Claude subscription alone (Cowork
> auth — no `ANTHROPIC_API_KEY` needed). Every external tool is *optional with a keyless fallback*,
> but Step 2 is what turns "runs" into "runs well" — connect the tools your skills actually depend on.

---

## Tools & keys

Every external tool is optional and falls back to keyless web search — but connecting the ones your
work depends on is what makes the output strong. Setup handles the connection; nothing is hardcoded.

| Tool | Powers | What it needs | Needed for | If you skip it |
|---|---|---|---|---|
| **Claude plan** | the brain — orchestration, judgement, review, all skills | your Claude subscription (Cowork auth) | **Mode 1** (Cowork) | — required for Mode 1 |
| `ANTHROPIC_API_KEY` | the self-hosted agent's headless pipeline runs | API key in `.env` | **Mode 2** (advanced) | not needed for Cowork mode |
| **Vibe Prospecting** | cold ICP company discovery, firmographics, company-level buyer-intent + events (`prospect`, `market-scan`, `events-tracker`) | OAuth connector (credit packs) — no key stored | Both | web search discovers instead |
| **RocketReach** | verified contact email/phone, news & hiring triggers, job-change timing, company intent (`prospect`, `call-prep`, `draft-outreach`) | `ROCKETREACH_API_KEY` (Doppler-injected; never in a file) | Both | Vibe enrichment → Apollo → public web (unverified) |
| **Apollo** | last-resort contact backstop (verified email, never phone), company buying-intent, job-posting signals (`prospect`) | OAuth connector (or `APOLLO_API_KEY` for the local tool). **Needs a PAID Apollo plan** — you can connect on free, but Apollo returns `API_INACCESSIBLE` for every data endpoint until you upgrade (verified 2026-07-27) | Both | falls back to public web (unverified) |
| **Firecrawl** | structured, JS-rendered web scraping (`content-radar`, `deck-research`, `events-tracker`) | `FIRECRAWL_API_KEY` | Both | built-in web tools |
| **DeepSeek** | cheap bulk first drafts (Claude always reviews) | `DEEPSEEK_API_KEY` | **Mode 2** (advanced) | a Claude worker drafts instead |
| **Media connectors** (Higgsfield · HeyGen) | carousel and infographic visuals, short-form video renders, the synthetic presenter (`carousel-visuals`, `video-render`, `video-avatar`) | OAuth connector in Claude — no key stored | Both | nothing is generated — the run routes to the live-action lane, which writes a phone-readable shoot list you film yourself |
| **Media API keys** (Gemini · Higgsfield · ElevenLabs) | the same renders on the *headless* path, plus podcast/voice TTS | keys in `.env` | **Mode 2** (advanced) | Mode 1 uses the connectors above instead |

Metered skills **estimate cost and check your cap before every paid call** — see
[`plugin/skills/prospect/references/discovery-and-budget.md`](plugin/skills/prospect/references/discovery-and-budget.md)
for the prospecting budget model.

---

## What it does out of the box

**9 packs ship in-repo, spanning 22 workflow variants** — each a wired **workflow graph** on the
same unmodified engine. A pack is just which skills run, in what order, under which gates. Most are
sequential chains; `planning` is a **batch** of independent nodes that run side by side, and
`creator` fans out and rejoins — proving a pack is a *graph*, not necessarily a pipeline. All skills
are shared, so a pack composes existing skills rather than owning them.

| Pack | For | Variants | External gate |
|---|---|---|---|
| **[Planning](#planning)** | Set your GTM direction before you execute | 1 | — documents only |
| **[Marketing](#marketing)** | Turn a real "why now" signal into on-brand written content | 3 | Gate 1 + Gate 2 |
| **[Creator](#creator)** | The same signal, as short-form **video** | 7 | Gate 1 + Gate 2 |
| **[Prospecting](#prospecting)** | Reach the right prospect, at the right time, with the right message | 1 | staged paused — you activate |
| **[Solution architecture](#solution-architecture)** | Use case → technical solution (pre-sales / SA) | 1 | — documents only |
| **Engagement** | Show up where buyers already are: `call-prep`, LinkedIn + Reddit replies (gated), community listening, weekly market watch | 5 | reply variants gated |
| **Inbound** | A reply landed — `inbound-triage` classifies it and drafts a response behind the same human gate | 1 | gated draft, never auto-sends |
| **Knowledge refresh** | Re-reads your corpus and flags what has gone stale before a run leans on it | 1 | — writes to your profile only |
| **Outcomes loop** | Feeds real results (replies, engagement) back so the next run is scored against what actually worked | 2 | — reads and records only |

### Planning

| **Planning** | Set your GTM direction before you execute |
|---|---|
| **Deliverables** | **Quarterly GTM plan** (market focus, ICP weighting, targets) · **strategic account plan** (buying-committee map, entry strategy, matched proof stories, a 5-step action plan with owners + dates) · **event planning** (weekly scan of conferences/meetups in your category, filtered by geography, per-event cost checked against your **travel policy**) |
| **Shape** | Batch — three independent nodes run side by side, no forced order between them |
| **Output & gates** | Documents / spreadsheets only. No external gate; nothing is published or sent. Travel policy is optional: event planning runs fine without one, it just skips the cost check |

### Marketing

| **Marketing** | Turn a real "why now" signal into on-brand content |
|---|---|
| **Channels** | LinkedIn post · long-form blog · X thread · podcast · builder-in-public update · customer case study |
| **Formats** | The studio step picks the asset per run: plain text, carousel, data infographic, or handwritten-style infographic. One workflow covers many formats |
| **Flow** | radar → plan → research → studio → publish (sequential; the blog variant fans research out into evidence + competitive, then rejoins) |
| **Output & gates** | The finished post or article. **Both gates apply**: you approve the plan (Gate 1), then the exact bytes before anything publishes (Gate 2) |

### Creator

| **Creator** | The same "why now" signal, rendered as short-form video |
|---|---|
| **Variants** | `short-form-video` (fully generated) · `presenter-video` (a disclosed synthetic presenter) · `live-action-video` (your own footage) · `repurpose-clips` and `restyle-shorts` (from existing video) · `demo-clips` (screen capture of your product) · `cross-modal-campaign` (one signal → text + image + video in a single fan-out) |
| **Flow** | Generated lanes: radar → plan → **brief** → **script** → **storyboard** → render → finish → score → publish. Render fans out (vertical + feed cuts, or presenter + inserts) and rejoins at `finish`. The **brief** settles the pre-spend decisions under the plan approval and adds no gate of its own: the cover, the structure, what stays constant across shots, whether a carousel would say it cheaper. Footage lanes skip the render half — `live-action-video` runs radar → plan → brief → script → **capture** → score → publish |
| **Extra gates** | Beyond the two permanent gates, a generated lane adds a **storyboard** approval — you sign off the frames before anything renders, because rendering is the expensive part, and the gate ships a free **animatic**, those same frames held for each shot's real duration with the voice-over over the top, so you judge pacing rather than pictures. A footage lane has no storyboard at all: its extra approval is the **capture** gate, where you approve the exact source file before anything is uploaded |
| **Disclosure** | Any render using a trained likeness or cloned voice must carry your configured disclosure line, checked at staging **and** re-checked independently at the publish gate. A tenant that never configured one fails closed — this is EU AI Act Article 50, not house style |
| **Output & gates** | A finished, captioned, scored cut. **Both gates apply**, plus the lane's extra gate — storyboard on a generated lane, capture on a footage lane |

### Prospecting

| **Prospecting** | Reach the right prospect, at the right time, with the right message |
|---|---|
| **What it does** | Sources, enriches, and scores leads so your outreach lands where it should. Every account scored against **your** ideal customer profile, not a generic list |
| **Flow** | prospect → dossier → **outreach (gated)** → email-quality → **sequence (gated)** |
| **Data sources** | **Vibe Prospecting** — discovery, firmographics, company-level buyer-intent. **RocketReach** — verified contact email/phone, news & hiring triggers, job-change timing. **Apollo** — last-resort contact backstop (email only), company buying-intent, job-posting signals. Fused into a "why now" heat signal. Free web search is the fallback when none are connected |
| **Output** | Scored brief · contact-ready outreach packs · HubSpot-ready CSV. Email drafts follow best-practice sequence structure (a real signal as the hook, a matched case study, one clear ask) and cite only public signals — intent times the touch, it never appears in the copy |
| **After a reply lands** | The `inbound` pack reads it (read-only), classifies intent (P0–P3), and drafts a reply behind the same human gate. Nothing auto-sends |
| **Scheduling** | Bring-your-own Calendly: paste your booking link and drafts propose a time, the prospect books themselves. Optional CRM sync is your own Calendly upgrade, not a credential this system holds |
| **Output & gates** | The sequence is staged **PAUSED** in your sender. There is no resume tool on the connector — a human activates it |

### Solution architecture

| **Solution architecture** | Use case → technical solution (for pre-sales / SA) |
|---|---|
| **What it does** | Turns a use case into a technical solution, either mapped onto your flagship product (product-led) or synthesised as a bespoke custom build |
| **Flow** | discovery question bank → solution design → setup runbook → deck (sequential chain) |
| **Under the hood** | Profiles the account's stack, produces architecture diagrams and a design doc, and hands off to the deck or Word skills |
| **Output & gates** | Documents only. No external gate; nothing is published |

---

## GTM skill suite (59 skills — all profile-driven)

Every skill is **company and product agnostic** — brand, voice, ICP, markets, and product all load
from the active profile bundle. Zero hardcoded company strings (CI-gated by `debrand_check.sh`).

> The full, always-current inventory is generated at [`docs/SKILLS.md`](docs/SKILLS.md) (one row per skill; CI fails if it drifts). The table below is a curated, categorized view.

| Category | Skills |
|---|---|
| **Prospecting** | `prospect`, `market-scan`, `events-tracker`, `draft-outreach`, `email-sequence`, `email-quality`, `inbound-triage` |
| **Account & call prep** | `call-prep`, `account-plan`, `account-dossier`, `deck-research`, `build-deck` |
| **Proof & partners** | `case-study`, `consulting-partner-brief`, `product-partner-brief` |
| **Content pipeline** | `content-radar`, `content-plan`, `content-research`, `content-studio`, `content-publish`, `format-router` |
| **Short-form video** | `video-router`, `creator-brief`, `video-script`, `video-storyboard`, `video-render`, `video-avatar`, `video-finish`, `video-score`, `video-clip`, `video-restyle`, `demo-capture`, `video-router`, `video-storyboard` |
| **Engagement** | `linkedin-engagers`, `linkedin-reply`, `reddit-reply`, `community-signal-analysis` |
| **Carousels & infographics** | `carousel-pdf`, `carousel-visuals`, `carousel-auto`, `infographic-data`, `infographic-handwritten` |
| **GTM planning** | `gtm-planning`, `campaign-plan`, `solution-discovery`, `solution-design`, `solution-scope-check`, `gateway-runbook` |
| **Market intelligence** | `market-harvest`, `market-intelligence` |
| **Risk assessment** | `airq-scan` |
| **Founder journey** | `builder-radar`, `builder-evidence`, `builder-studio` |
| **Operations** | `setup`, `profile-onboard`, `identity-kit`, `knowledge-refresh`, `outcomes-sync`, `content-outcomes-sync` |

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

To confirm which tenant is currently active (useful before running anything that writes state,
especially when running several companies locally for demos):

```
uv run python -m gtm_core.profile status   # active profile + resolved content root
uv run python -m gtm_core.profile list     # every profile bundle found, active one marked
```

This is a read-only status check — it never switches a profile itself; that stays the operator's
call.

---

## Content craft — the details that make output land

An AI that writes "on-brand" text is table stakes. What actually determines whether content gets
opened, read, and shared is a set of specific, opinionated techniques — refined over real usage and
enforced as hard gates, not just prompted for and hoped:

- **Virality engineering — write for what's *felt*, not just what's useful.** Every post is composed
  against an explicit **emotional-trigger system** ([`docs/virality-engineering.md`](docs/virality-engineering.md)):
  six triggers (identity validation, status signal, tribal belonging, productive discomfort,
  curiosity gap, aspiration) that are **stacked, not checklisted** — one fired alone is mild; two or
  three compound. Every draft must pass a **felt test** ("useful but not felt" gets rewritten) before
  it ships, and the system is deliberately **B2B-recalibrated**: tribal lines drawn on *how well you
  do the work* (never against a named competitor), every aspirational claim paired with real proof.
  *Why it matters:* almost no AI writing tool does this — most optimize for *informative*, which is
  exactly why it scrolls past. Engineering the *feeling*, on a B2B buyer, without sounding like a
  hype-merchant, is the hard part.
- **Hook optimization.** Every opening line is drawn from a named library of **9 hook archetypes**
  (a shipped artifact, a counterintuitive decision, a named number, a status-quo fault-line, and
  others) — never a generic template — and must be **zero-context self-contained** and traceable to a
  real fact in that run's research. At the plan gate you're offered **3 candidates from 3 different
  archetypes**, so you're choosing the angle, not just approving a single draft. Most AI content
  reads the same because it starts from a generic prompt instead of a considered rhetorical
  structure — and the hook is what earns the first three seconds.
- **Content quality & structure enforcement.** Every draft is checked by an automated linter against
  exact, per-format rules **before you ever see it** — a LinkedIn post needs a ≤140-character hook and
  a 1,300–2,500-character body; an X thread needs 5–9 tweets with the first standing alone, no link;
  a carousel needs 8–12 slides at ≤50 words each with a re-hook partway through. *Why it matters:*
  this isn't a style suggestion the model might follow — it's a hard gate a malformed draft fails
  before it reaches you, so "looks fine" is a floor, not a hope.
- **News hijacking, done safely.** The content radar scores every real news item, then layers two
  judgment calls that never distort the base score: a **fault-line** check (does the story have a
  genuine, arguable angle — never naming a competitor, skipped outright when the angle can't be taken
  safely, e.g. a tragedy) and a **velocity** check (is attention still rising or already peaked — a
  peaked story is down-weighted). *Why it matters:* reacting to news is where brands look either
  tone-deaf or three days late; timing the "why now" is most of the reason a radar exists.
- **Platform optimization, not reformatting.** Each platform gets its own shape from one shared
  research pack — you never re-research per channel, only re-shape for it: LinkedIn text runs
  1,300–2,500 characters with the link moved to the first comment; an X thread opens with a
  stand-alone tweet; a Facebook post caps near 480 characters before the fold; an Instagram reel
  scripts its hook for the first 1–2 seconds. What earns reach on LinkedIn actively hurts it on X —
  and shrinking one draft to fit every channel is one of the most common GTM content mistakes.
- **Best-practice outreach sequencing (prospecting).** Covered above — email drafts follow
  proven sequence structure (a real signal as the hook, a matched case study, one clear ask) rather
  than a generic cold-email template.

---

## How it works

gtm-engine runs GTM work as a **workflow graph**: the *engine* executes it, a *pack* defines it, and
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

**The three layers**:

- **Engine** *(domain-agnostic — all governance lives here).* Executes any graph: it walks the
  *frontier* of runnable nodes (a node runs once its dependencies are done), so a graph resumes from
  failure and can fan out in parallel. The engine — not any workflow — owns the two human gates, the
  per-node budget cap, the model registry, MCP egress, and the audit ledgers.
- **Pack** *(declarative workflow — data, not code).* A graph of **nodes** (`depends_on` edges, an
  optional gate, a model role each) wiring together shared **skills** into one domain workflow.
  They all ship in-repo, on the *same* unmodified engine — the inventory is in
  [What it does out of the box](#what-it-does-out-of-the-box). A pack references skills; it never
  owns them, so `build-deck` can appear in several packs at once. Pack graphs are versioned config: they name *which* skills run in *what*
  order under *which* gates — they **cannot** name a destination, an egress path, or a credential.
- **Tenant** *(your data — never logic).* Your profile: settings in `PROFILE.md` and a knowledge
  corpus in `knowledge/` (the "second brain"). A tenant can make a workflow **stricter** — add a
  node or turn on a gate — but can **never** remove a safety gate. That's enforced at load, by
  construction, not by trusting intent.

### Why it's built this way

1. **Two human gates are permanent; nothing publishes itself.** Every workflow pauses at **Gate 1**
   (approve the plan) and **Gate 2** (approve the exact bytes before they go out); `autopublish` is
   `false` everywhere, and the publish destination is pinned server-side where the agent can't touch
   it. *Why:* GTM output carries your name and your customers' data — a human always approves the
   exact text.
2. **Tenant state is isolated by construction.** Each company is a separate profile with its own
   state, ledgers, and customer data; the path-resolution spine binds every automated read and write
   to the active tenant. The hosted multi-tenant backend additionally enforces this at the database
   layer (row-level security); the single-operator local/VPS path keeps automated writes in-lane the
   same way but is not a sandbox against the agent itself, and shared external connector accounts
   (Syften, HubSpot, etc.) aren't yet scoped per tenant. *Why:* the same engine serves many companies without their data silently mixing.
3. **Claude is the brain; MCP servers are the only hands.** The agent never makes a raw HTTP call —
   every scrape, lookup, render, and publish goes through an MCP tool. *Why:* least privilege by
   construction. Credentials live with the tools, not in the model's context, so a bad instruction
   in a scraped page can't exfiltrate a key or reach an endpoint the tool surface doesn't expose.
4. **Everything is profile-driven — zero hardcoded company facts.** Brand, ICP, personas, voice,
   markets, and budget all load from the *active profile* at runtime (CI-gated: no company strings
   in code). *Why:* one engine serves many companies, and the highest-risk error in GTM automation
   — *right content, wrong company* — becomes structurally impossible to make silently.
5. **New workflows are data, not code.** Adding a workflow means writing a pack — a graph of nodes
   wiring existing skills — which the engine validates and runs unchanged; no engine edits, no new
   deploy. *Why:* the domain logic you'll change most often lives in reviewable, versioned config,
   while the governance you must never break stays fixed in the engine.
6. **Onboarding your knowledge is a first-class step.** Say `"set me up"` and the engine ingests your
   materials — docs, URLs, notes — condensing them into a structured "second brain" (company, ICP,
   voice, case studies) that every skill retrieves from; a readiness check flags what's missing or
   stale before a run leans on it. That includes `market-scan`: it auto-discovers a GTM plan, email
   sequence, or account plan you've already built and focuses its weekly signal sweep on the
   industries, use cases, and buyer personas where you're actually selling, instead of just
   whatever's loudest in the news that week. *Why:* good GTM output needs your real context, and
   keeping that context fresh shouldn't be manual bookkeeping.

**In advanced mode** (the self-hosted agent), a cheap worker model (DeepSeek) handles bulk first
drafts to keep costs down, but **Claude always reviews** anything before it's shown or shipped, and
every gate-critical or PII-handling node stays on Claude by construction. Model choice resolves
through a committed registry (`gtm_core/models.toml`). Cowork mode always talks to Claude directly —
this tiering only applies when a self-hosted agent is running the workflow unattended.

### For a technical evaluator

*Skip this if you're here to use it rather than to assess it.*

> gtm-engine is an **outer agent harness**. The
> [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk/overview) runs the inner loop — one
> session, model plus tools. gtm-engine owns the loop *around* it: what the model can see, what it can
> call, what runs next, and what can reach an external system.

Control flow lives in code, not in the model's context. The engine is a DAG scheduler: it advances a
frontier of runnable nodes, persists a run manifest at every transition, resumes a crashed run from
the first incomplete node, and dispatches fan-out concurrently under a single per-tenant lock. Spend
is capped by two independent mechanisms — a ledger check before every dispatch batch, and a hard
per-run ceiling at the SDK layer. The model runs inside a node and does not choose the next one, so a
bad generation produces a bad draft, not a bad run state.

Every tool call is adjudicated by a policy callback before it executes — fail-closed, with a
declarative deny floor beneath it. Publish and send are not in the model's tool schema at all. To
publish, the model emits bytes into a gate block; a human approves those exact bytes and Python makes
the call, to a destination pinned server-side. Email sequences are staged paused, and the sequencer
connector exposes no resume tool. A prompt injection cannot reach a capability the schema does not
contain.

The worst case for a bad model turn is a draft you reject. Every path to an external effect ends at a
human approving exact bytes, and no tool exists that skips that step.

Not done yet:
output-side DLP/PII moderation is a tracked gap, not a shipped control; and in advanced mode the
mechanical, no-PII nodes route to a non-US/EU inference endpoint.

---

## Repo layout

```
plugin/        gtm-engine plugin — the GTM skills, loaded by the Agent SDK
  skills/      one folder per skill; each has SKILL.md + references/
  .claude-plugin/plugin.json
profiles/      per-company bundles (the tenant layer — data only)
packs/         declarative workflow graphs — one folder per pack, graphs/<variant>.toml inside
gtm_core/      shared engine library (skills, graph runner, packs, profiles, ledgers, check_env)
agent/         Agent SDK app — brain, graph runner, session store, ledgers, publish, radar
cockpit/       Telegram bot + human-gate handlers (self-hosting only)
backend/       FastAPI multi-tenant backend (auth, runs, gate, billing) + schema/ migrations
mcp_server/    MCP server runtime — curated gtm_core tools, API-key auth, metering
deploy/        Docker Compose + tunnel config (self-hosting)
scripts/       ops + dashboard scripts (incl. bootstrap.sh / bootstrap.ps1)
schemas/       JSON Schemas for the data contracts
tests/         content linter + contract tests + fixtures
docs/          design docs + DEPLOY guide + enforced rules (RULES.md) + platform playbooks
.env.example   every variable the system reads, grouped by tier (real .env is gitignored)
```

Runtime state (`content/<profile>/…`, ledgers) is **gitignored** and lives wherever the agent runs.


---

## Self-hosting & publishing (advanced mode)

Everything in this section is **Mode 2 / advanced mode** — not needed for Cowork mode. The
autonomous pipeline, Docker Compose stack, secret management, Telegram cockpit, and the LinkedIn
publish gate (Gate 2) are documented in **[`docs/DEPLOY.md`](docs/DEPLOY.md)**.

Security posture (least-privilege tool surface, permanent gates, tenant boundary, publish-gate
design): [`CLAUDE.md`](CLAUDE.md).

How data is stored, scoped per tenant, and retired — files vs. database, the knowledge
lifecycle, ledgers, and the staging→promotion pattern: [`docs/gtm-data-infra.md`](docs/gtm-data-infra.md).

---

## Development

Working in the repo — dev setup, the CI gates your change must pass, and the invariants you must
not break: **[`CONTRIBUTING.md`](CONTRIBUTING.md)**. The enforced Python rules (§R1–§R18, most
CI-gated) live in [`docs/RULES.md`](docs/RULES.md).

CI runs the shell lint gates and `pytest tests/` on pull requests; use `uv run …` for everything
(`uv run pytest tests/ -q`, `uv run ruff check .`) — never bare `python`. Contributions are accepted
under the project's Apache-2.0 license (below).

---

## License

Licensed under the **Apache License 2.0** — see [`LICENSE`](LICENSE). Permissive use with an express
patent grant; you may use, modify, and distribute this software provided you retain the copyright
and license notices.
