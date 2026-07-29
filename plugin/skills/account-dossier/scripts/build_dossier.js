#!/usr/bin/env node
/*
 * build_dossier.js — committed docx-js renderer for the account-dossier skill.
 *
 * WHY THIS EXISTS (least-privilege / §R8 / ASI05): the brain is NOT allowed to run
 * arbitrary `node <anything>.js` — that is arbitrary code execution. Instead the
 * permission policy (agent/permissions.py) allows `node` for THIS one committed,
 * reviewed script only, exactly the way it allows `python scripts/dossier_visuals.py`.
 * The brain composes a JSON *spec* (structured content — its strength); this script
 * renders it deterministically into a Word document (docx-js). Same split as the deck
 * renderer: brain composes, committed code renders. Do NOT make this script read or
 * `eval` arbitrary code from the spec — it only maps declarative blocks to docx nodes.
 *
 * Usage:  node build_dossier.js <spec.json> <out.docx>
 *
 * Spec shape (all fields optional unless noted; unknown block types are skipped):
 *   {
 *     "brand": { "bg":"#0A0E27","accent1":"#3E7BFA","accent2":"#B57BFF",
 *                "accent3":"#22D3EE","textOnDark":"#FFFFFF","font":"Calibri" },
 *     "closingLine": "Internal briefing — not for customer distribution.",
 *     "blocks": [
 *       {"type":"banner","account":"Acme","subline":"Your meeting with …","meta":"2026-06-24 · ~4-page read"},
 *       {"type":"callout","lines":["…"],"variant":"info|angle"},
 *       {"type":"heading","text":"THE COMPANY, AT A GLANCE"},
 *       {"type":"paragraph","text":"…"},
 *       {"type":"facts_table","rows":[["Founded / HQ","2019 · Singapore"], …]},
 *       {"type":"table","header":["Agent","What it does"],"rows":[["…","…"]]},
 *       {"type":"two_col","leftTitle":"DO","rightTitle":"DON'T","left":["…"],"right":["…"]},
 *       {"type":"questions","groups":[{"title":"Open the conversation","items":["…"]}]},
 *       {"type":"image","path":"gap.png","widthIn":6},
 *       {"type":"sources","external":[{"text":"…","url":"https://…"}],"internal":["…"]},
 *       {"type":"spacer"} | {"type":"pagebreak"}
 *     ]
 *   }
 */

"use strict";

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
  Footer, AlignmentType, BorderStyle, WidthType, ShadingType, VerticalAlign,
  PageNumber, ExternalHyperlink, HeadingLevel,
} = require("docx");

// ── args ──────────────────────────────────────────────────────────────────────
const [specPath, outPath] = process.argv.slice(2);
if (!specPath || !outPath) {
  console.error("usage: node build_dossier.js <spec.json> <out.docx>");
  process.exit(2);
}

let spec;
try {
  spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
} catch (e) {
  console.error(`could not read/parse spec ${specPath}: ${e.message}`);
  process.exit(1);
}

// ── brand defaults (overridable per spec) ──────────────────────────────────────
const B = Object.assign(
  { bg: "0A0E27", accent1: "3E7BFA", accent2: "B57BFF", accent3: "22D3EE",
    textOnDark: "FFFFFF", font: "Calibri" },
  stripHashes(spec.brand || {})
);

function stripHashes(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    out[k] = typeof v === "string" ? v.replace(/^#/, "") : v;
  }
  return out;
}
const hex = (h) => String(h || "").replace(/^#/, "") || "000000";

// US Letter content width (12240 − 2×720 margins) in DXA.
const MARGIN = 720;
const CONTENT_W = 12240 - 2 * MARGIN;

// ── small builders ──────────────────────────────────────────────────────────────
function clearShade(fill) {
  return { type: ShadingType.CLEAR, fill: hex(fill), color: "auto" };
}
const noBorder = { style: BorderStyle.NONE, size: 0, color: "auto" };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder,
  insideHorizontal: noBorder, insideVertical: noBorder };
const cellMargins = { top: 80, bottom: 80, left: 120, right: 120 };

function run(text, opts = {}) {
  return new TextRun(Object.assign({ text: String(text == null ? "" : text), font: B.font }, opts));
}

// A single-cell full-width shaded box (banner / callout).
function shadedBox(children, fill, opts = {}) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    borders: opts.leftBorder
      ? { ...noBorders,
          left: { style: BorderStyle.SINGLE, size: 24, color: hex(opts.leftBorder) } }
      : noBorders,
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: clearShade(fill),
            margins: { top: 160, bottom: 160, left: 200, right: 200 },
            verticalAlign: VerticalAlign.CENTER,
            children,
          }),
        ],
      }),
    ],
  });
}

