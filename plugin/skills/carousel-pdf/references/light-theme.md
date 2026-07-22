# Light Theme — Reference

How to produce a white-background carousel instead of the default dark-cinematic style. Light carousels suit educational content, thought-leadership frameworks, and audiences who prefer a cleaner look.

---

## What "light theme" means

| Element | Dark (default) | Light |
|---|---|---|
| Background | Near-black `#020617` | Off-white `#F8FAFC` |
| Body text | White | Navy `#0F172A` |
| Ambient gradients | Visible, saturated | Soft watercolour wash |
| Brand accents | Full saturation | Full saturation (unchanged) |
| Gradient text (CTA, stat) | Same (looks great on both) | Same |
| Footer bar | Dark glass | Light glass |

The light theme does **not** change the layout, typography, grid, or component structure — only the colour tokens and scrim values.

---

## One-time setup (5 minutes, done once per Slidev deck workspace install)

Light mode requires two small edits to the Slidev deck workspace:

### Edit 1 — `tokens.css` (add ~20 lines)

Open `.engine/deck-theme/styles/tokens.css` and add this block at the end of the file:

```css
/* ── Light theme override (carousel-pdf Phase 2) ───────────────────────────
   Applied when slides.md sets colorSchema: light.
   Slidev adds class .light to <html> — these overrides activate then.
   ─────────────────────────────────────────────────────────────────────────── */
html.light {
  --surface-background:     var(--neutral-50);    /* #F8FAFC */
  --surface-background-alt: var(--neutral-100);   /* #F1F5F9 */
  --surface-primary:        rgba(0, 0, 0, 0.03);
  --surface-secondary:      rgba(0, 0, 0, 0.05);
  --surface-tertiary:       rgba(0, 0, 0, 0.08);
  --surface-nav:            rgba(248, 250, 252, 0.92);
  --text-primary:           var(--neutral-900);   /* #0F172A */
  --text-secondary:         rgba(15, 23, 42, 0.80);
  --text-tertiary:          rgba(15, 23, 42, 0.60);
  --text-disabled:          rgba(15, 23, 42, 0.38);
  --scrim-bg:               rgba(241, 245, 249, 0.80);
  --glass-surface:          rgba(0, 0, 0, 0.04);
  --glass-border:           rgba(0, 0, 0, 0.08);
  --footer-border:          rgba(15, 23, 42, 0.10);
}
```

### Edit 2 — Replace hardcoded dark values in four layout files

The carousel Vue layouts have hardcoded dark `rgba(2, 6, 23, ...)` scrim values in their `<style scoped>` blocks. Replace each one with the `--scrim-bg` variable. Open each file and make the substitution:

**`.engine/deck-theme/layouts/carousel-hook.vue`** — find and replace:
```css
/* FIND (2 occurrences): */
background: rgba(2, 6, 23, 0.60);

/* REPLACE WITH: */
background: var(--scrim-bg, rgba(2, 6, 23, 0.60));
```
Also find:
```css
/* FIND: */
border-top: 1px solid rgba(255, 255, 255, 0.08);

/* REPLACE WITH: */
border-top: 1px solid var(--footer-border, rgba(255, 255, 255, 0.08));
```

**`.engine/deck-theme/layouts/carousel-point.vue`** — find and replace:
```css
/* FIND: */
background: rgba(2, 6, 23, 0.55);

/* REPLACE WITH: */
background: var(--scrim-bg, rgba(2, 6, 23, 0.55));
```
```css
/* FIND (2 occurrences — chip-tag and card-num): */
background: rgba(255, 255, 255, 0.06);
border: 1px solid rgba(255, 255, 255, 0.12);

/* REPLACE WITH: */
background: var(--glass-surface, rgba(255, 255, 255, 0.06));
border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.12));
```
Also the footer border:
```css
/* FIND: */
border-top: 1px solid rgba(255, 255, 255, 0.08);

/* REPLACE WITH: */
border-top: 1px solid var(--footer-border, rgba(255, 255, 255, 0.08));
```

**`.engine/deck-theme/layouts/carousel-quote.vue`** — find and replace:
```css
/* FIND: */
background: rgba(2, 6, 23, 0.50);

/* REPLACE WITH: */
background: var(--scrim-bg, rgba(2, 6, 23, 0.50));
```
```css
/* FIND (glass-quote card): */
background: rgba(255, 255, 255, 0.04);
border: 1px solid rgba(255, 255, 255, 0.08);

/* REPLACE WITH: */
background: var(--glass-surface, rgba(255, 255, 255, 0.04));
border: 1px solid var(--glass-border, rgba(255, 255, 255, 0.08));
```
Also the footer border (same as above pattern).

**`.engine/deck-theme/layouts/carousel-cta.vue`** — find and replace:
```css
/* FIND: */
background: rgba(2, 6, 23, 0.45);

/* REPLACE WITH: */
background: var(--scrim-bg, rgba(2, 6, 23, 0.45));
```
Also the footer border.

> **Why `var(--scrim-bg, rgba(...))` with a fallback?** The fallback keeps the dark default working without any other changes. If `--scrim-bg` is not defined (dark mode), the fallback value is used. In light mode, `--scrim-bg` is defined by `html.light {}` and the light value takes effect. Dark carousels are completely unaffected.

---

## Verifying the setup

Run `npm run dev -w "@<deck-scope>/<deck-slug>"` and add `colorSchema: light` to its global frontmatter temporarily. If the background turns white and text turns dark — setup is complete. Revert `<deck-slug>` before proceeding.

---

## Using the light theme in a carousel

In the carousel's `slides.md` global frontmatter, change one line:

```yaml
# Dark (default):
colorSchema: dark

# Light:
colorSchema: light
```

Everything else — layouts, components, copy — stays identical. The same arc templates in `sample-carousels.md` work for both themes.

---

## When to use light vs dark

| Use dark | Use light |
|---|---|
| Product launches, cinematic hooks | Educational how-tos, frameworks |
| Technical audiences (engineers, CTOs) | Business audiences (ops, finance, legal) |
| Evening / event posts | Morning / weekday thought-leadership |
| Carousels with Higgsfield visuals | Carousels with data / structured lists |
| First impression / hook-led posts | Re-engagement / value-led posts |

The choice is aesthetic and audience-driven. Both themes produce on-brand output.

---

## Dark mode is always the default

If the colleague does not specify a theme, dark is used. The light theme is explicitly opt-in via `colorSchema: light`. This ensures the existing sample carousel (`ai-agent-identity`) and all previously built carousels are unaffected.
