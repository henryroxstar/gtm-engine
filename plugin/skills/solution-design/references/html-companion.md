# HTML companion (renders the doc as a premium, readable page + draws its components and diagrams)

Markdown viewers show the design's inline HTML components as raw tags and ```mermaid``` blocks as
literal code. So `solution-design` always emits a **self-contained HTML companion** next to each
`.md` — `marked.js` renders the doc, a small vanilla script upgrades the components, and
`mermaid.js` draws any remaining Mermaid block. **The `.md` is the single source**: the companion is
created once from the template below and thereafter only re-rendered from the `.md` by
`gtm_core.design_render`. Marked's output is passed through **DOMPurify** and inserted with
`insertAdjacentHTML` (a safe DOM method), so the page passes the repo's security hook on the headless
Write path.

**Design read:** a customer-facing solution-overview document for business, compliance and
technical buyers — *editorial / premium-docs* language (think Stripe or Linear docs). Refined
**system sans** for the hero + body with a **mono utility layer** (kicker, labels, table headers,
citations); one neutral base biased toward a single locked accent, plus **owner colours** that mean
an owner and nothing else (no AI-purple, no gradient text, no side-stripe callouts, one radius
scale). A sticky, numbered scroll-spy "On this page" table of contents; a designed hero +
executive-summary panel; a **visual-component layer** the doc authors with plain inline HTML; full
light **and** dark; a print stylesheet; motion only where it reveals an order.

---

## Rule 0 — budgets

| Budget | Target | Why |
|---|---|---|
| **One visual per section** | every section carries one diagram, component or table — and only one (a short Further reading is exempt) | a text desert loses the reader; a second visual in one section is two sections |
| **Customer overview ≤ ~2,000 words** | the customer-facing read is a handful of screens | the appendix is a *separate file*, not a longer scroll (Rule 1) |

**Audit any doc you did not just write** before adding to it:

```bash
# words per section — find the prose blocks that should have become components
awk '/^#{1,3} /{if(h!=""){printf "%6d  %s\n",w,h} h=$0;w=0;next}{w+=NF}
     END{if(h!="")printf "%6d  %s\n",w,h}' solution-design-<company>-<date>.md
# any section over ~300 words is a component that was written as prose.
```

## Rule 1 — the appendix is a separate file, and internal notes are a third

One document, one audience. Emit **three files**, never one scroll that tries to serve all of them:

| File | Audience | Contains |
|---|---|---|
| `solution-design-<company>-<date>.md` | the customer's decision-makers (and their regulator) | Exec summary + §1–9, with the sibling pointer |
| `solution-design-<company>-<date>-appendix.md` | the customer's architects | A1…An — every caveat the overview leaves out |
| `solution-design-<company>-<date>-internal.md` | **us only — never sent** | the "omit from customer copy" material |

The customer overview's H1 is `# <Account> × <Vendor> — Solution Overview`, and directly under its
meta line it declares its appendix in an invisible comment (the linter reads it; readers never see
it):

```
<!-- appendix: solution-design-<company>-<date>-appendix.md -->
```

Each file gets its own HTML companion the same way. Two hard rules:

- **A section whose own heading says "omit from customer copy" must not be in the customer file.** If
  you write that phrase, you are writing the internal file — put it there.
- **Never ship a version log to a customer.** Solution designs revise **silently**. A changelog
  narrating v2.1 → v2.5 to a reader who never saw v1 is pure scroll cost. Version history lives in git.

## Rule 2 — the executive summary is the most over-written section in every draft

It is read by the one person least willing to read. **Hard cap: 150 words after the headline.**

| Belongs in the Exec summary | Belongs somewhere else |
|---|---|
| The headline — one line naming the problem solved (`.o-after`) | A before→after where either side is not a real figure |
| The subline — how, and the governing framework if any (`.o-sub`) | Product names, protocols, acronyms beyond one → §1 Key terms, §4 |
| Optionally the trust strip — the checks every request passes | Any second visual |
| 3–4 bullets: the problem · the solution · the constraint · who it's for | Caveats, betas, assumptions → the appendix |

**The test:** every bullet is one or two sentences and survives being read aloud in a breath. A
bullet over ~40 words is a paragraph wearing a bullet.

```bash
awk '/^## Executive summary/{f=1;next} /^#/{f=0} f' solution-design-<company>-<date>.md | wc -w
```

---

## Brand binding (required — do this before the first render)

**This template ships neutral, de-branded defaults. They are nobody's brand and must not ship.**
Resolve the active profile's kit and substitute the real values into both the light `:root` block and
the dark `@media` block:

```bash
P=<active-profile>   # optionally: S=<product-slug>, add --product "$S" to each call
uv run python -m gtm_core.brandkit --profile "$P" --key palette.primary   # → light chrome accent
uv run python -m gtm_core.brandkit --profile "$P" --key palette.accent    # → dark  chrome accent
uv run python -m gtm_core.brandkit --profile "$P" --key typography.body   # → body face
```

Replace the four `__BRAND_*__` markers in the template below. **Never** copy a colour sideways from a
website, a deck, a previous companion, or another skill — `BRAND.toml` is the single source, and a
hex that appears in none of its keys is drift by definition.

**The resolved accent must clear 4.5:1 against the ground it sits on** — it carries links, the active
TOC item and component labels, so it is body-weight text, not decoration. A **monochrome or
dark-first brand** (primary == accent, both near-white) is legible on its own near-black canvas and
invisible as light-mode chrome. Where the declared value fails, darken or lighten **that same hue**
along its lightness axis until it clears, and say so in the render output — never swap in a
different colour, and never ship the failing one. Verify after substitution:

```bash
grep -o '#[0-9a-fA-F]\{6\}' solution-design-<company>-<date>.html | sort -u   # every hex…
uv run python -m gtm_core.brandkit --profile "$P" --key palette              # …must appear here,
# except the template's declared neutrals (greys, near-black, near-white).
```

**Owner colours (`--c-1` … `--c-4`, `--c-out`).** One colour per product suite or party, used
identically in the diagrams, `.suite-key`, `.who`, `.lanes` and `table.cov`. They default to
neutral tokens; bind each to the first stop of that suite's kit gradient (or the account's diagram
palette), in the order `.suite-key` lists them. A party with no kit colour keeps a neutral token.
`--c-out` marks the one path the record does not cover.

```bash
uv run python -m gtm_core.brandkit --profile "$P" --key gradients.<suite>.stops   # → ["#…","#…"]
```

Drive the same stops into Mermaid with `classDef` in the `.md` itself:

````
classDef suiteA stroke:<suite-A first stop>,stroke-width:2.5px
classDef suiteB stroke:<suite-B first stop>,stroke-width:2.5px
class NODE_A1,NODE_A2 suiteA
class NODE_B1,NODE_B2 suiteB
````

---

## Visual-component layer

Each component is written as plain block-level HTML **in the Markdown**, so it degrades to readable
text in a no-CSS viewer. DOMPurify keeps structural tags, `class`, `style` (custom properties such as
`--d`, `--lanes`) and `data-*` attributes. One per section (Rule 0).

| Component | Markup | Use it for | Page |
|---|---|---|---|
| Outcome headline | `.outcome` + `.o-after` / `.o-sub` | the Exec summary headline and subline. One per doc. | customer |
| Trust strip | `.trust-strip[data-anim]` | one request, the checks it passes in order, then the answer. Exec summary, optional. | customer |
| Requirement groups | `.req-groups` (+ `small.cite`) | §1 — requirements grouped by driver, each with its citation | customer |
| Key terms | `details.gloss` > `.defs` | §1 — the glossary, collapsed | customer |
| Current-state flow | `.cstate` | §2 — today's channel between the parties, what it lacks, the fixed obligation | customer |
| Who does what | `.who` | §3 — one card per party, owner-coloured | customer |
| Check pipeline | `.gate-pipe` | §4 — the product's per-request steps, each with its framework tag | customer |
| Swimlane | `.lanes` | §6 — a request passing between more than two parties | customer |
| FAQ by reader | `.faq` + `.faq-h` + `<details>` | §7 — questions grouped by reader type | customer |
| Coverage table | `table.cov` + `.role` | §8 — what each part does for each requirement | customer |
| Suite key | `.suite-key` | the owner-colour legend, once, beside the first diagram | customer |
| Comparison panel | `.compare` | two paths that differ in assurance | either |
| Definition grid | `.defs` | a glossary | either |
| Step timeline | `.steps` | a numbered walkthrough under a diagram (two-party flows) | either |
| Shipping board | `.board` | the V1 / V2 / not-building cut | appendix (Mode B: customer) |
| Status board | `.statuses` | open questions / dependencies | appendix |
| Capability matrix | `.matrix` + `.tag` | Enforced / Simulated / Design-target coverage | appendix |
| Control strip | `.controls` | a short sequential strip where each step is a label, not a sentence | either |
| Ladder | `.ladder` | an ordered progression where the **rung order is the information** | either |
| Role cards | `.roles` | who gets what, where `.who` is too heavy | either |
| Plate | `figure.plate` | a decorative cover. **Never** explanatory. | either |
| Product screenshot | `figure.shot` | a real product-UI figure | either |

### What a generated image may and may not be

