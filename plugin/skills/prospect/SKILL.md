---
name: prospect
description: >-
  Run the active profile's prospecting routine — discover, qualify, score, and enrich ICP
  accounts, then output a scored brief, Tier-A outreach packs, and a HubSpot-ready CSV.
  Product-agnostic: markets, ICP, and the lead product all come from whichever profile is
  active. Use when the user says "run my prospecting", "find prospects", "build a prospect
  list", "weekly prospecting run", "find accounts for [the lead product]", or "prospect
  [market]". Uses two data sources when connected — Vibe Prospecting (discovery +
  firmographics + Bombora topic-intent + events) and RocketReach (contact resolution +
  Intentsify topic-intent + news/hiring signals + job-change timing); free web-search is the
  fallback. Respects budget caps before any metered call.
metadata:
  version: "0.4.0"
  phase: "1"
  capability_tier: core
---

# Prospect

> Resolve the **active profile** (the agent provides it; PROFILE + company knowledge load from
> `profiles/<active>/`, never `plugin/`). The lead product is the active company's `default_product`
> from `PROFILE.md` → `products[]` — use its real name throughout, never a hardcoded one.

Produce a run of qualified accounts (default **10: 3 enterprise + 7 startup**) scored against the ICP rubric, each with named personas, a dated "why now" signal, a matched case study, and a recommended hook — plus a Tier-A outreach pack per 🔥 account and a HubSpot CSV for manual import. **No live CRM** (the CSV is the handoff).

**Two data sources, each doing what it's best at (both optional; free web-search is the floor):**
- **Vibe Prospecting** (Explorium) — cold **company discovery + firmographics + topic buyer-intent** (Bombora `business_intent_topics` filter) **+ business events**. The discovery engine.
- **RocketReach** — **contact resolution**: the named buyer's **verified email + direct phone** (deeper per-person database), plus **company topic-intent** (Intentsify tracked topics / `intent` facet), **news & hiring trigger signals**, and **person job-change timing**. Primary for enrichment.
- **Heat axis:** the rubric scores fit (*who*); intent scores heat (*when*). High topic-intent on either feed = **+2**; both feeds converging on one account (**double-intent**) = **+1 more** (cap at the rubric ceiling). 🆕 new-in-role champions get queue priority, not points. Record `heat` + `intent_feeds` per account. Intent times the touch and picks the angle — **never quote it in outreach copy**.
- **Toggle / fallback:** discovery **defaults to Vibe** — attempt it every run, including scoped/narrow cohort runs, given the large unused budget headroom (`per_run_cap_usd` vs typical spend); web is the **true fallback only**, used when Vibe is genuinely disconnected or erroring, never chosen for session expedience on a small or single-vertical run. Contact enrichment → RocketReach, else Vibe `enrich-prospects`, else public web (mark **unverified**). Signal search → RocketReach when available, else Vibe events + web sweep. If either source is disconnected or over its share of the cap, use the other; free web is always the floor.

## Load context first

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

1. **Read the PROFILE.** Read `profiles/<active>/PROFILE.md`. Pull: `company`, `brand_name`, `default_product` (the lead product), `target_markets`, `segment_mix`, `emphasize_personas/verticals`, `monthly_tool_budget_usd`, `per_run_cap_usd`, `tools_metered`, and the `vibe_prospecting` + `rocketreach` connection statuses. If no PROFILE exists, run `setup` first (or ask the 3 essentials: markets, segment mix, budget).
2. **Skim the references** (load as needed):
   - `references/gates-and-scoring.md` — **generic** scoring machinery only: order of ops, the gate/rubric *shapes*, the heat axis, Tier-A ordering, default thresholds, per-run distribution. The **actual gates, rubric line-items, segments, and thresholds are tenant-specific** and come from the active profile (next bullet), which wins over anything illustrated here.
   - `references/discovery-and-budget.md` — Vibe filters, credit/budget model, enrichment depth, the 6-source signal sweep, the RocketReach Intentsify two-tier intent path, and the web-search fallback.
   - `references/intent-signals-catalog.md` — reference catalog of **every** buyer-intent signal captured from Vibe + RocketReach (facet/field, meaning, heat mapping, freshness, the weekly-snapshot capture path).
   - `references/heat-rescore.md` — the **Re-score mode** procedure: refresh `heat`/`intent_feeds` on existing `latest.json` accounts against today's intent, without new discovery.
   - `references/output-templates.md` — run header, per-account, Tier-A pack, QA checklist.
   - `references/hubspot-csv-map.md` — CSV columns + import settings.
   - **The profile owns the ICP:** `profiles/<active>/knowledge/icp-personas.md` is the source of truth for this tenant's **gates + scoring rubric + segment thresholds** (some profiles put the rubric in a dedicated file it links, e.g. `buyer-intent-signals.md` — follow the link). Also read `case-studies.md` and the `hook-matrix.md` for facts/hooks.
   - **Vertical packs (if the profile ships them):** when the active profile has industry packs under `knowledge/industry/`, load the one matching an account's sector — `knowledge/industry/<vertical>.md` — for the industry overlay the generic ICP can't carry. Read its **"Prospecting signals & where to fish"** (where to source + which intent topics apply for that industry), **"Why now — urgency drivers"** + **"Regulatory & compliance landscape"** (the dated, industry-specific triggers that qualify a why-now and sharpen the ICP gate), and **"Matching proof shape" / "Email angles"** (the vertical-matched case study + hook). Cross-reference the pack's intent topics against `market-scan-config.md`. A profile with no `knowledge/industry/` simply skips this — nothing changes for it.

