---
name: diagram-design
description: >-
  Create publication-grade, on-brand architectural SVGs, flowcharts, MEDDPICC scorecards,
  quadrant matrices, and HTML diagram companions from Mermaid, Excalidraw, or concept specs
  using native editorial rendering. Trigger when the user says "design diagram", "render
  architecture diagram", "generate SVG schematic", "convert mermaid to SVG", or "make diagram
  for [topic]".
metadata:
  version: "0.1.0"
  phase: "3D"
  capability_tier: core
---
# Diagram Design

Create publication-grade, on-brand architectural SVGs, flowcharts, MEDDPICC scorecards, quadrant matrices, and HTML diagram companions using native editorial rendering.

Forty-one visual types. Semantic patterns describe behavior; type references describe layout.

---

## 1. Execution Procedure

### Step 1: Resolve Brand Tokens
Read active brand palette tokens before generating or styling diagrams:
```bash
uv run python -m gtm_core.brandkit --profile <active> [--product <slug>]
```
Diagrams render with brand-consistent colors (`canvas`, `surface`, `ink`, `primary`, `accent`, `rule`).

### Step 2: Extract from Existing Schematics (Optional)
When redrawing or importing an existing Mermaid (`.mmd`), Excalidraw (`.excalidraw`), or Draw.io file:
```bash
uv run python -m gtm_core.diagrams extract <input-file> --json
```
Extracts normalized nodes, edges, labels, and topology without interpreting untrusted formatting or executing code.

### Step 3: Render Editorial SVG or HTML
Render publication-grade SVGs or self-contained HTML reports directly via deterministic CLI:
```bash
# Render pure SVG with brand tokens:
uv run python -m gtm_core.diagrams render --input <diagram-spec> --out content/<active>/accounts/<account>/architecture.svg --format svg --profile <active>

# Render full editorial HTML report:
uv run python -m gtm_core.diagrams render --input <diagram-spec> --out content/<active>/accounts/<account>/architecture.html --format html --profile <active>
```

---

## 2. Universal Design System & Constraints

1. **4px Grid Geometry:** Node origins, dimensions, gaps, and padding are strictly aligned to the 4px grid.
2. **r=8 Orthogonal Connectors:** Every connector between off-axis nodes uses rounded right-angle elbows with $r=8$. Straight lines are reserved for nodes sharing an axis. Slanted diagonal lines are prohibited.
3. **Connector Label Masking:** Labels have an opaque background rect with a visible 6–10px margin above the connector stroke.
4. **Accessible SVG Contract:** Diagrams include `role="img"`, `aria-labelledby`, prefixed IDs, and descriptive `<title>` / `<desc>`.
5. **No AI-Slop Hallucinations:** Clean editorial styling, Geist sans for names, Geist Mono for technical sublabels (ports/types), Instrument Serif for titles. No glow, no generic cyan/purple dark mode.

---

## 3. Visual Types & Layout References

Consult the specific layout reference when rendering specialized diagram types:
- **Dependency:** [`references/type-dependency.md`](references/type-dependency.md)
- **Deployment:** [`references/type-deployment.md`](references/type-deployment.md)
- **Fishbone:** [`references/type-fishbone.md`](references/type-fishbone.md)
- **Heatmap:** [`references/type-heatmap.md`](references/type-heatmap.md)
- **IT State:** [`references/type-it-state.md`](references/type-it-state.md)
- **User Journey:** [`references/type-journey.md`](references/type-journey.md)
- **Kanban:** [`references/type-kanban.md`](references/type-kanban.md)
- **Feedback Loop:** [`references/type-loop.md`](references/type-loop.md)
- **Polar Chart:** [`references/type-polar.md`](references/type-polar.md)
- **Quadrant Matrix:** [`references/type-quadrant.md`](references/type-quadrant.md)
- **Radar Chart:** [`references/type-radar.md`](references/type-radar.md)
- **Scatter Plot:** [`references/type-scatter.md`](references/type-scatter.md)
- **User Story Map:** [`references/type-story-map.md`](references/type-story-map.md)
- **UML Class:** [`references/type-uml-class.md`](references/type-uml-class.md)
- **Venn Diagram:** [`references/type-venn.md`](references/type-venn.md)
- **Wardley Map:** [`references/type-wardley.md`](references/type-wardley.md)
- **Waterfall Chart:** [`references/type-waterfall.md`](references/type-waterfall.md)

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