`figure.plate` exists so a long document has visual punctuation. It is **decorative only**:

- **Allowed:** an abstract cover plate or a section opener. Abstract, on the profile's declared
  gradient, carrying no labels, no arrows, no boxes, no text.
- **Never:** architecture, flows, sequences, delegation chains, topologies, or anything a reader
  could mistake for evidence. A generated diagram will look plausible and be wrong.
- **Never:** people. These documents name real institutional roles at regulated buyers; synthetic
  faces read as unserious exactly where credibility is the product.
- **Never:** a rendered metaphor. "Trust perimeter", "ladder", "leash" render literally and badly.
- Every plate carries an `alt` that says it is decorative, and counts as its section's one visual.

---

## How to generate it

1. **Once per file:** write the template below to `solution-design-<company>-<date>.html` next to
   the `.md` (headless: the Write tool), with the four `__BRAND_*__` markers substituted (Brand
   binding) and `__TITLE__` set to the doc's H1. Leave `__MD__` in place.
2. **Render** — embeds the `.md` into the `<script type="text/markdown" id="src">` block and inlines
   every relative `.svg`/`.png` image as a data URI:

   ```bash
   uv run python -m gtm_core.design_render content/<active>/accounts/<slug>/solution-design-<company>-<date>.md
   ```

   Pass `--html <path>` when the companion is not the `.md`'s sibling of the same stem.
3. **After every `.md` edit, render again.** Never hand-edit the markdown embedded in the `.html`:
   the next render overwrites it, and until then the two files disagree.
4. **Before delivery**, confirm nothing drifted — exits non-zero if the `.html` no longer matches:

   ```bash
   uv run python -m gtm_core.design_render content/<active>/accounts/<slug>/solution-design-<company>-<date>.md --check
   ```

Edit the template's CSS or script only to change the page's design — never to change content.

## The template

