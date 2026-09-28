from __future__ import annotations

import json
import sys
from pathlib import Path

from ..page_inputs import Report, verify_inventory, write_inventory
from ..paths import resolve_content_root, resolve_profiles_root
from ..prospects_consolidate import _pool_dir, _prospects_dir
from .config import (
    PAGE_NAME,
    PROFILE_FILES,
    TAB_LABELS,
    TABS,
    dashboard_path,
    input_globs,
    page_title,
)
from .filters import script_block
from .format import _e, _tiles_reset
from .health import disagree_names
from .model import build_model, scope_to_campaign
from .scope import Scope, resolve
from .styles import render_stylesheet
from .views_accounts import _accounts_view
from .views_emails import _emails_view
from .views_ops import _ops_view
from .views_overview import _overview_view
from .views_results import _insights_view, _results_view


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
    names = disagree_names(m)
    sentences = []
    for reason in warnings:
        if reason == "unreadable":
            sentences.append(
                "The sending tool's figures couldn't be read, so sending numbers show as "
                "unknown, not zero."
            )
        elif reason == "records-disagree" and not rec["ok"]:
            who = _e(", ".join(names) or "sequences no campaign lists")
            sentences.append(
                "These numbers may be out of date — the live figures and our own records "
                "disagree about which email sequences exist. It affects "
                f"{who}. The detail is under {_e(TAB_LABELS['ops'])}. "
                "Refresh before trusting anything below."
            )

        elif reason == "records-disagree":
            sentences.append(
                "The sending figures and the campaign lists don't add up — a sequence may "
                "be counted twice." + (f" It affects {_e(', '.join(names))}." if names else "")
            )
    if not sentences:
        return ""
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
        "overview": _overview_view(m),
        "accounts": _accounts_view(m),
        "emails": _emails_view(m),
        "results": _results_view(m),
        "insights": _insights_view(m),
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
<style>{render_stylesheet(m.get("brand_palette"))}</style></head>
<body><div class="wrap">
  <h1>{title}</h1>
  <p class="muted" style="margin:0">refreshed {_e(m["generated_at"])}</p>
  {banners}
  <div class="tabs">{nav}</div>
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
function copyPrompt(btn) {{
  var targetEl = null;
  var row = btn.closest('.action-prompt-row') || btn.closest('.action-prompt-card') || btn.parentElement;
  if (row) {{
    targetEl = row.querySelector('.action-prompt-text');
  }}
  if (!targetEl) {{
    var targetId = btn.getAttribute('data-target');
    if (targetId) {{
      targetEl = document.getElementById(targetId);
    }}
  }}
  if (!targetEl) return;
  var text = targetEl.textContent.trim();
  var origText = btn.textContent;
  var onSuccess = function () {{
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(function () {{
      btn.textContent = origText;
      btn.classList.remove('copied');
    }}, 2000);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(onSuccess).catch(function () {{
      fallbackCopy(text, onSuccess);
    }});
  }} else {{
    fallbackCopy(text, onSuccess);
  }}
}}
function fallbackCopy(text, cb) {{
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {{
    document.execCommand('copy');
    if (cb) cb();
  }} catch (e) {{
    console.error('Copy failed', e);
  }}
  document.body.removeChild(ta);
}}
(function initRosterControls() {{
  var table = document.getElementById('roster-table');
  if (!table) return;

  var searchInput = document.getElementById('roster-search');
  var clearBtn = document.getElementById('roster-search-clear');
  var pillContainer = document.getElementById('roster-group-filters');
  var activeGroup = 'all';

  function filterRoster() {{
    var query = (searchInput ? searchInput.value : '').toLowerCase().trim();
    var rows = table.querySelectorAll('tbody tr[data-row]');
    var groupCounts = {{}};
    var totalVisible = 0;

    rows.forEach(function(row) {{
      var rowGroup = row.getAttribute('data-roster-group') || '';
      var matchesGroup = (activeGroup === 'all' || rowGroup === activeGroup);
      var matchesQuery = true;
      if (query) {{
        var text = (row.textContent || '').toLowerCase();
        matchesQuery = text.indexOf(query) !== -1;
      }}
      var isVisible = matchesGroup && matchesQuery;
      row.style.display = isVisible ? '' : 'none';
      if (isVisible) {{
        totalVisible++;
        groupCounts[rowGroup] = (groupCounts[rowGroup] || 0) + 1;
      }}
    }});

    table.querySelectorAll('tbody tr.grp').forEach(function(grp) {{
      var gid = grp.getAttribute('data-group');
      if (activeGroup !== 'all' && gid !== activeGroup) {{
        grp.style.display = 'none';
      }} else {{
        grp.style.display = (groupCounts[gid] > 0) ? '' : 'none';
      }}
    }});

    if (clearBtn) {{
      clearBtn.hidden = !query;
    }}
  }}

  if (searchInput) {{
    searchInput.addEventListener('input', filterRoster);
  }}
  if (clearBtn) {{
    clearBtn.addEventListener('click', function() {{
      searchInput.value = '';
      filterRoster();
      searchInput.focus();
    }});
  }}

  if (pillContainer) {{
    pillContainer.addEventListener('click', function(e) {{
      var btn = e.target.closest('.roster-pill');
      if (!btn) return;
      pillContainer.querySelectorAll('.roster-pill').forEach(function(p) {{
        p.classList.remove('active');
      }});
      btn.classList.add('active');
      activeGroup = btn.getAttribute('data-roster-group') || 'all';
      filterRoster();
    }});
  }}

  var thead = table.querySelector('thead');
  var currentSortCol = -1;
  var currentSortAsc = true;
  var originalRows = null;

  if (thead) {{
    var ths = thead.querySelectorAll('th');
    ths.forEach(function(th, colIdx) {{
      th.classList.add('sortable');
      var indicator = document.createElement('span');
      indicator.className = 'sort-indicator dim';
      indicator.textContent = ' ↕';
      th.appendChild(indicator);

      th.addEventListener('click', function() {{
        var tbody = table.querySelector('tbody');
        if (!tbody) return;

        if (!originalRows) {{
          originalRows = Array.prototype.slice.call(tbody.children);
        }}

        if (currentSortCol === colIdx) {{
          if (currentSortAsc) {{
            currentSortAsc = false;
          }} else {{
            currentSortCol = -1;
            currentSortAsc = true;
            ths.forEach(function(h) {{
              var ind = h.querySelector('.sort-indicator');
              if (ind) {{ ind.textContent = ' ↕'; ind.className = 'sort-indicator dim'; }}
            }});
            originalRows.forEach(function(node) {{ tbody.appendChild(node); }});
            filterRoster();
            return;
          }}
        }} else {{
          currentSortCol = colIdx;
          currentSortAsc = true;
        }}

        ths.forEach(function(h, idx) {{
          var ind = h.querySelector('.sort-indicator');
          if (!ind) return;
          if (idx === currentSortCol) {{
            ind.textContent = currentSortAsc ? ' ▲' : ' ▼';
            ind.className = 'sort-indicator';
          }} else {{
            ind.textContent = ' ↕';
            ind.className = 'sort-indicator dim';
          }}
        }});

        table.querySelectorAll('tbody tr.grp').forEach(function(grp) {{
          grp.style.display = 'none';
        }});

        var dataRows = Array.prototype.slice.call(tbody.querySelectorAll('tr[data-row]'));
        dataRows.sort(function(a, b) {{
          var aCells = a.querySelectorAll('td');
          var bCells = b.querySelectorAll('td');
          var aText = (aCells[colIdx] ? aCells[colIdx].textContent : '').trim().toLowerCase();
          var bText = (bCells[colIdx] ? bCells[colIdx].textContent : '').trim().toLowerCase();

          if (aText === bText) return 0;
          var res = aText.localeCompare(bText, undefined, {{ numeric: true, sensitivity: 'base' }});
          return currentSortAsc ? res : -res;
        }});

        dataRows.forEach(function(r) {{ tbody.appendChild(r); }});
        filterRoster();
      }});
    }});
  }}
}})();
(function initEmailControls() {{
  var table = document.getElementById('emails-table');
  if (!table) return;

  var searchInput = document.getElementById('email-search');
  var clearBtn = document.getElementById('email-search-clear');
  var pillContainer = document.getElementById('email-filters');
  var activeFilter = 'all';

  function filterEmails() {{
    var query = (searchInput ? searchInput.value : '').toLowerCase().trim();
    var rows = table.querySelectorAll('tbody tr[data-sequence]');

    rows.forEach(function(row) {{
      var moreRow = row.nextElementSibling;
      var text = (row.textContent || '').toLowerCase();
      var personaText = (row.children && row.children[0] ? row.children[0].textContent : '').toLowerCase();

      var matchesFilter = true;
      if (activeFilter === 'all') {{
        matchesFilter = true;
      }} else if (activeFilter.indexOf('persona:') === 0) {{
        var targetPersona = activeFilter.substring(8).toLowerCase();
        matchesFilter = personaText.indexOf(targetPersona) !== -1;
      }} else if (activeFilter === 'status:ready') {{
        // data-check, set by views_emails._row from the SAME _is_blocked rule the
        // "Needs Review (N)" pill count uses — not a text scan. "blocking" is also a
        // substring of the ready cell's own "no blocking problems", which used to make
        // this match every row, checked and clean alike.
        matchesFilter = row.getAttribute('data-check') === 'ready';
      }} else if (activeFilter === 'status:blocked') {{
        matchesFilter = row.getAttribute('data-check') === 'blocked';
      }}

      var matchesQuery = true;
      if (query) {{
        matchesQuery = text.indexOf(query) !== -1;
      }}

      var isVisible = matchesFilter && matchesQuery;
      row.style.display = isVisible ? '' : 'none';
      if (moreRow && moreRow.classList.contains('more')) {{
        moreRow.style.display = isVisible ? '' : 'none';
      }}
    }});

    if (clearBtn) {{
      clearBtn.hidden = !query;
    }}
  }}

  if (searchInput) {{
    searchInput.addEventListener('input', filterEmails);
  }}
  if (clearBtn) {{
    clearBtn.addEventListener('click', function() {{
      searchInput.value = '';
      filterEmails();
      searchInput.focus();
    }});
  }}

  if (pillContainer) {{
    pillContainer.addEventListener('click', function(e) {{
      var btn = e.target.closest('.email-pill');
      if (!btn) return;
      pillContainer.querySelectorAll('.email-pill').forEach(function(p) {{
        p.classList.remove('active');
      }});
      btn.classList.add('active');
      activeFilter = btn.getAttribute('data-email-filter') || 'all';
      filterEmails();
    }});
  }}
}})();
(function initInsightsControls() {{
  var pillContainer = document.getElementById('insight-lens-filters');
  if (!pillContainer) return;

  var activeLens = 'all';

  function filterLens() {{
    var insightCards = document.querySelectorAll('#p-insights .card[data-lens]');
    insightCards.forEach(function(card) {{
      var lens = card.getAttribute('data-lens') || '';
      var show = (activeLens === 'all' || lens === activeLens);
      card.style.display = show ? '' : 'none';
      var parentSection = card.closest('[data-section]');
      if (parentSection && parentSection.getAttribute('data-section') !== 'learnings') {{
        parentSection.style.display = show ? '' : 'none';
      }}
    }});
  }}

  pillContainer.addEventListener('click', function(e) {{
    var btn = e.target.closest('.lens-pill');
    if (!btn) return;
    pillContainer.querySelectorAll('.lens-pill').forEach(function(p) {{
      p.classList.remove('active');
    }});
    btn.classList.add('active');
    activeLens = btn.getAttribute('data-lens') || 'all';
    filterLens();
  }});
}})();
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