## Modes

- **Full run (default):** 10 accounts per PROFILE markets and segment_mix.
- **Proof mode (called by `setup`, or "quick sample"):** 3 accounts (1 enterprise + 2 startup), **web-search path only, 0 credits**, trimmed output. Skip enrichment and CSV; produce summary + score + one why-now + mapped case study per account (case study from `profiles/<active>/knowledge/case-studies.md`).
- **Scoped run:** if the user names a market, segment, or count ("5 startups in Singapore"), honor it.
- **Bulk run (operator states a large target, e.g. "300 accounts"):** standard mode's ~20–30-row `fetch-entities` intake per pass cannot reach a large target — use **bulk mode** instead (full funnel in `references/discovery-and-budget.md` §"Bulk mode"). Steps 1 (exclude set) and 2 (budget pre-check, auto-scaled) below still apply; Steps 3–4 (discovery + why-now) are replaced by that section's size→filter→export→ingest→score flow; Steps 5–6 (gate/score, enrichment) run the same rubric but **finalists-only enrichment and web-sweep confirmation**, not per-candidate; Step 7's `latest.json` merge happens via `python -m gtm_core.prospects_import finalize` (wraps the same safe merge-only writer + emits the HubSpot CSV in one call) instead of a hand-assembled items file. If any gate was relaxed for the run, set `qualification_path` (e.g. `intent-only-relaxed`) on every affected item — see the item shape in Step 7 below.
- **Re-score mode ("refresh heat", "re-score my prospects", "update intent"):** no new discovery — refresh `heat` / `intent_feeds` on the accounts already in `content/<active>/prospects/latest.json` against *today's* intent, then re-rank Tier-A. Reuses this skill's intent fetch + heat axis; skips discovery, gating, enrichment, and outreach drafting. Full procedure in `references/heat-rescore.md`. Near-zero cost (intent checks are credit-free). Use after a tracked-topic change, for a periodic heat refresh, or to apply the scored heat axis to older runs.

## Workflow

**Step 1 — Init, mode, & exclude set.** Confirm the mode (§Modes above) — **bulk mode** if the operator stated a large target the standard intake can't reach, **standard mode** otherwise — and state which in the run header. The output folder is always `content/<active>/prospects/` (create if absent). Build the **60-day exclude set** from prior run files in that folder (`prospects-*.md`, `prospects-*-hubspot.csv`): any company published <60 days ago is excluded. Also read `content/<active>/prospects/latest.json` if it exists — any account with `status: contacted|qualified|disqualified` is excluded from this run (respects agent and dashboard edits between runs). If an account tracker spreadsheet exists in the folder, also exclude its worked accounts and offer to append new rows at the end. **Pick the data path:** discovery **defaults to Vibe** whenever `tools_metered` includes it — actually attempt a Vibe call (e.g. `estimate-cost` or a small `fetch-entities` probe) before falling back; do not skip straight to the web-search path because the run is small, scoped, or narrow. Fall back to web only when that attempt fails or Vibe is confirmed disconnected, and **state the specific reason** in the run header (not just "unavailable"). **A null/empty result from RocketReach `company_search` is not a discovery result and does not satisfy this requirement** — that tool checks signals on a candidate you already have, it doesn't generate candidates; if it comes back empty, you still owe Vibe an attempt before web. Contact enrichment via **RocketReach** if connected, else Vibe `enrich-prospects`, else public web (unverified). Note in the run header which sources are live and, for any fallback, why.

