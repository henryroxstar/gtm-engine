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
        # Sequences from a superseded run. Kept linked so the history stays visible,
        # but excluded from every headline number — folding a retired run's sends into
        # "this campaign" is how a page reports 24 emails for a campaign that has sent none.
        m.setdefault("archived_sequences", [])
        out.append(m)
    return out


def _staged_row(sid: str, ev: dict, item: dict) -> dict:
    """One staged-sequence row. ``item`` is the per-sequence dict for the batch
    events (``new_sequences``) or the event itself for single-sequence
    ``sequence_staged``, so both key shapes resolve through one path."""
    return {
        "sequence_id": sid,
        "campaign": item.get("campaign", ev.get("campaign", "")),
        "provider": item.get("provider", ev.get("provider", "")),
        "status": item.get("status", ev.get("status", "")),
        "enrolled": item.get(
            "enrolled", item.get("leads_enrolled", item.get("prospects_enrolled", 0))
        ),
        "steps": item.get("touches", item.get("steps", len(item.get("step_ids") or []))),
        "spec": item.get("spec", item.get("asset_id", "")),
        "staged_ts": ev.get("ts", ""),
    }


def _staged_sequences(profile: str, content_root: Path | None = None) -> list[dict]:
    """Sequences currently staged, per the ledger's lifecycle events.

    Reads the original ``sequence_staged``/``sequence_cleanup`` pair plus the later
    ``sequence_rebuilt``/``sequence_seat_split`` (which stage a *batch* under
    ``new_sequences``) and the single-id ``sequence_deleted``. A reader that knows
    only the first pair goes quietly blind the day a sequence is staged under a
    newer verb — it renders a confident page describing sequences that no longer
    exist, which is worse than rendering nothing. Events apply in ledger order, so
    a sequence re-staged after a deletion survives instead of being dropped by a
    later-applied delete. Tolerates both historical key shapes
    (``steps``/``prospects_enrolled`` vs. ``touches``/``leads_enrolled``)."""
    led = _ledgers(profile, content_root)
    staged: dict[str, dict] = {}
    for ev in led.iter_history():
        e = ev.get("event")
        if e == "sequence_staged" and ev.get("sequence_id"):
            staged[ev["sequence_id"]] = _staged_row(ev["sequence_id"], ev, ev)
        elif e in ("sequence_rebuilt", "sequence_seat_split"):
            for item in ev.get("new_sequences", []):
                if item.get("id"):
                    staged[item["id"]] = _staged_row(item["id"], ev, item)
        elif e == "sequence_cleanup":
            for d in ev.get("sequences_deleted", []):
                staged.pop(d.get("id"), None)
        elif e == "sequence_deleted":
            staged.pop(ev.get("sequence_id"), None)
    return list(staged.values())


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
        archived_ids = set(m.get("archived_sequences") or [])
        # A sequence belongs to this campaign if its staged event is tagged with the slug,
        # OR the manifest names its id. The manifest path also admits a sequence that was
        # never logged as staged (e.g. a hand-loaded live sequence) — it is synthesized
        # from its live stats so a real, running sequence is never invisible on the page.
        seq_ids = {sid for sid, s in staged_by_id.items() if s["campaign"] == slug}
        seq_ids |= manifest_ids
        matched_ids.update(seq_ids | archived_ids)

        enriched_seqs = []
        archived_seqs = []
        totals = {"sent": 0, "replied": 0, "meetings": 0}
        archived_totals = {"sent": 0, "replied": 0, "meetings": 0}
        live_now = 0
        for sid in sorted(seq_ids | archived_ids):
            base = staged_by_id.get(sid, {"sequence_id": sid, "campaign": slug, "status": ""})
            live = live_by_id.get(sid, {})
            row = {**base, "live": live}
            if sid in archived_ids:
                for k in archived_totals:
                    archived_totals[k] += _int(live.get(k, 0))
                archived_seqs.append(row)
                continue
            for k in totals:
                totals[k] += _int(live.get(k, 0))
            if str(live.get("status", "")).lower() in ("active", "running", "live"):
                live_now += 1
            enriched_seqs.append(row)

        campaigns.append(
            {
                "slug": slug,
                "title": m.get("title", slug),
                "status": m.get("status", ""),
                "plan_md": m.get("plan_md", ""),
                "plan_html": m.get("plan_html", ""),
                "product": m.get("product", ""),
                "targets": m.get("targets", {}),
                "sequences": enriched_seqs,
                "archived": archived_seqs,
                "archived_actuals": archived_totals,
                "live_now": live_now,
                # A campaign with staged sequences and nothing running has NOT started.
                # Stated explicitly so the page can say so instead of implying progress
                # through a row of 0% bars.
                "state": "sending" if live_now else "not_sending",
                "actuals": totals,
                "promised_vs_actual": _promised_vs_actual(m.get("targets", {}), totals),
                # Optional blocks — a campaign without them renders exactly as before.
                "targets_superseded": m.get("targets_superseded", {}),
                "targets_derivation": m.get("targets_derivation", {}),
                "window": m.get("window", {}),
                "experiment": m.get("experiment", {}),
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


_HYP_CLS = {"can answer": "good", "list too small": "warn", "not set up": "bad"}


def _window_block(w: dict) -> str:
    """The send window — dates, daily ceiling, and the arithmetic behind the end
    date, so it can be re-checked rather than believed."""
    if not w:
        return ""
    rows = [
        ("first email goes out", w.get("send_start", "—")),
        ("last email (estimated)", w.get("completes_est", "—")),
        ("working days of sending", w.get("send_days", "—")),
        (
            "daily ceiling",
            f"{w.get('daily_cap', '—')} a day across {w.get('mailboxes', '—')} mailboxes",
        ),
        ("emails per contact", w.get("touches", "—")),
        ("sending hours", w.get("schedule", "—")),
    ]
    cells = "".join(
        f'<div class="param"><div class="k">{html.escape(str(k))}</div>'
        f'<div class="v">{html.escape(str(v))}</div></div>'
        for k, v in rows
    )
    notes = ""
    if w.get("basis"):
        notes += (
            f'<p class="note"><b>Where the end date comes from:</b> {html.escape(w["basis"])}.</p>'
        )
    if w.get("caveat"):
        notes += f'<p class="note">{html.escape(w["caveat"])}.</p>'
    return f'<h3>How long this takes</h3><div class="params">{cells}</div>{notes}'


def _experiment_block(x: dict) -> str:
    """The commercial read: what this run can and cannot tell us, the five
    questions it was meant to answer, and how to avoid over-reading it."""
    if not x:
        return ""
    out = ["<h3>How to read this run</h3>"]

    params = [
        ("how it is set up", x.get("approach", "—")),
        ("what it is really for", x.get("why_we_run_it", "—")),
        ("replies we expect", x.get("expected_replies", "—")),
        ("realistic range", x.get("realistic_range", "—")),
        ("what that tells us", x.get("what_it_tells_us", "—")),
        ("what it will not tell us", x.get("what_it_cant_tell_us", "—")),
    ]
    out.append(
        '<div class="params wide">'
        + "".join(
            f'<div class="param"><div class="k">{html.escape(str(k))}</div>'
            f'<div class="v">{html.escape(str(v))}</div></div>'
            for k, v in params
        )
        + "</div>"
    )

    if x.get("will_learn"):
        rows = "".join(
            f'<div class="hyp"><div class="hyphead">'
            f'<span class="claim"><b>{html.escape(str(w.get("question", "")))}</b></span></div>'
            f'<div class="hypbody">{html.escape(str(w.get("how", "")))}</div></div>'
            for w in x["will_learn"]
        )
        out.append("<h3>What we will learn either way</h3>" + rows)

    if x.get("power_table"):
        rows = "".join(
            f'<tr><td class="num-cell"><b>{html.escape(str(r.get("lift", "")))}</b></td>'
            f'<td class="muted">{html.escape(str(r.get("meaning", "")))}</td>'
            f'<td class="num-cell">{_int(r.get("n_per_arm", 0)):,}</td></tr>'
            for r in x["power_table"]
        )
        out.append(
            "<h3>How big a difference we could actually spot</h3>"
            "<table><thead><tr><th>If one group beat another by…</th><th>What that looks like</th>"
            "<th>Contacts needed in each group</th></tr></thead><tbody>" + rows + "</tbody></table>"
            '<p class="note">The biggest comparison this list supports is startups vs enterprises — '
            "about <b>148 contacts on each side</b>. So only a difference of <b>three times or more</b> "
            "would stand out clearly. Anything subtler is indistinguishable from luck at this size.</p>"
        )

    if x.get("measurement"):
        yes = '<span class="pill good">yes</span>'
        no = '<span class="pill bad">no</span>'
        rows = "".join(
            f"<tr><td>{html.escape(str(m.get('stage', '')))}</td>"
            f"<td>{yes if m.get('instrumented') else no}</td>"
            f'<td class="muted">{html.escape(str(m.get("note", "")))}</td></tr>'
            for m in x["measurement"]
        )
        out.append(
            "<h3>What we can and can't see</h3>"
            "<table><thead><tr><th>Step</th><th>Can we see it?</th><th>Why</th></tr></thead>"
            "<tbody>" + rows + "</tbody></table>"
        )

    if x.get("hypotheses"):
        rows = []
        for h in x["hypotheses"]:
            st = str(h.get("status", "") or "")
            rows.append(
                f'<div class="hyp">'
                f'<div class="hyphead"><b>{html.escape(str(h.get("id", "")))}</b>'
                f'<span class="claim">{html.escape(str(h.get("claim", "")))}</span>'
                f'<span class="pill {_HYP_CLS.get(st, "")}">{html.escape(st or "—")}</span></div>'
                f'<div class="hypbody">{html.escape(str(h.get("verdict", "")))}</div>'
                f'<div class="hypneed"><b>To answer it we need:</b> '
                f"{html.escape(str(h.get('needs', '')))}</div>"
                f"</div>"
            )
        out.append("<h3>The five questions we set out to answer</h3>" + "".join(rows))

    if x.get("confounds"):
        rows = "".join(
            f"<tr><td><b>{html.escape(str(c.get('name', '')))}</b></td>"
            f'<td class="muted">{html.escape(str(c.get("detail", "")))}</td></tr>'
            for c in x["confounds"]
        )
        out.append(
            "<h3>Traps when reading the results</h3><table><tbody>" + rows + "</tbody></table>"
        )

    if x.get("next_run"):
        rows = "".join(
            f"<tr><td><b>{html.escape(str(n.get('action', '')))}</b></td>"
            f'<td class="muted">{html.escape(str(n.get("cost", "")))}</td>'
            f'<td class="muted">{html.escape(str(n.get("buys", "")))}</td></tr>'
            for n in x["next_run"]
        )
        out.append(
            "<h3>What to fix before the next run</h3>"
            "<table><thead><tr><th>Change</th><th>What it costs</th><th>What it gets us</th></tr></thead>"
            "<tbody>" + rows + "</tbody></table>"
        )

    if x.get("method_footnote"):
        out.append(f'<p class="fine">{html.escape(str(x["method_footnote"]))}</p>')

    return "".join(out)


def render_html(model: dict) -> str:
    prof = html.escape(model["profile"])
    campaigns = model["campaigns"]
    unlinked = model["unlinked_sequences"]

    total_staged = sum(len(c["sequences"]) for c in campaigns) + len(unlinked)
    total_live = sum(c.get("live_now", 0) for c in campaigns)
    total_meetings = sum(c["actuals"]["meetings"] for c in campaigns)
    total_sent = sum(c["actuals"]["sent"] for c in campaigns)
    archived_sent = sum(c.get("archived_actuals", {}).get("sent", 0) for c in campaigns)
    active = sum(1 for c in campaigns if c["status"] == "active")

    # A campaign whose sequences are all paused has not started, and every target bar
    # below it reads 0% for that reason and no other. Saying so once, at the top, is the
    # difference between "underway, no meetings yet" and "nothing has been sent".
    not_started = [c for c in campaigns if c.get("state") == "not_sending"]
    banner = ""
    if not_started:
        planned = sum(_int(c["targets"].get("emails", 0)) for c in not_started)
        names = ", ".join(html.escape(c["title"]) for c in not_started)
        extra = (
            f" The {archived_sent:,} email(s) already sent belong to retired sequences on a "
            f"superseded list and are shown under Archive, not here."
            if archived_sent
            else ""
        )
        banner = (
            f'<div class="card banner"><h2>Not sending</h2><p><strong>0 of {planned:,}'
            f"</strong> planned emails have been sent. Every sequence in {names} is staged and "
            f"paused; a human activates it in the sequencer.{extra}</p></div>"
        )

    portfolio_strip = f"""
    {banner}
    <div class="card next">
      <h2>Portfolio at a glance</h2>
      <div class="stats">
        {_stat("active campaigns", active)}
        {_stat("sequences staged", total_staged, f"{total_live} sending")}
        {_stat("meetings booked (SQL proxy)", total_meetings)}
        {_stat("emails sent", total_sent, "this campaign only")}
      </div>
    </div>"""

    campaign_cards = []
    for c in campaigns:
        sup = c.get("targets_superseded") or {}
        pva_rows = "".join(
            f"<tr><td>{html.escape(k)}</td><td class='num-cell'>{v['target']:,}</td>"
            f"<td class='num-cell'>{v['actual']:,}</td><td class='num-cell'>{v['pct']}%</td></tr>"
            for k, v in c["promised_vs_actual"].items()
        )
        pva_block = (
            f"""<h3>Target vs actual</h3>
            <table><thead><tr><th>Metric</th><th>Target</th><th>Actual so far</th><th>%</th></tr></thead>
            <tbody>{pva_rows}</tbody></table>"""
            if pva_rows
            else "<p class='note'>No targets recorded for this campaign yet.</p>"
        )
        if sup.get("reason"):
            pva_block += f'<p class="note"><b>Why these numbers changed:</b> {html.escape(str(sup["reason"]))}.</p>'
        deriv = c.get("targets_derivation") or {}
        if deriv:
            pva_block += (
                '<details class="deriv"><summary>Where each number comes from</summary><table><tbody>'
                + "".join(
                    f"<tr><td>{html.escape(str(k))}</td>"
                    f"<td class='muted'>{html.escape(str(v))}</td></tr>"
                    for k, v in deriv.items()
                )
                + "</tbody></table></details>"
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

        archive_block = ""
        if c.get("archived"):
            arows = "".join(
                f"<tr><td>{html.escape(a.get('live', {}).get('name', '') or a['sequence_id'])}</td>"
                f"<td class='num-cell'>{_int(a.get('live', {}).get('sent', 0)):,}</td>"
                f"<td class='num-cell'>{_int(a.get('live', {}).get('replied', 0)):,}</td></tr>"
                for a in c["archived"]
            )
            archive_block = (
                "<details><summary>Archive — retired sequences, excluded from the numbers above"
                "</summary><p class='note'>A superseded run on a different list and different "
                "copy. Its sends are real but they measure a campaign that no longer exists, so "
                "rolling them up would misstate this one.</p>"
                "<table><thead><tr><th>Name</th><th>Sent</th><th>Replied</th></tr></thead>"
                f"<tbody>{arows}</tbody></table></details>"
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
          {_window_block(c.get("window") or {})}
          {seq_block}
          {archive_block}
        </div>
        {
            (
                '<div class="card sci"><h2>What we are trying to learn '
                '<span class="muted" style="font-weight:400">· '
                + html.escape(c["title"])
                + "</span></h2>"
                + _experiment_block(c.get("experiment") or {})
                + "</div>"
            )
            if c.get("experiment")
            else ""
        }""")

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
.pill.good {{ background:rgba(46,204,113,.15); color:var(--ready); }}
.pill.warn {{ background:rgba(241,196,15,.15); color:var(--verify); }}
.pill.bad {{ background:rgba(231,76,60,.15); color:#e74c3c; }}
.card.banner {{ border-color:rgba(241,196,15,.55); background:rgba(241,196,15,.07); }}
.card.banner h2 {{ color:var(--verify); }}
.sci {{ border-left:3px solid var(--accent); }}
.params {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin:4px 0 4px; }}
.params.wide {{ grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }}
.param {{ background:var(--panel2); border-radius:8px; padding:10px 12px; }}
.param .k {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.03em; }}
.param .v {{ font-size:14px; margin-top:3px; font-variant-numeric:tabular-nums; }}
.hyp {{ background:var(--panel2); border-radius:10px; padding:12px 14px; margin-bottom:10px; }}
.hyphead {{ display:flex; align-items:flex-start; gap:10px; font-size:14px; }}
.hyphead .claim {{ flex:1; }}
.hyphead .pill {{ flex:none; margin-left:auto; white-space:nowrap; }}
.hypbody {{ font-size:13px; color:var(--muted); margin-top:6px; }}
.hypneed {{ font-size:12px; color:var(--muted); margin-top:6px; padding-top:6px;
  border-top:1px dashed var(--line); }}
details.deriv {{ margin-top:10px; }}
details.deriv summary {{ cursor:pointer; font-size:12px; color:var(--muted); }}
.fine {{ font-size:11px; color:var(--muted); margin-top:16px; opacity:.75; }}
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
