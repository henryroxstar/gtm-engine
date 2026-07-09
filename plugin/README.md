# GTM Engine — plugin

A company-agnostic GTM skill suite for Claude Code / Cowork: prospecting, outreach, market
scanning, LinkedIn carousels and infographics, agent-security scans, call prep, deck research,
decks, account plans, quarterly planning, and a live command deck. Install once, spend two minutes
on setup, and every skill runs in **your market**, in **your voice**, with **your budget**.

Every skill is **profile-driven** — brand, voice, ICP, markets, personas, case studies, and product
all load from the active **profile bundle** under `profiles/<slug>/`. The plugin itself carries no
company strings and no credentials, so it is safe to share. (CI-gated by `debrand_check.sh`.)

---

## Getting started (2 minutes)

1. **Install / open** — open the repo in Claude Code, or install the plugin bundle and press **Accept**.
2. **Say "set me up"** — the `setup` skill makes sure the engine can run (it installs dependencies if
   needed and checks your environment), then interviews you: your name and signature, **your voice
   style**, the markets and cities you sell into, which personas to emphasize, your cadence, and a
   monthly budget for paid tools.
3. **Done** — you get a saved profile and a 3-account sample run so you see real output in your first
   five minutes.

Some tools are **free** (web search, browser) and some need **your own key** (Vibe Prospecting,
Firecrawl, media). Setup walks you through connecting them — no key is ever stored in a file.

---

## Your weekly rhythm

The skills are designed to run in sequence (you don't have to follow this order):

```
Monday
  1. "Run my market scan"          → rated signals, competitor moves, LinkedIn drafts
  2. "Auto-carousel"               → pick the strongest signal, build the week's carousel
  3. "Run my prospecting"          → scored accounts + outreach packs + CRM-ready CSV

Before a call
  4. "Prep me for my call with X"  → one-page brief: snapshot, personas, objections, ask

For key accounts
  5. "Build an account plan for X" → committee map, entry point, 5-step action plan
  6. "Research X for a deck"       → reusable account dossier (intel + per-persona slot-fills)
  7. "Build a deck for X"          → branded .pptx or cinematic Slidev deck (consumes the dossier)

Once a quarter
  8. "Plan my quarter"             → market snapshot, tiered accounts, motion calendar
```

Setup can schedule the weekly motions to run automatically.

---

## Skills (selected)

### Find & reach accounts

**`prospect`** — Discovers companies that fit the active profile's ICP, qualifies and scores them
against your target markets, finds the "why now" signal, and identifies the right contacts. Outputs a
scored brief, outreach packs for the top accounts, and a CRM-ready CSV.

**`draft-outreach`** — Writes LinkedIn DMs, cold emails, and follow-ups **in your voice** (from your
profile's voice style, not a generic template), grounded in a real signal and the closest matching
customer story from your profile.

### Stay current on the market

**`market-scan`** — Weekly scan of competitor moves, regulatory signals, and category news. Each
signal is rated H / M / L. For the strongest signals it produces ready-to-post LinkedIn drafts, a
blog brief, a campaign idea, and a POC flag.

**`events-tracker`** — Scans event platforms and the web for relevant events near your home base,
estimates travel cost per event, and updates one spreadsheet in place each week.

### Build LinkedIn content

**`carousel-pdf`** — Writes and renders a 4:5 portrait PDF carousel (hook → value arc → proof → CTA),
dark or light theme, with caption variants and DM copy.

**`carousel-visuals`** — Adds AI-generated visuals to a completed carousel (cover image, per-slide
backgrounds, optional motion teaser). Budget is checked and confirmed before every generation.

**`carousel-auto`** — Automates the full weekly carousel pipeline in one command, from the latest
market-scan output to a complete dated publish folder.

### Prep & win deals

**`call-prep`** — One-page pre-call brief: account snapshot, persona cards, the closest matching case
study from your profile, likely objections with rebuttals, and a sharp ask.

**`deck-research`** — Researches an account into a reusable two-layer dossier: Layer 1 is
persona-agnostic account intelligence (footnoted); Layer 2 is per-persona slot-fills keyed to each
deck template. Research once, spin decks for multiple personas.

**`build-deck`** — Profile-branded decks (discovery deck, one-pager, POC proposal, partner brief),
grounded in the profile's knowledge pack and personalised to the account. Consumes a `deck-research`
dossier when present, or runs standalone. Defaults to `.pptx`; deck byline defaults to your profile's
brand. Mode A (.pptx, no dependencies) or Mode B (cinematic Slidev).

**`account-plan`** — Strategic plan for high-value accounts: ICP score, buying committee map, entry
point strategy, proof story selection, and a sequenced 5-step action plan.

### Plan the quarter

**`gtm-planning`** — Structured quarterly plan: market snapshot, ICP priorities, tiered target
accounts, proof story selection, week-by-week motion calendar, risks, and open decisions.

> The full set is **31 skills** across prospecting, content, account prep, planning, engagement,
> risk, and the founder-journey track. Browse `plugin/skills/` for the complete list.

---

## Tools & costs

| Tool | What it's for | Cost model | Fallback |
|---|---|---|---|
| Web search / browser | Prospecting, market scan, events, research | Free | — |
| Vibe Prospecting | Enriched company + contact data | Credits (OAuth connector) | Web search |
| Firecrawl | Structured web scraping | Credits (env-var key) | Built-in web tools |
| Media (image/video) | Cover art + motion teasers | Credits (key/OAuth) | Text-only output |

Before any paid step, the plugin estimates the cost, shows it, and **stops at your cap** rather than
quietly spending. You set both a monthly and a per-run cap during setup.

---

## Keys & privacy

The plugin carries **no credentials** — safe to share. Keys resolve in order of preference:

1. **App connectors (preferred)** — OAuth connectors live in your account's secure store, never in the plugin.
2. **Environment variables** — e.g. `FIRECRAWL_API_KEY` on your own machine; the plugin references only a placeholder.
3. **Profile = config only** — your profile records that a tool is connected, never the key itself.

Each colleague connects their own keys after installing; those keys stay on their side.

---

## Modes: local vs self-hosted

Both modes use the same skills and knowledge; the difference is what runs alongside Claude.

**Local mode (default).** Claude Code / Cowork is your cockpit — no server, no always-on process.
Radar falls back to web search; prospecting uses web search when Vibe isn't connected; events use the
browser when Firecrawl isn't set; publishing is manual (the plugin emits the exact post text in chat
and you paste it). State lives in `content/<profile>/`.

**Self-hosted mode.** An always-on server runs a cockpit, the dashboard, and scheduled jobs (Docker +
a secret manager). Radar uses a live news DB; publishing goes through an approval gate to a pinned
account. See [`docs/DEPLOY.md`](../docs/DEPLOY.md).

**Graceful degradation.** Every connector has a free fallback. The plugin announces its mode and any
degraded paths at the top of each run. The state format is identical in both modes — promote from
local → self-hosted by copying `content/`.

---

## Updating

Install a newer version the same way; your profile and saved outputs stay where they are. When a
profile's product story changes (new case study, pricing, product), update that profile bundle — the
plugin code does not change.
