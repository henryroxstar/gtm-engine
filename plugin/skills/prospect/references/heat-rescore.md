# Prospect — Re-score mode (heat refresh over existing accounts)

> A **mode of the `prospect` skill**, not a separate skill. It refreshes `heat` / `intent_feeds` on
> the accounts already in `content/<active>/prospects/latest.json` against **today's** intent and
> re-ranks Tier-A — no new discovery, gating, enrichment, or outreach. Reuses this skill's
> account-level intent fetch and the heat axis in `gates-and-scoring.md`. Near-zero cost.

## When to run it
- After a tracked-topic change (Vibe, RocketReach/Intentsify, or Apollo/LeadSift).
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
- **A discovery cross-match sees 5 preview rows; a per-account probe does not have that problem.**
  The agent's own tool response is capped at a handful of preview rows per table regardless of
  `number_of_results` or `database_total`, and `show-sample` does not buy more (see
  `discovery-and-budget.md` §"Provider hard caps"). That caps the *in-market cross-match* path — but step 3's
  per-account probe filters to the accounts you asked about, so a batch of ≤5 surging hits is
  fully visible for free, and larger batches export at ~2 credits/row. Say which path produced
  the reading: "N accounts probed individually" and "a 5-row spot-check of the in-market pass"
  are different claims, and only one of them is per-account coverage.
- **Intent coverage is not uniform across segments, so one surge rate is not a finding.** See
  step 3's "read a zero by segment" note and step 8's reporting rule.

## Procedure

1. **Load the set.** Read `content/<active>/prospects/latest.json`. Select accounts whose `status`
   is **not** `contacted | qualified | disqualified` (the "open" set). Report the open vs frozen count.

2. **Budget pre-check.** Intent checks are **credit-free** on Vibe (scores in the free masked
   preview) and RocketReach (signal search is free) — no export is needed for either. **Apollo is
   the exception: its company-search intent filter costs 1 credit per page/call, same as a live
   run** (see `discovery-and-budget.md` §Apollo) — re-scoring via Apollo is not free. Skip Apollo in
   a re-score unless the operator specifically wants its corroborating check; Vibe + RocketReach
   alone keep this mode ~$0.

