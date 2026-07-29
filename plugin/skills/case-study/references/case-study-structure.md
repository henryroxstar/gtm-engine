# Case study — section spec, evidence tiers, and the banned-pattern list

The document spec. `body_template.md` Step 6 renders against this. Default is the 2-page layout;
`--onepager` keeps the front half only, plus a compressed evidence line.

**Read the length budget at the bottom before drafting, not after.** It is measured against the real
renderer and it is tighter than it looks — roughly 330 prose words for the whole document. Writing
to a comfortable length and cutting afterwards wastes a pass.

---

## Evidence tiers — resolve first, everything else follows

| Tier | Trigger | Title | Metric treatment | Verb tense |
|---|---|---|---|---|
| **Deployed** | Live in production; outcomes measured | `Case Study` | baseline → after → measurement window | past ("cut", "reduced") |
| **Pilot** | POC/pilot complete; partial or early outcomes | `Pilot Story` | scoped to the pilot; state N and window | past, scoped ("across the 6-week pilot") |
| **Design-stage** | Designed; not yet delivered | **`Solution Story`** | **modeled**; baseline + assumption inline | conditional ("is designed to", "models out at") |

**Rules that are not negotiable.**

- A design-stage engagement never produces a document titled "Case Study."
- A modeled number never appears in past tense.
- A capability tagged ROADMAP or Design-target never appears in a results section, in any tier.
- Default to the lower tier whenever the evidence is ambiguous.

### Metric basis tags

Every figure carries exactly one:

| Tag | Means |
|---|---|
| `measured` | Instrumented; the vendor or customer can show the measurement |
| `customer-reported` | The customer stated it; not independently verified |
| `modeled` | Calculated from a stated baseline and a stated assumption — both must appear |
| `(~unverified~)` | Sourced but unconfirmed; carried forward as-is, never silently upgraded |

In the body, tags ride as a superscript marker or a parenthetical; in the evidence log they are
explicit. An untagged number is a defect, not a style choice.

---

## Front half — the whole story for a skimmer

Roughly page 1. There is **no forced page break** between the halves — see the length budget.

### 1. Headline
The customer's outcome, one line. **The customer is the grammatical subject.**

- Deployed/Pilot: `How <Customer> <verb-past> <outcome> <qualifier>`
- Design-stage: `How <Customer> is <verb-ing> <outcome>` or `<Customer>: <the problem>, solved`

Never "We helped <Customer>…". Never a product name in the headline unless the customer's own
outcome is unintelligible without it.

### 2. Dateline
One line, pipe-separated: `Industry · Region · Company size · Product · <Tier badge>`
The tier badge is visible, not buried: `Deployed` / `Pilot` / `Design-stage — modeled outcomes`.

### 3. Results strip
Exactly three metrics. Each: the number, a ≤6-word label, and its basis tag. Tie every one to a
problem named later in the challenge section — a metric with no matching problem gets cut.
Design-stage: all three are `modeled`, and the strip is headed **"Modeled outcomes"**, not
"Results".

**Deployed but unquantified — use a capability strip.** A deployed engagement often has no
publishable metrics: the customer hasn't measured, won't share, or the value isn't a number. That is
*not* a reason to downgrade the tier, and it is *never* a reason to reach for an adjacent metric the
integration didn't produce. Head the strip **"What shipped"** and give three concrete, verifiable
capability statements instead of figures — each a thing that is now true and wasn't before. Keep the
same three-cell discipline; drop the basis tags, since there is no figure to source.

Borrowing a number from elsewhere in the customer's business to fill the strip is the worst
available option — it is the "metric with no matching problem" failure in its most damaging form,
because a real number attached to the wrong cause reads as a deliberate claim rather than a slip.

### 4. At a glance — Markdown and HTML only, **not** the Word layout
Three short blocks — Challenge / Solution / Results — 25 words each, or a BEFORE/AFTER pair. The
version most readers actually consume. Write it last, from the finished body.

**Omitted from the `.docx` on purpose.** Measured, it costs ~90 prose words of front-page room — and
in a 2-page document it largely restates the challenge and outcome sections a few inches below it.
Markdown and HTML have no page limit, so it earns its place there. If a Word render must include it,
swap it *for* the challenge and outcome prose rather than adding it on top, and re-check the page
count.

### 5. Why now
One paragraph. The industry shift that makes a lookalike buyer feel the same pressure, anchored by
**one** sourced, dated stat. Regulatory deadline, category shift, cost pressure, or a new class of
risk. This paragraph is why the document travels beyond the named account — it comes *before* the
customer's own problem, so the reader recognises their situation before they read anyone else's.

### 6. The challenge — Pain
What broke, in the customer's vocabulary. Concrete and specific: the manual process, the volume,
the risk, the cost, the person doing it. Then the credibility paragraph:

> **What they tried first, and why it failed.**

One short paragraph naming the alternative approach and the reason it didn't hold. Most commonly
omitted section; highest credibility per word in the document.

