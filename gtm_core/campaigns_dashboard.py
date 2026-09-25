"""The campaigns MODEL for every email **campaign** running for a profile —
``build_campaigns``, ``_load_manifests``, ``_campaigns_dir``, plus
``_experiment_block`` — the CRO/sales-leader layer above
:mod:`gtm_core.prospects_dashboard` (rep-facing, pipeline-wide) and the
per-sequence spec (`email-sequence` skill, one cadence). Model, plus one
HTML fragment (``_experiment_block``) the live renderer embeds; it writes
no page.

Imported by the one live renderer, :mod:`gtm_core.email_campaign_dashboard`
(``--scope all`` is the portfolio page; ``campaigns.html`` is a redirect
stub it writes to the old path) — and also by ``gtm_core/retention_sweep.py``
and ``gtm_core/retention_campaign_gate.py`` (the latter the only user of
``_campaigns_dir``). Don't delete those importers on the assumption the
renderer is the only consumer.

Three vocabulary layers exist on disk and this module joins them:

  * **Campaign** — one strategic push with a CRO business case + targets,
    recorded as a ``*.campaign.toml`` manifest under ``plans/campaigns/``.
  * **Sequence** — one staged email cadence (Saleshandy), recorded as a
    ``sequence_staged`` event in ``history.jsonl`` and, once live, actuals in
    ``sequence-stats.json``.
  * **Prospect pool** — the pipeline model
    (``prospects_dashboard.build_status``), shown on the same page;
    ``prospects/status.html`` is a redirect stub too.

There is no shared key between a campaign and its sequences on disk today, so
the join is explicit and dual-path (see ``build_campaigns`` below): a
sequence belongs to a campaign if its ``sequence_staged`` event carries a
matching ``campaign`` tag, OR the manifest's ``sequences`` list names its id.
Sequences matching neither are collected in ``unlinked_sequences``; the
renderer counts them as on record and uses them in its snapshot-vs-ledger
check, but does not list them.

Stdlib-only (like :mod:`gtm_core.prospects_dashboard`). Its model is rebuilt
at the tail of every ``consolidate()`` run, by the renderer that imports it.
Numbers are read from files on disk, never recomputed.
"""

from __future__ import annotations

import glob
import html
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.prospect_lede import go_live
from gtm_core.prospects_consolidate import _prospects_dir
from gtm_core.prospects_dashboard import load_sequence_snapshot
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


#: PS20 P3.1 — the sequencer's own reply-label vocabulary (kept by ``_normalize_seq`` in
#: ``prospects_dashboard.py``), summed alongside the existing send/reply keys. ``bounced``
#: and ``delivered`` are NOT here — they are an EMAIL count, a different unit from ``sent``
#: (people), and are only trustworthy over a row whose per-email status block was present;
#: see the ``bounce_source`` handling in ``build_campaigns`` below.
_TOTAL_KEYS = (
    "sent",
    "replied",
    "meetings",
    "loaded",
    "interested",
    "not_interested",
    "not_now",
    "out_of_office",
    "unsubscribed",
    "do_not_contact",
)


def _zero_totals() -> dict:
    return dict.fromkeys(_TOTAL_KEYS, 0) | {"bounced": 0, "delivered": 0, "bounce_unavailable": 0}


def build_campaigns(profile: str, content_root: Path | None = None) -> dict:
    """Assemble the joined campaigns model from files on disk. No MCP, no re-sweep."""
    manifests = _load_manifests(profile, content_root)
    staged = _staged_sequences(profile, content_root)
    snap = load_sequence_snapshot(profile, content_root)
    live_by_id = {s["id"]: s for s in snap["rows"] if s.get("id")}

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
        totals = _zero_totals()
        archived_totals = _zero_totals()
        # The current (non-archived) sequences' SNAPSHOT statuses — never the ledger's
        # staged-event status, which reads "paused" by construction. Fed to go_live below.
        statuses = []
        for sid in sorted(seq_ids | archived_ids):
            base = staged_by_id.get(sid, {"sequence_id": sid, "campaign": slug, "status": ""})
            live = live_by_id.get(sid, {})
            row = {**base, "live": live}
            target = archived_totals if sid in archived_ids else totals
            for k in _TOTAL_KEYS:
                target[k] += _int(live.get(k, 0))
            # PS20 P3.5 — a bounce figure is only trustworthy when the per-email status
            # block was present on this row (``_normalize_seq``'s ``bounce_source``).
            # Without it — the prospect-level fallback, the flat-dict path, or no live
            # entry at all — the row cannot contribute a bounce count, so it is tallied
            # as unavailable instead of mixed into a sum of a different unit.
            if live.get("bounce_source") == "emails":
                target["bounced"] += _int(live.get("bounced", 0))
                target["delivered"] += _int(live.get("delivered", 0))
            else:
                target["bounce_unavailable"] += 1
            if sid in archived_ids:
                archived_seqs.append(row)
                continue
            statuses.append(live.get("status"))
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
                # PS20 P1.10 — the one liveness rule. Stated explicitly so the page can say
                # so instead of implying progress through a row of 0% bars. on_record counts
                # only CURRENT sequences: a sequence can reach seq_ids via its staged-event
                # campaign tag even after the manifest archives it, so seq_ids alone would
                # call an archived-only campaign "staged".
                "state": go_live(
                    statuses,
                    totals["sent"],
                    bool(seq_ids - archived_ids),
                    readable=not snap["unreadable"],
                ),
                "actuals": totals,
                "promised_vs_actual": _promised_vs_actual(m.get("targets", {}), totals),
                # Optional blocks — a campaign without them renders exactly as before.
                "targets_superseded": m.get("targets_superseded", {}),
                "targets_derivation": m.get("targets_derivation", {}),
                # Where this campaign's OWN accounts live (run-export globs, relative to the
                # prospects dir). Carried through so a campaign-scoped page can count its own
                # roster instead of the shared pool. Absent for a campaign that declares none.
                "roster_globs": m.get("roster_globs", []),
                "window": m.get("window", {}),
                "experiment": m.get("experiment", {}),
            }
        )

    unlinked_sequences = [s for s in staged if s["sequence_id"] not in matched_ids]

    return {
        "profile": profile,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M timezone.utc"),
        "campaigns": campaigns,
        "unlinked_sequences": unlinked_sequences,
    }


# --- HTML ---------------------------------------------------------------


#: PS20 P1.5: "can answer" is done, so ok; the other two are states, so neutral.
_HYP_CLS = {"can answer": "pill ok", "list too small": "pill", "not set up": "pill"}


def _experiment_block(x: dict) -> str:
    """The commercial read: what this run can and cannot tell us, the questions
    it was meant to answer, and how to avoid over-reading it. Everything here is
    the manifest's own: engine code states nothing about a campaign (PS20 P1.7)."""
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
        )

    if x.get("measurement"):
        yes = '<span class="pill">yes</span>'
        no = '<span class="pill">no</span>'
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
                f'<span class="{_HYP_CLS.get(st, "pill")}">{html.escape(st or "—")}</span></div>'
                f'<div class="hypbody">{html.escape(str(h.get("verdict", "")))}</div>'
                f'<div class="hypneed"><b>To answer it we need:</b> '
                f"{html.escape(str(h.get('needs', '')))}</div>"
                f"</div>"
            )
        n = len(rows)
        out.append(
            f"<h3>The {n} question{'' if n == 1 else 's'} we set out to answer</h3>" + "".join(rows)
        )

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
