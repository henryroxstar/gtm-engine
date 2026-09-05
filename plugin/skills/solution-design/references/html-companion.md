# HTML companion (renders the doc as a premium, readable page + draws the Mermaid diagrams)

Markdown viewers without a mermaid engine (e.g. macOS "MD Viewer", many lightweight previewers) show
```mermaid``` blocks as literal code. So `solution-design` always emits a **self-contained HTML
companion** next to the `.md` — `marked.js` renders the doc, `mermaid.js` draws the diagrams, and a
small vanilla script upgrades the page from CDN. The `.md` stays the source of truth. Marked's output
is passed through **DOMPurify** and inserted with `insertAdjacentHTML` (a safe DOM method) rather than
a raw `innerHTML` sink, so the generator passes the repo's security hook on the headless Write path.

**Design read:** a customer-facing solution-overview document for technical + business buyers —
*editorial / premium-docs* language (think Stripe or Linear docs). Refined **system sans** for the
hero + body with a **mono utility layer** (kicker, tier labels, table headers, capability tags) that's
grounded in the subject's protocol/agent vernacular; one neutral base biased toward a single locked
accent (no AI-purple, no gradient text, no side-stripe callouts, one radius scale). A sticky, numbered
scroll-spy "On this page" table of contents; a designed hero + executive-summary panel; and a
**visual-component layer** the doc authors with plain inline HTML — theme-aware Mermaid tinted to the
brand accent; full light **and** dark; a print stylesheet; and one restrained, motion-gated reveal.
The polish is typography, spacing, hierarchy, and a set of purposeful components — never decoration.

---

## Rule 0 — a solution design is measured on visual density, not word count

A design that is *correct* and *unreadable* has failed. Two numbers govern this template, and the
generator is expected to hit both:

| Budget | Target | Why |
|---|---|---|
| **Visual anchor every ≤3 screens** | no run of prose longer than ~3 viewport heights without a diagram, component, or matrix | the failure mode this template exists to stop is the 15-screen text desert |
| **Tier 1 ≤ 5,000 words** | the customer-facing read is ~20 screens, not 50 | Tier 2 is a *separate file*, not a longer scroll (see Rule 1) |

**Audit any doc you did not just write** before adding to it:

```bash
# words per section — find the prose blocks that should have become components
awk '/^#{1,3} /{if(h!=""){printf "%6d  %s\n",w,h} h=$0;w=0;next}{w+=NF}
     END{if(h!="")printf "%6d  %s\n",w,h}' solution-design-<company>-<date>.md
# any section over ~600 words is a component that was written as prose.
```

## Rule 1 — Tier 2 is a separate file, and internal notes are a third

One document, one audience. Emit **two or three files**, never one scroll that tries to serve all of
them:

| File | Audience | Contains |
|---|---|---|
| `solution-design-<company>-<date>.md` | the exec + the customer | Exec summary + Tier 1 only, ending with a link to the appendix |
| `solution-design-<company>-<date>-appendix.md` | the customer's architects | Tier 2 (A1…An) |
| `solution-design-<company>-<date>-internal.md` | **us only — never sent** | the "omit from customer copy" material |

Each gets its own HTML companion by the same recipe. Two hard rules:

- **A section whose own heading says "omit from customer copy" must not be in the customer file.** If
  you write that phrase, you are writing the internal file — put it there.
- **Never ship a version log to a customer.** Solution designs revise **silently**. A changelog
  narrating v2.1 → v2.5 to a reader who never saw v1 is pure scroll cost, and it is the single
  largest concision win available in most existing docs. Version history lives in git.

## Rule 2 — the executive summary is the most over-written section in every draft

It is read by the one person least willing to read, and it is where authors put everything they are
proud of. **Hard cap: 150 words after the outcome band.** At that length it is one screen, which is
the entire point.

| Belongs in the Exec summary | Belongs somewhere else |
|---|---|
| The outcome band — one before→after where one side is real | Any second before→after |
| The value, in one sentence | Role definitions and who-does-what → §3 / `.roles` |
| The problem, in one sentence | Product names, protocols, acronyms → §4 |
| The solution, in one sentence | Caveats, betas, assumptions → the open-decisions section |
| Who it is for — a list of roles, not a paragraph | Anything a reader needs §1 to understand |

