# Prospect — Discovery, enrichment, budget & the web-search fallback

> From routine v2.3 §4, §5, §6, §11, §12 (v0.3.0: two data sources + intent fusion). Both paid sources are **optional** — if neither is connected, run the web-search fallback (this file's last section). If a metered source is connected, **estimate cost first and respect the PROFILE budget caps before any metered call.**
>
> **Discovery defaults to Vibe when it's connected — attempt it every run, including scoped/narrow
> cohort runs**, given the large unused budget headroom (`per_run_cap_usd` vs typical spend, §"Budget
> guardrails" below). Web-search discovery is the **true fallback only** — used when Vibe is genuinely
> disconnected or erroring, never chosen for session expedience because a run is small or
> single-vertical. If a run falls back to web for discovery while Vibe was connected, **state the
> specific reason** in the run header (a failed call, a null/empty result, a confirmed disconnect) —
> not just "unavailable" or "narrow cohort."

## Two data sources — who does what, and how to toggle

| Job | Primary | Fallback |
|---|---|---|
| Cold ICP **company discovery** + firmographics | **Vibe Prospecting** (`fetch-entities`) — attempt on every run where Vibe is connected, scoped/narrow cohorts included | web search, only after a genuine failed/empty attempt or a confirmed disconnect |
| **Topic buyer-intent** — in-market accounts | **Vibe** `business_intent_topics` filter (Bombora, weekly) **and** RocketReach tracked topics / `intent` facet (Intentsify, weekly) | — (flag as absent; run fit-only) |
| **Trigger signals** — news / hiring / events | **RocketReach** `news_signal` + `job_posting_signal`; **Vibe** `events` | web sweep discovers instead of confirms |
| **Person timing** — job changes / tenure | **RocketReach** `job_change_signal`; **Vibe** prospect events + `current_role_months` | — |
| **Contact resolution** — verified email + direct phone | **RocketReach** lookup (single / bulk) | Vibe `enrich-prospects` → public web (unverified) |
| "Why now" dated signal | web sweep (Step 4, 0 credits) — **confirms and dates** what the feeds pre-flag | — |

**Heat axis (the point of running both):** the rubric scores *fit* (who); intent scores *heat* (when). Both platforms' topic-intent feeds are **company-level** — Vibe's is Bombora topics, RocketReach's is Intentsify tracked topics (the old "RocketReach = person intent" framing was wrong; its person-level signal is job changes). After the rubric: **+2** when either feed shows high topic-intent on the profile's topic list; **+1 more** when both feeds converge on the same account (**double-intent** — the strongest "why now" available); cap at the rubric ceiling (`gates-and-scoring.md` §Heat axis). Record `heat` (0–3) and `intent_feeds` (e.g. `["vibe-topic","rr-intent","rr-news"]`) per account in `latest.json`. **Never quote intent data in outreach copy** — it times the touch and picks the angle; the message itself cites only public signals.

## Vibe Prospecting — tools & where they're used

Discovery + enrichment engine (OAuth connector "Vibe Prospecting"; an Explorium product). Tools seen in the registry: `autocomplete`, `fetch-entities`, `fetch-entities-statistics`, `fetch-businesses-events`, `enrich-prospects`, `enrich-business`, `estimate-cost`, `export-to-csv`. Reserve Vibe for **cold ICP discovery + the in-market pass** (Step 3), **company topic-intent + events**, and **fallback persona enrichment** (Step 6). Signal-only fetches are dropped — web search handles "why now" (Step 4) at zero credit cost.

**Topic-intent mechanics (Bombora):** `business_intent_topics` is a `fetch-entities` filter — object form **`{topics: [...]}`** (topic strings only; the old `topic_intent_level` key is **rejected** as of 2026-07-18 — do not send it). Topic strings MUST come from `autocomplete` (field `business_intent_topics`); take the list to run from the profile's `market-scan-config.md` → "Intent topics" section (~12 topics), re-verifying against autocomplete on the first run each month (the taxonomy drifts). The filter returns every company surging on **any** of the topics, and **each row carries the per-topic scores inline** — `business_business_intent_topics` is a JSON array of `{topic, score}` (0–100). Use the real number for heat: **score ≥75 = high intent (+2)**, 60–74 = elevated. Filtering/preview costs nothing; the usual export pricing applies to rows you export.

## RocketReach — contact resolution + company intent, trigger signals & job-change timing

Auth: `ROCKETREACH_API_KEY` (Doppler-injected — **never** hardcode or echo the key). Two capability groups, and two tool surfaces that may expose them:

**1) Contact resolution** — person lookup (single / bulk). **Quota model:** lookups are effectively unmetered; **person *exports* / premium lookups are the finite unit** (a plan allotment — check the remaining balance before a run). Look up freely to confirm a person exists, but **spend the metered unit only on a scored finalist's contact**, never a candidate's; bulk-resolve all finalists in one call. When the balance runs low, tell the colleague before the run — never auto-purchase.

**2) Signal search (credit-free — searches never consume lookups/exports):**
- **Topic intent** (`intent` on company search / `company_intent` on person search) — **company-level** Intentsify topics, scored 0–100 weekly in the web app (≥75 = high intent). Two prerequisites: a team admin must have **set the tracked topics** in the web app (≈6 active, changeable 3×/yr — mirror the profile's intent-topic list), and the API/MCP exposes intent as a **search facet only** (filter by topic; scores aren't returned) — the weekly ≥75 list lives in the app's Intent Data tab.
- **News triggers** (`news_signal` / `company_news_signal`) — `"Category::window"` strings, windows `one_week|one_month|three_months|six_months|one_year`. Highest-value categories for this routine: `Executive Hire`, `Executive Departure`, `Funding`, `Vulnerability`, `Launches Product`, `Mergers & Acquisitions`.
- **Hiring triggers** (`job_posting_signal` / `company_job_posting_signal`) — `"<Department> Roles::window"`, e.g. `"Engineering Roles::three_months"`, `"Machine Learning Roles::three_months"`.
- **Job-change timing** (`job_change_signal`, person search) — `"Company Change::three_months"` or `"Promotion::three_months"` (windows cap at three_months) — powers the new-in-role check (Step 6).