````html
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<style>
 /* ---- tokens ----
    THE ACCENTS BELOW ARE NEUTRAL PLACEHOLDERS AND MUST BE REPLACED. See "Brand binding":
      uv run python -m gtm_core.brandkit --profile <active> [--product <slug>] --key palette.primary
      uv run python -m gtm_core.brandkit --profile <active> [--product <slug>] --key palette.accent
    palette.primary drives the LIGHT chrome, palette.accent the DARK chrome. The kit is the single
    source; PROFILE.md and BRAND-ASSETS-README.md declare no colours. ---- */
 :root{color-scheme:light dark;
  --accent:__BRAND_ACCENT_LIGHT__; --accent-soft:color-mix(in srgb,var(--accent) 10%,transparent);
  --accent-line:color-mix(in srgb,var(--accent) 34%,transparent); --on-accent:#fff;
  --bg:#fbfbfd; --surface:#fff; --surface-2:#f4f5f8; --surface-3:#eceef3;
  --text:#171b23; --strong:#080a0f; --muted:#5a6170;
  --faint:#676f80;                       /* 4.88:1 on --bg — AA. Do not lighten. */
  --border:#e6e8ee; --border-2:#d6d9e2;
  --ok:#1f8a5b; --ok-soft:rgba(31,138,91,.13); --warn:#a9701a; --warn-soft:rgba(169,112,26,.15);
  --stop:#a33a2a; --stop-soft:rgba(163,58,42,.13);
  --shadow:0 1px 2px rgba(16,20,30,.05),0 12px 32px -16px rgba(16,20,30,.16);
  --measure:44rem;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:__BRAND_BODY_FACE__,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
 @media (prefers-color-scheme:dark){:root{
  --accent:__BRAND_ACCENT_DARK__; --on-accent:#0b0e13;
  --bg:#0c0e13; --surface:#12151d; --surface-2:#181c26; --surface-3:#1f2431;
  --text:#e7eaf1; --strong:#f6f8fc; --muted:#98a1b2;
  --faint:#7c8497;                       /* 5.15:1 on --bg — AA. Do not darken. */
  --border:#222836; --border-2:#2c3341;
  --ok:#5fca9a; --ok-soft:rgba(95,202,154,.16); --warn:#e2ad5f; --warn-soft:rgba(226,173,95,.16);
  --stop:#e88b78; --stop-soft:rgba(232,139,120,.16);
  --shadow:0 1px 2px rgba(0,0,0,.3),0 16px 36px -18px rgba(0,0,0,.66)}}
 *,*::before,*::after{box-sizing:border-box}
 html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
 @media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
 body{margin:0;background:var(--bg);color:var(--text);
  font:16.5px/1.66 var(--sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;font-feature-settings:"kern","liga"}
 /* ---- app shell: sticky TOC + reading column ---- */
 .app{display:grid;grid-template-columns:1fr;max-width:48rem;margin:0 auto;padding:2rem 1.35rem 5rem}
 .side{display:none}
 @media (min-width:1040px){
  .app{grid-template-columns:14.5rem minmax(0,44rem);justify-content:center;gap:4.5rem;max-width:none;padding:3.5rem 2rem 7rem}
  .side{display:block;position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;padding:3.9rem 0 2rem}}
 .skip{position:absolute;left:-9999px;top:0;background:var(--surface);color:var(--text);
  border:1px solid var(--accent);border-radius:8px;padding:.6rem .9rem;z-index:20}
 .skip:focus{left:1rem;top:1rem}
 .toc-h{font:600 .7rem/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 .9rem .85rem}
 .toc ol{list-style:none;margin:0;padding:0;counter-reset:s;display:flex;flex-direction:column;gap:.06rem}
 .toc a{display:flex;gap:.6rem;padding:.36rem .85rem;font-size:.85rem;line-height:1.35;color:var(--muted);
  text-decoration:none;border-left:2px solid transparent;border-radius:0 7px 7px 0;transition:color .15s,background .15s,border-color .15s}
 .toc a::before{content:attr(data-n);min-width:1.4rem;font:500 .7rem/1.5 var(--mono);color:var(--faint);flex:none}
 .toc a:hover{color:var(--text);background:var(--surface-2)}
 .toc a.active{color:var(--accent);border-left-color:var(--accent);background:var(--accent-soft);font-weight:500}
 .toc a.active::before{color:var(--accent)}
 .doc{min-width:0}
 #content>*{max-width:var(--measure)}
 h1,h2,h3,h4{color:var(--strong);line-height:1.2;letter-spacing:-.02em;font-weight:680;text-wrap:balance}
 .hero{max-width:var(--measure);margin:0 0 2.6rem;padding:0 0 1.6rem;border-bottom:1px solid var(--border)}
 .hero h1{font-size:clamp(2.05rem,1.35rem+2.3vw,2.85rem);letter-spacing:-.032em;font-weight:730;margin:0;line-height:1.08}
 .hero p{margin:1rem 0 0;color:var(--muted);font:500 .78rem/1.5 var(--mono);letter-spacing:.02em}
 .hero p em{font-style:normal}
 h1.tier{font:600 .74rem/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);
  margin:3.6rem 0 .2rem;padding-top:2.3rem;border-top:1px solid var(--border)}
 h2{font-size:1.4rem;margin:2.9rem 0 .9rem;padding-bottom:.35rem;border-bottom:1px solid var(--border)}
 h3{font-size:1.12rem;margin:2rem 0 .5rem} h4{font-size:1rem;margin:1.5rem 0 .35rem;color:var(--muted)}
 h1,h2,h3{scroll-margin-top:1.5rem}
 p{margin:0 0 1.05rem} strong{font-weight:640;color:var(--strong)}
 a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline;text-underline-offset:2px}
 a:focus-visible,summary:focus-visible,.skip:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:4px}
 ul,ol{margin:0 0 1.05rem;padding-left:1.35rem} li{margin:.33rem 0} li::marker{color:var(--faint)}
 hr{border:0;height:1px;background:var(--border);margin:2.6rem 0;max-width:var(--measure)}
 /* ---- executive summary panel ---- */
 .exec{max-width:var(--measure);margin:0 0 1.7rem;padding:1.5rem 1.7rem;background:var(--surface-2);
  border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
 .exec h2{border:0;margin:0 0 .7rem;padding:0;font:600 .72rem/1 var(--mono);letter-spacing:.13em;text-transform:uppercase;color:var(--muted)}
 .exec ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:.6rem}
 .exec li{margin:0;padding-left:1.15rem;position:relative;color:var(--text)}
 .exec li::before{content:"";position:absolute;left:0;top:.6em;width:.42rem;height:.42rem;border-radius:2px;background:var(--accent)}
 .exec strong{color:var(--strong)}
 /* ---- outcome band: the before -> after headline (Exec summary). No gradient, no big-number cliche ---- */
 .outcome{display:flex;flex-wrap:wrap;align-items:center;gap:.75rem 1.05rem;max-width:var(--measure);
  margin:0 0 1.7rem;padding:1.1rem 1.4rem;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
 .outcome .o-before{font:600 1.02rem/1.2 var(--sans);color:var(--muted)}
 .outcome .o-arrow{font:600 1.15rem/1 var(--sans);color:var(--accent);flex:none}
 .outcome .o-after{font:720 1.32rem/1.14 var(--sans);letter-spacing:-.02em;color:var(--strong)}
 .outcome .o-note{flex-basis:100%;margin:.1rem 0 0;color:var(--faint);font:500 .76rem/1.45 var(--mono);letter-spacing:.02em}
 /* ---- control strip: sequential per-request controls; numbering is meaningful here (and only here) ---- */
 /* 8rem min so a five-step strip fits on one row at the 44rem measure — a lone
    fifth card wrapping under four is the tell that this floor was raised. */
 .controls{list-style:none;counter-reset:c;display:grid;grid-template-columns:repeat(auto-fit,minmax(8rem,1fr));
  gap:.55rem;margin:1.3rem 0;padding:0;max-width:none}
 .controls li{counter-increment:c;margin:0;padding:.85rem .9rem;background:var(--surface);border:1px solid var(--border);
  border-radius:12px;display:flex;flex-direction:column;gap:.22rem;position:relative;overflow:hidden}
 .controls li::after{content:"";position:absolute;left:0;right:0;top:0;height:2px;background:var(--accent);opacity:.55}
 .controls li::before{content:counter(c,decimal-leading-zero);font:600 .68rem/1 var(--mono);color:var(--accent);letter-spacing:.06em}
 .controls li b{font-weight:640;color:var(--strong);font-size:.9rem;line-height:1.2}
 .controls li span{color:var(--muted);font-size:.81rem;line-height:1.36}
 /* ---- comparison panel: two paths that differ in assurance ---- */
 .compare{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:.75rem;margin:1.35rem 0;max-width:none}
 .compare>section{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:1rem 1.15rem 1.1rem;display:flex;flex-direction:column;gap:.5rem;box-shadow:var(--shadow)}
 .compare>section.is-strong{border-color:color-mix(in srgb,var(--ok) 55%,var(--border))}
 .compare>section.is-weak{border-color:color-mix(in srgb,var(--warn) 55%,var(--border))}
 .compare h4{margin:0;display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap;
  font:700 .74rem/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--strong)}
 .compare p{margin:0;font-size:.87rem;line-height:1.52;color:var(--muted)}
 .compare ul{margin:0;padding-left:1.05rem} .compare li{font-size:.85rem;color:var(--text)}
 /* ---- shipping board: V1 / V2 / not building ---- */
 .board{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem;margin:1.35rem 0;max-width:none}
 .board>section{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:1rem 1.15rem 1.1rem;box-shadow:var(--shadow)}
 .board>section.is-next{background:var(--surface-2);box-shadow:none}
 .board>section.is-out{background:transparent;border-style:dashed;box-shadow:none}
 .board h4{margin:0 0 .6rem;color:var(--strong);font-size:.96rem;font-weight:680;letter-spacing:-.01em;
  display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap}
 .board h4 .when{font:600 .62rem/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);
  padding:.24em .5em;border:1px solid var(--accent-line);border-radius:6px}
 .board .is-next h4 .when{color:var(--muted);border-color:var(--border-2)}
 .board .is-out h4 .when{color:var(--stop);border-color:color-mix(in srgb,var(--stop) 40%,transparent)}
 .board ul{margin:0;padding-left:1.05rem} .board li{margin:.3rem 0;font-size:.86rem;line-height:1.48;color:var(--text)}
 .board .is-out li{color:var(--muted)}
 /* ---- definition grid: a glossary as hairline-divided cells, not a bullet list ---- */
 .defs{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:1px;margin:1.35rem 0;
  padding:0;list-style:none;max-width:none;background:var(--border);border:1px solid var(--border);
  border-radius:12px;overflow:hidden}
 .defs li{margin:0;background:var(--surface);padding:.8rem 1rem .85rem;display:flex;flex-direction:column;gap:.22rem}
 .defs li b{font:700 .84rem/1.35 var(--sans);color:var(--strong);letter-spacing:-.005em}
 .defs li span{font-size:.82rem;line-height:1.5;color:var(--muted)}
 /* ---- steps: a numbered walkthrough as a connected timeline (the post-diagram "what happens") ---- */
 .steps{list-style:none;counter-reset:st;margin:1.35rem 0;padding:0;max-width:var(--measure);
  display:flex;flex-direction:column}
 .steps li{counter-increment:st;margin:0;position:relative;padding:0 0 1.05rem 2.6rem;
  font-size:.9rem;line-height:1.55;color:var(--text)}
 .steps li::before{content:counter(st);position:absolute;left:0;top:-.1rem;width:1.7rem;height:1.7rem;
  border-radius:50%;display:grid;place-items:center;font:700 .72rem/1 var(--mono);color:var(--accent);
  background:var(--accent-soft);border:1px solid var(--accent-line)}
 .steps li::after{content:"";position:absolute;left:.85rem;top:1.8rem;bottom:0;width:1px;background:var(--border-2)}
 .steps li:last-child{padding-bottom:0} .steps li:last-child::after{display:none}
 .steps li b{color:var(--strong);font-weight:640}
 /* ---- ladder: an ordered progression where the RUNG ORDER is the information.
    Each rung carries a filled meter, so the ascent survives any column count and stacking. ---- */
 .ladder{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.7rem;margin:1.5rem 0;
  padding:0;list-style:none;max-width:none}
 .ladder li{margin:0;background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:.9rem 1rem 1rem;display:flex;flex-direction:column;gap:.45rem;box-shadow:var(--shadow)}
 .ladder li .rung{display:flex;align-items:baseline;justify-content:space-between;gap:.6rem}
 .ladder li .rung b{font:700 .88rem/1.25 var(--sans);color:var(--strong)}
 .ladder li .rung em{font-style:normal;font:700 .7rem/1 var(--mono);color:var(--faint)}
 .ladder li p{margin:0;font-size:.82rem;line-height:1.5;color:var(--muted)}
 .ladder li .meter{height:.28rem;border-radius:3px;background:var(--surface-3);overflow:hidden;margin-top:.15rem}
 .ladder li .meter i{display:block;height:100%;border-radius:3px;background:var(--accent)}
 /* ---- role cards: who gets what ---- */
 .roles{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.7rem;margin:1.35rem 0;
  padding:0;list-style:none;max-width:none}
 .roles li{margin:0;background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:.9rem 1rem 1rem;display:flex;flex-direction:column;gap:.35rem}
 .roles li b{font:700 .7rem/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--accent)}
 .roles li span{font-size:.86rem;line-height:1.5;color:var(--text)}
 /* ---- status board: resolved / open / blocked ---- */
 .statuses{display:flex;flex-direction:column;gap:.4rem;margin:1.35rem 0;padding:0;list-style:none;max-width:none}
 .statuses li{margin:0;display:grid;grid-template-columns:auto 1fr;gap:.75rem;align-items:baseline;
  background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.7rem .95rem;
  font-size:.87rem;line-height:1.5;color:var(--text)}
 /* ---- capability matrix + status tags ---- */
 .tbl-scroll{overflow-x:auto;margin:1.35rem 0;max-width:none;
  border:1px solid var(--border);border-radius:12px;background:var(--surface)}
 .tbl-scroll table{margin:0;border:0}
 table{border-collapse:collapse;width:100%;margin:1.35rem 0;font-size:.9rem;font-variant-numeric:tabular-nums}
 th,td{border-bottom:1px solid var(--border);padding:.62rem .85rem;text-align:left;vertical-align:top}
 thead th{background:var(--surface-2);border-bottom:1px solid var(--border-2);color:var(--muted);
  font:600 .68rem/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;position:sticky;top:0}
 tbody tr:last-child td{border-bottom:0} tbody tr:hover td{background:var(--surface-2)}
 .tag{display:inline-flex;align-items:center;gap:.36rem;font:600 .68rem/1 var(--mono);letter-spacing:.04em;
  padding:.32em .55em;border-radius:6px;white-space:nowrap;border:1px solid transparent}
 .tag::before{content:"";width:.4rem;height:.4rem;border-radius:50%;background:currentColor;flex:none}
 .tag.ok{background:var(--ok-soft);color:var(--ok);border-color:color-mix(in srgb,var(--ok) 34%,transparent)}
 .tag.warn{background:var(--warn-soft);color:var(--warn);border-color:color-mix(in srgb,var(--warn) 34%,transparent)}
 .tag.sim{background:var(--surface-3);color:var(--muted);border-color:var(--border-2)}
 .tag.stop{background:var(--stop-soft);color:var(--stop);border-color:color-mix(in srgb,var(--stop) 34%,transparent)}
 .cap-legend{display:flex;flex-wrap:wrap;gap:.6rem;margin:1.3rem 0 .2rem;max-width:none}
 .cap-legend .lg{display:inline-flex;align-items:center;gap:.5rem;font-size:.8rem;color:var(--muted);
  background:var(--surface-2);border:1px solid var(--border);border-radius:9px;padding:.42rem .7rem}
 .cap-legend .lg b{color:var(--strong);font-variant-numeric:tabular-nums}
 /* ---- suite key: legend for per-suite diagram colours ---- */
 .suite-key{display:flex;flex-wrap:wrap;gap:.5rem .9rem;margin:.2rem 0 1.35rem;padding:0;list-style:none;max-width:none}
 .suite-key li{margin:0;display:inline-flex;align-items:center;gap:.45rem;font:500 .78rem/1.4 var(--sans);color:var(--muted)}
 .suite-key li i{width:.75rem;height:.75rem;border-radius:3px;flex:none;border:2px solid currentColor}
 /* ---- callouts (blockquote): tinted card, full border — never a colour side-stripe ---- */
 blockquote{margin:1.35rem 0;padding:.95rem 1.2rem;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;color:var(--text)}
 blockquote>:first-child{margin-top:0} blockquote>:last-child{margin-bottom:0}
 blockquote strong:first-child{color:var(--accent)}
 /* ---- code ---- */
 code{background:var(--surface-2);border:1px solid var(--border);padding:.08em .4em;border-radius:5px;font:.85em var(--mono);color:var(--strong)}
 pre{background:var(--surface-2);border:1px solid var(--border);padding:1rem 1.1rem;border-radius:12px;overflow:auto;margin:1.25rem 0}
 pre code{background:none;border:0;padding:0;font-size:.84em}
 /* ---- figures: decorative plate + real product screenshot ---- */
 figure.plate,figure.shot{margin:1.6rem 0;max-width:none}
 figure.plate img,figure.shot img{display:block;width:100%;height:auto;border-radius:14px}
 figure.shot img{border:1px solid var(--border);box-shadow:var(--shadow)}
 figure.plate img{aspect-ratio:24/7;object-fit:cover;border:1px solid var(--border)}
 figure.plate figcaption,figure.shot figcaption{margin:.6rem 0 0;color:var(--faint);font:500 .78rem/1.5 var(--mono);letter-spacing:.02em}
 /* ---- FAQ: native disclosure with a CSS chevron ---- */
 details{border:1px solid var(--border);border-radius:12px;margin:.55rem 0;background:var(--surface);overflow:hidden;transition:border-color .15s}
 details:hover{border-color:var(--border-2)}
 summary{cursor:pointer;padding:.9rem 1.1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;justify-content:space-between;align-items:center;gap:1rem}
 summary::-webkit-details-marker{display:none}
 summary::after{content:"";flex:none;width:.5em;height:.5em;margin-right:.15em;border-right:2px solid var(--faint);border-bottom:2px solid var(--faint);transform:rotate(-45deg);transition:transform .2s ease}
 details[open] summary::after{transform:rotate(45deg)} details[open] summary{border-bottom:1px solid var(--border)}
 details>:not(summary){padding:.55rem 1.1rem 1.05rem;margin:0;color:var(--muted)}
 /* ---- mermaid: theme-aware card (dark theme in dark mode, neutral in light), tinted to --accent ---- */
 .mermaid{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin:1.5rem 0;text-align:center;overflow-x:auto;box-shadow:var(--shadow);max-width:none}
 .mermaid svg{max-width:100%;height:auto}
 .foot{max-width:var(--measure);margin:3.5rem 0 0;padding-top:1.3rem;border-top:1px solid var(--border);color:var(--faint);font-size:.8rem}
 /* ---- owner colours: one per product suite or party, used identically in .who, .lanes, table.cov,
    .suite-key and the diagrams. NEUTRAL DEFAULTS — bind them from the kit (see "Brand binding").
    --c-out marks the one path the record does not cover. ---- */
 :root{--c-1:var(--accent);--c-2:var(--ok);--c-3:var(--strong);--c-4:var(--muted);--c-out:var(--warn)}
 [data-own="1"]{--c:var(--c-1)} [data-own="2"]{--c:var(--c-2)} [data-own="3"]{--c:var(--c-3)} [data-own="4"]{--c:var(--c-4)}
 /* ---- images scale to the column (exported diagrams are often 1300px+ wide) ---- */
 #content img{max-width:100%;height:auto}
 #content p>img{display:block;border:1px solid var(--border);border-radius:14px;background:#fff}
 .outcome .o-sub{flex-basis:100%;margin:0;color:var(--muted);font:500 .98rem/1.5 var(--sans)}
 /* ---- trust strip (Exec summary): one request, N checks lighting up in order, then the answer ---- */
 .trust-strip{display:grid;grid-template-columns:minmax(6.2rem,.75fr) 3fr minmax(6.2rem,.75fr);gap:.55rem;margin:0 0 1.4rem;max-width:none}
 .ts-end{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.7rem .8rem;display:flex;flex-direction:column;justify-content:flex-end;gap:.2rem}
 .ts-end b,.ts-check em{font:700 .64rem/1.2 var(--mono);letter-spacing:.08em;text-transform:uppercase;font-style:normal}
 .ts-end b{color:var(--strong)} .ts-end span{font-size:.76rem;line-height:1.4;color:var(--muted)}
 .ts-track{position:relative;display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:.45rem;padding-top:1.05rem}
 .ts-track::before{content:"";position:absolute;left:.2rem;right:.2rem;top:.38rem;height:2px;border-radius:2px;background:var(--border-2)}
 .ts-token{position:absolute;top:.05rem;left:0;width:.7rem;height:.7rem;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px var(--accent-soft);opacity:0}
 .ts-check{position:relative;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:.7rem 2rem .7rem .8rem;display:flex;flex-direction:column;gap:.18rem}
 .ts-check em{color:var(--faint)} .ts-check b{font-size:.86rem;line-height:1.25;color:var(--strong)}
 .ts-check span{font-size:.75rem;line-height:1.4;color:var(--muted)}
 .ts-check .ok{position:absolute;top:.6rem;right:.6rem;width:1.05rem;height:1.05rem;border-radius:50%;background:var(--accent);opacity:0;transform:scale(.4)}
 .ts-check .ok::after{content:"";position:absolute;left:.37rem;top:.2rem;width:.26rem;height:.5rem;border:solid var(--on-accent);border-width:0 2px 2px 0;transform:rotate(45deg)}
 .trust-strip.play .ts-token{animation:ts-move 3.2s cubic-bezier(.45,0,.2,1) .2s forwards}
 .trust-strip.play .ts-check{animation:ts-lit .45s ease calc(.7s + var(--d)*.95s) forwards}
 .trust-strip.play .ts-check .ok{animation:ts-pop .4s cubic-bezier(.2,.9,.3,1.4) calc(.75s + var(--d)*.95s) forwards}
 .trust-strip.play .ts-end.is-out{animation:ts-lit .45s ease 3.35s forwards}
 .trust-strip.static .ts-check,.trust-strip.static .ts-end.is-out{border-color:var(--accent-line);background:var(--accent-soft)}
 .trust-strip.static .ts-check .ok{opacity:1;transform:none}
 @keyframes ts-move{0%{left:0;opacity:0}10%{opacity:1}90%{opacity:1}100%{left:calc(100% - .7rem);opacity:0}}
 @keyframes ts-lit{to{border-color:var(--accent-line);background:var(--accent-soft)}}
 @keyframes ts-pop{to{opacity:1;transform:none}}
 @media (max-width:40rem){.trust-strip{grid-template-columns:1fr}.ts-track{grid-auto-flow:row;grid-auto-columns:auto;padding-top:0}.ts-track::before,.ts-token{display:none}}
 /* ---- current state (§2): today's channel, what it lacks, and the fixed obligation ---- */
 .cstate{margin:1.4rem 0;max-width:none;background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:var(--shadow);padding:1.15rem;display:flex;flex-direction:column;gap:1rem}
 .cs-flow{display:grid;grid-template-columns:1fr auto 1.8fr auto 1fr;align-items:center;gap:.5rem}
 .cs-party{background:var(--surface-2);border:1px solid var(--border-2);border-radius:14px;padding:.9rem .95rem;display:flex;flex-direction:column;gap:.25rem;text-align:center}
 .cs-party em,.cs-chan em,.cs-fmt em{font:700 .64rem/1.2 var(--mono);letter-spacing:.09em;text-transform:uppercase;font-style:normal;color:var(--muted)}
 .cs-party b{font-size:1rem;line-height:1.3;color:var(--strong)}
 .cs-arrow{display:flex;flex-direction:column;align-items:center;color:var(--c-out);font:700 1.5rem/1 var(--sans)}
 .cs-arrow .back{opacity:.45;font-size:1.2rem}
 .cs-chan{background:color-mix(in srgb,var(--c-out) 9%,var(--surface));border:2px dashed color-mix(in srgb,var(--c-out) 70%,transparent);border-radius:14px;padding:.9rem 1rem 1rem;display:flex;flex-direction:column;gap:.35rem}
 .cs-chan em{color:color-mix(in srgb,var(--c-out) 55%,var(--strong))}
 .cs-chan b{font-size:1.08rem;line-height:1.3;color:var(--strong)}
 .cs-chan ul{list-style:none;margin:.35rem 0 0;padding:0;display:flex;flex-direction:column;gap:.35rem}
 .cs-chan li{margin:0;display:flex;align-items:center;gap:.5rem;font-size:.9rem;line-height:1.35;color:var(--text)}
 .cs-chan li::before{flex:none;width:1.25rem;height:1.25rem;border-radius:50%;display:grid;place-items:center;font:700 .72rem/1 var(--sans)}
 .cs-chan li.x::before{content:"✕";color:var(--stop);background:var(--stop-soft)}
 .cs-chan li.w::before{content:"!";color:var(--warn);background:var(--warn-soft)}
 .cs-ob{display:grid;grid-template-columns:auto 1fr;gap:1rem;align-items:center;border-top:1px solid var(--border);padding-top:1rem}
 .cs-due{display:flex;align-items:center;gap:.6rem;background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:14px;padding:.6rem .9rem}
 .cs-due b{font:720 1.5rem/1 var(--sans);letter-spacing:-.02em;color:var(--accent)}
 .cs-due span{font:600 .86rem/1.25 var(--sans);color:var(--strong)}
 .cs-fmt{display:flex;flex-direction:column;gap:.45rem}
 .cs-fmt ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:.4rem}
 .cs-fmt li{margin:0;font-size:.84rem;line-height:1.3;color:var(--text);background:var(--surface-2);border:1px solid var(--border);border-radius:999px;padding:.32rem .7rem}
 @media (max-width:40rem){.cs-flow{grid-template-columns:1fr}.cs-arrow{flex-direction:row;gap:.6rem;justify-content:center;transform:rotate(90deg)}.cs-ob{grid-template-columns:1fr}}
 /* ---- requirement groups (§1): requirements grouped by driver, each with its citation ---- */
 .req-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.7rem;margin:1.35rem 0;max-width:none}
 .req-groups>section{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1rem 1rem 1.05rem;box-shadow:var(--shadow);position:relative;overflow:hidden}
 .req-groups>section::after{content:"";position:absolute;left:0;right:0;top:0;height:2px;background:var(--accent);opacity:.55}
 .req-groups h4{margin:0 0 .75rem;font-size:1rem;color:var(--strong);display:flex;flex-direction:column;gap:.2rem}
 .req-groups h4 span{font:700 .62rem/1.2 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--accent)}
 .req-groups ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.6rem}
 .req-groups li{margin:0;display:grid;grid-template-columns:2.3rem 1fr;gap:.05rem .55rem}
 .req-groups li em{grid-row:span 3;font-style:normal;font:700 .64rem/1 var(--mono);color:var(--accent);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:6px;padding:.32rem 0;text-align:center;align-self:start}
 .req-groups li b{font-size:.84rem;line-height:1.3;color:var(--strong)}
 .req-groups li span{font-size:.78rem;line-height:1.45;color:var(--muted)}
 .req-groups li small.cite{grid-column:2;margin-top:.15rem;font:600 .6rem/1.4 var(--mono);letter-spacing:.03em;color:var(--faint)}
 details.gloss{margin:1.2rem 0}
 details.gloss>.defs{padding:0;margin:.9rem 1rem 1rem}
 /* ---- who does what (§3): one card per party, colour-keyed to the diagrams ---- */
 .who{display:grid;grid-template-columns:repeat(auto-fit,minmax(12.5rem,1fr));gap:.7rem;margin:1.35rem 0;max-width:none}
 .who>section{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.05rem 1.05rem 1.1rem;box-shadow:var(--shadow);position:relative;overflow:hidden}
 .who>section::after{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--c,var(--c-4))}
 .who h4{margin:0 0 .75rem;font-size:.98rem;color:var(--strong);display:flex;flex-direction:column;gap:.2rem}
 .who h4 span{font:700 .62rem/1.2 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--c,var(--c-4))}
 .who ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.45rem}
 .who li{margin:0;padding-left:1.1rem;position:relative;font-size:.84rem;line-height:1.45;color:var(--text)}
 .who li::before{content:"";position:absolute;left:0;top:.5em;width:.45rem;height:.45rem;border-radius:2px;background:var(--c,var(--c-4))}
 /* ---- check pipeline (§4): the product's per-request steps, in order, each with its framework tag ---- */
 .gate-pipe{position:relative;display:flex;flex-direction:column;gap:.45rem;margin:1.4rem 0;padding:0;max-width:none}
 .gp-rail{position:absolute;left:1.05rem;top:1.4rem;bottom:1.4rem;width:2px;margin-left:-1px;background:var(--border-2);border-radius:2px}
 .gp-node{position:relative;display:grid;grid-template-columns:2.1rem 1fr;gap:.8rem;align-items:center}
 .gp-node>em{font-style:normal;width:2.1rem;height:2.1rem;border-radius:50%;display:grid;place-items:center;font:700 .68rem/1 var(--mono);color:var(--bg);background:var(--c,var(--c-1));border:2px solid var(--c,var(--c-1));position:relative;z-index:1}
 .gp-card{background:var(--surface);border:1px solid color-mix(in srgb,var(--c,var(--c-1)) 30%,var(--border));border-radius:12px;padding:.65rem .9rem .7rem;display:flex;flex-direction:column;gap:.2rem}
 .gp-card p{margin:0;display:flex;justify-content:space-between;align-items:baseline;gap:.3rem .7rem;flex-wrap:wrap}
 .gp-card b{font-size:.9rem;color:var(--strong)}
 .gp-card small{font:600 .6rem/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);border:1px solid var(--border-2);border-radius:5px;padding:.2em .45em;white-space:nowrap}
 .gp-card>span{font-size:.8rem;line-height:1.45;color:var(--muted)}
 .gp-deny{display:inline-flex;align-items:center;gap:.4rem;margin-top:.3rem;font:600 .7rem/1.3 var(--mono);color:var(--stop)}
 .gp-deny::before{content:"✕";font-weight:700}
 /* ---- swimlane (§6): one column per party, a numbered dot per step; the page script draws the
    orthogonal connector. Set the column count with style="--lanes:N" on .lanes. ---- */
 .lanes{--lw:3.9rem;--lanes:4;position:relative;margin:1.4rem 0;max-width:none;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);padding:.9rem 1rem 1rem}
 .ln-head,.ln-steps li{display:grid;grid-template-columns:repeat(var(--lanes),var(--lw)) 1fr}
 .ln-head{border-bottom:1px solid var(--border)}
 .ln-head span{padding:.5rem 0 .55rem;font:700 .56rem/1.25 var(--mono);letter-spacing:.06em;text-transform:uppercase;text-align:center;color:var(--c,var(--muted));align-self:end}
 .ln-steps{list-style:none;margin:0;padding:0}
 .ln-steps li{margin:0;align-items:stretch}
 .ln-dots{display:contents}
 .ln-dots b{position:relative;display:grid;place-items:center}
 .ln-dots b:nth-child(even),.ln-head span:nth-child(even){background:var(--surface-2)}
 .ln-flow{position:absolute;left:0;top:0;pointer-events:none;overflow:visible;z-index:1}
 .ln-flow marker path{fill:color-mix(in srgb,var(--faint) 80%,transparent);stroke:none}
 .ln-flow path{fill:none;stroke:color-mix(in srgb,var(--faint) 70%,transparent);stroke-width:1.75;stroke-linejoin:round;stroke-linecap:round}
 .ln-dots s{position:relative;z-index:2;width:1.6rem;height:1.6rem;border-radius:50%;display:grid;place-items:center;text-decoration:none;
  font:700 .68rem/1 var(--mono);color:var(--bg);background:var(--c,var(--muted));box-shadow:0 0 0 4px var(--surface)}
 .ln-steps li>div{padding:.62rem 0 .62rem .9rem;display:flex;flex-direction:column;gap:.12rem;border-bottom:1px dashed var(--border)}
 .ln-steps li:last-child>div{border-bottom:0}
 .ln-steps li>div>b{font-size:.88rem;line-height:1.3;color:var(--strong)} .ln-steps li>div>span{font-size:.8rem;line-height:1.45;color:var(--muted)}
 .ln-own{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
 .ln-notes{display:grid;grid-template-columns:repeat(auto-fit,minmax(14rem,1fr));gap:.55rem;margin-top:.85rem}
 .ln-notes div{font-size:.8rem;line-height:1.5;color:var(--muted);border:1px dashed var(--border-2);border-radius:10px;padding:.65rem .8rem}
 .ln-notes b{display:block;font:700 .62rem/1.3 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--strong);margin-bottom:.2rem}
 .ln-notes div.is-out{border-color:color-mix(in srgb,var(--c-out) 60%,var(--border))}
 @media (max-width:40rem){.lanes{--lw:1.9rem}.ln-head span{font-size:0}
  .ln-own{position:static;width:auto;height:auto;clip:auto;font:700 .6rem/1.3 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--faint);font-style:normal}}
 /* ---- FAQ, grouped by reader type ---- */
 .faq{max-width:none;margin:1rem 0 1.4rem}
 .faq-h{margin:1.3rem 0 .45rem;font:700 .66rem/1.2 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--accent)}
 .faq-h:first-child{margin-top:0}
 /* ---- coverage table (§8): what each part does for each requirement, as a role label ---- */
 table.cov{--c:var(--accent)}
 table.cov td:not(:first-child),table.cov tr.cols th{text-align:center;width:7.4rem;vertical-align:middle}
 table.cov td:first-child b{font:700 .68rem/1 var(--mono);color:var(--accent);margin-right:.4rem}
 table.cov td small{display:block;margin-top:.25rem;font-size:.77rem;line-height:1.45;color:var(--muted)}
 table.cov thead th{position:static;vertical-align:bottom}
 table.cov tr.grp th{text-align:center;color:var(--strong);border-bottom:1px solid var(--border-2);padding-bottom:.45rem}
 table.cov tr.grp th:first-child{text-align:left}
 table.cov tr.cols th{line-height:1.35}
 table.cov tr.cols th::before{content:"";display:block;width:.6rem;height:.6rem;border-radius:50%;margin:0 auto .35rem;background:var(--c)}
 table.cov .split{border-left:1px solid var(--border-2)}
 table.cov .role{display:inline-block;font:600 .68rem/1.3 var(--mono);color:var(--c);background:color-mix(in srgb,var(--c) 10%,transparent);border:1px solid color-mix(in srgb,var(--c) 35%,transparent);border-radius:6px;padding:.3rem .45rem}
 table.cov .role.part{color:var(--warn);background:var(--warn-soft);border-color:color-mix(in srgb,var(--c-out) 55%,transparent);border-style:dashed}
 table.cov .role-none{color:var(--border-2)}
 /* ---- motion (only added by JS when the visitor allows it) ---- */
 .reveal{opacity:0;transform:translateY(14px);transition:opacity .6s cubic-bezier(.23,1,.32,1),transform .6s cubic-bezier(.23,1,.32,1)}
 .reveal.in{opacity:1;transform:none}
 /* ---- print ---- */
 @media print{
  body{background:#fff;color:#000;font-size:10.5pt} .app{display:block;max-width:none;padding:0} .side,.foot,.skip{display:none}
  #content>*{max-width:none} a{color:#000;text-decoration:underline}
  .exec,.mermaid,blockquote,details,pre,.outcome,.controls li,.board>section,.compare>section,.roles li,.ladder li,figure.shot img,figure.plate img,.tbl-scroll{box-shadow:none}
  h1.tier{color:#000} h2,h3{break-after:avoid}
  table,pre,.mermaid,details,blockquote,.exec,.outcome,.controls,.board,.compare,.roles,.statuses,.defs,.ladder,.steps li,figure.shot,figure.plate,.tbl-scroll{break-inside:avoid}
  thead th{position:static}
  summary::after{display:none} details>:not(summary){display:block!important;padding:.2rem 0 1rem} details,summary{border:0}
  .ts-token,.ln-flow{display:none}
  .trust-strip .ts-check,.trust-strip .ts-end.is-out{border-color:var(--accent-line);background:var(--accent-soft)}
  .trust-strip .ts-check .ok{opacity:1;transform:none}
  .req-groups>section,.who>section,.lanes,.cstate{box-shadow:none}
  .trust-strip,.req-groups,.who,.gp-node,.lanes,.cstate,.faq details,table.cov{break-inside:avoid}
  .reveal{opacity:1!important;transform:none!important}}
</style></head><body>
<a class="skip" href="#content">Skip to content</a>
<div class="app">
 <aside class="side"><nav class="toc" id="toc" aria-label="On this page"><p class="toc-h">On this page</p></nav></aside>
 <main class="doc"><div id="content"></div>
  <footer class="foot">Rendered view — the source of truth is the <code>.md</code> file; re-render with <code>gtm_core.design_render</code>.</footer>
 </main>
</div>
<script type="text/markdown" id="src">
__MD__
</script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const content=document.getElementById("content");
content.insertAdjacentHTML("beforeend", DOMPurify.sanitize(marked.parse(document.getElementById("src").textContent)));
const slug=s=>s.toLowerCase().replace(/[^\w\s-]/g,"").trim().replace(/\s+/g,"-").slice(0,60)||"s";
// hero = first h1 (+ its meta paragraph); other h1 = tier dividers
const h1=content.querySelector("h1");
if(h1){const hero=document.createElement("header");hero.className="hero";h1.parentNode.insertBefore(hero,h1);hero.appendChild(h1);
  const nx=hero.nextElementSibling; if(nx&&nx.tagName==="P")hero.appendChild(nx);}
content.querySelectorAll(":scope > h1").forEach(h=>h.classList.add("tier"));
// section ids + executive-summary panel
const h2s=[...content.querySelectorAll("h2")]; h2s.forEach(h=>{if(!h.id)h.id=slug(h.textContent);});
const exec=h2s.find(h=>/^executive summary/i.test(h.textContent.trim()));
if(exec){const panel=document.createElement("section");panel.className="exec";exec.parentNode.insertBefore(panel,exec);
  const move=[];let c=exec;while(c&&!(c!==exec&&(c.tagName==="H1"||c.tagName==="H2"||c.tagName==="HR"))){move.push(c);c=c.nextElementSibling;}
  move.forEach(el=>panel.appendChild(el));}
// a11y: wrap every table in a labelled scroll container (keeps the table role intact —
// never set display:block on <table>, which drops it in several screen readers)
content.querySelectorAll("table").forEach(t=>{
  if(t.closest(".tbl-scroll"))return;
  const w=document.createElement("div");w.className="tbl-scroll";w.setAttribute("tabindex","0");
  w.setAttribute("role","region");w.setAttribute("aria-label","Table, scrollable");
  t.parentNode.insertBefore(w,t);w.appendChild(t);});
// capability legend: count status tags in the coverage matrix, insert a legend above it
(()=>{const cap=[...content.querySelectorAll("table")].find(t=>t.querySelector(".tag"));if(!cap)return;
  const n=s=>cap.querySelectorAll(".tag."+s).length,legend=document.createElement("div");legend.className="cap-legend";
  [["ok","Enforced"],["sim","Simulated"],["warn","Design-target"],["stop","Not building"]].forEach(([c,label])=>{
    const ct=n(c);if(!ct)return;const lg=document.createElement("div");lg.className="lg";
    const t=document.createElement("span");t.className="tag "+c;t.textContent=label;lg.appendChild(t);
    const b=document.createElement("b");b.textContent=String(ct);lg.appendChild(b);
    lg.appendChild(document.createTextNode(ct===1?" capability":" capabilities"));legend.appendChild(lg);});
  if(legend.children.length){const host=cap.closest(".tbl-scroll")||cap;host.parentNode.insertBefore(legend,host);}})();
// numbered table of contents + scroll-spy
const toc=document.getElementById("toc");
if(toc&&h2s.length){const ol=document.createElement("ol");
  h2s.forEach(h=>{const li=document.createElement("li"),a=document.createElement("a");
    a.href="#"+h.id;const num=h.textContent.match(/^\s*(A?\d+)[.·]?\s*/);a.dataset.n=num?num[1]:"";a.textContent=h.textContent.replace(/^\s*(A?\d+)[.·]?\s*/,"");a.dataset.id=h.id;li.appendChild(a);ol.appendChild(li);});
  toc.appendChild(ol);
  const links=new Map([...toc.querySelectorAll("a")].map(a=>[a.dataset.id,a]));
  const spy=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){links.forEach(a=>a.classList.remove("active"));const a=links.get(e.target.id);if(a)a.classList.add("active");}});},{rootMargin:"0px 0px -78% 0px",threshold:0});
  h2s.forEach(h=>spy.observe(h));}
