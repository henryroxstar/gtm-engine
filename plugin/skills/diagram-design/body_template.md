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

## 2.5 Animation-ready diagrams

A diagram that will be revealed piece by piece — in a talk, a walkthrough, an animated explainer —
is drawn for the order a reader meets it, not only for the finished picture. When the request says
the diagram will be built up or animated, add these four properties. They cost nothing on a static
diagram, so apply them whenever there is doubt.

1. **Stable, meaningful ids.** Every node, edge and group in the spec has an id that names the thing
   (`policy_engine`, not `n3`). The renderer carries it onto the SVG (`data-node-id`, `data-edge`,
   `data-group-id`), and that is what a reveal targets. Keep ids unique, use only letters, digits
   and `_ . : -`, and **never rename an id between revisions**: a renamed id breaks every build
   order that named it.
2. **One idea per element.** A box that carries two ideas cannot appear at the moment only one of
   them is being explained. Split it.
3. **A declared build order.** Write the order as a plain list of ids beside the spec — the first
   thing the reader must understand first, the payoff last. An element left off the list is shown
   from the start, so leave off only what is genuinely background.
4. **Short labels.** Two to four words per node; move detail to `<desc>` or the caption. A label the
   reader cannot take in at the pace the diagram builds is a label that is skipped.

Colour still comes from the brand kit (Step 1), including its `[design_system]` policy: keep the
accent to lines, highlights and the element being introduced unless the kit allows accent fills,
and put no background image behind the diagram when the kit turns background plates off.

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

