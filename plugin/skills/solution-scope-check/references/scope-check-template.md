# Scope-check .docx — structure + python-docx builder (de-branded)

The scope check is a **2-page Word doc**. Page 1 restates the solution; page 2 is the question groups
from `references/question-types.md`. Below: the page structure (a fill-in blank), then a python-docx
**builder skeleton** the brain adapts per account. Neutral default accent — **override the brand
colour** from `profiles/<active>/knowledge/brand/` when one is documented.

## Structure (fill-in blank)

> **Page 1 has two shapes** (see `body_template.md` Step 2). **Post-design** (from a solution design):
> "the solution, simplified" — the blank below. **Pre-design** (from a discovery brief): "what we're
> solving + the direction we're leaning" — same header/kicker/rule/table scaffolding, but the body is
> *what we heard* (the problem) + *the direction we're leaning* (a provisional hypothesis, **not** a
> committed architecture — no component inventory, no firm V1/V2). Page 2 is identical either way.

**Page 1 (post-design) — the solution, simplified**
- **Kicker:** `DRAFT · [SUBJECT] · SCOPE CHECK` (accent, mono, tracked).
- **Title:** e.g. "The solution, simplified — and what we need to confirm".
- **Recipient line:** `For [Buyer Name] ([Role]), [Company]  ·  [product/brand]  ·  draft, [date]`.
- **One-line outcome:** `In one line:  "[before]" → [after]  — [the value in one plain clause].`
  Then a thin accent rule.
- **Draft note (italic):** "Draft — [re-scoped [date] around …]. A companion to the full solution
  design: it restates the proposal in plain terms, then asks the questions that will confirm — or
  reshape — the scope. Mark it up and send it back."
- **What we're proposing (in plain terms):** 4–6 bullets (accent bullet marks; bold the key noun).
- **The N things you asked for → how each is delivered:** a 2-col table, accent-tinted header row,
  bold requirement (left) → plain mechanism (right); one row for the underpinning (provable/auditable).
- **What the POC / V1 covers:** three compact lines — `V1 (…):` / `Later (V2):` / `Not now:`
  (accent label, then the items).

**Page 2 — questions to confirm the scope** (page break)
- **Kicker:** `DRAFT · INPUTS TO VALIDATE · [N] QUESTIONS`.
- **Title:** "Questions to confirm the scope" + one italic intro line.
- **Question groups** (from `question-types.md`): `[n]  [Question title]   → unlocks: [decision]`,
  then `–` dash questions, then a faint italic `Our assumption today: [x]`.
- **Closing line:** "Answer what you can — anything left blank we'll carry as an explicit assumption
  in the next revision, not a silent guess." + a faint rule.
- **Footer:** `[brand] · [subject] · prepared by [byline] · companion to solution-design-[company]-[date]`.

## Builder skeleton (python-docx — adapt, don't run verbatim)

Write a build script to the scratchpad and run it with `uv run python <script>.py`. **Direct run
formatting + Arial on every run** (never override `Normal`/`Heading` styles — they render serif in
Quick Look/Word). Set the brand `ACCENT`/`ACCENTHD` from the profile; keep the neutral defaults below
if none is documented.

