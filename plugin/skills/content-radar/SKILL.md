---
name: content-radar
description: >-
  News-driven content radar for the active company. Reads fresh discovery_items via the
  read-only Postgres news MCP, dedupes against the content history, clusters stories by the
  active profile's content pillars, and scores each cluster 0–100 (blending the discovery
  trending_score with pillar-fit and relevance). DeepSeek (the worker MCP) writes bulk story
  summaries; Claude ranks and composes the brief. Produces a dated radar digest plus a
  StoryCluster[] the content-plan skill consumes. Falls back to a free web sweep (market-scan)
  when the news rows are stale or empty. This skill should be used when the user says "run
  content radar", "what's trending for content", "scan the news for content", "what should we
  post about", "content radar", "refresh the radar", or on the content cadence before
  planning.
metadata:
  version: "0.1.0"
  phase: "1"
  capability_tier: pipeline
---

# Content Radar

Turn fresh agentic-AI news into a ranked, on-narrative set of story clusters for the active company,
then write a dated digest the content-plan skill builds on. Deterministic scoring runs in code
(`python -m gtm_core.radar`); DeepSeek drafts summaries; Claude ranks and writes the brief.

> Resolve the **active profile** (the agent provides it; everything company-specific loads from
> `profiles/<active>/`, never from `plugin/`). The only writable state is `content/<active>/`.

## Step 0 — Read the profile

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

Read `profiles/<active>/PROFILE.md`. Extract:

- `content_pillars` — the pillars stories are clustered against (the scoring code reads these too).
- `brand_name`, `social_handle` — for framing the brief.
- `language` — write the digest narrative in this language (default English).
- `monthly_tool_budget_usd`, `per_run_cap_usd` — budget guard (see Step 6).

If no active profile resolves, ask the user to run the `setup` skill first.

## Step 1 — Budget pre-check

Before any metered call, check the month's spend:

```bash
python -m gtm_core.ledger_cli month-total --profile <active>
```

If the total is at/over `monthly_tool_budget_usd`, stop and tell the user — do not spend. Open a run
manifest for this run (resume + status):

```bash
python -m gtm_core.ledger_cli write-run-manifest --profile <active> \
  --json '{"run_id":"<run-id>","trigger":"telegram","stages":[{"name":"radar","status":"running"}]}'
```

Use a sortable `run_id` like `r-YYYYMMDD-HHMM`.

## Step 2 — Pull fresh news (read-only Postgres news MCP)

Use the **news** MCP `query` tool (read-only; it can only SELECT `discovery_items`) with EXACTLY:

```sql
SELECT COALESCE(item_name, name) AS title,
       id, trending_score, published_at,
       COALESCE(source_url, original_url) AS source_url,
       COALESCE(summary, description)     AS summary
FROM public.discovery_items
WHERE is_active = true
  AND is_stale = false
  AND published_at > NOW() - INTERVAL '14 days'
ORDER BY published_at DESC
LIMIT 100;
```

Write the returned rows to `content/<active>/radar/.rows.json` (a JSON array). Each row keeps
`id`, `title`, `trending_score`, `published_at`, `source_url`, `summary` (and `topics` if present).

**Staleness / empty guard (web-sweep fallback):** if the query returns 0 rows, or the newest
`published_at` is older than 7 days (ingestion may have stalled), DO NOT serve frozen rows silently.
Say so in chat, then run the `market-scan` skill as the free web-sweep fallback and use its signals to
build the digest instead. Note the fallback in the run manifest (`"name":"radar","status":"skipped"`
with an `error` note) and skip to Step 5.

## Step 3 — Score + cluster (deterministic, in code)

Run the radar core — it dedupes against the content history, clusters by pillar, and scores 0–100:

```bash
python -m gtm_core.radar --rows content/<active>/radar/.rows.json --profile <active>
```

This writes `content/<active>/radar/YYYY-MM-DD-digest.md` (a baseline digest) and
`content/<active>/radar/YYYY-MM-DD-clusters.json` (the `StoryCluster[]`), and prints the clusters as
JSON to stdout. Capture that JSON.

