# Visual Components — partnership brief

Three infographic components for the HTML companion, additive to the house template in
[`../../solution-design/references/html-companion.md`](../../solution-design/references/html-companion.md).
They use only tokens that template already defines (`--accent`, `--accent-soft`, `--accent-line`,
`--on-accent`, `--ok`, `--ok-soft`, `--warn`, `--warn-soft`, `--surface`, `--surface-2`,
`--surface-3`, `--border`, `--strong`, `--muted`, `--faint`, `--text`, `--mono`, `--measure`), so
they inherit light/dark, the brand accent, and the print stylesheet for free.

## Why CSS and not generated images

Raster infographics break dark mode, make text unselectable and unsearchable, print badly, cannot be
corrected without a re-render, and risk text errors baked inside the image. These components adapt
to the reader's theme, stay accessible, and are editable in seconds.

Generated imagery is acceptable **only** as a non-informational hero — and only where the profile's
brand notes permit imagery at all. Many forbid decoration outright; check before spending on one.

## Wiring into the template

Add the CSS below to the template's `<style>` block, then extend two existing lines:

**Print — add the components to the `break-inside:avoid` list:**

```css
table,pre,.mermaid,details,blockquote,.exec,.outcome,.controls,.phases,figure.shot,.ladder li,.quad,.cover
```

**Reveal — add them to the observed selector list** so they animate with everything else:

```js
:scope > .ladder,:scope > .quad,:scope > .cover
```

Because the markdown is rendered through DOMPurify, hand-authored HTML blocks in the `.md` survive
sanitisation as long as they use plain elements and `class` attributes — which these do. Author them
directly in the Markdown source; the `.md` stays readable because each component degrades to a
sensible list or table when read as plain text.

---

## 1 — Positioning quadrant (§7 Competitive landscape)

A 2×2 that lands the whitespace: the category competes on one axis, and one cell is unclaimed. The
`.win` cell is the seam. Put our own product in the `.us` chip so the reader can see the claim being
made.

```css
.quad{margin:1.5rem 0;max-width:var(--measure)}
.quad-grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
.quad-grid .q{padding:.9rem 1rem;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;min-height:7.5rem}
.quad-grid .q.win{background:var(--accent-soft);border-color:var(--accent);border-width:1.5px}
.quad-grid .q h5{margin:0 0 .5rem;font:600 .66rem/1.3 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.quad-grid .q.win h5{color:var(--accent)}
.quad-grid .q p{margin:0;font-size:.83rem;line-height:1.45;color:var(--muted)}
.vend{display:inline-block;font:600 .7rem/1 var(--mono);padding:.3em .55em;margin:0 .28em .32em 0;
 border-radius:6px;background:var(--surface-3);color:var(--text);border:1px solid var(--border)}
.vend.us{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
.ax-y{font:600 .66rem/1 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin:0 0 .45rem}
.ax-x{font:600 .66rem/1 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin:.5rem 0 0;text-align:right}
```

```html
<div class="quad">
 <p class="ax-y">↑ &lt;vertical axis — e.g. depth of capability&gt;</p>
 <div class="quad-grid">
  <div class="q"><h5>&lt;quadrant label&gt;</h5><p><span class="vend">Vendor A</span><span class="vend">Vendor B</span></p><p>&lt;what this cell optimises for&gt;</p></div>
  <div class="q win"><h5>&lt;the unclaimed cell&gt;</h5><p><span class="vend us">Us</span><span class="vend">Partner</span></p><p>&lt;why nobody owns this yet&gt;</p></div>
  <div class="q"><h5>&lt;quadrant label&gt;</h5><p><span class="vend">Vendor C</span></p><p>&lt;…&gt;</p></div>
  <div class="q"><h5>&lt;quadrant label&gt;</h5><p>&lt;…&gt;</p></div>
 </div>
 <p class="ax-x">&lt;horizontal axis — e.g. breadth of adoption&gt; →</p>
</div>
```

**Rules.** Exactly one `.win` cell. Never place a competitor in a cell you cannot justify from a
public source — the quadrant is the most-screenshotted element in the document and the most likely
to be forwarded to the vendor you placed.

---

## 2 — Capability ladder (§4 The missing primitive)

