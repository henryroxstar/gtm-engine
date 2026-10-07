# HTML companion — the sendable, printable version of the story

The `.md` is the source of truth. The `.html` is what actually gets sent, screenshotted, and
printed to PDF, so it carries the design. Emitted next to the `.md` in
`content/<active>/accounts/<account-slug>/`, self-contained, one file — and it **opens with no
network access**. That is what this file is for: it is emailed to a customer's comms and legal
contacts, who open it on whatever network, or none, they have, and a page that comes up blank there
is a page nobody read.

**Base shell — reuse it, don't re-derive it.** The token set, layout shell and light/dark handling
are already specified in
`${CLAUDE_PLUGIN_ROOT}/skills/solution-design/references/html-companion.md`. Read that file for the
`:root` custom properties, the `@media (prefers-color-scheme:dark)` block, the typographic scale and
the two `design_render` markers, then apply the deltas below. The render path is **not** a
difference: this page is rendered by `gtm_core.design_render`, the same code and the same allowlist
as the solution-design page, so there is one sanitizer and one render path to trust. Two
differences:

- **No Mermaid.** A case study has no architecture diagrams — cut the page script's `// mermaid`
  block (the lazy `import()` of the Mermaid host and its theme wiring), from that comment down to
  the `// motion-gated reveal` comment. It is the only thing in the shell that names a remote host,
  so without it the page references nothing outside the file.
- **No sticky TOC.** At 1–2 pages a table of contents is noise. Single reading column, centred: cut
  the `<aside class="side">`, the script's `// numbered table of contents` block (down to the
  `// coverage table` comment) **and the `@media (min-width:1040px)` rule that makes `.app` two
  columns**. Left in, the lone `<main>` lands in the 14.5rem rail and the story is squeezed into a
  sliver on a desktop screen.

## Non-negotiable safety pattern

The page carries **rendered HTML, never the markdown**, and renders nothing when it is opened.
`design_render` turns the `.md` into HTML when it writes the page and puts the result between the
shell's two markers, inside `<div id="content">`:

```html
<div id="content"><!-- design_render:start -->
<!-- design_render:end --></div>
```

Everything outside the markers is left exactly as written. Three properties follow, and they are
what makes the page safe to send:

- **The sanitizer runs before the write, and there is one.** The page loads no `marked` and no
  `DOMPurify`, and has no HTML-string sink (`innerHTML`, `insertAdjacentHTML`). The rendered HTML
  passes `design_render`'s allowlist first: tags, attributes and URL schemes outside it are left out
  and named on stderr, and `<script>`, `<style>`, `<iframe>`, inline `<svg>` and event-handler
  attributes never reach the page. The markdown can carry text lifted from untrusted research, so
  this is the control, not a nicety.
- **Nothing is fetched to open it.** The shell has no `<script src>`, no stylesheet link and no web
  font, and with the Mermaid block cut its script names no remote host at all. A relative
  `.svg`/`.png` is inlined as a `data:` URI, so keep images local: a remote `https://` image is left
  as written and will not load offline. What is left of the script builds only with
  `createElement` / `textContent` / `setAttribute`; that is also what clears the repo's security hook
  on the headless Write path, which blocks a script written with a raw HTML-string sink.
- **The `.md` is never in the page.** It is not embedded and not fetched at runtime, so a
  `</script>` sequence in it cannot break the page and needs no pre-write check.

## How to generate it

1. **Once per file:** write the shell — the solution-design template with the two deltas above, the
   brand binding and the component layer below — to `case-study-<account-slug>-<YYYY-MM-DD>.html`
   next to the `.md`, with `__TITLE__` set to the `.md`'s H1 and every `__BRAND_*__` marker
   substituted. Leave the two `design_render` markers inside `<div id="content">`; the render writes
   between them.
2. **Render** — writes the `.md` as HTML between the markers and inlines any relative `.svg`/`.png`
   as a `data:` URI:

   ```bash
   uv run python -m gtm_core.design_render content/<active>/accounts/<account-slug>/case-study-<account-slug>-<YYYY-MM-DD>.md
   ```

   Read its stderr: anything the allowlist left out is named there.
3. **After every `.md` edit, render again** — the cuts that fit the page cap included. Never
   hand-edit the HTML between the markers: the next render overwrites it, and until then the two
   files disagree.
4. **Before delivery**, confirm nothing drifted — exits 1 if the `.html` no longer matches the `.md`:

   ```bash
   uv run python -m gtm_core.design_render content/<active>/accounts/<account-slug>/case-study-<account-slug>-<YYYY-MM-DD>.md --check
   ```

A page made the earlier way — it loads `marked` and `DOMPurify` from a CDN and embeds the `.md` —
has no markers, and `design_render` refuses it (exit 2). Re-create it once from the shell, then
render.

> **Headless note.** The render is the committed CLI above, which the least-privilege policy
> permits (it is the same footing as the dossier renderer in Step 7). Headless, write the shell
> once with the Write tool — markers in place, no markdown in it — then run the CLI. There is no
> heredoc or `-c` recipe to reach for: arbitrary code execution is denied to the headless brain.
> Never write the rendered HTML into the page yourself and never hand-write a replacement renderer;
> the CLI is where the allowlist runs.

## Brand binding

Substitute the shell's `__BRAND_ACCENT_LIGHT__` and `__BRAND_ACCENT_DARK__` from the active
profile's brand kit — `palette.primary` for light-mode chrome, `palette.accent` for dark — and
`__BRAND_BODY_FACE__` from `typography.body`. Resolve with
`uv run python -m gtm_core.brandkit --profile <active> [--product <slug>] --key palette.primary`.
PROFILE.md declares no palette. If the profile ships no palette, put the shell's own `--text` value
for that scheme in each accent marker rather than inventing colours. No `__BRAND_*__` marker may
survive into the page. Never gradient text, never AI-purple, one radius scale.

## Component layer — authored as plain inline HTML in the `.md`

Each block is written as block-level HTML inside the Markdown so it degrades to readable text in a
plain viewer and needs no extra JS. The allowlist keeps the structural tags and the `class`,
`style`, `role`, `aria-*` and `data-*` attributes (the tag list is `_TAGS` in
`gtm_core/design_render.py`). Anything else is left out and named on stderr, and a tag left out
takes its class with it, its text kept. `<cite>` and `<footer>` are not on the list, so a pull
quote's attribution is a `<p>` with a class, not a `<cite>`.

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
forces the page to scroll sideways on mobile. Write each source as `[label](url)` or `<https://…>`:
a bare URL stays plain text, not a link, so the print rule below that expands link URLs has nothing
to expand.

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
