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

### 5. Why now — a convergence, not a trigger
One paragraph naming **three to five independent forces** landing in the same window, each
separately sourced and dated. Typical axes: demand, labour/capacity, regulation, technology, cost.
*Independent* is the operative word — five restatements of one pressure is one pressure, and reads
as one.

A single shift with a single stat is the most common defect in this section. It is not too short;
it is too thin. A reader in the buyer's seat does not act because one number moved — they act
because several unrelated things became true at once and the window is closing.

Where the technology shift is one of the forces, give it the opportunity framing rather than the
threat framing: what is *newly possible* that was not eighteen months ago. That is the sentence a
buyer forwards.

This paragraph is why the document travels beyond the named account — it comes *before* the
customer's own problem, so the reader recognises their situation before they read anyone else's.

### 6. The challenge — the buyer's unsolved job
**Not a description of the current state.** The test: does this section state what the *buyer's*
organisation cannot do, evidenced by the buyer's own measured pain — or does it narrate what the
customer's product does and what the vendor observed? If it narrates, it is the wrong section.

The shape that works:

1. **Concede the solved part.** Name the incumbent mechanism found in the falsification pass and
   grant it plainly, ideally in the heading itself. *"Registering a machine client is a solved,
   standardised step."* A reader whose first objection is answered before they raise it keeps
   reading.
2. **State what remains, in the buyer's numbers.** Their operational metrics, their peers' survey
   data, their statutory exposure. This is where the Step 4 buyer-world research lands.
3. **Locate the boundary precisely.** Point at the incumbent's own specification or documentation
   for where its coverage stops. A boundary you can cite beats an assertion you cannot.

Then the credibility paragraph:

> **What they tried first, and why it failed.**

One short paragraph naming the alternative approach and the reason it didn't hold. Most commonly
omitted section; highest credibility per word in the document.

**Every alternative is named.** "Existing approaches", "traditional tooling", "the usual pattern" —
each is a placeholder where research should be. Name the standard, the spec, the product, the
practice.

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

## Back half — the depth (dropped by `--onepager`, except §15)

### 10. The limit — what this does not earn you
One short paragraph conceding what the vendor's layer does **not** do. This is the section that
makes the rest believable, and it has exactly one rule:

> **The limit conceded is the vendor's, never the customer's.**

Naming a weakness in the customer's evidence base, product, methodology, or clinical validity is
not candour — it is undermining the person whose sign-off the document needs, in a document they
are about to be asked to approve. Name the boundary of *your own* layer instead: what it checks
and what it cannot check, what it makes expensive rather than impossible.

Where an adjacent standard or product partly closes the gap, say which part. Precision here reads
as confidence; vagueness reads as hedging.

### 11. How it works
A compact numbered flow, 4–7 steps, for the technical reader. Optionally one small diagram. Enough
that an engineer can tell it's real; not so much that it becomes the solution design.

### 12. Why they chose <vendor>
The alternatives genuinely considered and the deciding factor. Naming a real alternative is what
makes this credible — a section that names no alternative reads as marketing and should be cut
rather than padded.

### 13. What's next
The expansion path, in the customer's words where possible. Proves the deployment stuck. For
design-stage, this is the V1 → V2 cut from the solution design.

### 14. Applicability panel
**"This applies to you if…"** — exactly three bullets, each a condition a reader can test against
their own situation (not a benefit restated). This is what converts the document from a marketing
artifact into a selling one. Derive the conditions from the shape the story matches in the profile's
proof library, not from the customer's industry alone.

### 15. The ask — survives `--onepager`
One short closing section with a concrete next step, written in the register of the rest of the
document. Not "contact us to learn more": name the thing the reader would actually say back. A
document that ends on the applicability panel is a document the reader agrees with and puts down.

Restate the narrowed claim in one line first — the version that survived the falsification pass —
then the ask. This is the only place the vendor may be the grammatical subject.

### 16. Evidence & sources
The verification log. One row per claim: **claim · number · basis · scope · date · source**.

**Scope is a column, not a footnote.** A statistic's population and jurisdiction are part of the
claim. A US survey quoted in a Singapore story, a global benchmark quoted as local data, a
vendor-run poll quoted as independent research — each is a defect the basis tag alone does not
catch, because all three are legitimately `measured`. Two figures from the same publisher are not
necessarily from the same instrument; check before presenting them as one body of evidence.

Close the log with three short blocks:

- **Claims deliberately excluded** — what was available and not used, and why. Unsourced deck
  figures, market sizing, unconfirmable co-design claims, competitor comparisons.
- **A claim withdrawn on review** — anything an earlier draft asserted that research contradicted.
  State it plainly. It is the cheapest credibility in the document, and it stops a later editor
  reinstating the claim as an apparent oversight.
- **The tier statement**, in one sentence — e.g. *"Outcomes are modeled from the current-state
  baseline captured in discovery on <date>; the solution has not yet been deployed."*

