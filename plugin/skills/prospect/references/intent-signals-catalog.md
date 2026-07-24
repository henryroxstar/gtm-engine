# Buyer-intent signals catalog — what the `prospect` skill captures from Vibe + RocketReach

> **Purpose / posterity.** One place that records *every* buyer-intent signal the prospecting
> routine can pull from its two connected data sources, what each signal means, how it maps onto the
> **heat axis** and the `intent_feeds` array, and how fresh it is. Company-agnostic (the *mechanics*);
> the **actual tracked topics** are tenant-specific and live in the active profile's
> `market-scan-config.md` (§Intent topics + §RocketReach tracked intent topics) — this doc links, it
> does not restate them. Last updated 2026-07-18.

## The two feeds at a glance

| | **Vibe Prospecting** (Explorium) | **RocketReach** (Intentsify + triggers) |
|---|---|---|
| Intent engine | Bombora surge | Intentsify |
| Topic-intent granularity | **Company-level, scored 0–100 inline** | Company-level, weekly, **API filter-only (no score)** |
| Freshness | Per fetch (near-live) | **Weekly** recompute |
| Headless / API-native | **Yes** — scores come back in the fetch | Partial — filter yes, scores web-app only |
| Cost | Filter/preview free; export ~1 cr/row | All signal search **credit-free**; exports are the metered unit |
| Role in the heat axis | **Primary** automated feed | Corroborating feed for **double-intent** |

The rubric scores *fit* (who); these signals score *heat* (when). **Never quote intent data in
outreach copy** — it times the touch and picks the angle; the message cites only public signals.

## What we capture — signal by signal

### Topic buyer-intent (drives `heat`)

| Signal | Source | Facet / field | What it means | → capture |
|---|---|---|---|---|
| **Bombora topic surge** | Vibe | `business_intent_topics` filter `{topics:[…]}`; score read from each row's `business_business_intent_topics` (JSON `{topic,score}`, 0–100) | The company is researching a tracked topic this period | `intent_feeds += "vibe-topic"`; **score ≥75 → heat +2**, 60–74 elevated (noted, no points) |
| **Intentsify tracked-topic match** (Tier 1) | RocketReach | `intent` / `company_intent` search facet | Company appears on a tracked topic's weekly surge list | `intent_feeds += "rr-intent"` (boolean → qualifies for +2) |
| **Intentsify weekly score** (Tier 2) | RocketReach | weekly snapshot file `content/<active>/prospects/intent/rr-intentsify-latest.json` | The scored ≥75 ranked account (web-app-only data, captured weekly) | `rr-intent` with real **score ≥75 → +2** |
| **Double-intent** | both | — | Same account surges on Vibe **and** RocketReach | **+1 more** (cap at rubric ceiling) |

Tracked topic lists: the active profile's `market-scan-config.md` (resolve via
`python -m gtm_core.resolve_knowledge market-scan-config.md --profile <active>`) — Vibe/Bombora list
under **§Intent topics**, RocketReach/Intentsify list under **§RocketReach tracked intent topics**.
They are deliberately mirrored so double-intent can fire.

### Trigger signals ("why now" — NOT heat)

| Signal | Source | Facet | → capture |
|---|---|---|---|
| **News triggers** (Exec hire/departure, Funding, Vulnerability, Product launch, M&A) | RocketReach | `news_signal: "Category::window"` | `intent_feeds += "rr-news"` (why-now flag, no heat) |
| **Hiring triggers** (Engineering / ML / IT roles) | RocketReach | `job_posting_signal: "<Dept> Roles::window"` | `intent_feeds += "rr-jobs"` |
| **Business events** (funding, M&A, new product, breach, dept growth) | Vibe | `events` filter / `fetch-businesses-events` | why-now flag; the web sweep confirms + dates |

These pre-flag Step 4's 6-source web sweep, which **confirms and dates** the 🔥 signal. The 🔥 line
always cites the public source, never the feed.

### Person-timing (queue priority — NOT heat)

| Signal | Source | Facet | → capture |
|---|---|---|---|
| **Job change / promotion** (new-in-role champion or economic buyer) | RocketReach | `job_change_signal: "Company Change::three_months" \| "Promotion::three_months"` | `new_in_role: true` — jumps the Tier-A queue (conversion premium decays ~90 days) |
| **Tenure in seat** | Vibe | `current_role_months` 1–6 | same 🆕 flag on the web/Vibe path |

## Where it lands (per account, in `latest.json`)

```json
{ "heat": 0, "intent_feeds": ["vibe-topic","rr-intent","rr-news","rr-jobs"], "new_in_role": false }
```

- `heat` 0–3 — topic-intent only, per the heat table in [`gates-and-scoring.md`](gates-and-scoring.md) §Heat axis.
- `intent_feeds` — which feed(s) fired (empty array if none). `vibe-topic`, `rr-intent`, `rr-news`, `rr-jobs`.
- `new_in_role` — a 🆕 champion/economic-buyer was found.

## Freshness & the weekly-Intentsify constraint

- **Vibe/Bombora** — refreshed per fetch; treat as current every run.
- **RocketReach/Intentsify** — recomputed **weekly**; a topic set today returns nothing for ~1 cycle.
  Tier-2 snapshot carries a `week_of` stamp; **if >10 days old, ignore it** and fall back to the
  Tier-1 filter + Vibe (freshness guard in `discovery-and-budget.md`).

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
