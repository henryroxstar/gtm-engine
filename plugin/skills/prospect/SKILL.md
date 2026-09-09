---
name: prospect
description: >-
  Run the active profile's prospecting routine — discover, qualify, score, and enrich ICP
  accounts, then output a scored brief, Tier-A outreach packs, and a HubSpot-ready CSV.
  Product-agnostic: markets, ICP, and the lead product all come from whichever profile is
  active. Use when the user says "run my prospecting", "find prospects", "build a prospect
  list", "weekly prospecting run", "find accounts for [the lead product]", or "prospect
  [market]". Uses three data sources when connected — Vibe Prospecting (discovery +
  firmographics + Bombora topic-intent + events), RocketReach (contact resolution + Intentsify
  topic-intent + news/hiring signals + job-change timing), and Apollo (contact-resolution
  backstop, buying-intent company search, job-posting signals, bulk enrichment); free
  web-search is the fallback. Respects budget caps before any metered call. The dossier sweep
  (Step 11) checks every tier by default, not just Tier-A — a bulk-sourced account reaching a
  sequence with no research behind its Why Now clause is exactly the gap a Tier-A-only check
  misses. Research does not finish at a sentence: every signal-carrying row also records where
  the fact came from, when it was observed, the verbatim source span it reduces, the entity it
  is actually about, whether its "agents" are AI or people, and what the account is to us
  (prospect/competitor/partner/adjacent) — the fields that turn a clause about the wrong
  company, a stale claim, or a number absent from its source into a load-time type error
  instead of something a careful reader might notice on the third pass. Every finalist also
  leaves with a send/re-angle/drop verdict and a reason, so "not sendable" is a representable
  output rather than an outcome the pipeline has no way to express.
metadata:
  version: "0.13.0"
  phase: "1"
  capability_tier: core
---

# Prospect

> Resolve the **active profile** (the agent provides it; PROFILE + company knowledge load from
> `profiles/<active>/`, never `plugin/`). The lead product is the active company's `default_product`
> from `PROFILE.md` → `products[]` — use its real name throughout, never a hardcoded one.

Produce a run of qualified accounts (default **10: 3 enterprise + 7 startup**) scored against the ICP rubric, each with named personas, a dated "why now" signal, a matched case study, and a recommended hook — plus a Tier-A outreach pack per 🔥 account and a HubSpot CSV for manual import. **No live CRM** (the CSV is the handoff).

**Three data sources, each doing what it's best at (all optional; free web-search is the floor):**
- **Vibe Prospecting** (Explorium) — cold **company discovery + firmographics + topic buyer-intent** (Bombora `business_intent_topics` filter) **+ business events**. The discovery engine.
- **RocketReach** — **contact resolution**: the named buyer's **verified email + direct phone** (deeper per-person database), plus **company topic-intent** (Intentsify tracked topics / `intent` facet), **news & hiring trigger signals**, and **person job-change timing**. Primary for enrichment.
- **Apollo** — the **contact-resolution backstop**: a verified email (never phone — see `discovery-and-budget.md`) when both RocketReach and Vibe miss, plus company search with **buying-intent filters** (a third intent feed, LeadSift-powered) and job-posting hiring signals. Last *paid* source before the web path; both the in-repo worker and Apollo's official hosted MCP expose the same tools, use whichever the session offers.
- **Heat axis:** the rubric scores fit (*who*); intent scores heat (*when*). High topic-intent on **any** feed = **+2**; **two or more** feeds converging on one account (**double-intent**) = **+1 more** (cap at the rubric ceiling). 🆕 new-in-role champions get queue priority, not points. Record `heat` + `intent_feeds` per account. Intent times the touch and picks the angle — **never quote it in outreach copy**.
- **Toggle / fallback:** discovery **defaults to Vibe** — attempt it every run, including scoped/narrow cohort runs, given the large unused budget headroom (`per_run_cap_usd` vs typical spend); web is the **true fallback only**, used when Vibe is genuinely disconnected or erroring, never chosen for session expedience on a small or single-vertical run. Contact enrichment → **RocketReach → Vibe → Apollo → web**: **a RocketReach miss (404 or no verified email/phone) is not the end of the chain — Vibe `enrich-prospects` MUST be attempted next, by default, for every such finalist**; if Vibe also misses, attempt Apollo's person-enrich tool before marking the contact unverified (`apollo_person_enrich` on the in-repo worker, `apollo_people_match` on the hosted connector — tool names differ per surface, see `discovery-and-budget.md` §"Apollo surface note"). Only fall to public web (mark **unverified**) once RocketReach, Vibe, **and** Apollo have all been tried and missed. Signal search → RocketReach when available, else Vibe events + web sweep; Apollo company search adds buying-intent filters where RocketReach/Vibe intent is absent or stale. If a source is disconnected or over its share of the cap, use the next in line; free web is always the floor.

## Load context first

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

1. **Read the PROFILE.** Read `profiles/<active>/PROFILE.md`. Pull: `company`, `brand_name`, `default_product` (the lead product), `target_markets`, `segment_mix`, `emphasize_personas/verticals`, `monthly_tool_budget_usd`, `per_run_cap_usd`, `tools_metered`, and the `vibe_prospecting` + `rocketreach` + `apollo` connection statuses. If no PROFILE exists, run `setup` first (or ask the 3 essentials: markets, segment mix, budget).
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
- **Bulk run (operator states a large target, e.g. "300 accounts"):** standard mode's ~20–30-row `fetch-entities` intake per pass cannot reach a large target — use **bulk mode** instead (full funnel in `references/discovery-and-budget.md` §"Bulk mode"). Steps 1–4 (exclude set, preflight, budget pre-check auto-scaled, funnel sizing) below still apply; Steps 5–7 (discovery, why-now, the record) are replaced by that section's size→filter→export→ingest→score flow; Steps 8–9 (gate/score, enrichment) run the same rubric but **finalists-only enrichment and web-sweep confirmation**, not per-candidate; Step 10's `latest.json` merge happens via `python -m gtm_core.prospects_import finalize` (wraps the same safe merge-only writer + emits the HubSpot CSV in one call) instead of a hand-assembled items file. If any gate was relaxed for the run, set `qualification_path` (e.g. `intent-only-relaxed`) on every affected item — see the item shape in Step 10 below.
- **Re-score mode ("refresh heat", "re-score my prospects", "update intent"):** no new discovery — refresh `heat` / `intent_feeds` on the accounts already in `content/<active>/prospects/latest.json` against *today's* intent, then re-rank Tier-A. Reuses this skill's intent fetch + heat axis; skips discovery, gating, enrichment, and outreach drafting. Full procedure in `references/heat-rescore.md`. Near-zero cost (intent checks are credit-free). Use after a tracked-topic change, for a periodic heat refresh, or to apply the scored heat axis to older runs.

## Workflow

**Step 0 — Interpreter preflight. Run this before anything else, and STOP if it fails.**

```bash
uv run python -m gtm_core.paths || python -m gtm_core.paths
```

One of the two prints the resolved content root and exits 0. Free, reads nothing metered, writes
nothing — its only job is to prove that a real Python interpreter exists **and** that `gtm_core`
imports.

**Whichever form worked is the prefix for every `gtm_core` command below.** The working prefix is
discovered, never assumed, because the two runtimes disagree: the operator's laptop carries a
`modern-python` shim that refuses a bare `python`, and the `gtm-agent` container has no `uv` on
PATH at all. A skill that hardcodes either form fails in one of them.

If **both** fail — or either prints a Windows App-Execution-Alias message (`Python was not found;
run without arguments to install from the Microsoft Store`) — **stop the run and tell the operator
to fix their interpreter first.** Do not proceed. Do not hand-assemble the outputs the CLIs would
have produced.

