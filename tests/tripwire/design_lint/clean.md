# Solution design — Northwind Health Networks

*Prepared 2026-09-22. Fictional: this document exists to prove the linter stays silent on a
correct design. Every name, figure and system in it is invented.*

## Executive summary

Northwind books clinic sessions across four regions and reconciles them by hand each night.
We propose a booking gateway that holds the reconciliation rules in one place, so a session
that cannot be reconciled is refused at booking time rather than discovered the next morning.

## Tier 1 — customer overview

### What we heard

Three problems came up in every conversation:

- night reconciliation runs past the morning clinic list
- a refused session is discovered a day late
- nobody can say which region a discrepancy came from

### Constraints

Fixed and not ours to choose: the regional schedulers stay in place, the clinical record
system is read-only to us, and the change window is a single weekend.

### Context and current state

Today each region writes sessions straight into its own scheduler, and a nightly job copies
them into the reporting store. The clinical record system sits outside our boundary and is
not modified.

### The solution we propose

One gateway in front of the four schedulers. It applies the reconciliation rules at write
time and returns a refusal with a reason, so the rule that used to run at midnight runs at
the moment of booking.

### How it works

```mermaid
flowchart LR
  A[Scheduler] --> B[Gateway]
  B --> C[Reporting store]
```

How to read this: a booking leaves a regional scheduler, passes the gateway where the
reconciliation rules live, and only then reaches the reporting store. Nothing writes to the
store directly any more, which is what makes the nightly job unnecessary.

### What we will and will not claim

| Capability | Status | Note |
|---|---|---|
| Reconciliation at write time | Enforced | the rule store is consulted on every booking |
| Regional discrepancy report | Simulated | shown from fixture data in the walkthrough |
| Cross-region reconciliation | Design-target | one region at a time today |

Cross-region reconciliation is the one thing on that list we would build rather than ship.
It would reconcile two regions against each other once the single-region path is bedded in.

### Deployment topology

The gateway runs in Northwind's own tenancy, in the same region as the scheduler it fronts.
No session data crosses a regional boundary.

### Risks and open questions

- whether the weekend change window is long enough for all four regions
- who owns a refusal that the clinical record system later contradicts

## Tier 2 — technical appendix

### A3. Component inventory

| Component | What it does | Who runs it |
|---|---|---|
| Booking gateway | applies the rules at write time | us |
| Rule store | holds the reconciliation rules | us |
| Reporting adapter | writes accepted sessions onward | Northwind |

### A4. Identity, policy and data flow

Every call carries the scheduler's own service identity. The customer presents a session
reference with every call, and the gateway never holds a clinical record.

We store the verdict against that reference, because the schedulers have nowhere to put it.

### A7. Decisions and trade-offs

We chose a synchronous refusal over a nightly report. It costs latency at booking and buys
the day back on every discrepancy — the trade we heard asked for.

### A9. Quality requirements

| Attribute | Target |
|---|---|
| Availability | 99.9% monthly |
| Latency | 200 ms at the 95th percentile |
| Throughput | 40 bookings per second sustained |
| Recovery | RPO 5 minutes, RTO 1 hour |

### A10. Glossary

- **Session** — one booked appointment slot at one clinic.
- **Reconciliation** — the check that a session is consistent with the regional schedule.
- **Refusal** — a booking the gateway declines, with the rule that declined it.
