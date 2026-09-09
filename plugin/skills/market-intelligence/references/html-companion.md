# HTML companion (renders the VoC brief as a premium, data-room page)

The brief is written twice: the **`.md` is the source of truth** for every quote, number, and speaker
tag; the **`.html` is a self-contained, theme-aware page** authored next to it (same basename,
`.html`, in `content/<active>/plans/market-intelligence/`). It is **static** — hand-authored HTML + CSS
with **no `<script>` and no external fetch** — so it opens anywhere offline and passes the repo's
headless security hook trivially.

**Art direction — the shared "trust-ledger / data-room" system (same as `campaign-plan`).** Commit to
it; do **not** soften back toward a generic rounded-card / serif / drop-shadow look — that reads as
machine-generated. This companion is the demand-intelligence sibling of the campaign exec plan and
uses the identical register:

- **A document-cover masthead** — a mono **dossier meta strip** (`Prepared for · Document · Date ·
  Classification`) between a 2px rule and a hairline, then an **oversized** display headline
  (`clamp(35px … 68px)`, tight `-.037em`), the dek, a **reading-key legend** naming the eight
  speakers, and a closing 2px rule. It reads like a report cover, not a web hero.
- **A fixed section-index rail** (`.rail`, `position:fixed`, ≥1440px, hidden in print) — mono
  `00 · 01 … 11 · A/B` anchored to the `id`'d sections.
- **De-card everything.** Blocks are **flat regions separated by hairline rules** (a 1px top-rule,
  transparent background, **no box-shadow**, sharp ~3px corners). The **exec read** is the one framed
  element (a 3px accent top rule + full `--ink-2` border).
- **Prospectus tables** (`.tblwrap` + `table.tbl`) — 2px rule top & bottom, a mono uppercase header row
  on a 1px ink underline, hairline rows, hover tint; no rounded wrapper, no shadow, no fill.
- **All system-sans, no serif** (a serif reading body is the single most-tested AI-editorial tell), a
  restrained **mono** utility layer for all labels/figures/meta, **one radius scale** (3px / 2px), and
  **zero box-shadow**. Dials: `VARIANCE 5 / MOTION 1 / DENSITY 6` — a serious, near-static document;
  polish comes from typography, whitespace, and hierarchy, never decoration or motion.
- **The eight speakers stay visually distinct** — the reading-key legend + a **speaker chip**
  (`.spk.customer` / `.spk.std` / `.spk.vendor` / `.spk.bd` / `.spk.expert` / `.spk.own` /
  `.spk.regulator`, plus `.spk.mixed`) on every source/claim. Never a "synthetic/fake" label for
  the expert lens — they are real named practitioners' frameworks synthesized by us.
  - `.spk.customer` (**customer-voice**, inherits the brand accent) — what the market says &amp; does;
    **the only speaker counted as demand**.
  - `.spk.std` (**standards-voice**, blue) — where the category is being *defined*. A leading
    indicator that runs ahead of demand; **never** demand itself.
  - `.spk.vendor` (**vendor-voice**, rose) — a rival's revealed roadmap. Not a customer request.
  - `.spk.own` (**own-voice**, sage) — what we shipped. The capability baseline, **not demand**.
  - `.spk.regulator` (**regulator-voice**, rust) — an enforcement action or applicability date. A
    forcing function, not demand.
  - Add `.spk.nb` alongside any non-customer chip in an evidence context: it appends a mono
    "◦ not demand" marker.
  - Hues are distinct in **both** themes (verified: 7 distinct values light and dark). `--cust`
    inherits `--accent`; `--bd`, `--exp`, `--std`, `--vnd`, `--own`, `--regulator` are fixed so the
    speakers never collapse to one hue. When you set the brand accent, override **only** `--accent*`
    — leave the other six.

**Banned (the AI-tells to design out):** serif body, side-stripe accent borders, hero-metric cliché,
gradients, decorative emoji, mixed radii, `border` + heavy `box-shadow` on one element, an uppercase
eyebrow above every heading, motion used as polish.