// coverage table: each body cell takes its column header's owner colour (data-own) and group split
content.querySelectorAll("table.cov").forEach(t=>{
  const cols=[...t.querySelectorAll("tr.cols th")];
  t.querySelectorAll("tbody tr").forEach(tr=>[...tr.children].slice(1).forEach((td,i)=>{
    const th=cols[i];if(!th)return;
    if(th.dataset.own)td.dataset.own=th.dataset.own;
    if(th.classList.contains("split"))td.classList.add("split");}));});
// motion: a [data-anim] component plays once when scrolled into view. Reduced motion — and any
// renderer that never scrolls — gets the final state (.static). CSS classes only: DOMPurify strips
// SVG <animate>, so there is no other way to move anything on this page.
const anims=[...content.querySelectorAll("[data-anim]")];
if(matchMedia("(prefers-reduced-motion: no-preference)").matches){
  const ao=new IntersectionObserver((es,o)=>{es.forEach(e=>{if(e.isIntersecting){e.target.classList.add("play");o.unobserve(e.target);}});},{threshold:.35});
  anims.forEach(el=>ao.observe(el));
  setTimeout(()=>anims.forEach(el=>{if(!el.classList.contains("play"))el.classList.add("static");}),2500);
}else anims.forEach(el=>el.classList.add("static"));
// swimlane connector: one path through the step dots in step order, horizontal and vertical
// segments only, an arrowhead into each next dot. Redrawn on resize; hidden in print.
const NS="http://www.w3.org/2000/svg";
content.querySelectorAll(".lanes").forEach((ln,k)=>{
  const id="ln-arrow-"+k;
  const draw=()=>{ln.querySelector(":scope > svg.ln-flow")?.remove();
    const dots=[...ln.querySelectorAll(".ln-dots s")];if(dots.length<2)return;
    const o=ln.getBoundingClientRect(),f=n=>n.toFixed(1);
    const pts=dots.map(d=>{const r=d.getBoundingClientRect();return {x:r.left+r.width/2-o.left,y:r.top+r.height/2-o.top,r:r.height/2};});
    const svg=document.createElementNS(NS,"svg");svg.setAttribute("class","ln-flow");svg.setAttribute("aria-hidden","true");
    svg.setAttribute("width",f(o.width));svg.setAttribute("height",f(o.height));
    const defs=document.createElementNS(NS,"defs"),mk=document.createElementNS(NS,"marker"),tip=document.createElementNS(NS,"path");
    [["id",id],["viewBox","0 0 10 10"],["refX","8"],["refY","5"],["markerWidth","7"],["markerHeight","7"],["orient","auto"]].forEach(([a,v])=>mk.setAttribute(a,v));
    tip.setAttribute("d","M0 0L10 5L0 10z");mk.appendChild(tip);defs.appendChild(mk);svg.appendChild(defs);
    for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i],ym=(a.y+b.y)/2,p=document.createElementNS(NS,"path");
      p.setAttribute("d",`M${f(a.x)} ${f(a.y+a.r+2)} L${f(a.x)} ${f(ym)} L${f(b.x)} ${f(ym)} L${f(b.x)} ${f(b.y-b.r-3)}`);
      p.setAttribute("marker-end",`url(#${id})`);svg.appendChild(p);}
    ln.prepend(svg);};
  new ResizeObserver(draw).observe(ln);});
