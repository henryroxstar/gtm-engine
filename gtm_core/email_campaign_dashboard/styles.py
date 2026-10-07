"""The status page's stylesheet — one string, emitted verbatim inside ``<style>``.

Moved out of :mod:`.render` (PS20 Task 8) so the page's colour vocabulary lives in one file of
its own: ``render.py`` sat at the §R10 default cap, and a stylesheet is not prose (it is
exempt from ``tests/lint/rendered_prose_check.py`` for the same reason ``render.py`` is).
"""

from __future__ import annotations

from typing import Any


def render_stylesheet(palette: dict[str, Any] | None = None) -> str:
    """Build the stylesheet string, merging tenant brandkit palette tokens into :root."""
    pal = palette or {}
    bg = pal.get("canvas") or "#020617"
    panel = pal.get("surface") or "#0F172A"
    line = "rgba(255, 255, 255, 0.06)"
    ink = pal.get("ink") or "#F8FAFC"
    primary = pal.get("primary") or "#3464FD"
    accent = pal.get("accent") or "#68FAFD"

    root_css = f"""
/* PS20 P1.5 / PRD-041 Next Frontier UI: every colour on this page is a token defined here,
   and nowhere else. Deep canvas {bg}, surface {panel}, accents {primary} and {accent}.
   A warn or risk element names why: data-warn / data-risk, from config.WARN_REASONS /
   RISK_REASONS. Everything else is neutral. */
:root {{ --bg:{bg}; --panel:{panel}; --line:{line}; --ink:{ink}; --muted:#94A3B8;
        --accent:{primary}; --ok:#10B94E; --warn:#F5AB0B; --risk:#EF4444;
        --ok-bg:rgba(16,185,78,.15); --ok-line:rgba(16,185,78,.4);
        --warn-bg:rgba(245,171,11,.15); --warn-line:rgba(245,171,11,.5);
        --warn-wash:rgba(245,171,11,.06); --risk-bg:rgba(239,68,68,.15);
        --pill-bg:rgba(148,163,184,.15); --wash:rgba(255,255,255,.05);
        --bar-b:{primary}; --bar-c:{accent}; --teal:{accent};
        --panel-glass:rgba(15,23,42,.75);
        --glow-blue:rgba(52,100,253,.35); --glow-teal:rgba(104,250,253,.35); }}
"""
    return root_css + _STATIC_STYLESHEET_BODY


