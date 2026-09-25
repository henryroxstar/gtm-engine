---
name: team-pipeline
description: >-
  Produce a sales leadership view of team pipeline aggregated by rep, stage, and risk profile,
  highlighting coaching moments and coverage ratios. Trigger when the user says "review team
  pipeline", "leader view of pipeline", "rep pipeline coverage", or "1:1 pipeline review".
metadata:
  version: "0.1.0"
  phase: "3D"
  capability_tier: core
---
# Team Pipeline Review

Produce a sales leadership overview of pipeline health aggregated across account executives, deal stages, and risk profiles to support effective 1:1 coaching and forecast calls.

---

## 1. Safety & Architecture Invariants

- **Read-Only Intelligence:** Analyzes team performance and pipeline coverage without mutating CRM data.
- **Deliverables Destination:** Save generated leadership views to:
  `content/<active>/governance/team-pipeline-<YYYY-MM-DD>.md`.

---

## 2. Execution Procedure

### Step 0: Connector preflight — fail closed

```
uv run python -m gtm_core.connector_categories crm
```

`~~CRM` is a connector **category**, not a tool this repo ships: it is resolved against
whatever the operator has connected. Nothing in this deployment binds it today, and a skill that
addresses an unbound category does not stop — it produces a confident report over nothing, which
reads exactly like one built from real data. So this runs first, and **exit 1 is a refusal, not a
warning**.

**On a refusal: say so, name the category, and stop.** Every number below — coverage ratio,
quota attainment, per-rep close rate — is an aggregate over the CRM's own records. There is
no local store that holds rep quotas, so there is nothing to compute from, and a coverage
ratio is exactly the kind of figure a leader acts on without asking where it came from.

---

### Step 1: Ingest Team Pipeline Metrics
Query the connected `~~CRM` across all sales reps for active quarter deals:
- Weighted vs Unweighted Pipeline by stage
- Rep Quota vs Current Commit vs Best Case
- Historical close rates per rep

### Step 2: Pipeline Coverage Ratio Calculation
For each sales representative, calculate:
$$\text{Coverage Ratio} = \frac{\text{Open Pipeline ARR}}{\text{Remaining Quota ARR}}$$
- **Healthy:** $\ge 3.5\times$
- **Marginal:** $2.0\times - 3.4\times$
- **Critical Risk:** $< 2.0\times$

### Step 3: Deal Risk & Coaching Diagnosis
Inspect top 3 deals per rep and categorize risk factors:
1. Single-threaded relationships (need multi-threading coaching)
2. Lack of agreed Mutual Action Plan (MAP)
3. Unverified Paper Process (procurement/security hurdles)

### Step 4: Save Leadership Deliverable
Save the aggregated briefing to:
`content/<active>/governance/team-pipeline-<YYYY-MM-DD>.md`
Include:
- Team summary table (Rep, Quota, Commit, Total Pipeline, Coverage)
- 1:1 coaching agenda topics for reps under critical risk
- Executive deal interventions for top 5 cross-team deals

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