// mermaid (theme-aware + tinted to --accent so diagrams read as part of the brand)
content.querySelectorAll("pre code.language-mermaid").forEach(c=>{const d=document.createElement("div");d.className="mermaid";d.textContent=c.textContent;c.closest("pre").replaceWith(d);});
const css=getComputedStyle(document.documentElement),v=n=>css.getPropertyValue(n).trim();
const dark=matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({startOnLoad:false,theme:dark?"dark":"neutral",securityLevel:"strict",
  themeVariables:{primaryColor:v("--surface-2"),primaryBorderColor:v("--accent"),primaryTextColor:v("--strong"),
    lineColor:v("--accent"),secondaryColor:v("--surface-3"),tertiaryColor:v("--surface"),
    fontFamily:v("--sans")||"system-ui",fontSize:"15px"}});
await mermaid.run({querySelector:".mermaid"});
// a11y: a mermaid SVG is unlabelled by default — give each diagram an accessible name from the
// "How to read it:" paragraph the doc already writes beneath it, so it is not silent to a reader.
content.querySelectorAll(".mermaid").forEach((d,i)=>{
  const svg=d.querySelector("svg");if(!svg)return;
  let desc="";for(let n=d.nextElementSibling;n&&!/^H[1-4]$/.test(n.tagName);n=n.nextElementSibling){
    if(n.tagName==="P"&&/how to read it/i.test(n.textContent)){desc=n.textContent.replace(/^\s*how to read it:?\s*/i,"").trim();break;}}
  const name=desc||("Diagram "+(i+1)+" — see the surrounding text for the description.");
  svg.setAttribute("role","img");svg.setAttribute("aria-label",name);
  d.setAttribute("role","group");d.setAttribute("aria-label","Diagram "+(i+1));});
