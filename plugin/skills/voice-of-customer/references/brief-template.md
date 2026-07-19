# Voice-of-customer brief — section skeleton

The generic shape for the markdown deliverable. Audience is **product + engineering AND a
detail-oriented, skeptical Chief Strategy Officer / CTO** — assume they have *no* GTM-engine context
and little tolerance for unproven claims. Educational and evidence-first, never a pitch.
Company-agnostic — no hardcoded brand or account names in this template. Fill every quote/number from
the loaded sources; take all coverage/freshness figures from the Step-1 `source-manifest-<date>.json`.

**The spine:** customer voice and BD focus are kept in separate sections; the divergence section is
where they meet. Never let a customer-voice claim borrow BD's framing as evidence, or vice versa.

**Five principles this template exists to enforce** (a skeptical exec will punish their absence):
1. **Disclose the sample and its skews up front** (§1) — geography, company stage, industry, persona,
   listening-channel mix. Name the biases plainly; a hidden skew reads as a rigged read.
2. **Prove, don't say** — every demand links to a **verbatim evidence** entry (Appendix B) with
   attribution, source class, and *breadth* (how many independent sources). Show counts.
3. **Define every term** — execs won't know MCP, DID, NHI, the product names, or the listening tools.
   Define inline on first use and collect them in a glossary (Appendix D).
4. **Don't push engineering too early** — the "what to do" framing is **validate before building**
   (§7), not "close deals / build this." Mark thin signals as directional; give each a validation test.
5. **Detail lives at the back** — the main body is summary; appendices carry the manifest, the verbatim
   library, the corpus detail, the glossary, and the method, so a skeptic can inspect for themselves.

```markdown
# What the market is telling us — Voice of the Customer

_<brand_name> · <markets> · <date> · internal, directional read for Product & Strategy — not a
customer document, not committed demand_

> How to read this: customer voice vs BD focus (kept apart); top-of-funnel & directional (discovery,
> not a build trigger); every claim auditable at the back (App. A–E); terms in the glossary (App. D).

## 1. Who this is pulled from — and where it's skewed   ⟨the anti-bias panel — put it FIRST⟩
The actual sample, biases stated plainly (not hidden). Cover, from PROFILE + the manifest + the briefs:
- **Geography** — the markets in scope, and which regions are **absent** (a skew to disclose).
- **Company stage** — target vs actual mix (flag if a recent run over-represents one).
- **Industries** — heaviest → lightest, from the researched accounts; name the concentration.
- **Buyer personas** — who was targeted / who actually spoke; flag the dominant seat.
- **Listening-channel mix** — the social-listening breakdown (counts), and what it is NOT (e.g. no
  LinkedIn = practitioner not buyer voice).
Close with a **stated-skews** box: 3–5 one-liners a reader should discount for.

## 2. How to read the six sources — customer voice vs BD focus
A table: source · speaker · **what this corpus actually contains** (plain language). Lead with the
honest point that most sources are our own output (bd-focus), only a few are external. State the
confidence rule (≥2 independent customer-voice sources = High).

## 3. Executive read
Strongest demand (with confidence + an evidence pointer) · sharpest divergence · **what to do =
discovery, not engineering**. Close with a one-line caution: nothing here is confirmed paid demand.

## 4. Customer voice — what the market says & does   ⟨customer-voice only⟩
Table: what the market is saying · plain-language meaning · **breadth (N independent)** · confidence ·
**evidence link** (to Appendix B). No BD framing in this section.

## 5. BD focus — where our sales motion is pointed   ⟨bd-focus only — our bet, not demand⟩
Where BD is working (segments/accounts) + the hooks BD leads with. Labelled "our strategy, reported
as-is"; makes no demand claim.

## 6. Alignment & divergence
The 3-way matrix: **Validated** (market + we're on it) · **Leading bet** (we push it, signal thin —
pressure-test) · **Missed pull** (market asking, we're not leading — the opportunity). Populated by
comparing §4 and §5, never merging.

## 7. Signals to validate — before we build anything   ⟨discovery, not engineering⟩
Open with a **caveat banner**: top-of-funnel, not confirmed demand, discovery-not-build. Table: signal
· **what we've actually seen (and how thin)** · **what would confirm real demand** (the validation
test) · **don't do yet**. This replaces any "close deals in N months" framing — do not overcommit on
thin, top-of-funnel data.

## 8. Demand hypotheses vs. what we can build today   ⟨distance map, with evidence — not a roadmap⟩
For the strongest signals, one card each: **demand hypothesis + evidence trail** (which Appendix-B
items, N independent sources, confidence) · **current capability** (shipped / in-build / design-stage /
gap, from PRODUCT.md) · **before investing: the bar to clear** (the earliness gate) · **honest
ceiling**. Include any internal-hardening item honestly, labelled "not a demand play." No capability
the sources/PRODUCT.md don't ground.

## Appendix A — Source manifest
The collector's coverage table (source · speaker · latest · age · corpus size) + the manifest file path.

## Appendix B — Verbatim evidence library
One entry per customer-voice claim, each with an id the §4 table links to: the verbatim/near-verbatim
quote · attribution (who/where/date) · source class · **breadth** · confidence · corroboration.
Single-source items flagged "not for external citation."

## Appendix C — Corpus detail
Named accounts by segment (from the dossiers) + the listening-channel counts + a one-line
**representativeness verdict** (what this read IS and is NOT).

## Appendix D — Glossary (plain language)
Every product/market/tool term used, defined for a reader with no GTM-engine context.

## Appendix E — Method & caveats
Speaker rubric, confidence rubric, single-source flags, empty-intent-pre-cycle note, the expert-lens
note (real named practitioners' frameworks synthesized + hedged — name the sources, e.g. a roster of
who they are and the axis each covers), and "not read: no CRM data (off-by-default future source)."
```

## The confidence rubric (how to grade a customer-voice demand)

Grade by **independent corroboration across customer-voice source classes** — organic (social
listening), behavioral (intent), news, and a customer's own quoted words. Report BD focus separately;
it never raises a demand's confidence.

| Grade | Bar |
|---|---|
| **High** | ≥2 independent customer-voice classes corroborate, or one class + multiple named customers' own words. |
| **Medium** | One customer-voice class, or several customers' words in one class. Directional. |
| **Low / watch** | Single source, or single-source-flagged news, or only BD framing asserts it (→ a *leading bet*, not demand). |

## Authoring rules

- **Two speakers, never merged.** Customer-voice cites customer-voice; BD focus is labelled our bet.
- **Disclose skews, prove with verbatim, define terms, validate-before-build, detail at the back** —
  the five principles above are non-negotiable for the skeptical-exec reader.
- **Opportunity = demand − capability**, with an earliness gate. Never an invented roadmap; anchor to
  PRODUCT.md.
- **Reuse, don't invent frameworks** — Pain·Claim·Gain personas + hook-matrix why-now already exist.
- **Carry verification flags** — single-source items stay flagged; the expert lenses are presented as
  real named practitioners' frameworks synthesized by us (name the sources), never fabricated personas
  or demand; empty intent surging-lists are not overread.
- **Product/eng + exec register** — plain language, name the noun, spell out shorthand once. Evidence,
  not a sell.
