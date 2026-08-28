# Market-intelligence brief — section skeleton

The generic shape for the markdown deliverable. Audience is **product + engineering AND a
detail-oriented, skeptical Chief Strategy Officer / CTO** — assume they have *no* GTM-engine context
and little tolerance for unproven claims. Educational and evidence-first, never a pitch.
Company-agnostic — no hardcoded brand or account names in this template. Fill every quote/number from
the loaded sources; take all coverage/freshness figures from the Step-1 `source-manifest-<date>.json`.

**The spine:** the eight speakers are kept in separate sections; the divergence section is where
customer voice and BD focus meet. Never let a customer-voice claim borrow BD's framing as evidence, or
vice versa — and never let a spec proposal (`standards-voice`, §7b), a competitor changelog
(`vendor-voice`, §4b), our own release notes (`own-voice`, §3b), or a regulator action
(`regulator-voice`, §4c) count as demand. Only `customer-voice` does.

**Five principles this template exists to enforce** (a skeptical exec will punish their absence):
1. **Disclose the sample and its skews up front** (§1) — geography, company stage, industry, persona,
   listening-channel mix. Name the biases plainly; a hidden skew reads as a rigged read.
2. **Prove, don't say** — every demand links to a **verbatim evidence** entry (Appendix B) with
   attribution, source class, and *breadth* (how many independent sources). Show counts.
3. **Define every term** — execs won't know MCP, DID, NHI, the product names, or the listening tools.
   Define inline on first use and collect them in a glossary (Appendix D).
4. **Don't push engineering too early** — the "what to do" framing is **validate before building**
   (§6), not "close deals / build this." Mark thin signals as directional; give each a validation test.
5. **Detail lives at the back** — the main body is summary; appendices carry the manifest, the verbatim
   library, the corpus detail, the glossary, and the method, so a skeptic can inspect for themselves.

