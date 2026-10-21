---
source: manual
refreshed: 2026-09-27
review: 90d
---
# Messaging & Hook Intake

This document is the human-facing front door for updating messaging, capability claims, proof points, and outbound hooks. Fill in or edit the sections below in plain English. 

When ready, an operator or agent compiles this document into the live knowledge registry:
```bash
uv run python -m gtm_core.messaging_intake stage --profile <active> --file <path-to-this-file>
```

---

## 1. Target Seats & Pains

Define the buyer seats you are targeting and the specific commercial/technical pains that resonate with them.

Replace the two example seats below with your own. A seat is a buying role, not a job title:
give it the titles it shows up as, the one pain it leads on, and the pains that belong to a
DIFFERENT seat (`Forbidden Pains`) so a draft cannot borrow another seat's argument.

### Seat: finance
- **Title / Persona:** CFO, Finance Director, Head of Financial Operations
- **Lead Pain:** The month's figures are assembled by hand, so the close lands late and nobody can say which number is current.
- **Gain:** One set of figures, on a fixed day, that the team can cite without re-checking it.
- **Forbidden Pains:** Deployment cadence, integration effort (these belong to Engineering).
- **Register:** Close timelines, audit trail, headcount cost of manual work.

### Seat: engineering
- **Title / Persona:** CTO, VP Engineering, Head of Platform
- **Lead Pain:** Every new internal tool arrives as another one-off integration the team has to keep alive.
- **Gain:** One documented interface to configure instead of a bespoke connector per system.
- **Forbidden Pains:** Annual audit fatigue, board reporting (these belong to Finance/Exec).
- **Register:** Architecture fit, operational overhead, build-vs-buy.

---

## 2. Capabilities & Claims

Define what the product does. Only claims marked `verified` with a cited source link or path may be drafted into cold emails.

### Claim: example-scheduled-export
- **Group:** reporting
- **Status:** design-target
- **Statement:** A scheduled export delivers the month's figures to the finance team on a fixed day.
- **Source:** 
- **Do Not Say:** real-time, instant, always up to date
- **Boundary:** false
- **Notes:** Example placeholder in template. Mark verified only when backed by shipped code and documentation.

### Claim: example-self-serve-setup
- **Group:** onboarding
- **Status:** design-target
- **Statement:** A new workspace is configured by its own admin without a professional services engagement.
- **Source:** 
- **Do Not Say:** zero-touch, zero-configuration, works out of the box
- **Boundary:** false

### Claim: example-regional-hosting
- **Group:** deployment
- **Status:** design-target
- **Statement:** Customer records stay strictly in the region where the workspace was created.
- **Source:** 
- **Do Not Say:** never leaves, guaranteed, 100% isolated
- **Boundary:** false

---

## 3. Proof Points & Benchmarks

Define external standards, third-party industry stats, or measured case study metrics. Figures can only be cited in emails if marked `measured`.

### Proof: example-anchor-market-guidance
- **Kind:** anchor
- **Market:** singapore
- **Figure Kind:** none
- **Statement:** The market's published guidance expects an accountable owner named for each automated process.
- **Source:** 
- **Binding:** false
- **Notes:** Example placeholder in template. Replace with a guidance document your buyers actually cite.

### Proof: example-problem-stat
- **Kind:** stat
- **Market:** global
- **Figure Kind:** illustrative
- **Statement:** Finance teams commonly spend the first week of a quarter reconciling figures by hand.
- **Source:** 
- **Binding:** false

### Proof: example-customer-outcome
- **Kind:** outcome
- **Market:** global
- **Figure Kind:** illustrative
- **Statement:** A mid-market operator cut month-end reconciliation time from nine days to two.
- **Source:** 
- **Binding:** false

---

## 4. Narrative Angles & Hooks

Connect a target seat to a premise, a claim, and a proof point to form a cohesive outbound angle.

### Angle: finance-fixed-close
- **Seat:** finance
- **Premise:** manual-reconciliation
- **Claim:** example-scheduled-export
- **Proof:** example-anchor-market-guidance
- **Opener Kind:** account-event
- **Summary:** One set of month-end figures, on a fixed day, that nobody has to re-check.
- **Status:** draft
- **Give-First Gift CTA:** 1-page close-process teardown with the manual steps named
- **Notes:** Example placeholder in template. Draft angles promote to live only once their
  claims are verified.
