# Proposal structure — external proposal and internal brief

Two files. The external proposal is written for four readers at once — the exec sponsor, finance,
procurement and whoever does security/legal review — and stays **high level**: target **four to six
rendered pages**. Depth (architecture, integration detail) belongs to a solution design, not here.
The internal brief is written for the people who approve the deal and is **never sent**.

Why two files and not a fenced section: a section headed "omit from customer copy" inside the
customer copy is an instruction a forwarded PDF ignores. Separate files make it structural.

---

## External proposal — `commercial-proposal-<slug>-<YYYY-MM-DD>.md` (+ `.docx`)

Sections in this order. Omit a section only where this table says it is conditional.

| # | Section | What goes in it | docx block |
|---|---|---|---|
| 0 | **Banner** | `<Counterparty> × <our brand> — Commercial proposal`; subline = product(s) · shape; meta = date · version · valid until | `banner` |
| 1 | **Status** | the not-a-contract stamp (below), verbatim in substance | `callout` |
| 2 | **Summary** | ≤ 5 lines: what is proposed, for what purpose, unit price + term + **year-1 total**, the next step | `paragraph` |
| 3 | **Context** | their situation in **their** words, attributed to where we heard it (meeting, email, their public material) — not our pitch | `paragraph` |
| 3a | **How <product> would be used** | the use case in a few rows: each control or capability, when it runs, what it does *here*. Every capability traces to the product's own reference docs; configuration is labelled illustrative until tested. Detail goes in a companion use-case note. **Conditional:** when the deal is for a specific use | `paragraph` + `table` |
| 4 | **What we propose** | in scope / explicitly out of scope | `two_col` (leftTitle "IN SCOPE", rightTitle "NOT IN SCOPE") |
| 5 | **Commercial terms** | construct · unit of charge **and its definition** · unit price · units · term and minimum commitment · monthly total · **year-1 total** (and term total if the term is not 12 months) · invoicing and payment · tax basis · what increases spend · renewal pricing | `facts_table` |
| 6 | **Who does what** | us / them: onboarding, L1/L2 support, end-customer relationship, data roles | `table` |
| 7 | **Service levels** | the default agreement's objectives table, plus the sentence *"These are objectives, not commitments; they carry no service credits."* | `table` + `paragraph` |
| 8 | **Product maturity** | per product: release status as the product's own record states it, what is and is not committed, docs availability, what happens if it does not mature in the term. **Conditional:** omit only if every product is GA | `table` + `paragraph` |
| 9 | **Separate from these commercial terms: <name>** | outbound value: amount · what it is for · **conditions for payment** (tied to the purpose, including our product being in use) · **start date as an observable event** · how each condition is verified · payment timing · longstop · repayment/unspent funds · evaluation outputs and publication · data · instrument · independence statement where applicable. **Conditional:** only if outbound value exists | `heading` + `facts_table` + `callout` |
| 10 | **Assumptions and dependencies** | what the price and dates assume | `paragraph` bullets |
| 11 | **Next steps** | mutual action plan: step · owner (named side) · target date — ending at signature and go-live | `table` |
| 12 | **Key terms and what this becomes on paper** | the tenant's **product-agnostic key terms** (`knowledge/commercial/key-terms.md` "Shown in a proposal") with every *per product* row set for this product, then the agreement(s) it becomes. Where no template covers the product, say the agreement will carry these key terms with the product specifics set | `facts_table` + `paragraph` |
| 13 | **Contacts** | named owners each side | `facts_table` |

### The status stamp

> **Draft — not an offer.** This is a non-binding proposal for discussion. It creates no obligation on
> either party; no agreement exists until a written agreement is signed by both. Figures exclude
> <tax basis>. Valid until <date>.

Default validity: 30 days from the proposal date, unless the operator sets another.
While any **before-sending** item is open, add a second line: **Internal draft — not for distribution.**
Remove it only when the internal brief's "Decisions needed — before sending" is empty. Items tagged
**before signature** (drafting the agreement, counsel review, tax treatment) do not hold back a
non-binding proposal.

### Words the external proposal does not use

Unless the tenant's default agreement actually carries the commitment:
`SLA` · `uptime` · `guarantee` / `guaranteed` · `service credits` · `availability target` ·
`GA on <date>` / any release date · `roadmap commitment` · `exclusive` · `partnership` for a client or
embed deal (it is a supply relationship; most agreements disclaim partnership outright) ·
`independent` for evidence we fund · `discount` / `rebate` / `credit` for outbound value.