**Surface note:** both tool surfaces expose both groups — the official RocketReach MCP/connector as `person_search` / `company_search` / `person_lookup`, the in-repo VPS worker (`agent/mcp/rocketreach`) as `rocketreach_person_search` / `rocketreach_company_search` / `rocketreach_lookup` / `rocketreach_bulk_lookup`. Tool names differ per surface; call whichever the session offers (facet names are identical). If no signal-search tool is present at all, skip the RocketReach signal pass, run Vibe events + the web sweep as usual, and note "RR signals: unavailable" in the run header. Never substitute raw HTTP.

> **`company_search` is a signal lookup on a candidate you already have — not a discovery tool, and its
> emptiness is not a discovery result.** It exists to check facets (topic intent, news/hiring triggers)
> on a named company, not to generate the candidate list. A null/empty `company_search` response means
> *this candidate has no RocketReach-visible signal* — it says nothing about whether Vibe discovery has
> been tried, and it does **not** satisfy the "attempt Vibe" requirement in Step 1. Falling back to web
> because `company_search` returned nothing is the same violation as skipping Vibe outright: check the
> actual discovery tool (Vibe `fetch-entities`) before web, every time, regardless of what RocketReach
> did or didn't return.

## Budget discipline (do this before any metered call)

- Read `monthly_tool_budget_usd`, `per_run_cap_usd`, `tools_metered` from PROFILE — the PROFILE budget comments are the source of truth for what the cap covers and current plan shapes; never hardcode prices here. Claude Max is the flat brain seat — **not** metered here.
- Estimate spend across **both** sources **before** running fetches/enrichment: Vibe `estimate-cost` / credit balance **and** the RocketReach metered units the run would consume. Show it. If the run would exceed `per_run_cap_usd` or the remaining monthly budget, **stop and trim** (drop §"optional signal layers", then contact-enrichment on the bottom accounts) rather than silently spending.
- **Vibe credit model:** one-time credit **packs** with 365-day validity (not a monthly quota) — the pack purchase is logged to the cost ledger when it happens; runs then draw down the balance. Target **≤100 credits/run**. When the balance runs low, tell the colleague before the next run — never auto-purchase.
- **RocketReach model:** flat monthly subscription; the constrained unit is **exports / premium lookups**, not searches — budget ≤1 per finalist (≤10/default run). Searches and every signal filter (intent, news, job-posting, job-change) are **credit-free**, so the intent/trigger layer adds ~0 marginal cost.
- **Per-run Vibe ledger (target ≤100 credits):** autocomplete (amortized) ~1 · 4–6× fetch-entities @20–30 (fit + in-market passes) ~50 · events fetch ~10 · fallback enrichment ~30–60 · up to 3 enrich-business escape hatches ~15 · export ~5. If a run trends over ~120, drop fallback enrichment (RocketReach already owns contacts), then enrich-business entirely. Fetches return free masked previews — credits are spent on export.