**The test:** every bullet is one sentence and survives being read aloud in a breath. A bullet that
needs a subordinate clause to define a term is not an exec-summary bullet — the term belongs in the
glossary and the bullet belongs in Tier 1. A bullet over ~35 words is a paragraph wearing a bullet.

Count it before you ship:

```bash
awk '/^## Executive summary/{f=1;next} /^#/{f=0} f' solution-design-<company>-<date>.md | wc -w
```

---

## Brand binding (required — do this before you write the HTML)

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
TOC item and the control-strip numerals, so it is body-weight text, not decoration. Not every kit
survives this binding unchanged: a **monochrome or dark-first brand** (primary == accent, both
near-white) is legible on its own near-black canvas and invisible as light-mode chrome. Where the
declared value fails, darken or lighten **that same hue** along its lightness axis until it clears,
and say so in the render output — never swap in a different colour, and never ship the failing one.
A silent 1.2:1 accent is the failure this rule exists to catch. Verify after substitution:

```bash
grep -o '#[0-9a-fA-F]\{6\}' solution-design-<company>-<date>.html | sort -u   # every hex…
uv run python -m gtm_core.brandkit --profile "$P" --key palette              # …must appear here,
# except the template's declared neutrals (greys, near-black, near-white).
```

**Suite / product colour-coding.** Where a profile's kit declares per-suite gradients
(`gradients.<suite>.stops`), the diagrams **must** use them so a reader can tell which product does
what without reading a word — this is the cheapest large clarity win available and most existing docs
skip it. Resolve the stops and drive them into Mermaid with `classDef` in the `.md` itself:

```bash
uv run python -m gtm_core.brandkit --profile "$P" --key gradients.fabric.stops   # → ["#…","#…"]
```

````
classDef suiteA stroke:<suite-A first stop>,stroke-width:2.5px
classDef suiteB stroke:<suite-B first stop>,stroke-width:2.5px
class NODE_A1,NODE_A2 suiteA
class NODE_B1,NODE_B2 suiteB
````

Then legend it once, next to the first diagram that uses it, with `.suite-key` (below). One suite =
one stroke colour, used consistently in **every** diagram in the doc.

---

## Visual-component layer

Each component is written as plain block-level HTML **in the Markdown**, so it degrades to readable
text in a no-CSS viewer and needs **no extra JS** (DOMPurify keeps structural tags + `class`). Use
them where they earn their place — but Rule 0 means "where they earn their place" is *most dense
sections*, not one per document.

| Component | Class | Use it for |
|---|---|---|
| Outcome band | `.outcome` | the before→after headline. One per doc, top of the Exec summary. |
| Control strip | `.controls` | the product's sequential per-request steps. The **only** place `01/02…` numbering is allowed. |
| Comparison panel | `.compare` | two paths that differ in assurance — on-platform vs off, V1 vs fallback, us vs status quo. |
| Shipping board | `.board` | the V1 / V2 / not-building cut as three columns. |
| Role cards | `.roles` | who gets what, per stakeholder. Replaces a "stakeholder → value" table. |
| Definition grid | `.defs` | a glossary — the customer's systems, or the key terms. Replaces a bulleted term list. |
| Step timeline | `.steps` | the numbered walkthrough under a diagram. Replaces a bare `<ol>`. |
| Ladder | `.ladder` | an ordered progression where the **rung order is the information** — assurance levels, maturity tiers. |
| Status board | `.statuses` | open questions / dependencies as resolved · open · blocked. |
| Capability matrix | `.matrix` + `.tag` | Enforced / Simulated / Design-target coverage. |
| Suite key | `.suite-key` | the legend for per-suite diagram colours. |
| Plate | `figure.plate` | a decorative cover or tier-divider image. **Never** explanatory. |
| Product screenshot | `figure.shot` | a real product-UI figure, to make a proposed surface concrete. |

### What a generated image may and may not be

`figure.plate` exists so a long document has visual punctuation. It is **decorative only**:

- **Allowed:** an abstract cover plate, a tier divider, a section opener. Abstract, on the profile's
  declared gradient, carrying no labels, no arrows, no boxes, no text.