## Canonical style source — copy the template file, then set the brand accent

**Don't hand-assemble the CSS. Copy [`references/market-intel-template.html`](market-intel-template.html) verbatim** — a
fully de-branded, self-contained file that embeds this design system end-to-end (complete token block +
every component's CSS) with a **neutral placeholder accent** and `{{PLACEHOLDER}}` content for the
masthead, exec read, seven sections, and appendices A–F. Then:

1. **Set the accent** from the active profile's brand — resolve `profiles/<active>/knowledge/brand/`
   (its `BRAND-ASSETS-README.md` "Web / document accent tokens") and override **only**
   `--accent / --accent-ink / --accent-soft` in **all four** token blocks (light `:root`, dark
   `@media`, and both `:root[data-theme=…]`). The default is a neutral slate — replace it. The
   customer-voice chip inherits `--accent`; `--bd` (neutral) and `--exp` (violet) stay fixed so the
   the speaker chips never collapse to one hue.
2. **Replace every `{{PLACEHOLDER}}`** with this run's content from the `.md` (the source of truth).
3. Delete section blocks you don't need; keep it self-contained (no CDN, no network fonts).

The template file is authoritative for the **CSS system** (tokens, components, themes). For
**section structure**, the table below is authoritative — the template's placeholder sections
predate the 2026-07-30 reader restructure, so lay out the sections per the table (the 2026-07-29
worked instance under `content/<active>/plans/market-intelligence/` shows the shape) while taking
every component's markup and style from the template file.

## Component markup contracts — check the CSS before you use a component

The stylesheet styles **children**, not just containers. A component written without the child its
rule targets renders empty or unstyled while every class name is spelled correctly — so it passes a
class-existence check and fails in the browser. Gate tier **T13** derives these contracts from the
sheet automatically, but knowing them up front is cheaper than a failing gate.

| Component | Required markup | What goes wrong without it |
|---|---|---|
| **`.dseg`** (bar) | `<span class="dseg"><i style="background:var(--cust); width:52%"></i></span>` — `.dseg` is the **track**; the inner `<i>` is the fill | Width on the track itself paints nothing. The 2026-08-18 brief shipped a five-row chart of **empty outlines** |
| **`.dbar`** (bar row) | exactly **two** grid children: the label+track cell, then `<span class="dv">` for the number | A third child wraps to a new grid row |
| **`.gloss`** | `<dl class="gloss"><div><dt>term</dt><dd>definition</dd></div></dl>` | `<b>`/`<span>` never receive the `.gloss dt` / `.gloss dd` typography |
| **`.clist`** | a list container: `<ul><li>…</li></ul>` inside it | Prose `<p>` renders a size larger than its `<ul><li>` sibling in the same row. **For prose, use a plain `<div>` — not `.clist`** |
| **`.dqh`** (quadrant head) | `<div class="dqh"><i></i><h3>Validated</h3></div>` | No colour dot and no heading typography — shipped that way on 2026-07-29 |
| **`.key`** (legend) | `<span class="key k-c"><i></i><b>Label</b> · description</span>` | The `k-*` modifier colours `.key i`; with no `<i>` there is no dot |

**The colour modifiers are scoped.** `.val` / `.bet` / `.pull` set `--dc`, which is read only under
`.dq` and `.dqh i`. Putting `.val` on a `.dseg` does nothing — `.dseg i` has **no background rule at
all**, so its fill colour must be set inline per segment.

**And the numbers a chart draws must match the numbers it prints.** Bar widths are authored by hand,
so nothing stops a 52%-wide bar sitting next to the label "202" while the row below it is 44% wide
and labelled "172". Compute the widths from the values as a percentage of the largest, and re-read
the rendered row before shipping.

## Finish the head, not just the body

You are copying a template. **Replacing `{{PLACEHOLDER}}` inside the body leaves three things
untouched, and all three are reader-visible** (gate tier **T12** fails on each):

1. **`<title>`** — ships as `{{Voice of the Customer}} · {{MONTH YEAR}}` and appears in every browser
   tab and bookmark. Set it to `<brand> · Market Intelligence · <Month YYYY>`.
2. **The leading comment** — the template's own 22-line build instructions ("Copy this file,
   then…"). Replace with a one-line provenance note naming the issue date and the paired `.md`.
3. **The accent comment** — `/* PLACEHOLDER ACCENT (neutral slate-blue) */` sits directly above the
   colour you just replaced, so the one comment a future author would trust is a lie. Rewrite it to
   name the brand colour you actually set.

## Reader-surface structure (restructured 2026-07-30 — 12 sections + 5 appendices)

The `.md` record keeps its full section inventory; the HTML **reorders and consolidates it for a
reader who has never opened the repo**. The mapping is not 1:1 by design:

- The `.md`'s §3b (what we shipped) compresses to a **three-line strip inside HTML §03** — readers
  know what shipped; they don't know the outside-in view.
- The `.md`'s §4 (BD focus) renders as a **subsection of HTML §05 "Are we aligned?"** — our bets are
  context for the comparison, not a headline section.
- The `.md`'s §6 + §7 merge into **HTML §06 "What to validate"** (validation table + the three
  demand-vs-capability hypotheses).
