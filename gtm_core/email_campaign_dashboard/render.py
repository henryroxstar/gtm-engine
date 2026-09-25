from __future__ import annotations

import json
import sys
from pathlib import Path

from ..page_inputs import Report, verify_inventory, write_inventory
from ..prospects_consolidate import _pool_dir, _prospects_dir
from .config import PAGE_NAME, PROFILE_FILES, TABS, dashboard_path, input_globs, page_title
from .filters import bar_html, script_block
from .format import _e, _tiles_reset
from .health import figures_date, reconciliation_detail
from .model import build_model, scope_to_campaign
from .scope import Scope, resolve
from .styles import STYLESHEET
from .views_learn import _learn_view, _ops_view
from .views_status import _status_view
from .views_what import _what_view
from .views_who import _who_view
from .views_worklist import _worklist_view


def _warnings_strip(m: dict) -> str:
    """PS20: one page-wide strip for every reason ``m["warnings"]`` found, in that order —
    replaces the reconciliation-only banner. ``records-disagree`` keeps that banner's old
    wording when the reconciliation itself disagrees
    (tests/test_email_campaign_dashboard.py:972-982); a sum-only mismatch (Task 6) gets its
    own, different sentence.
    """
    warnings = m.get("warnings") or []
    if not warnings:
        return ""
    rec = m["reconciliation"]
    sentences = []
    for reason in warnings:
        if reason == "unreadable":
            sentences.append(
                "The sending tool's figures couldn't be read, so sending numbers show as "
                "unknown, not zero."
            )
        elif reason == "figures-old":
            fetched_date = figures_date((m["status"].get("snapshot") or {}).get("fetched"))
            sentences.append(
                f"Sending figures are from {fetched_date}; replies since then aren't counted."
                if fetched_date
                else "Sending figures carry no date, so treat them as old."
            )
        elif reason == "records-disagree" and not rec["ok"]:
            sentences.append(
                "These numbers may be out of date — the live figures and our own records "
                "disagree about which email sequences exist — "
                + _e(reconciliation_detail(rec))
                + ". Refresh before trusting anything below."
            )
        elif reason == "records-disagree":
            sentences.append(
                "The sending figures and the campaign lists don't add up — a sequence may "
                "be counted twice."
            )
    body = "".join(f"<p>{s}</p>" for s in sentences)
    return f'<div class="card warn" data-warn="{_e(" ".join(warnings))}">{body}</div>'


def render_html(m: dict) -> str:
    _tiles_reset()  # the recorder holds exactly one page: this one
    title = _e(page_title(m))
    # A page-level banner shows on every tab. Only the warnings strip earns that: the eval
    # round waiting to be labelled is a Maintenance line on Operator notes (PS20 P1.6), and
    # the review sheet is linked from the lede's "Yours" line (PS15) — both read by the
    # model (`health.page_extras`), because this function opens no file.
    banners = _warnings_strip(m)

    panels = {
        "worklist": _worklist_view(m),
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
<style>{STYLESHEET}</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  <p class="muted" style="margin:0">refreshed {_e(m["generated_at"])}</p>
  <div class="tabs">{nav}</div>
  <label class="techtoggle"><input type="checkbox" id="tech-toggle">Show the technical detail</label>
  {bar_html(m)}
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
var techToggle = document.getElementById('tech-toggle');
if (techToggle) {{
  var syncTech = function () {{
    document.body.classList.toggle('technical-detail-on', techToggle.checked);
  }};
  techToggle.addEventListener('change', syncTech);
  syncTech();
}}
</script>
{script_block(m)}
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


def _mode(scope: str | None, campaign: str | None, campaigns: list[dict]) -> str:
    """The scope mode to resolve. ``open`` on a profile with NO campaign manifest at all
    means "everything", said once on stderr.

    ``--scope open`` is the mandatory last step of the prospect skill, and a tenant's first
    runs have no manifest — so the step every run ends on exited 1 with instructions to edit
    a TOML file. With no manifest there is no campaign to confuse the rollup with, which is
    the only thing the refusal protects. When manifests exist and none is open, ``resolve``
    still refuses and names each one's status.
    """
    mode = scope or ("campaign" if campaign else "all")
    if mode == "open" and not any(c.get("slug") for c in campaigns):
        print("no campaign manifest yet — showing everything (--scope all)", file=sys.stderr)
        return "all"
    return mode


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
    campaigns = model["campaigns"]["campaigns"]
    sc = resolve(_mode(scope, campaign, campaigns), campaign, campaigns)
    # Read once, reused by both `write_inventory` calls below (PS20 T1.9) — nothing between
    # here and either call site changes what `input_globs` would re-glob from disk.
    spec = input_globs(profile, content_root)
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
        write_inventory(
            out, spec, scope=sc.mode, slugs=sc.slugs, profile=profile, profile_files=PROFILE_FILES
        )
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
    write_inventory(out, spec, scope="all", slugs=(), profile=profile, profile_files=PROFILE_FILES)
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

    manifests = _load_manifests(profile, content_root)
    sc = resolve(_mode(scope, campaign, manifests), campaign, manifests)
    root, _ = input_globs(profile, content_root)
    return verify_inventory(page_path(profile, content_root, sc), root, profile=profile)


def refresh_all(
    profile: str, content_root: Path | None = None, *, stubs: bool = True
) -> list[Path]:
    """Re-render **every page that already exists**, each under its own recorded scope.

    The refresh that runs after a consolidation only ever rendered the profile rollup —
    ``render_dashboard`` with no scope, which is ``--scope all``. Every scoped page ever
    built (``campaign-open.html``, ``campaign-<slug>.html``) was left exactly as it was, and
    nothing else re-renders them either: there is no timer, and the only other automated
    trigger is a pack prompt that also names the unscoped command.

    So they went stale silently, which is the failure ``page_inputs`` exists to describe —
    a stale page renders identically to a current one. Measured on a live tenant profile
    2026-09-21: of five pages on disk, the rollup was fresh and the other four were behind
    the same ``history.jsonl``, still showing a worklist whose grouping bug had already been
    fixed.

    Scope is READ BACK from each page's ``.inputs.json`` rather than guessed from the
    filename, because ``campaign-open.html`` and a two-slug page are both ``campaign-*`` on
    disk and only the inventory knows which mode built them. A page whose inventory is
    missing is skipped rather than rendered under an assumed scope — rendering the wrong
    scope over it would replace one campaign's numbers with another's, which is worse than
    leaving it stale and is the one thing this package refuses everywhere else.

    This re-renders what is there; it never invents a page. Returns the paths written.
    """
    written = [render_dashboard(profile, content_root, stubs=stubs)]
    seen = {written[0].name}
    base = _prospects_dir(profile, content_root).parent
    for inv in sorted(base.glob("*.inputs.json")):
        try:
            rec = json.loads(inv.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        page, mode = rec.get("page") or "", rec.get("scope") or ""
        if not page or page in seen or mode not in ("open", "campaign"):
            continue
        slugs = [s for s in (rec.get("slugs") or []) if s]
        if mode == "campaign" and not slugs:
            continue
        seen.add(page)
        written.append(
            render_dashboard(
                profile,
                content_root,
                stubs=False,  # the redirect stubs belong to the rollup, written above
                campaign=",".join(slugs) if mode == "campaign" else None,
                scope=mode,
            )
        )
    return written
