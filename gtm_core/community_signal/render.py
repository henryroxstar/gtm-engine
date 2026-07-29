"""Render a validated signal model → a single self-contained HTML dashboard.

Zero external assets (no CDN, fonts, images, or scripts fetched) — the whole page is one
inline ``<style>`` + inline HTML + one tiny theme-toggle script, so it renders offline and
passes the repo's headless-Write security posture. Theme-aware: dark-first CSS variables
plus ``prefers-color-scheme`` and an explicit ``data-theme`` toggle.

Every string that can derive from an untrusted match flows through ``esc`` / ``safe_url``
(see :mod:`gtm_core.community_signal.model`); the renderer never interpolates a raw model
string into HTML. Deterministic: no ``datetime.now()`` — the run date comes from the model.

CLI::

    python -m gtm_core.community_signal.render <model.json> --out <report.html>
    python -m gtm_core.community_signal.render --profile vtr <model.json> --out content/vtr/community-signals/2026-07-22/report.html

``--profile`` is a misroute guard (same contract as ``gtm_core.community_signal.score``):
when given, ``--out`` (and ``model``, if it's a file path) must resolve under
``content/<profile>/`` when they resolve under the content root at all — otherwise the CLI
refuses (exit 2) rather than writing a dashboard into the wrong tenant's tree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..paths import PathConfig
from .model import (
    NEUTRAL,
    esc,
    is_light,
    load_model,
    resolve_category_colors,
    safe_url,
)

# --------------------------------------------------------------------------- #
# Static assets (lifted from the hand-authored dashboard, kept generic)
# --------------------------------------------------------------------------- #

_CSS = """
  :root{
    --plane:#0d0d0d; --surface:#161615; --surface-2:#1c1c1a; --raised:#212120;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#8b8a84; --faint:#6a6963;
    --hair:rgba(255,255,255,.10); --hair-2:rgba(255,255,255,.06);
    --accent:#3987e5; --accent-soft:rgba(57,135,229,.14);
    --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
    --shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 8px 30px rgba(0,0,0,.35);
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  }
  @media (prefers-color-scheme: light){
    :root{
      --plane:#f4f3ef; --surface:#fcfcfb; --surface-2:#f8f7f3; --raised:#ffffff;
      --ink:#0b0b0b; --ink-2:#38372f; --muted:#6c6a63; --faint:#8f8d84;
      --hair:rgba(11,11,11,.12); --hair-2:rgba(11,11,11,.06);
      --accent:#1f6fd0; --accent-soft:rgba(42,120,214,.10);
      --shadow:0 1px 0 rgba(255,255,255,.6) inset, 0 10px 30px rgba(11,11,11,.08);
    }
  }
  :root[data-theme="light"]{
    --plane:#f4f3ef; --surface:#fcfcfb; --surface-2:#f8f7f3; --raised:#ffffff;
    --ink:#0b0b0b; --ink-2:#38372f; --muted:#6c6a63; --faint:#8f8d84;
    --hair:rgba(11,11,11,.12); --hair-2:rgba(11,11,11,.06);
    --accent:#1f6fd0; --accent-soft:rgba(42,120,214,.10);
    --shadow:0 1px 0 rgba(255,255,255,.6) inset, 0 10px 30px rgba(11,11,11,.08);
  }
  :root[data-theme="dark"]{
    --plane:#0d0d0d; --surface:#161615; --surface-2:#1c1c1a; --raised:#212120;
    --ink:#ffffff; --ink-2:#c3c2b7; --muted:#8b8a84; --faint:#6a6963;
    --hair:rgba(255,255,255,.10); --hair-2:rgba(255,255,255,.06);
    --accent:#3987e5; --accent-soft:rgba(57,135,229,.14);
    --shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 8px 30px rgba(0,0,0,.35);
  }
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0; background:var(--plane); color:var(--ink); font-family:var(--sans); line-height:1.5; letter-spacing:-.01em; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility}
  .wrap{max-width:1120px; margin:0 auto; padding:0 24px}
  a{color:inherit}
  h1,h2,h3{letter-spacing:-.02em; line-height:1.12; margin:0}
  .mono{font-family:var(--mono)}
  .tnum{font-variant-numeric:tabular-nums}
  .topbar{position:sticky; top:0; z-index:50; background:color-mix(in srgb,var(--plane) 82%, transparent); backdrop-filter:saturate(1.4) blur(10px); border-bottom:1px solid var(--hair)}
  .topbar .wrap{display:flex; align-items:center; gap:16px; height:52px}
  .brandmark{display:flex; align-items:center; gap:9px; font-weight:640; font-size:13px; letter-spacing:.02em}
  .dotpulse{width:8px; height:8px; border-radius:50%; background:var(--good); box-shadow:0 0 0 0 rgba(12,163,12,.6); animation:pulse 2.4s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(12,163,12,.45)}70%{box-shadow:0 0 0 7px rgba(12,163,12,0)}100%{box-shadow:0 0 0 0 rgba(12,163,12,0)}}
  .topbar .spacer{flex:1}
  .topbar .meta{font-family:var(--mono); font-size:11px; color:var(--muted); letter-spacing:.02em}
  .toggle{appearance:none; border:1px solid var(--hair); background:var(--surface-2); color:var(--ink-2); font-family:var(--mono); font-size:11px; padding:6px 10px; border-radius:8px; cursor:pointer}
  .toggle:hover{border-color:var(--accent); color:var(--ink)}
  .mast{padding:56px 0 30px; border-bottom:1px solid var(--hair)}
  .kicker{font-family:var(--mono); font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); margin-bottom:18px; display:flex; gap:10px; align-items:center; flex-wrap:wrap}
  .kicker .pip{width:22px; height:1px; background:var(--accent); opacity:.6}
  .mast h1{font-size:clamp(30px,5vw,52px); font-weight:680; max-width:22ch}
  .mast .dek{margin-top:18px; font-size:clamp(16px,2vw,19px); color:var(--ink-2); max-width:72ch; line-height:1.5}
  .metabar{margin-top:26px; display:flex; flex-wrap:wrap; gap:0; border:1px solid var(--hair); border-radius:12px; overflow:hidden; background:var(--surface)}
  .metabar div{flex:1 1 140px; padding:12px 16px; border-right:1px solid var(--hair-2)}
  .metabar div:last-child{border-right:0}
  .metabar .k{font-family:var(--mono); font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint)}
  .metabar .v{margin-top:3px; font-size:14px; color:var(--ink); font-weight:560}
  section{padding:46px 0; border-bottom:1px solid var(--hair)}
  .shead{display:flex; align-items:baseline; gap:14px; margin-bottom:26px}
  .shead .num{font-family:var(--mono); font-size:12px; color:var(--accent); letter-spacing:.06em; padding-top:4px}
  .shead h2{font-size:clamp(21px,2.6vw,27px); font-weight:640}
  .shead .sub{color:var(--muted); font-size:14px; margin-top:5px; max-width:80ch}
  .bluf{background:linear-gradient(180deg,var(--surface),var(--surface-2)); border:1px solid var(--hair); border-radius:16px; padding:28px; box-shadow:var(--shadow)}
  .bluf .lead{font-size:clamp(17px,2.2vw,21px); line-height:1.5; color:var(--ink); max-width:82ch; font-weight:520}
  .pillars{display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:26px}
  .pillar{background:var(--plane); border:1px solid var(--hair); border-radius:12px; padding:18px; position:relative; overflow:hidden}
  .pillar::before{content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--pc,var(--accent))}
  .pillar .pk{font-family:var(--mono); font-size:10.5px; letter-spacing:.09em; text-transform:uppercase; color:var(--muted)}
  .pillar h3{font-size:16px; margin:8px 0 6px; font-weight:620}
  .pillar p{font-size:13.5px; color:var(--ink-2); margin:0; line-height:1.5}
  .kpis{display:grid; grid-template-columns:repeat(5,1fr); gap:12px}
  .kpi{background:var(--surface); border:1px solid var(--hair); border-radius:12px; padding:16px 16px 15px}
  .kpi .val{font-size:clamp(24px,3.2vw,32px); font-weight:680; letter-spacing:-.03em; line-height:1}
  .kpi .lab{margin-top:9px; font-size:12px; color:var(--muted); line-height:1.35}
  .kpi .foot{margin-top:8px; font-family:var(--mono); font-size:10.5px; color:var(--faint)}
  .kpi.acc .val{color:var(--accent)}
  .momentum{display:grid; grid-template-columns:1fr 1fr; gap:16px}
  .mcard{background:var(--surface); border:1px solid var(--hair); border-radius:14px; padding:22px 24px}
  .mcard .mh{font-size:15px; font-weight:620; margin-bottom:4px}
  .mcard .msub{font-size:12.5px; color:var(--muted); margin-bottom:16px; line-height:1.45}
  .mrow{display:grid; grid-template-columns:150px 56px 1fr; gap:12px; align-items:center; padding:10px 0; border-top:1px solid var(--hair-2)}
  .mrow:first-of-type{border-top:0}
  .mrow .mn{font-size:13px; color:var(--ink); font-weight:560}
  .mrow .mn small{display:block; font-family:var(--mono); font-size:9.5px; color:var(--faint); font-weight:400; letter-spacing:.02em}
  .spark{display:flex; align-items:flex-end; gap:4px; height:36px}
  .spark b{width:11px; background:var(--faint); border-radius:2px 2px 0 0; display:block; min-height:3px}
  .spark b.last{background:var(--accent)}
  .spark.up b.last{background:var(--good)}
  .spark.fx b.last{background:var(--warn)}
  .spark.cool b.last{background:var(--muted)}
  .mtrend{font-family:var(--mono); font-size:11px; font-weight:640; font-variant-numeric:tabular-nums; color:var(--ink-2)}
  .chartcard{background:var(--surface); border:1px solid var(--hair); border-radius:14px; padding:22px 24px}
  .chart-top{display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; margin-bottom:8px}
  .chart-top .ct{font-size:15px; font-weight:620}
  .legend{display:flex; gap:14px; flex-wrap:wrap; font-family:var(--mono); font-size:11px; color:var(--muted)}
  .legend span{display:inline-flex; align-items:center; gap:6px}
  .legend i{width:10px; height:10px; border-radius:3px; display:inline-block}
  .bars{margin-top:16px; display:flex; flex-direction:column; gap:9px}
  .bar{display:grid; grid-template-columns:170px 1fr; gap:12px; align-items:center}
  .bar .name{font-size:12.5px; color:var(--ink-2); text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
  .bar .name .cat{font-family:var(--mono); font-size:9.5px; color:var(--faint); display:block; letter-spacing:.03em}
  .track{position:relative; height:24px; background:var(--hair-2); border-radius:5px; overflow:hidden}
  .fill{position:absolute; left:0; top:0; bottom:0; border-radius:5px 4px 4px 5px; display:flex; align-items:center; justify-content:flex-end; padding-right:8px; min-width:26px}
  .fill span{font-family:var(--mono); font-size:11px; font-weight:640; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.35); font-variant-numeric:tabular-nums}
  .fill.lite span{color:#0b0b0b; text-shadow:none}
  .split2{display:grid; grid-template-columns:1.15fr .85fr; gap:16px}
  .stack{margin-top:6px; display:flex; height:44px; border-radius:9px; overflow:hidden; gap:2px; background:var(--plane)}
  .seg{display:flex; align-items:center; justify-content:center; min-width:22px; position:relative}
  .seg span{font-family:var(--mono); font-size:11px; font-weight:640; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.4)}
  .seg.lite span{color:#0b0b0b; text-shadow:none}
  .stack-key{margin-top:14px; display:flex; flex-direction:column; gap:9px}
  .skrow{display:flex; align-items:center; gap:10px; font-size:13px; color:var(--ink-2)}
  .skrow i{width:11px; height:11px; border-radius:3px; flex:none}
  .skrow .n{margin-left:auto; font-family:var(--mono); color:var(--ink); font-variant-numeric:tabular-nums}
  .plat{display:flex; flex-direction:column; gap:8px; margin-top:6px}
  .prow{display:grid; grid-template-columns:130px 1fr 44px; gap:10px; align-items:center; font-size:12.5px}
  .prow .pn{color:var(--ink-2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
  .ptrack{height:8px; background:var(--hair-2); border-radius:4px; overflow:hidden}
  .pfill{height:100%; background:var(--muted); border-radius:4px}
  .prow .pv{font-family:var(--mono); color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; font-size:11px}
  .prow.jx .pn{color:var(--serious)} .prow.jx .pfill{background:var(--serious); opacity:.6}
  .prow.lx .pn{color:var(--crit)} .prow.lx .pfill{background:var(--crit); opacity:.5}
  .signals{display:grid; grid-template-columns:1fr 1fr; gap:14px}
  .sig{background:var(--surface); border:1px solid var(--hair); border-radius:13px; padding:18px 19px; display:flex; flex-direction:column; gap:10px}
  .sig:hover{border-color:var(--accent)}
  .sig .tag{align-self:flex-start; font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; padding:3px 8px; border-radius:6px; font-weight:600}
  .t-open{background:rgba(12,163,12,.14); color:var(--good)}
  .t-threat{background:rgba(208,59,59,.14); color:var(--crit)}
  .t-demand{background:var(--accent-soft); color:var(--accent)}
  .t-white{background:rgba(201,133,0,.16); color:#c98500}
  .t-move{background:rgba(139,138,132,.16); color:var(--muted)}
  .sig h3{font-size:15.5px; font-weight:620; line-height:1.28}
  .sig p{margin:0; font-size:13px; color:var(--ink-2); line-height:1.5}
  .sig .why{font-size:13px; color:var(--ink); line-height:1.5}
  .sig .src{margin-top:auto; font-family:var(--mono); font-size:11px; color:var(--faint); display:flex; align-items:center; gap:7px; padding-top:6px; border-top:1px solid var(--hair-2); text-decoration:none; word-break:break-word}
  .sig .src:hover{color:var(--accent)}
  .sig .src .arrow{color:var(--accent)}
  .moves{display:flex; flex-direction:column; gap:12px}
  .move{display:grid; grid-template-columns:44px 1fr; gap:16px; background:var(--surface); border:1px solid var(--hair); border-radius:12px; padding:18px 20px}
  .move .ic{width:44px; height:44px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-family:var(--mono); font-weight:700; font-size:14px; color:#fff}
  .move .body h3{font-size:15.5px; font-weight:620; margin-bottom:5px}
  .move .body h3 .who{font-family:var(--mono); font-size:11px; font-weight:500; color:var(--muted); margin-left:8px}
  .move .body p{margin:0; font-size:13.5px; color:var(--ink-2); line-height:1.5}
  .sowhat{display:grid; grid-template-columns:1fr 1fr; gap:16px}
  .playbook{background:var(--surface); border:1px solid var(--hair); border-radius:14px; padding:22px 24px}
  .playbook .ph{display:flex; align-items:center; gap:10px; margin-bottom:16px}
  .playbook .ph .badge{font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:.06em; padding:5px 10px; border-radius:7px; color:#fff}
  .playbook .ph .role{font-size:15px; font-weight:640}
  .playbook ol{margin:0; padding:0; list-style:none; counter-reset:p; display:flex; flex-direction:column; gap:14px}
  .playbook li{counter-increment:p; display:grid; grid-template-columns:26px 1fr; gap:12px}
  .playbook li::before{content:counter(p,decimal-leading-zero); font-family:var(--mono); font-size:11px; color:var(--accent); padding-top:2px}
  .playbook li .pt{font-size:14px; color:var(--ink); font-weight:580; line-height:1.35}
  .playbook li .pd{font-size:13px; color:var(--muted); line-height:1.5; margin-top:2px}
  .fsug{display:flex; flex-direction:column; gap:12px}
  .fs{background:var(--surface); border:1px solid var(--hair); border-radius:13px; padding:18px 20px; display:flex; flex-direction:column; gap:9px}
  .fs .fshead{display:flex; align-items:center; gap:10px; flex-wrap:wrap}
  .fs .act{font-family:var(--mono); font-size:10px; letter-spacing:.06em; text-transform:uppercase; padding:3px 8px; border-radius:6px; font-weight:700; color:#fff}
  .fs .act.add{background:var(--good)} .fs .act.replace{background:var(--warn); color:#0b0b0b} .fs .act.remove{background:var(--crit)} .fs .act.tune{background:var(--accent)}
  .fs .noise{margin-left:auto; font-family:var(--mono); font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums}
  .fs code{display:block; font-family:var(--mono); font-size:12px; background:var(--plane); border:1px solid var(--hair-2); border-radius:8px; padding:10px 12px; color:var(--ink); white-space:pre-wrap; word-break:break-word}
  .fs .rat{font-size:13px; color:var(--ink-2); line-height:1.5}
  .fs .ev{font-family:var(--mono); font-size:11px; color:var(--faint); line-height:1.5}
  .fs .ev b{color:var(--muted)}
  .method{background:var(--surface-2); border:1px solid var(--hair); border-radius:14px; padding:24px}
  .method .grid{display:grid; grid-template-columns:1fr 1fr; gap:22px 34px}
  .method h4{font-family:var(--mono); font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint); margin:0 0 10px}
  .method ul{margin:0; padding-left:0; list-style:none; display:flex; flex-direction:column; gap:9px}
  .method li{font-size:13px; color:var(--ink-2); line-height:1.5; padding-left:18px; position:relative}
  .method li::before{content:"—"; position:absolute; left:0; color:var(--faint)}
  .method li b{color:var(--ink)}
  .method li.warnli::before{content:"!"; color:var(--warn); font-weight:700; font-family:var(--mono)}
  footer{padding:34px 0 60px; color:var(--faint); font-family:var(--mono); font-size:11.5px; line-height:1.7}
  footer .wrap{display:flex; justify-content:space-between; gap:20px; flex-wrap:wrap}
  footer b{color:var(--muted)}
  @media (max-width:860px){
    .kpis{grid-template-columns:repeat(2,1fr)}
    .pillars{grid-template-columns:1fr}
    .momentum{grid-template-columns:1fr}
    .split2{grid-template-columns:1fr}
    .signals{grid-template-columns:1fr}
    .sowhat{grid-template-columns:1fr}
    .method .grid{grid-template-columns:1fr}
    .bar{grid-template-columns:120px 1fr}
  }
  @media (max-width:520px){ .kpis{grid-template-columns:1fr 1fr} .metabar div{flex-basis:50%} }
"""

_THEME_SCRIPT = """
  (function(){
    var btn=document.getElementById('themeBtn');
    if(!btn)return;
    btn.addEventListener('click',function(){
      var root=document.documentElement;
      var cur=root.getAttribute('data-theme');
      if(!cur){cur=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
      root.setAttribute('data-theme',cur==='dark'?'light':'dark');
    });
  })();
"""

_SIG_TAGS = {"open", "threat", "demand", "white", "move"}
_SPARK_STATES = {"up", "fx", "cool"}
_PLAT_STATES = {"jx", "lx"}
_FS_ACTIONS = {"add", "replace", "remove", "tune"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, min(100.0, value / maximum * 100.0))


def _fmt(value: Any) -> str:
    """Format a metric for display: ints without a trailing .0, else the string as given."""
    f = _num(value, None if isinstance(value, str) else 0.0) if not isinstance(value, str) else None
    if isinstance(value, (int,)) or (isinstance(value, float) and value.is_integer()):
        return str(int(value))
    if f is not None and float(f).is_integer():
        return str(int(f))
    return esc(value)


def _num_label(text: str) -> str:
    return f'<div class="num mono">{text}</div>'


def _section(num: str, title: str, sub: str, body: str) -> str:
    sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<section><div class="wrap"><div class="shead">{_num_label(num)}'
        f"<div><h2>{esc(title)}</h2>{sub_html}</div></div>{body}</div></section>"
    )


# --------------------------------------------------------------------------- #
# section renderers
# --------------------------------------------------------------------------- #


def _topbar(meta: dict) -> str:
    brand = esc(meta.get("brandmark") or "COMMUNITY SIGNAL")
    src = esc(meta.get("source_label") or "SYFTEN")
    return (
        '<div class="topbar"><div class="wrap">'
        f'<div class="brandmark"><span class="dotpulse"></span> {brand}</div>'
        '<div class="spacer"></div>'
        f'<div class="meta">{src}</div>'
        '<button class="toggle" id="themeBtn" aria-label="Toggle theme">◐ theme</button>'
        "</div></div>"
    )


def _mast(meta: dict) -> str:
    kicker = esc(meta.get("kicker") or "Community Signal Brief")
    title = esc(meta.get("title"))
    dek = f'<p class="dek">{esc(meta["subtitle"])}</p>' if meta.get("subtitle") else ""
    cells = ""
    for cell in meta.get("metabar") or []:
        if isinstance(cell, dict):
            cells += (
                f'<div><div class="k">{esc(cell.get("k"))}</div>'
                f'<div class="v tnum">{esc(cell.get("v"))}</div></div>'
            )
    metabar = f'<div class="metabar">{cells}</div>' if cells else ""
    return (
        '<header class="mast">'
        f'<div class="kicker"><span class="pip"></span> {kicker}</div>'
        f"<h1>{title}</h1>{dek}{metabar}</header>"
    )


def _bluf(bluf: dict) -> str:
    lead = f'<div class="lead">{esc(bluf.get("lead"))}</div>' if bluf.get("lead") else ""
    pillars = ""
    for p in bluf.get("pillars") or []:
        if not isinstance(p, dict):
            continue
        color = esc(p.get("color") or "var(--accent)")
        pillars += (
            f'<div class="pillar" style="--pc:{color}">'
            f'<div class="pk">{esc(p.get("key"))}</div>'
            f"<h3>{esc(p.get('title'))}</h3><p>{esc(p.get('body'))}</p></div>"
        )
    pillars_html = f'<div class="pillars">{pillars}</div>' if pillars else ""
    return f'<div class="bluf">{lead}{pillars_html}</div>'


def _kpis(kpis: list) -> str:
    cells = ""
    for k in kpis:
        if not isinstance(k, dict):
            continue
        cls = "kpi acc" if k.get("accent") else "kpi"
        foot = f'<div class="foot">{esc(k.get("foot"))}</div>' if k.get("foot") else ""
        cells += (
            f'<div class="{cls}"><div class="val tnum">{_fmt(k.get("val"))}</div>'
            f'<div class="lab">{esc(k.get("label"))}</div>{foot}</div>'
        )
    return f'<div class="kpis">{cells}</div>'


def _momentum(rows: list) -> str:
    cards = ""
    # Render each row as its own card row inside a single card grid; group into one card.
    body = ""
    for r in rows:
        if not isinstance(r, dict):
            continue
        series = [_num(x) for x in (r.get("series") or [])]
        mx = max(series) if series else 0.0
        bars = ""
        for i, val in enumerate(series):
            h = 8 + _pct(val, mx) * 0.28  # min 8px, scaled to ~36px
            last = " last" if i == len(series) - 1 else ""
            bars += f'<b class="{last.strip()}" style="height:{h:.0f}px"></b>'
        state = r.get("state")
        spark_cls = f"spark {state}" if state in _SPARK_STATES else "spark"
        sub = f"<small>{esc(r.get('sub'))}</small>" if r.get("sub") else ""
        trend = (
            f'<div class="mtrend">{esc(r.get("trend"))}</div>' if r.get("trend") else "<div></div>"
        )
        body += (
            f'<div class="mrow"><div class="mn">{esc(r.get("name"))}{sub}</div>'
            f'{trend}<div class="{spark_cls}">{bars}</div></div>'
        )
    cards = f'<div class="mcard">{body}</div>'
    return f'<div class="momentum">{cards}</div>'


def _categories_and_share(model: dict, colors: dict[str, str]) -> str:
    """The category stacked bar (left) + share-of-voice bars (right) as a split2."""
    cats = [c for c in (model.get("categories") or []) if isinstance(c, dict)]
    sov = [s for s in (model.get("share_of_voice") or []) if isinstance(s, dict)]
    left = ""
    if cats:
        total = sum(_num(c.get("count")) for c in cats) or 1.0
        segs = ""
        keyrows = ""
        for i, c in enumerate(cats):
            key = str(c.get("key", "") or f"cat{i}")
            color = colors.get(key) or c.get("color") or NEUTRAL
            count = _num(c.get("count"))
            lite = " lite" if is_light(color) else ""
            label = esc(c.get("label") or key)
            segs += (
                f'<div class="seg{lite}" style="flex:{count:.4f};background:{esc(color)}" '
                f'title="{label}"><span>{_fmt(c.get("count"))}</span></div>'
            )
            keyrows += (
                f'<div class="skrow"><i style="background:{esc(color)}"></i>{label}'
                f'<span class="n">{_fmt(c.get("count"))} · {_pct(count, total):.0f}%</span></div>'
            )
        left = (
            '<div class="chartcard"><div class="chart-top"><div class="ct">Category mix</div></div>'
            f'<div class="stack">{segs}</div><div class="stack-key">{keyrows}</div></div>'
        )
    right = ""
    if sov:
        mx = max(_num(s.get("value")) for s in sov) or 1.0
        bars = ""
        for s in sov:
            val = _num(s.get("value"))
            key = str(s.get("category", "") or "")
            color = colors.get(key) or NEUTRAL
            lite = " lite" if is_light(color) else ""
            cat_line = (
                f'<span class="cat">{esc(s.get("category"))}</span>' if s.get("category") else ""
            )
            bars += (
                '<div class="bar"><div class="name">'
                f"{esc(s.get('name'))}{cat_line}</div>"
                f'<div class="track"><div class="fill{lite}" '
                f'style="width:{_pct(val, mx):.1f}%;background:{esc(color)}">'
                f"<span>{_fmt(s.get('value'))}</span></div></div></div>"
            )
        right = (
            '<div class="chartcard"><div class="chart-top"><div class="ct">Share of voice</div></div>'
            f'<div class="bars">{bars}</div></div>'
        )
    if left and right:
        return f'<div class="split2">{left}{right}</div>'
    return left or right


def _platforms(rows: list) -> str:
    mx = max(_num(r.get("value")) for r in rows if isinstance(r, dict)) or 1.0
    body = ""
    for r in rows:
        if not isinstance(r, dict):
            continue
        state = r.get("state")
        cls = f"prow {state}" if state in _PLAT_STATES else "prow"
        pct = _num(r.get("pct")) if r.get("pct") is not None else _pct(_num(r.get("value")), mx)
        body += (
            f'<div class="{cls}"><div class="pn">{esc(r.get("name"))}</div>'
            f'<div class="ptrack"><div class="pfill" style="width:{pct:.1f}%"></div></div>'
            f'<div class="pv">{_fmt(r.get("value"))}</div></div>'
        )
    return f'<div class="chartcard"><div class="plat">{body}</div></div>'


def _signals(rows: list) -> str:
    cards = ""
    for s in rows:
        if not isinstance(s, dict):
            continue
        tag = s.get("tag") if s.get("tag") in _SIG_TAGS else "move"
        tag_label = esc(s.get("tag_label") or tag)
        why = f'<div class="why">{esc(s.get("why"))}</div>' if s.get("why") else ""
        url = safe_url(s.get("source_url"))
        if url:
            label = esc(s.get("source_label") or "source")
            src = f'<a class="src" href="{url}" rel="noopener noreferrer nofollow" target="_blank"><span class="arrow">↗</span> {label}</a>'
        elif s.get("source_label"):
            src = f'<div class="src">{esc(s.get("source_label"))}</div>'
        else:
            src = ""
        cards += (
            f'<div class="sig"><span class="tag t-{tag}">{tag_label}</span>'
            f"<h3>{esc(s.get('title'))}</h3><p>{esc(s.get('body'))}</p>{why}{src}</div>"
        )
    return f'<div class="signals">{cards}</div>'


def _moves(rows: list) -> str:
    body = ""
    for i, m in enumerate(rows):
        if not isinstance(m, dict):
            continue
        color = esc(m.get("color") or "var(--muted)")
        who = f'<span class="who">{esc(m.get("who"))}</span>' if m.get("who") else ""
        body += (
            f'<div class="move"><div class="ic" style="background:{color}">{i + 1:02d}</div>'
            f'<div class="body"><h3>{esc(m.get("title"))}{who}</h3>'
            f"<p>{esc(m.get('body'))}</p></div></div>"
        )
    return f'<div class="moves">{body}</div>'


def _plays(rows: list) -> str:
    cols = ""
    for p in rows:
        if not isinstance(p, dict):
            continue
        badge = esc(p.get("badge_color") or "var(--accent)")
        items = ""
        for it in p.get("items") or []:
            if not isinstance(it, dict):
                continue
            detail = f'<div class="pd">{esc(it.get("detail"))}</div>' if it.get("detail") else ""
            items += f'<li><div><div class="pt">{esc(it.get("title"))}</div>{detail}</div></li>'
        cols += (
            f'<div class="playbook"><div class="ph">'
            f'<span class="badge" style="background:{badge}">{esc(p.get("audience"))}</span></div>'
            f"<ol>{items}</ol></div>"
        )
    return f'<div class="sowhat">{cols}</div>'


def _filter_suggestions(rows: list) -> str:
    body = ""
    for s in rows:
        if not isinstance(s, dict):
            continue
        action = s.get("action") if s.get("action") in _FS_ACTIONS else "tune"
        noise = ""
        if s.get("noise_before") is not None or s.get("noise_after") is not None:
            nb = esc(s.get("noise_before")) if s.get("noise_before") is not None else "?"
            na = esc(s.get("noise_after")) if s.get("noise_after") is not None else "?"
            noise = f'<span class="noise">noise {nb} → {na}</span>'
        code = f"<code>{esc(s.get('filter'))}</code>" if s.get("filter") else ""
        rat = f'<div class="rat">{esc(s.get("rationale"))}</div>' if s.get("rationale") else ""
        ev = ""
        evidence = [e for e in (s.get("evidence") or []) if e]
        if evidence:
            joined = " · ".join(esc(e) for e in evidence[:6])
            ev = f'<div class="ev"><b>evidence:</b> {joined}</div>'
        body += (
            f'<div class="fs"><div class="fshead"><span class="act {action}">{esc(action)}</span>'
            f"{noise}</div>{code}{rat}{ev}</div>"
        )
    return f'<div class="fsug">{body}</div>'


def _method(method: dict) -> str:
    def col(title: str, items: list, warn: bool = False) -> str:
        lis = ""
        for it in items or []:
            cls = ' class="warnli"' if warn else ""
            lis += f"<li{cls}>{esc(it)}</li>"
        return f"<div><h4>{esc(title)}</h4><ul>{lis}</ul></div>"

    grid = col("Method", method.get("notes") or []) + col(
        "Caveats & limitations", method.get("caveats") or [], warn=True
    )
    return f'<div class="method"><div class="grid">{grid}</div></div>'


def _footer(meta: dict, footer: dict) -> str:
    left = esc(footer.get("left") or meta.get("brandmark") or "Community Signal")
    right = esc(footer.get("right") or meta.get("date") or "")
    return f'<footer><div class="wrap"><div>{left}</div><div>{right}</div></div></footer>'


# --------------------------------------------------------------------------- #
# top-level
# --------------------------------------------------------------------------- #


def render_html(model: dict) -> str:
    """Render a validated signal model to a complete HTML document string."""
    meta = model.get("meta", {})
    colors = resolve_category_colors(model)

    sections: list[str] = []
    n = 0

    def add(title: str, sub: str, body: str) -> None:
        nonlocal n
        n += 1
        sections.append(_section(f"{n:02d}", title, sub, body))

    if model.get("bluf"):
        add("Bottom line", meta.get("bluf_sub", ""), _bluf(model["bluf"]))
    if model.get("kpis"):
        add("Scoreboard", "", _kpis(model["kpis"]))
    if model.get("momentum"):
        add("Momentum", "Volume trend across pulls.", _momentum(model["momentum"]))
    if model.get("categories") or model.get("share_of_voice"):
        add("Category & share of voice", "", _categories_and_share(model, colors))
    if model.get("platforms"):
        add("Platform mix", "Where the conversation lives.", _platforms(model["platforms"]))
    if model.get("signals"):
        add("Signals", "", _signals(model["signals"]))
    if model.get("moves"):
        add("Notable moves", "", _moves(model["moves"]))
    if model.get("filter_suggestions"):
        add(
            "Filter tuning (recommend-only — apply in Syften)",
            "Evidence-cited suggestions to raise signal quality. Review each, then apply in the Syften dashboard.",
            _filter_suggestions(model["filter_suggestions"]),
        )
    if model.get("plays"):
        add("Plays", "", _plays(model["plays"]))
    if model.get("method"):
        add("Method & limitations", "", _method(model["method"]))

    body_inner = "".join(sections)
    footer = _footer(meta, model.get("footer") or {})
    title = esc(meta.get("title"))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        f"{_topbar(meta)}\n"
        f'<div class="wrap">\n{_mast(meta)}\n</div>\n'
        f"{body_inner}\n{footer}\n"
        f"<script>{_THEME_SCRIPT}</script>\n"
        "</body>\n</html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    from .score import _guard_profile_path  # local import: avoid a load-order dependency

    parser = argparse.ArgumentParser(
        prog="gtm_core.community_signal.render",
        description="Render a signal-model JSON into a self-contained HTML dashboard.",
    )
    parser.add_argument("model", help="Path to the signal-model JSON file (or '-' for stdin).")
    parser.add_argument("--out", required=True, help="Path to write the HTML report.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Misroute guard: when given, --out (and model, if a file path) that resolves "
        "under the content root must sit under content/<profile>/, or the CLI refuses (exit 2).",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    args = parser.parse_args(argv)

    if args.profile:
        cfg = PathConfig.from_env(repo_root=args.repo_root)
        if args.model != "-":
            _guard_profile_path(Path(args.model), cfg.content_root, args.profile, "model")
        _guard_profile_path(Path(args.out), cfg.content_root, args.profile, "--out")

    source: str | Path
    if args.model == "-":
        source = sys.stdin.read()
    else:
        source = Path(args.model)
    model = load_model(source)
    html_doc = render_html(model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
