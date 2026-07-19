
# GTM — Outbound Campaign / Program Plan

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> Read the company brand from `PROFILE.md` → `brand_name` and use it throughout — never hardcode a company name.

Produce a clear, executive-facing plan for a **scaled, staged outbound program** — grounded in the
**live prospect pipeline**, current **market signals**, the profile's **ICP/scoring rubric**, and the
profile's **outbound-program defaults**. The deliverable is a markdown document (the source of truth)
plus a self-contained HTML exec companion. The program's **north-star metric is SQLs**; sending stays
human-gated (this skill plans, it never sends — see [`email-sequence`](../email-sequence/SKILL.md) for staging).

## Load context (in this order; everything below is optional — degrade gracefully, never invent)

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md`, `market-scan-config.md`, `case-studies.md`, or `outbound-program-defaults.md` —
> resolve its path with `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]`
> and read whatever path it prints, instead of opening `knowledge/<file>` directly. The helper returns
> the product-level file (`products/<slug>/<file>`) when present and falls back to the profile-level
> `knowledge/<file>` otherwise. Pass `--product` when the run is bound to one product (the lead
> `default_product` from PROFILE.md, or a product the operator named); omit it for profile-wide work.

1. **PROFILE** — `profiles/<active>/PROFILE.md`. Pull: `name`, `brand_name`, `target_markets`,
   `icp_weighting`, `primary_persona`, `budget_monthly`, `email_tool`.
2. **`profiles/<active>/knowledge/outbound-program-defaults.md`** — the profile's durable framing:
   the differentiation wedge, the cross-org use-case cohorts (party chains + pain→claim→gain +
   representative account archetypes), cohort sequencing by readiness, the learning-agenda
   hypotheses, funnel/reply-rate assumptions, budget/unit-economics baseline, and honesty guardrails.
   **These are defaults you refine with live data — not frozen facts.** If this file is absent, derive
   cohorts from the industry packs + ICP instead, and say so.
3. **Live prospect pipeline** — `content/<active>/prospects/latest.json`. Compute fresh each run:
   total accounts, Tier-A vs Tier-B split, heat distribution (how many at heat +2 / +3, named), geo
   split, and which named accounts carry the strongest fit + intent. Note `generated_at` /
   `source_run`. **If the file is missing or clearly stale, say "no fresh pipeline data" and build the
   structural plan with placeholders — do not fabricate counts.**
4. **Market signals** — the most recent file(s) in `content/<active>/market-signals/`. Pull the
   H-rated signals (competitive moves, regulation/enforcement dates, category-size data, incidents)
   that shape *why now* and *which cohort to weight*. Carry each signal's own verification flags
   (single-source items stay flagged as leads-to-verify, never asserted as fact).
5. **Tracked intent topics** — `market-scan-config.md` (via resolve_knowledge). Summarize which
   topics are tracked on each feed and which feeds are currently live; **link**, don't restate the
   mechanics (they live in the `prospect` skill's `references/intent-signals-catalog.md` and
   `gates-and-scoring.md`).
6. **ICP + proof** — `icp-personas.md`, `case-studies.md`, and any packs under `knowledge/industry/`
   for per-vertical bottom-up targets to sanity-check the funnel against.

## Gather inputs (ask the operator in one message — all have sensible defaults)

- **Program size** — target number of prospects (and rough accounts). Default: read the ambition from
  `outbound-program-defaults.md`; else propose one anchored on the current Tier-A count.
- **Markets** — confirm from PROFILE `target_markets` or override.
- **Timeframe** — the ramp window (e.g. a 6-week test-then-scale). Default from the defaults file.
- **What changed** — if refreshing, what's new since the last version (fresh prospect batch, new
  signal, shifted target)?

If a prior plan exists in `content/<active>/plans/campaigns/` and they say "just refresh it", take the
refresh path at the end — don't rebuild from scratch.

## Build the plan (exec-summary-first, punchy — this is for executives)

Follow the section skeleton in [`references/plan-template.md`](references/plan-template.md). Keep the
summary compact and up front; details follow. **Do not frame it as an approval ask.** Structure:

- **Executive summary** — the bet in 3–4 sentences + a KPI strip. Lead with the timing argument from
  the market signals, name the differentiation wedge, and state the **north-star = SQLs** (with the
  base-case SQL → pilot count from the funnel). Note the built-in guardrails (staged, human-gated,
  rates are hypotheses not forecasts).
- **§ Why now** — the market-signal case: competitor move(s), regulation/enforcement dates, category
  size, and the incidents (flagged single-source where they are). One paragraph of "so the focus is…".
- **§ Pipeline snapshot** — visualize the live `latest.json`: total / Tier-A / heat / geo, a density
  map by cross-org cluster, and the in-market (heat +2) accounts named. Then a short "reading → how
  each insight shapes the program" table. Flag Week-0 realities honestly (e.g. not-yet-in-CRM, a feed
  not yet switched on) exactly as the run report states them.
- **§ The wedge** — the cross-org use-case cohorts from the defaults file, each as a row: party chain,
  pain→claim→gain, and named accounts already in the pipeline. Re-segment what's qualified; don't
  invent logos.
- **§ How the engine works** — a short explainer of the prospecting engine for readers unfamiliar with
  it (discover → qualify → score → enrich → draft; Claude = brain; two paid data feeds; human on every
  gate; drafts only). Summarize the ICP gate + rubric + heat axis visually; **link** to the `prospect`
  skill references rather than restating them.
- **§ The plan** — the universe pyramid (Tier-A/B/C counts), the cohort calendar sequenced by
  readiness (from the defaults file, weighted by the live density map), and the learning-agenda
  **hypotheses table with a "how we measure" column** (sample size → pass bar) and a tripwire per row.
- **§ The numbers** — a tiered funnel (reply rates fall down the pyramid) to **SQLs → pilots**, plus a
  conservative/base/stretch scenario table. State every rate as an assumption to test. Sanity-check the
  base case against the industry packs' bottom-up per-vertical targets.
- **§ Guardrails & decision gates** — deliverability as the binding constraint, de-dupe against
  already-contacted lists, human send-gate, honesty (sell the pilot not the product; verify
  single-source signals before customer-facing use), and the gates that decide whether the tail ships.
- **§ Tools & budget** — the stack and its economics from `content/<active>/costs.jsonl` (fixed core
  vs metered; cost per prospect / per qualified account / **per SQL** vs a labeled industry range).
  If no in-repo cost-per-SQL benchmark exists, say so and use a labeled external range.

## Review and save

Present the plan, then ask: "Anything to adjust before I save?"

Save the markdown as **`content/<active>/plans/campaigns/<program-slug>-<YYYY-MM-DD>.md`**
(`<program-slug>` = kebab-cased program name, e.g. `agent-gateway-cross-org`). Resolve the content
root via `python -m gtm_core.paths` / the `GTM_CONTENT_ROOT` override — never write to the repo root.
This is a **new subfolder** under `plans/`; do not touch the weekly content-calendar files
(`plans/<YYYY-WW>-plan.json`, owned by `content-plan`).

Then author the **HTML exec companion** next to it — same basename, `.html` — following
[`references/html-companion.md`](references/html-companion.md) (self-contained, theme-aware, brand
accent from `knowledge/brand/`, static — no external fetch). The `.md` stays the source of truth for
every number; the `.html` presents the same figures.

Finally append **both** `⟦FILE:…⟧` sentinels at the very end of your response so the cockpit delivers
both files:

```
⟦FILE:/absolute/path/to/<program-slug>-<YYYY-MM-DD>.md⟧
⟦FILE:/absolute/path/to/<program-slug>-<YYYY-MM-DD>.html⟧
```

Use the real resolved absolute paths of the files you just saved.

## Refresh an existing plan (keep numbers current)

When the operator says "refresh the campaign plan" or the prospect ledger was updated:
1. Read the existing `.md` in `content/<active>/plans/campaigns/`.
2. Re-read `latest.json` + the newest market-signals file; recompute the pipeline snapshot, funnel
   inputs, and any named in-market accounts.
3. Update only the changed sections; add a `## Updated [date]` note at the top summarizing what moved.
4. Re-author the `.html` companion from the updated `.md`. Save with the same basename (overwrite);
   emit both `⟦FILE:…⟧` sentinels again.

Do not rebuild from scratch — treat the prior plan as the working draft.

## Guardrails

- **Never invent pipeline numbers, conversion rates, market data, or customer names.** Every count
  traces to `latest.json`; every named account is already in the pipeline; every rate is labeled an
  assumption. If data is missing, say so and use a placeholder.
- Keep specifics traceable: cohorts/use-cases come from `outbound-program-defaults.md` (refined with
  live data), never baked into this generic body.
- Carry the market-signals verification flags through — single-source items stay flagged; confirm any
  metric the defaults file marks "confirm internally" before customer-facing use.
- **Free paths only** — no metered tool calls during planning. Discovery/enrichment happen in their
  own skills; this skill reads what already exists.
- The plan is an **internal working document**. Sending is out of scope and stays human-gated — never
  suggest activating a sequence or posting the plan publicly.
- **Write for executives in plain language.** No insider jargon or compressed metaphors ("the
  unclaimed middle", "the wedge is being claimed", "the plumbing"). Name the actual noun — e.g. "the
  market for cross-org agent trust that no vendor owns yet" — and spell out any strategy shorthand the
  first time it appears. A reader who has never seen this space should follow the argument.
