# Partnership Brief — Document Structure

The twelve sections of a `product-partner-brief` collaboration brief, in order. This is the layout
spec; the method that fills it lives in [`seam-finding.md`](seam-finding.md) and the skill body.

The document is written **for two seats at once** — a founder/CEO and a CTO/CPO reading the same
file. Neither should have to hunt for their own section, so most sections carry a line for each.

Target: a **15-minute read**. Long enough to answer the CTO's objections, short enough that a
founder finishes it. If it is running long, cut the competitive section and the ecosystem context
first — never the seam, the layer boundary, or the risks.

---

## Front matter

**Title** — `<Partner product> + <our product>` framed as a joint capability, not as a pitch.
Not *"Why <partner> needs <us>"*. Prefer *"<Their thing> and <our thing>: <the joint outcome>"*.

**Meta line** — who it is for (names and roles if known), the date, and the read time.

**Outcome band** — one sentence, visually set apart, that states the finding the whole document
rests on. Usually the competitive-whitespace form:

> *the category competes on <what everyone optimises> → nobody owns <the seam>*

This is the line they will quote to each other after the call. Write it last, once you know what the
document actually says.

---

## 1 — Executive summary

Four to six sentences, no headings, no bullets. Must contain, in this order:

1. What they built and what it genuinely achieves.
2. The seam — the thing their design names and cannot produce.
3. What we bring to it, in one clause.
4. The smallest concrete first step.

If a reader stops here, they should be able to repeat the thesis correctly to a colleague.

---

## 2 — The opportunity

The market case, for the CEO. Dated, external, verifiable — and the section most likely to be
weak, because it is the easiest to write as assertion. **A market you assert is not an
opportunity; a market you can show someone buying is.**

Four blocks, all required. A draft missing any one of them is thin, and the linter says so:

**(a) The shift.** What changed, with sources — and *who the buyer became*. The most useful
framing is usually two waves: the first wave optimised for one axis and is now crowded; the second
wave is bought by a different person asking a different question. Name both people.

**(b) Who buys, and at what moment.** A table: buyer role · the moment they start caring · what
they actually ask for. Abstract "enterprises want governance" is not a buyer. Include the
**indirect buyer** — often the platform or ISV embedding the partner's product, whose motivation is
a deal they currently cannot close. That row is frequently the commercially important one.

**(c) Where it is required, not preferred.** Three to five concrete use cases. Rank them by how
*unavoidable* the need is, and lead with any case that is **structurally unsolvable** without the
missing primitive — a need that cannot be engineered around at any budget is worth more than five
that merely benefit. Map at least one onto something the partner *already builds*, so the need
arrives in their own vocabulary.

**(d) Why the position is defensible.** Name a **mechanism**, not a lead. "We're ahead" is not
defensibility. Real mechanisms look like:

- a *foundational architectural choice* a competitor cannot retrofit without re-architecting
  (the strongest, and usually the true one for an early product),
- a standards or network position where verifiers/adopters accumulate,
- a regulatory clock that rewards being early.

Then close with **what it changes commercially** — which budget line it moves the product to,
whether it converts a feature comparison into a blocker-removal, and which use cases become
sellable that were not.

**No product pitch in this section.** It should read as true whether or not the partnership
happens. **And if the market case is a thesis rather than measured demand, say so in one line** —
that concession is what makes the cheap first step in §10 read as prudent rather than timid.

---

## 3 — Why they are positioned for it

**Credit before gap — this section is mandatory and must come before §4.**

Explain their architecture in their own vocabulary, accurately enough that their CTO would not
correct a sentence of it. Name the specific design decisions that were *good*, and say what
guarantee those decisions actually deliver.

End with the honest, narrow statement of the guarantee:

> *Their mechanism proves X. It does not prove Y, Z, or W — and it was never meant to.*

That sentence is the hinge of the whole document. It is generous and precise at the same time, and
it sets up §4 without ever being a criticism.

---

## 4 — The missing primitive

The seam, stated as a **build-vs-buy decision**, never as a deficiency.

- **The slot.** Quote the artifact — the enum variant, the non-goal line, the config field, the
  roadmap item — where their own design names the capability. Name the tier (see
  [`seam-finding.md`](seam-finding.md) §2) so the reader can judge the strength of the claim
  themselves.
- **What filling it themselves costs.** In roadmap language: what they would have to build, own,
  operate, and keep current. Be specific and fair — do not inflate it.
- **What it unlocks.** The capability their product gains, in their customers' language.

Frame throughout as *"this is quarters of roadmap you can spend or redirect"*, not *"you are
missing this"*.

---

## 5 — How it works

The technical section, for the CTO. Four required parts:

- **A diagram** — the joint flow, mermaid, in the two products' own vocabulary. One diagram, not
  three.