- **Never:** architecture, flows, sequences, delegation chains, topologies, or anything a reader
  could mistake for evidence. A generated diagram will look plausible and be wrong. Those are
  Mermaid, always.
- **Never:** people. These documents name real institutional roles at regulated buyers; synthetic
  faces read as unserious exactly where credibility is the product.
- **Never:** a rendered metaphor. "Trust perimeter", "ladder", "leash" render literally and badly.
- Budget **4–6 plates for a full document.** Every plate carries an `alt` that says it is decorative.

---

## How to generate it

> **Operator-run fallback (local Terminal only).** The recipe below uses `python3 -` (stdin
> heredoc), which is **denied** by the headless least-privilege policy as arbitrary code exec — the
> brain must not run it. Headless, the brain authors the `.html` companion directly with the Write
> tool (embed the `.md` into the template below). The `python3 -` recipe is documented only for an
> **operator** running it locally from their own Terminal.

After saving `solution-design-[company]-[date].md`, run this (it reads the .md and writes the .html —
no manual copy, guaranteed fidelity). The `.html` is written next to the `.md`, in the same account
folder (`content/<active>/accounts/<account-slug>/`). Run it once per file from Rule 1.

```bash
python3 - <<'PY'
src="solution-design-<<company>>-<<date>>.md"          # the file you just saved
out=src[:-3]+".html"
md=open(src).read()
assert "</script>" not in md.lower(), "markdown contains a script close tag — handle before embedding"
TPL=r'''<!DOCTYPE html>
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
 .toc a::before{content:counter(s,decimal-leading-zero);counter-increment:s;font:500 .7rem/1.5 var(--mono);color:var(--faint);flex:none}
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
  .reveal{opacity:1!important;transform:none!important}}
</style></head><body>
<a class="skip" href="#content">Skip to content</a>
<div class="app">
 <aside class="side"><nav class="toc" id="toc" aria-label="On this page"><p class="toc-h">On this page</p></nav></aside>
 <main class="doc"><div id="content"></div>
  <footer class="foot">Rendered view — the source of truth is the <code>.md</code> file. Diagrams drawn by mermaid.js.</footer>
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
    a.href="#"+h.id;a.textContent=h.textContent.replace(/^\s*(A?\d+)[.·]?\s*/,"");a.dataset.id=h.id;li.appendChild(a);ol.appendChild(li);});
  toc.appendChild(ol);
  const links=new Map([...toc.querySelectorAll("a")].map(a=>[a.dataset.id,a]));
  const spy=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){links.forEach(a=>a.classList.remove("active"));const a=links.get(e.target.id);if(a)a.classList.add("active");}});},{rootMargin:"0px 0px -78% 0px",threshold:0});
  h2s.forEach(h=>spy.observe(h));}
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
  [content.querySelector(".hero"),...content.querySelectorAll(":scope > h1,:scope > h2,:scope > h3,:scope > p,:scope > ul,:scope > ol,:scope > blockquote,:scope > details,:scope > pre,:scope > .mermaid,:scope > .outcome,:scope > .controls,:scope > .board,:scope > .compare,:scope > .roles,:scope > .statuses,:scope > .defs,:scope > .ladder,:scope > .steps,:scope > .tbl-scroll,:scope > figure,.exec")].forEach(el=>{if(el){el.classList.add("reveal");io.observe(el);}});
  // safety net: any renderer that never scrolls (static snapshot, print-to-PDF, headless
  // screenshot, embedded preview pane) leaves everything below the fold at opacity:0 forever.
  // Reveal unconditionally after the animation window — the document must never be invisible.
  setTimeout(()=>{document.querySelectorAll(".reveal:not(.in)").forEach(el=>el.classList.add("in"));},2500);}
</script></body></html>'''
title=(md.splitlines()[0].lstrip("# ").strip() or "Solution Design")
open(out,"w").write(TPL.replace("__TITLE__",title).replace("__MD__",md))
print("wrote",out)
PY
```

## Component recipes (copy these into the `.md`)