## Filter library

### Enterprise filters
| Filter | Value |
|---|---|
| `entity_type` | `businesses` |
| `company_size` | `["1001-5000", "5001-10000", "10001+"]` |
| `company_revenue` | `["200M-500M", "500M-1B", "1B-10B", "10B-100B", "100B-1T"]` |
| `is_public_company` | `null` (include public + private) |
| `has_website` | `true` |
| `company_country_code` | one ISO code per market pass (from PROFILE `target_markets`) |
| `linkedin_category` | populate from autocomplete (industries below) |
| `website_keywords` | `["AI agent", "agentic", "agent governance", "AI governance", "MCP", "verifiable credentials"]` |
| `events` (optional, sparing) | `["new_funding_round", "merger_and_acquisitions", "outages_and_security_breaches", "employee_joined_company", "new_partnership"]`, last 90 days |
| `number_of_results` | `20` |

### Startup filters
| Filter | Value |
|---|---|
| `entity_type` | `businesses` |
| `company_size` | `["11-50", "51-200", "201-500"]` |
| `company_revenue` | `null` (use funding events instead) |
| `is_public_company` | `false` |
| `has_website` | `true` |
| `company_country_code` | one ISO code per market pass (from PROFILE) |
| `linkedin_category` | autocomplete: "software development", "artificial intelligence", "computer software", "internet" |
| `website_keywords` | `["AI agent", "agentic", "MCP", "A2A", "AP2", "verifiable credentials", "agent identity"]` |
| `events` | `["new_funding_round"]`, `last_occurrence: 90` (API cap) — primary startup discovery filter; optional second pass: `["new_product", "increase_in_engineering_department"]` |
| `number_of_results` | `30` |

### In-market pass (topic intent — run per market, alongside the fit passes)

| Filter | Value |
|---|---|
| `entity_type` | `businesses` |
| `business_intent_topics` | `{topics: [<~12 from the profile's market-scan-config.md → "Intent topics">]}` — topic strings via `autocomplete`; **no `topic_intent_level` key** (rejected). Read per-topic scores from each row's `business_business_intent_topics`; treat ≥75 as high intent |
| `company_country_code` | one ISO code per market pass |
| `company_size` | segment floors still apply — run with enterprise sizes; repeat with startup sizes if candidates run short |
| `has_website` | `true` |
| `number_of_results` | `30` |

In-market hits still clear the gates and rubric like any other candidate — the pass changes *where candidates come from*, and the topic-intent hit sets the heat axis (+2). Companies surfacing in **both** a fit pass and the in-market pass are prime Tier-A material.

### RocketReach signal pre-flag pass (credit-free; skip if signal search unavailable)

One `company_search` per market with the segment size floor plus, in separate calls: `news_signal: ["Executive Hire::three_months", "Funding::three_months", "Vulnerability::six_months", "Launches Product::three_months"]` and `job_posting_signal: ["Engineering Roles::three_months", "Machine Learning Roles::three_months", "Information Technology Roles::three_months"]`. Cross-reference hits against the candidate pool: a match pre-flags that account's trigger for Step 4 (the web sweep **confirms and dates** it) and counts toward `intent_feeds` (`rr-news` / `rr-jobs`). Also cross-check finalists against the `intent` facet (topics from the profile's tracked list) → `rr-intent`.