**Scoring rubric (for transparency — the code is the source of truth):**
`score = 100 × (0.55·norm_trending + 0.45·(0.60·pillar_fit + 0.40·relevance))`, clamped 0–100, where
`norm_trending` is the discovery `trending_score` normalised across the batch, `pillar_fit` is the
keyword overlap with the best-fit pillar, and `relevance` is overlap with core agentic-AI vocabulary.
Near-duplicate-title stories from different sources merge into one cluster. Never recompute or override
the numeric score by hand — it is ledgered and must stay reproducible.

## Step 4 — Summaries (worker MCP) + ranking (Claude)

For the top clusters (by score), call the **worker** MCP `summarize` tool with each cluster's source
titles + summaries to get tight, factual draft summaries (this is bulk DeepSeek work — cheap). Then
YOU (Claude) review every summary for accuracy (the worker may hallucinate — drop anything not
supported by the source), rank the clusters, and write a one-paragraph "why this matters now" per top
cluster. Do not invent facts not present in the sources.

For each top cluster, add two judgment overlays that sharpen the ranking. They inform your ordering
and the digest — they never touch the reproducible numeric `score`:

- **Fault-line** — is there a live tension here: a claim smart people disagree on, or a lazy consensus
  worth challenging? A story with a real fault-line earns reach; a consensus announcement doesn't.
  Name the arguable angle in one line. **Brand-safety:** the fault-line is always a *belief or a
  status-quo default* (e.g. "trust-by-convention vs runtime accountability"), never a named competitor,
  vendor, or a company whose stack we analyse — complementary-positioning holds.
- **Velocity** — is attention still **rising** or already **peaked**? Use `published_at` recency and
  whether multiple sources are still picking it up. Down-weight peaked stories; a rising fault-line
  spotted early beats a saturated one.

Skip any story where a fault-line can't be taken safely — tragedy, crisis, or a toxic partisan
flashpoint off the company's domain. Silence is the correct call there.

## Step 5 — Compose the digest

Enrich `content/<active>/radar/YYYY-MM-DD-digest.md`: keep the per-cluster score/pillar/sources the
code wrote, and add your reviewed summary + "why now" under each top cluster, plus a one-line
**Tension:** (the fault-line) and **Velocity:** (rising / peaked) from Step 4 — these are what
content-plan and builder-studio's trend tie-in read to pick an angle that's live, not stale. Lead with
a 2–3 sentence "state of the week" for the active company's narrative. Keep `…-clusters.json` as the
machine contract (do not edit its scores).

## Step 6 — Ledger + report

Log the metered worker spend and close the run manifest:

```bash
python -m gtm_core.ledger_cli append-cost --profile <active> \
  --json '{"tool":"deepseek-worker","skill":"content-radar","cost_usd":<usd>,"run_id":"<run-id>"}'
python -m gtm_core.ledger_cli append-history --profile <active> \
  --json '{"event":"radar_complete","skill":"content-radar","run_id":"<run-id>","digest":"<digest-path>","clusters":<N>,"source_items":[<ids surfaced>]}'
python -m gtm_core.ledger_cli write-run-manifest --profile <active> \
  --json '{"run_id":"<run-id>","trigger":"telegram","stages":[{"name":"radar","status":"ok","outputs":["<digest-path>","<clusters-path>"]}]}'
```

The `source_items` you log in history are what the next radar run dedupes against, so list every
NewsItem id you surfaced. Then report in chat:

- Top N clusters: score · pillar · one-line title
- The digest path
- Month-to-date spend vs `monthly_tool_budget_usd`

## Guardrails

- The news MCP is **read-only** — never attempt writes. `profiles/<active>/` is read-only too.
- Only write under `content/<active>/`. Never write to `plugin/` or `profiles/`.
- DeepSeek (worker MCP) drafts only; Claude verifies every summary before it lands in the digest.
- Never auto-publish or auto-post. The radar only produces a brief.
- Surface stale/empty news loudly and fall back to the web sweep — never serve frozen rows silently.
- Never hand-edit the numeric `score` in `…-clusters.json` — it must stay reproducible. The
  fault-line/velocity overlays inform ranking and the digest narrative only, never the score.
- The fault-line is always a **belief or status-quo default**, never a named competitor or vendor —
  complementary-positioning holds. Skip tragedy, crisis, or toxic-partisan stories.