**Why this is a hard stop and not a warning.** Every gate in this skill is a deterministic CLI. With
no interpreter they all fail *individually and silently*, and the run degrades into prose that looks
like a finished result — which is exactly what happened on 2026-09-07: a Windows machine with only
the App Execution Alias stub ran to completion, spent **~209 premium RocketReach lookups**, and
handed back five "qualified, Tier-A, lane: repair" accounts. What was actually lost:

| Step | CLI | What went dark |
|---|---|---|
| 1 | `latest.json` read + 60-day exclude set | the run could not tell it was re-working known accounts |
| 1 | `python -m gtm_core.slugify` | account folders hand-kebab-cased — the documented way one account silently gets two folders |
| 3 | budget pre-check → `costs.jsonl` | the §R2 cap. 209 metered lookups with the guard non-functional and **no cost row written** — no cap, no audit |
| 7 | `gtm_core.account_integrity` | **the gate that refuses.** The judge *ranks*; this is what says no. Every lane verdict was a model's opinion |
| 10 | `gtm_core.prospects_consolidate` / `prospects_import finalize` | the consolidate sweep and the merge-only writer; `latest.json` never learned about the accounts |
| 11/13 | the dossier sweep + status page | no coverage check, and nothing for the operator to read the run off |

The `lane` on all five was assigned by hand, not by the router. A run that gets past Step 7 without
this preflight having passed has produced a list, not a qualified list.

**Fix on the operator's machine** (Windows is where this bites — `winget install Python…` alone
often does not fix it, because the WindowsApps alias still shadows the real interpreter on PATH):