### RocketReach Intentsify — the two-tier intent path (weekly-refresh reality)

Intentsify scores **weekly** and the API is **filter-only** (a topic match, never a score). So the automated skill uses it in two tiers, and **Vibe/Bombora is the primary automated intent feed** (scored + fresh per run); Intentsify is the corroborating second feed for double-intent, not the workhorse.

- **Tier 1 — always-on, zero-touch (API filter):** the `intent` / `company_intent` facet cross-check above. Credit-free, headless, but boolean and up to a week stale. Sets `rr-intent` true/false. This is the baseline — no file, no human.
- **Tier 2 — weekly scored snapshot (optional, higher fidelity):** the ranked ≥75 list with scores lives only in the web app's **Intent Data** tab (no API path). If the profile maintains a weekly capture at **`content/<active>/prospects/intent/rr-intentsify-latest.json`**, read it at Step 5 and match candidate **domains** against it: a hit carries the real **score** → `rr-intent` with `score ≥75 = +2` (per `gates-and-scoring.md` §Heat axis). Shape:
  ```json
  {"source":"rocketreach-intentsify","week_of":"YYYY-MM-DD","topics":["<tracked topic>", "..."],
   "accounts":[{"domain":"acme.com","top_topic":"AI Agent Security","score":88}]}
  ```
  **Freshness guard (mandatory):** if `week_of` is **>10 days** old (or the file is absent), ignore Tier 2, log `rr-intent: snapshot stale/absent — Tier-1 filter only` in the run header, and fall back to the Tier-1 filter + Vibe. Never let a stale weekly list read as current.

The capture itself is an **operator-side step** (the Intent Data tab is browser-only — it is *not* an MCP tool and *not* a headless egress path): once a week, ideally the morning of the prospect run, export/copy the ≥75 accounts into that file. Because both cadences are weekly, one capture feeds one run. See `intent-signals-catalog.md` for the exact click-path.

### Industry autocomplete (enterprise)
Run `autocomplete` for each, take relevant returns: banking · financial services · insurance · healthcare / hospitals · telecommunications · retail · software development (large platforms) · pharmaceutical.

### Optional signal layers (only if budget allows)
`company_tech_stack_tech` (LangChain, LlamaIndex, MCP) for framework-adopters · `events: hiring_in_engineering_department` for both segments.

### Filter format conventions (verified)
- Multi-value filters (`company_country_code`, `company_size`, `company_revenue`, `linkedin_category`, `website_keywords`, `business_id`, `job_title`, `events`) MUST be wrapped as `{values: [...]}` — bare arrays error with `invalid_type: expected object, received array`.
- Boolean filters (`has_website`, `is_public_company`, `has_email`, `has_phone_number`) are bare booleans.
- `events.last_occurrence` is **capped at 90 days** by the API.
- **Schema drift:** taxonomies (`linkedin_category`, `company_size`, `company_revenue`, `events`) change. Re-run `autocomplete` against actuals on the first run each month; flag values that no longer resolve.

### Paste-ready pass (per market)
```yaml
entity_type: businesses
filters:
  company_country_code: { values: ["<ISO code, e.g. US>"] }
  company_size: { values: ["1001-5000", "5001-10000", "10001+"] }          # startup: ["11-50","51-200","201-500"]
  company_revenue: { values: ["200M-500M","500M-1B","1B-10B","10B-100B","100B-1T"] }  # startup: omit / null
  is_public_company: false                                                  # enterprise: omit (null)
  linkedin_category: { values: [<from autocomplete>] }
  website_keywords: { values: ["AI agent","agentic","MCP","A2A","AP2","verifiable credentials","agent identity"] }
  events: { values: ["new_funding_round"], last_occurrence: 90 }            # startup pass
  has_website: true
number_of_results: 30
exclude_key: <prior run's dataset_id>
```
Run one enterprise pass and one startup pass **per market** in PROFILE.

## Persona enrichment (budget-controlled)

Personas to fetch per account (see `profiles/<active>/knowledge/icp-personas.md` for the why):
- **Enterprise:** Champion = Head of AI Platform; Economic buyer = CISO (alt CIO); Co-signers = CRO/Compliance, Cloud/Platform Architect, FinOps; Influencer = Security Architect.
- **Startup:** Primary buyer = CEO/Founder (pre-Series B) or CPO (Series B+); Co-decider = CTO/Founding Engineer; Influencer = Head of Security/Engineering.