function heading(text) {
  return new Paragraph({
    spacing: { before: 260, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: hex(B.accent1), space: 1 } },
    children: [run(String(text || "").toUpperCase(), { bold: true, color: hex(B.accent1), size: 26 })],
  });
}

function paragraph(text) {
  return new Paragraph({ spacing: { after: 120 }, children: [run(text, { size: 22 })] });
}

function genericTable(columnWidths, rows) {
  const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
  const borders = { top: border, bottom: border, left: border, right: border };
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths,
    rows: rows.map((r) =>
      new TableRow({
        children: r.cells.map((c, i) =>
          new TableCell({
            width: { size: columnWidths[i], type: WidthType.DXA },
            borders,
            margins: cellMargins,
            shading: c.fill ? clearShade(c.fill) : undefined,
            children: [new Paragraph({ children: c.runs })],
          })
        ),
      })
    ),
  });
}

// ── block renderers ─────────────────────────────────────────────────────────────
function renderBanner(blk) {
  const lines = [
    new Paragraph({ children: [run(blk.account || "", { bold: true, color: hex(B.textOnDark), size: 44 })] }),
  ];
  if (blk.subline) {
    lines.push(new Paragraph({ spacing: { before: 40 },
      children: [run(blk.subline, { color: hex(B.textOnDark), size: 24 })] }));
  }
  if (blk.meta) {
    lines.push(new Paragraph({ spacing: { before: 40 },
      children: [run(blk.meta, { color: hex(B.accent3), size: 18 })] }));
  }
  return [shadedBox(lines, B.bg), new Paragraph({ spacing: { after: 120 } })];
}

function renderCallout(blk) {
  const lines = blk.lines && Array.isArray(blk.lines) ? blk.lines : [blk.text || ""];
  const fill = blk.variant === "angle" ? B.accent2 : B.accent3;
  const kids = lines.map((ln, i) =>
    new Paragraph({ spacing: { after: i === lines.length - 1 ? 0 : 60 },
      children: [run(ln, { size: 22, bold: blk.variant === "angle" })] }));
  return [shadedBox(kids, "F0F4FF", { leftBorder: fill }), new Paragraph({ spacing: { after: 120 } })];
}

function renderFactsTable(blk) {
  const rows = (blk.rows || []).map((pair) => ({
    cells: [
      { runs: [run(pair[0], { bold: true, size: 20 })], fill: "EEF2FB" },
      { runs: [run(pair[1], { size: 20 })] },
    ],
  }));
  if (!rows.length) return [];
  return [genericTable([Math.round(CONTENT_W * 0.32), Math.round(CONTENT_W * 0.68)], rows),
    new Paragraph({ spacing: { after: 120 } })];
}

function renderTable(blk) {
  const cols = (blk.header && blk.header.length) ||
    ((blk.rows && blk.rows[0] && blk.rows[0].length) || 1);
  const w = Math.floor(CONTENT_W / cols);
  const widths = Array(cols).fill(w);
  const rows = [];
  if (blk.header && blk.header.length) {
    rows.push({ cells: blk.header.map((h) => ({ runs: [run(h, { bold: true, color: hex(B.textOnDark), size: 20 })], fill: B.accent1 })) });
  }
  for (const r of blk.rows || []) {
    rows.push({ cells: r.map((c) => ({ runs: [run(c, { size: 20 })] })) });
  }
  if (!rows.length) return [];
  return [genericTable(widths, rows), new Paragraph({ spacing: { after: 120 } })];
}

function renderTwoCol(blk) {
  const half = Math.floor(CONTENT_W / 2);
  const bullet = (items) => (items || []).map((t) =>
    new Paragraph({ spacing: { after: 40 }, bullet: { level: 0 }, children: [run(t, { size: 20 })] }));
  const rows = [
    { cells: [
      { runs: [run(blk.leftTitle || "DO", { bold: true, color: hex(B.textOnDark), size: 20 })], fill: B.accent1 },
      { runs: [run(blk.rightTitle || "DON'T", { bold: true, color: hex(B.textOnDark), size: 20 })], fill: B.accent2 },
    ] },
  ];
  // Build the body row with multi-paragraph cells via a custom table (genericTable is single-paragraph).
  const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const bodyRow = new TableRow({
    children: [
      new TableCell({ width: { size: half, type: WidthType.DXA }, borders, margins: cellMargins,
        children: bullet(blk.left).length ? bullet(blk.left) : [new Paragraph({ children: [run("")] })] }),
      new TableCell({ width: { size: half, type: WidthType.DXA }, borders, margins: cellMargins,
        children: bullet(blk.right).length ? bullet(blk.right) : [new Paragraph({ children: [run("")] })] }),
    ],
  });
  const headerRow = new TableRow({
    children: rows[0].cells.map((c) =>
      new TableCell({ width: { size: half, type: WidthType.DXA }, borders, margins: cellMargins,
        shading: clearShade(c.fill), children: [new Paragraph({ children: c.runs })] })),
  });
  return [new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: [half, half],
    rows: [headerRow, bodyRow] }), new Paragraph({ spacing: { after: 120 } })];
}

