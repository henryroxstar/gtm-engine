# HTML exec companion (renders the program plan as a premium, boardroom-ready page)

The program plan is written twice: the **`.md` is the source of truth** for every number, cohort, and
hypothesis; the **`.html` is a self-contained, theme-aware exec page** authored next to it (same
basename, `.html`, in `content/<active>/plans/campaigns/`). Unlike the `solution-design` companion,
this page is **static** — hand-authored HTML + CSS with **no `<script>` and no external fetch**. That
means no `marked`/`mermaid`/`DOMPurify` runtime, no innerHTML sink, nothing loaded from a CDN — so it
opens anywhere offline and passes the repo's headless security hook trivially (there is no dynamic DOM
sink to guard). You fill the component vocabulary below with the **live figures from the `.md`**; when
the plan refreshes, you re-author the page from the updated numbers.

**Art direction — commit to a POV, don't default to "clean."** A neutral, rounded-card, subtle-shadow
layout *is* the generic default look and reads as machine-generated. This template commits to a
distinctive **Swiss "trust-ledger" / data-room** register instead — the aesthetic of a confidential
financial prospectus, which also fits the credentials/DID product:
- **A document-cover masthead.** Open with a monospace **dossier meta strip** (`Prepared for ·
  Document · Date · Classification`) between a 2px rule and a hairline, then an **oversized** display
  headline (`clamp(35px … 68px)`, tight `-.037em`), the dek, and a closing 2px rule. It should read
  like a report cover, not a web hero.
- **A fixed section-index rail.** A slim mono `01 … 08` vertical index pinned in the left gutter
  (`position:fixed`, shown only ≥1440px, hidden in print) — anchored to `id`'d sections. This is the
  signature "designed document" device.
- **De-card everything.** Do **not** wrap each block in a rounded, shadowed card — that is the tell.
  Use **flat regions separated by hairline rules** (a 1px top-rule per block, transparent background,
  no shadow, sharp `~2–3px` corners). Reserve a single framed treatment (a 3px accent **top** rule +
  full hairline border) for the one centerpiece (the exec summary).
- **Ruled "prospectus" tables** — heavy 2px rule top and bottom, a mono uppercase header row on a
  1px ink underline, hairline rows; no rounded wrapper, no shadow, no fill.
- **Big confident type scale + a deliberate mono utility layer.** Section headers are large
  (`clamp(24px … 36px)`) with a big mono accent numeral hanging in the margin; all labels, figures,
  meta, party-chains, and table headers live in mono — the data-room signal.
- **Sharp, square data marks.** Bars have square ends (radius 0), chips are `2px`, one radius scale
  throughout. No pills in a sharp system.