**Step 2 — Budget pre-check (only if using a metered source).** Read budget caps. Before any fetch/lookup, estimate spend across **both** metered sources — Vibe `estimate-cost` / credit balance **and** the RocketReach metered-unit count against its plan quota — and show it. The monthly cap and what it covers come from PROFILE (Claude Max is a flat brain seat, not metered here; signal searches are credit-free on both sources); if the run would breach `per_run_cap_usd` or the remaining monthly budget, trim (drop optional signal layers, then enrichment depth) or fall back to the web path — never silently overspend. If Vibe balance < ~200 credits or RocketReach is near its plan quota, tell the user before spending — never auto-purchase.

**Step 3 — Cold discovery.** One enterprise pass + one startup pass + one **in-market pass** (topic-intent filter) **per market**, plus the credit-free **RocketReach signal pre-flag pass** when signal search is available (paste-ready filters in `discovery-and-budget.md`). On the web path, build the candidate list by searching for ICP-matching companies per segment + market + the website keywords. Aim for a healthy candidate pool (≈2–3× the target) to survive gating. **Bulk mode:** skip this step's per-pass intake — follow `discovery-and-budget.md` §"Bulk mode" steps 1–6 instead (size → in-query filter passes → cost gate → export → `python -m gtm_core.prospects_import ingest`), which produces `candidates-<run-id>.json` in place of a Step 3 candidate list.

**Step 4 — "Why now" signal hunt (0 credits).** Run the fixed 6-source web sweep per candidate — the intent/trigger feeds pre-flag, the sweep **confirms and dates** (the 🔥 cites the public source, never the feed). Tag each hit `[type | date | URL | H/M/L]`. Keep the strongest as the 🔥 signal; it must map to an ICP "why now" trigger. When the account maps to a vertical pack, prefer its **"Why now — urgency drivers"** + **"Regulatory & compliance landscape"** — they name the specific dated instruments (e.g. a named runtime-governance mandate for that industry) that count as a why-now, so you qualify on the real industry trigger rather than a generic guess. **Drop** candidates with no dated hit (<90 days enterprise / <18 months startup) or that fail a gate.

**Step 5 — Gate & score.** Apply the **profile's** segment gates, then its rubric (from `icp-personas.md` / its linked scoring file). Drop anything below the profile's publish threshold (default ≥6; the profile may set its own per-segment threshold + ceiling). **Heat axis:** after the rubric, add **+2** for a topic-intent **score ≥75** on either feed — Vibe row scores are inline (`business_business_intent_topics`); for RocketReach/Intentsify read the optional weekly snapshot `content/<active>/prospects/intent/rr-intentsify-latest.json` when present and <10 days old, else use the credit-free `intent`-facet filter hit — and **+1 more** when both converge (**double-intent**); a score 60–74 is elevated but earns no points. Cap at the rubric ceiling (`gates-and-scoring.md` §Heat axis). Record `heat` and `intent_feeds`. Select the run mix across markets per `segment_mix`; order the Tier-A queue by heat → 🆕 new-in-role → signal recency. Aim ≥3 Tier-A; if short, note "broaden next run."

**Step 6 — Persona enrichment.** For each finalist, identify the segment personas (`profiles/<active>/knowledge/icp-personas.md`). **Contact resolution → RocketReach first** (when connected): resolve the top-1 persona's **verified email + direct phone** via `LookupProfile`/`LookupProfileAndCompany` (or `BulkLookup` for the finalists in one call). RocketReach **lookups are unlimited; person *exports* are the metered quota** — spend an export only to pull a finalist's contact, never a candidate's, and pace against the plan's remaining export balance. **Vibe** supplies firmographics + top-2 persona profiles + company intent, and is the **enrichment fallback** if RocketReach returns no hit; `enrich-business` only as an escape hatch (≤3/run). On the **web path** (no paid source): pull names/titles from public LinkedIn / company pages and mark emails **unverified**. An account is complete with ≥1 champion/primary-buyer contact. Run the **new-in-role check** on finalist personas (`job_change_signal` ≤3 months, or Vibe `current_role_months` 1–6, credit-free): mark hits 🆕 — they jump the Tier-A queue and take the hook matrix's new-in-role column.

