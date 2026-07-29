"""Render a single, self-refreshing HTML status page for the whole prospect →
enrichment → ready-to-load pipeline.

The operator was drowning in near-identical CSVs with no way to see, at a glance,
*where the data is* and *what to do next*. This module answers that in one page:

  * the **account layer** — companies identified by the bulk (Vibe) pulls in
    ``prospects/imports/`` that carry no person/email yet (the enrichment backlog),
  * the **email funnel** — people who DO have an email, gated by deliverability
    into ready / needs-verification / blocked / excluded, and
  * the **process, cost, and next step** for each layer, in plain English.

It is deliberately stdlib-only (like :mod:`gtm_core.prospects_consolidate`) so it
can run standalone, on a schedule, or be called at the tail of every consolidation
run — every sweep refreshes ``prospects/status.html`` automatically. Anything that
needs an MCP call (the live Saleshandy sequence state) is read from an optional
``.pool/sequence-state.json`` the skill/agent layer drops; the page degrades
gracefully when it is absent (same plumbing-vs-judgment split as consolidate).

Numbers are read from the *files on disk* (the source of truth already written by
consolidate), never recomputed — so the page can never disagree with the CSVs.
"""

from __future__ import annotations

import argparse
import csv
import glob
import html
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.outreach_log import collect_rows
from gtm_core.paths import resolve_content_root
from gtm_core.prospects_backlog import enrichment_queue_path
from gtm_core.prospects_consolidate import (
    _account_has_dossier,
    _load_master,
    _org_token,
    _pool_dir,
    _prospects_dir,
    needs_verification_path,
    pool_status,
    ready_to_load_path,
)
from gtm_core.prospects_state import load_latest

# Reference cost figures (NOT live — see gtm_core cost ledger for actuals).
# Sourced from the Vibe/RocketReach cost-model notes; shown so the operator can
# see the shape of the spend before authorizing an enrichment pass.
COST_MODEL = [
    (
        "Bulk account pull (Vibe fetch)",
        "free",
        "masked 5-row preview; identifies companies, no emails",
    ),
    (
        "Person resolution (RocketReach person_search)",
        "~metered lookup",
        "find the decision-maker at each account",
    ),
    (
        "Email + grade (RocketReach lookup)",
        "~metered lookup",
        "returns the address + A/B/F deliverability grade",
    ),
    (
        "Vibe export (unmask emails)",
        "~1 credit/row (~$0.03)",
        "alternative bulk path; ~$30 per 1,000 rows",
    ),
    ("Saleshandy verify-on-import", "included", "grades Valid/Risky/Bad inside the sequencer"),
]


def _domain_from_site(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0]


