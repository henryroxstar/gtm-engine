"""Render a single, self-refreshing HTML portfolio page for every email
**campaign** running for a profile — the CRO/sales-leader layer above
:mod:`gtm_core.prospects_dashboard` (rep-facing, pipeline-wide) and the
per-sequence spec (`email-sequence` skill, one cadence).

Three vocabulary layers exist on disk and this module joins them:

  * **Campaign** — one strategic push with a CRO business case + targets,
    recorded as a ``*.campaign.toml`` manifest under ``plans/campaigns/``.
  * **Sequence** — one staged email cadence (Saleshandy), recorded as a
    ``sequence_staged`` event in ``history.jsonl`` and, once live, actuals in
    ``sequence-stats.json``.
  * **Prospect pool** — unchanged, ``status.html``.

There is no shared key between a campaign and its sequences on disk today, so
the join is explicit and dual-path (see ``_campaign_sequences`` below): a
sequence belongs to a campaign if its ``sequence_staged`` event carries a
matching ``campaign`` tag, OR the manifest's ``sequences`` list names its id.
Sequences matching neither show up in an honest "Unlinked sequences" section.

Stdlib-only (like :mod:`gtm_core.prospects_dashboard`), auto-refreshed at the
tail of every ``consolidate()`` run. Numbers are read from files on disk,
never recomputed.
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.prospects_consolidate import _pool_dir, _prospects_dir
from gtm_core.prospects_dashboard import _load_sequences
from gtm_core.prospects_import import _ledgers


def _campaigns_dir(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root).parent / "plans" / "campaigns"


def _load_manifests(profile: str, content_root: Path | None = None) -> list[dict]:
    """Every ``*.campaign.toml`` under ``plans/campaigns/``. Missing/malformed
    files are skipped, not fatal. Every field optional except ``slug``."""
    out = []
    for p in sorted(glob.glob(str(_campaigns_dir(profile, content_root) / "*.campaign.toml"))):
        try:
            with open(p, "rb") as f:
                m = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if not m.get("slug"):
            continue
        m.setdefault("targets", {})
        m.setdefault("sequences", [])
        out.append(m)
    return out


def _staged_sequences(profile: str, content_root: Path | None = None) -> list[dict]:
    """``sequence_staged`` events minus anything a later ``sequence_cleanup``
    deleted. Tolerates both historical key shapes (``steps``/``prospects_enrolled``
    vs. ``touches``/``leads_enrolled``)."""
    led = _ledgers(profile, content_root)
    staged: dict[str, dict] = {}
    deleted: set[str] = set()
    for ev in led.iter_history():
        e = ev.get("event")
        if e == "sequence_staged" and ev.get("sequence_id"):
            sid = ev["sequence_id"]
            staged[sid] = {
                "sequence_id": sid,
                "campaign": ev.get("campaign", ""),
                "provider": ev.get("provider", ""),
                "status": ev.get("status", ""),
                "enrolled": ev.get("leads_enrolled", ev.get("prospects_enrolled", 0)),
                "steps": ev.get("touches", ev.get("steps", 0)),
                "spec": ev.get("spec", ev.get("asset_id", "")),
                "staged_ts": ev.get("ts", ""),
            }
        elif e == "sequence_cleanup":
            for d in ev.get("sequences_deleted", []):
                if d.get("id"):
                    deleted.add(d["id"])
    return [s for sid, s in staged.items() if sid not in deleted]


def _int(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _promised_vs_actual(targets: dict, actuals: dict) -> dict:
    metric_map = {"emails": "sent", "replies": "replied", "sqls": "meetings", "pilots": "pilots"}
    out = {}
    for target_key, actual_key in metric_map.items():
        if target_key not in targets:
            continue
        target = _int(targets[target_key])
        actual = _int(actuals.get(actual_key, 0))
        pct = round(100 * actual / target) if target else 0
        out[target_key] = {"target": target, "actual": actual, "pct": pct}
    return out


def build_campaigns(profile: str, content_root: Path | None = None) -> dict:
    """Assemble the joined campaigns model from files on disk. No MCP, no re-sweep."""
    manifests = _load_manifests(profile, content_root)
    staged = _staged_sequences(profile, content_root)
    live_by_id = {s["id"]: s for s in _load_sequences(profile, content_root) if s.get("id")}

    staged_by_id = {s["sequence_id"]: s for s in staged}

    matched_ids: set[str] = set()
    campaigns = []
    for m in manifests:
        slug = m["slug"]
        manifest_ids = set(m.get("sequences") or [])
        # A sequence belongs to this campaign if its staged event is tagged with the slug,
        # OR the manifest names its id. The manifest path also admits a sequence that was
        # never logged as staged (e.g. a hand-loaded live sequence) — it is synthesized
        # from its live stats so a real, running sequence is never invisible on the page.
        seq_ids = {sid for sid, s in staged_by_id.items() if s["campaign"] == slug}
        seq_ids |= manifest_ids
        matched_ids.update(seq_ids)

        enriched_seqs = []
        totals = {"sent": 0, "replied": 0, "meetings": 0}
        for sid in sorted(seq_ids):
            base = staged_by_id.get(sid, {"sequence_id": sid, "campaign": slug, "status": ""})
            live = live_by_id.get(sid, {})
            for k in totals:
                totals[k] += _int(live.get(k, 0))
            enriched_seqs.append({**base, "live": live})

        campaigns.append(
            {
                "slug": slug,
                "title": m.get("title", slug),
                "status": m.get("status", ""),
                "plan_md": m.get("plan_md", ""),
                "plan_html": m.get("plan_html", ""),
                "targets": m.get("targets", {}),
                "sequences": enriched_seqs,
                "actuals": totals,
                "promised_vs_actual": _promised_vs_actual(m.get("targets", {}), totals),
            }
        )

    unlinked_sequences = [s for s in staged if s["sequence_id"] not in matched_ids]

    return {
        "profile": profile,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "campaigns": campaigns,
        "unlinked_sequences": unlinked_sequences,
    }


# --- HTML ---------------------------------------------------------------


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d > 0 else "—"


def _stat(label: str, value, sub: str = "") -> str:
    sub_html = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ""
    return (
        f'<div class="stat"><div class="num">{value:,}</div>'
        f'<div class="lbl">{html.escape(label)}</div>{sub_html}</div>'
    )


def render_html(model: dict) -> str:
    prof = html.escape(model["profile"])
    campaigns = model["campaigns"]
    unlinked = model["unlinked_sequences"]

    total_sequences = sum(len(c["sequences"]) for c in campaigns) + len(unlinked)
    total_meetings = sum(c["actuals"]["meetings"] for c in campaigns)
    total_sent = sum(c["actuals"]["sent"] for c in campaigns)
    active = sum(1 for c in campaigns if c["status"] == "active")

    portfolio_strip = f"""
    <div class="card next">
      <h2>Portfolio at a glance</h2>
      <div class="stats">
        {_stat("active campaigns", active)}
        {_stat("sequences live", total_sequences)}
        {_stat("meetings booked (SQL proxy)", total_meetings)}
        {_stat("emails sent", total_sent)}
      </div>
    </div>"""

    campaign_cards = []
    for c in campaigns:
        pva_rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td class='num-cell'>{v['target']:,}</td>"
            f"<td class='num-cell'>{v['actual']:,}</td><td class='num-cell'>{v['pct']}%</td></tr>"
            for k, v in c["promised_vs_actual"].items()
        )
        pva_block = (
            f"""<h3>Promised vs actual</h3>
            <table><thead><tr><th>Metric</th><th>Target</th><th>Actual</th><th>%</th></tr></thead>
            <tbody>{pva_rows}</tbody></table>"""
            if pva_rows
            else "<p class='note'>No targets recorded for this campaign yet.</p>"
        )

        seq_rows = "".join(
            f"<tr><td>{html.escape(s.get('live', {}).get('name', '') or s['sequence_id'])}</td>"
            f"<td>{html.escape(str(s.get('live', {}).get('status') or s.get('status') or '—'))}</td>"
            f"<td class='num-cell'>{_int(s.get('live', {}).get('sent', 0)):,}</td>"
            f"<td class='num-cell'>{_int(s.get('live', {}).get('replied', 0)):,}</td>"
            f"<td class='num-cell'>{_int(s.get('live', {}).get('meetings', 0)):,}</td></tr>"
            for s in c["sequences"]
        )
        seq_block = (
            f"""<h3>Sequences</h3>
            <table><thead><tr><th>Name</th><th>Status</th><th>Sent</th><th>Replied</th><th>Meetings</th></tr></thead>
            <tbody>{seq_rows}</tbody></table>"""
            if seq_rows
            else "<p class='note'>No sequences staged under this campaign yet.</p>"
        )

        plan_links = ""
        if c["plan_html"] or c["plan_md"]:
            links = []
            if c["plan_html"]:
                links.append(
                    f'<a href="plans/campaigns/{html.escape(c["plan_html"])}">plan (html)</a>'
                )
            if c["plan_md"]:
                links.append(f'<a href="plans/campaigns/{html.escape(c["plan_md"])}">plan (md)</a>')
            plan_links = f'<p class="note">{" · ".join(links)}</p>'

        status = html.escape(c["status"] or "—")
        campaign_cards.append(f"""
        <div class="card">
          <h2>{html.escape(c["title"])} <span class="pill">{status}</span></h2>
          {plan_links}
          {pva_block}
          {seq_block}
        </div>""")

    unlinked_block = ""
    if unlinked:
        rows = "".join(
            f"<tr><td>{html.escape(s['sequence_id'])}</td><td>{html.escape(str(s.get('status', '') or '—'))}</td>"
            f"<td class='num-cell'>{_int(s.get('enrolled', 0)):,}</td></tr>"
            for s in unlinked
        )
        unlinked_block = f"""
        <div class="card">
          <h2>Unlinked sequences</h2>
          <p class="note">Staged sequences with no campaign tag or manifest link — tag these with
          <code>campaign:</code> in the sequence spec to roll them up.</p>
          <table><thead><tr><th>Sequence ID</th><th>Status</th><th>Enrolled</th></tr></thead>
          <tbody>{rows}</tbody></table>
        </div>"""

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campaigns · {prof}</title>
<style>
:root {{
  --bg:#0b0d12; --panel:#141821; --panel2:#1b202b; --ink:#e7ecf3; --muted:#8b96a8;
  --line:#252b38; --ready:#2ecc71; --verify:#f1c40f; --accent:#7c5cff;
}}
@media (prefers-color-scheme: light) {{
  :root {{ --bg:#f6f7f9; --panel:#fff; --panel2:#f0f2f6; --ink:#1a1f2b; --muted:#5b6675;
    --line:#e3e7ee; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:32px 20px; }}
.wrap {{ max-width:960px; margin:0 auto; }}
header {{ display:flex; align-items:baseline; justify-content:space-between; flex-wrap:wrap; gap:8px; margin-bottom:24px; }}
h1 {{ font-size:22px; margin:0; letter-spacing:-.02em; }}
h1 span {{ color:var(--accent); }}
h2 {{ font-size:17px; margin:0 0 12px; display:flex; align-items:center; gap:10px; }}
h3 {{ font-size:13px; text-transform:uppercase; letter-spacing:.02em; color:var(--muted); margin:18px 0 8px; }}
.ts {{ color:var(--muted); font-size:13px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:20px 22px; margin-bottom:18px; }}
.stats {{ display:flex; gap:14px; flex-wrap:wrap; }}
.stat {{ background:var(--panel2); border-radius:10px; padding:14px 16px; min-width:120px; flex:1; }}
.stat .num {{ font-size:28px; font-weight:700; letter-spacing:-.03em; }}
.stat .lbl {{ color:var(--muted); font-size:13px; margin-top:2px; }}
.muted {{ color:var(--muted); }}
.note {{ font-size:13px; color:var(--muted); margin:12px 0 0; padding-left:12px; border-left:3px solid var(--accent); }}
.pill {{ font-size:11px; padding:2px 8px; border-radius:999px; background:rgba(124,92,255,.15); color:var(--accent); text-transform:uppercase; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--muted); font-weight:600; padding:6px 10px; border-bottom:1px solid var(--line); }}
td {{ padding:8px 10px; border-bottom:1px solid var(--line); }}
.num-cell {{ white-space:nowrap; font-variant-numeric:tabular-nums; }}
code {{ background:var(--panel2); padding:1px 6px; border-radius:5px; font-size:12px; }}
.next {{ background:linear-gradient(135deg,rgba(124,92,255,.10),rgba(74,168,255,.06)); }}
a {{ color:var(--accent); }}
</style></head><body><div class="wrap">
<header>
  <h1>Campaigns <span>· {prof}</span></h1>
  <div class="ts">refreshed {html.escape(model["generated_at"])}</div>
</header>
{portfolio_strip}
{"".join(campaign_cards)}
{unlinked_block}
<div class="card">
  <p class="note"><b>Meetings booked is used as an SQL proxy</b> — a true SQL requires a human
  judgment call this pipeline doesn't automate yet. Pilot counts are hand-tagged, same honesty
  discipline as the prospect pipeline's "Loaded/Sent not tracked per-account yet" note.</p>
</div>
<p class="muted" style="font-size:12px;text-align:center;margin-top:24px">
Generated by <code>gtm_core.campaigns_dashboard</code> · numbers read from files on disk, never recomputed.
</p>
</div></body></html>"""


def dashboard_path(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root).parent / "campaigns.html"


def render_dashboard(profile: str, content_root: Path | None = None) -> Path:
    """Build the campaigns model and write ``campaigns.html`` + a machine
    ``.pool/campaigns.json``. Returns the HTML path."""
    model = build_campaigns(profile, content_root)
    out = dashboard_path(profile, content_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(model), encoding="utf-8")
    pool = _pool_dir(profile, content_root)
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "campaigns.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.campaigns_dashboard")
    ap.add_argument("--profile", required=True)
    ap.add_argument(
        "--json", action="store_true", help="print the campaigns model instead of writing HTML"
    )
    args = ap.parse_args(argv)
    if args.json:
        json.dump(build_campaigns(args.profile), sys.stdout, indent=2, default=list)
        sys.stdout.write("\n")
        return 0
    path = render_dashboard(args.profile)
    print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