// motion-gated reveal (content stays visible if JS/motion is off)
if(matchMedia("(prefers-reduced-motion: no-preference)").matches){
  const io=new IntersectionObserver((es,o)=>{es.forEach(e=>{if(e.isIntersecting){e.target.classList.add("in");o.unobserve(e.target);}});},{rootMargin:"0px 0px -6% 0px",threshold:.04});
  [content.querySelector(".hero"),...content.querySelectorAll(":scope > h1,:scope > h2,:scope > h3,:scope > p,:scope > ul,:scope > ol,:scope > blockquote,:scope > details,:scope > pre,:scope > .mermaid,:scope > .outcome,:scope > .controls,:scope > .board,:scope > .compare,:scope > .roles,:scope > .statuses,:scope > .defs,:scope > .ladder,:scope > .steps,:scope > .tbl-scroll,:scope > .req-groups,:scope > .cstate,:scope > .who,:scope > .gate-pipe,:scope > .lanes,:scope > .faq,:scope > figure,.exec")].forEach(el=>{if(el){el.classList.add("reveal");io.observe(el);}});
  // safety net: any renderer that never scrolls (static snapshot, print-to-PDF, headless
  // screenshot, embedded preview pane) leaves everything below the fold at opacity:0 forever.
  // Reveal unconditionally after the animation window — the document must never be invisible.
  setTimeout(()=>{document.querySelectorAll(".reveal:not(.in)").forEach(el=>el.classList.add("in"));},2500);}
