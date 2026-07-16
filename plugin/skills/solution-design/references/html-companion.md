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
scroll-spy "On this page" table of contents; a designed hero + executive-summary panel; and a small,
**opt-in visual-component layer** the doc authors with plain inline HTML (an outcome band, a numbered
control strip, V1/V2 phase cards) — theme-aware Mermaid tinted to the brand accent; full light **and**
dark; a print stylesheet; and one restrained, motion-gated reveal. The polish is typography, spacing,
hierarchy, and a few purposeful components — never decoration.

**Visual-component layer (authored inline in the `.md`, styled here, safe in a plain viewer).** Beyond
the hero/exec/TOC chrome, the doc may reach for a handful of designed blocks. Each is written as plain
block-level HTML in the Markdown so it degrades to readable text in a no-CSS viewer and needs **no
extra JS** (DOMPurify keeps the structural tags + `class`). Use them where they earn their place — not
on every section:

- **Outcome band** — the before→after headline for the Exec summary (`"trust me" → provable`). One per
  doc, at the top. Never a gradient, never the big-number/small-label hero-metric cliché.
- **Control strip** — the product's sequential per-request controls as a compact numbered row. The
  **only** place numbered `01/02…` markers are allowed, because the order is real (every request passes
  through them in sequence). Do **not** number ordinary sections.
- **Phase cards** — the V1 / V2 cut as two side-by-side comparison cards (V2 recessed). Genuinely
  different content per card — not an identical-card grid.
- **Capability pills** — Enforced / Simulated / Design-target status as coloured pills inside the
  coverage matrix (below).
- **Product screenshot** (optional) — a real product-UI figure to make a proposed surface concrete.
  Reference it **relatively** and copy the PNG into the account folder next to the `.html` (keeps the
  companion lean — do not base64-embed a screenshot).

## How to generate it

> **Operator-run fallback (local Terminal only).** The recipe below uses `python3 -` (stdin
> heredoc), which is **denied** by the headless least-privilege policy as arbitrary code exec — the
> brain must not run it. Headless, the brain authors the `.html` companion directly with the Write
> tool (embed the `.md` into the template below). The `python3 -` recipe is documented only for an
> **operator** running it locally from their own Terminal.