**Design read:** an internal exec brief for a leadership team — *editorial / premium-docs* language
(think Stripe or Linear long-form). **All system-sans, no serif** — a well-set system sans
(`-apple-system, BlinkMacSystemFont, "Segoe UI", …`) reads intentional and modern; a serif reading
body is the single most-tested AI-editorial tell, so it is banned here. A restrained **mono layer**
carries only labels/figures/party-chains. One neutral paper base biased to a single locked **brand
accent**; full light **and** dark via `prefers-color-scheme` **and** an explicit `data-theme` override
(the viewer's theme toggle wins in both directions). Dials: `VARIANCE 5 / MOTION 2 / DENSITY 6` — a
serious, data-dense document, near-static; **polish comes from typography, whitespace, and hierarchy,
never from decoration or motion.**

**Non-negotiables — the AI-tells to design *out* (this is what makes it not look generated):**
- **No serif body.** System-sans throughout; emphasise with weight/italic of the same family.
- **No side-stripe accent borders** (`border-left: 3px solid …` on cards/callouts). Callouts are
  full tinted, fully-bordered cards; use a small uppercase label or a status dot, never a colour bar.
- **No hero-metric cliché** (giant accent-coloured number + tiny grey label in a bordered box). KPI
  values sit in the strong ink colour at a restrained size, grouped in **one hairline grid** (cells
  divided by 1px lines), not four separate shadowed boxes.
- **No decorative emoji** in tables, lists, headers, or the tool stack. Keep only genuinely semantic
  glyphs (a `🆕` new-in-role flag, `⇄` party-chain arrows, `★` fit-rating). Everything else goes.
- **No gradients** (fills, text, or bars — solid accent only), **one radius scale** (≈14px cards /
  9px chips / pill for status chips), and **border *or* heavy shadow, never both** (shadows stay
  ≤ a few px blur, tinted to the background).
- **Section numerals are editorial chapter marks** — a large, light-weight, faint numeral beside the
  headline — not tiny mono `01`/`02` scaffolding chips.
- **One eyebrow region max** (the masthead kicker). Data-block sub-labels read as chart captions, kept
  quiet in mono; don't stack an uppercase eyebrow above every heading.
- Ship a **print stylesheet** (hide the sticky bar, drop shadows, avoid mid-section page breaks) — this
  brief gets exported to PDF.

**Canonical style source:** [`references/campaign-template.html`](campaign-template.html) — a fully
de-branded, self-contained file that embeds this design system end-to-end (complete token block +
every component's CSS) with a **neutral placeholder accent** and `{{PLACEHOLDER}}` content for all
eight sections. **Copy that file**, override the accent from the active profile's brand, then replace
each `{{PLACEHOLDER}}` with this run's live figures from the `.md`. The token/component sketch below is
an illustrative summary only — the template file is authoritative, and prior worked instances under
`content/<active>/plans/campaigns/` are examples, not the source.

## Brand accent (override the neutral default)

The token block below ships a neutral teal-ish default. When the active profile documents a brand
palette (`profiles/<active>/knowledge/brand/` — see that folder's `BRAND-ASSETS-README.md` "Web /
document accent tokens"), **override `--accent`, `--accent-ink`, and `--accent-soft` in all three
places** (the light `:root`, the dark `@media` block, and both `:root[data-theme=…]` blocks) with that
profile's accent. Never introduce a second chrome colour or a gradient-text heading; a brand secondary
is at most a single data accent inside one chart.

## The template — copy the file, then fill with live data

**Don't hand-assemble the CSS from a sketch — copy [`references/campaign-template.html`](campaign-template.html)
verbatim.** It carries the complete, current token block and every component's CSS, already committed
to the art direction above. What follows is a *reading guide* to what that file contains, not a source
to retype:

- **Palette tokens** — `--paper / --card / --well / --ink / --ink-2 / --strong / --muted / --faint /
  --hair / --hair-2`, semantic `--good / --warn / --crit` (+ `-soft`), and a geo series `--us / --sg /
  --eu` (recolour or drop if the program's geos differ). Defined four times: light `:root`, dark
  `@media`, and both `:root[data-theme=…]` blocks so the manual toggle wins in either direction.
- **Accent tokens** — `--accent / --accent-ink / --accent-soft` ship a **neutral placeholder** and are
  the *only* colours you override from the profile brand (see the section above). Override in all four
  token blocks; never add a second chrome colour or a gradient heading.
- **Type + shape tokens** — `--sans` (system stack) and `--mono` only; **no `--serif` — the body is
  system-sans**. `--r:3px / --r-sm:2px` (sharp), `--rule:2px solid var(--strong)` (prospectus rules),
  and `--shadow:none / --shadow-lg:none` (flat — shadows are banned in this direction).
- **Components** — `.topbar`, `.mast`/`.dossier`/`.kicker` (document cover), `.exec`/`.ekpi`/`.eglance`
  (framed summary + KPI tiles), `.shead` (mono numeral + h2 + sub), `.punch`, `.note` (+ `.teal`/`.crit`),
  `table.tbl` in `.tblwrap` (prospectus table), `.sig`, `.dblock`/`.dbar`, `.split2`/`.logo`,
  `.eflow`/`.estep`/`.io`/`.ecol`, `.guardstrip`/`.gchip`, `.clist`, `.funnel`/`.frow`, `.rail`
  (fixed section index, ≥1440px), `footer`, plus the responsive/reduced-motion/print blocks.

Prior worked instances under `content/<active>/plans/campaigns/` show the same system filled with real
data — use them as examples of *how much* to say per block, never as the style source.

## Component vocabulary (map each `.md` section → a block)

| Plan section | Block | Notes |
|---|---|---|
| Masthead | `.mast` (kicker + h1 + dek) | one line who it's for; the h1 is the positioning |
| Executive summary | `.exec` → `.ekpis` (4 KPI tiles) + `.eglance` (the bet / how we de-risk / why now) + `.enot` guardrail line | KPI values in `--accent-ink`; **north-star SQLs is one tile** |
| Why now | `.sigs` (signal chips, `.stag` tag = comp/reg/pay/tam) + `.note crit` for single-source "teeth" | keep single-source flags visible in the chip date line |
| Pipeline snapshot | `.ekpis` (live counts) + `.dblock`/`.dbar` density + geo bars + heat callout + a `table.tbl` "reading → shapes the program" | every count from `latest.json`; mark indicative bars "indicative, N≈…" |
| The wedge | `table.tbl` with `.clustrow` group rows, `.chain` party chains, `.pcg` pain/claim/gain, `.stars` fit | named accounts only if already in pipeline |
| Engine | `.eflow` (5 steps) + `.io` (inputs/outputs) + `.guardstrip` + rubric `table.tbl` | link mechanics to prospect refs; don't over-detail |
| The plan | universe `table.tbl` + calendar `table.tbl` + hypotheses `table.tbl` | hypotheses table carries the "how we measure" column |
| The numbers | `.funnel`/`.frow` (prospects → SQLs → pilots) + scenario `table.tbl` | last funnel row = SQLs (north-star), full-opacity bar |
| Guardrails | `.cols2` two `.clist` (guardrails / decision gates) with status `.tag` | |
| Tools & budget | stack `table.tbl` + `.ekpis` economics + benchmark `table.tbl` | figures from `costs.jsonl`; label external benchmarks |

## Authoring rules

- **Static only.** No `<script>`, no `<link>`/`@import`, no remote fonts or images, no `fetch`. Inline
  all CSS. The page must render identically offline. (This is why it needs no DOMPurify — there is no
  runtime DOM injection to sanitize.)
- **Theme-aware both ways.** Ship the `@media (prefers-color-scheme)` dark block **and** the
  `:root[data-theme="light"|"dark"]` overrides so an explicit toggle wins.
- **The `.md` owns the numbers.** Never let a figure appear in the `.html` that isn't in the `.md`.
  On refresh, re-author the page from the updated `.md` — don't hand-patch stale numbers.
- **No slop.** One neutral base + one locked accent; callouts are tinted, fully-bordered cards (a
  left accent bar is fine, never a rainbow); numbered markers only on the genuinely-sequential engine
  flow; tabular-nums on every figure; responsive down to one column at ≤860px.
- Write the file with the Write tool. Keep the masthead/footer labeled **internal** and note that
  conversion rates are hypotheses, not forecasts.