_STATIC_STYLESHEET_BODY = """@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@300;400;500;600;700&display=swap');
* { box-sizing:border-box; }
body { margin:0; background-color:var(--bg);
       background-image:
          radial-gradient(50% 60% at 8% 12%, var(--glow-blue) 0%, transparent 60%),
          radial-gradient(40% 50% at 92% 18%, var(--glow-teal) 0%, transparent 60%),
          radial-gradient(50% 60% at 50% 80%, var(--glow-blue) 0%, transparent 60%);
       background-attachment: fixed;
       color:var(--ink); font:15px/1.6 "Figtree",Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:32px 24px 80px; }
.wrap { max-width:1120px; margin:0 auto; }
h1 { font-family:"FatFrank","Figtree",sans-serif; font-size:32px; margin:0 0 6px; font-weight:700; letter-spacing:-0.02em; }
h2 { font-family:"FatFrank","Figtree",sans-serif; font-size:19px; margin:0 0 16px; font-weight:600; letter-spacing:0.01em; color:var(--ink); }
h3 { font-size:13px; margin:24px 0 10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.1em; font-weight:600; }
.card { background:var(--panel-glass); backdrop-filter:blur(24px); -webkit-backdrop-filter:blur(24px);
        border:1px solid var(--line); border-radius:16px; padding:24px 28px; margin-bottom:24px; }
.card.warn { background:linear-gradient(180deg, var(--warn-wash) 0%, var(--panel-glass) 30%); border-top:2px solid var(--warn); border-left:1px solid var(--warn-line); border-right:1px solid var(--warn-line); border-bottom:1px solid var(--warn-line); }
.card.lede { border:1px solid var(--line); }
.card.lede.warn { background:var(--panel-glass); border:1px solid var(--line); }
.card.warn p, .card.risk p { margin:.35em 0; }
.card.risk { border-top:2px solid var(--risk); }
.card.lede p { margin:.4em 0; }
.card.lede .lede-detail { margin:12px 0 6px 0; padding:8px 14px; background:var(--wash); border-left:3px solid var(--warn); border-radius:0 8px 8px 0; font-weight:600; font-size:13.5px; }
.card.lede .lede-reason { margin:4px 0 4px 18px; padding:6px 12px; background:var(--panel-glass); border-left:1px solid var(--line); font-size:13px; color:var(--muted); line-height:1.5; }
.card.lede .lede-line.yours { margin:16px 0 8px 0; padding:14px 18px; background:var(--wash); border:1px solid var(--line); border-left:3px solid var(--teal); border-radius:10px; font-weight:500; font-size:14px; color:var(--ink); line-height:1.5; }
.card.lede .review-sheet-link { color:var(--teal); text-decoration:underline; text-underline-offset:3px; font-weight:600; margin-left:4px; }
.card.lede .review-sheet-link:hover { color:var(--ink); }
.card.warn h2 { color:var(--warn); }
.card.lede h2, .card.lede.warn h2 { color:var(--ink); }
a { color:var(--teal); text-decoration:none; }
a:hover { text-decoration:underline; }
.stats { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:16px; }
.stat { background:var(--wash); border:1px solid var(--line); border-radius:12px;
        padding:18px 22px; flex:1 1 190px; }
.stat-value { font-family:"FatFrank","Figtree",sans-serif; font-size:34px; font-weight:700; color:var(--ink); margin-bottom:6px; letter-spacing:-0.02em; line-height:1; }
.stat-label { font-size:12px; text-transform:uppercase; color:var(--muted); letter-spacing:0.08em; font-weight:600; }
.stat-sub { font-size:12px; color:var(--muted); margin-top:3px; }
table { width:100%; border-collapse:collapse; margin:10px 0; font-size:13.5px;
        display:block; overflow-x:auto; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase;
     letter-spacing:.05em; white-space:nowrap; }
td.num-cell { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.muted { color:var(--muted); }
.note { font-size:13px; color:var(--muted); margin:12px 0 0; padding-left:12px;
        border-left:3px solid var(--accent); }
.note strong { color:var(--ink); }
.pill { font-size:11px; padding:2px 8px; border-radius:999px; white-space:nowrap;
        background:var(--pill-bg); color:var(--ink); }
.pill.ok { background:var(--ok-bg); color:var(--ok); }
.pill.warn { background:var(--warn-bg); color:var(--warn); }
.pill.risk { background:var(--risk-bg); color:var(--risk); }
li.warn { color:var(--warn); }
.yours { color:var(--accent); }
code { background:var(--wash); padding:1px 5px; border-radius:4px; font-size:12.5px; }
.steps { margin:8px 0 0; padding-left:0; list-style:none; } .steps li { margin:10px 0; }
.bars { margin:12px 0 4px; }
.brow { display:flex; align-items:center; gap:12px; margin:7px 0; }
.blabel { flex:0 0 210px; font-size:13px; }
.btrack { flex:1; height:12px; background:var(--wash); border-radius:999px; }
.bfill { height:100%; border-radius:999px; background:var(--accent); }
.bfill.tb { background:var(--bar-b); }
.bfill.tc { background:var(--bar-c); }
.bval { flex:0 0 96px; text-align:right; font-size:13px; font-variant-numeric:tabular-nums; }
.gridwrap { overflow-x:auto; }
table.grid { display:table; width:auto; }
table.grid th { text-transform:none; letter-spacing:0; font-size:12px; }
td.gcell { text-align:center; min-width:104px; border:1px solid var(--line); }
td.gcell.empty { color:var(--line); }
.verdicts { display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 4px; }
.v { flex:1 1 210px; border:1px solid var(--line); border-radius:12px; padding:14px 16px; background:var(--panel-glass); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); transition:transform .2s ease; }
.v:hover { transform:translateY(-2px); border-color:var(--accent); }

.v.ok { border-color:var(--ok-line); }
.vn { font-size:26px; font-weight:650; }
.vl { font-size:13px; } .vd { font-size:12px; margin-top:5px; }
.tabs { display:inline-flex; align-items:center; gap:4px; margin:20px 0 28px; background:var(--wash); border:1px solid var(--line); border-radius:9999px; padding:4px 6px; max-width:100%; overflow-x:auto; }
.tab { background:transparent; color:var(--muted); border:1px solid transparent;
       border-radius:9999px; padding:8px 20px; font-size:13.5px; font-weight:500; cursor:pointer; transition:all 0.18s cubic-bezier(0.16,1,0.3,1); white-space:nowrap; }
.tab:hover { color:var(--ink); background:var(--pill-bg); }
.tab.on { color:var(--ink); background:var(--panel-glass); border-color:var(--line); font-weight:600; box-shadow:0 2px 8px var(--wash); }
.panel { display:none; } .panel.on { display:block; }
details { margin-top:12px; }
summary { cursor:pointer; color:var(--muted); font-size:13px; }
details.ops-group { margin:0 0 14px; }
details.ops-group > summary { font-size:15px; font-weight:600; color:var(--ink);
                              padding:10px 0; }
/* The client-side filter. `[hidden]` is stated explicitly because a <tr> carries a
   table display role that overrides the UA's hidden rule in some engines, and the row
   filter hides table rows. */
[hidden] { display:none !important; }
.filterbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0 0 16px;
             padding:12px 14px; background:var(--panel); border:1px solid var(--line);
             border-radius:12px; }
.flabel { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
          color:var(--muted); }
.ffield { display:flex; align-items:center; gap:6px; font-size:12.5px; color:var(--muted); }
.ffield select { background:var(--bg); color:var(--ink); border:1px solid var(--line);
                 border-radius:8px; padding:6px 10px; font-size:13px; }
/* A facet with one value states it rather than offering a choice: no border, no caret,
   so it does not read as a control that is merely broken. */
.ffield.fixed { color:var(--muted); }
.ffield.fixed strong { color:var(--ink); font-weight:600; }
#filter-clear { background:none; color:var(--accent); border:1px solid var(--line);
                border-radius:999px; padding:6px 14px; font-size:12.5px; cursor:pointer; }
.stale { opacity:.45; }
/* Muted, not warn: filter.js hides these on load, so a warning must never reuse them. */
.stat-why, .why { font-size:12px; color:var(--muted); margin-top:6px; line-height:1.45; }
/* PS14 — the technical-detail toggle. `.tech` marks a column that names the pipeline's
   own machine vocabulary (a lane, a verdict word, a hold trigger) rather than the plain
   sentence a reader acts on. Hidden by default; the checkbox below flips one body class,
   never a per-column one, so no view module has to know the toggle exists. */
.tech { display:none; }
body.technical-detail-on .tech { display:table-cell; }
.techtoggle { display:flex; align-items:center; gap:7px; font-size:12.5px;
              color:var(--muted); margin:0 0 14px; cursor:pointer; }
.techtoggle input { cursor:pointer; }

/* PRD-041: Next Frontier UI Micro-Interactions, Halos, Glassmorphism & Keyframes */
@keyframes _showcaseSweep {
  0% { transform:translateX(-100%); }
  50% { transform:translateX(100%); }
  100% { transform:translateX(100%); }
}
.showcase-sweep { position:relative; overflow:hidden; }
.showcase-sweep::after { content:""; position:absolute; top:0; left:0; right:0; height:2px;
  background:linear-gradient(90deg, transparent, var(--accent), var(--teal), transparent);
  animation:_showcaseSweep 4s ease-in-out infinite; }

@keyframes animUpLg {
  from { opacity:0; transform:translateY(16px); }
  to { opacity:1; transform:translateY(0); }
}
.animate-on-load.anim-up-lg { animation:animUpLg .6s cubic-bezier(.16,1,.3,1) both;
  animation-delay:var(--anim-delay, 0ms); }

.glass-panel { background:var(--panel-glass); backdrop-filter:blur(24px);
  -webkit-backdrop-filter:blur(24px); border:1px solid var(--line); border-radius:16px; }

.glow-card { position:relative; overflow:hidden; }
.atmospheric-glow { position:absolute; top:-40px; right:-40px; width:180px; height:180px;
  border-radius:999px; background:radial-gradient(circle, var(--glow-blue) 0%, transparent 70%);
  pointer-events:none; }

.sc-halo { fill:none; stroke:var(--teal); stroke-width:1.5; opacity:.6;
  transition:all .3s ease; transform-origin:center; }
.sc-node:hover .sc-halo { stroke:var(--accent); opacity:1; transform:scale(1.3); }
.sc-core { fill:var(--accent); transition:fill .2s ease; }
.sc-node:hover .sc-core { fill:var(--teal); }
.sc-label { fill:var(--muted); font-size:11px; }

@keyframes marquee {
  0% { transform:translateY(0%); }
  100% { transform:translateY(-50%); }
}
.sentiment-marquee-container { overflow:hidden; height:220px; position:relative; }
.sentiment-marquee-track { display:flex; flex-direction:column; gap:10px;
  animation:marquee 25s linear infinite; }
.sentiment-marquee-track:hover { animation-play-state:paused; }
.sentiment-item { background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:10px 14px; }
.sentiment-meta { display:flex; justify-content:space-between; font-size:12px; margin-bottom:4px; }
.sentiment-body { font-size:13px; color:var(--ink); line-height:1.4; }

.action-prompt-card { margin-top:14px; padding:14px 18px; border-radius:12px;
  border:1px solid var(--line); }
.action-prompt-row { display:flex; align-items:center; justify-content:space-between;
  gap:12px; flex-wrap:wrap; }
.action-prompt-copy-box { display:flex; align-items:center; gap:10px; background:var(--wash);
  padding:6px 12px; border-radius:8px; border:1px dashed var(--line); }
.action-prompt-text { font-family:monospace; font-size:13px; color:var(--teal); user-select:all; }
.copy-btn { background:var(--panel); color:var(--teal); border:1px solid var(--line);
  border-radius:6px; padding:6px 14px; font-size:12.5px; font-weight:500; cursor:pointer;
  transition:all .2s ease; min-height:36px; display:inline-flex; align-items:center; }
.copy-btn:hover { border-color:var(--teal); color:var(--ink); }
.copy-btn.copied { background:var(--ok-bg); border-color:var(--ok-line); color:var(--ok); }

.hyp { background:var(--wash); border:1px solid var(--line); border-radius:12px;
  padding:16px 18px; margin-bottom:14px; display:flex; flex-direction:column; gap:8px; }
.hyphead { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.hyphead b { color:var(--accent); font-size:13px; font-weight:700; letter-spacing:.03em; }
.hyphead .claim { font-size:15px; font-weight:600; color:var(--ink); flex:1 1 200px; }
.hypbody { color:var(--ink); font-size:13.5px; line-height:1.55; opacity:.9; }
.hypneed { font-size:12.5px; color:var(--muted); border-top:1px solid var(--line);
  padding-top:8px; margin-top:4px; line-height:1.5; }
.hypneed b { color:var(--teal); font-weight:600; }
.hyp-deep { margin-top:8px; border-top:1px dashed var(--line); padding-top:8px; }
.hyp-deep summary { font-size:12px; color:var(--teal); cursor:pointer; font-weight:500; }
.hyp-deep summary:hover { color:var(--accent); }
.hyp-deep-body { margin-top:8px; font-size:12.5px; line-height:1.6; color:var(--muted);
  background:var(--wash); padding:10px 14px; border-radius:8px; }
.scope-box { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:14px; }
.scope-col { border-radius:10px; padding:12px 14px; }
.scope-col.ok { background:var(--ok-bg); border:1px solid var(--ok-line); }
.scope-col.risk { background:var(--risk-bg); border:1px solid var(--line); }
.scope-col strong { display:block; margin-bottom:6px; font-size:13px; }
.scope-col.ok strong { color:var(--ok); }
.scope-col.risk strong { color:var(--risk); }
.scope-col p { margin:0; font-size:13px; line-height:1.5; color:var(--ink); }

/* PMF Signal Banner & Funnel (Results Tab) */
.bottom-line-banner {
  padding:14px 20px;
  border-radius:12px;
  font-size:14.5px;
  margin-bottom:20px;
  font-weight:500;
  display:flex;
  align-items:center;
  gap:12px;
  background:var(--panel-glass);
  backdrop-filter:blur(16px);
  -webkit-backdrop-filter:blur(16px);
  border:1px solid var(--line);
}
.signal-strong { background:var(--ok-bg); border:1px solid var(--ok-line); color:var(--ink); }
.signal-medium { background:var(--warn-wash); border:1px solid var(--warn-line); color:var(--ink); }
.signal-early { background:var(--pill-bg); border:1px solid var(--line); color:var(--ink); }
.signal-waiting { background:var(--wash); border:1px solid var(--line); color:var(--muted); }

.pmf-funnel {
  display:flex;
  gap:14px;
  margin-bottom:24px;
  padding:20px 24px;
  background:var(--panel-glass);
  backdrop-filter:blur(24px);
  -webkit-backdrop-filter:blur(24px);
  border:1px solid var(--line);
  border-radius:16px;
  overflow-x:auto;
}
.funnel-stage {
  flex:1;
  background:var(--wash);
  border:1px solid var(--line);
  border-radius:12px;
  padding:16px 20px;
  text-align:left;
  min-width:140px;
  position:relative;
}
.funnel-step {
  display:inline-block;
  padding:12px 18px;
  margin:4px;
  background:var(--wash);
  border:1px solid var(--line);
  border-radius:12px;
  text-align:center;
}
.funnel-metric {
  font-family:"FatFrank","Figtree",sans-serif;
  font-size:36px;
  font-weight:700;
  color:var(--ink);
  margin-bottom:6px;
  letter-spacing:-0.02em;
  line-height:1;
}
.funnel-label-pmf {
  font-size:14px;
  font-weight:600;
  color:var(--teal);
  letter-spacing:0.02em;
  margin-bottom:2px;
}
.funnel-label-sales {
  font-size:11.5px;
  color:var(--muted);
  text-transform:uppercase;
  letter-spacing:0.06em;
}

/* Campaign Snapshot under Overview */
.campaign-snapshot {
  margin: 8px 0 16px 0;
  padding: 12px 16px;
  background: var(--wash);
  border-left: 2px solid var(--accent);
  border-radius: 0 10px 10px 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.55;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.campaign-snapshot-line { display: flex; gap: 12px; align-items: baseline; }
.campaign-snapshot-line strong { min-width: 125px; flex-shrink: 0; color: var(--ink); font-weight: 600; }

/* Actions Required Widget */
.action-item-box {
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.action-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.action-item-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}
.action-detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.5;
}
.action-detail-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.action-detail-col strong {
  color: var(--teal);
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.action-detail-col span {
  color: var(--ink);
  opacity: 0.9;
}
.action-links {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 2px;
  flex-wrap: wrap;
}

/* Roster Toolbar & Controls (Accounts Tab) */
.roster-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 16px 0 18px;
}
.roster-search-bar {
  position: relative;
  max-width: 480px;
}
.roster-search-icon {
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
  pointer-events: none;
  font-size: 14px;
}
.roster-search-input {
  width: 100%;
  padding: 9px 36px 9px 38px;
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--ink);
  font-family: inherit;
  font-size: 13.5px;
  outline: none;
  transition: border-color .2s;
}
.roster-search-input:focus {
  border-color: var(--accent);
}
.roster-search-input::placeholder {
  color: var(--muted);
  opacity: 0.8;
}
.roster-search-clear {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--muted);
  font-size: 16px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.roster-search-clear:hover {
  color: var(--ink);
}
.roster-pills {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.roster-pill {
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--wash);
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all .2s ease;
  user-select: none;
}
.roster-pill:hover {
  background: var(--pill-bg);
  color: var(--ink);
}
.roster-pill.active {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
  font-weight: 600;
}
th.sortable {
  cursor: pointer;
  user-select: none;
  transition: color .15s ease;
}
th.sortable:hover {
  color: var(--ink);
}
.sort-indicator {
  display: inline-block;
  margin-left: 4px;
  font-size: 10px;
  color: var(--teal);
}
.sort-indicator.dim {
  color: var(--muted);
  opacity: 0.4;
}
.roster-empty-notice {
  text-align: center;
  padding: 32px 16px;
  color: var(--muted);
  font-size: 14px;
}

/* Email Variants Toolbar & Controls */
.email-toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 16px 0 18px;
}
.email-search-bar {
  position: relative;
  max-width: 480px;
}
.email-search-icon {
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
  pointer-events: none;
  font-size: 14px;
}
.email-search-input {
  width: 100%;
  padding: 9px 36px 9px 38px;
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--ink);
  font-family: inherit;
  font-size: 13.5px;
  outline: none;
  transition: border-color .2s;
}
.email-search-input:focus {
  border-color: var(--accent);
}
.email-search-input::placeholder {
  color: var(--muted);
  opacity: 0.8;
}
.email-search-clear {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--muted);
  font-size: 16px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.email-search-clear:hover {
  color: var(--ink);
}
.email-pills {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.email-pill {
  padding: 6px 14px;
  border-radius: 999px;
  background: var(--wash);
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all .2s ease;
  user-select: none;
}
.email-pill:hover {
  background: var(--pill-bg);
  color: var(--ink);
}
.email-pill.active {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
  font-weight: 600;
}

/* Campaign Card Visual Hierarchy */
.campaign-card {
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 18px 22px;
  margin-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color .2s ease;
}
.campaign-card:hover {
  border-color: var(--accent);
}
.campaign-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.campaign-card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 10px;
}
.campaign-card-metrics {
  font-size: 13px;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 12px;
}
.campaign-card-metrics strong {
  color: var(--ink);
}

/* Visual Lede Status Progress & Bottleneck Cards */
.lede-progress-card {
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px 20px;
  margin: 16px 0 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.lede-progress-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.lede-progress-bar {
  display: flex;
  height: 12px;
  border-radius: 9999px;
  overflow: hidden;
  background: var(--pill-bg);
  gap: 2px;
}
.lede-progress-seg {
  height: 100%;
  transition: width .3s ease;
}
.lede-progress-seg.seg-live {
  background: var(--ok);
}
.lede-progress-seg.seg-yours {
  background: var(--accent);
}
.lede-progress-seg.seg-held {
  /* Neutral, not warn: "being reworked" is a queue state, not a number the operator
     should distrust — a warn colour with no data-warn reason is exactly what
     tests/contracts/test_dashboard_colour_reasons.py refuses to let through. */
  background: var(--muted);
}
.lede-progress-seg.seg-staging {
  background: var(--teal);
}
.lede-progress-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 12px;
  color: var(--muted);
}
.lede-progress-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.lede-progress-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  display: inline-block;
}

/* Lens Toolbar & Strategic Intelligence Pills */
.lens-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 24px;
  padding: 12px 18px;
  background: var(--wash);
  border: 1px solid var(--line);
  border-radius: 14px;
}
.lens-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.lens-pills {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.lens-pill {
  padding: 6px 14px;
  border-radius: 9999px;
  background: var(--panel-glass);
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all .2s cubic-bezier(0.16, 1, 0.3, 1);
  user-select: none;
}
.lens-pill:hover {
  background: var(--pill-bg);
  color: var(--ink);
}
.lens-pill.active {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
  font-weight: 600;
  box-shadow: 0 2px 10px var(--glow-blue);
}
.insight-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.insight-card-header h2 {
  margin: 4px 0 0 0;
  font-size: 17px;
  color: var(--ink);
}
.insight-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.insight-card-body {
  font-size: 13.5px;
  color: var(--ink);
  line-height: 1.6;
  opacity: 0.95;
}
.insight-card-body p {
  margin: 6px 0;
}
.insight-card-body strong {
  color: var(--teal);
}
"""

STYLESHEET: str = render_stylesheet()