</script></body></html>
````

## Component recipes (copy these into the `.md`)

Owner colours are set with `data-own="1"` … `"4"` (→ `--c-1` … `--c-4`), the same number for the
same owner everywhere in the doc.

- **Outcome headline (Exec summary).** One per doc, first thing under `## Executive summary`:
  `<div class="outcome"><span class="o-after">[The problem the design solves, in one line.]</span><span class="o-sub">[How — who does what — and the framework it follows, if any.]</span></div>`
  A before→after pair (`.o-before` `.o-arrow` `.o-after`) only when both sides are real figures.
  No gradient; never the big-number cliché.

- **Trust strip (Exec summary, optional).** The checks every request passes, lighting up in order —
  the one animation on the page, because the order is the point. `--d` is the check's position:
  ```
  <div class="trust-strip" data-anim role="group" aria-label="[Every request passes N checks before anything is shared]">
  <div class="ts-end"><b>Request</b><span>[from whom]</span></div>
  <div class="ts-track"><i class="ts-token" aria-hidden="true"></i>
  <div class="ts-check" style="--d:0"><em>01</em><b>[Check]</b><span>[how]</span><i class="ok" aria-hidden="true"></i></div>
  <div class="ts-check" style="--d:1"><em>02</em><b>[Check]</b><span>[how]</span><i class="ok" aria-hidden="true"></i></div>
  </div>
  <div class="ts-end is-out"><b>Answer</b><span>[decided by whom]</span></div>
  </div>
  ```

- **Requirement groups (§1).** Grouped by driver; R-numbers run in group order; `small.cite` carries
  section + page of the primary document, only for requirements derived from it:
  ```
  <div class="req-groups">
  <section><h4><span>[Meet the regulation]</span>[What it asks, in four words]</h4><ul>
  <li><em>R1</em><b>[Requirement]</b><span>[One sentence.]</span></li></ul></section>
  <section><h4><span>[Align with the framework]</span>[…]</h4><ul>
  <li><em>R2</em><b>[Requirement]</b><span>[One sentence.]</span><small class="cite">[Framework: component, § / p. N]</small></li></ul></section>
  <section><h4><span>[Easy to adopt]</span>[…]</h4><ul>
  <li><em>R3</em><b>[Requirement]</b><span>[One sentence.]</span></li></ul></section>
  </div>
  ```

- **Key terms (§1).** `<details class="gloss"><summary>Key terms</summary><ul class="defs"><li><b>[Term]</b><span>[One sentence. For a framework: what it is and how it describes its own status.]</span></li></ul></details>`

- **Current-state flow (§2).** Today's channel between two parties, what it lacks (`.x` missing,
  `.w` weak), and optionally the fixed obligation. Each "missing" item must be true today:
  ```
  <div class="cstate" role="group" aria-label="[Today: … travels by …]">
  <div class="cs-flow">
  <div class="cs-party"><em>[Party A]</em><b>[What they do]</b></div>
  <div class="cs-arrow" aria-hidden="true"><span>→</span><span class="back">←</span></div>
  <div class="cs-chan"><em>Today's channel</em><b>[Email · phone · …]</b><ul>
  <li class="x">[What is missing]</li><li class="w">[What exists but is weak]</li></ul></div>
  <div class="cs-arrow" aria-hidden="true"><span>→</span><span class="back">←</span></div>
  <div class="cs-party"><em>[Party B]</em><b>[What they must do]</b></div>
  </div>
  <div class="cs-ob"><div class="cs-due"><b>[N]</b><span>[unit]<br>[to do what]</span></div>
  <div class="cs-fmt"><em>[Required content]</em><ul><li>[item]</li><li>[item]</li></ul></div></div>
  </div>
  ```

- **Who does what (§3).** One card per party; say who stays accountable in the heading:
  ```
  <div class="who">
  <section data-own="4"><h4><span>[The customer]</span>[Decides, and stays accountable]</h4><ul><li>[…]</li></ul></section>
  <section data-own="3"><h4><span>[The integrator]</span>[Builds and runs the service]</h4><ul><li>[…]</li></ul></section>
  <section data-own="1"><h4><span>[The vendor]</span>[Provides the …]</h4><ul><li>[…]</li></ul></section>
  </div>
  ```

- **Check pipeline (§4).** The product's per-request steps, in order. `small` carries the framework
  component + page; `.gp-deny` states what happens on a failed check, once:
  ```
  <div class="gate-pipe" data-own="1" role="list" aria-label="[The N checks the product runs, in order]">
  <div class="gp-rail" aria-hidden="true"></div>
  <div class="gp-node" role="listitem"><em>01</em><div class="gp-card"><p><b>[Step]</b><small>[Framework · component · p. N]</small></p><span>[What it does, in plain words]</span></div></div>
  <div class="gp-node" role="listitem"><em>02</em><div class="gp-card"><p><b>[Step]</b></p><span>[…]</span><strong class="gp-deny">[A request that breaks a rule is blocked, and the block is logged]</strong></div></div>
  </div>
  ```

