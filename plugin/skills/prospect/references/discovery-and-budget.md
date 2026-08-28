# Prospect — Discovery, enrichment, budget & the web-search fallback

> From routine v2.3 §4, §5, §6, §11, §12 (v0.3.0: two data sources + intent fusion; v0.4.0 adds bulk
> mode, §"Bulk mode" below; v0.6.0 adds Apollo as a third source — §"Apollo" below). All paid sources
> are **optional** — if none is connected, run the web-search fallback (this file's last section). If
> a metered source is connected, **estimate cost first and respect the PROFILE budget caps before any
> metered call.**
>
> **Discovery defaults to Vibe when it's connected — attempt it every run, including scoped/narrow
> cohort runs**, given the large unused budget headroom (`per_run_cap_usd` vs typical spend, §"Budget
> guardrails" below). Web-search discovery is the **true fallback only** — used when Vibe is genuinely
> disconnected or erroring, never chosen for session expedience because a run is small or
> single-vertical. If a run falls back to web for discovery while Vibe was connected, **state the
> specific reason** in the run header (a failed call, a null/empty result, a confirmed disconnect) —
> not just "unavailable" or "narrow cohort."

## Standard mode vs bulk mode — which one this run uses

This file documents **two modes** over the same two data sources. Pick one at the top of the run and
say which in the run header:

| | **Standard mode** (default) | **Bulk mode** (opt-in) |
|---|---|---|
| When | routine weekly ~10-account run, or any target the ~30-row `fetch-entities` intake can plausibly reach | operator states an explicit large target (e.g. "300 accounts") that standard mode's ~20–30-row-per-pass intake cannot reach |
| Candidate intake | `fetch-entities` rows treated directly as the candidate pool (≤30/pass) | `export-to-csv` materializes a much larger qualified slice to a file; the skill scores it from disk in batches |
| Qualification | web sweep runs per-row, before the set is finalized | firmographic + why-now gating happens **in-query** (Vibe filters); the web sweep only confirms/dates Tier-A finalists |
| Section to follow | everything below except "Bulk mode" | §"Bulk mode" below, using the same filter library + budget discipline |

Bulk mode is **additive** — it does not replace or change standard-mode behavior. If the operator
hasn't stated a large target, use standard mode.

## Bulk mode (target-driven large runs)

Bulk mode exists because standard mode's intake is arithmetically capped: `fetch-entities` returns at
most ~30 rows/pass, and the per-row web sweep that qualifies them burns the shared WebSearch quota
long before a 200–300 target is reached (this was diagnosed after the 2026-07-19 run delivered 77 of
a 300 target). Bulk mode fixes the *funnel*, not the judgment — scoring, tiering, and why-now stay the
skill's call; only firmographic/heat filtering and set materialization move into Vibe's query layer.

**8-step flow:**

1. **Size first (free).** Run `fetch-entities-statistics` per ICP×market cell. Report the pool size in
   the run header up front — if the qualified pool is smaller than the target, say so before running
   anything else, not as an end-of-run surprise.
2. **Two filter passes, both defined in-query:**
   - **Coverage pass** = the segment's firmographic filters (§Filter library below) **plus a why-now
     `events` trigger** (`new_funding_round`, `hiring_in_engineering_department`, etc., ≤90 days per
     the API cap). This pass drives the count; topic-intent is **not** a gate here.
   - **Heat pass** = the same firmographic filters **plus** `business_intent_topics`. Marks in-market
     accounts; the inline per-topic score (≥75) sets heat, exactly as in standard mode's in-market
     pass.
3. **Preview + cost estimate.** Take the free 5-row preview per cell to sanity-check the filters and
   read `database_total`, then call `estimate-cost`. Auto-scale the export size to roughly
   `target × 1.5–2` (accounting for expected attrition through gating), prioritized by intent score
   where available. **Gate on `estimate-cost` vs the PROFILE `per_run_cap_usd` $ cap before any
   export** — same §Budget discipline rule as standard mode, just applied to a bigger number.
4. **Exclude in-query.** Apply the net-new exclude set via `exclude_key` (the prior run's `dataset_id`)
   or `get-dataset`, so exclusion happens in Vibe's database, not as a post-hoc filter on the exported
   rows.
5. **Materialize (autonomous — no operator in the loop).** Call `export-to-csv` for the qualified
   slice, then download it with the host-pinned fetcher:
   ```bash
   uv run python -m gtm_core.dataset_fetch --profile <active> \
     --url "<_full_download_url from the export response>" --name <cell-slug>
   ```
   or, for a whole bulk run, one call over a manifest
   (`{"downloads":[{"name":"…","url":"…"}, …]}` written to
   `content/<active>/prospects/imports/manifest-<run-id>.json`):
   ```bash
   uv run python -m gtm_core.dataset_fetch --profile <active> \
     --manifest content/<active>/prospects/imports/manifest-<run-id>.json
   ```
   Files land in `content/<active>/prospects/imports/<name>.csv`, ready for step 6's `ingest`.
   **Four rules that are not optional:**
   - **`dataset_name` accepts only `[a-z0-9_]`.** A hyphen — the natural separator for a
     `<profile>-<cell>-<date>` slug — is rejected outright
     (`Dataset name must contain only lowercase letters, numbers, and underscores`). Use underscores.
   - **Always pass `_full_download_url`, never `_core_download_url`.** The core export drops
     `business_business_intent_topics` and `business_domain` — i.e. the entire heat axis and the
     dedup key — and does so silently. A run built on the core CSV scores every account heat 0 and
     looks fine.
   - **`export-to-csv` share links are single-use.** A link already consumed (e.g. by a manual
     browser download) returns HTTP 400 from the storage bucket. Download each link once, from the
     fetcher; if you need the rows again, re-export — and see the dedup rule below, because in the
     same session you often *can't*.
   - **Export the whole table in one call.** Full-table exports up to at least 260 rows succeed
     (verified 2026-08-12). If one times out, it is transient — retry once before splitting. Only
     if it repeatedly fails, page with `limit` + `exclude_key: <prior batch's dataset_id>`, which
     returns the *next* N rows rather than the same ones. Prefer one call: each batch is a separate
     dataset, a separate link, and a separate row in the manifest.
   - **Vibe dedupes exports per session, and the reused response carries NO download URL.**
     Re-exporting a table you already exported this session returns `already_exported: true`, the
     *original* (possibly smaller) dataset, and no `_full_download_url` — so the rows are
     unreachable. It also does not re-charge. Practical consequence: **download each export
     immediately after creating it.** If a table's links are already spent, you cannot re-export it —
     re-run `fetch-entities` to build a *fresh* table (new `table_name`) and export that.

   `gtm_core.dataset_fetch` is fail-closed: it refuses any host outside its hardcoded allowlist
   (Vibe/Explorium + RocketReach) and refuses a redirect that leaves it. If it refuses a host, that
   is the guard working — report the host, do not route around it with shell (`curl`/`wget` are
   denied by design) and do not substitute WebFetch, which summarises the page through a model and
   would silently fabricate rows. Boundary detail: CLAUDE.md §Egress, SECURITY-SELF-ASSESSMENT DR-02.

   *Legacy fallback (only if the fetcher is unavailable):* the operator forwards the downloaded file
   into the cockpit (`cockpit/ingress.py:on_document` saves it to
   `content/<profile>/prospects/imports/<stamp>-<hash>.csv`), or drops it there manually.
6. **Ingest.** Run `python -m gtm_core.prospects_import ingest --profile <active> --csv <path>
   --source-run <run-id> [--exclude <exclude.json>]`. This parses the CSV, derives heat from the
   inline intent scores, dedups in-file and against the exclude set, writes
   `content/<profile>/prospects/imports/candidates-<run-id>.json`, and **logs the export's credit
   cost to `costs.jsonl`** (Vibe doesn't self-meter, unlike the in-repo RocketReach/vision workers —
   this call is the only place the spend gets recorded).
7. **Score in the skill, from the file, in batches.** Read `candidates-<run-id>.json` and apply the
   rubric (`gates-and-scoring.md`) — this stays LLM judgment, never mechanical. Heat is already on
   each candidate from ingest; the firmographic gate is already satisfied by the query filters, so
   scoring here is fit + why-now + tier, not re-deriving heat. **Enrich finalists only**: RocketReach
   export for Tier-A verified contacts, Vibe `enrich-prospects` bulk for Tier-B, and the web sweep
   (§"Why now" below) confirms/dates why-now for **Tier-A only** — this is what removes the per-row
   WebSearch bottleneck that capped the 2026-07-19 run.
8. **Merge to state.** Run `python -m gtm_core.prospects_import finalize --profile <active> --items
   <scored-items.json> --source-run <run-id>` — this merges the scored items into `latest.json` via
   the safe, snapshot-taking writer (`gtm_core.prospects_state`, same merge-only guarantee as standard
   mode's Step 7) and writes the run's HubSpot CSV in one call.

**Relaxed-gate labeling still applies.** If a bulk run relaxes the why-now gate (e.g. intent-only
qualification, per an explicit operator instruction) exactly as the 2026-07-19 run did, label every
affected account's `qualification_path` accordingly (e.g. `intent-only-relaxed`) — bulk mode changes
*how* candidates are sourced, not the labeling discipline once they're scored.

**Degrade gracefully.** `gtm_core.dataset_fetch` (step 5) makes bulk mode self-sufficient, so this
should now be rare. If the CSV still can't reach disk — the fetcher refuses the host, the provider
changes its download host, or the export itself fails — bulk mode has no way to materialize its
candidate set: fall back to standard mode rather than guessing at a larger `fetch-entities`
`number_of_results` (the MCP wrapper caps inline rows at 5 regardless of what's requested), and never
substitute a summarising fetch for the real rows.

## Three data sources — who does what, and how to toggle

| Job | Primary | Fallback |
|---|---|---|
| Cold ICP **company discovery** + firmographics | **Vibe Prospecting** (`fetch-entities`) — attempt on every run where Vibe is connected, scoped/narrow cohorts included | web search, only after a genuine failed/empty attempt or a confirmed disconnect; **Apollo** `apollo_company_search` is an optional extra in-market pass (1 credit/page — see §Apollo below), never the primary |
| **Topic buyer-intent** — in-market accounts | **Vibe** `business_intent_topics` filter (Bombora, weekly), **RocketReach** tracked topics / `intent` facet (Intentsify, weekly), **and Apollo** buying-intent filters on company search (LeadSift, weekly — tracked topics must be set in Apollo's web UI first) | — (flag as absent; run fit-only) |
| **Trigger signals** — news / hiring / events | **RocketReach** `news_signal` + `job_posting_signal`; **Vibe** `events`; **Apollo** `apollo_job_postings` (hiring signal, 1 credit/page) | web sweep discovers instead of confirms |
| **Person timing** — job changes / tenure | **RocketReach** `job_change_signal`; **Vibe** prospect events + `current_role_months` | — (Apollo does not carry this signal) |
| **Contact resolution** — verified email (RocketReach also direct phone) | **RocketReach** lookup (single / bulk) | Vibe `enrich-prospects` → **Apollo** `apollo_person_enrich`/`apollo_bulk_person_enrich` (email only, never phone — see §Apollo below) → public web (unverified) |
| **Bulk contact resolution** | **RocketReach** `rocketreach_bulk_lookup` (≤25/call) | Vibe `enrich-prospects` bulk → **Apollo** `apollo_bulk_person_enrich` (≤10/call, Apollo's documented batch cap) |
| "Why now" dated signal | web sweep (Step 4, 0 credits) — **confirms and dates** what the feeds pre-flag | — |

**Heat axis (the point of running more than one feed):** the rubric scores *fit* (who); intent scores *heat* (when). All three topic-intent feeds are **company-level** — Vibe's is Bombora topics, RocketReach's is Intentsify tracked topics (the old "RocketReach = person intent" framing was wrong; its person-level signal is job changes), Apollo's is LeadSift topics. After the rubric: **+2** when **any** feed shows high topic-intent (score ≥75, or a tracked-topic filter hit) on the profile's topic list; **+1 more** when **two or more** feeds converge on the same account (**double-intent** — the strongest "why now" available, regardless of which two or three feeds fired); cap at the rubric ceiling (`gates-and-scoring.md` §Heat axis). Record `heat` (0–3) and `intent_feeds` (e.g. `["vibe-topic","rr-intent","apollo-intent"]`) per account in `latest.json`. **Never quote intent data in outreach copy** — it times the touch and picks the angle; the message itself cites only public signals.

## Vibe Prospecting — tools & where they're used

Discovery + enrichment engine (OAuth connector "Vibe Prospecting"; an Explorium product). Tools seen in the registry: `autocomplete`, `fetch-entities`, `fetch-entities-statistics`, `fetch-businesses-events`, `enrich-prospects`, `enrich-business`, `estimate-cost`, `export-to-csv`. Reserve Vibe for **cold ICP discovery + the in-market pass** (Step 3), **company topic-intent + events**, and **fallback persona enrichment** (Step 6). Signal-only fetches are dropped — web search handles "why now" (Step 4) at zero credit cost.

**Topic-intent mechanics (Bombora):** `business_intent_topics` is a `fetch-entities` filter — object form **`{topics: [...]}`** (topic strings only; the old `topic_intent_level` key is **rejected** as of 2026-07-18 — do not send it). Topic strings MUST come from `autocomplete` (field `business_intent_topics`); take the list to run from the profile's `market-scan-config.md` → "Intent topics" section, re-verifying against autocomplete on the first run each month (the taxonomy drifts). **Vibe hard-caps this filter at 20 topics** (`Array must contain at most 20 element(s)`) — a profile whose topic list exceeds 20 **cannot be covered by one query**, so run the profile's documented passes separately and union the results. Do not silently truncate to the first 20: on 2026-08-11 a single hybrid pass concluded "SG enterprise is only 108 companies", when the agentic pass alone returned 85 at 1,000+ and the governance/identity pass returned a further 67 that the agentic pass missed entirely (KPMG Singapore, NHG Health). A single-pass topic query systematically under-counts the market and will read as a market-size fact. The filter returns every company surging on **any** of the topics, and **each row carries the per-topic scores inline** — `business_business_intent_topics` is a JSON array of `{topic, score}` (0–100). Use the real number for heat: **score ≥75 = high intent (+2)**, 60–74 = elevated. Filtering/preview costs nothing; the usual export pricing applies to rows you export.

## RocketReach — contact resolution + company intent, trigger signals & job-change timing

Auth: `ROCKETREACH_API_KEY` (Doppler-injected — **never** hardcode or echo the key). Two capability groups, and two tool surfaces that may expose them:

**1) Contact resolution** — person lookup (single / bulk). **Quota model:** lookups are effectively unmetered; **person *exports* / premium lookups are the finite unit** (a plan allotment — check the remaining balance before a run). Look up freely to confirm a person exists, but **spend the metered unit only on a scored finalist's contact**, never a candidate's; bulk-resolve all finalists in one call. When the balance runs low, tell the colleague before the run — never auto-purchase.

**2) Signal search (credit-free — searches never consume lookups/exports):**
- **Topic intent** (`intent` on company search / `company_intent` on person search) — **company-level** Intentsify topics, scored 0–100 weekly in the web app (≥75 = high intent). Intent is a **premium, plan-gated feature** — check `PROFILE.md` §"Connector plans & entitlements" → `rocketreach.features` **before** relying on this facet. If `"intent"` isn't listed there (or the section says "not captured"), don't assume the filter is live: an unentitled plan can pass the facet through and silently return **unfiltered** results — a false sense of intent-filtering, not an error. When entitlement is confirmed, two more prerequisites apply: a team admin must have **set the tracked topics** in the web app (**12 active**, changeable ~3×/yr — mirror the profile's intent-topic list; the cap is plan-dependent, confirm against the live "Intent Data" tab), and the API/MCP exposes intent as a **search facet only** (filter by topic; scores aren't returned) — the weekly ≥75 list lives in the app's Intent Data tab.
- **News triggers** (`news_signal` / `company_news_signal`) — `"Category::window"` strings, windows `one_week|one_month|three_months|six_months|one_year`. Highest-value categories for this routine: `Executive Hire`, `Executive Departure`, `Funding`, `Vulnerability`, `Launches Product`, `Mergers & Acquisitions`.
- **Hiring triggers** (`job_posting_signal` / `company_job_posting_signal`) — `"<Department> Roles::window"`, e.g. `"Engineering Roles::three_months"`, `"Machine Learning Roles::three_months"`.
- **Job-change timing** (`job_change_signal`, person search) — `"Company Change::three_months"` or `"Promotion::three_months"` (windows cap at three_months) — powers the new-in-role check (Step 6).

**Surface note:** both tool surfaces expose both groups — the official RocketReach MCP/connector as `person_search` / `company_search` / `person_lookup`, the in-repo VPS worker (`agent/mcp/rocketreach`) as `rocketreach_person_search` / `rocketreach_company_search` / `rocketreach_lookup` / `rocketreach_bulk_lookup`. Tool names differ per surface; call whichever the session offers (facet names are identical). If no signal-search tool is present at all, skip the RocketReach signal pass, run Vibe events + the web sweep as usual, and note "RR signals: unavailable" in the run header. Never substitute raw HTTP.

**Apollo surface note (both surfaces now recorded — live-verified 2026-07-27):**

| Capability | In-repo worker (`agent/mcp/apollo`) | Hosted OAuth connector |
|---|---|---|
| Connectivity / credit balance | `apollo_usage` | `apollo_users_api_profile` (pass `include_credit_usage: true`) · `apollo_usage_stats_credit_usage_stats` |
| People search (free) | `apollo_person_search` | `apollo_mixed_people_api_search` |
| Person enrich | `apollo_person_enrich` | `apollo_people_match` |
| Bulk person enrich | `apollo_bulk_person_enrich` | `apollo_people_bulk_match` |
| Company search (+ intent) | `apollo_company_search` | `apollo_mixed_companies_search` |
| Company enrich | `apollo_company_enrich` | `apollo_organizations_enrich` (+ `apollo_organizations_bulk_enrich`) |
| Job postings | `apollo_job_postings` | `apollo_organizations_job_postings` |

Tool names differ per surface; call whichever the session offers. Never guess a name, never substitute raw HTTP. **Phone reveal is never requested on either surface** — on the in-repo worker it is not representable at all; on the hosted connector it is async-via-webhook and must stay off (`reveal_phone_number: false`), as must `run_waterfall_email`/`run_waterfall_phone` (variable, plan-dependent credit cost).

> ⛔ **The hosted connector also exposes SEND and CRM-write tools the prospect skill must never call** — `apollo_emailer_messages_send_now`, `apollo_emailer_campaigns_approve`, `apollo_emailer_campaigns_add_contact_ids`, `apollo_sequences_create/update`, `apollo_contacts_create/update`, `apollo_accounts_create/update`, `apollo_tasks_*`, `apollo_email_account_purchase_create`, `apollo_domain_purchase_index`. This repo's sending posture is that **email sending is unrepresentable to the headless brain** (see the Saleshandy wrapper, which deliberately ships no send tool). The hosted Apollo connector breaks that structurally, so as of 2026-07-27 those tool names are **denied in code**, not merely discouraged: `agent/permissions.py` allowlists only Apollo's read/enrich tools and denies every send/approve/write/purchase name (and any unlisted future Apollo tool). Expect a hard denial, not a silent success, if one is ever attempted. Sending, sequence enrollment, CRM writes, and anything that spends money on domains/mailboxes stay operator-only, in Apollo's own UI. Note the code-level deny covers the agent and backend runtimes; in a desktop session with a self-added connector the doctrine above is still what holds. See SECURITY-SELF-ASSESSMENT.md **AP-02** (and SH-01 for the same shape on Saleshandy).

**⚠️ Apollo's API is PAID-PLAN-ONLY — verified live 2026-07-27, and this is the single most important fact about this connector.** Authorizing the connector on a **Free** Apollo plan succeeds and the account-level tools work, but *every prospecting data endpoint returns an error rather than data*:

```
{"error":"The api/v1/people/match API is not included in your Free plan and is not
 accessible. All paid plans include full API access. Upgrade your plan ...",
 "error_code":"API_INACCESSIBLE"}
```

Confirmed blocked on Free: `apollo_mixed_people_api_search`, `apollo_people_match`. By the same gate, expect `apollo_people_bulk_match`, `apollo_mixed_companies_search`, `apollo_organizations_enrich`, `apollo_organizations_job_postings` to behave identically — they are all `api/v1/*` data endpoints. Confirmed **working** on Free: `apollo_users_api_profile`, `apollo_usage_stats_credit_usage_stats` (account reads only).

The practical consequences for a run:
- **On a Free Apollo plan, treat Apollo as NOT CONNECTED** for every purpose this skill has. The credits the account shows (lead/AI/dial) are spendable in Apollo's *web UI*, not through the API — do not read a non-zero credit balance as "Apollo is usable here."
- `error_code: API_INACCESSIBLE` is a **plan** problem, not an outage and not a miss. Report it as "Apollo: API not included in plan" in the run header and fall through the waterfall exactly as if Apollo were absent. Never retry it, and never let it be mistaken for "no match found" — a miss and a paywall are different outcomes.
- Apollo's marketing states *"Any Apollo plan qualifies, including free"* — that is true of **connecting** the MCP, not of reaching the data. The live API is the authority; believe the error, not the pricing page.

> **`company_search` is a signal lookup on a candidate you already have — not a discovery tool, and its
> emptiness is not a discovery result.** It exists to check facets (topic intent, news/hiring triggers)
> on a named company, not to generate the candidate list. A null/empty `company_search` response means
> *this candidate has no RocketReach-visible signal* — it says nothing about whether Vibe discovery has
> been tried, and it does **not** satisfy the "attempt Vibe" requirement in Step 1. Falling back to web
> because `company_search` returned nothing is the same violation as skipping Vibe outright: check the
> actual discovery tool (Vibe `fetch-entities`) before web, every time, regardless of what RocketReach
> did or didn't return.

## Apollo — contact backstop, company intent & bulk

Auth: `APOLLO_API_KEY` (Doppler-injected, in-repo worker) or the hosted OAuth connector — **never** hardcode or echo either. Apollo is the **last paid source** in the contact-enrichment waterfall (RocketReach → Vibe → Apollo → web) and a third buying-intent feed.

**1) Contact resolution (email only)** — `apollo_person_enrich` (single) / `apollo_bulk_person_enrich` (≤10/call, Apollo's documented batch cap). Use **only** after both RocketReach and Vibe have missed a finalist. Metered 1 credit **only when a match with an email is found** — a miss costs nothing, so a speculative attempt is cheap, but only spend it on scored finalists, not candidates. **Never requests a phone number** — Apollo's phone reveal (`reveal_phone_number=true`) resolves asynchronously via a caller-hosted `webhook_url` and costs 8 extra credits; this deployment has no inbound path to receive that webhook (the same reason `agent/mcp/rocketreach` loops synchronous lookups instead of RocketReach's native async bulk endpoint), so the worker hardcodes `reveal_phone_number: false` on every call. If a finalist needs a phone, that stays RocketReach-only.

> ⚠️ **A returned Apollo email can be a placeholder, not an address.** When a record isn't unlocked for the team, Apollo returns the literal string `email_not_unlocked@domain.com`. The in-repo worker rejects these and returns `[apollo-error] … locked placeholder …`; treat that exactly like a miss and continue down the waterfall. **If you are on the hosted connector, check this yourself** — the connector has no such guard, and a placeholder is the one failure that produces a *wrong* contact rather than a missing one: it looks like a valid address, would survive consolidation into `ready-to-load.csv`, and is enrollable in a sequence. Never write one into a prospect list. Apollo's `revealed_for_current_team` flag is the corroborating signal.

> **Bulk has a much tighter rate limit than the rest of Apollo: 100 calls/hour and 20/minute** (vs 600/hour for person search and single match). Batch finalists into one `apollo_bulk_person_enrich` call rather than looping singles. The bulk result reports Apollo's own `credits_consumed` — trust that over counting returned emails, because Apollo can charge for a demographics-only match that carries no email. If `query_mapping_reliable` comes back `false`, Apollo returned fewer records than you asked for, so a miss can't be attributed to a specific query — re-check those finalists individually rather than assuming which one failed.

**2) Company search + buying intent** — `apollo_company_search`. Buying-intent filters (LeadSift, weekly refresh) require **tracked topics selected in Apollo's own web UI first** (Settings → Buying Intent → Edit topics) — this is the same prerequisite that caused `heat: 0` on every RocketReach run until its Intentsify topics were set (2026-07-18); an unconfigured Apollo account should be treated as "intent feed absent," never as "no accounts are surging." **Metered 1 credit per page/call regardless of hit count** (unlike person enrichment) — this is an optional extra in-market pass (Step 3), not the primary discovery engine, and should be skipped when the budget is tight or Vibe/RocketReach intent already covers the account.

> **Read `intent_strength`, don't infer intent from the row's existence.** Each company in a search result carries `intent_strength`, `show_intent`, and `has_intent_signal_account`. A `null` `intent_strength` means the feed is unconfigured (see the tracked-topics prerequisite above) — it does **not** mean the account is cold.

> ⚠️ **Company search returns thin records — no firmographics.** Apollo's search response carries only `{id, name, domain, linkedin_url, founded_year, phone}` plus the intent fields. It does **not** return industry, employee count, or location; those exist only on the *enrichment* endpoints. If you need firmographics for a searched company, that's a separate `apollo_company_enrich` call (another credit) — or, better, Vibe, which returns them in the discovery pass. Don't plan a run that expects search alone to fill the firmographic columns.

**3) Company enrichment** — `apollo_company_enrich` (domain preferred, else linkedin_url/name/website). Metered 1 credit per successful call. This is the **only** Apollo call that returns industry / employee count / location. Escape-hatch use only, same spirit as Vibe's `enrich-business` (≤3/run) — don't call this as a primary firmographics source when Vibe already has the data.

**4) Job-posting hiring signal** — `apollo_job_postings(organization_id)` — needs the Apollo org ID (from `apollo_company_search`/`apollo_company_enrich`'s `id` field, not a domain). Metered 1 credit/page. Parallels RocketReach's `job_posting_signal` facet; use whichever source is connected, or both to corroborate.

**5) Free preflight** — `apollo_usage()`. Call this once at the top of any run where Apollo is connected. It returns three things: `is_logged_in` (the key is live), `credits_remaining_this_month`, and `rate_limits`. Read the last two differently — they are not the same kind of number:
- **`credits_remaining_this_month` is a FLOOR, not Apollo's own figure.** Apollo exposes **no credit-balance endpoint** (unlike RocketReach's free `account` tool), so this is computed from this worker's `costs.jsonl` against `APOLLO_MONTHLY_CREDIT_CAP`. It cannot see credits spent outside this system (e.g. a human working in Apollo's web UI). Say so if the operator asks for an exact balance. `null` = no cap configured (uncapped).
- **`rate_limits` IS live from Apollo** — per-endpoint day/hour/minute limit + consumed + left_over — but the endpoint behind it is **master-API-key-only**, so on a non-master key the field reads `unavailable (HTTP 403 …)`. That is *not* a connectivity failure: `is_logged_in` already proved the key works. Don't report Apollo as down on a 403 here.

**6) Two surface caveats worth knowing before you blame the connector.** (a) `apollo_person_search` is documented as requiring a **master** API key, so it can 403 while `apollo_person_enrich` works fine on the same key — the free search and the metered enrich have different access requirements. (b) `apollo_company_search` is documented as paid-plans-only (403 on a free Apollo plan). In both cases report the specific 403 rather than "Apollo unavailable."