**Step 7 — Generate outputs** (all under `content/<active>/prospects/`):
- `prospects-YYYYMMDD.md` — header + one section per account (per-account template). Pull the **recommended opening hook** from the hook matrix (don't free-write); map a case study from `profiles/<active>/knowledge/case-studies.md` — when the account is in a vertical with an industry pack, prefer that pack's **"Matching proof shape"** for the proof and seed the hook from its **"Email angles"** (industry-level `[bracketed]` fills only, never account-specific).
- `prospects-YYYYMMDD-hubspot.csv` — one row per contact, per the CSV map.
- For **each Tier-A (🔥)** account: `prospects-YYYYMMDD-outreach-[company-slug].md` using the Tier-A pack template — a pre-drafted **5-touch / 2-channel arc** (email + LinkedIn over ~12 days) threading **4–6 personas** with role-differentiated first lines. **Read `profiles/<active>/knowledge/voice.md` first**; every line must pass the voice rules. These are drafts — never auto-send.
- **Nurture split (5/95):** fit-but-cold accounts (Tier-B, heat 0) get **no meeting-ask sequence** — list them in the run file under "Nurture" with a suggested monthly no-ask value touch (give-first artifact, LinkedIn presence). A later signal promotes them into a sequence.
- **Refresh the outreach log** — after writing this run's outreach pack(s), regenerate the cross-run rollup so there's always one place to see everything drafted:
  ```bash
  python -m gtm_core.outreach_log build --profile <active>
  ```
  This parses every pack under `content/<active>/accounts/*/prospects-*outreach-*.md` (this run's and all prior ones) and rewrites `content/<active>/prospects/outreach-log.md` + `.csv` — date, account, tier, persona, verified email, subject, channels, path back to the full pack. Idempotent and cheap (0 credits, no LLM call); safe to run even if this run produced zero Tier-A packs.
- If appending to a local tracker spreadsheet, add the run's rows now.
- **`content/<active>/prospects/latest.json`** — **MERGE this run's accounts in; never overwrite the file.** `latest.json` is the **cumulative** dashboard-state file: it holds every prior run's accounts *and* the operator's between-run `status` edits (contacted/qualified/disqualified). Writing only this run's items destroys all of that (a real incident — 2026-07-19). **Do not hand-write this file.** Build a JSON array of this run's item objects (shape below) and merge it through the safe writer, which snapshots the current file first, upserts by company (keeping existing `status`/operator fields), and writes atomically — merge-only, so it can never shrink the cumulative file:
  ```bash
  python -m gtm_core.prospects_state merge --profile <active> \
    --items <path-to-this-run-items.json> --source-run <run-id>
  ```
  **Bulk mode:** run `python -m gtm_core.prospects_import finalize --profile <active> --items
  <scored-items.json> --source-run <run-id>` instead — it calls the exact same safe merge-only writer
  under the hood and additionally emits `prospects-<run-id>-hubspot.csv` in one step, so bulk mode
  doesn't need a separate CSV-writing pass.
  Each item object:
  ```json
  {
    "id": "<company-slug>",
    "company": "<company name>",
    "segment": "enterprise|startup",
    "market": "<market>",
    "tier": "A|B",
    "score": <number>,
    "why_now": "<one-line signal>",
    "qualification_path": "<optional — set only when a gate was relaxed, e.g. 'intent-only-relaxed'; omit under a normal full-gate qualification>",
    "contact_name": "<primary contact>",
    "contact_title": "<title>",
    "status": "new",
    "priority": "<high|medium|low>",
    "heat": 0,
    "intent_feeds": ["vibe-topic|rr-intent|rr-news|rr-jobs"],
    "new_in_role": false
  }
  ```
  `id` = company name lowercased, non-ASCII stripped, spaces to hyphens (e.g. `acme-corp`). Tier-A accounts get `priority: high` by default. `heat` is 0–3 per the heat axis; `intent_feeds` lists which feed(s) fired (empty array if none); `new_in_role` is true when a 🆕 champion/economic buyer was found. If the merge is ever wrong, recover with `python -m gtm_core.prospects_state restore --profile <active>` (newest snapshot) — snapshots live in `content/<active>/prospects/.snapshots/`.

  **Bulk mode's items carry a richer, optional superset** — because `prospects_import finalize` derives the HubSpot CSV from the *same* item objects (see `references/hubspot-csv-map.md` for what each maps to): `contact_email` (blank/omit if unverified — never guess), `contact_phone`, `contact_linkedin_url`, `domain`, `city`, `employees_range` (e.g. `"5001-10000"`, midpointed automatically) or a precomputed `employees_number`, `persona_tier` (e.g. `Champion`), `case_study`, `gtm_source` (`Cold` / `Warm-Event: <name>`, defaults to `Cold`), `top_intent_score` (raw 0–100, behind the bucketed `heat`), `intent_topics` (`[{"topic": "agentic ai", "score": 86}, ...]` — `candidates-<run-id>.json` already carries this from ingest; which signal(s) actually fired, not just the heat bucket), `industry`, `revenue_range`. Standard mode's `latest.json` items don't need these — extra keys merge through harmlessly (the writer treats items as plain dicts) — but bulk mode should populate what it has, since `candidates-<run-id>.json` already carries `domain`/`city`/`employees_range`/`industry`/`revenue_range`/`top_intent_score`/`intent_topics` from ingest.
- **`content/<active>/prospects/runs/<run-id>.json`** — per-run manifest:
  ```json
  {"run_id":"<run-id>","profile":"<active>","kind":"prospects","started_at":"<ISO>","completed_at":"<ISO>","discovery_path":"vibe|web","total_accounts":<N>,"tier_a_count":<N>}
  ```
- Record in the ledger:
  ```bash
  python -m gtm_core.ledger_cli append-history --profile <active> \
    --json '{"event":"prospect_run","skill":"prospect","run_id":"<run-id>","total":<N>,"tier_a":<N>,"path":"<vibe|web>","snapshot":"content/<active>/prospects/latest.json"}'
  python -m gtm_core.ledger_cli write-run-manifest --profile <active> \
    --json '{"run_id":"<run-id>","trigger":"scheduled","stages":[{"name":"prospect_run","status":"ok","outputs":["content/<active>/prospects/prospects-YYYYMMDD.md","content/<active>/prospects/latest.json"]}]}'
  ```
  For each metered source used, append a cost row:
  ```bash
  python -m gtm_core.ledger_cli append-cost --profile <active> \
    --json '{"tool":"vibe-prospecting","skill":"prospect","cost_usd":<usd>,"run_id":"<run-id>"}'
  # RocketReach: lookups are unlimited; log the run's person EXPORTS (the metered unit) and their $ share of the plan
  python -m gtm_core.ledger_cli append-cost --profile <active> \
    --json '{"tool":"rocketreach","skill":"prospect","cost_usd":<usd>,"units":{"exports":<n>},"run_id":"<run-id>"}'
  ```

**Step 8 — QA & close.** Run the §8 checklist in `output-templates.md`. Report a short summary: counts, Tier-A count, discovery path, spend vs cap, and the file names produced. Offer to draft/refine outreach (`draft-outreach`) or to schedule the weekly run.

## Guardrails

- **Budget is a hard stop**, not a warning: read `monthly_tool_budget_usd` + `per_run_cap_usd` from PROFILE — its budget comments define what the cap covers (RocketReach is a monthly subscription; Vibe sells one-time credit packs with 365-day validity; Claude Max is a flat brain seat outside the cap). Estimate before metered calls; trim or fall back rather than breach the cap; never auto-buy credits or exports.
- **Spend the scarce unit sparingly:** RocketReach lookups are unlimited but **person exports are finite** — export a contact only for a scored finalist, never a candidate.
- **Never echo the API key:** RocketReach auth is `ROCKETREACH_API_KEY` (Doppler-injected env); the key value never appears in a file, ledger, output, or chat.
- **Provenance travels:** every account carries its dated 🔥 signal + URL into the brief and the outreach pack.
- **Drafts only:** outreach is never sent from this skill.
- **Portable & private:** no live CRM; outputs are local files; no secret is read from or written to any file.
- **Market-aware:** everything keys off PROFILE `target_markets` — never hardcode geographies.