```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

then, in a **new** shell from the repo root, `uv sync` and re-run the probe as `uv run python -m
gtm_core.paths`. `uv` provisions its own Python 3.11+, so the alias never gets a vote. `scripts/bootstrap.ps1`
does all of this in one step. Everywhere else: `bash scripts/bootstrap.sh`.

**Step 1 — Init, mode, & exclude set.** Read the current state; do not sweep yet. The consolidation
sweep runs **once per run, at Step 10**. It is cumulative over *every* `prospects-*-hubspot.csv` on
disk, so that one run also folds in whatever a previous, interrupted run wrote and never
consolidated — this step used to run the identical command for that reason, which was one command
twice for one effect.

The whole picture lives on one page: `content/<active>/email_campaign_status.html` (account backlog →
email funnel → ready/verifying/blocked, plus cost and next steps), refreshed by every sweep. Point
the operator there when they ask "where's the data / how many are ready / what's next" instead of
enumerating CSVs. Confirm the mode (§Modes above) — **bulk mode** if the operator stated a large target the standard intake can't reach, **standard mode** otherwise — and state which in the run header.

**Where output goes — two folders, not interchangeable.** Run-level files (the run markdown, the
export CSV, `latest.json`, the pool) live under `content/<active>/prospects/`. **Per-account
deliverables — outreach packs, dossiers, briefs — live under
`content/<active>/accounts/<canonical-slug>/`**, which is the only place the outreach-log rollup
reads (it globs `accounts/*/prospects-*outreach-*.md`). A pack written anywhere else is invisible
to the rollup and to every later run looking for prior work. Resolve the slug with
`python -m gtm_core.slugify "<company name>"` — never hand-kebab-case it, or the same account ends
up with two folders. Print this profile's exact paths at any time:
```bash
uv run python -m gtm_core.prospects paths --profile <active>
```

**Exclude set.** Build the **60-day exclude set** from prior run files in that folder (`prospects-*.md`, `prospects-*-hubspot.csv`): any company published <60 days ago is excluded. Also read `content/<active>/prospects/latest.json` if it exists — any account with `status: contacted|qualified|disqualified` is excluded from this run (respects agent and dashboard edits between runs). If an account tracker spreadsheet exists in the folder, also exclude its worked accounts and offer to append new rows at the end. **Pick the data path:** discovery **defaults to Vibe** whenever `tools_metered` includes it — actually attempt a Vibe call (e.g. `estimate-cost` or a small `fetch-entities` probe) before falling back; do not skip straight to the web-search path because the run is small, scoped, or narrow. Fall back to web only when that attempt fails or Vibe is confirmed disconnected, and **state the specific reason** in the run header (not just "unavailable"). **A null/empty result from RocketReach `company_search` is not a discovery result and does not satisfy this requirement** — that tool checks signals on a candidate you already have, it doesn't generate candidates; if it comes back empty, you still owe Vibe an attempt before web. **If Apollo is connected, run its free preflight once at the top of the run** — `apollo_usage` on the in-repo worker, or `apollo_users_api_profile` (with `include_credit_usage: true`) on the hosted connector. **Tool names differ per surface — check which Apollo tools the session actually offers before concluding Apollo is absent** (`discovery-and-budget.md` §"Apollo surface note" has the full mapping). Two failure modes to tell apart, because they look similar and mean opposite things: (a) **no Apollo tools in the session at all** ⇒ genuinely not connected, proceed without it; (b) tools present but a data call returns **`error_code: API_INACCESSIBLE`** ⇒ connected but the **Apollo plan doesn't include API access** (this is the case on a Free Apollo plan — verified 2026-07-27). In case (b) do **not** retry, do **not** report it as a miss or an outage: state "Apollo: API not included in plan" in the run header and fall through the waterfall exactly as if Apollo were absent. A non-zero Apollo credit balance does **not** mean the API is reachable — those credits are spendable in Apollo's web UI only. Contact enrichment via **RocketReach** if connected, else Vibe `enrich-prospects`, else **Apollo**'s person-enrich tool if connected and its plan allows API access, else public web (unverified). Note in the run header which sources are live and, for any fallback, why.

**Step 2 — Connectivity preflight (MANDATORY, free, before ANY spend).** Probe every connector with its cheapest liveness call, then adjudicate the result — **a note is not a gate**. On 2026-08-11 a bulk run spent ~$77 discovering and scoring 500 accounts and delivered **zero contacts**, because RocketReach was not loaded into the session and Apollo was paywalled. Both were knowable in ten seconds for nothing. Discovery spend is **not** recoverable by connecting the tool afterwards — you cannot retro-fit contacts onto a finished run without paying again.

Probe (all free, no credits): RocketReach `ping` then `account` (returns plan + per-type credit balances — check `premium_lookup.remaining` > 0, not merely that the server answers); Vibe `autocomplete` on any field; Apollo `apollo_usage` / `apollo_users_api_profile`; Saleshandy `list_sequences`. Record each as `ok` | `absent` (no tools in session) | `api_inaccessible` (authorized but plan blocks data) | `error`, then:

```bash
uv run python -m gtm_core.preflight --profile <active> \
  --observed '{"vibe":"ok","rocketreach":"absent","apollo":"api_inaccessible","saleshandy":"ok"}' \
  --need discovery,intent,contacts
```

It maps connectors → capabilities (`discovery`, `intent`, `double_intent`, `contacts`, `sequencing`), **exits 2** when a requested capability is gone or down to the free web floor, and warns when one is merely on a **fallback** provider (e.g. contacts via Vibe because RocketReach is absent — materially worse: no phone, lower match rate). Put the rendered table in the run header, and repeat any degradation in the final report. **On exit 2, stop and tell the operator what to connect** — do not spend and then apologise. `--need` must list what this run actually promises: a run that will produce outreach needs `contacts`; a re-score needs only `intent`.

Distinguish the two Apollo failure modes (they look alike, mean opposites): no Apollo tools at all ⇒ genuinely absent; tools present but `error_code: API_INACCESSIBLE` ⇒ connected on a plan without API access — record `api_inaccessible`, do not retry, do not report it as an outage. **A missing MCP server is not a broken API**: RocketReach was reported "not working" on 2026-08-11 when in fact the server simply was not loaded in that session; `account` later showed 1,285 premium lookups remaining and every rate limit healthy. Check `claude mcp list` before concluding a provider is down.

**Step 3 — Budget pre-check (only if using a metered source).** Read budget caps. Before any fetch/lookup, estimate spend across **all connected metered sources** — Vibe `estimate-cost` / credit balance, the RocketReach metered-unit count against its plan quota, **and**, if Apollo is connected, its remaining credit allowance from the Apollo preflight tool — and show it. The monthly cap and what it covers come from PROFILE (Claude Max is a flat brain seat, not metered here; RocketReach and Apollo signal/person **searches** are credit-free — only enrichment and Apollo company search spend); if the run would breach `per_run_cap_usd` or the remaining monthly budget, trim (drop optional signal layers, then enrichment depth) or fall back to the web path — never silently overspend. If Vibe balance < ~200 credits, RocketReach is near its plan quota, or Apollo's remaining allowance is low, tell the user before spending — never auto-purchase.

**Step 4 — Funnel sizing (MANDATORY when the operator names a number; free; before ANY spend).** A target is ambiguous and the ambiguity is expensive: "500 accounts" almost always means **500 deliverable contacts**, not 500 discovered rows. Every stage has a yield, and their product is small — so a run sized as if `discovered == delivered` narrows silently and lands a fraction of the ask at the very end, after the money is spent. On 2026-08-12 exactly that happened: 500 discovered → 7 sequence-ready, discovered only in the final report.

Resolve three things with the operator (or state your assumption explicitly), then size:

1. **Unit** — delivered contacts vs discovered accounts. Ask if it is not obvious.
2. **Tier** — `a+b` (the normal cut for a volume send) or `a` (Tier-A only). **Tier A is one thing: a rubric score at or above the profile's Tier-A threshold** (default ≈70% of the rubric ceiling — `gates-and-scoring.md` §Tier-A). It is a threshold, not a quota and not a share: about 16% of published accounts clear it in practice and Step 8 aims for ≥3 per run, but those are an observed yield and a run target — neither defines the tier, and neither may promote a row that did not score. A and B are the same rubric at different thresholds, not different treatment classes; the only difference is depth (Tier-A earns a 1:1 pack, Tier-B goes in the merge sequence). Neither ever carries a meeting ask.
3. **Why-now mode** — `structural` (a durable fact about what the company does), `news` (a dated public event), or `hybrid`. **This is the single biggest lever on cost:** `news` needs roughly 3× the discovery of `structural`, and news decays — a prior run found only 2 of 286 signals still fresh weeks later.

Then run, and paste the plan into the run header:

```bash
python -m gtm_core.funnel --target <delivered> --tier <a+b|a> --why-now-mode <structural|news|hybrid> \
  --profile-root profiles/<active> --pool-available <net-new after exclusions> \
  --backlog-ready <already-qualified accounts needing only a contact> --lookup-credits <remaining>
```

It returns the discovery target, per-stage expected counts, and lookups required — or **exits 2 and refuses** when the pool or credits cannot support the ask, naming the shortfall and what would fix it. A refusal is the correct outcome: report it and re-scope with the operator rather than starting a run that cannot finish.

**Work the backlog before discovering.** The cheapest deliverable contact is an already-qualified account that needs only a lookup. The sizing call subtracts `--backlog-ready` first and says so. Query `latest.json` for in-market accounts with a contact name but no email, and for qualified accounts still at status `new`, before spending a credit on discovery.

**Per-stage tripwire (during the run).** After each stage call `gtm_core.funnel.check_stage(stage, actual_in, actual_out, plan)`. A stage below **70%** of its modelled yield returns `ok: false` — **STOP and report the projected delivery**, then re-size or widen. Never carry a silently smaller set forward. At run end call `record_actuals()` so the profile's `knowledge/funnel-yields.toml` self-corrects.

**Step 5 — Cold discovery.** One enterprise pass + one startup pass + one **in-market pass** (topic-intent filter) **per market**, plus the credit-free **RocketReach signal pre-flag pass** when signal search is available (paste-ready filters in `discovery-and-budget.md`). **Apollo's company search (`apollo_company_search` in-repo / `apollo_mixed_companies_search` hosted) is an OPTIONAL extra in-market pass** — it is metered per page — the rate is stated on the `apollo_company_search` tool itself, so read it there rather than assuming one (unlike RocketReach's free signal search), so gate it behind the Step 3 budget check and only run it when Vibe/RocketReach intent is absent, stale, or the profile explicitly wants Apollo's buying-intent topics cross-checked; it never replaces Vibe as the primary discovery engine. On the web path, build the candidate list by searching for ICP-matching companies per segment + market + the website keywords. Aim for a healthy candidate pool (≈2–3× the target) to survive gating. **Bulk mode:** skip this step's per-pass intake — follow `discovery-and-budget.md` §"Bulk mode" steps 1–6 instead (size → in-query filter passes → cost gate → export → `python -m gtm_core.prospects_import ingest`), which produces `candidates-<run-id>.json` in place of a Step 5 candidate list.

**Step 6 — "Why now" signal hunt (0 credits).**

**Before you sweep, check whether the research already exists.** Step 4's "work the backlog before
discovering" applies to signals too, and the sweep is the most expensive free thing this skill does.
An account whose dossier is already on disk usually carries the source, date, and verbatim span the
six fields want — that is a **backfill**, not a research pass:

```bash
python -m gtm_core.prospects_consolidate accounts-needing-dossier --profile <active>
```
An account **absent** from that list already has a dossier: open it and promote what it carries onto
the row via `gtm_core.signal_backfill --promote` (Step 10) instead of re-researching it. Measured
2026-08-30 on the live pool, of 724 rows carrying a substantive claimed fact with no provenance on
the row, **321 already had a dossier on disk** — 159 of them with a source URL in it. Re-sweeping
those is paying twice for a fact already bought. Verify the join per row before promoting: a URL in
the dossier is not automatically the source for *this* clause.

Then run the fixed 6-source web sweep per candidate — the intent/trigger feeds pre-flag, the sweep **confirms and dates** (the 🔥 cites the public source, never the feed). Tag each hit `[type | date | URL | H/M/L]`. Keep the strongest as the 🔥 signal; it must map to an ICP "why now" trigger **and pass both
tests in Step 7 — sendable and sellable — before you stop looking.** A fact that fails either one
is not a why-now you can use, and finding that out at staging means the research spend is already
gone: measured 2026-08-29 on the live pool, **74% of rows carrying a researched fact reduce to a
clause that cannot ship**, and of the ones that can, **40% attest a premise no argument can carry**.
Both are cheap to check here and expensive to discover three gates later. When the account maps to a vertical pack, prefer its **"Why now — urgency drivers"** + **"Regulatory & compliance landscape"** — they name the specific dated instruments (e.g. a named runtime-governance mandate for that industry) that count as a why-now, so you qualify on the real industry trigger rather than a generic guess.

**A candidate that fails a GATE is dropped. A candidate with no dated hit is ROUTED, not dropped.**
The distinction is the whole point and it used to be collapsed:

> **Gates disqualify on properties of the ACCOUNT. Research thinness downgrades the ANGLE, never
> the account.**

A Gate A/B/C failure is a finding about the company — it is a competitor, it is a body shop, its
agents draft text a human sends, it is outside the market. Drop it. But "the sweep found nothing
dated this pass" is a fact about *our research*, not about them, and hard-dropping on it silently
converted an unworked account into a rejected one. That is how `latest.json` came to hold 545
accounts whose `why_now` asserts no signal was found, 541 of them with a blank `verdict`, sitting
in the backlog looking exactly like researched accounts.

So: a candidate that clears the gates but yields no usable signal leaves this step alive, as a
**generic-lane candidate** (Step 8 stamps the lane). It is not enrollable on a personalised body —
there is no clause to carry one — but it is enrollable on a body that makes no account-specific
claim at all. **Keep looking first**: the generic lane is the fallback after research fails, never
a substitute for doing it, and it is emphatically not the destination for a high-fit account you
simply have not worked yet.

### A dropped candidate is a VERDICT, never a sentence in `why_now`

When the sweep comes back empty, that outcome is recorded as **`verdict: re-angle`** (or `drop`)
with a `verdict_reason` — the vocabulary Step 8 already defines. **Never write the negative result
into `why_now`.** It is the field every downstream count reads as "this account has a signal", so a
`why_now` reading *"Bombora topic-intent: agentic ai (79) — feed signal; dated public why-now not
found"* makes an unresearched account indistinguishable from a researched one in every query that
matters — the account backlog, the status dashboard, and the funnel sizing in Step 4.

This is not hypothetical and it is not rare. Measured 2026-08-30 on the live pool: **545 accounts
carried a `why_now` that asserts no signal was found** — feed-only hits, "no dated public article
found this pass", "firmographic + cohort fit only" — and **541 of those 545 had a blank `verdict`**.
452 sat at status `new`, in the backlog, looking exactly like researched accounts. The whole
population then reads as 99% `why_now` coverage, and a reader sizing the remaining work from that
number sizes the wrong work.

Note what this costs to comply with: nothing. The refusal vocabulary is two steps down this same
file, and `verdict` is the one column research owns outright. A free-text field that can hold a
negative result is the same defect `signal_record` exists to remove — an unverifiable claim in prose
— one field to the left. The candidate stays in `latest.json`; what changes is that it says what it
is.

**Step 7 — Record the signal, don't just write the clause.** A why-now is not finished when a
sentence exists. The sentence is the *output*; what makes it checkable later is the record behind it.

### The two tests a fact must pass — check them BEFORE you record it

Research is the most expensive input in the pipeline, and both of these are free to check while
you still have the source open. Neither is enforced here; both are enforced later, by which point
the spend is unrecoverable.

**1. SENDABLE — can this fact reduce to a clause that ships?** The clause is what renders into
`{{Why Now}}`, and `gtm_core.merge_hygiene.signal_clause` is fail-closed: a fact it cannot reduce
goes to the generic arc instead. Aim the *research* at a fact that satisfies this, rather than
recording one that cannot:

- **No digits at all** — no dates, no money, no counts. `signal-stray-digit` is a hard ERROR, and
  freshness lives in `signal_observed`, which is why the clause is forbidden from carrying it.
  This is the single biggest loss in the pipeline: a funding round is the most common thing a
  research sweep finds and **"$27M Series A for agentic healthcare-finance AI" can never open an
  email here.** When the money is the only fact, keep looking for what the money *bought*.
- **Between `SIGNAL_MIN_CHARS` and `SIGNAL_MAX_CHARS`** (`gtm_core.merge_hygiene`) — a standalone
  sentence, not a paragraph of notes and not a fragment.
- **A verbatim reduction of `signal_evidence`** — you may drop trailing facts; you may not reword.
  A reworded claim is a new claim about a real company that nobody verified.
- **Carries agent/AI content** — `signal-off-topic` fails a clause whose subject the body then
  makes a claim about. A clause with no agent content under an agent body is the most reliable
  "this is generated" tell a recipient gets.
- **No trailing source citation or date stamp.** Those belong in `signal_source_url` and
  `signal_observed`; a clause reading `agentOS launch 2026-05-14 - acme.example/agentos` is a
  research note, not an opener.

**2. SELLABLE — does this fact attest a premise an argument can carry?** Check it against
`profiles/<active>/knowledge/premise-vocab.toml`, which is the tenant's own list of what a body may
assume the recipient's evidence establishes. A fact can be fresh, sourced, on-topic and about the
right company and still be unusable, because no spec can rest on it.

**`ships-agent-product` is a FILTER, never a spec premise.** "They built an agent" does not entail
"their agent crosses an organisational boundary", and every argument in this profile is a boundary
argument. That was tested on 2026-08-25 across 56 rows and refuted — 3 send / 6 re-angle / 47 drop,
53 of 56 carrying the same defect — and the vocabulary file says so in capitals. Use it to segment
*who ships agents at all*, then pair it: a row attesting **both** it and `cross-org-agents` is the
strongest candidate there is. **Prefer a boundary fact** — a named partner, an MCP/A2A/AP2 surface
other organisations call, an agent marketplace, a vendor ecosystem, agentic commerce with a named
counterparty — over an internal rollout or a raise. Those are what the arguments are written for.

Populate all six fields on every row that carries a `signal_clause` — they are real columns
(`gtm_core.signal_record`), carried through consolidation, and gated at load time:

| field | what goes in it |
|---|---|
| `signal_source_url` | the **https** page that carries the fact. One source. **Never a search-results URL** — that is where you went looking, and it returns something different next month, so the claim becomes uncheckable exactly when someone tries to check it. |
| `signal_observed` | ISO `YYYY-MM-DD` — when the source published or showed it. Freshness lives **here**, not in the clause, which resolves the old contradiction where a clause had to be dated and was forbidden from containing a date. Anything older than 210 days blocks at load. |
| `signal_evidence` | a **verbatim span** of that source — the sentence the clause is a reduction *of*. Not your summary of it. This is what the clause gets checked against. |
| `signal_subject` | the entity the fact is genuinely **about**. Usually the account. When it is the investor, the parent, the acquirer, or a same-named stranger, write that — the mismatch is the finding, and it can only fire if the truth is on the row. |
| `signal_agent_kind` | `ai` \| `human` \| `none` \| `unclear` — what the word "agent" denotes here. An insurance carrier's agents are people; a staffing firm's are recruiters. Both read as an AI-agent buying signal to a regex **and to a hurried human**. `none` is the correct, common answer for a funding round. |
| `category_relation` | `prospect` \| `competitor` \| `regulator` \| `partner` \| `adjacent` \| `unclear` — what this account is to us. Decide it here, during research, against `profiles/<active>/knowledge/competitors.toml`. Deciding it at send time means deciding it never. **`regulator` is not a niche case**: a supervisory or standards body writes the rules a governance pitch appeals to, and on 2026-08-21 one was recorded `prospect` — the only value that fit — and staged on a clause describing the framework it had published itself. A fail-closed field cannot fail closed on a value it cannot say. |

`unclear` is a legitimate value and it **blocks**. That is the point: it routes the row back to
research instead of into a sequence. Leaving a field blank does the same thing, so there is no
version of this you can skip your way past.

**Check the record when you write it, not when the judge reads it.** "Gated at load time" means
`account_integrity` runs `signal_record.audit_records` on the list about to enroll — which is the
last possible moment, after research is closed and copy is written. On 2026-09-04 three rows
carried `signal_agent_kind: none` while their own `signal_evidence` said "agent", and one carried a
`signal_subject` left over from a different signal on the same account; all four had sat at
researcher `verdict: send` for weeks and were first caught as `grounding: research=1` inside the
judge, where the only remedy was a data fix plus a re-route. Run the same audit on the rows you
just wrote, before Step 8, and fix or drop what blocks:

```bash
uv run python -m gtm_core.account_integrity --csv <the rows you just recorded>.csv --profile <active>
```

`agent-kind-contradiction` (clause says "agent", kind says `none`) and `signal-subject-mismatch`
(subject ≠ the row's `company` after normalisation) are the two that survived longest; both are
one field each, and both are yours to get right here.

**Also record which matrix SIGNAL this account's own why-now attests — this is the field that was
missing, not just hand-typed and thrown away.** Two more columns, neither gated at load (a list
without them still passes — this is new, not a migration), but both are what makes the message
axis checkable the way the persona axis already is:

| field | what goes in it |
|---|---|
| `signal_column` | the matrix's **signal column label**, written verbatim, for this account's own **segment** grid — e.g. `Compliance event (audit, breach)` or `Partner / third-party agents entering the estate`. This is the one non-derivable fact: the account's persona already comes from its title and its segment is already a column, so this is the only atom worth writing by hand. `gtm_core.hook_coverage.derive_row_cell` combines it with the row's own title and segment to compute the full cell — record this and `hook_cell` below is optional. |
| `hook_cell` | the full matrix cell (**segment × observed signal**), written verbatim in the matrix's labels — a shortcut if you already know it, but `signal_column` is what actually needs writing down. Recording either is what turns `cell-segment-fit` / `cell-signal-fit` / the merge-render linter's `signal-cell-mismatch` from heuristics into an equality test at drafting time — today the spec declares a cell and nothing says which cell the *row* belongs to, so the check has to infer the row's half from free text. This skill already picks a hook per account and then throws away which one; stop throwing it away. |

Resolve the matrix through `python -m gtm_core.resolve_knowledge hook-matrix.md --profile <active>
[--product <slug>]` (product-first, profile-fallback per CLAUDE.md) rather than reading
`knowledge/hook-matrix.md` directly, so the read stays product-aware.

**Why this exists rather than more copy review.** Three defect classes were invisible for months —
a clause naming a company renamed years earlier, a clause about the investor opening an email to the
portfolio company, and a number that drifted across three documents until it described a mechanism
its source never measured. Each survived two review rounds and was caught on the third by someone
reading carefully. None of them is a matter of taste; all three are contradictions between the clause
and its source, and none was detectable while the source was not written down. With the record they
are **type errors** — a token in the clause that does not appear in the quoted evidence, a number the
source does not contain, a subject that is not the recipient — checked deterministically at load.

> **Verify attribution before a feed event becomes a why-now.** Vibe's `fetch-businesses-events` is a cheap way to batch this sweep (`match-business` → `fetch-businesses-events`, ~1 credit/row, far cheaper than one web search per account) — but its events are attributed loosely, and on 2026-08-12 **more than half of the candidates it returned were wrong or unverifiable**. Three failure modes, all seen in one run: a **name collision** (a health system drew a release from a *credit union* that shares one word of its name; a hospital operator drew a *similarly-named automation vendor*; a company drew news from its own *separately-listed spin-off*), a **partner's or vendor's news** filed under the account (one account drew its AI vendor's own product announcement), and a **product that does not exist** (a "launch" no primary source corroborates). Checking whether the account's name appears in the event text is necessary but **never sufficient** — substring matching is precisely what lets a same-word stranger pass as the account. Open the source for every why-now that will reach copy, and record the outcome as `VERIFIED`, `VERIFIED-CORRECTED`, or killed-with-reason. An unverified event is not a why-now; leave it out rather than quoting it.

**Step 8 — Gate & score.** Apply the **profile's** segment gates, then its rubric (from `icp-personas.md` / its linked scoring file). Drop anything below the profile's publish threshold (default ≥6; the profile may set its own per-segment threshold + ceiling). **Heat axis:** after the rubric, add **+2** for a topic-intent **score ≥75** on any feed — Vibe row scores are inline (`business_business_intent_topics`); for RocketReach/Intentsify read the optional weekly snapshot `content/<active>/prospects/intent/rr-intentsify-latest.json` when present and <10 days old, else use the credit-free `intent`-facet filter hit; for Apollo, a company-search hit against a tracked buying-intent topic (Apollo Settings → Buying Intent must have tracked topics configured first, or treat the feed as absent — see `discovery-and-budget.md`) — and **+1 more** when **two or more** feeds converge (**double-intent**); a score 60–74 is elevated but earns no points. Cap at the rubric ceiling (`gates-and-scoring.md` §Heat axis). Record `heat` and `intent_feeds`. Select the run mix across markets per `segment_mix`; order the Tier-A queue by heat → 🆕 new-in-role → signal recency. Aim ≥3 Tier-A; if short, note "broaden next run."

**Every finalist leaves research with a verdict — `send`, `re-angle`, or `drop` — plus a
`verdict_reason` for anything that is not `send`.** "Not sendable" has to be a representable output
of this step, or the shape of the failure is fixed in advance: a research run asked for 442 emails
produces 442, because nothing in the pipeline can say *no* about an individual row. It is not a
score threshold restated — a 9-scoring account whose only dated signal turns out to be about its
acquirer is a `re-angle`, and a competitor is a `drop` at any score.

**"Finalist" is not the only row that gets one.** A candidate the Step 6 sweep dropped never reaches
this step, so for months nothing assigned it a verdict and the negative result went into `why_now`
instead (545 rows, 541 of them with `verdict` blank). Any candidate that leaves research **for any
reason** carries a verdict: a Step-6 empty sweep is `re-angle` with the reason, exactly as if it had
been decided here. The row is still in `latest.json` either way — the only question is whether it
admits what it is.

- **`send`** — the account is right, the seat is right, and the clause survives its own record.
- **`re-angle`** — real account, wrong angle. The clause cannot carry this pitch (it is about the
  parent, it is a human-agent homonym, it is stale, the proof does not map). Goes back to research.
- **`drop`** — a competitor, a partner where cold outbound is the wrong motion, a dead account, an
  entity that no longer exists under that name. Write the reason; it is what stops the same row being
  re-sourced next month and re-decided from scratch.

A `drop` should also go to the durable suppression ledger with its reason, not just be left out of
one file — `.pool/suppression.csv` is the source of truth, and a row omitted from a build output
comes back on the next sweep.

### The verdict is not the enrollment gate — the LANE is

`verdict` says what research concluded. **`lane` says which body the row can carry**, and they are
different questions. Enrollment reads the lane (`gtm_core.account_integrity --lane <lane>`), which
widens `--require-verdict` to that lane's admissible set:

| Lane | Admissible verdicts | The body it carries |
|---|---|---|
| `personalised` / `signal` | `send` | a clause grounded in this account's own recorded signal |
| `repair` | `send`, `re-angle` | a personalised body whose defect is fixable — re-research, re-draft |
| `generic` | `send`, `re-angle`, **blank** | a body that makes **no account-specific claim** |

**Read that middle column again: `re-angle` and blank are enrollable in the generic lane.** They
were not always — the gate hard-dropped every non-`send` row and stranded 461 of 598 pooled rows,
which is the defect `gtm_core.lanes` was built (2026-09-03) to fix. In the generic lane
`no-dossier`, `verdict-missing` and `relation-unresolved` report as one advisory line each rather
than per-row errors, because a body referencing no research cannot be wrong about research. Every
other class — competitor, academic domain, stale artifact, why-now shape, freshness — stays a hard
ERROR in **every** lane. A calibrated judge's `drop` never removes a generic row either: the judge
read the *personalised* body, and its rejection of that argument is why the row is in this lane.

**Why the generic lane is the safer failure, not the lax one.** On 2026-08-21 the operator rejected
11 of 12 annotated emails, and six rejections were one defect in different words: the body claimed
plurality the evidence did not attest. That is **ungrounded specificity**. A generic body asserts
nothing about the recipient, so it *structurally cannot* commit that defect. It trades reply rate
for truthfulness. Sending a thin-research personalised body makes the opposite trade, silently.

**Stamp `lane` and `lane_reason` on every row** (Step 10's item shape). Do not invent a new tier for
this: `tier` means "rubric score band" and overloading it breaks cross-segment conversion analysis.
Lane is the axis; tier stays what it is.

**Then ask the operator once — never auto-include.** Same interaction shape as Step 11's dossier
sweep: name the count and a few example companies, state the tradeoff in one line, and wait.

> "N accounts cleared the fit gates but have no usable signal — e.g. <A>, <B>, <C>. Route them to
> the generic lane (no account-specific claim, lower expected reply rate, measured separately), or
> hold them for research?"

**Respect the share cap before you ask.** `generic_lane_share_cap` in
`content/<active>/settings.json` (default 0.5) bounds generic as a fraction of the run's enrollable
rows. Over the cap, propose only up to it and say which accounts you held back and why. The cap
exists because a lane that accepts blank verdicts is exactly the lane every under-researched row
drifts into, and a pipeline that is 100% generic has stopped doing research without anyone deciding
to. Reply rate **by lane** is already reported (`gtm_core.cells`, with a Wilson interval), so this
is measurable — but only if generic never silently becomes the default.

**Step 9 — Persona enrichment.** For each finalist, identify the segment personas (`profiles/<active>/knowledge/icp-personas.md`). **Contact resolution → RocketReach first** (when connected): resolve the top-1 persona's **verified email + direct phone** via `rocketreach_lookup` (the in-repo VPS worker) or `person_lookup` (the official connector) — or the bulk variant (`rocketreach_bulk_lookup` / `BulkLookup`, capped at 25 finalists per call) when resolving several finalists at once. See `discovery-and-budget.md`'s "Surface note" for the full tool-name mapping between surfaces. RocketReach **searches are credit-free; person lookups (a.k.a. exports) are the metered quota** — spend a lookup only to pull a finalist's contact, never a candidate's, and pace against the plan's remaining monthly allowance (`PROFILE.md` §"Connector plans & entitlements"). **Vibe is the mandatory next step, not an optional one, whenever RocketReach misses for a finalist** — a RocketReach `404`, a resolved contact with no valid/graded email, or no plausible named contact at all: before marking that finalist unverified/unresolved, run a Vibe `fetch-entities` (`entity_type: prospects`, filtered by the finalist's company + persona job title) and, on a match, `enrich-prospects-contacts` on the resulting table. Vibe supplies firmographics + top-2 persona profiles + company intent this way. **If Vibe also misses (or Vibe isn't connected), Apollo is the next mandatory step before falling to web** — call Apollo's person-enrich tool for that finalist — `apollo_person_enrich` (in-repo worker) or `apollo_people_match` (hosted connector) — passing name/linkedin_url + organization_name or domain, or the bulk variant (`apollo_bulk_person_enrich` / `apollo_people_bulk_match`, ≤10 per call) when several finalists need it at once. **Tool names differ per surface; call whichever the session offers** (`discovery-and-budget.md` §"Apollo surface note"). If the call returns `error_code: API_INACCESSIBLE`, Apollo's plan has no API access — treat Apollo as absent for the rest of the run and go straight to the web path; that is a paywall, not a miss. Apollo charges 1 credit only on a match with an email (a miss is free) and **never reveals a phone number** — Apollo's phone reveal resolves asynchronously via a webhook this deployment has no inbound path for, so this integration doesn't request it; a finalist's phone, if ever needed, stays RocketReach-only. Skipping straight from a RocketReach or Vibe miss to "unresolved" without attempting the next source in line is a process gap, not a valid outcome — do this for every finalist, every run. `enrich-business` remains an escape hatch (≤3/run) for company-level gaps only. Only after **RocketReach, Vibe, and Apollo have all missed** (or are disconnected) does a contact fall to the **web path** (no paid source): pull names/titles from public LinkedIn / company pages and mark emails **unverified**. An account is complete with ≥1 champion/primary-buyer contact. Run the **new-in-role check** on finalist personas (`job_change_signal` ≤3 months, or Vibe `current_role_months` 1–6, credit-free): mark hits 🆕 — they jump the Tier-A queue and take the hook matrix's new-in-role column.

**Step 10 — Generate outputs.** Run-level files under `content/<active>/prospects/`; per-account
packs under `content/<active>/accounts/<canonical-slug>/`, per the rule in Step 1:
- `prospects-YYYYMMDD.md` — header + one section per account (per-account template). Pull the **recommended opening hook** from the hook matrix (don't free-write); map a case study from `profiles/<active>/knowledge/case-studies.md` — when the account is in a vertical with an industry pack, prefer that pack's **"Matching proof shape"** for the proof and seed the hook from its **"Email angles"** (industry-level `[bracketed]` fills only, never account-specific).
- `prospects-YYYYMMDD-hubspot.csv` — one row per contact, per the CSV map.
- **Fold this run's emails into the loadable pool now — don't wait for the whole flow to finish.**
  Consolidating emailed contacts into a sequencer-ready list has historically been a manual,
  end-of-flow step the operator often can't reach (a run gets interrupted, or only part of the
  flow runs) — exports then pile up invisibly, since `latest.json` is account-level and never
  holds person+email rows. Run the sweep right after writing the CSV above, so even a run that
  stops here still lands its emails:
  ```bash
  python -m gtm_core.prospects_consolidate consolidate --profile <active>
  ```
  This dedupes by email against every prior `prospects-*-hubspot.csv`, excludes the Do Not Contact
  list (refresh the cache the sweep reads via `python -m gtm_core.prospects_consolidate` — see its
  module docstring for `dnc_cache_path()`; the email-sequence skill's provider preflight is what
  keeps that cache current), and gates by deliverability confidence — RocketReach A/A- or
  `verified`/`account-folder-verified` → `sequences/ready-to-load.csv` (the **one** visible list,
  safe to load today); everything else with no or weak signal → the hidden hold queue
  `sequences/.pool/needs-verification.csv` (needs a verification pass, never load blind); known-bad
  grades → excluded and logged. The full audit `master-list.csv` and all snapshots also live under
  `sequences/.pool/` — so a human browsing `sequences/` sees exactly one load file. The sweep is also
  person-unique and cross-address DNC-clean (the same human under two email formats, or already
  contacted under a different address, is collapsed/excluded).

  **Correcting a fact on a row that already exists is a DIFFERENT write, and the obvious one is
  wrong.** This sweep skips an already-present email except to re-rank its confidence, and no flag
  re-reads `why_now` from an export — so a corrected clause written into `ready-to-load.csv` (or
  any pooled CSV) is silently discarded by the next sweep. A lock does not help: the file is
  *rebuilt*, not raced. On 2026-08-29 one batch was lost to this three times, twice after being
  re-applied under `gtm_core.locks.profile_lock`. The record's durable home is the **account**:
  ```bash
  python -m gtm_core.signal_backfill --list <list.csv> --records <records.json> \
    --profile <active> --promote
  ```
  `--promote` writes each record onto its account in `latest.json` and reports any record whose row
  has no account rather than dropping it; the next `consolidate` carries it back onto every row.
  Aiming `--out` at a pooled CSV is refused outright.

  **"How many emails are ready to send"
  is always this sweep's `ready_to_load` output, never a hand-built or dated list.**
- For **each Tier-A (🔥)** account: `prospects-YYYYMMDD-outreach-[company-slug].md` using the Tier-A pack template — a pre-drafted **4-touch / 2-channel arc** (LinkedIn first, then email, over ~12 days) threading **4–6 personas** with role-differentiated first lines. The exact touch shape lives in `references/output-templates.md`, which renders the ceiling `voice.md` sets — do not restate a touch count here, or the two drift (they did: this file said 5 while `voice.md` said 4, and packs shipped both). **Read `profiles/<active>/knowledge/voice.md` first**; every line must pass the voice rules. These are drafts — never auto-send, and never presented as sendable until the pack passes `tests/linter/outreach_pack_linter.py --format prospect-pack` with zero errors (run it `--batch` over the run so cross-account subject/hedge reuse is caught too).
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
    "intent_feeds": ["vibe-topic|rr-intent|rr-news|rr-jobs|apollo-intent"],
    "new_in_role": false,
    "signal_source_url": "https://<the page that carries the fact>",
    "signal_observed": "YYYY-MM-DD",
    "signal_evidence": "<verbatim span of that source>",
    "signal_subject": "<the entity the fact is about>",
    "signal_agent_kind": "ai|human|none|unclear",
    "category_relation": "prospect|competitor|partner|adjacent|unclear",
    "signal_column": "<the matrix's signal column label for this account's own segment, verbatim>",
    "hook_cell": "<optional — the full matrix cell, verbatim; derivable from signal_column>",
    "verdict": "send|re-angle|drop",
    "verdict_reason": "<required unless verdict is send>",
    "lane": "personalised|repair|generic|hold|excluded",
    "lane_reason": "<why this lane — required for anything but `personalised`>"
  }
  ```
  `id` = company name lowercased, non-ASCII stripped, spaces to hyphens (e.g. `acme-corp`). Tier-A accounts get `priority: high` by default. `heat` is 0–3 per the heat axis; `intent_feeds` lists which feed(s) fired (empty array if none); `new_in_role` is true when a 🆕 champion/economic buyer was found. If the merge is ever wrong, recover with `python -m gtm_core.prospects_state restore --profile <active>` (newest snapshot) — snapshots live in `content/<active>/prospects/.snapshots/`.

  **`lane` must reach `ready-to-load.csv`, or the routing decision is invisible where it is used.**
  `gtm_core.lanes route` writes its assignment to `evals/lanes-state.jsonl`, and the enrollment gate
  reads a `lane` **column on the CSV** (`--lane` refuses a list whose own column disagrees). A lane
  that lives only in the jsonl is a decision nobody downstream can see: on 2026-09-04 the pool had
  592 rows routed — 363 of them `generic` — while `ready-to-load.csv` carried no `lane` column at
  all. So after the consolidate sweep, route and re-stamp:
  ```bash
  python -m gtm_core.lanes route --profile <active> \
    --csv content/<active>/prospects/sequences/ready-to-load.csv \
    --records content/<active>/prospects/evals/*.jsonl
  ```
  Then report the split (Step 12). Never hand-write a `lane` value into a pooled CSV — those files
  are rebuilt, so the edit is discarded on the next sweep, exactly as with a corrected `why_now`
  (Step 10's `signal_backfill --promote` note). The account is the durable home; the router is the
  only thing that stamps the row.

  **Bulk mode's items carry a richer, optional superset** — because `prospects_import finalize` derives the HubSpot CSV from the *same* item objects (see `references/hubspot-csv-map.md` for what each maps to): `contact_email` (blank/omit if unverified — never guess), `contact_phone`, `contact_linkedin_url`, `domain`, `city`, `employees_range` (e.g. `"5001-10000"`, midpointed automatically) or a precomputed `employees_number`, `persona_tier` (e.g. `Champion`), `case_study`, `gtm_source` (`Cold` / `Warm-Event: <name>`, defaults to `Cold`), `top_intent_score` (raw 0–100, behind the bucketed `heat`), `intent_topics` (`[{"topic": "agentic ai", "score": 86}, ...]` — `candidates-<run-id>.json` already carries this from ingest; which signal(s) actually fired, not just the heat bucket), `industry`, `revenue_range`. Standard mode's `latest.json` items don't need these — extra keys merge through harmlessly (the writer treats items as plain dicts) — but bulk mode should populate what it has, since `candidates-<run-id>.json` already carries `domain`/`city`/`employees_range`/`industry`/`revenue_range`/`top_intent_score`/`intent_topics` from ingest.
- **`content/<active>/prospects/runs/<run-id>.json`** — per-run manifest:
  ```json
  {"run_id":"<run-id>","profile":"<active>","kind":"prospects","started_at":"<ISO>","completed_at":"<ISO>","discovery_path":"vibe|apollo|web","total_accounts":<N>,"tier_a_count":<N>}
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
  **Apollo — log a cost row here ONLY if you called the hosted OAuth connector this run.** The
  in-repo worker (`agent/mcp/apollo`) self-meters — it writes its own `costs.jsonl` row per
  metered call, same as RocketReach — so logging it again here would double-count. Check which
  surface answered (its tool-call result, or the run header's connectivity note) before deciding:
  ```bash
  # ONLY for the hosted "Apollo" OAuth connector path — the in-repo worker already logged this.
  python -m gtm_core.ledger_cli append-cost --profile <active> \
    --json '{"tool":"apollo","skill":"prospect","cost_usd":0,"units":{"credits":<n>},"run_id":"<run-id>"}'
  ```

**Step 11 — Dossier + outreach-draft sweep.** After the Step 10 consolidate sweep, check whether any
account is missing a dossier — this is the only variable-cost step here (each candidate is a real
agent turn), so it always asks before it runs, never on autopilot. This step used to check **only**
Tier-A; on 2026-08-12 a 496-contact bulk sequence staged and reached 439 accounts (mostly Tier-B/bulk
sourced) with **zero** research behind their Why Now clause — Tier-A coverage alone does not protect a
bulk load, so this now checks every tier by default:
```bash
python -m gtm_core.prospects_consolidate accounts-needing-dossier --profile <active>
```
Pass `--tier A` to restrict to the old Tier-A-only scope (rarely needed — the wider check is cheap to
run and idempotent, see below). This returns accounts (across the **whole cumulative** `master-list.csv`,
not just this run's new accounts) that don't yet have a dossier of any kind — checked both by the
canonical account-slug folder and, for accounts dossiered before that convention existed, a fuzzy match
against every existing legacy folder name, so an already-covered account is never silently
re-generated under a second, duplicate folder.

- If the candidate list is empty, say so in one line and move on — nothing to do.
- Otherwise, **ask the operator once**, naming the count and a few example companies, before
  generating anything (same pattern as the email-sequence skill's hold-queue auto-drain approval —
  a batch operation with real per-account cost gets exactly one confirmation, not per-account nagging
  and not silent execution). At bulk scale, name the cost tradeoff too: a **Tier-A** candidate gets the
  full prospecting-brief pass below; every other candidate defaults to the cheaper **research-pack**
  variant (`account-dossier` skill §"Research pack variant") unless the operator asks for more depth —
  say so explicitly so the operator isn't surprised by the lighter output on a Tier-B account.
- **On confirmation, for each candidate:**
  1. Invoke `account-dossier` — **prospecting-brief mode** for a Tier-A candidate (its lightest *docx*
     variant: no deep web research, built from the candidate's `why_now`/`cohort`/`top_intent_score`
     plus one light company-overview lookup; output
     `content/<active>/accounts/<canonical-slug>/prospecting-brief-<canonical-slug>-<YYYY-MM-DD>.docx`),
     **research-pack mode** for everything else (a markdown-only pass at the same research depth, no
     docx/pdf/images — the mode this sweep needs to stay affordable across hundreds of accounts;
     output `content/<active>/accounts/<canonical-slug>/dossier-<canonical-slug>-<YYYY-MM-DD>.md`).
     Both use the CLI-computed slug the sweep already returned as `canonical_slug` — never
     hand-kebab-case it.
  2. Invoke `draft-outreach` for that account's resolved contact, saved with the same naming
     convention Step 10 already uses (`prospects-YYYYMMDD-outreach-[company-slug].md`, same canonical
     slug) — this is what makes the draft automatically show up in the outreach-log rollup below with
     zero new plumbing.
- **Re-run the outreach log** after the loop so the rollup reflects every draft just generated:
  ```bash
  python -m gtm_core.outreach_log build --profile <active>
  ```
- Idempotency is free: an account drops out of the `accounts-needing-dossier` list the moment it has a
  dossier of any variant (prospecting-brief, research-pack, or a full dossier), so re-running this step
  later (e.g. next scheduled run) only ever processes genuinely new accounts — no separate tracking
  needed.
- This step closes the **research** gap. It does not, on its own, prove a batch is safe to load into a
  sequence — a wrong email domain, a stale research-note artifact in `company`, or an unflagged
  competitor can all still be sitting in `ready-to-load.csv` even when every account has a dossier. The
  `email-sequence` skill's account-integrity gate is what actually blocks on those before copy is
  composed; running this step first just means fewer of its findings are `no-dossier`.

**Step 12 — QA & close.** Run the §8 checklist in `output-templates.md`. Report a short summary: counts, Tier-A count, **the lane split**, discovery path, spend vs cap, and the file names produced. Offer to draft/refine outreach (`draft-outreach`) or to schedule the weekly run.

**Never report a Tier-A count without the lane split beside it.** Tier-A is the *pack* tier — the
rows earning a 1:1 outreach pack — and it was repeatedly misread as the send gate, which made a
healthy run look like a failing one. On 2026-09-04 a run reported "2 Tier-A" for a 20-account set
whose pool at that moment held 1,187 accounts above the publish threshold and 363 rows already
routed `generic`; the natural reading of "2" was that the ICP gate had rejected everything, when the
gate rejects about 6% of what it scores. Report both numbers, always, in this shape:

> `N accounts · T Tier-A (packs) · lanes: personalised P / repair R / generic G / hold H / excluded X`

and state what each non-`personalised` lane needs to move — research for `hold`, a re-draft for
`repair`, an operator decision for `generic`. A row in a lane is work queued, not work rejected;
say so, or the next reader re-derives "we have no prospects" from a number that never meant that.

**Step 13 — Always last: render the status page AT THIS RUN'S SCOPE, then prove it is fresh.** **Every run of this skill — any mode, including re-score/refresh-heat and enrichment-only passes that skip the sweep — ends with this. Never skip it.** It is cheap and read-only. Two commands, not one:
```bash
# 1. render at the scope this run actually touched
uv run python -m gtm_core.email_campaign_dashboard --profile <active> --scope open
# 2. assert it is built from what is on disk NOW — exits non-zero if not
uv run python -m gtm_core.email_campaign_dashboard --profile <active> --scope open --check-fresh
```

**Pick the scope; do not default to the rollup.** `--scope open` renders the campaigns whose manifest says `status = "active"` — the right answer for a normal run. Use `--scope campaign --campaign <slug>[,<slug>]` when the run worked one named campaign, and `--scope all` only when the question really is profile-wide. A page scoped wrong is the failure this flag exists for: on 2026-09-04 a campaign with 8 planned emails and 4 people showed "0 of 990 emails · 51 people · 3 sequences", every number real and every number belonging to a different campaign.

**Run the freshness check and report what it says.** It compares the page against a digest of every input it read, so it catches an input edited after the render *and* one that appeared since. This matters because **a stale page renders identically to a current one** — on 2026-09-05 a live page was seventeen hours behind `cells.toml` and looked perfectly current, and the first regeneration of that session was a byte-identical no-op that only a `grep` caught. If `--check-fresh` exits non-zero, re-render and say so; never report the page as current because the render command succeeded.

**Do not read a refused tile as a zero.** Where a figure cannot be aggregated honestly across the scope the page renders `—` and a reason (a sending ceiling is shared infrastructure and is never summed; a forecast is refused outright when the campaigns run different cadences; a roster covering only some of them is not shown at all). That is the page declining to guess — quote the reason, do not substitute a number from one campaign.

Then **surface the page to the operator** — one line, e.g. *"📊 Status: `content/<active>/campaign-open.html` — 30 ready · 417 verifying · 1,231 accounts to enrich"* — so they always land on the one page that shows the whole picture (account backlog → email funnel → ready/verifying/blocked, cost, next steps) instead of hunting through CSVs. The full `consolidate` sweep in Step 1 / Step 10 regenerates the profile-wide page only; Step 13 is what produces a page at the *run's* scope and proves it fresh. Ask for the path rather than spelling it: `uv run python -m gtm_core.prospects paths --profile <active>`.

**There is one renderer, and it is `email_campaign_dashboard`.** `gtm_core.prospects_dashboard` is the status *model* (`build_status`) and nothing else — its second renderer and the `status-standalone.html` it wrote were retired 2026-09-05, so the module now exports no writer at all and the command that used to call it no longer exists.

**Do not manually count ready rows or sum counts across files.** Always read the metrics directly from `content/<active>/email_campaign_status.html` (or `.pool/status.json`, which the same command writes). If judge consensus is needed, use `python -m gtm_core.adjudication tally --records <records.jsonl...> --out <consensus.jsonl>`.

**Never sum adjudication record files, and never quote a single send count.** Re-judging runs
cover the *same* recipients, so adding their tallies double-counts; and the judge is not stable
across runs, so one pass's number is a draw, not a measurement. `tally` reports a **range**
(`consensus < majority < single-pass < any-pass`) plus a contested count — quote the range. It
groups by `(email, touch)` and **refuses** `row_id`, which is an eval-sheet row identity
(`sha256(spec|csv|email|touch)`), not a recipient identity. A contested row is emitted
`unscored`, so `write-verdicts` skips it and a draw can never reach the sequencer as a verdict.

**A tool that groups records must verify its key and fail loudly when the key is unstable.** On
2026-08-30 a tally keyed on `row_id` turned 1298 records over 599 recipients into 1298 singleton
groups and reported "flip rate 0.0%" for a pool where 36% of recipients had changed verdict.
A grouping key is a claim about identity — prove it before you group on it.

## Guardrails

- **Product-accuracy discipline** — tag any capability claim in a Tier-A outreach pack SHIPPED/CONDITIONAL/ROADMAP (never a conditional/roadmap capability as live) and verify cited external facts: `docs/product-accuracy.md`.
- **Budget is a hard stop**, not a warning: read `monthly_tool_budget_usd` + `per_run_cap_usd` from PROFILE — its budget comments define what the cap covers (RocketReach and Apollo are both flat monthly plan allocations, not per-call dollars; Vibe sells one-time credit packs with 365-day validity; Claude Max is a flat brain seat outside the cap). Estimate before metered calls; trim or fall back rather than breach the cap; never auto-buy credits, exports, or a bigger Apollo plan.
- **Spend the scarce unit sparingly:** RocketReach lookups are unlimited but **person exports are finite**; Apollo credits are a **finite monthly allocation** too — spend either only for a scored finalist, never a candidate. Apollo company search costs 1 credit **per call, even on zero results** (unlike person enrichment, which is free on a miss) — don't loop it speculatively.
- **Never reveal an Apollo phone number.** This integration never requests one — Apollo's phone reveal needs a caller-hosted webhook this headless deployment can't receive, and costs 8 extra credits per match. A finalist's phone stays RocketReach-only.
- **Never echo an API key:** RocketReach auth is `ROCKETREACH_API_KEY`, Apollo's in-repo-worker auth is `APOLLO_API_KEY` (both Doppler-injected env); neither key value ever appears in a file, ledger, output, or chat.
- **Provenance travels as data, not as prose.** Every account carries its dated 🔥 signal + URL into the brief and the outreach pack **and** into the six record columns (Step 7). A URL that appears only in a markdown brief is not provenance the pipeline can check — the load gate reads the row, not the write-up.
- **"Not sendable" is a real answer.** Every finalist carries `verdict` + `verdict_reason` (Step 8). A research step with no way to refuse produces exactly as many emails as it was asked for, whatever the accounts turned out to be.
- **`verdict` is yours, and only yours.** It is the researcher's column, written once here and never machine-overwritten. The judge writes `judge_verdict` / `judge_verdict_reason` / `judge_calibrated` in its own columns, and enrollment keeps a row only when `verdict == send` **and** it is not a *calibrated* judge `drop`. An uncalibrated judge — one with no sealed holdout behind it — ranks rows and never removes them. Do not write the `judge_*` columns, and do not read them as if they were your finding.
- **Identity is stamped, not re-derived.** `latest.json` is the ledger of record and assigns each account an immutable `account_id`; the consolidate sweep stamps `pool_row_id` per person-row and joins the two. Quote those ids when referring to a row or an account across files, rather than re-deriving a key from a company name that six other places normalise differently. The pooled CSVs are **derived views** — never hand-edit one and expect the edit to survive a rebuild; change the ledger, or the suppression ledger, instead.
- **The last wave has to have been read before the next one is staged.** `email-sequence` refuses to stage without a `positive_reply_rate` reading on file (`gtm_core.prospects wave-gate check`). This skill does not send, but it is what fills the next wave — a list built while the previous one is unmeasured is a list nobody can learn from.
- **Drafts only:** outreach is never sent from this skill.
- **Portable & private:** no live CRM; outputs are local files; no secret is read from or written to any file.
- **Market-aware:** everything keys off PROFILE `target_markets` — never hardcode geographies.
