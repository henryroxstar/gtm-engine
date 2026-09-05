from __future__ import annotations

import json
from pathlib import Path

from ..page_inputs import Report, verify_inventory, write_inventory
from ..prospects_consolidate import _pool_dir, _prospects_dir
from .config import PAGE_NAME, TABS, dashboard_path, input_globs, page_title
from .format import _e, _tiles_reset
from .model import build_model, scope_to_campaign
from .scope import Scope, resolve
from .views_learn import _learn_view, _ops_view
from .views_status import _status_view
from .views_what import _what_view
from .views_who import _who_view


def render_html(m: dict) -> str:
    _tiles_reset()  # the recorder holds exactly one page: this one
    title = _e(page_title(m))
    rec = m["reconciliation"]
    banners = ""
    if not rec["ok"]:
        parts = []
        if rec["in_ledger_only"]:
            parts.append("missing from the live figures: " + ", ".join(rec["in_ledger_only"]))
        if rec["in_snapshot_only"]:
            parts.append(
                "in the figures but not our records: " + ", ".join(rec["in_snapshot_only"])
            )
        banners += (
            '<div class="card banner"><h2>These numbers may be out of date</h2><p>'
            "The live figures and our own records disagree about which email sequences exist — "
            + _e("; ".join(parts))
            + ". Refresh before trusting anything below.</p></div>"
        )
    # Only the reconciliation warning stays here. A page-level banner shows on every tab, so
    # it has to earn that: this one says the figures on all four panels cannot be trusted, and
    # burying it in a panel a reader may never open would defeat it. "Nothing has been sent" is
    # a fact about the run rather than about the page — it lives in Operator notes, and the
    # status panel's first tile carries the same number for the reader who only wants that.

    panels = {
        "status": _status_view(m),
        "who": _who_view(m),
        "what": _what_view(m),
        "learn": _learn_view(m),
        "ops": _ops_view(m),
    }
    nav = "".join(
        f'<button class="tab{" on" if i == 0 else ""}" data-t="{k}">{_e(v)}</button>'
        for i, (k, v) in enumerate(TABS)
    )
    bodies = "".join(
        f'<section id="p-{k}" class="panel{" on" if i == 0 else ""}">{panels[k]}</section>'
        for i, (k, _) in enumerate(TABS)
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#0f1115; --panel:#171a21; --line:#252a34; --ink:#e8ecf3; --muted:#98a2b3;
        --accent:#7c5cff; --ok:#2ecc71; --warn:#f1c40f; --bad:#e74c3c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 -apple-system,
       BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:28px 20px 70px; }}
.wrap {{ max-width:1080px; margin:0 auto; }}
h1 {{ font-size:23px; margin:0 0 4px; }}
h2 {{ font-size:16px; margin:0 0 14px; letter-spacing:.01em; }}
h3 {{ font-size:14px; margin:24px 0 8px; color:var(--muted); text-transform:uppercase;
     letter-spacing:.06em; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
        padding:20px 22px; margin-bottom:16px; }}
.card.banner {{ border-color:rgba(241,196,15,.5); background:rgba(241,196,15,.06); }}
.card.banner h2 {{ color:var(--warn); }}
.stats {{ display:flex; flex-wrap:wrap; gap:14px; margin-bottom:16px; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
        padding:14px 18px; flex:1 1 190px; }}
.stat-value {{ font-size:27px; font-weight:650; }}
.stat-label {{ font-size:13px; }}
.stat-sub {{ font-size:12px; color:var(--muted); margin-top:3px; }}
table {{ width:100%; border-collapse:collapse; margin:10px 0; font-size:13.5px;
        display:block; overflow-x:auto; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
        vertical-align:top; }}
th {{ color:var(--muted); font-weight:500; font-size:12px; text-transform:uppercase;
     letter-spacing:.05em; white-space:nowrap; }}