## Budget discipline (do this before any metered call)

- Read `monthly_tool_budget_usd`, `per_run_cap_usd`, `tools_metered` **and** §"Connector plans & entitlements" from PROFILE — the PROFILE budget comments are the source of truth for what the dollar cap covers, and the connector entries are the source of truth for a flat-subscription tool's real count-based limit (the dollar cap can't see those). Never hardcode prices or allowances here. Claude Max is the flat brain seat — **not** metered here.
- Estimate spend across **every connected source** **before** running fetches/enrichment: Vibe `estimate-cost` / credit balance, the RocketReach metered units the run would consume, and, if Apollo is connected, `apollo_usage`'s ledger-computed remaining allowance. Show it. If the run would exceed `per_run_cap_usd` or the remaining monthly budget, **stop and trim** (drop §"optional signal layers", then contact-enrichment on the bottom accounts) rather than silently spending.
- **Vibe credit model:** one-time credit **packs** with 365-day validity (not a monthly quota) — the pack purchase is logged to the cost ledger when it happens; runs then draw down the balance. Target **≤100 credits/run in standard mode**. When the balance runs low, tell the colleague before the next run — never auto-purchase.
- **Bulk mode's budget auto-scales, hard $-capped.** A stated large target (e.g. 300 accounts) is
  expected to need more than 100 credits — that's fine. The credit budget scales to roughly
  `target × 1.5–2` automatically, with **no separate confirmation prompt required**; the real control
  is the same `estimate-cost` call gated against `per_run_cap_usd` ($92 at time of writing — read the
  live PROFILE value, don't hardcode it) that standard mode already uses, just evaluated against the
  bigger number before the `export-to-csv` call. A 300-account bulk run (~450–600 credits, roughly
  $9–18 at the observed ~$0.02/credit) clears that cap without issue; a run that wouldn't clear it
  gets trimmed (reduce the auto-scale multiplier toward 1.5×, or the target) before exporting, never
  spent silently over cap.
- **RocketReach model:** flat monthly subscription — `cost_usd` on every RocketReach call is `0` by design (see `agent/mcp/rocketreach/server.py`), so **`monthly_tool_budget_usd`/`per_run_cap_usd` never gate RocketReach spend, no matter how high they're set.** The real constraint is the export/lookup **count** against the plan's `monthly_allowance` in `PROFILE.md` §"Connector plans & entitlements" (`rocketreach.monthly_allowance.limit`) — read it before a run with several finalists and pace against it the same way you'd pace Vibe credits; if it says "not captured," ask the operator once rather than assuming an unlimited quota. Budget ≤1 lookup per finalist (≤10/default run) regardless. Searches and every signal filter (intent, news, job-posting, job-change) are **credit-free**, so the intent/trigger layer adds ~0 marginal cost against that allowance.
- **Apollo model:** same shape as RocketReach — a flat monthly **plan allocation**, `cost_usd` is `0` by design (see `agent/mcp/apollo/server.py`), so the dollar cap never gates it either. The real constraint is the **credit count** against `apollo.monthly_allowance` in `PROFILE.md` §"Connector plans & entitlements". Unlike RocketReach, Apollo's person **enrich** is metered only on a match (a miss is free), but **company search and job postings are billed 1 credit per call/page regardless of hit count** — don't loop those speculatively. Budget Apollo as the last-resort finalist enrichment (≤1 person-enrich per finalist that both RocketReach and Vibe already missed) plus, only when the budget allows, one optional company-search intent pass.
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
