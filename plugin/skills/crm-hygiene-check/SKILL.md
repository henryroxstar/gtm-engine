---
name: crm-hygiene-check
description: >-
  Perform a read-only audit of CRM opportunities to flag missing MEDDPICC fields, stale close
  dates, inactive stages, and output a clean rep nudge list. Trigger when the user says "check
  CRM hygiene", "audit pipeline cleanliness", "find stale deals in CRM", or "CRM hygiene
  check".
metadata:
  version: "0.1.0"
  phase: "3D"
  capability_tier: core
---
# CRM Hygiene Check

Perform a strict read-only audit of CRM opportunities to detect pipeline anomalies, stale close dates, missing MEDDPICC fields, and produce an actionable rep nudge list.

---

## 1. Safety & Architecture Invariants

- **Strictly Read-Only:** Sweeps CRM opportunities and logs anomalies. Never alters field values, updates close dates, or advances/reverts stages without explicit human confirmation.
- **Egress & Least Privilege (§R6):** All CRM data queries run via authorized `~~CRM` MCP tools.
- **Deliverables Destination:** Save generated hygiene reports to:
  `content/<active>/governance/crm-hygiene-<YYYY-MM-DD>.md`.

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

**On a refusal: say so, name the category, and stop.** There is no substitute for the CRM
here — every anomaly rule below is a claim about *the deal record as the CRM holds it*, and
a hygiene report built from anything else is a fabrication with a filename. Do not read a
`content/` prospect store instead: it is a prospecting artefact, not a pipeline, and it
carries no stage, owner, close date or qualification field for these rules to check.

---

### Step 1: Sweep Active Opportunities
Extract all open deals currently in active pipeline stages from the connected `~~CRM`:
- Deal Name & Account
- Amount & Stage
- Close Date & Last Activity Date
- Opportunity Owner / AE
- Core Qualification Fields (Economic Buyer, Champion, Decision Criteria, Paper Process)

### Step 2: Anomaly Detection Rules
Flag opportunities violating hygiene standards:
1. **Past-Due Close Dates:** Close date is in the past while deal remains in open stage.
2. **Activity Drought:** No logged activity, email, or meeting in >21 days on an active Stage 2+ deal.
3. **Ghost Champion:** Deal marked as "Commit" with no verified Champion or Economic Buyer identified.
4. **Stage Velocity Anomaly:** Deal parked in the same stage for >2x average cycle length.
5. **Round-Number Blindness:** Unrealistic round amounts with zero quote or solution brief attached.

### Step 3: Produce Rep Nudge List
Group anomalies by Opportunity Owner (Account Executive) into clear, polite, actionable punch-lists:
- Specific field to populate
- Specific date to refresh
- Concrete qualification step required

### Step 4: Save Report
Save the audit report and nudge summary to:
`content/<active>/governance/crm-hygiene-<YYYY-MM-DD>.md`

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