function renderQuestions(blk) {
  const out = [];
  for (const g of blk.groups || []) {
    out.push(new Paragraph({ spacing: { before: 80, after: 40 },
      children: [run(g.title || "", { bold: true, size: 22, color: hex(B.accent1) })] }));
    for (const q of g.items || []) {
      out.push(new Paragraph({ spacing: { after: 30 }, numbering: undefined, bullet: { level: 0 },
        children: [run(q, { size: 20 })] }));
    }
  }
  return out;
}

function renderImage(blk) {
  try {
    const abs = path.isAbsolute(blk.path) ? blk.path : path.resolve(path.dirname(specPath), blk.path);
    if (!fs.existsSync(abs)) {
      console.error(`image not found, skipping: ${blk.path}`);
      return [];
    }
    const widthPx = Math.round((blk.widthIn || 6) * 96);
    // Preserve aspect if provided, else assume 16:9-ish card.
    const heightPx = blk.heightIn ? Math.round(blk.heightIn * 96) : Math.round(widthPx * 0.5);
    const ext = (path.extname(abs).replace(".", "").toLowerCase()) || "png";
    return [
      new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80, after: 120 },
        children: [new ImageRun({
          type: ext === "jpg" ? "jpeg" : ext,
          data: fs.readFileSync(abs),
          transformation: { width: widthPx, height: heightPx },
          altText: { title: "Dossier visual", description: "On-brand dossier visual", name: "visual" },
        })] }),
    ];
  } catch (e) {
    console.error(`image render failed (${blk.path}): ${e.message}`);
    return [];
  }
}

function renderSources(blk) {
  const out = [];
  for (const s of blk.external || []) {
    out.push(new Paragraph({ spacing: { after: 30 }, bullet: { level: 0 },
      children: s.url
        ? [new ExternalHyperlink({ link: s.url, children: [run(s.text || s.url, { style: "Hyperlink", size: 18 })] })]
        : [run(s.text || "", { size: 18 })] }));
  }
  for (const t of blk.internal || []) {
    out.push(new Paragraph({ spacing: { after: 30 }, bullet: { level: 0 },
      children: [run(t, { size: 18, italics: true })] }));
  }
  return out;
}

// ── assemble ─────────────────────────────────────────────────────────────────────
const children = [];
for (const blk of spec.blocks || []) {
  if (!blk || typeof blk !== "object") continue;
  switch (blk.type) {
    case "banner": children.push(...renderBanner(blk)); break;
    case "callout": children.push(...renderCallout(blk)); break;
    case "heading": children.push(heading(blk.text)); break;
    case "paragraph": children.push(paragraph(blk.text)); break;
    case "facts_table": children.push(...renderFactsTable(blk)); break;
    case "table": children.push(...renderTable(blk)); break;
    case "two_col": children.push(...renderTwoCol(blk)); break;
    case "questions": children.push(...renderQuestions(blk)); break;
    case "image": children.push(...renderImage(blk)); break;
    case "sources": children.push(...renderSources(blk)); break;
    case "spacer": children.push(new Paragraph({ spacing: { after: 120 } })); break;
    case "pagebreak": children.push(new Paragraph({ pageBreakBefore: true, children: [run("")] })); break;
    default: console.error(`unknown block type, skipping: ${blk.type}`); break;
  }
}

const closing = spec.closingLine || "Internal briefing — not for customer distribution.";
const footer = new Footer({
  children: [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        run(closing + "   ·   Page ", { size: 16, color: "808080" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "808080", font: B.font }),
      ],
    }),
  ],
});

const doc = new Document({
  styles: { default: { document: { run: { font: B.font, size: 22 } } } },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: "bullet", text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 360, hanging: 200 } } } }] },
    ],
  },
  sections: [
    {
      properties: {
        page: { size: { width: 12240, height: 15840 },
          margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN } },
      },
      footers: { default: footer },
      children: children.length ? children : [paragraph("(empty dossier spec)")],
    },
  ],
});

Packer.toBuffer(doc)
  .then((buf) => {
    fs.writeFileSync(outPath, buf);
    console.log(`wrote ${outPath} (${buf.length} bytes, ${children.length} blocks)`);
  })
  .catch((e) => {
    console.error(`docx build failed: ${e.message}`);
    process.exit(1);
  });