Three negations are **required**, not banned: *"they carry no service credits"* (§7), *"not a discount
on, or credit against, subscription fees"* (§9), and *"non-exclusive"* in the right to use (§12). A check that matches the bare words flags every correct
proposal — match the word used as a commitment or characterisation, not its denial.

Never: a signature block, "agreed and accepted", "accept by signing", any acceptance field, or any statement that the proposal binds either party (the stamp's own "non-binding" is required, not banned).

### Variant inserts

| Shape | Add | Leave out |
|---|---|---|
| Direct client | self-serve vs assisted onboarding, stated | end-customer rows in §6 |
| Embed | §5 unit definition in end-customer terms; §4 "may be made available inside your product / may not be resold standalone"; §6 their L1; one line in §3 or §12 leaving the two-sided door open | reseller economics |
| Reseller | §5 becomes: revenue base definition, share by sourcing, floor, deal registration + lookback + reconciliation, price protection; §6 L1 partner / L2–L3 us; §11 enablement and governance cadence | per-unit list pricing to end customers, unless the tenant publishes it |

---

## Internal brief — `commercial-proposal-<slug>-<YYYY-MM-DD>-internal.md`

First line, always: **Internal only — never send to the counterparty.**

| # | Section | What goes in it |
|---|---|---|
| 1 | **Net position** | year-1 revenue, outbound value, net year 1 — pasted from `gtm_core.commercial quote` output. If negative, the next sentence names it: *a market-development investment, not a revenue deal* |
| 2 | **Decisions needed** | two lists, each with owner: **before sending** (keeps the external "not for distribution" line) and **before signature** |
| 3 | **Why this construct and this price** | the rationale a finance approver would ask for |
| 4 | **List vs offered** | list price, offered price, the override reason, authority it rests on (or "no authority on record") |
| 5 | **Figures ledger** | figure · where it appears · CLI command · CLI output — every currency figure in either file |
| 6 | **Gate results** | gate · witness consulted · pass / fail / open · note |
| 7 | **Precedent check** | comparable proposals found, what differs, whether the difference is deliberate |
| 8 | **Risk register** | risk · likelihood · impact · mitigation · owner (delivery, support load, commercial consistency, legal/propriety, evidence independence, product maturity) |
| 9 | **Concession ladder** | what we could give · what we would ask for in return · who must approve |
| 10 | **Competitive context** | only what changes the commercial position; optional |
| 11 | **Open decisions** | by the tenant deal desk's own ids where they exist |
| 12 | **Proposed precedent row** | one row in the shape of the tenant's precedents log, for the operator to add — this skill does not write under `profiles/` |
| 13 | **Sources** | every document, meeting and file relied on |

---

## Key terms when the product has no agreement template

Reuse the terms of the template you have, classified clause by clause:

- **as-is** — already reads for any product (payment, tax, interest, law, confidentiality, data roles);
- **generalised** — names another product, but the term is neutral once the name is replaced (right to
  use, no resale, minimum term, support boundary);
- **per product** — depends on what the product is (the unit, the fee line and allowances, the outputs
  disclaimer's description of the product, how the lock-in is expressed).

Show as-is and generalised terms in product-neutral words; set every per-product term for this product,
or leave it out and list it in "Decisions needed — before signature". If the tenant has no
`key-terms.md`, do this classification in the internal brief and record the missing file as a finding —
this skill does not write under `profiles/`.

## .docx render spec

The external `.docx` is rendered from a JSON spec by the committed block renderer shared with the
account-dossier skill. Top-level keys:

```json
{
  "closingLine": "Draft — not an offer · <counterparty> × <our brand> · Confidential",
  "brand": {"bg": "<palette.canvas>", "accent1": "<palette.primary>", "accent2": "<palette.primary>",
            "accent3": "<palette.accent>", "textOnDark": "FFFFFF"},
  "blocks": [ {"type": "banner", "account": "...", "subline": "...", "meta": "..."}, ... ]
}
```

Block shapes: `banner {account, subline, meta}` · `callout {lines[], variant?}` · `heading {text}` ·
`paragraph {text}` · `facts_table {rows: [[label, value], ...]}` · `table {header[], rows[[...]]}` ·
`two_col {leftTitle, rightTitle, left[], right[]}` · `sources {external: [{text, url}], internal: []}` ·
`spacer` · `pagebreak`. Colours are hex without `#`.

**`closingLine` is mandatory.** The renderer's default footer reads *"Internal briefing — not for
customer distribution"* — correct for a dossier, wrong on every page of a document written to the
counterparty.
