
# GTM Engine — Setup (front door)

Onboard a new colleague in about two minutes: interview them, scaffold their company profile bundle at `profiles/<company>/`, help them connect optional tools safely, and prove value with a 3-account sample. Keep the conversation in plain language — never expose file paths, schema, or internal mechanics unless asked.

## When this runs

First install, or any time the user wants to (re)configure. If a `profiles/<company>/PROFILE.md` already exists, offer to review/update it rather than overwrite — read it first, then confirm each change.

## Step 0 — Make sure the engine can run (first install only)

Before interviewing, confirm the local environment is ready — the user should never have to do this by hand:

1. Run `bash scripts/bootstrap.sh`. It installs `uv` if missing, runs `uv sync` (which provisions the right Python and all dependencies), and prints the environment self-check. Re-running is safe.
2. Read the self-check output (`uv run python -m gtm_core.check_env`). It reports which capability **tier** is unlocked:
   - **TIER 0** (`ANTHROPIC_API_KEY`) is required — if it's missing, tell the user to copy `.env.example` → `.env` and set `ANTHROPIC_API_KEY`, then continue. Nothing runs without it.
   - **TIER 1** (`DEEPSEEK_API_KEY`, `FIRECRAWL_API_KEY`) is optional — unlocks the content pipeline and richer research; safe keyless fallbacks exist if unset.
   - **TIER 2** is self-hosting only (Telegram, publishing, media) — skip it for a Claude Cowork user.
3. State plainly what's on and what's using a fallback, then move on. Do **not** block setup on TIER 1/2 — only TIER 0 is required.

## Step 1 — Interview (use AskUserQuestion)

Ask in small batches, not one giant form. Use the `AskUserQuestion` tool so answers are structured. Cover, in order:

1. **Name & signature** — their name, title, and how they sign outreach (e.g. just "Henry", or a full block).
2. **Voice style** — ask: "Do you have a voice guide, or can you describe how you write? This shapes every outreach draft." Give them two options: (a) paste or summarize their guide, (b) skip and use the built-in default. If they provide a voice style, capture the key markers: core philosophy, tone, structure preferences, close style, and any banned words. Store it in `voice_style` in their PROFILE. If they skip, note that the profile's `knowledge/voice.md` applies and they can update their profile later.
3. **Markets** — target markets (primary first) and, optionally, key cities. Offer the plan's worked examples (United States, Singapore) as defaults but let them type their own.
4. **ICP weighting** — per-run segment mix (default **3 enterprise + 7 startup**, i.e. 70% startup / 30% enterprise), plus any personas or verticals to emphasize. Blank = use the full ICP in `profiles/<active>/knowledge/icp-personas.md`.
5. **Language** — default English; ask only if it might differ.
6. **Home base & travel** — home city and nearest transit/airport hub (used later by events-tracker). Travel policy can stay default for now.
7. **Cadence** — confirm the defaults (prospect weekly Mon; market-scan & events weekly Mon; gtm-planning quarterly) or adjust. All skills are active now. Ask: "Want me to schedule your weekly motions so they run automatically each Monday?" — this is a yes/no, not a detail question.
8. **Budget** — monthly cap and per-run cap (USD) for metered tools. Defaults: $50/month, $10/run. Explain free paths (web search, browser) never count against it.

Recommend the defaults explicitly; don't make them guess. Keep it to a handful of quick questions.

## Step 2 — Scaffold the company profile bundle + content root

Ask for the **company slug** (lowercase, no spaces — e.g. `acme`) if it isn't already clear; this names the bundle. Then scaffold a per-company profile bundle at **`profiles/<company>/`**:

1. **`profiles/<company>/PROFILE.md`** — read the template at `${CLAUDE_PLUGIN_ROOT}/PROFILE.template.md`, fill every `<…>` placeholder from the interview answers (config only — see Step 3 on secrets), and write it here. This is the active-profile config every other skill reads at runtime.
2. **`profiles/<company>/knowledge/`** — the company knowledge pack (company.md, product.md, icp-personas.md, case-studies.md, voice.md, voice-bans.txt, audience-psychology.md, and any guidance/source/brand subfolders). Create the folder; populate it from the company's own material if the colleague supplies it, otherwise leave stub files for them to fill in via the `knowledge` refresh flow later. `voice-bans.txt` (the machine-readable ban list the prose linter reads) is seeded from the voice `ban_list`; `audience-psychology.md` is built with `docs/audience-psychology-method.md`.
3. **`profiles/<company>/products/`** — one `<slug>/PRODUCT.md` per product the colleague declared (mirror the `products[]` list in PROFILE.md), plus a `references/` folder for each product's reference docs.

**Content root (state location):** All mutable output — run histories, costs, events sheets, prospect snapshots — goes to a *content root* separate from the profile bundle. Create it now:

- **Default (in-repo):** `content/<company>/` in the current working directory. This is gitignored; outputs stay local and survive sessions. Best for quick starts.
- **Custom (recommended for portability):** suggest `~/.gtm/content` so state lives outside the repo and is preserved across clones. Ask: "Where should I save your weekly outputs — the default `content/<company>/` folder here, or somewhere like `~/.gtm/content/<company>/`?" If they choose a custom path, tell them to add `export GTM_CONTENT_ROOT=<path>` to their shell profile (`~/.zshrc` or `~/.bashrc`) so every session picks it up automatically.

Create the chosen content root directory and record `content_root` in PROFILE.md (the resolved path, not the env var name). All skills derive their output path from this at runtime.

Show the user a short plain-language summary of what you captured and where the bundle lives, and confirm it's right.

## Step 3 — Connect tools (NO key ever goes in a file)

Explain the three-tier model in one breath: app connectors hold their own credentials; raw keys live in the OS environment; plugin files only ever record *that* a tool is connected. Then:

- **Vibe Prospecting (optional, recommended).** This is the cold-discovery + enrichment engine. It's an OAuth connector — the plugin already references it. Tell the user they can connect it from Claude's connector UI (search "Vibe Prospecting"); the credential stays in the app's secure store, never in the plugin. If they skip it, `prospect` still works via web-search fallback. Record `vibe_prospecting: connected | not connected` in the PROFILE.
- **Firecrawl (optional).** Used by the events-tracker for bulk scraping of JS-rendered event calendars (Luma, Eventbrite, Meetup). It's key-based: the user sets `FIRECRAWL_API_KEY` as an OS environment variable (or in their keychain / a secrets manager). The plugin's config references `${FIRECRAWL_API_KEY}` — a placeholder, never the value. The events-tracker falls back to the browser if Firecrawl isn't connected, so this is optional. Record `firecrawl: connected | not connected` in the PROFILE.
- **Higgsfield (optional).** Used by the carousel-visuals skill for AI-generated 4:5 cover art, per-slide backgrounds, and 9:16 motion teasers for LinkedIn carousels. It's an OAuth connector — connect it from Claude's connector UI (search "Higgsfield"). Budget-guarded with `get_cost` preflight before every call; text-only carousels are always the free fallback if Higgsfield isn't connected. Record `higgsfield: connected | not connected` in the PROFILE.

Never ask the user to paste a key into the chat or any file. If they try, stop them and point to the env-var / connector path.

## Step 4 — 3-account proof run

Prove the plugin works in their first five minutes. Invoke the `prospect` skill in **proof mode**: discover and score just **3 accounts** for their primary market (1 enterprise + 2 startup is a good mini-mix), using the web-search path so it costs nothing even if Vibe isn't connected. Produce a trimmed version of the normal output (summary + score + one why-now signal + the mapped case study per account). Do **not** spend metered credits during the proof. Save it to their working folder so they can see a real artefact.

## Step 5 — Cadence scheduling (if the colleague said yes)

If the colleague said yes to automatic scheduling in the Step 1 cadence question, wire up the recurring tasks now using the `schedule` skill (or `mcp__scheduled-tasks__create_scheduled_task`):

**Suggest this Monday-morning order** (signals inform outreach angles before prospecting runs):
1. Market scan — every Monday, prompt: "Run my market scan"
2. Prospecting — every Monday (after market scan), prompt: "Run my prospecting"
3. Events tracker — every Monday, prompt: "Run my events tracker"

Create scheduled tasks only for the motions the colleague enabled in their PROFILE. Confirm each task name, cadence, and first run time before creating. After creating, show a plain-language summary: "I've set up N recurring tasks — your first Monday run is [date]."

**Firecrawl budget reminder:** when setting up events-tracker scheduling, remind the colleague that before any Firecrawl call the skill estimates cost, shows it, and stops at their cap ($X/run from PROFILE). The budget guard runs automatically; they don't need to do anything.

If they declined scheduling or want to add it later, note: "Say 'schedule my weekly market scan' or 'schedule my events tracker' any time to add it."

## Step 6 — Confirm & hand off

Summarize, in plain language:
- What you saved (their profile + the 3-account sample) and where (their working folder).
- What's connected vs. still optional (Vibe, Firecrawl).
- Which scheduled tasks were set up (if any), and when they'll first run.
- What they can say next: **"run my prospecting"**, **"draft outreach to [name] at [company]"**, **"run my market scan"**, **"run my events tracker"**, **"build a carousel about [topic]"**, **"auto-carousel"** (automated carousel from the week's scan), **"add visuals to my carousel"**, **"prep me for my call with [company]"**, **"build a deck for [company]"**, **"make a one-pager for [account]"**, **"build an account plan for [company]"**, **"plan my quarter"**.

Keep the close short and encouraging. The goal is that they've seen real output and know the two or three things to say next.

## Guardrails

- Config only — if a value looks like a secret (key, token, password), refuse to write it to the PROFILE and explain where it belongs instead.
- Respect the budget caps from the moment they're set: the proof run must stay on the free path.
- Don't overwrite an existing PROFILE without confirming each change.
