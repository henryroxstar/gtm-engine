# Commercial models — the generic method

**Generic method only.** Which models a tenant actually offers, at what price, on what paper, lives
in the active profile's `knowledge/commercial/counterparty-models.md` and `pricing.toml`. This file
says how to recognise the shape and what each shape must answer. When the two disagree about a
*fact*, the profile wins; when they disagree about *method*, fix whichever is wrong.

## Recognise the shape first

Ask one question: **who signs with the end customer, and who uses the software?**

| If… | Shape |
|---|---|
| the counterparty uses it for its own operations | **direct client** |
| the counterparty's product contains it, and the counterparty's customers get the capability through that product | **embed** |
| the counterparty sells it — alone or bundled with services — under its own contract with the end customer | **reseller / channel** |
| the counterparty only introduces, and we contract with the customer | **referral** — no proposal to the referrer; a referral agreement instead |

Mixed signals are common and are the most expensive mistake in this skill. An embed counterparty
that "also wants to sell it to a few hospitals directly" is a reseller for those deals, and an embed
agreement that forbids resale will not carry them. **Name the shape per deal line, not per company.**

## What each shape must answer

### Direct client
- Unit of charge (appliance, instance, seat) and what one unit covers.
- Term and minimum commitment; what increases spend; renewal pricing.
- Self-serve vs assisted onboarding — state which, never imply the other.

### Embed
- **Unit definition in end-customer terms.** If one unit serves one end-customer organisation, say so
  in those words. A counterparty will otherwise reasonably assume one unit serves every customer.
- **What may be exposed vs what may not be sold.** Positive phrasing for the proposal: *you may make
  the capability available inside your product to your customers; it may not be resold as a
  standalone product.*
- **L1 support belongs to the counterparty** for its own customers; we support the counterparty.
- **Who owns the end-customer contract and the representations made in it.**
- **The two-sided door.** A large end customer may later want to run the capability on its own terms
  (customer-hosted, on-prem). Leave that door visibly open; do not price it in this proposal.
- **Scaling.** How price moves with end-customer count, and where a tier boundary is a *feature*
  boundary rather than a unit-count wall. Read the tenant's rule; never invent a ceiling.

### Reseller / channel
- **The revenue base, defined to survive bundling.** Software and licence revenue, net of pass-through
  and tax, excluding services — plus two clauses, or the base can be discounted to nothing:
  - **pro-rata discounting** — a bundle discount lands proportionally on software and services;
  - **allocation consistency** — the software/services split matches the partner's standalone
    pricing and its statutory reporting.
- **Share by sourcing** (who found the deal), with a **floor** and what the floor covers.
- **Deal registration**: how a deal is registered, the lookback window, how conflicts resolve,
  reconciliation cadence. A timestamped registration trail is what makes a partner trust you.
- **Price protection**: what the partner can rely on for a registered deal.
- **Support split**: partner L1 to the end customer; our L2/L3 to the partner only.
- **Enablement and governance**: named owners each side, working and executive cadence.
- **Exclusivity**: not offered unless the tenant has decided it for that market. Absence of a
  decision is not a decision.

External reference points (2026 industry guides — re-check before quoting a figure): reseller margins
commonly sit around 20–40% and referral fees around 15–30%; tiered structures tend to outperform flat
rates on partner-sourced revenue; deal registration is the standard trust mechanism. A tenant's share
outside that band is not wrong — it should be *written down as deliberate*.

## Outbound value — in any shape

**Outbound value** is anything flowing from us to the counterparty: a cash grant, research or
evaluation funding, service credits, free months, free professional services, co-marketing or event
spend. It appears in all three shapes and is never a discount, rebate or credit against price.

It needs, every time:

1. **Its own instrument** — a side letter, funding agreement or SOW. Default commercial agreements
   rarely carry a payment *from* the vendor, and many say explicitly the vendor is not obliged to fund
   or participate in pilots.
2. **Conditions tied to its purpose** — payable only when what it is for is real: the named pilot or
   programme is approved and under way, **our product is in use in it**, and the resource it funds is
   engaged. Value that flows before its purpose exists is a gift, and reads as one.
3. **An observable start date** — an event with evidence (first participant enrolled, first production
   traffic), never a plan date.
4. **A witness for each condition** — prefer one we observe ourselves: product use from the product's own
   usage records; the rest from documents the counterparty supplies (approval letter, engagement letter).
5. **Payment timing, longstop, repayment** — when it is paid once conditions are confirmed; the date after
   which the offer lapses; what is repaid if funds are not applied or our product is withdrawn.
6. **Deliverable** — what it must produce, and who owns that output.
7. **Net position** — year-1 revenue minus outbound value, computed, on the internal brief's first line.
8. **Independence** — if it funds an evaluation, study or evidence about outcomes our product enables,
   that evidence is *funded by us*. Fund at arm's length, pre-register the analysis plan before data
   is seen, give the funder no veto over results or publication, and disclose the funding wherever the
   result is cited. "Independent validation" is the phrase that ends credibility when the funding
   surfaces later.
9. **Propriety** — when the end customer or pilot host is a public body (a public hospital, a
   regulator, a state-owned enterprise), structure and document it so it cannot be read as an
   inducement connected to winning that work, and route it through counsel under the applicable
   anti-corruption law before it is offered.
10. **Human-subjects research** — an efficacy study on patients or members of the public usually needs
   ethics-board approval; align the trigger with that approval, not with a launch date.