- **An integration table** — step · what happens · which side owns it · **status tag**. Every row on
  our side tagged **Shipped** / **Confirm in V1** / **Roadmap**. Untagged claims are not permitted.
- **The layer boundary** — a short table of what each side owns, followed by an explicit sentence
  that neither absorbs the other.
- **The watch item** — the one place the two roadmaps could collide, named by us, before they find
  it. Usually an unreleased project of theirs pointing at our territory, or vice versa.

This is also where the **dependency answer** belongs, and it is the highest-value paragraph in the
document: what standards the integration is built on, and precisely what they keep if the
partnership ends. Answer it before it is asked.

---

## 6 — What each side brings

Two columns. Both must be substantive — **a one-sided list invalidates the document.**

- **They bring:** the substrate, the customer context, the domain surface, distribution into their
  users, the design constraints only they know.
- **We bring:** the primitive, the standards work, the compliance surface, our own distribution, the
  reference integration, the proof point.

Then a line on what we *get* out of it — plainly stated. A brief that lists only their gaps and our
generosity reads as a sales technique. Naming our own interest makes the rest credible.

---

## 7 — Competitive landscape

Where the category is, and what is unclaimed. **Not a scorecard of the partner's weaknesses** — you
are not qualified to grade their product in a document asking them to work with you.

- Name the real players and what each genuinely leads on.
- Say where the partner is **behind** — framed as *why speed matters*, never as *where you lose*.
- Land the whitespace: *the category competes on A; nobody owns B*, where B is the seam.

The **positioning quadrant** component belongs here.

---

## 8 — Why now

Dated, external urgency only:

- A published threat class, incident pattern, or research result.
- A regulation or standard with a date.
- A shift in how buyers procure.

Never manufacture a deadline. If one has moved or been amended, **say so** — the amended timeline is
itself the story, and honesty here is load-bearing for §10.

---

## 9 — Standards alignment

The third-party framework that independently describes the seam, with **both** products mapped
against its elements: theirs / ours / neither.

This converts an opinion about a gap into a named control objective that neither party satisfies
alone — the sentence their CTO can take to their own security and procurement stakeholders.

Cite identifiers exactly, checked against the framework's current published text. A wrong control
number destroys credibility with exactly the reader this section is written for.

The **framework coverage strip** component belongs here.

---

## 10 — Commercial shape

A staged path whose first step is **small and reversible**. Three stages is usually right:

| Stage | What happens | What it proves | Commitment |
|---|---|---|---|
| 1 | a spike / reference integration | the seam is real in code | days, reversible |
| 2 | a joint demo or design partner | a customer cares | weeks |
| 3 | commercial terms | it repeats | negotiated |

State **no exclusivity**, explicitly, by default. State what is *not* being asked for — no
re-platforming, no roadmap commitment, no licence change.

Do not put pricing in this document. The brief proposes a **shape**; terms are the operator's.

---

## 11 — Risks and open questions

Credibility is built here, not in §2.

- **Our own maturity risk, first.** Where we are early, what is not yet generally available, what
  they would be depending on. Volunteering it buys more than any claim, and they will find it anyway.
- **Their risks** — stated as questions, not assertions.
- **Open questions for the joint call** — the things neither side can answer alone. Three to five,
  specific enough to be answerable in one meeting.
- **Unresolved facts** — anything we could not verify, including any person we could not identify.
  Say "unresolved" rather than guessing.

---

## 12 — Ecosystem context *(optional)*

Only if there is genuine, dated, public substance: standards-body membership, donated
specifications, public research, notable deployments. Skip it entirely rather than padding it —
a thin ecosystem section reads as insecurity.

---

## 13 — Plain-language key *(required once 4+ technical terms appear)*

Three columns: **term · what it means · why it matters here**. The third column is what makes it
worth reading rather than a dictionary.

The brief is written for two seats at once, and the founder seat has no reason to know what a
decentralized identifier is. Gloss terms at first use in the body *as well* — a reader should
never have to jump to the back mid-paragraph — and keep this section as the single consolidated
reference.

Keep it to the terms that actually carry argument weight (typically 8–12). A glossary that defines
everything defines nothing.

---

## Sources

Every external claim, grouped by side, as inline links with the publisher named. **Public URLs
only.** Nothing internal, nothing from private access, nothing unopened.

---

## Phrasing rules

- **Their vocabulary, not ours.** Use their names for their own concepts throughout. A partner who
  has to translate your terminology into theirs is being sold to.
- **No superlatives about our product.** Capabilities and status tags, not adjectives.
- **No "you need us".** The document says *here is a slot, here is a shape that fits, here is what
  it costs you to make it yourself* — and lets them decide.
- **Concede limits early and specifically.** A conceded limit is the cheapest credibility in the
  document.
- **One idea per paragraph.** Both readers are skimming on a phone before a call.