```python
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import pathlib

# ---- palette: neutral default; override ACCENT/ACCENTHD from profiles/<active>/knowledge/brand/ ----
NAVY   = RGBColor(0x0B, 0x1A, 0x2E)   # ink for titles
ACCENT = RGBColor(0x3A, 0x5B, 0xD9)   # small accent text (labels, numbers) — legible on white
ACCENTHD = RGBColor(0x3A, 0x5B, 0xD9) # larger accent (rules, arrow)
ACCENT_HEX = "3A5BD9"                  # for borders/shading (rule, table header)
ACCENT_BG  = "EEF1FC"                  # very light accent fill for the table header
INK    = RGBColor(0x20, 0x26, 0x32)
MUTED  = RGBColor(0x5A, 0x61, 0x70)
FAINT  = RGBColor(0x8C, 0x93, 0xA2)

doc = Document()
for s in doc.sections:
    s.top_margin = Inches(0.62); s.bottom_margin = Inches(0.6)
    s.left_margin = Inches(0.8);  s.right_margin = Inches(0.8)
nf = doc.styles["Normal"].font; nf.name = "Arial"; nf.size = Pt(10); nf.color.rgb = INK

def _font(run, name="Arial", size=10, color=INK, bold=False, italic=False):
    run.font.name = name; run.font.size = Pt(size); run.font.color.rgb = color
    run.font.bold = bold; run.font.italic = italic
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts")) or OxmlElement("w:rFonts")
    if rf.getparent() is None: rpr.append(rf)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"): rf.set(qn(a), name)

def para(after=5, before=0, line=1.12):
    p = doc.add_paragraph(); pf = p.paragraph_format
    pf.space_after = Pt(after); pf.space_before = Pt(before); pf.line_spacing = line
    return p

def run(p, text, **kw):
    r = p.add_run(text); _font(r, **kw); return r

def bottom_border(p, color=ACCENT_HEX, sz=12, space=4):
    pPr = p._p.get_or_add_pPr(); bdr = OxmlElement("w:pBdr"); b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single"); b.set(qn("w:sz"), str(sz)); b.set(qn("w:space"), str(space)); b.set(qn("w:color"), color)
    bdr.append(b); pPr.append(bdr)

def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr(); sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear"); sh.set(qn("w:fill"), hex_fill); tcPr.append(sh)

def kicker(text):                       # accent, tracked, small
    p = para(after=2); run(p, text, size=8, color=ACCENT, bold=True)
    sp = OxmlElement("w:spacing"); sp.set(qn("w:val"), "30")
    p.runs[0]._element.get_or_add_rPr().append(sp)

def heading(text):
    p = para(before=9, after=3); run(p, text, size=12, color=NAVY, bold=True); return p

def bullet(segs, after=3):              # segs: list of (text, color, bold?)
    p = para(after=after, line=1.1)
    p.paragraph_format.left_indent = Inches(0.2); p.paragraph_format.first_line_indent = Inches(-0.13)
    run(p, "•  ", size=10, color=ACCENT, bold=True)
    for s in segs: run(p, s[0], size=9.5, color=(s[1] if len(s) > 1 else INK), bold=(s[2] if len(s) > 2 else False))

def qblock(num, title, unlocks, questions, assume=None):
    p = para(before=7, after=2)
    run(p, f"{num}  ", size=11.5, color=ACCENTHD, bold=True)
    run(p, title, size=11.5, color=NAVY, bold=True)
    run(p, f"     → unlocks: {unlocks}", size=8.5, color=ACCENT, bold=True)
    for q in questions:
        pb = para(after=2, line=1.08)
        pb.paragraph_format.left_indent = Inches(0.22); pb.paragraph_format.first_line_indent = Inches(-0.13)
        run(pb, "–  ", size=9.5, color=ACCENT, bold=True); run(pb, q, size=9.5, color=INK)
    if assume:
        pa = para(after=2, line=1.05); pa.paragraph_format.left_indent = Inches(0.22)
        run(pa, "Our assumption today: ", size=8.5, color=FAINT, bold=True, italic=True)
        run(pa, assume, size=8.5, color=FAINT, italic=True)

# ---- PAGE 1 ----  kicker → title → recipient → one-liner+rule → draft note → proposing → table → V1
# ---- (build with the helpers above; keep spacing compact so it fits 2 pages) ----
# ---- PAGE 2 ----  doc.add_page_break(); kicker; title+intro; qblock(...) per group; closing; footer
# doc.save("content/<active>/accounts/<slug>/solution-scope-check-<company>-<date>.docx")
```

## Verify (before calling it done)
- **Page count:** `soffice --headless --convert-to pdf --outdir <tmp> <docx>` then
  `pdfinfo <pdf> | grep Pages` — must be **2**. If 3, tighten the assumption notes or drop the
  lowest-leverage group.
- **Font:** `qlmanage -t -s 1000 -o <tmp> <docx>` and glance at the thumbnail — the body must render
  **Arial (sans-serif)**, not a serif fallback. Quick Look is the true renderer; LibreOffice looking
  right is not enough.

## Notes
- **Brand accent** — replace `ACCENT` / `ACCENTHD` / `ACCENT_HEX` / `ACCENT_BG` with the profile's
  documented brand tokens when present; otherwise the neutral indigo default ships. One accent only;
  navy/near-black ink; no gradient, no colour side-stripes.
- **Two-column table widths** — set both cells (`row.cells[0].width` / `[1].width`) explicitly and
  `table.autofit = False`; python-docx otherwise reflows unpredictably.
- **Draft** — keep `DRAFT` in both kickers and "draft, [date]" in the recipient line until the
  operator explicitly drops it.
