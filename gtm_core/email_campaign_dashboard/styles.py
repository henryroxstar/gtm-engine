"""The status page's stylesheet — one string, emitted verbatim inside ``<style>``.

Moved out of :mod:`.render` (PS20 Task 8) so the page's colour vocabulary lives in one file of
its own: ``render.py`` sat at the §R10 default cap, and a stylesheet is not prose (it is
exempt from ``tests/lint/rendered_prose_check.py`` for the same reason ``render.py`` is).
"""

from __future__ import annotations

STYLESHEET = """
/* PS20 P1.5: every colour on this page is a token defined here, and nowhere else. The four
   meaning tokens: --warn, a number that cannot be trusted; --risk, a genuine risk to a
   person or the brand; --ok, done or passed; --accent, links, the selected tab and .yours
   (it also draws the bars and the note rule, which say nothing). A warn or risk element
   names why: data-warn / data-risk, from config.WARN_REASONS / RISK_REASONS. Everything
   else is neutral. */
:root { --bg:#0f1115; --panel:#171a21; --line:#252a34; --ink:#e8ecf3; --muted:#98a2b3;
        --accent:#7c5cff; --ok:#2ecc71; --warn:#f1c40f; --risk:#e74c3c;
        --ok-bg:rgba(46,204,113,.15); --ok-line:rgba(46,204,113,.4);
        --warn-bg:rgba(241,196,15,.15); --warn-line:rgba(241,196,15,.5);
        --warn-wash:rgba(241,196,15,.06); --risk-bg:rgba(231,76,60,.15);
        --pill-bg:rgba(152,162,179,.15); --wash:rgba(255,255,255,.05);
        --bar-b:#3aa3ff; --bar-c:#4b5364; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 -apple-system,
       BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:28px 20px 70px; }
.wrap { max-width:1080px; margin:0 auto; }
h1 { font-size:23px; margin:0 0 4px; }
h2 { font-size:16px; margin:0 0 14px; letter-spacing:.01em; }
h3 { font-size:14px; margin:24px 0 8px; color:var(--muted); text-transform:uppercase;
     letter-spacing:.06em; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:14px;
        padding:20px 22px; margin-bottom:16px; }
.card.warn { border-color:var(--warn-line); background:var(--warn-wash); }
.card.warn p { margin:.35em 0; }
.card.lede p { margin:.35em 0; }
.card.lede .lede-detail { margin-left:1.4em; }
.card.lede .lede-reason { margin-left:2.8em; }
.card.warn h2 { color:var(--warn); }
.stats { display:flex; flex-wrap:wrap; gap:14px; margin-bottom:16px; }
.stat { background:var(--panel); border:1px solid var(--line); border-radius:12px;
        padding:14px 18px; flex:1 1 190px; }
.stat-value { font-size:27px; font-weight:650; }
.stat-label { font-size:13px; }
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
.steps { margin:8px 0 0; padding-left:20px; } .steps li { margin:7px 0; }
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
.v { flex:1 1 210px; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.v.ok { border-color:var(--ok-line); }
.vn { font-size:26px; font-weight:650; }
.vl { font-size:13px; } .vd { font-size:12px; margin-top:5px; }
.tabs { display:flex; gap:8px; margin:18px 0; flex-wrap:wrap; }
.tab { background:var(--panel); color:var(--muted); border:1px solid var(--line);
       border-radius:999px; padding:9px 20px; font-size:13.5px; cursor:pointer; }
.tab.on { color:var(--ink); border-color:var(--accent); }
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
"""