3. **Account-level intent — there IS a per-account probe (re-verified 2026-09-22).** Two filters
   that this file said "conflict" in fact **combine**, and that combination is the only real
   per-account intent read available. Three claims that lived here until 2026-09-22 were wrong,
   and each carried a `verified 2026-07-18` marker:

   | Claim that was here | What a live probe returns |
   |---|---|
   | "Vibe has **no** per-company intent lookup" | It has one — the three calls below |
   | "`business_id` silently routes to *prospects* (people)" | With `entity_type: businesses` it returns **businesses** |
   | "`business_id` **cannot** be combined with `business_intent_topics` (they conflict)" | They combine, and only the surging subset comes back |

   **A dated verification marker is not a warranty.** All three were re-tested in one session
   because the calls are free; the re-test cost nothing and recovered a capability the file had
   told two sessions not to attempt. **Re-probe a documented blocker whenever the probe is free**
   — provider surfaces drift, and a blocker is the most expensive kind of claim to leave wrong.

   **The working three-call sequence:**

   ```bash
   # 1. autocomplete  -> the EXACT topic strings (see the trap below)
   # 2. match-business -> domains/names to business_id   (free; capped per call —
   #                      see discovery-and-budget.md §"Provider hard caps")
   # 3. fetch-entities -> entity_type: businesses, filtered on BOTH business_id AND
   #                      business_intent_topics; returns each company's surging topics WITH scores
   ```

   Measured 2026-09-22: three `business_id`s probed against two identity topics returned **two**
   rows, each carrying `business_business_intent_topics` as `[{topic, score}]`; the third company
   was simply not surging. **Only surging companies come back**, so `records_matching_filters` is
   the hit count for the batch — and it is *upstream headroom*, never a delivered-row count, so
   never do arithmetic on it. A result of ≤5 is fully readable in the free preview; beyond that,
   export at ~2 credits/row and download with `gtm_core.dataset_fetch`.

   **The trap that makes a broken probe look like a cold account.** Topic strings are
   **category-prefixed** — `security: non-human identity management`, not
   `non-human identity management`. A bare string is not rejected; it silently matches nothing,
   and the call returns `records_matching_filters: 0`, which reads exactly like "this account has
   no intent". On 2026-09-22 that produced a zero result *and a zero positive control* before the
   strings were corrected. **Always take topic values from `autocomplete`, and always run one
   unfiltered positive control** so a zero can be told apart from a typo.

   **`enrich-business` does not return intent at all** — it has no intent enrichment type. It was
   listed here as a paid per-company intent fallback; it is not one, at any price.

   **Read a zero by segment, never as "cold".** Bombora surge is size-dependent — measured
   2026-09-22 on this tenant, 35–46% of large enterprises surged against **2.6%** of SME/agent
   factories (n=374). A small builder reading heat 0 is a coverage limit of the feed, not a fact
   about the account, and some accounts have **no Explorium business record at all**, which is a
   third state distinct from "probed and cold". Report surge rate **by segment** and refuse to
   call a small-company cohort cold on a feed that cannot see it.

   - **RocketReach (corroborating):** Tier 1 — cross-check the domain against the tracked-topic
     `intent` facet (`company_search`, credit-free) → boolean `rr-intent`. Tier 2 — if
     `content/<active>/prospects/intent/rr-intentsify-latest.json` exists and `week_of` is <10 days
     old, match the domain against it → scored `rr-intent`. Stale/absent → skip Tier 2 (freshness
     guard; see `discovery-and-budget.md`).
   - **Apollo (corroborating, NOT free — only if the operator asks for it):** cross-check the
     account's domain via Apollo company search (`apollo_company_search` in-repo / `apollo_mixed_companies_search` hosted) with the tracked buying-intent topics
     (must be configured in Apollo's web UI first — an unconfigured account means "absent," not
     "cold"). 1 credit per call, unlike the two feeds above.

4. **Recompute heat** per `gates-and-scoring.md` §Heat axis: **+2** for a score ≥75 on any feed
   (an RR or Apollo filter hit qualifies), **+1 more** when two or more feeds fire (double-intent),
   60–74 elevated (note, no points), cap at the rubric ceiling. Update `heat` (0–3) and
   `intent_feeds` only.

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

8. **Report — by segment, and never call a cohort cold on a feed that cannot see it.**
   Open vs frozen count, accounts that gained/lost heat, Tier-A delta (promoted/demoted), which
   feeds fired, spend, and the limitations above — including how many accounts were probed
   individually versus reached only by a 5-row spot-check, so "no change found" reads as "not
   found in what we could see," not "confirmed absent everywhere."

   **Break the surge rate out per segment.** One blended number hides the only thing that
   matters here: measured 2026-09-22 on this tenant, large enterprises surged at **35–46%** and
   SME/agent-factory accounts at **2.6%** (n=374). A single figure averaged over both describes
   neither, and reading the low half as "cold" buries an entire segment behind a feed that cannot
   see it.

   **`intent_coverage_floor` (default 0.10, in `content/<active>/settings.json`) is a REPORTING
   guard, not a spend guard.** When a segment's surge rate falls below it, the report must say
   *"coverage too thin to characterise"* and name the floor — not "this cohort is cold". Below
   the floor the reading is about the feed, not the accounts, and for a small-company or
   owner-operator cohort the timing signal has to come from somewhere else entirely (hiring
   triggers, funding, a named client win, founder activity). Record **three** states, not two: surging, probed-and-not-
   surging, and **no Explorium business record at all** — the third is not a cold reading, it is
   an account this feed cannot answer for, and collapsing it into "cold" is how a whole segment
   silently scores zero.