- **Outcome band (Exec summary).** One per doc, right after the `# Title` meta line:
  `<div class="outcome"><span class="o-before">"trust me"</span><span class="o-arrow">→</span><span class="o-after">provable</span><span class="o-note">every agent action, cryptographically evidenced</span></div>`
  No gradient; never the big-number cliché.

- **Control strip.** The product's sequential per-request steps — the **only** place `01/02…`
  numbering belongs, because each request really does pass through them in order:
  `<ol class="controls"><li><b>Caller context</b><span>bind the authorising human</span></li><li><b>Agent identity</b><span>verify the agent's DID</span></li>…</ol>`
  Do **not** number ordinary `##` sections (numbered scaffolding on non-sequential content is slop).
  Author it **once** and cross-reference it; restating the same steps as prose in three sections is
  the most common padding in these documents.

- **Comparison panel.** Two paths that differ in assurance. Mark the stronger `is-strong` and the
  weaker `is-weak`, and state the weakness plainly rather than burying it:
  `<div class="compare"><section class="is-strong"><h4>Path A · verified <span class="tag ok">Cryptographic</span></h4><p>…</p></section><section class="is-weak"><h4>Path B · fallback <span class="tag warn">Reputational</span></h4><p>…</p></section></div>`

- **Shipping board (the V1 / V2 / not-building cut).** Three columns, not prose:
  `<div class="board"><section><h4>V1 <span class="when">Pilot · now</span></h4><ul><li>…</li></ul></section><section class="is-next"><h4>V2 <span class="when">Next</span></h4><ul><li>…</li></ul></section><section class="is-out"><h4>Not building <span class="when">V1</span></h4><ul><li>…</li></ul></section></div>`

- **Role cards (who gets what).** Replaces the stakeholder table:
  `<ul class="roles"><li><b>Hiring FI · CISO</b><span>Its own identity as the named principal on every check.</span></li>…</ul>`

- **Definition grid (a glossary).** Every solution design carries two — the customer's own
  systems/agents/acronyms, and the product key-terms. Both are bulleted term lists by default and
  both read far better as a grid:
  `<ul class="defs"><li><b>DID</b><span>A cryptographically verifiable ID for an agent or person.</span></li><li><b>VC</b><span>A tamper-evident, digitally signed claim.</span></li></ul>`
  Keep each definition to one sentence — a glossary entry that needs two is a Tier-1 paragraph.

- **Step timeline (the walkthrough under a diagram).** Every diagram gets a "How to read it"
  paragraph *and*, where the sequence matters, a numbered walkthrough. Write the walkthrough as
  `.steps` so it renders as a connected timeline rather than a flat list:
  `<ol class="steps"><li><b>Caller authorises.</b> The mandate is bound to the agent's identity.</li><li><b>The gateway verifies …</b></li></ol>`
  Lead each step with a bolded clause so it scans without being read.

- **Ladder (an ordered progression).** Use it **only** where the order carries information a reader
  needs — assurance levels, maturity tiers, a path the design pushes participants along. A `.board`
  flattens that ordering and loses it. The `.meter` width encodes the level, so the ascent survives
  stacking on a narrow screen:
  `<ul class="ladder"><li><div class="rung"><b>Email</b><em>01</em></div><p>Reputational trust only.</p><div class="meter"><i style="width:25%"></i></div></li>…</ul>`
  Do **not** reach for it as a generic three-card grid — that is `.board`.

- **Status board (open questions / dependencies).**
  `<ul class="statuses"><li><span class="tag ok">Closed</span><span>Issuer = the operator; employer named in-VC.</span></li><li><span class="tag warn">Vendor</span><span>Singapore data-residency specifics.</span></li></ul>`

- **Capability matrix.** Wrap each status in a pill so the HTML renders state in colour:
  `<span class="tag ok">Enforced</span>`, `<span class="tag sim">Simulated</span>`,
  `<span class="tag warn">Design-target</span>`, `<span class="tag stop">Not building</span>`. The
  script counts them and inserts a legend above the matrix automatically. In a plain `.md` viewer
  they degrade to the bare word.

