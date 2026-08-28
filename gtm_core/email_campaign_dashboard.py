"""The email-campaign status page — written for a reader who does not work the tooling.

Supersedes ``status.html`` (pipeline, rep-facing) and ``campaigns.html`` (portfolio,
CRO-facing). Those were split **by audience**, which is the wrong axis: both readers need
both halves, and splitting there is what let a reply rate live on one page while the list
quality that explains it lived on another. This page splits **by question**:

* **Who we're emailing** — how many, where, which seat, and what we actually know.
* **What we're saying** — the template, the per-seat message, every subject line, the
  signals it opens on, and every check the copy passed.
* **What we'll learn** — the hypothesis, the parameters, and which comparisons are
  readable versus confounded.
* **Operator notes** — the mechanics: what is loaded in the sending tool right now, what
  still has to be pushed, and what is blocking the start. Split off because the reader this
  page is written for is not the person who presses the buttons, and the re-push procedure
  was displacing the numbers the page is actually opened for.

Two rules govern the wording. **No tool nouns**: a reader who has never opened the
sequencer should not meet the word "sequencer", "enrolled", "merge tag" or "variant"
without a plain-English gloss. And **no bare counts**: every number says what it means for
whether an email can be sent to that person today.

Stdlib-only and provider-free. The live numbers come from a snapshot the agent layer
drops by hand, so the page **reconciles it against the ledger** and says so loudly when
they disagree, rather than rendering a confident page about sequences that no longer exist.

CLI::

    python -m gtm_core.email_campaign_dashboard --profile P [--no-stubs]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from gtm_core.campaigns_dashboard import _experiment_block, build_campaigns
from gtm_core.cells import build_cells, intent_profile, supply_profile
from gtm_core.outcomes import read_outcomes
from gtm_core.paths import resolve_content_root
from gtm_core.prospects_consolidate import _pool_dir, _prospects_dir
from gtm_core.prospects_dashboard import build_status

TABS = (
    ("status", "Where things stand"),
    ("who", "Who we're emailing"),
    ("what", "What we're saying"),
    ("learn", "What we'll learn"),
    ("ops", "Operator notes"),
)

PAGE_NAME = "email_campaign_status.html"

#: The tenant's persona axis, from ``knowledge/voice.md``. Reproduced here as the page's
#: reference copy so a reader can see the rule the copy is written against without opening
#: the knowledge pack. Seven seats are defined; the automated resolver recognises three
#: (see :data:`SEAT_COVERAGE`) — that gap is the reason so many recipients read "unknown".
PERSONA_AXIS = (
    (
        "CEO / Founder",
        "the trust gap stalling an enterprise or partner deal",
        "close the logo your agents keep getting stuck on",
    ),
    (
        "CTO / Head of Platform",
        "identity and policy rebuilt per framework, burning 2–4 engineers",
        "one identity layer across your frameworks — ship, don't build",
    ),
    (
        "CISO / Head of Security",
        "cannot prove what agents did or who authorised them",
        "verifiable identity, tamper-evident audit, policy per action",
    ),
    (
        "Head of AI / Applied AI",
        "can build agents, cannot safely run them at scale",
        "reach production without the risk team stopping you",
    ),
    (
        "Chief Data Officer / DPO",
        "an agent on regulated data is a reportable breach",
        "delegation bound to the consenting human, auditor-ready",
    ),
    (
        "Head of Partnerships",
        "roadmap depends on partner agents you cannot verify",
        "trust that travels with the partner's agent",
    ),
    (
        "Compliance / Audit",
        "no provenance or attribution to show a regulator",
        "provenance from agent #1, not retrofitted at audit",
    ),
)

#: Which persona-axis seats the automated resolver can actually detect today.
#: Widened 2026-08-20 (hook-coverage H3) from three buckets to six, sized to where
#: recipients actually are: the old "exec" bucket held 156 of 397 live rows — CEOs, CPOs,
#: data leaders and 28 CTOs that a bare "president" cue had swept in. `finops` and
#: `partnership` resolve as personas but have no seat and so are absent here: 0 and 1
#: recipients respectively across the whole pool.
SEAT_COVERAGE = {
    "security": "CISO / Risk / Data & Compliance",
    "cto": "CTO / Head of Platform",
    "ceo": "CEO / Founder",
    "architect": "Enterprise / Cloud Architect · CIO",
    "ai-platform": "Chief AI Officer / Head of AI Platform",
    "product": "CPO / Head of Product",
}

#: Plain-English gloss for each pipeline bucket. The bucket names are internal; a reader
#: asked "what is awaiting verification — a qualified account, research, or compliance?"
#: and could not tell from the label, which is the whole problem.
FUNNEL_GLOSS = (
    (
        "ready",
        "Good to email now",
        "The address is verified deliverable, the person is inside a market we are allowed to "
        "email, they are not on the do-not-contact list, they have not been emailed before, and "
        "their row renders cleanly into the copy. Note what this does NOT mean: it is not a "
        "statement that the account was scored against the ICP, that buying-intent signals were "
        "found, that a dossier exists, or that an email has been drafted for them. Those happen "
        "later and are tracked per campaign.",
    ),
    (
        "needs_verification",
        "Address not confirmed",
        "We have the right person at the right company, but nobody has proven the inbox exists. "
        "A deliverability gap, not a research or qualification gap.",
    ),
    (
        "blocked",
        "Bad address",
        "The address failed verification outright. Emailing it would bounce and damage the "
        "reputation of the sending domain.",
    ),
    (
        "excluded_dnc_or_sent",
        "Off limits",
        "Everything we are holding but cannot mail. Three different reasons live here: already "
        "contacted, asked us not to write again, and — the largest group — outside the "
        "jurisdictions this profile is allowed to email at all.",
    ),
)


#: Published cold-email reply-rate reference points, researched 2026-08-19.
#:
#: Three warnings travel with these numbers and are rendered on the page, because a
#: benchmark quoted without them is worse than none.
#:
#: 1. **The denominator is not standardised.** Belkins divides replies by *emails sent*;
#:    most others divide by *people contacted*. A three-touch sequence makes those differ
#:    by roughly 3x, which is most of the gap between 0.45% and 3.4% — not a real
#:    difference in performance.
#: 2. **Every source is a cold-email vendor** reporting on its own platform's traffic.
#:    Self-selected populations, and an interest in the answer.
#: 3. **The widely-cited 8.5% (Backlinko/Pitchbox, 12M emails) is link-building and
#:    blogger outreach, not B2B sales** — a different population answering a different
#:    request. It is excluded here rather than shown, since quoting it as a sales
#:    benchmark is a category error.
#:
#: The retired "5-18%" band traced to Woodpecker's "up to 18% for advanced personalization"
#: — a single vendor's best case presented as a range ceiling. It was never a band.
BENCHMARKS = (
    {
        "label": "SaaS selling to enterprise",
        "low": 0.018,
        "high": 0.018,
        "basis": "per person contacted",
        "source": "Cleanlist meta-analysis, Feb 2026",
        "url": "https://www.cleanlist.ai/blog/2026-02-18-cold-email-response-rate-statistics",
        "note": "the closest published comparator to this campaign's own audience",
    },
    {
        "label": "SaaS selling to SaaS",
        "low": 0.024,
        "high": 0.024,
        "basis": "per person contacted",
        "source": "Cleanlist meta-analysis, Feb 2026",
        "url": "https://www.cleanlist.ai/blog/2026-02-18-cold-email-response-rate-statistics",
        "note": "",
    },
    {
        "label": "all industries, average",
        "low": 0.031,
        "high": 0.0343,
        "basis": "mixed / per email sent",
        "source": "Woodpecker 20M+ emails (Jun 2026) and Cleanlist (Feb 2026)",
        "url": "https://woodpecker.co/blog/cold-email-statistics/",
        "note": "Woodpecker reports 3.43%, down from 5.1% in 2024",
    },
    {
        "label": "all industries, per email sent",
        "low": 0.0045,
        "high": 0.0051,
        "basis": "per email sent",
        "source": "Belkins, 7.5M emails sent in 2025 (updated Jun 2026)",
        "url": "https://belkins.io/blog/cold-email-response-rates",
        "note": "0.45% global, 0.51% US — the strictest denominator of any source found",
    },
    {
        "label": "considered good / top decile",
        "low": 0.05,
        "high": 0.10,
        "basis": "per person contacted",
        "source": "Woodpecker (Jun 2026)",
        "url": "https://woodpecker.co/blog/cold-email-statistics/",
        "note": "10%+ described as excellent; 18% is one vendor's personalisation best case",
    },
)

#: The comparator the target is judged against — same ICP shape as this campaign.
PRIMARY_BENCHMARK = BENCHMARKS[0]


def dashboard_path(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root).parent / PAGE_NAME


# --- model ----------------------------------------------------------------------


def reconcile_snapshot(campaigns_model: dict, status_model: dict) -> dict:
    """Do the live-stats snapshot and the ledger agree on which sequences exist?

    Nothing ever checked, so a stale snapshot rendered as fact. This does not fix
    staleness; it makes it impossible to miss.
    """
    snapshot_ids = {s["id"] for s in status_model.get("sequences", []) if s.get("id")}
    ledger_ids: set[str] = set()
    for c in campaigns_model.get("campaigns", []):
        ledger_ids |= {s["sequence_id"] for s in c.get("sequences", [])}
        ledger_ids |= {s["sequence_id"] for s in c.get("archived", [])}
    ledger_ids |= {s["sequence_id"] for s in campaigns_model.get("unlinked_sequences", [])}
    return {
        "ok": snapshot_ids == ledger_ids,
        "in_snapshot_only": sorted(snapshot_ids - ledger_ids),
        "in_ledger_only": sorted(ledger_ids - snapshot_ids),
    }


def market_split(profile: str, content_root: Path | None = None) -> dict:
    """How much of the held pool is out of market, and which markets are allowed.

    ``consolidate`` counts this and prints it, but never persisted it, so the page could
    only describe the "off limits" bucket as "already contacted or opted out" — which is
    the smaller half of it. Recomputed here from the same master list and the same gate,
    so the page and the pipeline cannot disagree.
    """
    from gtm_core.prospects_consolidate import (
        _load_master,
        _resolve_market_gate,
    )
    from gtm_core.prospects_consolidate import (
        _pool_dir as _pd,
    )

    out = {"markets": [], "out_of_market": 0, "unknown_country": 0, "gate_on": False}
    try:
        gate = _resolve_market_gate(profile, False)
    except Exception:  # noqa: BLE001 - an unreadable profile leaves the gate off, loudly
        return out
    out["markets"] = list(getattr(gate, "markets", []) or [])
    out["gate_on"] = bool(out["markets"])
    master = _pd(profile, content_root) / "master-list.csv"
    if not master.is_file() or not out["gate_on"]:
        return out
    for row in _load_master(master):
        country = (row.get("country") or "").strip()
        if not country:
            out["unknown_country"] += 1
        elif gate.blocks(country):
            out["out_of_market"] += 1
    return out


def prospecting_runs(profile: str, content_root: Path | None = None, limit: int = 8) -> list[dict]:
    """Recent discovery/enrichment runs from the ledger, newest first.

    The question "is prospecting still running?" has no home on any page today, so a
    pipeline that quietly stopped looks identical to one that is working.
    """
    from gtm_core.prospects_import import _ledgers

    runs = []
    for ev in _ledgers(profile, content_root).iter_history():
        e = ev.get("event")
        if e not in ("prospect_run", "prospect_enrich"):
            continue
        runs.append(
            {
                "kind": "discovery" if e == "prospect_run" else "enrichment",
                "run_id": ev.get("run_id", ""),
                "market": ev.get("market", ""),
                "found": ev.get("total", ev.get("contacts", 0)),
                "tier_a": ev.get("tier_a", ""),
                "ts": (ev.get("ts") or "")[:10],
            }
        )
    runs.sort(key=lambda r: r["ts"], reverse=True)
    return runs[:limit]


def _lint_records(profile: str, content_root: Path | None) -> dict[str, dict]:
    """Copy-QA records, each tagged with whether it still describes what is staged.

    A PASS is a statement about **one body and one list**. Edit either after the sequence
    was staged and the record still says PASS while the sequencer holds something else —
    which is the most dangerous possible reading of this page, because "checked and
    loaded" is exactly what a reader acts on when they press send.

    Found 2026-08-19: the four Run-500 sequences were re-linted clean after a copy
    rewrite and a 42-row suppression, and nothing on the page distinguished that from the
    live sequences, which still held the pre-rewrite bodies and the pre-suppression list.
    The record now carries a fingerprint of the spec and CSV it read; this recomputes them.
    """
    out: dict[str, dict] = {}
    for path in sorted(_pool_dir(profile, content_root).glob("lint-*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not rec.get("sequence_id"):
            continue
        drift = []
        for label, key, digest_key in (
            ("copy", "spec", "spec_sha256"),
            ("recipient list", "csv", "csv_sha256"),
        ):
            recorded = rec.get(digest_key)
            src = Path(rec.get(key, ""))
            if not recorded:
                # Pre-fingerprint record: cannot prove freshness either way.
                drift.append(f"{label} (unverifiable — record predates fingerprinting)")
                continue
            if not src.is_file():
                drift.append(f"{label} (source file is gone)")
            elif hashlib.sha256(src.read_bytes()).hexdigest()[:16] != recorded:
                drift.append(f"{label} changed since the check ran")
        rec["drift"] = drift
        out[rec["sequence_id"]] = rec
    return out


def _spec_copy(spec_path: Path) -> list[dict]:
    """Subject and the argument line for each email in one spec."""
    if not spec_path.is_file():
        return []
    text = spec_path.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(
        r"\*\*Step (\d+) — Day (\d+)\*\*(?: · Subject: `([^`]+)`|[^\n]*)\n\n((?:>.*\n)+)",
        text,
    ):
        body = [
            ln[2:].strip() if ln.startswith("> ") else ln[1:].strip()
            for ln in m.group(4).splitlines()
        ]
        body = [ln for ln in body if ln]
        opener = next((ln for ln in body[1:] if ln != "{{Why Now}}."), "")
        out.append(
            {
                "step": int(m.group(1)),
                "day": int(m.group(2)),
                "subject": m.group(3) or "",
                "opener": opener,
                "words": sum(len(ln.split()) for ln in body),
            }
        )
    return out


def build_model(profile: str, content_root: Path | None = None) -> dict:
    status = build_status(profile, content_root)
    campaigns = build_campaigns(profile, content_root)
    rows = read_outcomes(content_root or resolve_content_root(), profile)
    baseline = 0.059
    for c in campaigns["campaigns"]:
        r = _rate_of(c.get("targets") or {})
        if r:
            baseline = r
            break
    cellmodel = build_cells(profile, content_root, outcome_rows=rows, baseline=baseline)
    supply = supply_profile(profile, content_root)
    intent = intent_profile(profile, content_root)

    seq_dir = _prospects_dir(profile, content_root) / "sequences"
    lint = _lint_records(profile, content_root)
    staged_ts = {
        sq["sequence_id"]: sq.get("staged_ts", "")
        for c in campaigns["campaigns"]
        for sq in c.get("sequences", [])
    }
    for sid, rec in lint.items():
        # The check ran after the sequence was last pushed, so the sending tool is holding
        # the older copy and the older list. This is the drift that matters: the other
        # check compares the record to the files, this one compares it to what would send.
        ran, pushed = rec.get("ran_at", ""), staged_ts.get(sid, "")
        if ran and pushed and ran > pushed:
            rec.setdefault("drift", []).insert(
                0,
                f"copy and list were re-checked on {ran[:10]}, after this sequence was "
                f"last pushed to the sending tool on {pushed[:10]}",
            )
    messages = [
        {
            **src,
            "copy": _spec_copy(seq_dir / src["spec"]),
            "lint": lint.get(src["sequence_id"], {}),
            "audience": [c for c in cellmodel["cells"] if c["sequence_id"] == src["sequence_id"]],
        }
        for src in cellmodel["sources"]
    ]

    return {
        "profile": profile,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "status": status,
        "campaigns": campaigns,
        "cells": cellmodel,
        "supply": supply,
        "intent": intent,
        "messages": messages,
        "outcome_rows": len(rows),
        "market": market_split(profile, content_root),
        "runs": prospecting_runs(profile, content_root),
        "reconciliation": reconcile_snapshot(campaigns, status),
    }


# --- render helpers -------------------------------------------------------------


def _e(x) -> str:
    return html.escape(str(x))


def _rate_of(targets: dict) -> float:
    """The campaign's reply-rate goal. Prefers the explicitly declared ``reply_rate`` and
    falls back to replies/prospects — deriving it from two rounded integers reports 3.1%
    for a goal set at 3.0%, which is the kind of small lie a reader has no way to catch."""
    try:
        rate = float(targets.get("reply_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate:
        return rate
    try:
        return float(targets["replies"]) / float(targets["prospects"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _i(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _seat_label(seat: str) -> str:
    """Display name for a seat. ``unknown`` stays the internal key — it is written into
    the cell id and onto outcome tags, so renaming the value would orphan any row already
    tagged with it. Only the label a reader sees changes."""
    return "other" if seat == "unknown" else seat


def _pct(n: float, d: float) -> str:
    return f"{round(100 * n / d)}%" if d else "—"


def _stat(value, label: str, sub: str = "") -> str:
    v = f"{value:,}" if isinstance(value, int) else _e(value)
    sub_html = f'<div class="stat-sub">{_e(sub)}</div>' if sub else ""
    return (
        f'<div class="stat"><div class="stat-value">{v}</div>'
        f'<div class="stat-label">{_e(label)}</div>{sub_html}</div>'
    )


def _barlist(items: list[tuple[str, int]], total: int, *, tone: str = "a") -> str:
    """A labelled proportional bar per row — the cheapest honest chart."""
    if not total:
        return "<p class='note'>Nothing to show yet.</p>"
    out = []
    top = max((n for _, n in items), default=1) or 1
    for label, n in items:
        out.append(
            f'<div class="brow"><div class="blabel">{_e(label)}</div>'
            f'<div class="btrack"><div class="bfill t{tone}" style="width:{100 * n / top:.1f}%">'
            f"</div></div>"
            f'<div class="bval">{n:,}<span class="muted"> · {_pct(n, total)}</span></div></div>'
        )
    return f'<div class="bars">{"".join(out)}</div>'


# --- views ----------------------------------------------------------------------


def _forecast_block(m: dict) -> str:
    """How long the run takes once it starts, and what sets the ceiling.

    Every duration here is arithmetic over a number printed beside it. A schedule quoted
    without its divisor is exactly the figure that outlives the list it was computed from —
    "about five weeks" was true of 1,359 emails and is still sitting in the campaign file
    now that the checked list is smaller.
    """
    window: dict = {}
    tgt: dict = {}
    for c in m["campaigns"]["campaigns"]:
        window = window or (c.get("window") or {})
        tgt = tgt or (c.get("targets") or {})
    cap = _i(window.get("daily_cap"))
    boxes = _i(window.get("mailboxes"))
    touches = _i(window.get("touches")) or max(
        (_i((msg.get("lint") or {}).get("touches")) for msg in m["messages"]), default=0
    )
    if not (cap and touches):
        return ""

    # The sequence's own span: the last person enrolled still has to live through it after
    # the final new enrolment, so it is added to the sending days rather than hidden in them.
    days_seen = [c["day"] for msg in m["messages"] for c in msg["copy"]]
    span = (max(days_seen) - min(days_seen)) if days_seen else 0
    tail = -(-span * 5 // 7)

    reviewed = sum(_i((msg.get("lint") or {}).get("rows")) for msg in m["messages"])
    bases: list[tuple[str, int]] = []
    if reviewed:
        bases.append(("the list that was checked, once it is loaded", reviewed))
    if _i(tgt.get("prospects")) and _i(tgt.get("prospects")) != reviewed:
        bases.append(("the campaign's own qualified-and-sendable target", _i(tgt["prospects"])))

    start = date.today()
    while start.weekday() > 4:
        start += timedelta(days=1)

    rows = ""
    for label, people in bases:
        emails = people * touches
        send_days = -(-emails // cap)
        total = send_days + tail
        finish, added = start, 0
        while added < total:
            finish += timedelta(days=1)
            if finish.weekday() < 5:
                added += 1
        rows += (
            f"<tr><td>{_e(label)}</td><td class='num-cell'>{people:,}</td>"
            f"<td class='num-cell'>{emails:,}</td>"
            f"<td class='num-cell'>{send_days} + {tail}</td>"
            f"<td class='num-cell'>{total} working days</td>"
            f"<td class='muted'>about {total / 5:.0f} weeks · {finish:%-d %b}</td></tr>"
        )

    per_box = (cap // boxes) if boxes else 0
    people_day = cap // touches
    month = people_day * 21
    why = (
        f"<strong>{cap} emails a day is the ceiling, and it is {boxes} mailboxes at "
        f"{per_box} a day each.</strong> "
        if boxes and per_box
        else f"<strong>{cap} emails a day is the ceiling.</strong> "
    )
    cap_note = window.get("capacity_note") or ""
    caveat = window.get("caveat") or ""

    return f"""
      <div class="card">
        <h2>How long it runs, once it starts</h2>
        <table><thead><tr><th>Counted on</th><th>People</th><th>Emails</th>
        <th>Sending + tail</th><th>Total</th><th>Done by</th></tr></thead>
        <tbody>{rows}</tbody></table>
        <p class="note">Each person receives {touches} emails over {span} days, so the run is
        not finished when the last email is <em>started</em> — the last person enrolled still
        has their own {span}-day sequence to live through. That is the "+ {tail}" column.
        Dates assume it starts on the next working day, which it cannot yet.
        {_e(caveat) and "<strong>Caveat:</strong> " + _e(caveat) + "."}</p>
      </div>

      <div class="card">
        <h2>The ceiling, and why it is where it is</h2>
        <p>{why}The list is not the constraint — the mailboxes are. At {touches} emails a
        person that ceiling absorbs about <strong>{people_day} new people a working day</strong>,
        or <strong>{month:,} a month</strong>. Going faster means more mailboxes, not a setting:
        the per-mailbox rate is the number deliberately held down, because volume out of a
        single mailbox is what costs a sending domain its reputation.</p>
        {f'<p class="note">{_e(cap_note)}.</p>' if cap_note else ""}
      </div>"""


def _status_view(m: dict) -> str:
    seqs = [x for x in m["status"].get("sequences", []) if x.get("id")]
    live_ids = set()
    window: dict = {}
    planned = 0
    for c in m["campaigns"]["campaigns"]:
        live_ids |= {x["sequence_id"] for x in c.get("sequences", [])}
        window = window or (c.get("window") or {})
        planned += int(c["targets"].get("emails", 0) or 0)
    current = [x for x in seqs if x["id"] in live_ids]

    sent = sum(_i(x.get("sent")) for x in current)
    loaded = sum(_i(x.get("loaded")) for x in current)
    replied = sum(_i(x.get("replied")) for x in current)
    cap = _i(window.get("daily_cap")) or 0

    seq_rows = "".join(
        f"<tr><td>{_e(x.get('name', '') or x['id'])}</td>"
        f"<td><span class='pill {'good' if str(x.get('status', '')).lower() in ('active', 'running') else 'warn'}'>"
        f"{_e(x.get('status') or '—')}</span></td>"
        f"<td class='num-cell'>{_i(x.get('loaded')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('sent')):,}</td>"
        f"<td class='num-cell'>{_i(x.get('replied')):,}</td>"
        f"<td class='num-cell muted'>{_pct(_i(x.get('sent')), _i(x.get('loaded')) * 3)}</td></tr>"
        for x in sorted(current, key=lambda y: -_i(y.get("loaded")))
    )

    # Derived from the campaign's own goals, never hardcoded here: a second copy of this
    # number is a second thing to forget when the target moves.
    tgt = {}
    for c in m["campaigns"]["campaigns"]:
        tgt = c.get("targets") or {}
        if tgt.get("replies") and tgt.get("prospects"):
            break
    target_rate = _rate_of(tgt)
    rate_txt = f"{replied / sent:.1%}" if sent else "—"
    rate_sub = (
        f"target {target_rate:.1%} · nothing sent yet" if not sent else f"target {target_rate:.1%}"
    )
    prim = PRIMARY_BENCHMARK
    ratio = (target_rate / prim["high"]) if prim["high"] else 0
    verdict = (
        f"about {ratio:.1f}x the closest published comparator"
        if ratio >= 1.15
        else "roughly in line with the closest published comparator"
        if ratio >= 0.85
        else f"about {ratio:.0%} of the closest published comparator"
    )
    stance = (
        "That is a stretch target: beating it means outperforming the published comparator."
        if ratio >= 1.15
        else "That is neither a stretch nor a floor — it is a bet that we perform like the "
        "published comparator, so missing it is a real signal rather than a rounding error."
        if ratio >= 0.85
        else "That is a deliberate floor, set below the comparator so that clearing it proves "
        "very little and missing it is unambiguous."
    )
    scale = 0.11  # bar full-width; the top band shown is 10%

    def _brow(label: str, low: float, high: float, tone: str, right: str) -> str:
        return (
            f'<div class="brow"><div class="blabel">{_e(label)}</div>'
            f'<div class="btrack"><div class="bfill {tone}" '
            f'style="width:{min(100, high * 100 / scale):.0f}%"></div></div>'
            f'<div class="bval">{right}</div></div>'
        )

    rows = _brow(
        "this campaign, so far",
        0,
        (replied / sent if sent else 0),
        "ta",
        rate_txt,
    ) + _brow("our target", 0, target_rate, "tb", f"{target_rate:.1%}")
    for bm in BENCHMARKS:
        val = (
            f"{bm['low']:.2%}".rstrip("0").rstrip(".")
            if bm["low"] == bm["high"]
            else f"{bm['low']:.1%}–{bm['high']:.1%}"
        )
        rows += _brow(bm["label"], bm["low"], bm["high"], "tc", val)

    src_rows = "".join(
        f"<tr><td>{_e(bm['label'])}</td><td class='muted'>{_e(bm['basis'])}</td>"
        f"<td><a href='{_e(bm['url'])}'>{_e(bm['source'])}</a>"
        + (f"<br><span class='muted'>{_e(bm['note'])}</span>" if bm["note"] else "")
        + "</td></tr>"
        for bm in BENCHMARKS
    )

    bench_html = f"""
      <div class="card">
        <h2>Reply rate</h2>
        <div class="bars">{rows}</div>
        <p class="note">Our {target_rate:.1%} target is <strong>{verdict}</strong> —
        {prim["low"]:.1%} for {_e(prim["label"])}, the published figure closest to who this
        campaign actually writes to. {stance}</p>
        <details>
          <summary>Where these numbers come from, and why to distrust them</summary>
          <p class="note"><strong>The denominator is not standardised.</strong> Belkins divides
          replies by <em>emails sent</em>; most others divide by <em>people contacted</em>. At
          three emails per person that is a ~3x difference, which accounts for most of the gap
          between 0.45% and 3.4% — methodology, not performance. Our own target is stated per
          person contacted.</p>
          <p class="note"><strong>Every source below is a cold-email vendor</strong> reporting on
          its own platform's traffic: self-selected populations, and an interest in the answer. No
          independent academic or analyst dataset was found.</p>
          <p class="note"><strong>One number is deliberately excluded.</strong> The widely-quoted
          8.5% (Backlinko/Pitchbox, 12M emails) is link-building and blogger outreach, not B2B
          sales — a different population answering a different request. Quoting it as a sales
          benchmark is a category error, and inflated ranges usually trace back to it.</p>
          <table><thead><tr><th>Figure</th><th>Counted as</th><th>Source</th></tr></thead>
          <tbody>{src_rows}</tbody></table>
        </details>
      </div>"""

    blocker = window.get("capacity_blocker") or ""
    # The blocker itself is sending-tool mechanics, so it lives in the Operator notes panel;
    # what stays here is only the pointer, so a reader of this panel is never left wondering
    # why four ready sequences are sending nothing.
    blocker_html = (
        "<p class='note'>Why it has not started yet is in <strong>Operator notes</strong>.</p>"
        if blocker
        else ""
    )

    runs = m["runs"]
    if runs:
        last = runs[0]["ts"]
        try:
            age = (datetime.now(UTC).date() - datetime.strptime(last, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        age_txt = f"{age} days ago" if age is not None else last
        tone = "bad" if (age or 0) > 14 else "warn" if (age or 0) > 7 else "good"
        run_rows = "".join(
            f"<tr><td>{_e(r['ts'])}</td><td>{_e(r['kind'])}</td>"
            f"<td>{_e(r['market'] or '—')}</td>"
            f"<td class='num-cell'>{_i(r['found']):,}</td></tr>"
            for r in runs
        )
        runs_html = f"""
        <p><span class="pill {tone}">last run {_e(age_txt)}</span>
        Finding new people is a separate activity from emailing them. Nothing here is
        running automatically — each run is started by hand.</p>
        <table><thead><tr><th>Date</th><th>Kind</th><th>Market</th><th>People found</th>
        </tr></thead><tbody>{run_rows}</tbody></table>"""
    else:
        runs_html = "<p class='note'>No discovery or enrichment runs recorded.</p>"

    return f"""
      <div class="stats">
        {_stat(f"{sent:,} of {planned:,}", "emails sent", "nothing goes out until a person starts it")}
        {_stat(rate_txt, "reply rate", rate_sub)}
        {_stat(loaded, "people loaded and waiting")}
        {_stat(replied, "replies so far")}
        {_stat(len(current), "sequences set up", f"{len([x for x in current if str(x.get('status', '')).lower() in ('active', 'running')])} currently sending")}
        {_stat(f"{cap:,}/day", "sending ceiling", f"{_i(window.get('mailboxes'))} mailboxes at {cap // _i(window.get('mailboxes'))} a day each" if _i(window.get("mailboxes")) else "the daily cap")}
      </div>

      <div class="card">
        <h2>Email sequences</h2>
        <table><thead><tr><th>Sequence</th><th>State</th><th>People</th><th>Emails sent</th>
        <th>Replies</th><th>Progress</th></tr></thead><tbody>{seq_rows}</tbody></table>
        <p class="note">Progress is emails sent against the three each person is due.
        How long the whole run takes is worked out below.</p>
        {blocker_html}
      </div>

      {_forecast_block(m)}

      {bench_html}

      <div class="card">
        <h2>Finding new people</h2>
        {runs_html}
      </div>"""


def _who_view(m: dict) -> str:
    f = m["status"]["funnel"]
    sup = m["supply"]

    mk = m["market"]
    gloss_rows = ""
    for key, title, desc in FUNNEL_GLOSS:
        extra = ""
        if key == "excluded_dnc_or_sent" and mk["gate_on"]:
            allowed = ", ".join(mk["markets"])
            extra = (
                f" <strong>{mk['out_of_market']:,} of them are out of market</strong> — this "
                f"profile may only email {_e(allowed)}. A further {mk['unknown_country']:,} rows "
                "carry no country at all; the gate only blocks a country it can read, so those "
                "are still treated as mailable."
            )
        gloss_rows += (
            f"<tr><td><strong>{_e(title)}</strong></td>"
            f"<td class='num-cell'>{f.get(key, 0):,}</td>"
            f"<td class='muted'>{_e(desc)}{extra}</td></tr>"
        )

    reasons = sup.get("suppression_reasons") or []
    supp_top = (
        ", ".join(f"{r['n']:,} {_e(r['reason'])}" for r in reasons[:3])
        if reasons
        else "no reasons recorded"
    )
    supp_rows = (
        "<table><thead><tr><th>Why held back</th><th>People</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_e(r['reason'])}</td><td class='num-cell'>{r['n']:,}</td></tr>"
            for r in reasons
        )
        + "</tbody></table>"
        if reasons
        else ""
    )

    unplaced = sup["unplaced_total"]
    unplaced_rows = "".join(
        f"<tr><td>{_e(t['name'])}</td><td class='num-cell'>{t['n']:,}</td></tr>"
        for t in sup["unplaced_titles"][:12]
    )
    covered = ", ".join(f"{v} (<code>{k}</code>)" for k, v in SEAT_COVERAGE.items())

    return f"""
      <div class="stats">
        {
        _stat(
            sup["qualified_sendable"],
            "people we will actually email",
            f"of {sup['total']:,} loaded — goals are set on this number",
        )
    }
        {_stat(f["ready"], "more we could email", "verified address, not yet loaded")}
        {_stat(f["needs_verification"], "address not confirmed", "person is right, inbox unproven")}
        {_stat(f["excluded_dnc_or_sent"], "off limits", "already contacted or opted out")}
      </div>

      <div class="card">
        <h2>How many actually count</h2>
        <p class="note">Two filters stand between "loaded" and "will receive an email", and the
        goals are set after both. <strong>{sup["suppressed"]:,} of {sup["total"]:,} are held
        back</strong> because the one researched sentence their email opens on does not survive
        inspection — {supp_top}. Separately, <strong>{sup["unclear"]:,} rows are
        "unread"</strong>: nobody has checked whether that job title could own this, and the
        qualification gate treats unread as not-proven rather than a soft pass.
        {sup["not_qualified"]:,} are ruled out outright. What is left —
        <strong>{sup["qualified_sendable"]:,} people</strong> — is what the goals are stated
        on.</p>
        {supp_rows}
        {
        _barlist(
            [
                ("will be emailed", sup["qualified_sendable"]),
                ("held back — weak opening line", sup["suppressed"]),
                ("job title unread", sup["unclear"]),
                ("ruled out", sup["not_qualified"]),
            ],
            sup["total"],
        )
    }
      </div>

      <div class="card">
        <h2>What each group means</h2>
        <p class="note">These are the four states a person can be in. The only one we can
        email today is the first.</p>
        <table><tbody>{gloss_rows}</tbody></table>
      </div>

      <div class="card">
        <h2>Where they are</h2>
        {_barlist([(c["name"], c["n"]) for c in sup["countries"]], sup["total"])}
        <p class="note">Three markets. The campaign is overwhelmingly a US motion — the two
        smaller markets are too small to read a result from on their own.</p>
      </div>

      <div class="card">
        <h2>What job they do</h2>
        {_barlist([(_seat_label(s["name"]), s["n"]) for s in sup["seats"]], sup["total"], tone="b")}
        <h3>Why {unplaced:,} say "other"</h3>
        <p class="note"><strong>This is a gap in our own classifier, not missing data.</strong>
        Every one of these people has a job title on file. The tenant's voice guide defines
        <strong>seven</strong> buyer seats, but the automated resolver only recognises
        <strong>three</strong> — {covered} — so anything outside those three is grouped as "other".
        The largest group below is Chief Information Officer, which is plainly a technology
        seat and simply is not in the resolver's word list.</p>
        <p class="note">This matters beyond tidiness: the check that stops us leading on the
        wrong seat's problem stays <em>silent</em> on an unrecognised title, by design. So
        these {unplaced:,} people received seat-targeted copy that nothing verified was aimed
        at them.</p>
        <table><thead><tr><th>Title we could not place</th><th>People</th></tr></thead>
        <tbody>{unplaced_rows}</tbody></table>
        <p class="note">{sup["unplaced_distinct"]} distinct titles in total.</p>
      </div>

      {_intent_block(m)}"""


def _intent_block(m: dict) -> str:
    """ICP score and buying-intent evidence, with coverage stated first.

    Coverage leads because it governs everything below it: a score distribution drawn
    from half the list is not a description of the list.
    """
    it = m["intent"]
    total, matched = it["total"], it["matched"]
    cov = _pct(matched, total)

    feed_rows = "".join(
        f"<tr><td>{_e(f['label'])}</td>"
        f"<td class='muted'>{_e(f['level'])}-level · {_e(f['means'])}</td>"
        + (
            f"<td class='num-cell'>{f['n']:,}</td><td><span class='pill good'>present</span></td>"
            if f["present"]
            else "<td class='num-cell muted'>0</td>"
            "<td><span class='pill warn'>not on this list</span></td>"
        )
        + "</tr>"
        for f in it["feeds"]
    )
    topic_rows = "".join(
        f"<tr><td>{_e(t['topic'])}</td><td class='num-cell'>{t['n']:,}</td>"
        f"<td class='num-cell muted'>{t['avg_score'] if t['avg_score'] is not None else '—'}</td></tr>"
        for t in it["topics"]
    )
    path_rows = "".join(
        f"<tr><td>{_e(p['name'])}</td><td class='num-cell'>{p['n']:,}</td>"
        f"<td class='num-cell muted'>{_pct(p['n'], matched)}</td></tr>"
        for p in it["paths"]
    )
    nir = it["new_in_role"]

    score_line = (
        f"Scores run {it['score_min']:.0f} to {it['score_max']:.0f} "
        f"(average {it['score_avg']}) across the {it['score_n']:,} we can trace."
        if it["score_n"]
        else "No ICP score could be recovered for anyone on this list."
    )

    return f"""
      <div class="card">
        <h2>How well they fit the ideal customer</h2>
        <p class="note"><strong>We can only answer this for {matched:,} of {total:,} people
        ({cov}).</strong> The other {it["unmatched"]:,} were loaded into the sending list without a
        traceable link back to the scored prospect pool, so they carry no known score and no known
        buying-intent signal at all. That is not a low score — it is no score, and it is the single
        biggest gap in what we know about who we are writing to.</p>
        <p class="note">{score_line} The scores are attached to the <em>company</em>, not the
        person, and the link back is made on company domain, so read every number below as a
        statement about the employer.</p>
        {_barlist([("traceable to a scored record", matched), ("no score on file", it["unmatched"])], total)}
      </div>

      <div class="card">
        <h2>What buying-intent signals we actually hold</h2>
        <p class="note">The full roster of signals this pipeline can source, and which of them
        reached this list. Showing the absent ones matters: otherwise there is no way to tell
        "we have no hiring signal here" from "hiring signal is not a thing we collect".</p>
        <table><thead><tr><th>Signal</th><th>What it means</th><th>People</th><th></th></tr>
        </thead><tbody>{feed_rows}</tbody></table>
        <p class="note"><strong>Only one feed is actually present.</strong> Everything we know
        about intent on this list is topic surge — a third party observing that people at the
        company are reading unusually much about a subject. No hiring signal, no news signal, and
        no job-change timing reached these rows.</p>
        <p class="note"><strong>"New in role" is empty.</strong> {nir["true"]:,} people are marked
        as recently changed job, {nir["false"]:,} are marked as not, {nir["unknown"]:,} are
        unrecorded. A marker that is never true cannot be tested, and it is one of the questions
        this campaign originally set out to answer.</p>
      </div>

      <div class="card">
        <h2>Which topics they are researching</h2>
        <p class="note">Topic surge across the traceable group. The score is the third party's own
        intensity reading, not ours.</p>
        <table><thead><tr><th>Topic</th><th>Companies</th><th>Avg intensity</th></tr></thead>
        <tbody>{topic_rows or "<tr><td colspan=3 class='muted'>none recorded</td></tr>"}</tbody>
        </table>
        <h3>How each one qualified</h3>
        <table><thead><tr><th>Route</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{path_rows}</tbody></table>
        <p class="note">Worth reading closely: the dominant route is a <em>relaxed</em> one. Very
        few cleared the full qualification gate, which means most of this list is here on topic
        surge alone rather than on surge plus a firmographic fit check.</p>
      </div>"""


def _what_view(m: dict) -> str:
    sup = m["supply"]

    axis_rows = "".join(
        f"<tr><td><strong>{_e(seat)}</strong>"
        + (
            ' <span class="pill good">detected</span>'
            if seat in SEAT_COVERAGE.values()
            else ' <span class="pill warn">not detected</span>'
        )
        + f"</td><td>{_e(pain)}</td><td class='muted'>{_e(gain)}</td></tr>"
        for seat, pain, gain in PERSONA_AXIS
    )

    subjects: dict[str, int] = {}
    subj_variants: dict[str, set[str]] = {}
    slots = 0
    threaded = 0
    for msg in m["messages"]:
        n = sum(c["enrolled"] for c in msg["audience"])
        for touch in msg["copy"]:
            if not touch["subject"]:
                threaded += 1
                continue
            slots += 1
            subjects[touch["subject"]] = subjects.get(touch["subject"], 0) + n
            subj_variants.setdefault(touch["subject"], set()).add(
                msg.get("title") or msg["sequence_id"]
            )
    shared = {k: v for k, v in subj_variants.items() if len(v) > 1}
    subj_rows = "".join(
        f"<tr><td><code>{_e(k)}</code></td><td class='num-cell'>{n:,}</td>"
        f"<td class='muted'>{'shared by every group' if k in shared else 'one group only'}</td></tr>"
        for k, n in sorted(subjects.items(), key=lambda kv: -kv[1])
    )
    shared_note = ""
    if shared:
        names = ", ".join(f"<code>{_e(x)}</code>" for x in sorted(shared))
        shared_note = (
            f"<p class='note'><strong>{names} is the subject of the final email for every "
            f"group.</strong> It is the only subject not tailored to the reader's job, so the "
            f"last thing all {max(subjects.values()):,} people see is identical and arrives in "
            "the same window. That is the highest-risk line for spam filtering, and the single "
            "easiest thing to vary.</p>"
        )

    sig_rows = "".join(
        f"<tr><td>{_e(s['kind'])}</td>"
        f"<td><span class='pill {'good' if s['shape'] == 'event' else 'warn'}'>"
        f"{_e(s['shape'])}</span></td>"
        f"<td class='num-cell'>{s['n']:,}</td>"
        f"<td class='num-cell muted'>{_pct(s['n'], sup['total'])}</td></tr>"
        for s in sup["signals"]
    )

    seq_blocks = []
    for msg in m["messages"]:
        n = sum(c["enrolled"] for c in msg["audience"])
        touches = "".join(
            f"<tr><td>Email {c['step']}<br><span class='muted'>day {c['day']}</span></td>"
            + (
                f"<td><code>{_e(c['subject'])}</code></td>"
                if c["subject"]
                else "<td class='muted'>reply inside the first email's thread — no new subject</td>"
            )
            + f"<td class='muted'>{_e(c['opener'][:200])}</td>"
            + f"<td class='num-cell muted'>{c['words']}w</td></tr>"
            for c in msg["copy"]
        )
        lint = msg["lint"]
        if lint:
            fired = lint.get("by_rule", {})
            catalogue = lint.get("checks_run", {})
            by_cat: dict[str, list[str]] = {}
            for rule, meta in sorted(catalogue.items()):
                by_cat.setdefault(meta.get("category", "other"), []).append(rule)
            cat_rows = "".join(
                f"<tr><td>{_e(cat)}</td><td class='num-cell'>{len(rules)}</td>"
                f"<td class='muted'>{_e(', '.join(r for r in rules if r in fired)) or '— all clear'}</td></tr>"
                for cat, rules in sorted(by_cat.items())
            )
            finding_rows = "".join(
                f"<tr><td>{_e(rule)}</td>"
                f"<td class='muted'>{_e(catalogue.get(rule, {}).get('protects', ''))}</td>"
                f"<td class='num-cell'>{levels.get('ERROR', 0):,}</td>"
                f"<td class='num-cell'>{levels.get('WARN', 0):,}</td></tr>"
                for rule, levels in sorted(fired.items())
            )
            ok = lint.get("verdict") == "PASS"
            drift = lint.get("drift") or []
            # The state stays here, next to the badge it qualifies — a PASS must never read
            # as "ready to send" while the checked copy and the loaded copy differ. The
            # procedure that clears it lives in the Operator notes panel.
            drift_note = (
                '<p class="note"><span class="pill warn">not cleared to start</span> '
                "The badge below describes the reviewed files, not what would go out today. "
                "See <strong>Operator notes</strong> for what is left to do.</p>"
                if drift
                else ""
            )
            qa = f"""
              {drift_note}
              <p><span class="pill {"good" if ok else "bad"}">{_e(lint.get("verdict", "?"))}</span>
              Every email was rendered against every recipient and checked —
              <strong>{lint.get("renders", 0):,} rendered emails</strong>
              ({lint.get("rows", 0):,} people × {lint.get("touches", 0)} emails),
              <strong>{len(catalogue):,} different checks</strong> per render.
              {lint.get("errors", 0)} blocking problem(s), {lint.get("warnings", 0)} cosmetic note(s).</p>
              <table><thead><tr><th>What is checked</th><th>Checks</th><th>Flagged here</th>
              </tr></thead><tbody>{cat_rows}</tbody></table>
              <details><summary>The {len(fired)} check(s) that flagged something</summary>
              <table><thead><tr><th>Check</th><th>Protects against</th><th>Blocking</th>
              <th>Cosmetic</th></tr></thead><tbody>{finding_rows}</tbody></table></details>"""
        else:
            qa = (
                "<p class='note'>No quality record on file for this message. Never-checked and "
                "checked-and-clean look identical here — which is exactly why the record is kept.</p>"
            )
        seats = ", ".join(f"{_seat_label(c['seat'])} ({c['enrolled']})" for c in msg["audience"])
        seq_blocks.append(
            f"""<div class="card">
              <h2>{_e(msg.get("title") or msg["sequence_id"])}</h2>
              <p class="note">{n:,} people · seats: {_e(seats)}</p>
              <table><thead><tr><th>Email</th><th>Subject</th><th>The argument it opens on</th>
              <th>Length</th></tr></thead><tbody>{touches}</tbody></table>
              <h3>Quality checks</h3>{qa}
            </div>"""
        )

    return f"""
      <div class="card">
        <h2>How every email is built</h2>
        <p class="note">Each email follows one fixed structure. Only the first line and the
        seat-specific problem change per person.</p>
        <ol class="steps">
          <li><strong>A fact about them</strong> — one verified sentence about that specific
              company, researched and checked against a primary source.</li>
          <li><strong>Their seat's problem</strong> — the thing that job actually loses sleep
              over, never a generic pitch.</li>
          <li><strong>Why their current stack can't close it</strong> — identity tooling
              records what happened; it does not prove who authorised it.</li>
          <li><strong>Proof</strong> — one comparable customer, described by company type,
              never named.</li>
          <li><strong>One offer</strong> — we put a single artifact on the table and let them
              take it or not. We are not asking them for anything: no meeting request, no
              calendar link, no "quick call". "Want the short write up?" is the shape.</li>
        </ol>
      </div>

      <div class="card">
        <h2>Which problem we lead on, per job</h2>
        <p class="note">The rule the copy is written against. "Detected" means our automated
        check can recognise that title and verify the email matches the seat; the other four
        are written by hand and unverified.</p>
        <table><thead><tr><th>Job</th><th>What we lead on</th><th>What they get</th></tr></thead>
        <tbody>{axis_rows}</tbody></table>
      </div>

      <div class="card">
        <h2>Every subject line in the campaign</h2>
        <p class="note"><strong>{len(subjects)} different subject lines across {
        sup["total"]:,} people.</strong>
        {slots} of the emails carry a subject of their own, of which {slots - len(subjects)}
        reuse a subject another group also gets.{
        f" The other {threaded} arrive as replies inside the first email's own thread, so they"
        " carry no new subject at all — the reader sees the conversation, not a fresh pitch."
        if threaded
        else ""
    }
        Subjects are deliberately not personalised (the personalisation is the first line of the
        body), but this is the least varied part of the campaign.</p>
        {shared_note}
        <table><thead><tr><th>Subject</th><th>People who receive it</th><th>Used by</th></tr>
        </thead><tbody>{subj_rows}</tbody></table>
      </div>

      <div class="card">
        <h2>What the opening line is about</h2>
        <p class="note">Every email opens on one researched sentence about that company.
        They are not all the same kind of claim: an <strong>event</strong> is something that
        happened on a date and decays; a <strong>capability</strong> describes what the company
        does and stays true. Both are legitimate, but only an event justifies "why now".</p>
        {
        _barlist(
            [
                ("something happened (event)", sup["event_shaped"]),
                ("what they already do (capability)", sup["capability_shaped"]),
            ],
            sup["total"],
        )
    }
        <p class="note"><strong>{_pct(sup["capability_shaped"], sup["total"])} are capability
        statements.</strong> Worth knowing, because the sequence spec describes the clause as
        "event-shaped, not a static capability statement" — the shipped list is mostly the
        latter. The campaign's own experiment notes say the news hook was dropped deliberately;
        the spec text was not updated to match.</p>
        <table><thead><tr><th>Kind</th><th>Shape</th><th>People</th><th>Share</th></tr></thead>
        <tbody>{sig_rows}</tbody></table>
      </div>

      {"".join(seq_blocks)}"""


def _learn_view(m: dict) -> str:
    cm = m["cells"]
    cells = cm["cells"]
    sup = m["supply"]

    seats = sorted({c["seat"] for c in cells})
    variants = sorted({c["variant"] for c in cells})
    params = [
        ("Market", len(sup["countries"]), ", ".join(c["name"] for c in sup["countries"])),
        ("Company type", len({c["segment"] for c in cells}), "startup, enterprise"),
        ("Buyer seat", len(seats), ", ".join(_seat_label(x) for x in seats)),
        ("Message", len(variants), f"{len(variants)} different bodies"),
        ("Email in sequence", 3, "day 1, day 4, day 9"),
        (
            "Trigger type",
            1,
            "topic surge only — the other four signal types reached none of this list",
        ),
    ]
    combos = 1
    for _, n, _ in params:
        combos *= max(n, 1)
    param_rows = "".join(
        f"<tr><td><strong>{_e(name)}</strong></td><td class='num-cell'>{n}</td>"
        f"<td class='muted'>{_e(detail)}</td></tr>"
        for name, n, detail in params
    )

    # Seat × message grid — the shape of the experiment at a glance.
    grid_head = "".join(f"<th>{_e(v)}</th>" for v in variants)
    grid_rows = ""
    biggest = max((c["enrolled"] for c in cells), default=1) or 1
    for seat in seats:
        tds = ""
        for var in variants:
            cell = next((c for c in cells if c["seat"] == seat and c["variant"] == var), None)
            if cell:
                heat = 0.12 + 0.6 * (cell["enrolled"] / biggest)
                tds += (
                    f'<td class="gcell" style="background:rgba(124,92,255,{heat:.2f})">'
                    f"<strong>{cell['enrolled']:,}</strong></td>"
                )
            else:
                tds += '<td class="gcell empty">·</td>'
        grid_rows += f"<tr><th>{_e(_seat_label(seat))}</th>{tds}</tr>"

    readable = [c for c in cells if c["comparable_on"]]
    seat_readable = [c for c in cells if any(e.startswith("seat") for e in c["comparable_on"])]
    var_readable = [c for c in cells if any(e.startswith("variant") for e in c["comparable_on"])]

    lift_rows = "".join(
        f'<div class="brow"><div class="blabel">{_e(_seat_label(c["seat"]))} · {_e(c["variant"])}</div>'
        f'<div class="btrack"><div class="bfill ta" '
        f'style="width:{min(100, 100 * (c["detectable_lift"] or 20) / 14):.0f}%"></div></div>'
        f'<div class="bval">{c["detectable_lift"]}×</div></div>'
        for c in sorted(cells, key=lambda x: x["detectable_lift"] or 99)
        if c["detectable_lift"]
    )

    goal_cards = ""
    for c in m["campaigns"]["campaigns"]:
        pva = "".join(
            f"<tr><td>{_e(k)}</td><td class='num-cell'>{v['target']:,}</td>"
            f"<td class='num-cell'>{v['actual']:,}</td>"
            f"<td class='num-cell muted'>{v['pct']}%</td></tr>"
            for k, v in c["promised_vs_actual"].items()
        )
        goal_cards += (
            f'<div class="card"><h2>{_e(c["title"])}</h2>'
            + (
                "<p class='note'>What this campaign promised, and where it actually is. "
                "Everything reads 0% because nothing has been sent yet.</p>"
                "<table><thead><tr><th>Measure</th><th>Goal</th><th>So far</th><th></th>"
                f"</tr></thead><tbody>{pva}</tbody></table>"
                if pva
                else "<p class='note'>No goals recorded for this campaign.</p>"
            )
            + "</div>"
        )

    return f"""
      {goal_cards}
      <div class="card banner">
        <h2>The hypothesis</h2>
        <p style="font-size:17px;margin:0 0 10px">If we open with a <strong>verified fact about
        the company</strong> and lead on <strong>the problem that buyer's seat actually owns</strong>,
        more of them reply than to a generic pitch.</p>
        <p class="note">The last attempt sent 24 emails, got one reply, and it was an unsubscribe.
        An audit found half the list could not have acted on the offer. This run fixes the aim
        before spending more sends — so the thing being tested is <em>targeting plus opening
        line together</em>, not either one alone.</p>
      </div>

      <div class="card">
        <h2>Second hypothesis: does the trigger predict the reply?</h2>
        <p style="font-size:16px;margin:0 0 10px">Not every reason to write is worth the same.
        A company <strong>researching the exact topic we sell into</strong> may be a better
        timing signal than one that merely <em>ships something adjacent</em> — and if so, the
        topic itself may rank: agent identity surging is a different buying moment from
        multi-agent systems surging.</p>
        <p class="note">This is worth testing because it changes what we buy. Topic-surge data
        is a paid feed; a durable capability statement is free research. If surge predicts
        replies, the feed earns its cost and should gate the list. If it does not, we are paying
        for a sort order that does nothing.</p>
        <p class="note"><strong>Right now this question is half-answerable at best.</strong>
        {_pct(m["intent"]["matched"], m["intent"]["total"])} of the people
        being emailed can be traced to an intent record; the rest carry no signal at all, so they
        cannot sit on either side of the comparison. And only one signal type is present
        (topic surge), so "which kind of trigger works best" cannot be asked of this run — only
        "does topic surge beat no recorded surge", and even that compares a traceable group
        against an untraceable one, which is not a clean contrast.</p>
        <p class="note"><strong>To make it answerable next run:</strong> carry the intent fields
        onto the sending list instead of leaving them in the prospect pool, so a reply can be
        attributed to the trigger that earned the send; and record the trigger <em>type</em>
        per person, not just the sentence it produced.</p>
      </div>

      <div class="card">
        <h2>What varies, and by how much</h2>
        <p class="note">Five things change from person to person. Multiplied out that is
        <strong>{combos:,} possible combinations</strong> across {sup["total"]:,} people — roughly
        {sup["total"] // max(combos, 1)} people per combination. That ratio, not the total, is what
        decides whether anything is measurable.</p>
        <table><thead><tr><th>Parameter</th><th>Levels</th><th></th></tr></thead>
        <tbody>{param_rows}</tbody></table>
      </div>

      <div class="card">
        <h2>The experiment, drawn</h2>
        <p class="note">Rows are buyer seats, columns are the four different message bodies.
        A filled square is a real group of people; darker means larger.</p>
        <div class="gridwrap"><table class="grid">
          <thead><tr><th></th>{grid_head}</tr></thead><tbody>{grid_rows}</tbody></table></div>
        <p class="note"><strong>Read the shape, not the numbers.</strong> Where a column has
        several filled squares, one message went to several seats — so seat can be compared
        cleanly. Where a row has several filled squares, one seat got several messages — so the
        message can be compared cleanly. A square that is alone in both its row and column can
        be compared with nothing.</p>
      </div>

      <div class="card">
        <h2>What this run can and cannot answer</h2>
        <div class="verdicts">
          <div class="v ok"><div class="vn">{len(seat_readable)}</div>
            <div class="vl">groups where <strong>seat</strong> is readable</div>
            <div class="vd muted">Same message, different job — so a gap is about the person.</div></div>
          <div class="v ok"><div class="vn">{len(var_readable)}</div>
            <div class="vl">groups where <strong>message</strong> is readable</div>
            <div class="vd muted">Same job, different message — so a gap is about the copy.</div></div>
          <div class="v bad"><div class="vn">{len(cells) - len(readable)}</div>
            <div class="vl">groups readable against <strong>nothing</strong></div>
            <div class="vd muted">Both the audience and the message differ, so a gap means neither.</div></div>
        </div>
        <p class="note">The message comparison is an accident worth keeping: the recipients whose
        job title our classifier could not place are spread across all three enterprise messages,
        which holds the audience roughly constant and varies only the copy. It is the one place
        this run can compare messages at all.</p>
      </div>

      <div class="card">
        <h2>How big a difference each group could even show</h2>
        <p class="note">At the number of people planned per group, a difference smaller than this
        would be indistinguishable from chance. Shorter is better.</p>
        {lift_rows or "<p class='note'>No groups sized yet.</p>"}
        <p class="note">Nothing has been sent, so these are forecasts at the planned size. Once
        sending starts they are recalculated against real numbers.</p>
      </div>

      {
        "".join(
            f'<details class="card"><summary>Full experiment notes · {_e(c["title"])}</summary>'
            + _experiment_block(c.get("experiment") or {})
            + "</details>"
            for c in m["campaigns"]["campaigns"]
            if c.get("experiment")
        )
    }"""


def _ops_view(m: dict) -> str:
    """The mechanics behind the other four panels — what is actually loaded in the sending
    tool, what still has to be pushed, and what is blocking the start.

    This panel exists so that detail has somewhere to live other than the top of the page.
    The reader this page is written for opens it for the numbers; the re-push instruction is
    real and load-bearing, but it is an instruction to the one person who presses the
    buttons, not a headline. The safety property survives the move because the *state* stays
    where the copy is judged ("not cleared to start", next to the quality badge it qualifies)
    and only the *procedure* comes here.
    """
    staged = {
        sq["sequence_id"]: sq.get("staged_ts", "")
        for c in m["campaigns"]["campaigns"]
        for sq in c.get("sequences", [])
    }
    drifted = [msg for msg in m["messages"] if (msg.get("lint") or {}).get("drift")]
    cards = ""

    # "Written, checked and loaded" is the sentence a reader acts on. It must never appear
    # while the reviewed copy and the loaded copy are different things — the whole point of
    # the drift check is that those two can silently diverge after a revision.
    if any(c.get("state") == "not_sending" for c in m["campaigns"]["campaigns"]):
        planned = sum(int(c["targets"].get("emails", 0) or 0) for c in m["campaigns"]["campaigns"])
        loaded_note = (
            "The emails are written and checked, but the campaign is <strong>not cleared to "
            "start</strong> yet — the re-push below is what is left."
            if drifted
            else "Everything is written, checked and loaded; a person still has to press send."
        )
        cards += (
            '<div class="card banner"><h2>Nothing has been sent</h2><p><strong>0 of '
            f"{planned:,}</strong> planned emails have gone out. {loaded_note}</p></div>"
        )

    if drifted:
        rows = ""
        for msg in drifted:
            lint = msg["lint"]
            pushed = (staged.get(msg["sequence_id"]) or "")[:10] or "—"
            checked = (lint.get("ran_at") or "")[:10] or "—"
            what = "; ".join(lint["drift"]).capitalize()
            rows += (
                f"<tr><td>{_e(msg.get('title') or msg['sequence_id'])}</td>"
                f"<td class='muted'>{_e(pushed)}</td>"
                f"<td class='muted'>{_e(checked)}</td>"
                f"<td class='muted'>{_e(what)}</td></tr>"
            )
        cards += f"""
      <div class="card banner">
        <h2>Re-push before anyone starts a sequence</h2>
        <p><strong>{len(drifted)} of {len(m["messages"])} sequences</strong> were revised
        after they were last pushed to the sending tool. The tool still holds the older
        emails and the older recipient list, so every check on this page describes the
        revised files rather than what would actually go out today. Starting one now would
        send the version that was already replaced, to a list that still includes the people
        since held back.</p>
        <table><thead><tr><th>Sequence</th><th>Last pushed</th><th>Copy last checked</th>
        <th>What changed since it was pushed</th></tr></thead><tbody>{rows}</tbody></table>
        <p class="note">Re-pushing is a person's job in the sending tool's own interface.
        Nothing on this page and nothing in the pipeline can do it — that capability is
        withheld by design, so that no automated step can put mail in front of a human
        without one.</p>
      </div>"""

    blocker = ""
    for c in m["campaigns"]["campaigns"]:
        blocker = blocker or ((c.get("window") or {}).get("capacity_blocker") or "")
    if blocker:
        cards += (
            '<div class="card"><h2>What is holding it up</h2>'
            f'<p class="note">{_e(blocker)}</p></div>'
        )

    # Keyed on whether anything is *pending*, not on whether the panel happens to be empty:
    # "no outstanding steps" is a finding a reader needs stated, and it must not be inferable
    # only from the absence of a card — never-checked and checked-and-clear look identical
    # when both render as silence.
    if not drifted and not blocker:
        cards += (
            '<div class="card"><h2>Nothing outstanding</h2>'
            "<p class='note'>Every sequence in the sending tool matches the copy and the "
            "recipient list that were checked. Whether to start one is a decision, not a "
            "missing step.</p></div>"
        )
    return cards


def page_title(m: dict) -> str:
    """ "Email Campaign Status — <product>". Falls back to the profile when no campaign
    declares a product, so a tenant that never set one still gets a usable title rather
    than a dangling dash."""
    for c in m["campaigns"]["campaigns"]:
        if c.get("product"):
            return f"Email Campaign Status — {c['product']}"
    return f"Email Campaign Status — {m['profile']}"


def render_html(m: dict) -> str:
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


def render_dashboard(profile: str, content_root: Path | None = None, *, stubs: bool = True) -> Path:
    model = build_model(profile, content_root)
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
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the email-campaign status page.")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument("--no-stubs", action="store_true", help="do not rewrite the retired pages")
    args = ap.parse_args(argv)
    root = Path(args.content_root) if args.content_root else None
    print(f"wrote {render_dashboard(args.profile, root, stubs=not args.no_stubs)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