Rungs of increasing capability, indented to show progression, with the top rung marked as the one
their design names but cannot reach. This is the visual form of the self-named gap, and it is the
most important component in the brief.

```css
.ladder{display:flex;flex-direction:column;gap:.5rem;margin:1.4rem 0;max-width:var(--measure);list-style:none;padding:0}
.ladder li{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem .8rem;margin:0;padding:.85rem 1.1rem;
 background:var(--surface);border:1px solid var(--border);border-radius:12px;position:relative}
.ladder li b{font-weight:640;color:var(--strong);font:600 .88rem/1.2 var(--mono)}
.ladder li span{color:var(--muted);font-size:.87rem;flex:1 1 12rem}
.ladder .t2{margin-left:1.1rem}
.ladder .t3{margin-left:2.2rem;border-style:dashed;border-color:var(--accent-line);background:var(--accent-soft)}
.ladder .t3 b{color:var(--accent)}
.ladder i{font:600 .66rem/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;font-style:normal;
 padding:.3em .55em;border-radius:6px;background:var(--warn-soft);color:var(--warn);white-space:nowrap}
.ladder .t3.filled{border-style:solid;border-color:var(--accent)}
.ladder .t3.filled i{background:var(--ok-soft);color:var(--ok)}
```

```html
<ul class="ladder">
 <li><b>&lt;their rung 1&gt;</b><span>&lt;what it guarantees&gt;</span><i>they ship this</i></li>
 <li class="t2"><b>&lt;their rung 2&gt;</b><span>&lt;what it guarantees&gt;</span><i>they ship this</i></li>
 <li class="t3"><b>&lt;the named rung&gt;</b><span>&lt;the capability their design names&gt;</span><i>needs an external issuer</i></li>
</ul>
```

Add `filled` to the top rung (`class="t3 filled"`) in a second, adjacent ladder to show the
after-state — the dashed border becomes solid and the tag flips from warning to confirmed. Two
ladders side by side, before and after, communicate the whole partnership in one glance.

**Rules.** Use *their* names for the rungs, exactly as their code or docs spell them. The credibility
of the component comes entirely from being their vocabulary, not ours.

---

## 3 — Framework coverage strip (§9 Standards alignment)

One row per framework element, two columns of coverage: theirs and ours. The `none`/`none` rows are
the argument — those are the elements neither party satisfies alone.

```css
.cover{list-style:none;margin:1.4rem 0;padding:0;max-width:var(--measure);display:flex;flex-direction:column;gap:.4rem}
.cover li{display:grid;grid-template-columns:minmax(9rem,1.4fr) 1fr 1fr;gap:.45rem;align-items:center;margin:0}
.cover li.head{font:600 .64rem/1.3 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--faint)}
.cover b{font-weight:620;color:var(--strong);font-size:.86rem;line-height:1.3}
.cover .c{font:600 .68rem/1 var(--mono);padding:.5em .55em;border-radius:7px;text-align:center;border:1px solid transparent}
.cover .c.full{background:var(--ok-soft);color:var(--ok);border-color:rgba(31,138,91,.28)}
.cover .c.part{background:var(--warn-soft);color:var(--warn);border-color:rgba(169,112,26,.3)}
.cover .c.none{background:var(--surface-2);color:var(--faint);border-color:var(--border)}
```

```html
<ul class="cover">
 <li class="head"><span>&lt;framework&gt; element</span><span>Partner</span><span>Us</span></li>
 <li><b>&lt;element 1&gt;</b><span class="c full">covered</span><span class="c none">—</span></li>
 <li><b>&lt;element 2&gt;</b><span class="c part">partial</span><span class="c full">covered</span></li>
 <li><b>&lt;element 3&gt;</b><span class="c none">—</span><span class="c full">covered</span></li>
</ul>
```

**Rules.** Show at least one element where **we** are the `none` column — a coverage strip in which
our side is uniformly green is not analysis, and a technical reader will discount the whole section.
Label elements with the framework's exact published identifiers, verified against its current text.

---

## Component budget

**Three components, maximum, per brief — one per section named above.** These earn their place
because each carries an argument that prose makes slowly. A fourth is decoration, and decoration in
a technical partnership document reads as compensation for a thin thesis.
