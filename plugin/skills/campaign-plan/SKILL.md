---
name: campaign-plan
description: >-
  Build or refresh an executive-facing outbound campaign plan grounded in live prospect data,
  market signals, and message portfolios. Trigger when the user says "build the campaign
  plan", "outbound program plan", "plan the outbound campaign", "refresh the campaign plan",
  or "present the outbound program to execs".
metadata:
  version: "0.5.0"
  phase: "4"
  capability_tier: core
---

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
7. **The message assets** — resolve each, and read whatever path it prints:

   ```bash
   python -m gtm_core.resolve_knowledge hook-matrix.md --profile <active> [--product <slug>]
   ```

   `knowledge/hook-matrix.md` is the **persona × signal** grid this plan draws its arguments from;
   where a cohort matches one, the `knowledge/use-cases/` dossiers carry the substance behind it
   (a directory of per-use-case files, not a single doc — list it rather than resolving a name). The plan **chooses** the
   portfolio (below) — it does not leave the message to whichever spec gets written first.

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
- **§ The message portfolio** — **which arguments this campaign runs, and who gets each.** Choose
  them here; do not let them emerge from whichever sequence spec is written first. One row per
  argument: the `hook_cell` from the matrix (persona × signal, in the matrix's own labels), a
  stable `argument_id`, the claim in one sentence, the proof that carries it (by *type*, never a
  logo), and the cohort/persona it is assigned to with its recipient count. Rules:
  - **Every persona holding a meaningful share of the list gets an argument.** A persona with
    recipients and no argument is receiving someone else's pitch.
  - **`K` — how many arguments — is the operator's call**, and belongs in the plan. Anchor it on
    recipients per argument: too few and nothing is readable, too many and nothing is comparable.
    State the number and the reasoning.
  - **Two arguments must be genuinely different claims**, not one claim in two vocabularies.
    Restating "the same gap" with different domain nouns is one argument; a shared sentence across
    variants is the tell.
  - Never invent a hook: the matrix is the only source of hook text and it is the tenant's asset.

  The `email-sequence` skill implements one cell per spec and declares it; this table is what it
  implements. Check the result once the specs exist:

  ```bash
  uv run python -m gtm_core.hook_coverage --profile <active> --campaign <campaign-slug>
  ```

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

**Also write the campaign manifest** — a small machine-readable file so the campaigns portfolio page
(`campaigns.html`) can find this plan and show target-vs-actual automatically. Save
`content/<active>/plans/campaigns/<program-slug>.campaign.toml` (no date in this filename — one
manifest per program, overwritten on refresh).

The manifest is **not just targets**: everything below `[experiment]` is rendered verbatim on the
portfolio page, so a plan whose hypotheses cannot actually be answered says so there rather than
implying a result is coming. Write every prose value in it for a **CRO, a Head of Inside Sales and a
CMO** — plain business English, no research vocabulary (see *Write the manifest for the exec
reader* below).

```toml
slug = "<program-slug>"
title = "<program name>"
status = "draft"                  # draft while under review; set "active" once a sequence is staged
created = "<YYYY-MM-DD>"
plan_md = "<program-slug>-<YYYY-MM-DD>.md"
plan_html = "<program-slug>-<YYYY-MM-DD>.html"
sequences = []                    # manual backfill only — leave empty; email-sequence stamps its own
                                   # "campaign" tag on each sequence it stages against this slug

[targets]                         # base-case numbers from § The numbers — never the stretch case
prospects = <n>
emails = <n>
replies = <n>
sqls = <n>
pilots = <n>

[targets_derivation]              # one line per target: the arithmetic, so it can be re-checked
emails = "<n> contacts x <n> emails each = <n>"
replies = "by tier, using the plan's own reply rates: …"
"meetings (SQLs)" = "…"
pilots = "…"

[window]                          # what the daily sending ceiling actually implies for the calendar
send_start = "<YYYY-MM-DD>"
completes_est = "<YYYY-MM-DD>"
send_days = <n>
daily_cap = <n>
mailboxes = <n>
touches = <n>
schedule = "<e.g. Mon-Fri, 9am-6pm New York time>"
basis = "<the arithmetic behind completes_est — including the follow-up tail, see below>"
caveat = "<pauses, holidays the schedule does not skip>"

[experiment]                      # what this run can and cannot tell us
approach = "<e.g. One version of the email to everyone. No A/B test, no control group.>"
why_we_run_it = "<what it is really for>"
expected_replies = "<About <n> replies from <n> contacts>"
realistic_range = "<Anywhere from <n> to <n> replies would be normal>"
what_it_tells_us = "<the decision it can support>"
what_it_cant_tell_us = "<the comparisons the list is too small to support>"
power_table = [                   # contacts needed per group to trust a difference of each size
  { lift = "a slight edge (30% better)", meaning = "…", n_per_arm = <n> },
  { lift = "twice as good", meaning = "…", n_per_arm = <n> },
  { lift = "three times as good", meaning = "…", n_per_arm = <n> },
]
method_footnote = "<one line of small print naming the basis for those group sizes>"

[[experiment.will_learn]]         # the payload — write this BEFORE the limitations, and lead with it
question = "<what we will actually know at the end>"
how = "<the numbers behind it, and whether it is a firm read or only directional>"

[[experiment.measurement]]        # one per funnel step; mark the blind ones honestly
stage = "<e.g. They opened it>"
instrumented = false
note = "<why it is invisible, and what that costs us when reading a quiet week>"

[[experiment.hypotheses]]         # mirrors § The plan's learning agenda, one block per question
id = "Q1"
claim = "<the question, phrased as a question an exec would ask>"
status = "not set up"             # exactly one of: "can answer" | "list too small" | "not set up"
verdict = "<why, in numbers>"
needs = "<what would make it answerable>"

[[experiment.confounds]]          # reasons a good-looking number might not mean what it appears to
name = "<short label>"
detail = "<the trap, in plain language>"

[[experiment.next_run]]           # cheapest fix first
action = "<change>"
cost = "<what it costs>"
buys = "<what it gets us>"
```

This is plumbing, not a deliverable — do not add a `⟦FILE:…⟧` sentinel for it.

### Before writing `[experiment]`, check three things per hypothesis

A plan that names hypotheses does not mean an experiment is running. For each row of the learning
agenda, verify in this order and set `status` from the **first** one that fails:

1. **Is the variable actually varied?** A question about message framing needs two message variants
   configured in the sequencer. One variant → `not set up`, however many contacts receive it.
2. **Is the outcome actually visible?** Check the sequencer's own settings, not the plan's funnel
   diagram. Open/click tracking is often deliberately off for deliverability — if so, say it here and
   mark those `[[experiment.measurement]]` rows `instrumented = false`.
3. **Is the list big enough for the effect the plan claims to care about?** Compute the per-group
   requirement from the pass bar *the plan itself wrote down*, then compare it against the largest
   split the loaded list actually supports. Smaller → `list too small`.

`status` is a **verdict, not a caveat**. A confident-sounding answer from too few replies is worse
than no answer — it launders noise into strategy. Never soften an unanswerable question into
"directional"; write what it would take in `needs`.

**Read the shipped copy before judging any of it.** Pull the live steps and variants from the
sequencer rather than reasoning from the plan — the plan describes intent, the sequencer holds what
buyers will actually receive. Count the **distinct bodies**, not the sequences: merge fields make
every email look unique while the argument stays identical, and follow-up steps are often shared
byte-for-byte across sequences. A question about message framing is only `not set up` once you have
confirmed there is genuinely one argument in market — and if the shipped copy matches *neither*
option the hypothesis named, say so, because the question was never really put to the market.

### Then say what the run WILL answer — this is the headline, not the caveats

Underpowered for message-ranking is not the same as uninformative. A run too small to rank messages
almost always still answers, at full strength:

- **Deliverability** — bounce rate. Gated by volume, not by reply count; a few hundred sends is
  plenty, and it is the number every other result depends on.
- **List quality by source** — bounce rates between contact sources differ far more widely than reply
  rates do (a 3–4× gap is common where a 1.3× reply gap is not), so a comparison hopeless for replies
  can be decisive here. It tells you where enrichment budget should go next.
- **Cadence** — the share of replies arriving on each touch. The same people receive every touch, so
  there is no selection effect at all; it is the cleanest comparison in any sequence.
- **What buyers say back** — the highest-value output and it needs no statistics. "Four people said
  they already do this with vendor X" is a positioning lesson worth having on four replies, not four
  hundred.
- **Whether the copy irritates people** — unsubscribes and complaints scale with sends, not replies.
- **Sender health** — per-mailbox delivery, which must be clean before volume goes up.

Small groups cannot detect small effects, but they still surface **large** ones — so where a tiny arm
exists, say what a dramatic result would justify rather than dismissing it outright. Never write off
a run as "too small to learn anything"; name what it does answer, and reserve
`what_it_cant_tell_us` for the specific comparisons that genuinely fail the three checks.

Compute `completes_est` from the **follow-up tail**, not just total volume: with a daily ceiling
spread over N emails per contact, only `daily_cap ÷ touches` new people can start each day, so the
window is `contacts ÷ (daily_cap ÷ touches)` working days of intake **plus** the gap to the last
contact's final touch.

### Write the manifest for the exec reader

Every prose string above renders directly on `campaigns.html` in front of revenue leadership. Use
their vocabulary, not a methods section's:

| Don't write | Write |
|---|---|
| base rate, 95% CI, ±2.7 points | "About 18 replies expected; 12 to 28 would be normal" |
| n per arm, minimum detectable effect | "contacts needed in each group", "how big a difference we could spot" |
| underpowered · not instrumented | `list too small` · `not set up` |
| confounds, threats to validity | "reasons a good-looking number might mislead" |
| single-arm observational, no randomisation | "One version of the email to everyone. No A/B test." |

State outcomes as **counts before percentages** ("about 18 replies", not "5.9%") — a reply count is
what gets forecast against. Keep the statistical basis to one line in `method_footnote` so the
numbers stay defensible without being in the way.

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
5. Overwrite the `.campaign.toml` manifest's `[targets]` with the refreshed base-case numbers and
   `plan_md`/`plan_html` if the date changed. Leave `sequences` and `status` alone unless the operator
   explicitly names a change — this step is a numbers refresh, not a re-link.
6. **Re-check `[experiment]` against what is now actually loaded and sending.** Re-run the
   three checks above; a question that was `can answer` at plan time becomes `list too small` the
   moment the real list lands smaller. Refresh `[window]` from the live daily ceiling, and re-read
   `[[experiment.confounds]]` for **decay** — a plan whose urgency rests on dated triggers
   (a competitor launch, a regulation taking effect, a deadline) goes stale the day those dates pass,
   and copy written as "this is coming" then reads as old news.

Do not rebuild from scratch — treat the prior plan as the working draft.

### When reality lands smaller than the plan

Plans are written before the list exists, so a gap is normal — leaving it unreconciled is not. When
the loaded, verified, in-market count is known, **resize `[targets]` to it** and record the original:

```toml
[targets_superseded]
as_of = "<the original plan's date>"
prospects = <original>
emails = <original>
replies = <original>
sqls = <original>
pilots = <original>
reason = "<what changed, in plain language — and name the real binding constraint>"
```

The `reason` renders on the portfolio page as "Why these numbers changed", so make it say which
constraint actually bound. Sending capacity (mailboxes × daily limit) is the usual one and is easily
mistaken for a list problem — if contacts are still queued for verification or companies still need a
named contact, the list was never the limit and the fix is mailboxes, not more prospecting. A target
that quietly shrinks to meet the actuals is not a target; keeping the superseded block is what makes
the resize honest rather than invisible.

## Guardrails

- **Product-accuracy discipline** — tag any capability claim SHIPPED/CONDITIONAL/ROADMAP (never a conditional/roadmap capability as live) and verify cited external facts before they ship: `docs/product-accuracy.md`.
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