- The `.md`'s §§9–11 merge into **HTML §08 "What this means for each team"** — rebuilt **by
  function**: a Marketing block (lead message, A/B candidates, timing, retire list — absorbs
  "message of the moment"), a Sales block (segments with deadlines, budget-trigger accounts, buyer
  language, watch-outs — absorbs "channel & partner"), and a Product block (validate-don't-build,
  say-do gaps, competitive briefs, standards lead time). Then a compact **Watch** and
  **Deliberately ignore** list. `.act` cards with `Why / Do / Careful` labelled lines.
- The `.md`'s §8 source key, §1 caveat block, and App. F provenance all live in **HTML Appendix A
  "Sources & how to read them"** — reference material leaves the reading flow.
- **HTML Appendix E is the operator appendix** (`id="operator-notes"`,
  `data-audience="operator"`): decisions waiting on a human, follow-ups, fact-checks of internal
  material, method corrections, coverage gaps. Its masthead says who it is for and that it is safe
  to skip. **This is the only place operator mechanics may appear on the reader surface.**

Every `<section>` carries **`data-audience="all|marketing|sales|product|operator"`**; operator
sections come after every reader-facing one. Enforced by the readability gate (T8).

| HTML section | Block | Notes |
|---|---|---|
| 00 Since last issue | `.delta` strip — New / Escalated / Decayed / Resolved / Still ignored | baseline notice on first run |
| Masthead | `.mast` (`.dossier` + `.kicker` + `h1` + `.dek`) + `.keys` reading-key | the report cover; the reading-key names every speaker |
| 01 Who we heard from | `.comp` grid of `.dblock`s with `.dseg` bars + `.rank` lists | anti-bias panel; **one closing pointer line** to the Appendix-A caveats — the five-item caveat block itself lives in Appendix A, never ahead of the findings (T9) |
| 02 What buyers say | `.tblwrap`/`table.tbl` (saying / meaning / sources / conf / `.ref` evidence link) | **no BD framing**; rows link to Appendix B |
| 03 What people say about us | `.note.crit` headline (the outside-in verdict) + a what-is-out-there table + a 3-line own-voice shipped strip + a coverage-limits fine line | mentions ≠ demand; silence ≠ no market. Sources: the practitioner-archive brand check, the vendor maps checked (linked), wire syndication of our own releases, independent echoes |
| 04b Competitor moves | competitor grid: vendor · dated event · verdict · source; "no movement" rows | `vendor-voice`; registry proposals go to the operator appendix |
| 04c Regulatory clock | table: jurisdiction · date · obligation-or-penalty · armed cohort | `regulator-voice`; dates only where primary text was read |
| 04d Proof points | takeaway-first callouts (see below) + the failure-modes table | the quotable third-party layer; highest citation bar in the brief |
| 04e Account moves | table: date · account · event · what changed · what it does NOT tell us | timing, never demand; operator notes about the check itself go to Appendix E |
| 05 Are we aligned? | `.divg` three-column split + **"Where our motion is pointed" subsection** (`.cols2` `.clist`, labelled "our bets, not demand") | the centrepiece; compare, never merge |
| 06 What to validate | `.note.warn` banner + validation table + **"Demand vs. what we already ship" hypotheses** (`.oc` blocks with `.gate` + `.ceil`) | discovery-first; a distance map, not a roadmap |
| 07 Standards watch | `.cols2` Adopted vs Proposed + absorption-risk callout | `standards-voice`, never demand |
| 08 What this means for each team | `h3.blk` Marketing / Sales / Product blocks of `.act` cards, then Watch + Deliberately ignore | **the by-function shape is enforced (T9)**; recommendations judged, not measured |
| App. A Sources & how to read them | collector table + the source key (source · speaker chip · contents) + confidence rule + **"Before quoting any number externally"** caveats + **"Where our data comes from"** provenance table | all reference material, one place |
| App. B Evidence | `.ev` flat-ruled blocks, each with an `id` | the "prove don't say" layer |
| App. C SEC search | what the instrument returned; read ratio | |
| App. D Glossary | `.gloss` ruled definition grid | plain-language term defs |
| App. E Operator notes | `data-audience="operator"` — decisions waiting, follow-ups, fact-checks, method corrections, coverage gaps | **"safe to skip"** in the subtitle; the one section exempt from the reader-register lint tiers |