def _validate_profile(
    profile: str,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
) -> None:
    p_root = profiles_root if profiles_root is not None else resolve_profiles_root()
    c_root = content_root if content_root is not None else resolve_content_root()
    if not (p_root / profile).is_dir() and not (c_root / profile).is_dir():
        raise FileNotFoundError(f"Profile {profile!r} does not exist in {p_root}")


def render_dashboard(
    profile: str,
    content_root: Path | None = None,
    *,
    stubs: bool = True,
    campaign: str | None = None,
    scope: str | None = None,
    profiles_root: Path | None = None,
) -> Path:
    """Render the status page at one scope.

    ``scope`` is ``campaign``/``open``/``all``; omitting it means ``campaign`` when slugs
    were named and ``all`` otherwise, so every existing caller — the `prospect` skill and
    the no-argument refresh at the tail of `consolidate` — keeps its behaviour exactly.
    """
    _validate_profile(profile, profiles_root=profiles_root, content_root=content_root)
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
    profiles_root: Path | None = None,
) -> Report:
    """Is the page for this scope still built from what is on disk now?

    Read-only, and deliberately separate from rendering: a render always produces a fresh
    page, so a check folded into it could only ever pass. The question that matters is
    asked LATER — which is exactly when nobody asks it.
    """
    _validate_profile(profile, profiles_root=profiles_root, content_root=content_root)
    from ..campaigns_dashboard import _load_manifests

    manifests = _load_manifests(profile, content_root)
    sc = resolve(_mode(scope, campaign, manifests), campaign, manifests)
    root, _ = input_globs(profile, content_root)
    return verify_inventory(page_path(profile, content_root, sc), root, profile=profile)


def refresh_all(
    profile: str,
    content_root: Path | None = None,
    *,
    stubs: bool = True,
    profiles_root: Path | None = None,
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
