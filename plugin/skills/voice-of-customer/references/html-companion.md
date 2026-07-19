# HTML companion (renders the VoC brief as a premium, data-room page)

The brief is written twice: the **`.md` is the source of truth** for every quote, number, and speaker
tag; the **`.html` is a self-contained, theme-aware page** authored next to it (same basename,
`.html`, in `content/<active>/plans/voice-of-customer/`). It is **static** — hand-authored HTML + CSS
with **no `<script>` and no external fetch** — so it opens anywhere offline and passes the repo's
headless security hook trivially.

**Art direction — the shared "trust-ledger / data-room" system (same as `campaign-plan`).** Commit to
it; do **not** soften back toward a generic rounded-card / serif / drop-shadow look — that reads as
machine-generated. This companion is the demand-intelligence sibling of the campaign exec plan and
uses the identical register:

- **A document-cover masthead** — a mono **dossier meta strip** (`Prepared for · Document · Date ·
  Classification`) between a 2px rule and a hairline, then an **oversized** display headline
  (`clamp(35px … 68px)`, tight `-.037em`), the dek, a **reading-key legend** naming the three
  speakers, and a closing 2px rule. It reads like a report cover, not a web hero.
- **A fixed section-index rail** (`.rail`, `position:fixed`, ≥1440px, hidden in print) — mono
  `01 … 07 · A/B` anchored to the `id`'d sections.
- **De-card everything.** Blocks are **flat regions separated by hairline rules** (a 1px top-rule,
  transparent background, **no box-shadow**, sharp ~3px corners). The **exec read** is the one framed
  element (a 3px accent top rule + full `--ink-2` border).
- **Prospectus tables** (`.tblwrap` + `table.tbl`) — 2px rule top & bottom, a mono uppercase header row
  on a 1px ink underline, hairline rows, hover tint; no rounded wrapper, no shadow, no fill.
- **All system-sans, no serif** (a serif reading body is the single most-tested AI-editorial tell), a
  restrained **mono** utility layer for all labels/figures/meta, **one radius scale** (3px / 2px), and
  **zero box-shadow**. Dials: `VARIANCE 5 / MOTION 1 / DENSITY 6` — a serious, near-static document;
  polish comes from typography, whitespace, and hierarchy, never decoration or motion.
- **The three speakers stay visually distinct** — the reading-key legend + a **speaker chip**
  (`.spk.customer` / `.spk.bd` / `.spk.expert`, plus `.spk.mixed`) on every source/claim. Never a
  "synthetic/fake" label for the expert lens — they are real named practitioners' frameworks
  synthesized by us.

**Banned (the AI-tells to design out):** serif body, side-stripe accent borders, hero-metric cliché,
gradients, decorative emoji, mixed radii, `border` + heavy `box-shadow` on one element, an uppercase
eyebrow above every heading, motion used as polish.

## Canonical style source — copy the template file, then set the brand accent

