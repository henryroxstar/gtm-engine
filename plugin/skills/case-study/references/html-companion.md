# HTML companion — the sendable, printable version of the story

The `.md` is the source of truth. The `.html` is what actually gets sent, screenshotted, and
printed to PDF, so it carries the design. Emitted next to the `.md` in
`content/<active>/accounts/<account-slug>/`, self-contained, one file.

**Base shell — reuse it, don't re-derive it.** The token set, layout shell, light/dark handling, and
safety pattern are already specified in
`${CLAUDE_PLUGIN_ROOT}/skills/solution-design/references/html-companion.md`. Read that file for the
`:root` custom properties, the `@media (prefers-color-scheme:dark)` block, and the typographic
scale, then apply the deltas below. Two differences from the solution-design companion:

- **No Mermaid.** A case study has no architecture diagrams. Load `marked` and `DOMPurify` only —
  drop the mermaid script and its theme wiring entirely.
- **No sticky TOC.** At 1–2 pages a table of contents is noise. Single reading column, centred.

## Non-negotiable safety pattern

Markdown is rendered with `marked`, passed through **DOMPurify**, and inserted with
`insertAdjacentHTML` — never a raw `innerHTML` sink. This is what clears the repo's security hook on
the headless Write path; a companion written with `innerHTML` will be blocked.

```js
const clean = DOMPurify.sanitize(marked.parse(MD));
document.getElementById('content').insertAdjacentHTML('afterbegin', clean);
```

Before embedding, assert the Markdown contains no `</script>` sequence. Embed the `.md` as a
template literal or a JSON string — do not fetch it at runtime, or the file stops being
self-contained.

> **Headless note.** The `python3 -` heredoc recipe in the solution-design companion is an
> **operator-only** convenience for a local Terminal — it is denied to the headless brain as
> arbitrary code execution. Headless, author the `.html` directly with the Write tool, embedding the
> `.md` into the template.

## Brand binding

Override `--accent` and `--accent-soft` from the active profile's brand kit — `palette.primary`
for light-mode chrome, `palette.accent` for dark. Resolve with
`uv run python -m gtm_core.brandkit --profile <active> [--product <slug>] --key palette.primary`.
PROFILE.md declares no palette. If the profile
ships no palette, keep the neutral defaults rather than inventing colours. Never gradient text,
never AI-purple, one radius scale.

## Component layer — authored as plain inline HTML in the `.md`

Each block is written as block-level HTML inside the Markdown so it degrades to readable text in a
plain viewer and needs no extra JS (DOMPurify keeps structural tags plus `class`).

### Tier badge — `.tier-badge`
Sits in the hero, next to the dateline. Colour-coded and **always visible**: `deployed` takes the
`--ok` token, `pilot` the accent, `design` the `--warn` token. The design-stage badge reads
`Solution story — modeled outcomes`, never just a colour with no words. This badge is the reader's
only defence against mistaking a modeled figure for a measured one; it does not get styled away.

### Results strip — `.results`
Three cells, `grid-template-columns:repeat(auto-fit,minmax(9rem,1fr))`. Per cell: the number
(`720` weight, `clamp(1.6rem,…,2.1rem)`), a ≤6-word label in the mono utility font, and the basis
tag as a small pill. Design-stage: the strip's heading is **"Modeled outcomes"**, and every pill
carries the `modeled` styling. Exactly three cells — a fourth breaks the scan.

### Basis pills — `.basis`
`measured` → `--ok-soft`; `customer-reported` → `--surface-3`; `modeled` → `--warn-soft`;
`(~unverified~)` → `--warn-soft` with an outline. Small, mono, uppercase. They appear inline beside
figures in the body too, not only in the strip.

### Before / after — `.ba`
Two columns, the "before" recessed (muted text, `--surface-2`), the "after" foregrounded. Genuinely
different content per side — not a mirrored list. Collapses to stacked at `<40rem`.

### Pull quote — `.pq`
Left accent rule, larger type, attribution line in mono. A `[QUOTE PENDING …]` placeholder renders
in `--warn` so it is impossible to ship by accident.

### Applicability panel — `.applies`
A bordered card with the heading "This applies to you if…" and exactly three list items. Placed
last before the evidence log — the final thing a reader sees before the sources.

### Evidence log — `.evidence`
A compact table, `--faint` borders, mono column headers. Wrapped in `overflow-x:auto` so it never
forces the page to scroll sideways on mobile.

## Print stylesheet

A case study gets printed and PDF'd far more than a solution design, so `@media print` is a
first-class requirement, not an afterthought:

- Force the light token set; remove shadows and background fills that waste toner.
- `.results`, `.ba`, `.applies`, `.pq` get `break-inside:avoid`.
- `page-break-before:always` on the "How it works" heading so page 1 stays intact as the standalone
  skimmer's version.
- Expand link URLs after their text (`a[href^="http"]::after{content:" (" attr(href) ")"}`) inside
  the evidence log only — not in body prose, where it shreds the paragraphs.
- `@page{margin:14mm}`.

## Accessibility

Contrast ≥ 4.5:1 in both schemes — if a brand accent fails on the surface token, darken the *text*
and keep the raw accent for decorative rules only (the same rule the Word renderer applies in
`readable_on()`). Basis tags must not rely on colour alone: the word is always present. One
restrained, `prefers-reduced-motion`-gated reveal, or none.