Same shape as the `## Sources & verification log` sections in the knowledge packs.

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
| A premise with no falsification attempt | Sourced rows around an uncited assertion | Name the incumbent mechanism; narrow the claim to what survives |
| An unnamed alternative ("existing approaches") | Placeholder where research should be | Name the standard, spec, product, or practice |
| A why-now with one force | Reads thin; no window is established | Three to five independent, separately sourced forces |
| A challenge section that narrates current state | Describes the vendor's world, not the buyer's | Restate as what the buyer's organisation cannot do, in their numbers |
| The opening written from the end user's chair | Moves someone with no budget | Write from the reader resolved in Step 1 |
| A conceded limit that belongs to the customer | Undermines the approver | Concede the vendor's own layer's boundary |
| A borrowed statistic with no scope | Global or foreign data reads as local | Scope column in the log; in prose if load-bearing |
| A figure carried from a search summary | Secondary sources drop qualifiers and invent precision | Confirm at the primary source or drop it |
| No closing ask | Reader agrees and puts it down | One concrete next step in the document's own register |
| A seat in the buying committee with no authority named | That seat has no reason to believe you know their job | Cite one framework or body that seat already tracks |
| A trust/safety/compliance argument with no security or risk source | Reads as a vendor's clever observation | Find what the risk seat's own frameworks call it |
| Cited guidance compressed into endorsement | "Government-recommended" is a claim nobody made | State the recommendation as a fact about the guidance; label voluntary frameworks voluntary |

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
| The limit | 30 |
| How it works (4 steps) | 36 |
| What's next | 22 |
| Applicability (3 bullets) | 27 |
| The ask | 15 |

**The full section list does not fit the page cap, and this is expected.** Sixteen sections against
~330 prose words is a genuine conflict, not a drafting failure — a document carrying real buyer
evidence, a named incumbent mechanism, and a convergence why-now will always exceed two pages if
every section renders. The Word spec is therefore a *selection*, decided in the spec, not
improvised at render time.

**Drop order — decide it before the first render, follow it in order:**

| # | Dropped | Why it yields first |
|---|---|---|
| 1 | `[QUOTE PENDING]` placeholder | Says nothing to a reader; lives in the `.md` and approval pack |
| 2 | Evidence table → a compact sources callout | Naming principal sources and pointing at the full log in the `.md` preserves the contract at a fraction of the room |
| 3 | A callout whose argument can fold into the adjacent paragraph | Removes the block's chrome and keeps every word of the argument — the cheapest real cut available |
| 4 | A **heading block**, folding its lead-in into the paragraph | Measured at **~200 characters of overflow per heading** — heading space-before/after is worth about three lines of body text. Costs the section its scannability, nothing else |
| 5 | A claim the abridgement states more than once | The `.md` can repeat an authority across sections; two pages cannot. Keep the placement that does the most work |
| 6 | Why they chose <vendor> | Overlaps "what they tried first" in a short document |
| 7 | What's next | The least load-bearing depth section |
| 8 | Applicability panel | Costs the document its selling function — drop only under protest |
| 9 | The limit | Costs the document its credibility — drop last |

Never cut the tier badge, the basis tags, or the closing ask to make room. If the drop order runs
out before the render clears, the document needs a 3-page variant, not more trimming — say so
rather than shipping a mutilated one.

Headline, dateline, results-strip cells, table headers, and the evidence rows sit outside these
counts — they are chrome and are already accounted for in the measurement.

`--onepager` = the front half only, same 200-word ceiling.

### Findings that came out of the measurement

1. **Never emit a `pagebreak` block.** It renders a fully blank page between the halves — 3 pages
   with it, 2 without, at identical content. The front/back split is a *length budget*, not a forced
   break; let the content flow.
2. **The results strip is one row, three columns** — not a three-row `facts_table`. The single-row
   form buys ~40 more prose words on the front page for the same three metrics.
3. **No separate `sources` block.** The evidence table already carries a "Source & date" column;
   a `sources` block duplicates it and costs room the story needs. (It also takes objects
   `{text, url}` for `external` but plain strings for `internal` — an easy render crash.)
4. **Word-level trimming does not clear an overflow, and can deepen it.** Measured across two
   independent documents: nine consecutive sentence-level trims moved a spill by roughly forty
   characters, and one shortened table cell made the overflow *worse* — the shorter cell changed
   where the table broke across the page boundary. Tables are the worst offenders, because their
   break points move non-monotonically with content length.

   The rule: **over cap → remove a block, from the drop order above.** If two consecutive passes
   have not cleared the spill, you are trimming words; stop and cut a block. Budget one or two
   render passes, not a loop.
5. **Compose the Word spec from the ledger, not from the Markdown.** The `.md` runs ten times the
   Word budget, so "trim the Markdown down" is an open-ended search. The two are separate documents
   built from one ledger — see the Step 6 doc-model note.