**Don't hand-assemble the CSS. Copy [`references/voc-template.html`](voc-template.html) verbatim** — a
fully de-branded, self-contained file that embeds this design system end-to-end (complete token block +
every component's CSS) with a **neutral placeholder accent** and `{{PLACEHOLDER}}` content for the
masthead, exec read, seven sections, and appendices A–E. Then:

1. **Set the accent** from the active profile's brand — resolve `profiles/<active>/knowledge/brand/`
   (its `BRAND-ASSETS-README.md` "Web / document accent tokens") and override **only**
   `--accent / --accent-ink / --accent-soft` in **all four** token blocks (light `:root`, dark
   `@media`, and both `:root[data-theme=…]`). The default is a neutral slate — replace it. The
   customer-voice chip inherits `--accent`; `--bd` (neutral) and `--exp` (violet) stay fixed so the
   three speakers never collapse to one hue.
2. **Replace every `{{PLACEHOLDER}}`** with this run's content from the `.md` (the source of truth).
3. Delete section blocks you don't need; keep it self-contained (no CDN, no network fonts).

Worked instances under `content/<active>/plans/voice-of-customer/` are examples, not the source — the
template file is authoritative.

## Component vocabulary (map each `.md` section → a block in the template)

| Brief section | Block | Notes |
|---|---|---|
| Masthead | `.mast` (`.dossier` + `.kicker` + `h1` + `.dek`) + `.keys` reading-key | the report cover; the reading-key names the three speakers |
| Executive read | `.exec` (the one framed block) → `.ekpis` hairline KPI grid + `.eglance` (strongest demand / divergence / discovery-not-engineering) + `.enot` caution | coverage KPIs (sources / external-vs-ours / mentions / accounts), not a sales funnel |
| 1 Who this is from / skews | `.comp` grid of `.dblock`s with `.dseg` bars + `.rank` lists, then a `.note.crit` "Skews" | the anti-bias panel; state the skews plainly |
| 2 Read the sources | `.tblwrap`/`table.tbl` (source · **speaker chip** · corpus) + a `.note.teal` confidence rule | "three external / three ours / one expert-synthesis" |
| 3 Customer voice | `.tblwrap`/`table.tbl` (saying / meaning / **breadth** / conf / **`.ref` evidence link**) | **no BD framing**; rows link to Appendix B |
| 4 BD focus | `.cols2` of `.clist` (where BD works / the hooks) | labelled "our bet, not demand" |
| 5 Alignment & divergence | `.divg` → three `.dq.val/.bet/.pull` flat columns with a coloured header dot | the centrepiece; compare §3 and §4, never merge |
| 6 Signals to validate | `.note.warn` banner + `.tblwrap`/`table.tbl` (signal / seen & how thin / **what would confirm** / don't-do-yet) | replaces any "close deals" framing; discovery-first |
| 7 Demand vs capability | `.comp` grid of `.oc` blocks: **evidence** (`.ref`) + `.state ship/impl/absent` + **`.gate` "before investing"** + `.ceil` ceiling | distance map, not a roadmap; ground in PRODUCT.md |
| 8 The market conversation | `.comp` of `.dblock`s with **`.sv` share-of-voice bars** (`.svrow.gap` = the whitespace) + `.sigs`/`.sig` dated signal cards (`.stag` reg/comp/std/cat/inc) + a sourced `.ekpis` stat strip | **market-level customer voice** — what the whole market discusses + the events moving it; the whitespace behind §03's top demand. Source every stat; flag single-source. |
| 9 Pain → Claim → Gain | `.tblwrap`/`table.tbl` — persona × **Pain (customer-voice)** → Claim (capability implied) → Gain → lens | the bridge from what customers say to what they'd need; **Pain traces to §03/evidence**, Claim/Gain is the mapping (not demand) |
| App. A Manifest | `.tblwrap`/`table.tbl` from the collector | the un-fakeable coverage |
| App. B Evidence | `.ev` flat-ruled blocks (`.evq` quote + `.meta` + `.brd` breadth), each with an `id` | the "prove don't say" layer |
| App. C Corpus + lenses | `.cols2` `.clist` + `.note.crit` verdict + `.tblwrap` expert-lens roster (real named sources) | name the 8 lenses |
| App. D Glossary | `.gloss` ruled definition grid | plain-language term defs |
| App. E Method | `.note` block | speaker + confidence rubric; expert-lens + no-CRM notes |

## Authoring rules

- **Static only.** No `<script>`, no `<link>`/`@import`, no remote fonts/images, no `fetch`. Renders
  identically offline. Ship the print rules (hide the topbar + rail; avoid mid-section page breaks).
- **Theme-aware both ways.** Keep the `@media (prefers-color-scheme)` dark block **and** the
  `:root[data-theme=…]` overrides so an explicit toggle wins.
- **The `.md` owns the content.** Never let a quote/number/opportunity appear in the `.html` that isn't
  in the `.md`. On refresh, re-author from the updated `.md`.
- **Two speakers stay distinct; the expert lens is real people synthesized.** The reading-key,
  speaker chips, and divergence matrix are not optional — they are the visual expression of the brief's
  one rule.
- **No slop, enforced by the system:** all-sans, flat (no shadows), sharp 3px corners, prospectus
  tables, callouts as tinted panels with a mono label — never a side stripe.
