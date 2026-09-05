# Quality Gates — verification, linting, accessibility

Three gates a partnership brief must clear before it leaves the building. They exist because a
brief that had already been through a careful manual pass still shipped **thirteen** factual
errors — and every one was found by an outside reviewer, not by the author.

Run them in this order. Each is cheap; the cost of skipping one is a partner correcting you.

---

## Gate 1 — Claim verification

> **Verify your own side harder than theirs.**

This is the counterintuitive lesson and the most expensive one to relearn. In the brief that
motivated this file, every single overclaim about the *partner's* product was correct — because
the author knew they didn't know, and researched. Every overclaim about **our own** product was
wrong, because "we know our product" felt true:

- a five-step product flow that was a paraphrase, not the documented flow
- a supported protocol that isn't supported
- an architecture term ("two-tier policy") that is nobody's published wording
- a cited spec page with no author, no branding, and a domain root that 404s
- a press-release claim repeated **without its hedge** ("what is believed to be the first…")

The partner will fact-check *our* claims hardest, because those are the ones they are being asked
to depend on. Budget verification effort accordingly: **at least half on your own side.**

### The pass

1. **Extract every atomic claim** into a list — anything falsifiable: a number, a capability, a
   status, a standards attribution, a competitor statement, a date.
2. **Route each to a verifier** with the instruction to *refute*, not confirm. Fan these out in
   parallel; group by domain (standards / threat research / market / our own product).
3. **Tier the result** and act on the tier:

| Tier | Meaning | Action |
|---|---|---|
| **Verified** | primary source says exactly this | ship it, with the link inline |
| **Imprecise** | true but wrongly worded, attributed, or scoped | rewrite to the source's own wording |
| **Stale** | was true, no longer is | refresh the number and its date |
| **Unverifiable** | plausible, no primary source found | **remove.** Not "false" — unusable. |
| **Refuted** | a source contradicts it | remove, and check what else rested on it |

**"Unverifiable" is a removal, not a hedge.** Three separate claims in the motivating brief were
plausible, widely repeated, and had no primary source behind them. One traced back to a vendor
asserting it in a "Key Findings" block with no study, then laundered into apparent consensus by
SEO content. A hedge ("reportedly ~65%…") would have carried the error into the partner's hands
with our name on it.

### Watch for these specific traps

- **Standards-body borrowing.** A widely-known body's authority attached to a spec it does not
  own. Check the *owning* organisation of every named specification, not just its plausibility.
- **Enumerated framework claims.** "X prescribes five layers" — verify the *count* **and** the
  element *names* against the framework's own published text. A paraphrase presented as the
  framework's taxonomy is the most embarrassing error class, because the framework is public.
- **Absolute claims about competitors.** "None of them does X" is the single most falsifiable
  sentence shape in any brief. One competitor having a blog post titled exactly X destroys the
  paragraph and taints the rest. Scope it: *"of the vendors reviewed in §N."*
- **Benchmark numbers.** Confirm the vendor *published* the number. Third-party comparison pages
  invent and propagate benchmark scores that the vendor never claimed.
- **Press-release hedges.** If the source says "what is believed to be the first," you may not
  write "the first."

---

## Gate 2 — The linter

Deterministic, stdlib-only, no network by default:

```bash
uv run python -m tests.linter.partnership_brief_linter <brief>.md
```

Add `--check-links` for an HTTP pass that verifies every external URL **and its domain root** —
the check that catches an unattributable source whose page loads but whose site has no owner.

Run it against the rendered HTML too (`--mode html`) for the structural accessibility rules.

Rules map 1:1 to the failure classes above: `standards-attribution`, `unsourced-statistic`,
`absolute-claim`, `framework-enumeration`, `internal-voice`, `internal-leak`,
`untagged-capability`, `placeholder-link`, `missing-section`, `thin-opportunity`,
`missing-glossary`, `diagram-a11y`.

Any rule can be suppressed on its line with `<!-- lint-ok: <rule-id> -->`. Suppression is
deliberately visible so it reads as a decision rather than a default.

**Expect the linter to find real problems in a brief you believe is finished.** On the motivating
document — after a full manual verification pass — it caught an uncited Gartner statistic and four
unscoped absolute claims. Treat a clean run as the floor, not the goal.

---

## Gate 3 — Accessibility

Half the audience is a founder, not an engineer. Accessibility here means both senses: the page
works for assistive technology, and the *argument* is readable by someone without the domain.

### Comprehension

- **A plain-language key is required** once four or more technical terms appear. Term · what it
  means · **why it matters here** — the third column is what makes it worth reading.
- **Gloss at first use** as well; a reader should never have to jump to the back mid-paragraph.

### Rendered page

Structural rules (heading skips, `th scope`, `img alt`, placeholder hrefs) are covered by the
linter in `--mode html`. Colour contrast cannot be computed from markup — measure it in the
browser against the real computed styles, compositing semi-transparent backgrounds:

```js
const P=c=>{const m=c.match(/[\d.]+/g).map(Number);return{r:m[0],g:m[1],b:m[2],a:m[3]??1}};
const ov=(f,b)=>({r:f.r*f.a+b.r*(1-f.a),g:f.g*f.a+b.g*(1-f.a),b:f.b*f.a+b.b*(1-f.a),a:1});
const L=c=>{const f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};
  return .2126*f(c.r)+.7152*f(c.g)+.0722*f(c.b)};
const R=(f,b)=>{const x=L(f),y=L(b);return +(((Math.max(x,y)+.05)/(Math.min(x,y)+.05))).toFixed(2)};
function effBg(el){let st=[],n=el;while(n){const c=P(getComputedStyle(n).backgroundColor);
  if(c.a>0)st.push(c); if(c.a===1)break; n=n.parentElement;}
  let b=st.pop()||{r:255,g:255,b:255,a:1}; while(st.length)b=ov(st.pop(),b); return b;}
// every text-bearing component, in BOTH colour schemes; AA = 4.5 normal, 3.0 large
```

> **Compositing matters.** A naive contrast check that ignores alpha reports `1.0` for any text on
> a semi-transparent background and looks like a pass-through. Every status tag in the motivating
> document silently failed this way.

Check **both** colour schemes. Faint/muted tokens that pass on white routinely fail on dark.

### The invisible-content trap

Scroll-triggered reveal animations leave content at `opacity: 0` forever in any renderer that
never scrolls — print-to-PDF, headless screenshot, an embedded preview pane. Always ship the
unconditional safety net:

```js
setTimeout(()=>{document.querySelectorAll(".reveal:not(.in)").forEach(el=>el.classList.add("in"));},2500);
```

Then assert `document.querySelectorAll('.reveal:not(.in)').length === 0` before delivering.

---

## Gate 4 — Diagram/table coherence

A diagram asserts more confidently than prose, and it is the element most likely to be
screenshotted and forwarded without its caption.

In the motivating brief the integration table honestly said *"Confirm in V1"* while the diagram
drew the same relationship as an unqualified arrow — and the reviewer, correctly, challenged the
diagram. Worse, the underlying assumption turned out to be a **departure from the partner's
documented default**, which no amount of table hedging would have surfaced.

- Every diagram node and edge must trace to a status-tagged row.
- Anything not **Shipped** must be marked *in the diagram itself* or in a caption directly beneath.
- If the proposed design differs from the partner's documented default, **show both shapes** in a
  comparison table and say which is proposed and which is certain to work. A fallback that
  definitely works makes the proposal safer to say yes to, not weaker.