## Takeaway-first callouts (every reader-facing `.note` over ~60 words)

**Open with the takeaway in bold — meaning before method.** The reviewed failure was callouts that
led with "65 tasks, 824 criteria, SOPs of 20–124 pages" and left the reader asking *why should I
care?* The shape that works:

1. **Bold first sentence: the claim in reader terms.** "Agents cannot yet reliably follow company
   rules — and there is now a published number for it."
2. One or two sentences of evidence, with the link.
3. **"Why it matters to us:"** one sentence connecting it to what we sell.
4. **"How to quote it / Careful:"** the phrasing rule or the limit that travels with the number.

Method detail (task counts, page ranges, criteria totals) goes last, labelled "Method, for when a
buyer asks" — or into the `.md` record only. Enforced by the readability gate (T9).

## Evidence-gated stat strip

The §7 "market, by the numbers" stat strip (`.ekpis`) is **evidence-gated**: every card carries:
- the number,
- what it measures,
- the **evidence id** that grounds it (e.g. `B-71`), and
- a `.vchip`/`verified` chip when the primary source was read.

A number without a verified primary source does **not** get a card — render it as a plain table row
in the coverage note instead. This is the visual expression of the evidence-store rule: a search hit
is not a stat.

- **Static only.** No `<script>`, no `<link>`/`@import`, no remote fonts/images, no `fetch`. Renders
  identically offline. Ship the print rules (hide the topbar + rail; avoid mid-section page breaks).
- **Theme-aware both ways.** Keep the `@media (prefers-color-scheme)` dark block **and** the
  `:root[data-theme=…]` overrides so an explicit toggle wins.
- **The `.md` owns the content.** Never let a quote/number/opportunity appear in the `.html` that isn't
  in the `.md`. On refresh, re-author from the updated `.md`.
- **Both speakers stay distinct; the expert lens is real people synthesized.** The reading-key,
  speaker chips, and divergence matrix are not optional — they are the visual expression of the brief's
  one rule.
- **No slop, enforced by the system:** all-sans, flat (no shadows), sharp 3px corners, prospectus
  tables, callouts as tinted panels with a mono label — never a side stripe.