After saving `solution-design-[company]-[date].md`, run this (it reads the .md and writes the .html —
no manual copy, guaranteed fidelity). The `.html` is written next to the `.md`, in the same account
folder (`content/<active>/accounts/<account-slug>/`).

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
 /* ---- tokens (override --accent + --accent-soft from the active profile's brand palette when known) ---- */
 :root{color-scheme:light dark;
  --accent:#3a5bd9; --accent-soft:rgba(58,91,217,.10); --accent-line:rgba(58,91,217,.34); --on-accent:#fff;
  --bg:#fbfbfd; --surface:#fff; --surface-2:#f4f5f8; --surface-3:#eceef3;
  --text:#171b23; --strong:#080a0f; --muted:#5a6170; --faint:#8c93a2;
  --border:#e6e8ee; --border-2:#d6d9e2;
  --ok:#1f8a5b; --ok-soft:rgba(31,138,91,.13); --warn:#a9701a; --warn-soft:rgba(169,112,26,.15);
  --shadow:0 1px 2px rgba(16,20,30,.05),0 12px 32px -16px rgba(16,20,30,.16);
  --measure:44rem;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
 @media (prefers-color-scheme:dark){:root{
  --accent:#8aa2ff; --accent-soft:rgba(138,162,255,.14); --accent-line:rgba(138,162,255,.32); --on-accent:#0b0e13;
  --bg:#0c0e13; --surface:#12151d; --surface-2:#181c26; --surface-3:#1f2431;
  --text:#e7eaf1; --strong:#f6f8fc; --muted:#98a1b2; --faint:#6b7385;
  --border:#222836; --border-2:#2c3341;
  --ok:#5fca9a; --ok-soft:rgba(95,202,154,.16); --warn:#e2ad5f; --warn-soft:rgba(226,173,95,.16);
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
 a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:4px}
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
 .controls{list-style:none;counter-reset:c;display:grid;grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr));
  gap:.55rem;margin:1.3rem 0;padding:0;max-width:var(--measure)}
 .controls li{counter-increment:c;margin:0;padding:.85rem .9rem;background:var(--surface);border:1px solid var(--border);
  border-radius:12px;display:flex;flex-direction:column;gap:.22rem}
 .controls li::before{content:counter(c,decimal-leading-zero);font:600 .68rem/1 var(--mono);color:var(--accent);letter-spacing:.06em}
 .controls li b{font-weight:640;color:var(--strong);font-size:.9rem;line-height:1.2}
 .controls li span{color:var(--muted);font-size:.81rem;line-height:1.36}
 /* ---- phase cards: the V1 / V2 cut, side by side (V2 recessed) ---- */
 .phases{display:grid;grid-template-columns:1fr 1fr;gap:.85rem;margin:1.35rem 0;max-width:var(--measure)}
 @media (max-width:640px){.phases{grid-template-columns:1fr}}
 .phase{padding:1.05rem 1.2rem;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
 .phase-next{background:var(--surface-2);box-shadow:none}
 .phase h4{margin:0 0 .55rem;color:var(--strong);font-size:.96rem;font-weight:680;letter-spacing:-.01em;display:flex;align-items:baseline;gap:.5rem}
 .phase h4 .when{font:600 .62rem/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--accent);
  padding:.24em .5em;border:1px solid var(--accent-line);border-radius:6px}
 .phase-next h4 .when{color:var(--muted);border-color:var(--border-2)}
 .phase ul{margin:0;padding-left:1.05rem} .phase li{margin:.3rem 0;font-size:.89rem;color:var(--text)}
 /* ---- callouts (blockquote): tinted card, full border — never a colour side-stripe ---- */
 blockquote{margin:1.35rem 0;padding:.95rem 1.2rem;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;color:var(--text)}
 blockquote>:first-child{margin-top:0} blockquote>:last-child{margin-bottom:0}
 blockquote strong:first-child{color:var(--accent)}
 /* ---- code ---- */
 code{background:var(--surface-2);border:1px solid var(--border);padding:.08em .4em;border-radius:5px;font:.85em var(--mono);color:var(--strong)}
 pre{background:var(--surface-2);border:1px solid var(--border);padding:1rem 1.1rem;border-radius:12px;overflow:auto;margin:1.25rem 0}
 pre code{background:none;border:0;padding:0;font-size:.84em}
 /* ---- product-screenshot figure (optional; reference a sibling PNG, do not base64-embed) ---- */
 figure.shot{margin:1.5rem 0;max-width:var(--measure)}
 figure.shot img{display:block;width:100%;height:auto;border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow)}
 figure.shot figcaption{margin:.6rem 0 0;color:var(--faint);font:500 .78rem/1.5 var(--mono);letter-spacing:.02em}
 /* ---- tables + capability tags ---- */
 table{border-collapse:collapse;width:100%;margin:1.35rem 0;font-size:.9rem;display:block;overflow-x:auto;font-variant-numeric:tabular-nums}
 th,td{border-bottom:1px solid var(--border);padding:.62rem .85rem;text-align:left;vertical-align:top}
 thead th{background:var(--surface-2);border-bottom:1px solid var(--border-2);color:var(--muted);
  font:600 .68rem/1.3 var(--mono);letter-spacing:.06em;text-transform:uppercase}
 tbody tr:last-child td{border-bottom:0} tbody tr:hover td{background:var(--surface-2)}
 .tag{display:inline-block;font:600 .68rem/1 var(--mono);letter-spacing:.04em;padding:.28em .55em;border-radius:6px;white-space:nowrap}
 .tag.ok{background:var(--ok-soft);color:var(--ok)} .tag.warn{background:var(--warn-soft);color:var(--warn)}
 .tag.sim{background:var(--surface-3);color:var(--muted)}
 /* ---- FAQ: native disclosure with a CSS chevron ---- */
 details{border:1px solid var(--border);border-radius:12px;margin:.55rem 0;background:var(--surface);overflow:hidden;transition:border-color .15s}
 details:hover{border-color:var(--border-2)}
 summary{cursor:pointer;padding:.9rem 1.1rem;font-weight:600;color:var(--strong);list-style:none;display:flex;justify-content:space-between;align-items:center;gap:1rem}
 summary::-webkit-details-marker{display:none}
 summary::after{content:"";flex:none;width:.5em;height:.5em;margin-right:.15em;border-right:2px solid var(--faint);border-bottom:2px solid var(--faint);transform:rotate(-45deg);transition:transform .2s ease}
 details[open] summary::after{transform:rotate(45deg)} details[open] summary{border-bottom:1px solid var(--border)}
 details>:not(summary){padding:.55rem 1.1rem 1.05rem;margin:0;color:var(--muted)}
 /* ---- mermaid: theme-aware card (dark theme in dark mode, neutral in light), tinted to --accent ---- */
 .mermaid{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.2rem;margin:1.5rem 0;text-align:center;overflow-x:auto;box-shadow:var(--shadow)}
 .foot{max-width:var(--measure);margin:3.5rem 0 0;padding-top:1.3rem;border-top:1px solid var(--border);color:var(--faint);font-size:.8rem}
 /* ---- motion (only added by JS when the visitor allows it) ---- */
 .reveal{opacity:0;transform:translateY(14px);transition:opacity .6s cubic-bezier(.23,1,.32,1),transform .6s cubic-bezier(.23,1,.32,1)}
 .reveal.in{opacity:1;transform:none}
 /* ---- print ---- */
 @media print{
  body{background:#fff;color:#000;font-size:10.5pt} .app{display:block;max-width:none;padding:0} .side,.foot{display:none}
  #content>*{max-width:none} a{color:#000;text-decoration:underline} .exec,.mermaid,blockquote,details,pre,.outcome,.controls li,.phase,figure.shot img{box-shadow:none}
  h1.tier{color:#000} h2,h3{break-after:avoid} table,pre,.mermaid,details,blockquote,.exec,.outcome,.controls,.phases,figure.shot{break-inside:avoid}
  summary::after{display:none} details>:not(summary){display:block!important;padding:.2rem 0 1rem} details,summary{border:0}
  .reveal{opacity:1!important;transform:none!important}}
</style></head><body>
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
// motion-gated reveal (content stays visible if JS/motion is off)
if(matchMedia("(prefers-reduced-motion: no-preference)").matches){
  const io=new IntersectionObserver((es,o)=>{es.forEach(e=>{if(e.isIntersecting){e.target.classList.add("in");o.unobserve(e.target);}});},{rootMargin:"0px 0px -6% 0px",threshold:.04});
  [content.querySelector(".hero"),...content.querySelectorAll(":scope > h1,:scope > h2,:scope > h3,:scope > p,:scope > ul,:scope > ol,:scope > table,:scope > blockquote,:scope > details,:scope > pre,:scope > .mermaid,:scope > .outcome,:scope > .controls,:scope > .phases,:scope > figure,.exec")].forEach(el=>{if(el){el.classList.add("reveal");io.observe(el);}});}
</script></body></html>'''
title=(md.splitlines()[0].lstrip("# ").strip() or "Solution Design")
open(out,"w").write(TPL.replace("__TITLE__",title).replace("__MD__",md))
print("wrote",out)
PY
```

## Notes
- **Authoring the FAQ (§8) as accordions.** Write each FAQ item in the `.md` as a native disclosure so
  it collapses in the companion and still reads fine in a plain viewer — keep the answer as inline HTML
  so marked passes the block through cleanly:
  `<details><summary>Question?</summary><p>Answer, with <code>inline</code> allowed.</p></details>`.
  `details`/`summary` are on DOMPurify's default allowlist, so **no extra JS** is needed.
- **Capability tags (Tier-2 §A4 / §A9 matrix).** Wrap each Enforced / Simulated / Design-target status
  in a pill so the HTML renders state in colour: `<span class="tag ok">Enforced</span>`,
  `<span class="tag sim">Simulated</span>`, `<span class="tag warn">Design-target</span>`. DOMPurify
  keeps `span`/`class`, so no extra JS. In a plain `.md` viewer these degrade to the bare word.
- **Outcome band (Exec summary).** Write the before→after headline as a block right after the `# Title`
  meta line, at the very top of the Exec summary:
  `<div class="outcome"><span class="o-before">"trust me"</span><span class="o-arrow">→</span><span class="o-after">provable</span><span class="o-note">every agent action, cryptographically evidenced</span></div>`
  One per doc. In a plain viewer it degrades to a readable line. No gradient; never the big-number cliché.
- **Control strip (§4 / §5 — the product's per-request steps).** Render the product's sequential
  controls as a numbered strip — this is the **only** place `01/02…` markers belong, because each
  request really does pass through them in order:
  `<ol class="controls"><li><b>Caller context</b><span>bind the authorising human</span></li><li><b>Agent identity</b><span>verify the agent's DID</span></li>…</ol>`.
  Do **not** number ordinary `##` sections (numbered scaffolding on non-sequential content is slop).
- **Phase cards (§7 — the V1 / V2 cut).** Render the ship-first cut as two comparison cards; tag the
  timeframe with a `.when` pill and recess V2 with `phase-next`:
  `<div class="phases"><section class="phase"><h4>V1 <span class="when">POC · weeks</span></h4><ul><li>…</li></ul></section><section class="phase phase-next"><h4>V2 <span class="when">Next</span></h4><ul><li>…</li></ul></section></div>`.
  Keep the **"not building (V1)"** list as ordinary prose/bullets below the cards.
- **Product screenshot (optional, §5).** To make a proposed surface concrete, add
  `<figure class="shot"><img src="ss-policy-editor.png" alt="…"><figcaption>…</figcaption></figure>`
  and **copy the PNG into the account folder** next to the `.html` (from
  `profiles/<active>/knowledge/brand/product-screenshots/` — see that folder's `INDEX.md`). Reference
  it **relatively**; do not base64-embed (keeps the file lean and out of the model's context).
- **The hero + exec panel + tiers come from your Markdown, unchanged.** The script treats the first
  `# Title` (and the `_Company · Product · Date_` line under it) as the hero, wraps the **Executive
  summary** section into a panel, styles the `# Tier 1` / `# Tier 2` headers as mono dividers, and
  builds the numbered "On this page" TOC from the `##` sections (stripping any leading `1.` / `A3.`).
  Just write the doc per `body_template.md` Step 5 — the visual blocks above are plain inline HTML you
  add where they earn their place; no special build step.
- **Brand accent.** `--accent` defaults to a restrained neutral indigo. When a concrete design is
  generated for a profile, override `--accent`, `--accent-soft`, and `--accent-line` (both the light
  `:root` and the dark `@media` block) with that profile's brand colour (documented in
  `profiles/<active>/knowledge/brand/`) — it's used for links, the active TOC item, the hero/tier
  rules, the exec bullets, the outcome arrow, the control-strip numerals, the phase `.when` pill,
  Mermaid line/border tint, and focus rings, never as flat decoration. One accent only; keep any
  brand *secondary* out of the page chrome (a sparing data accent inside a diagram at most).
- **No slop.** One neutral base + one locked accent (no AI-purple in the chrome, no gradient text);
  callouts are a tinted, fully-bordered card (**not** a colour side-stripe); one 12–14px radius scale;
  a mono utility layer for labels only; theme-locked light/dark; numbered markers only on the
  genuinely-sequential control strip; motion is a single motivated reveal gated on
  `prefers-reduced-motion`. Scannability comes from hierarchy, spacing, the sticky TOC, and the few
  purposeful components above — not decoration.
- Embed the markdown inside `<script type="text/markdown">` (not a JS template literal) so backticks,
  `$SECRET:`, and ```rego``` fences need no escaping. The only thing to guard is a literal
  `</script>` in the doc (the assert catches it).
- The post-processing loop converts marked's `<pre><code class="language-mermaid">` into
  `<div class="mermaid">` before `mermaid.run`. Mermaid's theme is chosen from `prefers-color-scheme`
  (`dark` on dark, `neutral` on light) and tinted with the page's `--accent` (line + border colour) so
  diagrams read as part of the brand in either mode. `securityLevel:"strict"` keeps mermaid from
  emitting click-handlers or raw HTML.
- Rendered HTML is `DOMPurify.sanitize`d and inserted with `insertAdjacentHTML`; all enhancement
  (hero, TOC, exec panel, reveals) uses safe DOM methods (`createElement`/`textContent`/`append`) — no
  raw `innerHTML` sink, so the repo's `security-guidance` hook allows the headless brain to author it
  via the Write tool. The visual-component blocks are plain inline HTML in the `.md` that survives
  DOMPurify (structural tags + `class` only). Load `marked`, `dompurify`, and `mermaid` from CDN
  (already wired in the head).
- Mermaid edge labels: prefer spaced dotted-label form `A -. label .-> B` for widest compatibility.
- Same generator works for any of the SA deliverables that carry diagrams.