```markdown
# What the market is telling us — Voice of the Customer

_<brand_name> · <markets> · <date> · internal, directional read for Product & Strategy — not a
customer document, not committed demand_

> How to read this: customer voice vs BD focus (kept apart); top-of-funnel & directional (discovery,
> not a build trigger); every claim auditable at the back (App. A–F); terms in the glossary (App. D).

## 0. Since last issue   ⟨computed delta, not recollection⟩

Run `uv run python -m gtm_core.voc.delta --profile <active>` before synthesizing. Render:
- **Baseline notice** — if this is the first issue (no prior `signals-<date>.json`), say so explicitly:
  *"No prior issue — this is the baseline."* Never render as "nothing changed."
- **New** — signal ids absent from the prior issue.
- **Escalated** — direction moved toward `threat`, or `material` went false → true.
- **Decayed** — prior signals past `date + decay_days` and not re-observed.
- **Resolved** — signals explicitly closed this run (triage → `ignore`).
- **Still ignored** — `triage: ignore` carried forward; print compactly once.

Keep this section short: counts and one-line bullets. The full evidence lives in §3/§4b and
Appendix B.

## 1. Who this is pulled from — and where it's skewed   ⟨the anti-bias panel — put it FIRST⟩
The actual sample, biases stated plainly (not hidden). Cover, from PROFILE + the manifest + the briefs:
- **Geography** — the markets in scope, and which regions are **absent** (a skew to disclose).
- **Company stage** — target vs actual mix (flag if a recent run over-represents one).
- **Industries** — heaviest → lightest, from the researched accounts; name the concentration.
- **Buyer personas** — who was targeted / who actually spoke; flag the dominant seat.
- **Listening-channel mix** — the social-listening breakdown (counts), and what it is NOT (e.g.
  practitioner not buyer voice). Syften itself has no LinkedIn crawl — state that as a real limit of
  *that tool*, but pair it with `linkedin_replies.extra.original_posts_captured` from the Step-1
  manifest (real LinkedIn posts quoted verbatim in reply drafts — a separate, genuine channel) so "no
  Syften LinkedIn coverage" doesn't misread as "we have zero LinkedIn signal."
Close with a **stated-skews** box: 3–5 one-liners a reader should discount for.

## 2. Executive read
Strongest demand (with confidence + an evidence pointer) · sharpest divergence · **what to do =
discovery, not engineering**. Close with a one-line caution: nothing here is confirmed paid demand.

## 3. Customer voice — what the market says & does   ⟨customer-voice only⟩
Table: what the market is saying · plain-language meaning · **breadth (N independent)** · confidence ·
**evidence link** (to Appendix B). No BD framing in this section. Restate the confidence rule in one
clause here (`strong` = ≥3 independent customer-voice sources; 2 = `moderate`; 1 = `weak`) and point to
§8 for the full source split — the grading rule must not be first defined *after* the table that uses
it.

**Split the breadth count by seat.** Breadth counts distinct `(source_id, entity)` pairs, so one named
practitioner counts exactly as much as one bank. That is the rule and it is not being changed — but a
pooled number hides *whose* voice it is, and "whose voice is this?" is the skeptic's first question
(§1). Render breadth as **total (enterprise filers · practitioners · behavioral)** wherever the mix is
not homogeneous, e.g. `14 (9 filers · 5 practitioners)`. A claim carried mostly by practitioner posts
is a genuinely different fact from one carried by enterprise risk disclosures, and the reader must be
able to see which without opening Appendix B.

**Flag non-retrievable sources.** Some customer-voice records — LinkedIn captures in particular — are
verified (someone read the passage) but have **no public URL**; their stored `url` is a local capture
path. They legitimately count toward breadth, and they are legitimately **not externally citable**.
Say how many of a claim's sources are in that state, right in the row or its footnote. Never present a
locally-captured quote as something a customer could be shown.

## 3b. What we shipped this window   ⟨own-voice — the baseline, not a signal⟩
Dated releases, blog posts, changelog entries, and shipped capabilities from `own_product_watch`. This
is the **capability baseline** §7 measures demand against — explicitly *not* a market signal and *not*
counted toward demand breadth. Keep it short: date, what shipped, and a one-line implication for how it
changes the demand/capability distance map.

## 3c. What people say about us   ⟨outside-in mentions + own-voice context — never demand⟩

The outside-in view: what shows up when you look for the company's name **beyond its own
channels** — and, just as important, what doesn't. Four checks, each reported honestly:

1. **Practitioner channels** — search the social-listening archive for the brand name (every post,
   not a sample) and report the count plainly, including zero. Zero is a finding: state how many
   consecutive windows it has held.
2. **Third-party vendor/analyst maps** — search for category landscape pieces in the window and say
   whether the company appears. Name and link every map checked, and state the count checked ("six
   maps is six, not all analyst coverage").
3. **Coverage of the company's own announcements** — distinguish **syndication** (wire pickup of
   our own release) from **independent coverage** (someone else's words). Syndication is presence,
   not proof anyone is talking about us.
4. **Independent echoes** — a third party arriving at our framing without naming us. Validates the
   architecture; does nothing for the name. Never inflate an echo into a mention.

A mention is not demand and contributes nothing to any §3 confidence count; silence is not absence
of a market. State the coverage limits (channels the listening tool cannot see, whether a brand
filter exists, how many maps were checked). In the HTML companion this section absorbs §3b as a
three-line shipped strip — see `references/html-companion.md`.

## 4. BD focus — where our sales motion is pointed   ⟨bd-focus only — our bet, not demand⟩
Where BD is working (segments/accounts) + the hooks BD leads with. Labelled "our strategy, reported
as-is"; makes no demand claim.

## 4b. Competitor movement   ⟨vendor-voice only — revealed roadmap, not demand⟩
Iterate `profiles/<active>/knowledge/competitors.toml`. For every competitor whose `status` is `active`
or `watch`:

- **Dated event in the window** — funding, product launch, enforcement response, or significant
  changelog entry, with a **primary source link**.
- **Verdict** — one of:
  - `overlap` — directly competes with the core wedge;
  - `adjacency` — could enter from the side or bundle around us;
  - `non-issue` — noise or not materially relevant to the wedge.
- If there is **no movement in the window**, state so explicitly. Absence is a reported fact, not
  silence.

Also render any `registry-proposals-<date>.json` candidates under **"Proposed additions (not yet
tracked)"**, naming the evidence that surfaced each one. These are proposals only — they reach the
registry only through an operator-approved `apply`.

This section is `vendor-voice`: it records what rivals are building, never what customers asked for.
It does **not** count toward a demand's breadth.

## 4c. Regulatory & enforcement clock   ⟨regulator-voice — forcing function, not demand⟩
Jurisdiction · date · obligation-or-penalty · which cohort/persona it arms. Dates only where the primary
text was actually read. A regulator action is a **forcing function**: it creates urgency, but no
customer said anything. Do not let an enforcement headline appear in the customer-voice breadth count.
Close with the lane's coverage line (nothing new since / not pulled / pull failed).

## 4d. Measured failures & expert frameworks   ⟨expert-lens — third-party evidence, not demand⟩
Two kinds of material, both `expert-lens`, both non-demand:

- **`incidents_benchmarks`** — measured agent failures and published benchmarks (incident corpora,
  OWASP exploit round-ups, research papers). This is the section that carries the **quotable
  third-party proof points**, so the citation bar is at its highest here: **a number is only usable
  with a link to the primary source** — the paper, the order, the vendor advisory — never an
  aggregator's summary of it. An uncited number is worse than no number, because it will end up in a
  deck. State the methodology in one clause (what was measured, on what, under what grading), because
  a benchmark quoted without its method is how "36%" becomes folklore.
- **`adversary_personas`** — reasoning lenses built from **real, named practitioners' published
  frameworks**. Name the person/source and the axis each covers. These are real external expert
  thinking, synthesized and hedged by us — **not** the practitioner's verified quotes, and never a
  demand count.

Neither half is a customer saying anything. A measured failure proves the *problem* is real; it does
not prove anyone will fund a fix. Keep it out of every breadth number.

## 4e. Customer & prospect moves   ⟨account-event — timing signal, not demand⟩
From `customer_moves`. Corporate events at accounts **we sell to**: a round raised, an AI or
automation unit stood up, a relevant exec hired, a named agent programme launched.

The question this section answers is **"who just acquired budget, urgency and an owner?"** — which is
a *timing* signal for sales, not evidence that anyone wants what we build. Keep those two apart
explicitly, because this is the section most likely to be misread as demand: a prospect announcing an
AI programme is announcing a **capacity to buy something**, not a stated need for agent identity.

- **One row per account**, newest first: date · account · event · what changed about their capacity ·
  what it does *not* tell us. Name the owner if the announcement names one — that is the single most
  actionable field here.
- **Distinguish this from §4b.** §4b is *competitors* (from `competitors.toml`); this is *accounts*.
  If a company is in both, say so and treat it as a competitor first — a rival raising money is not a
  prospect acquiring budget.
- **Register rule still governs.** A prospect's press release is promotional and forward-looking, so
  it stays `account-event`. If that same company files a risk disclosure saying it cannot control its
  agents, **that** passage is `customer-voice` and reaches the brief via `enterprise_filings` — the
  two must never be merged, and the filing is worth an order of magnitude more.
- **This section holds third-party PII** (named execs at named companies). It lives under `content/`
  per §R9 and never enters code, tests, fixtures or docs.

Never counts toward breadth: `account-event` is excluded by speaker, in code.

## 5. Alignment & divergence
The 3-way matrix: **Validated** (market + we're on it) · **Leading bet** (we push it, signal thin —
pressure-test) · **Missed pull** (market asking, we're not leading — the opportunity). Populated by
comparing §3 and §4, never merging.

## 6. Signals to validate — before we build anything   ⟨discovery, not engineering⟩
Open with a **caveat banner**: top-of-funnel, not confirmed demand, discovery-not-build. Table: signal
· **what we've actually seen (and how thin)** · **what would confirm real demand** (the validation
test) · **don't do yet**. This replaces any "close deals in N months" framing — do not overcommit on
thin, top-of-funnel data.

## 7. Demand hypotheses vs. what we can build today   ⟨distance map, with evidence — not a roadmap⟩
For the strongest signals, one card each: **demand hypothesis + evidence trail** (which Appendix-B
items, N independent sources, confidence) · **current capability** (shipped / in-build / design-stage /
gap, from PRODUCT.md) · **before investing: the bar to clear** (the earliness gate) · **honest
ceiling**. Include any internal-hardening item honestly, labelled "not a demand play." No capability
the sources/PRODUCT.md don't ground.

## 7b. Standards & spec watch   ⟨standards-voice — a leading indicator, never demand⟩
A two-column split: **Adopted** (formally accepted — e.g. A2A `gitvote/passed`; treat as a commitment
the protocol will carry) vs **Proposed** (open, may never land — the value is lead time, not certainty).
Then an **absorption-risk** callout: when a primitive we sell is proposed as *native* to the protocol,
that is validation **and** a threat — say both. Close with the lane's coverage line, naming which of
*nothing new since \<date\>* / *not pulled* / *pull failed* applies. Nothing in this section may appear
in a §3 breadth count.

## 8. How to read the sources — the eight speakers   ⟨reference — keep it LAST, adjacent to App. A⟩
Deliberately at the back: it is the reference key to the corpus, not a finding, and it sits next to the
Appendix-A manifest it explains. §3's confidence column must therefore restate the grading rule in one
clause rather than depend on this section appearing first.
A table: source · speaker · **what this corpus actually contains** (plain language). Lead with the
honest point that most sources are our own output (bd-focus), only a few are external. State the
confidence rule and note that it is computed, not judged (`gtm_core.voc.evidence`).

## 9. Actions — Act now / Watch / Explicitly ignore   ⟨judged recommendations, not a build licence⟩

A short, triaged action table. Every item names at least one function from
**Product · Marketing · Sales · Partnerships**. **Each Act-now row must state what it is *not* a licence
to do** — preserving §6's validate-before-build discipline. Watch rows are signals to monitor; Ignore
rows are deliberate non-actions carried forward from prior issues (`triage: ignore`). No item in this
section executes automatically.

## 10. Message of the moment   ⟨judged; ties hooks to this window's evidence⟩

Rank the current `hook-matrix.md` hooks against this issue's evidence ids. For each hook, render one
of:
- **hammer** — evidence strongly supports it this window;
- **hold** — still valid but thin or noisy this window;
- **retire** — contradicted by fresh evidence or overtaken by events.

Cite the evidence ids that drove each call. This is a judgment, not a computed score.

## 11. Channel & partner avenues   ⟨partner-shaped signals, not direct demand⟩

Two sub-sections:
- **SI / consulting channel** — `si-channel` rows from `competitors.toml` (e.g. a systems integrator
  launching a practice in the space). For each, name the follow-on skill:
  `consulting-partner-brief`.
- **Product / platform partner** — partner-shaped signals (a complementary vendor, a co-sell motion, a
  bundle opening). For each, name the follow-on skill: `product-partner-brief`.

This section is `bd-focus` / `vendor-voice` — it does not count toward demand breadth.

## Appendix A — Source manifest
The collector's coverage table (source · speaker · latest · age · corpus size) + the manifest file path.

## Appendix B — Verbatim evidence library
One entry per customer-voice claim, each with an id the §3 table links to: the verbatim/near-verbatim
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

## Appendix F — Data provenance
A short, standing disclosure of what data gtm-engine uses and where it comes from — always include this,
not just when asked. Two categories, plus one explicit exclusion:
- **(a) Public data** — free, already-published info (Syften's public posts, news/regulation, the
  original post quoted inside a LinkedIn reply, published expert-lens frameworks, a prospect's own
  website/press/job postings).
- **(b) Proprietary data, sourced externally** — paid, licensed third-party providers only (RocketReach
  contact resolution + Intentsify; Vibe Prospecting firmographics + Bombora + business events) — never a
  scrape of a private system.
- **Our own material** — only the active company's already-public, customer-facing content (product descriptions,
  case studies, pitch-deck material in the profile's knowledge base) — never anything internal or
  confidential.
State plainly what **this profile's actual pipeline** does and doesn't read — don't copy a prior run's
exclusion list by default. As of this skill's v1 collector, no profile reads CRM data (an off-by-default
future source — see the "Optional sources" note above), but a profile may later wire one in, or may have
its own reasons to name other things it deliberately keeps out. Check the profile's actual configured
sources each run and state *those*, rather than asserting a blanket "we never use X" that may not hold
for every company using this skill. Note that the customer-voice sections (§3/App. B) are ~100% public
data; the BD-focus sections (§4/App. C) add the proprietary-external layer on top.
```

## The confidence rubric — computed, not judged

**Do not grade by hand.** Breadth and confidence come from `gtm_core.voc.evidence`, which counts
**distinct `(source_id, entity)` pairs** that are both `verified` and `customer-voice`. Ten quotes from
one filing are one source; an unread search hit is zero. Report BD focus separately — it never raises a
demand's confidence, and neither does a spec proposal or a competitor changelog.

| Band | Bar (`evidence.confidence`) |
|---|---|
| **strong** | ≥3 independent customer-voice sources |
| **moderate** | 2 |
| **weak** | 1 — a single source is never better than weak, however emphatic |
| **unsupported** | 0 |

Where a claim is weak, say *which* weakness: `assess()` separates `excluded_unverified` ("three
sources, none read") from `excluded_wrong_speaker` ("three sources, none of them customers"). Those are
different facts and must render differently. A demand with no records in the store has **no evidence** —
say so plainly rather than arguing it from prose.

## Authoring rules

- **Eight speakers, never merged.** Customer-voice cites customer-voice; BD focus is labelled our bet;
  standards-voice and vendor-voice are leading indicators that never enter a breadth count.
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