- **Suite key.** Legend the diagram colour code once, beside the first diagram that uses it:
  `<ul class="suite-key"><li><i style="color:SUITE_A_STOP"></i>Suite A — the components it covers</li><li><i style="color:SUITE_B_STOP"></i>Suite B — the components it covers</li></ul>`
  Substitute the real suite names and resolved stops from the kit. The `style` carries only
  `color`; DOMPurify keeps it. Set only `stroke` in the `classDef`, never `fill` — a fill tuned
  for one Mermaid theme inverts illegibly in the other.

- **Plate (decorative).** Read "What a generated image may and may not be" first:
  `<figure class="plate"><img src="plate-tier2.png" alt="Decorative section plate — abstract, no information content."><figcaption>Tier 2 — technical appendix</figcaption></figure>`
  Copy the PNG into the account folder next to the `.html`; reference it **relatively** and never
  base64-embed it.

- **Product screenshot.** A real product-UI figure, to make a proposed surface concrete:
  `<figure class="shot"><img src="ss-policy-editor.png" alt="…"><figcaption>…</figcaption></figure>`
  Source from `profiles/<active>/knowledge/brand/product-screenshots/` (see that folder's `INDEX.md`).

- **FAQ accordions.** Write each item as a native disclosure so it collapses in the companion and
  still reads fine in a plain viewer:
  `<details><summary>Question?</summary><p>Answer, with <code>inline</code> allowed.</p></details>`.
  `details`/`summary` are on DOMPurify's default allowlist, so **no extra JS** is needed.

## Notes

- **The hero + exec panel + tiers come from your Markdown, unchanged.** The script treats the first
  `# Title` (and the `_Company · Product · Date_` line under it) as the hero, wraps the **Executive
  summary** section into a panel, styles the `# Tier 1` / `# Tier 2` headers as mono dividers, and
  builds the numbered "On this page" TOC from the `##` sections (stripping any leading `1.` / `A3.`).
- **Every diagram needs a "How to read it:" paragraph directly beneath it.** This is not optional
  prose — the script harvests that sentence as the SVG's `aria-label`, because a Mermaid SVG is
  otherwise completely silent to a screen reader. No paragraph, no accessible name.
- **Accessibility invariants — do not regress these.** `--faint` is set at the AA floor against
  `--bg` in both themes (4.88:1 light, 5.15:1 dark); lightening it re-breaks the footer, the TOC
  numerals and the list markers. Tables are wrapped in a focusable `.tbl-scroll` region rather than
  given `display:block`, which drops the table role. A skip link precedes the sticky TOC. The reveal
  is gated on `prefers-reduced-motion` and has an unconditional 2.5 s safety net.
- **No slop.** One neutral base + one locked brand accent (no AI-purple in the chrome, no gradient
  text); callouts are a tinted, fully-bordered card (**not** a colour side-stripe); one 12–14px
  radius scale; a mono utility layer for labels only; theme-locked light/dark; numbered markers only
  on the genuinely-sequential control strip; motion is a single motivated reveal. Scannability comes
  from hierarchy, spacing, the sticky TOC, and the components above — not decoration.
- Component blocks are `max-width:none` so they use the full column while running prose stays at the
  44 rem measure. That contrast is deliberate: a component reads as a distinct object, not a paragraph.
- Embed the markdown inside `<script type="text/markdown">` (not a JS template literal) so backticks,
  `$SECRET:`, and ```rego``` fences need no escaping. The only thing to guard is a literal
  `</script>` in the doc (the assert catches it).
- The post-processing loop converts marked's `<pre><code class="language-mermaid">` into
  `<div class="mermaid">` before `mermaid.run`. Mermaid's theme is chosen from `prefers-color-scheme`
  (`dark` on dark, `neutral` on light) and tinted with the page's `--accent` (line + border colour) so
  diagrams read as part of the brand in either mode. `securityLevel:"strict"` keeps mermaid from
  emitting click-handlers or raw HTML.
- Rendered HTML is `DOMPurify.sanitize`d and inserted with `insertAdjacentHTML`; all enhancement
  (hero, TOC, exec panel, table wrapping, diagram labelling, reveals) uses safe DOM methods
  (`createElement`/`textContent`/`append`) — no raw `innerHTML` sink, so the repo's
  `security-guidance` hook allows the headless brain to author it via the Write tool.
- Mermaid edge labels: prefer spaced dotted-label form `A -. label .-> B` for widest compatibility.
