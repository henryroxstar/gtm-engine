# Buyer-intent signals catalog — what the `prospect` skill captures from Vibe + RocketReach + Apollo

> **Purpose / posterity.** One place that records *every* buyer-intent signal the prospecting
> routine can pull from its three connected data sources, what each signal means, how it maps onto
> the **heat axis** and the `intent_feeds` array, and how fresh it is. Company-agnostic (the
> *mechanics*); the **actual tracked topics** are tenant-specific and live in the active profile's
> `market-scan-config.md` (§Intent topics + §RocketReach tracked intent topics + §Apollo tracked
> intent topics) — this doc links, it does not restate them. Last updated 2026-10-05.

## The three feeds at a glance

| | **Vibe Prospecting** (Explorium) | **RocketReach** (Intentsify + triggers) | **Apollo** (LeadSift) |
|---|---|---|---|
| Intent engine | Bombora surge | Intentsify | LeadSift |
| Topic-intent granularity | **Company-level, scored 0–100 inline** | Company-level, weekly, **API filter-only (no score)** | Company-level, weekly; the filter is API-queryable and each row carries `intent_strength` (a strength label, **not** a 0–100 score) + `show_intent` — `null` means the feed is unconfigured, not that the account is cold |
| Freshness | **Weekly** Bombora recompute; business events run ~1 week behind (measured 2026-10-05) | **Weekly** recompute | **Weekly** recompute |
| Headless / API-native | **Yes** — scores come back in the fetch | Partial — filter yes, scores web-app only | **Yes** — filter is live-queryable via Apollo company search (either surface; no manual weekly-capture step, unlike RocketReach's Tier 2) — **but only on a PAID Apollo plan**, see below |
| Cost | Filter/preview free; export ~1 cr/row | All signal search **credit-free**; exports are the metered unit | **1 credit per call/page** — NOT free, unlike the other two feeds' search/filter layer |
| Prerequisite | none | tracked topics set in RocketReach's web UI | tracked topics set in Apollo's web UI (Settings → Buying Intent) |
| Role in the heat axis | **Primary** automated feed | Corroborating feed for **double-intent** | Corroborating feed for **double-intent**; costs credits, so used more sparingly than RocketReach's free facet |

The rubric scores *fit* (who); these signals score *heat* (when). **Never quote intent data in
outreach copy** — it times the touch and picks the angle; the message cites only public signals.

## What we capture — signal by signal

### Topic buyer-intent (drives `heat`)

| Signal | Source | Facet / field | What it means | → capture |
|---|---|---|---|---|
| **Bombora topic surge** | Vibe | `business_intent_topics` filter `{topics:[…]}`; score read from each row's `business_business_intent_topics` (JSON `{topic,score}`, 0–100) | The company is researching a tracked topic this period | `intent_feeds += "vibe-topic"`; **score ≥75 → heat +2**, 60–74 elevated (noted, no points) |
| **Intentsify tracked-topic match** (Tier 1) | RocketReach | `intent` / `company_intent` search facet | Company appears on a tracked topic's weekly surge list | `intent_feeds += "rr-intent"` (boolean → qualifies for +2) |
| **Intentsify weekly score** (Tier 2) | RocketReach | weekly snapshot file `content/<active>/prospects/intent/rr-intentsify-latest.json` | The scored ≥75 ranked account (web-app-only data, captured weekly) | `rr-intent` with real **score ≥75 → +2** |
| **LeadSift tracked-topic match** | Apollo | Apollo company-search buying-intent filter (`apollo_company_search` in-repo / `apollo_mixed_companies_search` hosted) (topics must be tracked in Apollo's web UI first). Each returned company carries `intent_strength` + `show_intent` — read them, don't infer intent from the mere presence of a row | Company appears on a tracked topic's weekly surge list | `intent_feeds += "apollo-intent"` (filter hit → qualifies for +2); **1 credit/call, not free** |
| **Double-intent** | any 2+ | — | Same account surges on **two or more** of Vibe / RocketReach / Apollo | **+1 more** (cap at rubric ceiling) |

Tracked topic lists: the active profile's `market-scan-config.md` (resolve via
`python -m gtm_core.resolve_knowledge market-scan-config.md --profile <active>`) — Vibe/Bombora list
under **§Intent topics**, RocketReach/Intentsify list under **§RocketReach tracked intent topics**,
Apollo/LeadSift list under **§Apollo tracked intent topics**. They are deliberately mirrored so
double-intent can fire across any pair.

### Trigger signals ("why now" — NOT heat)

| Signal | Source | Facet | → capture |
|---|---|---|---|
| **News triggers** (Exec hire/departure, Funding, Vulnerability, Product launch, M&A) | RocketReach | `news_signal: "Category::window"` | `intent_feeds += "rr-news"` (why-now flag, no heat) |
| **Hiring triggers** (Engineering / ML / IT roles) | RocketReach | `job_posting_signal: "<Dept> Roles::window"` | `intent_feeds += "rr-jobs"` |
| **Business events** (funding, M&A, new product, breach, dept growth) | Vibe | `events` filter / `fetch-businesses-events` | why-now flag; the web sweep confirms + dates. **Read the event text, not the event type** — see §"Vibe events vs intent" below |

These pre-flag Step 4's 6-source web sweep, which **confirms and dates** the 🔥 signal. The 🔥 line
always cites the public source, never the feed.

### Person-timing (queue priority — NOT heat)

| Signal | Source | Facet | → capture |
|---|---|---|---|
| **Job change / promotion** (new-in-role champion or economic buyer) | RocketReach | `job_change_signal: "Company Change::three_months" \| "Promotion::three_months"` | `new_in_role: true` — jumps the Tier-A queue (conversion premium decays ~90 days) |
| **Tenure in seat** | Vibe | `current_role_months` 1–6 | same 🆕 flag on the web/Vibe path. Title matching is loose: an assistant *to* a Chief AI Officer matches `chief ai officer` — read the title before flagging |

## Where it lands (per account, in `latest.json`)

```json
{ "heat": 0, "intent_feeds": ["vibe-topic","rr-intent","rr-news","rr-jobs","apollo-intent"], "new_in_role": false }
```

- `heat` 0–3 — topic-intent only, per the heat table in [`gates-and-scoring.md`](gates-and-scoring.md) §Heat axis.
- `intent_feeds` — which feed(s) fired (empty array if none). `vibe-topic`, `rr-intent`, `rr-news`, `rr-jobs`, `apollo-intent`.
- `new_in_role` — a 🆕 champion/economic-buyer was found.

## Freshness & the weekly-Intentsify/LeadSift constraint

- **Vibe/Bombora** — recomputed **weekly** (each score carries a weekly date stamp); the fetch reads
  the latest week, not a live value. Vibe **business events** lag about a week: on 2026-10-05 the
  newest event across 42 large accounts was dated 09-28.
- **RocketReach/Intentsify** — recomputed **weekly**; a topic set today returns nothing for ~1 cycle.
  Tier-2 snapshot carries a `week_of` stamp; **if >10 days old, ignore it** and fall back to the
  Tier-1 filter + Vibe (freshness guard in `discovery-and-budget.md`).
- **Apollo/LeadSift** — recomputed **weekly**, like Intentsify, but the filter is **live-queryable via
  the API** (Apollo company search, either surface) — there is no separate web-app-only
  weekly-snapshot capture step the way RocketReach's Tier 2 needs. A topic just tracked today still
  returns nothing for ~1 weekly cycle (LeadSift has no backfill either); the cost is credits
  (1/call), not staleness. ⚠️ **Requires a PAID Apollo plan** — on a Free plan every Apollo data
  endpoint returns `error_code: API_INACCESSIBLE`, so this feed contributes nothing at all
  (verified live 2026-07-27; see `discovery-and-budget.md` §"Apollo surface note"). Treat that as
  the feed being **absent**, never as "no accounts are surging".

## Vibe events vs intent — what each can and cannot tell you (measured 2026-10-05)

They are different signals, and neither one says "this account runs agents today".

- **Intent (Bombora surge)** = the company read more about a topic over the last 3 weeks than its
  own 12-week baseline (≥60 surging). A company that always researches AI **never surges**: twelve
  large banks, payments networks and health systems with dated, public agent news that same month
  showed no surge on *any* AI topic, while the same probe did return other large accounts (a
  life insurer that had just hired its first Chief AI Officer, a health-benefits company) for those
  topics. So intent finds accounts **starting to evaluate** — a different angle from an
  agents-in-operation premise — and **no surge on a large account is not cold**. Same rule as the
  small-company coverage gap: absence is a property of the feed, not of the account.
- **Business events** = dated things a company announced, about 90% of them its own LinkedIn posts,
  typed automatically. **Agent evidence sits in the text, not the type** (one bank's agent platform
  arrived as a `company_award`). An events-led pass over 30 large financial firms returned 40 events
  and none mentioned agents; against accounts the web sweep had already caught with agent news, the
  feed surfaced the agent content for 2 of 7. Scan `snippet`/`product_description`/
  `award_reason` for agent terms, and check attribution (name collisions, 13F filings filed as
  "investments", fund series listed as companies).
- **Don't stack intent and events in one fetch.** One stacked run returned 2 events across 30
  accounts in a 2-week window; an events-only pass on a different cohort returned 40 across 30. The
  cohorts differ (health-heavy vs financial-only), so the cause is not isolated — the likeliest
  reading is that stacking selects by intent and the events then thin out. Run them as separate passes (the coverage pass and heat pass in
  `discovery-and-budget.md` §Bulk mode).
- **The useful agent check is `enrich-business` → `enrich-business-website-keywords`** (~1 credit/row)
  with `keywords: ["AI agents", "agentic"]`: it returns the company's own pages that use the words —
  premise evidence for "talks about agents", **undated**, so it is fit, not timing. Verify the URLs
  are on the company's domain: one bank's result came back as other vendors' explainer pages.
  `enrich-business-linkedin-posts` and `enrich-business-website-changes` returned empty for all
  twelve large accounts tested — don't pay for them on enterprise runs.
- **Timing comes from the web sweep**, which caught the same month's agent news at no credit cost.

## Operator weekly-capture click-path (RocketReach Intentsify → snapshot file)

The scored ≥75 list is browser-only (no MCP/API). Once a week, ideally the morning of the run:

1. RocketReach → **Account** → **Intent Data** tab (`/account?section=nav_gen_intent_settings` shows
   the tracked topics; the scored weekly list is in the same Intent Data area once populated).
2. Read the accounts scoring **≥75** for the tracked topics (domain + top topic + score).
3. Write them into `content/<active>/prospects/intent/rr-intentsify-latest.json` in the shape shown
   in `discovery-and-budget.md` §Tier 2, stamped with this week's `week_of` date.

This is an operator step, not a headless egress path. It can be done by hand or via an assisted
browser session; it is intentionally *outside* the MCP tool surface (§R6). Until the first weekly
cycle populates, the file stays empty and the skill runs Tier-1 + Vibe only.
