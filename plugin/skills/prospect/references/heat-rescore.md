# Prospect — Re-score mode (heat refresh over existing accounts)

> A **mode of the `prospect` skill**, not a separate skill. It refreshes `heat` / `intent_feeds` on
> the accounts already in `content/<active>/prospects/latest.json` against **today's** intent and
> re-ranks Tier-A — no new discovery, gating, enrichment, or outreach. Reuses this skill's
> account-level intent fetch and the heat axis in `gates-and-scoring.md`. Near-zero cost.

## When to run it
- After a tracked-topic change (Vibe or RocketReach/Intentsify).
- Periodic heat refresh (intent is time-sensitive — a cold account can go hot).
- To apply the scored heat axis (score ≥75 = +2) to older runs made before it existed.

## What it does NOT do
- **Never re-gates or changes fit.** Fit ("who") is stable; this only updates heat ("when") and the
  resulting Tier ordering. Rubric/gate scores are left untouched.
- **Never overwrites operator state.** Accounts with `status` in `contacted | qualified |
  disqualified` are frozen — skip them (they are done or deliberately dropped).
- **No new contacts, no exports, no outreach drafts.** Priority re-rank only.

## Three honest limitations (state them in the report)
- **Measures now, not then.** Bombora surge and the Intentsify weekly list are *current-period*
  signals — a re-score reflects today's market, not the account's intent on its original run date.
  That is the point (promote newly-hot accounts), but it is not a historical reconstruction.
- **Intentsify has no backfill.** It only accrues data from when the topics were set; for ~1 weekly
  cycle after a topic change `rr-intent` stays empty. Expected, not a bug — Vibe/Bombora carries the
  re-score until Intentsify populates.
- **The agent only ever sees 5 rows per table, paid or not (verified 2026-07-18).** This is the
  limitation to plan around — see the "Coverage vs cost" callout in step 3 for the mechanics. In
  practice: a re-score is a **spot-check against whatever 5 rows the in-market pass happens to
  surface per market/segment pass, not a guaranteed per-account probe.** Report it that way — "N
  accounts reconfirmed via spot-check, full coverage not mechanically available at this population
  size" — rather than implying every open account was individually verified.

## Procedure

1. **Load the set.** Read `content/<active>/prospects/latest.json`. Select accounts whose `status`
   is **not** `contacted | qualified | disqualified` (the "open" set). Report the open vs frozen count.

2. **Budget pre-check.** Intent checks are **credit-free** on both sources (Vibe returns scores in
   the free masked preview; RocketReach signal search is free). No export is needed — do not export
   just to re-score. Confirm ~$0 and proceed.

3. **Account-level intent — the Vibe tool reality (verified 2026-07-18):** Vibe has **no per-company
   intent lookup**. `business_id` as a filter routes to *prospects (people)*, not company intent, and
   it **cannot** be combined with `business_intent_topics` (they conflict). The only call that returns
   `business_business_intent_topics` (the scored `{topic,score}` array) is the **discovery** path:
   `fetch-entities(business_intent_topics={topics:[…]}, company_country_code, company_size)`. So a
   re-score is a **discovery cross-match**, not a per-account probe:
   - **Vibe/Bombora (primary):** run the in-market discovery pass for the profile's markets + segment
     size bands (topics from `market-scan-config.md` → §Intent topics). It returns companies surging
     on those topics, each with inline scores. **Cross-match by name/domain against the open account
     set;** a match with score ≥75 → `vibe-topic` (+2). The free preview already unmasks
     `business_name` + `business_business_intent_topics` (only employee/revenue ranges stay masked)
     — no need to call `show-sample` just to reveal those two fields.
     **Coverage vs cost (verified 2026-07-18 — read carefully, this cost real credits to learn):**
     the agent's own tool response is **hard-capped at 5 preview rows per table, regardless of
     `number_of_results` or `database_total`, and regardless of whether you pay.** `show-sample`
     does **not** buy the agent more rows — it charges roughly 2 cr/row for the table's *full* row
     count (a 66-row table cost 132 credits) but still only returns 5 rows in the response; the rest
     is unmasked into an interactive **widget for a human**, which the agent cannot read. So:
     - When `database_total` ≤ your `number_of_results` (a small/niche population, e.g. a single
       secondary market), you technically pulled the *entire* surging set in one free call — you
       just can't see past row 5 of it yourself. Treat this as "the population is small enough to
       plausibly contain your open accounts" context, not as verified per-account coverage.
     - When `database_total` is in the thousands (typical for a large primary market), a real
       full-coverage cross-match means exporting the *entire* surging population
       (~1–2 cr/row × thousands of rows = tens of thousands of credits) — not "near-zero cost" and
       not what this mode is for. Don't do it without the operator explicitly asking for that scale
       of spend.
     - **Do not try the `business_id` filter as a workaround** to fetch a specific company's intent
       record directly — confirmed (again, 2026-07-18) that it silently routes to *prospects*
       (people) even when `entity_type: "businesses"` is set, exactly as the paragraph above already
       warned. `match-business` (free) resolves a name/domain to a `business_id`, but that ID still
       can't be turned into a company-level intent lookup.
     - **Practical default:** run the in-market pass per market/segment band, read the 5 free preview
       rows, cross-match by name against the open set, and report exactly what that spot-check found
       — do not claim broader coverage than 5 rows/pass actually gives you. `enrich-business` per
       company also returns intent but is the paid escape hatch (≤3/run) — not for bulk, and still
       not a way past the 5-row cap for any one *discovery* table.
   - **RocketReach (corroborating):** Tier 1 — cross-check the domain against the tracked-topic
     `intent` facet (`company_search`, credit-free) → boolean `rr-intent`. Tier 2 — if
     `content/<active>/prospects/intent/rr-intentsify-latest.json` exists and `week_of` is <10 days
     old, match the domain against it → scored `rr-intent`. Stale/absent → skip Tier 2 (freshness
     guard; see `discovery-and-budget.md`).

4. **Recompute heat** per `gates-and-scoring.md` §Heat axis: **+2** for a score ≥75 on either feed
   (an RR filter hit qualifies), **+1 more** when both feeds fire (double-intent), 60–74 elevated
   (note, no points), cap at the rubric ceiling. Update `heat` (0–3) and `intent_feeds` only.

5. **Re-rank Tier-A** by heat → 🆕 new-in-role → 🔥 signal recency. A newly-hot Tier-B account is
   promoted to Tier-A; an account whose intent went cold drops to Tier-B (it does **not** get
   disqualified — fit is unchanged). Update `tier` and `priority` accordingly.

6. **Rewrite `latest.json`** in place — change only `heat`, `intent_feeds`, `tier`, `priority`, and
   `generated_at`. Preserve `status`, contacts, `why_now`, `new_in_role`, and every other field.

7. **Ledger + close.** Append a history event (cost 0):
   ```bash
   python -m gtm_core.ledger_cli append-history --profile <active> \
     --json '{"event":"prospect_rescore","skill":"prospect","open_accounts":<N>,"promoted":<N>,"demoted":<N>,"snapshot":"content/<active>/prospects/latest.json"}'
   ```
   If Tier-A membership changed, offer to regenerate the outreach log
   (`python -m gtm_core.outreach_log build --profile <active>`) — do not auto-draft outreach.

8. **Report:** open vs frozen count, accounts that gained/lost heat, Tier-A delta (promoted/demoted),
   which feeds fired, spend, and the three limitations above — including how many rows were actually
   spot-checked vs the true size of the open set, so "no change found" reads as "not found in what we
   could see," not "confirmed absent everywhere."
