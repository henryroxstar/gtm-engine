# HTML companion — the reviewer-facing twin of a video script

The `.md` script is the **source of truth** for every fact. The `.html` is a self-contained,
theme-aware page authored next to it (same basename), for the human who has to approve the film
before anyone spends a credit on it. Same content, different reader: the `.md` is written for the
pipeline, the `.html` is written for a reviewer with three minutes.

Template: [`brief-template.html`](brief-template.html). Copy it, fill the `{{PLACEHOLDER}}` slots,
delete nothing structural.

## Why it exists

A 600-line markdown script is not reviewable at the speed a review actually happens. The failure it
prevents is specific and has happened: the beats get read, the *argument* does not, and the things
that were never written down at all — who this is for, what it must not say, how anyone would know
it worked — get re-litigated after the render, when changing them costs money.

The page inverts the markdown. One screen of idea, then the shape, then the detail. The prompt and
the front block go last.

## Hard rules

- **Static only.** No `<script>`, no `<link>`, no `@import`, no remote fonts, images or `fetch`.
  The page must render correctly from a `file://` URL with no network. System font stack only.
- **Theme-aware both ways.** Keep the `@media (prefers-color-scheme: dark)` block **and** the
  `:root[data-theme="dark"]` overrides, so an explicit toggle wins in either direction. Every colour
  is a token declared in the bare `:root` first; a colour whose only definition sits inside a media
  block renders one theme's text on the other theme's ground.
- **Do not dress the page in the product's brand.** This is a document *about* the film, not an
  asset *of* it. The ground is a neutral conform sheet; the tenant's colour appears as `--accent`
  and in the palette swatches, as subject matter. A brief wearing the brand reads as a mock-up of
  the deliverable and gets reviewed as one.
- **No generated imagery.** This skill spends no credits and calls no generation tool, so the
  storyboard cells are typographic: timecode, role, the visual line, and the caption rendered in its
  true lower-third position inside a 9:16 frame. That last detail is the point of the cell — it
  shows the reading load and whether the band will land on a face, which is a real review question
  a prose beat sheet cannot answer. Reference frames arrive later, at `video-storyboard`.

## Filling the slots

Most placeholders are one-for-one with a section of the `.md`. Three need construction:

**`{{ACT_SEGMENTS}}` / `{{BEAT_SEGMENTS}}` / `{{TRIGGER_SEGMENTS}}`** — the timeline is three flex
rows whose children are sized by duration, so the ribbon is proportional to real time. Emit one
child per act / beat / beat, each carrying `style="flex:<seconds> 0 0"`:

```html
<div class="tl-act" style="flex:6 0 0; background:var(--act-1)">Hook</div>
<div class="tl-b" style="flex:3 0 0; border-top-color:var(--act-1)">
  <span class="id">B1</span><span class="tc">3.00s</span></div>
<div class="tl-t" style="flex:3 0 0">T5</div>
```

Use `--act-1` … `--act-5` in running order; a beat's top border takes its act's colour so the two
rows read as one object. A beat with no trigger takes `class="tl-t none"` and a `·`.

**`{{STORYBOARD_CELLS}}`** — one `.frame` per beat, `screen` or `card` modifier where it applies:

```html
<div><div class="frame">
  <div class="fr-top"><span class="fr-tc">0.00</span><span class="fr-role">B-ROLL</span></div>
  <div class="fr-vis">What is in shot, one or two lines.</div>
  <div class="fr-band"><div class="fr-cap">The caption, verbatim.</div>
    <div class="fr-meta"><span>B1 · 3.00s</span><span class="trig">T5</span></div></div>
</div></div>
```

A beat with no caption uses `<div class="fr-cap none">no caption — the picture runs</div>`.

**`{{CAPTION_ROWS}}`** — one row per beat, in running order. Put `class="num"` on the numeric cells
so the digits align, and `class="num over"` on any w/s above 4.0. An over-budget row must be visible
as a colour, not buried in a paragraph: making the arithmetic legible per beat is what catches the
caption that clears the aggregate and breaks its own screen.

## What the page must not become

Not a deck, not a pitch, and not a place for anything the `.md` does not already say. If a fact
appears here and not in the script, the script is wrong — fix it there and regenerate. Two documents
that disagree is worse than one document nobody reads.