### 7. The solution — Claim
What was deployed (or designed), in three to five concrete steps. Name the components. Plain
English — a reader outside the vendor's category must follow it. State what made this different
from the alternative in §6.

**Not a feature list.** If the section reads as bullets of capabilities rather than a sequence of
what happens, rewrite it.

### 8. The outcome — Gain
The quantified result, expanded from the strip: baseline → after → delta → measurement window. Then
**one qualitative outcome** — the thing that changed for an actual person. Numbers prove; the
qualitative line is what gets remembered.

Design-stage: this section is titled **"Expected outcome"** and every figure shows its baseline and
assumption inline.

### 9. Quote
One, on page 1. Verbatim, attributed to a named person with a title. About the change, not the
vendor. Missing → `[QUOTE PENDING — request from <name>, <title>]`, never a composed substitute.

---

## Back half — the depth (dropped by `--onepager`)

### 10. How it works
A compact numbered flow, 4–7 steps, for the technical reader. Optionally one small diagram. Enough
that an engineer can tell it's real; not so much that it becomes the solution design.

### 11. Why they chose <vendor>
The alternatives genuinely considered and the deciding factor. Naming a real alternative is what
makes this credible — a section that names no alternative reads as marketing and should be cut
rather than padded.

### 12. What's next
The expansion path, in the customer's words where possible. Proves the deployment stuck. For
design-stage, this is the V1 → V2 cut from the solution design.

### 13. Applicability panel
**"This applies to you if…"** — exactly three bullets, each a condition a reader can test against
their own situation (not a benefit restated). This is what converts the document from a marketing
artifact into a selling one. Derive the conditions from the shape the story matches in the profile's
proof library, not from the customer's industry alone.

### 14. Evidence & sources
The verification log. One row per claim: **claim · number · basis · date · source**. Same shape as
the `## Sources & verification log` sections in the knowledge packs. Close with the tier statement
in one sentence, plainly — e.g. *"Outcomes are modeled from the current-state baseline captured in
discovery on <date>; the solution has not yet been deployed."*

---

## Banned patterns

Each of these is a rewrite trigger, not a style preference.

| Pattern | Why it fails | Fix |
|---|---|---|
| "We helped <Customer>…" | Vendor as hero | Customer becomes the subject |
| An unsourced round number ("40% faster") | No baseline, no window, no basis | Add all three or downgrade to `modeled` |
| Feature bullets in the solution section | Describes the product, not the change | Rewrite as a sequence of what happens |
| Adjective quotes ("great to work with") | Says nothing about the outcome | Cut, or replace with an outcome quote |
| A metric with no matching problem | Reader can't tell why it matters | Cut the metric or add the problem |
| Named customer or person with no approval record | Legal and relationship risk | Track in the approval pack before it goes out |
| Past tense on a modeled number | Reads as an achieved result | Conditional tense + basis tag |
| A roadmap capability in a results section | Claims what doesn't ship | Move to "what's next" or cut |
| Jargon in the headline or at-a-glance | Skimmer bounces | Plain language; jargon can live in §10 |
| Two or more quotes on page 1 | Crowds out the story | One on page 1, two in the document |

---

## Length budget — measured, not estimated

These numbers were measured against the actual Word renderer (spec → `.docx` → PDF → page count),
not guessed. The document is chrome-heavy — a banner, a results strip, two tables, three callouts,
eight headings — and the chrome, not the prose, is what consumes the page. Treat the budget as a
hard ceiling.

**Front half — 200 prose words** (verified 1 page; 220 still fits, 248 spills):

| Section | Words |
|---|---|
| Why now | 28 |
| The challenge | 50 |
| What they tried first | 22 |
| The solution | 56 |
| The outcome | 30 |
| Quote | 14 |

**Back half — 130 prose words** (verified 1 page):

| Section | Words |
|---|---|
| How it works (4 steps) | 36 |
| Why they chose us | 32 |
| What's next | 24 |
| Applicability (3 bullets) | 27 |
| Tier sentence | 11 |

Headline, dateline, results-strip cells, table headers, and the evidence rows sit outside these
counts — they are chrome and are already accounted for in the measurement.

`--onepager` = the front half only, same 200-word ceiling.

### Three findings that came out of the measurement

1. **Never emit a `pagebreak` block.** It renders a fully blank page between the halves — 3 pages
   with it, 2 without, at identical content. The front/back split is a *length budget*, not a forced
   break; let the content flow.
2. **The results strip is one row, three columns** — not a three-row `facts_table`. The single-row
   form buys ~40 more prose words on the front page for the same three metrics.
3. **No separate `sources` block.** The evidence table already carries a "Source & date" column;
   a `sources` block duplicates it and costs room the story needs. (It also takes objects
   `{text, url}` for `external` but plain strings for `internal` — an easy render crash.)

Over budget → cut "How it works" and "What's next" first. Never cut the evidence log, the basis
tags, or the tier badge to make room.