- **Swimlane (§6).** One column per party (`--lanes` = the count; a party may appear twice, e.g. a
  requesting and a responding customer), one row per step, exactly one `<s>` per row in the acting
  party's column. The script draws the connector; `.ln-own` names the actor for screen readers and
  narrow screens; `.ln-notes` holds the fallback path (`is-out`) and other edge cases:
  ```
  <div class="lanes" style="--lanes:4" role="group" aria-label="[One request, end to end, by who acts at each step]">
  <div class="ln-head" aria-hidden="true"><span data-own="4">[Party]</span><span data-own="3">[Party]</span><span data-own="1">[Product]</span><span data-own="4">[Party]</span><span></span></div>
  <ol class="ln-steps">
  <li><i class="ln-dots" aria-hidden="true"><b data-own="4"><s>1</s></b><b></b><b></b><b></b></i><div><em class="ln-own">[Party]</em><b>[What happens]</b><span>[One sentence.]</span></div></li>
  <li><i class="ln-dots" aria-hidden="true"><b></b><b></b><b data-own="1"><s>2</s></b><b></b></i><div><em class="ln-own">[Product]</em><b>[…]</b><span>[…]</span></div></li>
  </ol>
  <div class="ln-notes"><div class="is-out"><b>[If … (step N)]</b>[What happens instead, and what is not recorded.]</div></div>
  </div>
  ```

- **FAQ by reader (§7).** At most two per reader type, answers ≤ ~40 words:
  ```
  <div class="faq">
  <p class="faq-h">[Regulator]</p>
  <details><summary>[Question?]</summary><p>[Answer from the requirements or the design.]</p></details>
  <p class="faq-h">[IT &amp; security]</p>
  <details><summary>[Question?]</summary><p>[…]</p></details>
  </div>
  ```

- **Coverage table (§8).** Two header rows: the groups (technology · parties — the first party
  column carries `class="split"`), then one column per part with its `data-own`. Each cell is a
  2–4 word role, `role part` (amber, dashed) only where a real gap lies — with the gap stated in the
  row's `<small>` — and `role-none` where the part plays no part:
  ```
  <table class="cov">
  <thead><tr class="grp"><th rowspan="2">Requirement</th><th colspan="2">[Vendor] technology</th><th colspan="2" class="split">Parties</th></tr>
  <tr class="cols"><th data-own="1">[Component]</th><th data-own="2">[Component]</th><th data-own="3" class="split">[Party]</th><th data-own="4">[Party]</th></tr></thead>
  <tbody>
  <tr><td><b>R1</b>[Requirement]</td><td><span class="role">[checks each call]</span></td><td><span class="role">[confirms the requester]</span></td><td><span class="role">[runs the process]</span></td><td><span class="role">[decides what to share]</span></td></tr>
  <tr><td><b>R2</b>[Requirement]<small>[What is not covered.]</small></td><td><span class="role part">[logs agent steps]</span></td><td><span class="role-none" aria-label="Not involved">—</span></td><td><span class="role">[…]</span></td><td><span class="role-none" aria-label="Not involved">—</span></td></tr>
  </tbody></table>
  ```

- **Suite key.** Legend the owner colours once, beside the first diagram that uses them:
  `<ul class="suite-key"><li><i style="color:var(--c-1)"></i>[Suite or party]</li><li><i style="color:var(--c-out)"></i>[Not recorded]</li></ul>`

- **Diagram image.** `![[Alt text: what the diagram shows]](diagrams/<name>.svg)` then a
  `*How to read it:*` paragraph. `design_render` inlines the file; the page scales it to the column.

- **Comparison panel.** Two paths that differ in assurance; mark the stronger `is-strong`, the
  weaker `is-weak`, and state the weakness plainly:
  `<div class="compare"><section class="is-strong"><h4>Path A <span class="tag ok">Verified</span></h4><p>…</p></section><section class="is-weak"><h4>Path B <span class="tag warn">Fallback</span></h4><p>…</p></section></div>`

- **Shipping board (appendix; Mode B customer page).**
  `<div class="board"><section><h4>V1 <span class="when">Pilot · now</span></h4><ul><li>…</li></ul></section><section class="is-next"><h4>V2 <span class="when">Next</span></h4><ul><li>…</li></ul></section><section class="is-out"><h4>Not building <span class="when">V1</span></h4><ul><li>…</li></ul></section></div>`

- **Status board (appendix).**
  `<ul class="statuses"><li><span class="tag ok">Closed</span><span>…</span></li><li><span class="tag warn">Vendor</span><span>…</span></li></ul>`

- **Capability matrix (appendix).** Wrap each status in a pill — `<span class="tag ok">Enforced</span>`,
  `<span class="tag sim">Simulated</span>`, `<span class="tag warn">Design-target</span>`,
  `<span class="tag stop">Not building</span>`. The script counts them and inserts a legend above the
  matrix. In a plain `.md` viewer they degrade to the bare word.

- **Definition grid · step timeline · control strip · ladder · role cards.**
  `<ul class="defs"><li><b>Term</b><span>One sentence.</span></li></ul>` ·
  `<ol class="steps"><li><b>Step.</b> What happens.</li></ol>` ·
  `<ol class="controls"><li><b>Step</b><span>what it does</span></li></ol>` ·
  `<ul class="ladder"><li><div class="rung"><b>Level</b><em>01</em></div><p>…</p><div class="meter"><i style="width:25%"></i></div></li></ul>` ·
  `<ul class="roles"><li><b>Role</b><span>What they get.</span></li></ul>`.

- **Plate / product screenshot.** `<figure class="plate"><img src="plate-cover.png" alt="Decorative plate — abstract, no information content."></figure>` ·
  `<figure class="shot"><img src="ss-<surface>.png" alt="…"><figcaption>…</figcaption></figure>`
  (screenshots from `profiles/<active>/knowledge/brand/product-screenshots/`, see its `INDEX.md`).
  Copy the file next to the `.md` and reference it relatively; `design_render` inlines it.

## Notes

- **The hero + exec panel come from your Markdown, unchanged.** The script treats the first `# Title`
  (and the italic meta line under it) as the hero, wraps the **Executive summary** section into a
  panel, and builds the numbered "On this page" TOC from the `##` sections (stripping any leading
  `1.` / `A3.`). Any later `# H1` renders as a mono divider — the customer overview has none.
- **Every diagram needs a "How to read it:" paragraph directly beneath it.** The script harvests that
  sentence as a Mermaid SVG's `aria-label`; for an image, write the same content into its alt text.
- **Motion.** DOMPurify strips SVG `<animate>` (and all SMIL and `<script>`), so nothing inside the
  markdown can move itself: motion is a CSS class the page script toggles on a `[data-anim]`
  component (`.play` when scrolled into view). `prefers-reduced-motion`, and any renderer that never
  scrolls, get `.static` — the final state — and print forces the final state too. Only add
  `data-anim` where the motion reveals an order the static version cannot show.
- **Swimlane connector.** Drawn by the page script as an inline SVG over the lane grid (`.ln-flow`):
  one path per consecutive pair of dots — down from the dot, across at the midpoint, down into the
  next dot with an arrowhead — redrawn by a `ResizeObserver`, hidden in print. Lanes are told apart
  by alternating shading, never by guide lines.
- **Accessibility invariants — do not regress these.** `--faint` is set at the AA floor against
  `--bg` in both themes (4.88:1 light, 5.15:1 dark). Tables are wrapped in a focusable `.tbl-scroll`
  region rather than given `display:block`, which drops the table role. A skip link precedes the
  sticky TOC. The reveal is gated on `prefers-reduced-motion` and has an unconditional 2.5 s safety
  net. Decorative component parts carry `aria-hidden`; each composite component carries a
  `role` + `aria-label` that says what it shows.
- **No slop.** One neutral base + one locked brand accent + owner colours that mean an owner (no
  AI-purple, no gradient text); callouts are a tinted, fully-bordered card (**not** a colour
  side-stripe); one 12–14px radius scale; a mono utility layer for labels only; numbered markers
  only where the order is real. Scannability comes from hierarchy, spacing, the sticky TOC and the
  components — not decoration.
- Component blocks are `max-width:none` so they use the full column while running prose stays at the
  44 rem measure. That contrast is deliberate: a component reads as a distinct object, not a paragraph.
- The markdown lives inside `<script type="text/markdown" id="src">` (not a JS template literal) so
  backticks, `$SECRET:` and code fences need no escaping. The only thing to guard is a literal
  `</script>` in the doc — never write one.
- Rendered HTML is `DOMPurify.sanitize`d and inserted with `insertAdjacentHTML`; all enhancement
  (hero, TOC, exec panel, table wrapping, coverage colours, swimlane connector, diagram labelling,
  reveals) uses safe DOM methods (`createElement`/`createElementNS`/`setAttribute`/`textContent`) —
  no raw HTML-string sink, so the repo's `security-guidance` hook allows the headless brain to author
  it via the Write tool.
- Mermaid blocks still render client-side (theme from `prefers-color-scheme`, tinted with `--accent`,
  `securityLevel:"strict"`), but prefer a rendered SVG image (SKILL Step 4) so the diagram also draws
  in a plain viewer, an email client and a PDF. Mermaid edge labels: prefer the spaced dotted-label
  form `A -. label .-> B`.
