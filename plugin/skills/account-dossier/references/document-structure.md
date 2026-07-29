# Account Dossier — Document Structure & docx-js Build Spec

The exact layout for the `.docx`. Rendered by the **committed builder**
`scripts/build_dossier.js` (docx-js) from a declarative JSON spec you compose — the brain is **not**
permitted to run arbitrary `node`, only that one reviewed script (see SKILL Step 6).
Target ~3–4 pages, **hard cap 4**. Sections appear in this order. Replace `[account]`, `[buyer]`,
`[role]`, and `[agents/product]` with the real names.

## JSON spec → block-type mapping

`build_dossier.js` accepts `{ "brand": {…}, "closingLine": "…", "blocks": [ … ] }`. Each section
below maps to one or more declarative blocks (unknown block types are skipped, missing fields use
safe defaults, so the render never crashes on a partial spec):

| Section | Block(s) |
|---|---|
| 1 Title banner | `{"type":"banner","account","subline","meta"}` |
| 2 Read this first | `{"type":"callout","lines":[…],"variant":"info"}` |
| 3 Company at a glance | `{"type":"heading","text"}` + `{"type":"facts_table","rows":[[label,value],…]}` |
| 4 Business & market | `{"type":"heading"}` + `{"type":"paragraph","text"}` ×2 |
| 5 Problem / gap | `heading` + `{"type":"table","header":[…],"rows":[[…]]}` + `paragraph` + `{"type":"image","path":"gap.png","widthIn":6}` |
| 6 Regulatory | `heading` + `paragraph` + `{"type":"callout","variant":"angle"}` |
| 7 Tech stack | `heading` + `facts_table` (+ optional `image` `flow.png`) |
| 8 How to engage | `heading` + `paragraph` + `{"type":"two_col","leftTitle":"DO","rightTitle":"DON'T","left":[…],"right":[…]}` |
| 9 Questions | `heading` + `{"type":"questions","groups":[{"title","items":[…]}]}` |
| 10 Honesty notes | `heading` + `paragraph` |
| 11 Sources | `heading` + `{"type":"sources","external":[{"text","url"}],"internal":[…]}` |
| 12 Footer | automatic — page numbers + `closingLine` on every page |

Brand colours go in `"brand"` (`bg`/`accent1`/`accent2`/`accent3`/`textOnDark`/`font`); the `#` is
optional. Use `{"type":"pagebreak"}` / `{"type":"spacer"}` to manage the page budget below.

---

## Section order

### 1. Branded title banner
A full-width navy banner block (shaded single-cell table, no visible borders) at the top of page 1:
- **[account]** — large, white, bold.
- **"Your meeting with [buyer] — [role]"** — white sub-line.
- The date and **"~4-page read"** — small, in the accent color.
Logo/wordmark optional bottom-right if a brand asset is available.

### 2. Read this first
A **30-second-version callout box** (accent-shaded single-cell table). 3–5 plain sentences:
- Who [account] is and what they do.
- Why we're talking to them.
- Who [buyer] is — specifically what they personally own and care about.

### 3. The company, at a glance
A **two-column facts table** (label | value). Rows:
- What it is (one plain-English sentence)
- Founded / HQ
- Leadership (relevant names + titles)
- Scale (headcount, customers, geographies)
- Funding (last round, amount, date — verified)
- The product / platform
- Why we're here

### 4. The business & the market
**Two short paragraphs** (prose, no table):
- Their position vs. incumbents / competitors.
- Their current strategic narrative (the story they're telling the market right now).

### 5. The problem they're solving with [agents/product]
- A **small table** of the key things in play — e.g. named agents/products: *who they serve* and
  *what they do* (2–4 rows).
- **One paragraph** naming the actual gap **we** close.
- Embed the **"problem / gap" three-card** PNG here (`gap.png`, ~6in wide).

### 6. The regulatory picture
ONLY the frameworks **[buyer] is actually measured against** — stated with **current** accuracy.
- If a deadline has moved or been amended, **say so** and turn it into an honest angle — never
  manufacture a fake-urgency deadline.
- End with a short **"the angle that lands"** callout box.

### 7. Their tech stack
A **facts table** framed "so the seller can hold the conversation." Each row notes **why it matters
to us**. Rows:
- Data model
- Hosting
- Identity
- Observability
- Integration surface

Optionally embed the **process / flow** PNG here (`flow.png`) if it reads better against the stack
than in §5.

### 8. Who [buyer] is — and how to engage
- **One paragraph** on the persona's worldview + what they're allergic to.
- A **DO / DON'T two-column table** (3–4 rows each side).

### 9. Questions to ask
Three grouped sets, **3 questions each**. Questions double as discovery that tells us whether we fit:
- **Open the conversation**
- **Go deeper / test for fit**
- **Test for a pilot / next step**

### 10. Honesty notes
Candid limits to state out loud — what we do vs. don't do, beta / maturity caveats. Builds trust with
technical and legal buyers.

### 11. Sources
A **bulleted list** with **clickable hyperlinks** for every external claim. Internal docs referenced
as **plain text** (no `#` links). Group external vs. internal if helpful.

### 12. Footer
Page numbers in the footer. Closing line: **"Internal briefing — not for customer distribution."**

---

## docx-js styling rules (pitfall guardrails)

- **Page:** US Letter — set `size: { width: 12240, height: 15840 }` (twips) explicitly on the section;
  do not rely on defaults. Reasonable margins (~720–1080 twips) to keep within 4 pages.
- **Tables:** set **both** `columnWidths: [...]` on the table **and** a per-cell `width: { size: <DXA>,
  type: WidthType.DXA }` on every cell. Mismatched/absent widths cause reflow and page overflow.
- **Shading:** use `shading: { type: ShadingType.CLEAR, fill: "<hex>", color: "auto" }` for banner,
  callout, and shaded header rows.
- **Cell padding:** set `margins` (e.g. `{ top: 80, bottom: 80, left: 120, right: 120 }` twips) on
  cells so text isn't flush against borders.
- **Never use a table as a divider rule.** For a section-heading accent rule, use a thin bottom border
  on the heading paragraph (or a short shaded single-row table styled as a rule only if unavoidable) —
  not an empty table standing in for a line.
- **Callout boxes** = a single-cell shaded table with an accent treatment (e.g. accent fill or a thick
  left border), **not** four borders faking a box around body text.
- **Section headings** = brand-blue, **uppercase**, bold, with a thin accent rule beneath.
- **Hyperlinks:** real URLs only via `ExternalHyperlink`. **No `#` placeholder hrefs** — they break
  docx validation. Internal sources stay plain text.
- **Images:** embed PNGs at ~6in wide (`transformation: { width: ~576, height: proportional }` in EMUs
  or points per the docx skill's image helper). Keep within the content width.
- **Fonts:** geometric sans-serif (DM Sans / Inter / Calibri / Montserrat). No serif fonts.

---

## Page budget (keeping it ≤4 pages)

Rough target allocation:
- Page 1: banner (§1) + read-this-first (§2) + company at a glance (§3).
- Page 2: business & market (§4) + problem/gap with `gap.png` (§5).
- Page 3: regulatory (§6) + tech stack (§7).
- Page 4: how to engage (§8) + questions (§9) + honesty notes (§10) + sources (§11) + footer (§12).

If content overflows, tighten prose and trim table rows to the highest-signal items — do **not** drop
a required section.