def _count_rows(path: Path) -> int:
    """Data rows (excluding header) in a CSV, or 0 if missing."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)


def _import_tokens(profile: str, content_root: Path | None) -> tuple[set[str], int, int]:
    """Org tokens across the bulk-pull batches in ``imports/``, plus row and file counts.

    Split out of :func:`_account_layer` because the *identified* headline is a
    union across every discovery path (see :func:`_lifecycle_funnel`), not just
    this one — both callers need the token set, and re-globbing per caller would
    read the same batches twice.
    """
    imports_dir = _prospects_dir(profile, content_root) / "imports"
    acct_tokens: set[str] = set()
    acct_rows = 0
    files = sorted(glob.glob(str(imports_dir / "*.csv")))
    for f in files:
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                acct_rows += 1
                dom = (row.get("business_domain") or "").strip() or _domain_from_site(
                    row.get("business_website", "")
                )
                tok = _org_token(dom, row.get("business_name", ""))
                if tok:
                    acct_tokens.add(tok)
    return acct_tokens, acct_rows, len(files)


def _account_layer(imports: tuple[set[str], int, int], master_tokens: set[str]) -> dict:
    """The bulk-pull backlog: unique companies in ``imports/`` and how many
    already have at least one person (with an email) in the master list.

    Scoped to ``imports/`` on purpose — this is the *bulk-pull* backlog, one of
    two discovery paths. The page's headline "identified" count is the union of
    both (``lifecycle["identified"]``); these keys stay narrow.
    """
    acct_tokens, acct_rows, files = imports
    enriched = acct_tokens & master_tokens
    return {
        "raw_rows": acct_rows,
        "files": files,
        "unique_accounts": len(acct_tokens),
        "enriched_accounts": len(enriched),
        "backlog_accounts": len(acct_tokens - master_tokens),
    }


def _latest_tokens(profile: str, content_root: Path | None) -> tuple[set[str], set[str], set[str]]:
    """Org tokens from ``latest.json`` — (all, ICP-scored, Tier-A).

    ``latest.json`` is the cumulative account state every prospect run merges
    into, and is the *only* record of a run that resolved people directly
    (``prospects-*-hubspot.csv``) without ever emitting an ``imports/`` batch.
    Reading it here is what stops such a run from showing as zero discovered.
    """
    all_tokens: set[str] = set()
    scored: set[str] = set()
    tier_a: set[str] = set()
    for item in load_latest(profile, content_root).get("items", []):
        tok = _org_token(item.get("domain", ""), item.get("company", ""))
        if not tok:
            continue
        all_tokens.add(tok)
        tier = (item.get("tier") or "").strip().upper()
        if tier or item.get("score") is not None:
            scored.add(tok)
        if tier == "A":
            tier_a.add(tok)
    return all_tokens, scored, tier_a


def _lifecycle_funnel(
    profile: str,
    content_root: Path | None,
    master: list[dict],
    master_tokens: set[str],
    import_tokens: set[str],
) -> dict:
    """Discovered -> Scored -> Enriched -> Dossier -> Drafted — the real sales-readiness
    funnel, distinct from Layer 2's *email-confidence* funnel (ready/needs-verification are
    about deliverability, not lifecycle stage). This is the pipeline's actual order: ICP
    scoring ranks the backlog *before* any enrichment spend (see
    ``prospects_backlog.select_backlog`` — "score before you spend"), so an account is scored
    first and only a subset of scored accounts get enriched, not the other way around.

    Dossier/Drafted are counted over **Tier-A accounts only** — the one cohort the pipeline
    auto-generates either for (see ``prospects_consolidate.tier_a_needing_dossier``) — so the
    funnel keeps its narrowing property instead of mixing in unrelated manually-drafted
    Tier-B/C accounts.

    Every stage is a **union over all sources that record it**, never one file: a run that
    resolved people directly (no ``imports/`` batch) and a bulk pull that never got enriched
    are both real discovery, and counting only one path reported a stage that ran as zero.
    The union is also what keeps the funnel monotone — identified ⊇ qualified ⊇ enriched —
    which reading unrelated files per stage did not guarantee.
    """
    root = content_root or resolve_content_root()

    queue_tokens: set[str] = set()
    qpath = enrichment_queue_path(profile, content_root)
    if qpath.exists():
        with qpath.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                tok = _org_token(row.get("domain", ""), row.get("company", ""))
                if tok:
                    queue_tokens.add(tok)

    latest_all, latest_scored, latest_tier_a = _latest_tokens(profile, content_root)

    enriched = len(master_tokens)
    # The queue is post-scoring by construction ("score before you spend" —
    # prospects_backlog.select_backlog), and every master row carries a tier/score.
    scored = len(latest_scored | queue_tokens | master_tokens)
    identified = len(latest_all | queue_tokens | master_tokens | import_tokens)

    tier_a: dict[str, dict] = {}
    for r in master:
        if (r.get("tier") or "").strip().upper() != "A":
            continue
        tok = _org_token(r.get("company_domain", ""), r.get("company", ""))
        if tok and tok not in tier_a:
            tier_a[tok] = {"company": r.get("company", ""), "domain": r.get("company_domain", "")}
    # Tier-A accounts scored but not yet enriched exist only in latest.json — they are the
    # accounts still needing a brief, so omitting them understated exactly the open work.
    for item in load_latest(profile, content_root).get("items", []):
        tok = _org_token(item.get("domain", ""), item.get("company", ""))
        if tok in latest_tier_a and tok not in tier_a:
            tier_a[tok] = {"company": item.get("company", ""), "domain": item.get("domain", "")}

    dossier_tokens: set[str] = set()
    dossier_samples: list[dict] = []
    for tok, acct in tier_a.items():
        has, matched = _account_has_dossier(profile, acct["company"], acct["domain"], content_root)
        if has:
            dossier_tokens.add(tok)
            if len(dossier_samples) < 5:
                dossier_samples.append({"company": acct["company"], "folder": matched})

    outreach_rows = collect_rows(root, profile)
    drafted_raw_tokens = {_org_token("", row.account) for row in outreach_rows if row.account}
    drafted_tokens = set(tier_a) & drafted_raw_tokens

    prospects_dir_abs = _prospects_dir(profile, content_root)
    draft_samples: list[dict] = []
    seen_draft_accounts: set[str] = set()
    for row in outreach_rows:
        tok = _org_token("", row.account)
        if tok not in tier_a or tok in seen_draft_accounts:
            continue
        seen_draft_accounts.add(tok)
        abs_file = root.parent / row.file
        draft_samples.append(
            {
                "company": row.account,
                "href": os.path.relpath(abs_file, start=prospects_dir_abs),
                "repo_path": row.file,
            }
        )
        if len(draft_samples) >= 5:
            break

    return {
        "identified": identified,
        "scored": scored,
        "enriched": enriched,
        "tier_a": len(tier_a),
        "dossier": len(dossier_tokens),
        "drafted": len(drafted_tokens),
        "dossier_samples": dossier_samples,
        "draft_samples": draft_samples,
    }


def build_status(profile: str, content_root: Path | None = None) -> dict:
    """Assemble the full status model from files on disk. No MCP, no re-sweep."""
    ps = pool_status(profile, content_root)
    imports = _import_tokens(profile, content_root)
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    master_tokens = {_org_token(r.get("company_domain", ""), r.get("company", "")) for r in master}
    master_tokens.discard("")

    raw_files = sorted(
        glob.glob(str(_prospects_dir(profile, content_root) / "prospects-*-hubspot.csv"))
    )
    raw_rows = 0
    for f in raw_files:
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            raw_rows += max(0, sum(1 for _ in csv.reader(fh)) - 1)

    ready = _count_rows(ready_to_load_path(profile, content_root))
    needs = _count_rows(needs_verification_path(profile, content_root))
    blocked = ps["by_confidence_tier"].get("blocked", 0)
    master_total = ps["master_total"]
    excluded = max(0, master_total - ready - needs - blocked)  # DNC + already-sent

    sequences = _load_sequences(profile, content_root)

    prospects_dir_abs = _prospects_dir(profile, content_root)
    links = {
        "ready_csv": os.path.relpath(
            ready_to_load_path(profile, content_root), start=prospects_dir_abs
        ),
        "needs_csv": os.path.relpath(
            needs_verification_path(profile, content_root), start=prospects_dir_abs
        ),
        "accounts_dir": "../accounts/",
    }

    return {
        "profile": profile,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "links": links,
        "accounts": _account_layer(imports, master_tokens),
        "lifecycle": _lifecycle_funnel(profile, content_root, master, master_tokens, imports[0]),
        "funnel": {
            "raw_files": len(raw_files),
            "raw_rows": raw_rows,
            "master_total": master_total,
            "ready": ready,
            "needs_verification": needs,
            "blocked": blocked,
            "excluded_dnc_or_sent": excluded,
            "unconsolidated_in_raw": ps["unconsolidated_in_raw_exports"],
        },
        "sequences": sequences,
        "sequence": sequences[0] if sequences else None,  # back-compat
        "cost_model": COST_MODEL,
    }


def _int(v) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_seq(d: dict) -> dict:
    """Flatten a sequence's live stats into the curated set the page shows.

    Accepts EITHER a raw Saleshandy ``get_sequence_stats`` payload (``prospects``
    list + ``emails``) OR an already-flat dict the skill assembled — so the skill
    layer can drop the MCP response verbatim without reshaping it.
    """
    if "prospects" in d and isinstance(d.get("prospects"), list):
        p = (d["prospects"] or [{}])[0]
        est = (d.get("emails") or {}).get("status", {})
        bounced = _int(est.get("bounced")) or (
            _int(est.get("hardBounced"))
            + _int(est.get("softBounced"))
            + _int(est.get("blockBounced"))
        )
        return {
            "id": d.get("sequenceId", ""),
            "name": d.get("sequenceName", ""),
            "status": d.get("status", ""),
            "loaded": _int(p.get("total")),
            "sent": _int(p.get("contacted")),
            "pending": _int(p.get("upcoming")) + _int(p.get("waiting")),
            "delivered": _int(est.get("delivered")),
            "opened": _int(p.get("open")) or _int(est.get("opened")),
            "replied": _int(p.get("replied")) or _int(est.get("replied")),
            "bounced": bounced or _int(p.get("bounced")),
            "interested": _int(p.get("interested")),
            "meetings": _int(p.get("meetingBooked")),
            "deal_value": _int(p.get("meetingBookedDealValue"))
            + _int(p.get("interestedDealValue")),
        }
    # already flat
    keys = (
        "id",
        "name",
        "status",
        "loaded",
        "sent",
        "pending",
        "delivered",
        "opened",
        "replied",
        "bounced",
        "interested",
        "meetings",
        "deal_value",
    )
    return {k: (d.get(k) if k in ("id", "name", "status") else _int(d.get(k))) for k in keys}


def _load_sequences(profile: str, content_root: Path | None) -> list[dict]:
    """Live sequencer stats, dropped by the skill/agent layer (MCP-free module).

    Prefers ``sequence-stats.json`` (a list of raw or flat sequence stats); falls
    back to the older single-sequence ``sequence-state.json``. Absent → empty list
    (the page renders fine without a sequencer section)."""
    pool = _pool_dir(profile, content_root)
    stats_file = pool / "sequence-stats.json"
    if stats_file.exists():
        try:
            raw = json.loads(stats_file.read_text(encoding="utf-8"))
            items = raw.get("sequences", raw) if isinstance(raw, dict) else raw
            return [_normalize_seq(s) for s in items if isinstance(s, dict)]
        except (json.JSONDecodeError, OSError):
            pass
    state_file = pool / "sequence-state.json"
    if state_file.exists():
        try:
            d = json.loads(state_file.read_text(encoding="utf-8"))
            d.setdefault("loaded", d.get("loaded", 0))
            return [_normalize_seq(d)]
        except (json.JSONDecodeError, OSError):
            pass
    return []


# --- HTML ---------------------------------------------------------------


def _bar(segments: list[tuple[str, int, str]], total: int) -> str:
    """A single stacked proportion bar. segments = (label, value, css-class)."""
    if total <= 0:
        total = 1
    cells = "".join(
        f'<span class="seg {cls}" style="flex:{max(v, 0)}" title="{html.escape(lbl)}: {v}"></span>'
        for lbl, v, cls in segments
        if v > 0
    )
    return f'<div class="bar">{cells}</div>'


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d > 0 else "—"


def render_html(status: dict) -> str:
    f = status["funnel"]
    a = status["accounts"]
    lc = status["lifecycle"]
    links = status["links"]
    sequences = status.get("sequences") or []
    prof = html.escape(status["profile"])

    def stat(label, value, sub="", cls=""):
        sub_html = f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ""
        return (
            f'<div class="stat {cls}"><div class="num">{value:,}</div>'
            f'<div class="lbl">{html.escape(label)}</div>{sub_html}</div>'
        )

    seq_block = ""
    if sequences:
        cards = []
        for s in sequences:
            st = html.escape(str(s.get("status", "") or "")).upper() or "—"
            st_cls = "ok" if st == "PAUSED" else ("warn" if st == "ACTIVE" else "")
            loaded, sent = s["loaded"], s["sent"]
            delivered, opened, replied = s["delivered"], s["opened"], s["replied"]
            bounced, meetings = s["bounced"], s["meetings"]
            pending = s["pending"] or max(0, loaded - sent)
            # Deliverability flag: bounce rate vs delivered+bounced (ramp-critical).
            bounce_base = delivered + bounced
            bounce_warn = bounce_base >= 20 and (100 * bounced / bounce_base) >= 5
            health = (
                f'<span class="pill blk">⚠ {_pct(bounced, bounce_base)} bounce</span>'
                if bounce_warn
                else (
                    f'<span class="pill ok">{_pct(bounced, bounce_base)} bounce</span>'
                    if sent
                    else ""
                )
            )
            funnel_bar = _bar(
                [
                    ("replied", replied, "ready"),
                    ("opened", max(0, opened - replied), "verify"),
                    ("delivered", max(0, delivered - opened), "acct-b"),
                    ("pending", pending, "out"),
                ],
                max(loaded, 1),
            )
            cards.append(f"""
          <div class="seqrow">
            <div class="seqhead">
              <b>{html.escape(str(s.get("name", "") or "Sequence"))}</b>
              <span class="pill {st_cls}">{st}</span> {health}
              <code>{html.escape(str(s.get("id", "") or ""))}</code>
            </div>
            <div class="stats tight">
              {stat("loaded", loaded)}
              {stat("sent", sent, _pct(sent, loaded) + " of loaded")}
              {stat("pending", pending, "not yet reached")}
              {stat("opened", opened, _pct(opened, delivered) + " open rate", "verify")}
              {stat("replied", replied, _pct(replied, delivered) + " reply rate", "ready")}
              {stat("meetings", meetings, "booked", "ready")}
            </div>
            {funnel_bar}
            <div class="legend"><span><i style="background:var(--ready)"></i>replied</span><span><i style="background:var(--verify)"></i>opened</span><span><i style="background:var(--acct)"></i>delivered</span><span><i style="background:var(--out)"></i>pending</span></div>
          </div>""")
        seq_block = f"""
        <div class="card">
          <h2>Live sequencer performance <span class="muted" style="text-transform:none;font-weight:400">· Saleshandy</span></h2>
          {"".join(cards)}
          <p class="note">Live from Saleshandy stats. Nothing sends until a human resumes a paused sequence — deliverability (bounce %) is the number to watch during mailbox ramp.</p>
        </div>"""

    cost_rows = "".join(
        f"<tr><td>{html.escape(step)}</td><td class='num-cell'>{html.escape(cost)}</td>"
        f"<td class='muted'>{html.escape(desc)}</td></tr>"
        for step, cost, desc in status["cost_model"]
    )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prospecting pipeline · {prof}</title>
<style>
:root {{
  --bg:#0b0d12; --panel:#141821; --panel2:#1b202b; --ink:#e7ecf3; --muted:#8b96a8;
  --line:#252b38; --ready:#2ecc71; --verify:#f1c40f; --blocked:#e74c3c; --out:#5b6675;
  --acct:#4aa8ff; --accent:#7c5cff;
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
h2 {{ font-size:15px; margin:0 0 12px; letter-spacing:.02em; text-transform:uppercase; color:var(--muted); font-weight:600; }}
.ts {{ color:var(--muted); font-size:13px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:20px 22px; margin-bottom:18px; }}
.stats {{ display:flex; gap:14px; flex-wrap:wrap; }}
.stat {{ background:var(--panel2); border-radius:10px; padding:14px 16px; min-width:120px; flex:1; }}
.stat .num {{ font-size:28px; font-weight:700; letter-spacing:-.03em; }}
.stat .lbl {{ color:var(--muted); font-size:13px; margin-top:2px; }}
.stat .sub {{ color:var(--muted); font-size:11px; margin-top:4px; opacity:.8; }}
.stat.ready .num {{ color:var(--ready); }}
.stat.verify .num {{ color:var(--verify); }}
.stat.blocked .num {{ color:var(--blocked); }}
.stat.acct .num {{ color:var(--acct); }}
.bar {{ display:flex; height:22px; border-radius:6px; overflow:hidden; margin:14px 0 8px; background:var(--panel2); }}
.seg {{ display:block; }}
.seg.ready {{ background:var(--ready); }} .seg.verify {{ background:var(--verify); }}
.seg.blocked {{ background:var(--blocked); }} .seg.out {{ background:var(--out); }}
.seg.acct-b {{ background:var(--acct); }} .seg.acct-e {{ background:var(--ready); }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:var(--muted); }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:middle; }}
.flow {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:13px; color:var(--muted); margin-top:6px; }}
.flow b {{ color:var(--ink); }}
.flow .arrow {{ color:var(--accent); }}
.muted {{ color:var(--muted); }}
.note {{ font-size:13px; color:var(--muted); margin:12px 0 0; padding-left:12px; border-left:3px solid var(--accent); }}
.pill {{ font-size:11px; padding:2px 8px; border-radius:999px; vertical-align:middle; }}
.pill.ok {{ background:rgba(46,204,113,.15); color:var(--ready); }}
.pill.warn {{ background:rgba(241,196,15,.15); color:var(--verify); }}
.pill.blk {{ background:rgba(231,76,60,.15); color:var(--blocked); }}
.stats.tight {{ gap:10px; }}
.stats.tight .stat {{ min-width:96px; padding:10px 12px; }}
.stats.tight .stat .num {{ font-size:22px; }}
.seqrow {{ padding:14px 0; border-bottom:1px solid var(--line); }}
.seqrow:last-of-type {{ border-bottom:none; }}
.seqhead {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; }}
.seqhead code {{ margin-left:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
td {{ padding:8px 10px; border-bottom:1px solid var(--line); }}
.num-cell {{ white-space:nowrap; font-variant-numeric:tabular-nums; }}
code {{ background:var(--panel2); padding:1px 6px; border-radius:5px; font-size:12px; }}
ol.steps {{ margin:0; padding-left:20px; }}
ol.steps li {{ margin:8px 0; }}
.next {{ background:linear-gradient(135deg,rgba(124,92,255,.10),rgba(74,168,255,.06)); }}
details.card summary {{ cursor:pointer; font-size:15px; margin:0; letter-spacing:.02em;
  text-transform:uppercase; color:var(--muted); font-weight:600; list-style:none; }}
details.card summary::-webkit-details-marker {{ display:none; }}
details.card summary::before {{ content:"▸ "; color:var(--accent); }}
details.card[open] summary::before {{ content:"▾ "; }}
details.card .details-body {{ margin-top:16px; }}
.filelinks {{ display:flex; flex-direction:column; gap:8px; margin-top:4px; }}
.filelinks a {{ color:var(--acct); text-decoration:none; font-weight:600; }}
.filelinks a:hover {{ text-decoration:underline; }}
.filelinks .path {{ color:var(--muted); font-size:12px; margin-left:8px; }}
</style></head><body><div class="wrap">
<header>
  <h1>Prospecting pipeline <span>· {prof}</span></h1>
  <div class="ts">refreshed {html.escape(status["generated_at"])}</div>
</header>

<div class="card next">
  <h2>Your pipeline right now</h2>
  <div class="flow">
    <b>{lc["identified"]:,}</b> companies identified <span class="arrow">→</span>
    <b>{lc["scored"]:,}</b> qualified by fit <span class="arrow">→</span>
    <b>{lc["enriched"]:,}</b> have a contact <span class="arrow">→</span>
    <b style="color:var(--ready)">{f["ready"]:,}</b> verified, ready to send
  </div>
  <p class="note">This is the path every account takes, start to finish — from "we found the
  company" to "we can put a real, deliverable email in front of a rep."</p>
</div>

<div class="card">
  <h2>Accounts &amp; contacts</h2>
  <div class="stats">
    {stat("identified", lc["identified"], f"{a['files']} import batches + prospect runs", "acct")}
    {stat("qualified", lc["scored"], "ranked by ICP fit", "acct")}
    {stat("contact found", lc["enriched"], "name + email found", "ready")}
  </div>
  {
        _bar(
            [
                ("contact found", lc["enriched"], "acct-e"),
                ("qualified, no contact yet", max(0, lc["scored"] - lc["enriched"]), "verify"),
                ("not yet qualified", max(0, lc["identified"] - lc["scored"]), "acct-b"),
            ],
            lc["identified"],
        )
    }
  <div class="legend"><span><i style="background:var(--ready)"></i>contact found</span><span><i style="background:var(--verify)"></i>qualified, no contact yet</span><span><i style="background:var(--acct)"></i>not yet qualified</span></div>

  <h2 style="margin-top:22px">Email quality <span class="muted" style="text-transform:none;font-weight:400">· of the {
        lc["enriched"]:,} accounts with a contact</span></h2>
  <div class="stats">
    {stat("verified", f["ready"], "deliverability proven — ready to send", "ready")}
    {stat("needs a check", f["needs_verification"], "real person, address unproven", "verify")}
    {
        stat(
            "not usable",
            f["blocked"] + f["excluded_dnc_or_sent"],
            f"{f['blocked']} bad address · {f['excluded_dnc_or_sent']} do-not-contact/already emailed",
            "blocked",
        )
    }
  </div>
  {
        _bar(
            [
                ("verified", f["ready"], "ready"),
                ("needs a check", f["needs_verification"], "verify"),
                ("not usable", f["blocked"] + f["excluded_dnc_or_sent"], "blocked"),
            ],
            f["master_total"],
        )
    }
  <p class="note">"Verified" is a <b>deliverability</b> signal (we're confident the mailbox exists)
  — it doesn't by itself mean the account is a good fit or has been researched; that's the
  qualified/contact-found numbers above.
  {
        (
            f"<b>{f['unconsolidated_in_raw']} new rows</b> haven't been folded in yet — run consolidate."
        )
        if f["unconsolidated_in_raw"]
        else ""
    }</p>
</div>

<div class="card">
  <h2>Top-priority accounts <span class="muted" style="text-transform:none;font-weight:400">· Tier A</span></h2>
  <div class="stats">
    {stat("Tier A", lc["tier_a"], "highest fit + intent", "acct")}
    {stat("prep brief ready", lc["dossier"], "1-page account + buyer brief", "ready")}
    {stat("outreach drafted", lc["drafted"], "draft sitting in the account folder", "ready")}
  </div>
  {
        _bar(
            [
                ("prep ready", lc["dossier"], "acct-e"),
                ("still needs a brief", max(0, lc["tier_a"] - lc["dossier"]), "acct-b"),
            ],
            lc["tier_a"],
        )
    }
  <p class="note">Prep brief and outreach-drafted are counted independently, not as a strict
  funnel: outreach has always been drafted for Tier-A accounts regardless of whether a brief
  exists, so "outreach drafted" can be higher than "prep brief ready." Only new Tier-A accounts
  get both together going forward. <b>Loaded/Sent are not tracked per-account yet</b> — the
  sequencer card below shows aggregate totals only; per-contact load/send tracking is a
  separate, larger piece of work (Saleshandy instrumentation), not yet built.</p>
</div>

<div class="card">
  <h2>Browse the underlying files</h2>
  <p class="note" style="margin:0 0 12px">Every number above is read straight off these files —
  open them directly to sample and double-check anything.</p>
  <div class="filelinks">
    <div><a href="{
        html.escape(links["ready_csv"])
    }">Ready-to-send list (ready-to-load.csv)</a><span class="path">{
        html.escape(links["ready_csv"])
    }</span></div>
    <div><a href="{
        html.escape(links["needs_csv"])
    }">Needs-a-check list (needs-verification.csv)</a><span class="path">{
        html.escape(links["needs_csv"])
    }</span></div>
    <div><a href="{
        html.escape(links["accounts_dir"])
    }">All account folders (prep briefs, dossiers, outreach drafts)</a><span class="path">content/{
        prof
    }/accounts/</span></div>
  </div>
  {
        (
            '<p class="note" style="margin-top:14px"><b>Sample a prep brief:</b></p><div class="filelinks">'
            + "".join(
                f'<div><a href="../accounts/{html.escape(s["folder"])}/">{html.escape(s["company"])}</a>'
                f'<span class="path">content/{prof}/accounts/{html.escape(s["folder"])}/</span></div>'
                for s in lc["dossier_samples"]
            )
            + "</div>"
        )
        if lc["dossier_samples"]
        else ""
    }
  {
        (
            '<p class="note" style="margin-top:14px"><b>Sample an outreach draft:</b></p><div class="filelinks">'
            + "".join(
                f'<div><a href="{html.escape(s["href"])}">{html.escape(s["company"])}</a>'
                f'<span class="path">{html.escape(s["repo_path"])}</span></div>'
                for s in lc["draft_samples"]
            )
            + "</div>"
        )
        if lc["draft_samples"]
        else ""
    }
</div>
{seq_block}
<div class="card next">
  <h2>What to do next</h2>
  <ol class="steps">
    <li><b>Send now:</b> {
        f[
            "ready"
        ]:,} contacts are verified and ready — review the paused sequence in Saleshandy, then activate.</li>
    <li><b>Grow the ready list:</b> {
        f[
            "needs_verification"
        ]:,} contacts just need an email check — run a verification batch to push the next 50 through.</li>
    <li><b>Find more contacts:</b> {
        max(
            0, lc["scored"] - lc["enriched"]
        ):,} qualified companies still need a decision-maker found — run the enrichment pass.</li>
    <li><b>Prep top accounts:</b> {
        max(
            0, lc["tier_a"] - lc["dossier"]
        ):,} Tier-A accounts don't have a prep brief yet — the prospect skill can auto-generate one.</li>
  </ol>
</div>

<details class="card">
  <summary>For the ops team — how this pipeline works &amp; what it costs</summary>
  <div class="details-body">
    <h2>How the pipeline works</h2>
    <ol class="steps">
      <li><b>Prospect run</b> → emits a per-run <code>prospects-*-hubspot.csv</code> (people + emails) and/or a bulk <code>imports/*.csv</code> (accounts only).</li>
      <li><b>Consolidate</b> (auto, every run) → dedupes by person, checks DNC + already-sent, gates by deliverability. Writes <code>ready-to-load.csv</code>.</li>
      <li><b>Verify</b> → hold-queue rows go to the sequencer with verify-on-import; survivors re-gate to ready on the next sweep.</li>
      <li><b>Load + send</b> → ready contacts enroll into a <em>paused</em> Saleshandy sequence. A human presses go. Nothing sends automatically.</li>
    </ol>
    <h2 style="margin-top:20px">Cost of enrichment (reference)</h2>
    <table><tbody>{cost_rows}</tbody></table>
    <p class="note">Enrichment is metered and pushes PII to a processor — it's operator-gated by design. See the cost ledger for actuals.</p>
  </div>
</details>

<p class="muted" style="font-size:12px;text-align:center;margin-top:24px">
Generated by <code>gtm_core.prospects_dashboard</code> · numbers read from files on disk, never recomputed.
</p>
</div></body></html>"""


def dashboard_path(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root) / "status.html"


def render_dashboard(profile: str, content_root: Path | None = None) -> Path:
    """Build the status model and write ``prospects/status.html`` + a machine
    ``.pool/status.json``. Returns the HTML path."""
    status = build_status(profile, content_root)
    out = dashboard_path(profile, content_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(status), encoding="utf-8")
    (_pool_dir(profile, content_root)).mkdir(parents=True, exist_ok=True)
    (_pool_dir(profile, content_root) / "status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_dashboard")
    ap.add_argument("--profile", required=True)
    ap.add_argument(
        "--json", action="store_true", help="print the status model instead of writing HTML"
    )
    args = ap.parse_args(argv)
    if args.json:
        json.dump(build_status(args.profile), sys.stdout, indent=2, default=list)
        sys.stdout.write("\n")
        return 0
    path = render_dashboard(args.profile)
    print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