Depth:
- **Contact (email + phone) → RocketReach** for the **top-1 persona per account** (champion-tier / primary-buyer-tier) — one **export** each, finalists only. `BulkLookup` all finalists in one call.
- **Profiles → Vibe `enrich-prospects`** for the **top 2 personas per account** (title/seniority context), and as the **contact fallback** when RocketReach has no hit.
- `enrich-business` — **skip by default**; only if web search can't yield the firmographics needed to score. Cap 3/run.

**New-in-role check (finalists only, credit-free):** for each finalist's champion/economic-buyer personas, check whether the person is new in seat — RocketReach `person_search` scoped to the company with `job_change_signal: ["Company Change::three_months", "Promotion::three_months"]`, or (Vibe path) `current_role_months` 1–6 as a prospect filter. Mark hits **🆕 new-in-role** on the contact and the account: 🆕 accounts jump the Tier-A queue (the conversion premium decays inside ~90 days) and take the hook matrix's new-in-role column instead of the default signal column.

**Acceptance:** an account is "complete" when ≥1 verified contact (champion / primary-buyer tier) has an email — RocketReach-verified where possible, Vibe- or web-sourced (marked unverified) otherwise.

## "Why now" signal hunt — fixed 6-source web sweep (0 credits)

Run the same six sources per candidate, in order. Tag every hit `[type | date | source URL | strength H/M/L]`:
1. **Newsroom / PR** — `"{company}" (agentic OR "AI governance" OR "agent identity")`
2. **Hiring (= building)** — `"{company}" ("AI platform" OR agent OR "ML platform") site:linkedin.com/jobs`
3. **Eng signal** — GitHub org + engineering blog → MCP / A2A / framework adoption
4. **Regulatory / standards** — earnings call + regulator/standards participation (e.g. MAS, IMDA, W3C, DIF; adapt regulators to the market)
5. **Funding (startup)** — `"{company}" (raised OR "Series")` — within 18 months
6. **Pressure / incident** — breach · audit finding · EU AI Act mention · enterprise-deal stall

Keep the single strongest hit as the 🔥 signal — it must map to an ICP enterprise/startup "why now" trigger. **Drop** the candidate if no dated hit <90 days (enterprise) / <18 months (startup), or if it fails a gate Step 3 couldn't catch. Write the 🔥 line + date + URL into the output — provenance travels into outreach. If intent/trigger feeds are live (Vibe `events` + topic-intent, RocketReach news/job-posting signals), let them pre-flag sources 1, 2, 5 & 6 so web search **confirms and dates** rather than discovers. The 🔥 line always cites the public source, never the feed.

## Web-search fallback (when Vibe genuinely isn't usable this run)

**Before reaching for this section, actually attempt Vibe discovery** (an `estimate-cost` call or a
small `fetch-entities` probe) — don't skip straight here because the cohort is small, narrow, or a
single vertical; "the run is scoped" is not a qualifying reason on its own. This section applies only
when that attempt fails, returns nothing usable, or Vibe is confirmed disconnected for the session —
and the run header must say which of those three it was, not just "unavailable."

Vibe substitutes are all free:
| Vibe would provide… | Substitute with… |
|---|---|
| Cold ICP discovery (`fetch-entities`) | Web search for ICP-matching companies by segment + market + the website_keywords above; build the candidate list manually |
| Community / engagement signal | GitHub org activity, conference talks, OSS PR history |
| Content / intent | Press releases, earnings transcripts, exec blogs, HN/Reddit threads |
| Job-posting signal | LinkedIn / Lever / Greenhouse job-post search |
| Persona contacts | Public LinkedIn + company "team"/"about" pages for names/titles; mark emails as **unverified** (no paid enrichment) |

The fallback is more labour-intensive but costs nothing — fine for the budget. Flag in the run header
that it was a web-search-only run, **why** Vibe wasn't used (failed attempt / empty result / confirmed
disconnect), and that contacts are unverified.
