# HTML companion (renders the Mermaid diagrams anywhere)

Markdown viewers without a mermaid engine (e.g. macOS "MD Viewer", many lightweight previewers) show
```mermaid``` blocks as literal code. So `solution-design` always emits a **self-contained HTML
companion** next to the `.md` — `marked.js` renders the doc and `mermaid.js` draws the diagrams from
CDN, working in any browser with internet. The `.md` stays the source of truth. Marked's output is
passed through **DOMPurify** and inserted with `insertAdjacentHTML` (a safe DOM method) rather than a
raw `innerHTML` sink, so the generator passes the repo's security hook on the headless Write path.

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
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
<style>
 body{max-width:860px;margin:40px auto;padding:0 20px;font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a2e}
 h1,h2,h3{line-height:1.25;margin-top:1.6em} h1{border-bottom:2px solid #e0e0ea;padding-bottom:.3em}
 h2{border-bottom:1px solid #ececf3;padding-bottom:.25em} code{background:#f4f4f8;padding:.1em .35em;border-radius:4px;font-size:.9em}
 pre{background:#f7f7fb;padding:14px;border-radius:8px;overflow:auto} pre code{background:none;padding:0}
 table{border-collapse:collapse;width:100%;margin:1em 0} th,td{border:1px solid #e0e0ea;padding:8px 10px;text-align:left;font-size:.92em}
 th{background:#f4f4f8} blockquote{border-left:4px solid #c9c9e0;margin:1em 0;padding:.2em 1em;color:#555;background:#fafaff}
 .mermaid{background:#fff;text-align:center;margin:1.4em 0}
 .banner{background:#eef6ff;border:1px solid #cfe3ff;border-radius:8px;padding:8px 12px;font-size:.85em;color:#345}
</style></head><body>
<div class="banner">Rendered view · diagrams drawn by mermaid.js. Source of truth remains the .md file.</div>
<div id="content"></div>
<script type="text/markdown" id="src">
__MD__
</script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const md=document.getElementById("src").textContent;
document.getElementById("content").insertAdjacentHTML("beforeend", DOMPurify.sanitize(marked.parse(md)));
document.querySelectorAll("pre code.language-mermaid").forEach(c=>{const d=document.createElement("div");d.className="mermaid";d.textContent=c.textContent;c.closest("pre").replaceWith(d);});
mermaid.initialize({startOnLoad:false,theme:"neutral"});
await mermaid.run({querySelector:".mermaid"});
</script></body></html>'''
title=(md.splitlines()[0].lstrip("# ").strip() or "Solution Design")
open(out,"w").write(TPL.replace("__TITLE__",title).replace("__MD__",md))
print("wrote",out)
PY
```

## Notes
- Embed the markdown inside `<script type="text/markdown">` (not a JS template literal) so backticks,
  `$SECRET:`, and ```rego``` fences need no escaping. The only thing to guard is a literal
  `</script>` in the doc (the assert catches it).
- The post-processing loop converts marked's `<pre><code class="language-mermaid">` into
  `<div class="mermaid">` before `mermaid.run` — required, since mermaid looks for `.mermaid` nodes.
- Rendered HTML is `DOMPurify.sanitize`d and inserted with `insertAdjacentHTML` (not a raw `innerHTML`
  assignment). The content is the SA's own trusted markdown, but the sanitised/safe-DOM form is what the
  repo's `security-guidance` hook allows the headless brain to author via the Write tool — a raw
  `innerHTML` sink is blocked there. Load `dompurify` from CDN (already in the template `<head>`).
- Mermaid edge labels: prefer spaced dotted-label form `A -. label .-> B` for widest compatibility.
- Same generator works for any of the SA deliverables that carry diagrams.