td.num-cell {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.muted {{ color:var(--muted); }}
.note {{ font-size:13px; color:var(--muted); margin:12px 0 0; padding-left:12px;
        border-left:3px solid var(--accent); }}
.note strong {{ color:var(--ink); }}
.pill {{ font-size:11px; padding:2px 8px; border-radius:999px; white-space:nowrap;
        background:rgba(124,92,255,.15); color:var(--accent); }}
.pill.good {{ background:rgba(46,204,113,.15); color:var(--ok); }}
.pill.warn {{ background:rgba(241,196,15,.15); color:var(--warn); }}
.pill.bad {{ background:rgba(231,76,60,.15); color:var(--bad); }}
code {{ background:rgba(255,255,255,.05); padding:1px 5px; border-radius:4px; font-size:12.5px; }}
.steps {{ margin:8px 0 0; padding-left:20px; }} .steps li {{ margin:7px 0; }}
.bars {{ margin:12px 0 4px; }}
.brow {{ display:flex; align-items:center; gap:12px; margin:7px 0; }}
.blabel {{ flex:0 0 210px; font-size:13px; }}
.btrack {{ flex:1; height:12px; background:rgba(255,255,255,.05); border-radius:999px; }}
.bfill {{ height:100%; border-radius:999px; background:var(--accent); }}
.bfill.tb {{ background:#3aa3ff; }}
.bfill.tc {{ background:#4b5364; }}
.bval {{ flex:0 0 96px; text-align:right; font-size:13px; font-variant-numeric:tabular-nums; }}
.gridwrap {{ overflow-x:auto; }}
table.grid {{ display:table; width:auto; }}
table.grid th {{ text-transform:none; letter-spacing:0; font-size:12px; }}
td.gcell {{ text-align:center; min-width:104px; border:1px solid var(--line); }}
td.gcell.empty {{ color:var(--line); }}
.verdicts {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 4px; }}
.v {{ flex:1 1 210px; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.v.ok {{ border-color:rgba(46,204,113,.4); }}
.v.bad {{ border-color:rgba(231,76,60,.4); }}
.vn {{ font-size:26px; font-weight:650; }}
.vl {{ font-size:13px; }} .vd {{ font-size:12px; margin-top:5px; }}
.tabs {{ display:flex; gap:8px; margin:18px 0; flex-wrap:wrap; }}
.tab {{ background:var(--panel); color:var(--muted); border:1px solid var(--line);
       border-radius:999px; padding:9px 20px; font-size:13.5px; cursor:pointer; }}
.tab.on {{ color:var(--ink); border-color:var(--accent); }}
.panel {{ display:none; }} .panel.on {{ display:block; }}
details {{ margin-top:12px; }}
summary {{ cursor:pointer; color:var(--muted); font-size:13px; }}
</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  <p class="muted" style="margin:0">refreshed {_e(m["generated_at"])}</p>
  <div class="tabs">{nav}</div>
  {banners}
  {bodies}
</div>
<script>
document.querySelectorAll('.tab').forEach(function (b) {{
  b.addEventListener('click', function () {{
    document.querySelectorAll('.tab').forEach(function (x) {{ x.classList.remove('on'); }});
    document.querySelectorAll('.panel').forEach(function (x) {{ x.classList.remove('on'); }});
    b.classList.add('on');
    document.getElementById('p-' + b.dataset.t).classList.add('on');
  }});
}});
</script>
</body></html>
"""


def _stub(target: str, title: str) -> str:
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={target}">'
        f"<title>{title} — moved</title></head><body>"
        f'<p>This page has moved to <a href="{target}">{target}</a>.</p>'
        f"</body></html>\n"
    )


def page_path(profile: str, content_root: Path | None, sc: Scope) -> Path:
    """Where a scope's page lives. One place, so the renderer and ``--check-fresh``
    cannot disagree about which file the check is checking."""
    if sc.stem is None:
        return dashboard_path(profile, content_root)
    return _prospects_dir(profile, content_root).parent / f"campaign-{sc.stem}.html"


def render_dashboard(
    profile: str,
    content_root: Path | None = None,
    *,
    stubs: bool = True,
    campaign: str | None = None,
    scope: str | None = None,
) -> Path:
    """Render the status page at one scope.

    ``scope`` is ``campaign``/``open``/``all``; omitting it means ``campaign`` when slugs
    were named and ``all`` otherwise, so every existing caller — the `prospect` skill and
    the no-argument refresh at the tail of `consolidate` — keeps its behaviour exactly.
    """
    model = build_model(profile, content_root)
    sc = resolve(
        scope or ("campaign" if campaign else "all"), campaign, model["campaigns"]["campaigns"]
    )
    if sc.is_scoped:
        model = scope_to_campaign(model, sc.csv)
        if model.get("campaign_scope") != sc.csv:
            raise SystemExit(
                f"unknown campaign in {sc.csv!r} — every slug needs a manifest; one the page cannot see "
                "renders as the profile-wide rollup, which is a different campaign's numbers. "
                "Add content/<profile>/plans/campaigns/<slug>.campaign.toml first."
            )
        model["scope_label"] = sc.label
        out = page_path(profile, content_root, sc)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(model), encoding="utf-8")
        write_inventory(out, input_globs(profile, content_root), scope=sc.mode, slugs=sc.slugs)
        return out  # the redirect stubs belong to the profile-wide page, not to a scoped one
    out = dashboard_path(profile, content_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model), encoding="utf-8")

    pool = _pool_dir(profile, content_root)
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "status.json").write_text(json.dumps(model["status"], indent=2), encoding="utf-8")
    (pool / "cells.json").write_text(json.dumps(model["cells"], indent=2), encoding="utf-8")

    if stubs:
        base = _prospects_dir(profile, content_root).parent
        for name, target in (("campaigns.html", PAGE_NAME), ("gtm.html", PAGE_NAME)):
            (base / name).write_text(_stub(target, "Campaigns"), encoding="utf-8")
        (base / "prospects" / "status.html").write_text(
            _stub(f"../{PAGE_NAME}", "Prospecting pipeline"), encoding="utf-8"
        )
    write_inventory(out, input_globs(profile, content_root), scope="all", slugs=())
    return out


def check_fresh(
    profile: str,
    content_root: Path | None = None,
    *,
    campaign: str | None = None,
    scope: str | None = None,
) -> Report:
    """Is the page for this scope still built from what is on disk now?

    Read-only, and deliberately separate from rendering: a render always produces a fresh
    page, so a check folded into it could only ever pass. The question that matters is
    asked LATER — which is exactly when nobody asks it.
    """
    from ..campaigns_dashboard import _load_manifests

    sc = resolve(
        scope or ("campaign" if campaign else "all"),
        campaign,
        _load_manifests(profile, content_root),
    )
    root, _ = input_globs(profile, content_root)
    return verify_inventory(page_path(profile, content_root, sc), root)
