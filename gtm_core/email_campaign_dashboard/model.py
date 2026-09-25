from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ..campaigns_dashboard import build_campaigns
from ..cells import build_cells, intent_profile, supply_profile
from ..outcomes import read_outcomes
from ..paths import resolve_content_root
from ..prospect_lede import compose_lede
from ..prospect_paths import evals_dir
from ..prospect_readiness import load_readiness
from ..prospect_status import (
    STATUSES,
    UnmappedStatus,
    compute_attrition_receipt,
    needs_address,
    status_of,
)
from ..prospect_status_receipt import cross_check
from ..prospects_consolidate import _pool_dir, _prospects_dir
from ..prospects_dashboard import build_status
from ..prospects_state import load_latest
from .aggregate import _scope_figures
from .format import _rate_of
from .health import capability_rows_for, list_rows, page_extras, page_go_live
from .lane_state import LaneStateUnreadable as LaneStateUnreadable
from .lane_state import _read_lane_state
from .loadfiles import load_files
from .sources import (  # noqa: F401  (re-exported: model is the package's assembly point)
    packs_model,
    roster_model,
    roster_sources,
    samples_model,
    seat_fit,
)

#: The five statuses `status_of` derives from a routed row's (lane, reason). `needs_address`
#: is the sixth `STATUSES` id but comes from `latest.json`, not from a routed row — see
#: `prospect_status_model`, which gives it its own key rather than folding it into this tuple.
_LANE_STATUSES: tuple[str, ...] = tuple(s for s in STATUSES if s != "needs_address")


def prospect_status_model(profile: str, content_root: Path | None = None) -> dict:
    """PS14's population: the six-way operator status, derived the one place it is derived
    (`gtm_core.prospect_status`) — never re-implemented here, so the page and `prospects
    status` cannot disagree about what one of these words means.

    A `(lane, reason)` pair `status_of` has never seen is a real gap in the mapping (see
    that module's docstring), and `prospects status` answers for nothing else on a run, so
    it can afford to raise loudly on one. A dashboard render answers for the WHOLE page —
    one such row must not blank every tile beside it, so it is counted separately
    (`unmapped`) rather than raising. Both surfaces read the same file through the same
    function; only the failure mode differs, and this is where that divergence is decided
    and recorded.

    `needs_address` is a different, larger population (the account ledger, not the current
    routed list) and is never summed into `total` — see `gtm_core.prospect_status.
    needs_address`'s own docstring.
    """
    records = _read_lane_state(profile, content_root)
    counts: dict[str, int] = dict.fromkeys(_LANE_STATUSES, 0)
    by_email: dict[str, str] = {}
    unmapped = 0
    for rec in records:
        reason = rec.get("reason") or rec.get("trigger") or ""
        email = (rec.get("email") or "").strip().lower()
        try:
            status = status_of(rec.get("lane") or "", reason)
        except UnmappedStatus:
            unmapped += 1
            if email:
                by_email[email] = "unmapped"
            continue
        counts[status] += 1
        if email:
            by_email[email] = status
    items = load_latest(profile, content_root).get("items", [])
    return {
        # Whether `lanes route` has ever produced output for this profile — the tiles
        # render "—" rather than a misleading zero when this is False.
        "available": bool(records),
        "counts": counts,
        "total": sum(counts.values()),
        "unmapped": unmapped,
        "needs_address": sum(1 for item in items if needs_address(item)),
        # `email -> status`, for the worklist and who-tab tables' Status column. A miss is
        # ordinary (a roster row and a router row are different populations) and is handled
        # by the reader, not by this dict.
        "by_email": by_email,
    }


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


def inbound_health(profile: str, content_root: Path | None = None) -> dict:
    """The inbound lane's own answer to "is anything watching, and does it agree?".

    Three facts, all from `history.jsonl`, none of them a provider call:

    * the newest `capability_asserted` — was this sequencer's contract checked, and when;
    * the newest `dnc_reconciled` / `dnc_sync_skipped` / `dnc_sync_refused` — is the
      suppression mirror current, and does the ledger agree with the provider;
    * how many replies the sweep could not read (`optout_unreadable`), which is a coverage
      gap in OUR matcher rather than anything the sender did wrong.

    The point of surfacing these is that a sweep or a sync that quietly stopped looks
    identical to one that is working — the same failure `prospecting_runs` exists to catch.
    """
    from gtm_core.prospects_import import _ledgers

    latest: dict = {"capability": None, "dnc": None, "unreadable": 0, "unreadable_recent": []}
    for ev in _ledgers(profile, content_root).iter_history():
        event = ev.get("event")
        if event == "capability_asserted":
            latest["capability"] = ev
        elif event in ("dnc_reconciled", "dnc_sync_skipped", "dnc_sync_refused"):
            latest["dnc"] = ev
        elif event == "optout_unreadable":
            latest["unreadable"] += 1
            latest["unreadable_recent"].append(
                {"email": ev.get("email", ""), "ts": (ev.get("ts") or "")[:10]}
            )
    latest["unreadable_recent"] = latest["unreadable_recent"][-5:]
    return dict(latest, capability_rows=capability_rows_for(latest["capability"]))


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
    """Subject, day and the argument line for each email in one spec.

    Reads the spec with ``merge_render_linter.parse_spec`` — the same parser
    :mod:`~gtm_core.email_campaign_dashboard.sources` and the merge-render gate use — rather
    than a second regex of its own. The second regex is not hypothetical: this function used
    to carry one that required a blank line between the ``**Step N — Day D**`` header and its
    blockquote, and every spec on disk writes them adjacent. It therefore returned NOTHING for
    every campaign, silently, and the one caller that divides by it —
    :func:`~gtm_core.email_campaign_dashboard.views_status._forecast_block` — reported a
    two-touch day-0/day-5 sequence as "2 emails over 0 days … 1 working day", finishing the
    day it starts. A parser that returns an empty list looks exactly like a campaign with no
    copy, which is why nothing caught it.
    """
    if not spec_path.is_file():
        return []
    from ..hook_coverage.config import parse_spec

    out = []
    for tch in parse_spec(spec_path.read_text(encoding="utf-8")):
        body = [ln.strip() for ln in tch.body.splitlines() if ln.strip()]
        out.append(
            {
                "step": tch.number,
                "day": tch.day,
                "subject": tch.subject or "",
                "opener": next((ln for ln in body[1:] if ln != "{{Why Now}}."), ""),
                "words": sum(len(ln.split()) for ln in body),
            }
        )
    return out


def scope_to_campaign(m: dict, campaign: str) -> dict:
    """Narrow a profile-wide model to ONE campaign.

    Without this the page answers a question nobody asked. On 2026-09-04 the sg-builders run
    had exactly one staged sequence and six 1:1 packs, and the page's headline tiles read
    "0 of 990 emails / 51 people / 3 sequences" — every one of those numbers belonging to a
    DIFFERENT campaign (`agent-gateway-cross-org`), because the tiles roll up the profile.

    Only what is genuinely per-campaign is filtered: the sequences, their copy, and the packs.
    The pool-wide sections (supply, intent, market, the consolidation funnel) are NOT scoped —
    a prospect pool is shared across campaigns, and silently relabelling it as this campaign's
    would trade one wrong number for another. ``m["campaign_scope"]`` tells the views to say so.
    """
    # `campaign` is one slug or several (comma-separated). Several is not a special mode: a
    # programme is a set of campaigns, and the honest page for it is the union of their
    # sequences and rosters — never the profile rollup, which also sweeps in campaigns nobody
    # asked about.
    slugs = [s.strip() for s in str(campaign).split(",") if s.strip()]
    wanted = [c for c in m["campaigns"].get("campaigns", []) if c.get("slug") in slugs]
    if not wanted or len(wanted) != len(set(slugs)):
        return m
    ids: set[str] = set()
    for c in wanted:
        ids |= {s["sequence_id"] for s in c.get("sequences", [])}
        ids |= {s["sequence_id"] for s in c.get("archived", [])}

    seqs = [s for s in m["status"].get("sequences", []) if s.get("id") in ids]
    # A sequence the LEDGER knows but the live snapshot does not would otherwise vanish, and
    # the tiles would read "0 sequences set up" for a campaign that has one staged with people
    # in it. That is how this page reported 0 of 0 for sg-builders on 2026-09-04: the snapshot
    # predated the 06:21 staging, so filtering it to this campaign filtered it to nothing.
    # Only what the ledger actually asserts is carried over — enrolment and state. The send
    # metrics stay 0, which is true and is what the rest of the page says.
    known = {s.get("id") for s in seqs}
    ledger_rows = [
        x for c in wanted for x in list(c.get("sequences", [])) + list(c.get("archived", []))
    ]
    for led in ledger_rows:
        sid = led["sequence_id"]
        if sid in known:
            continue
        seqs.append(
            {
                "id": sid,
                "name": Path(led.get("spec") or "").stem or sid,
                # Unknown here, not the ledger's: the live snapshot never named this row, and
                # the ledger's own status is "paused" by construction (PS20).
                "status": "",
                "loaded": int(led.get("enrolled") or 0),
                "sent": 0,
                "pending": int(led.get("enrolled") or 0),
                "delivered": 0,
                "opened": 0,
                "replied": 0,
                "bounced": 0,
                "interested": 0,
                "meetings": 0,
                "from_ledger": True,
            }
        )
    m["status"] = dict(m["status"], sequences=seqs, sequence=(seqs[0] if seqs else {}))
    m["campaigns"] = dict(m["campaigns"], campaigns=wanted, unlinked_sequences=[])
    m["messages"] = [x for x in m.get("messages", []) if x.get("sequence_id") in ids]

    # Packs carry no sequence id; they are tied to the campaign by its date suffix, which is
    # the same key `hook_coverage.campaign_packs` joins on.
    # Packs carry no sequence id; a campaign claims them by its date suffix. A slug WITHOUT
    # one can claim none — leaving the full list in place would have let `agent-gateway-cross-org`
    # display six packs written for a different campaign.
    dates = {mm.group(1) for s in slugs if (mm := re.search(r"(\d{8})$", s))}
    if True:
        pm = m.get("packs") or {}
        rows = [r for r in pm.get("packs", []) if r["date"].replace("-", "") in dates]
        spread: dict[str, int] = {}
        for r in rows:
            if r["capability"]:
                spread[r["capability"]] = spread.get(r["capability"], 0) + 1
        # Packs in the capability era but belonging to ANOTHER campaign are dropped by the
        # date filter. Counted here rather than silently vanishing — a pack that exists and
        # appears in no total is how a lane goes missing in the first place.
        m["packs"] = dict(
            pm,
            packs=rows,
            spread=sorted(spread.items(), key=lambda kv: -kv[1]),
            undeclared=sum(1 for r in rows if not r["capability"]),
            other_campaigns=len(pm.get("packs", [])) - len(rows),
        )
    m["roster"] = roster_model(m["profile"], roster_sources(wanted), m.get("_content_root"))
    samples: dict = {"packs": [], "touches": [], "rendered": []}
    for s in slugs:
        one = samples_model(m["profile"], s, m.get("_content_root"))
        samples["packs"] += one["packs"]
        samples["touches"] += one["touches"]
        samples["rendered"] += one.get("rendered", [])
    m["samples"] = samples
    # Computed after both roster and samples, because it joins them: the addresses come from
    # the rendered bodies, the titles from the roster, and the declared persona from the spec.
    m["seat_fit"] = seat_fit(m, m["profile"], m.get("_content_root"))
    m["campaign_scope"] = campaign
    # How the views name this scope in prose. Written here so `campaign_scope` keeps its
    # exact meaning (the slug list, which `render` compares for identity) and the wording
    # is a separate, optional key — a caller reaching `scope_to_campaign` directly, as the
    # tests do, still gets the old singular phrasing rather than an empty sentence.
    m.setdefault(
        "scope_label", "this campaign" if len(slugs) == 1 else f"these {len(slugs)} campaigns"
    )
    return m


def build_model(profile: str, content_root: Path | None = None) -> dict:
    """Everything one page renders, read once — the views read this dict, never the disk.

    Profile-wide; ``scope_to_campaign`` narrows a copy of it to named campaigns. Four keys —
    ``prospect_status``, ``attrition_receipt``, ``load_files``, ``go_live_status`` — are the
    operator's answers ("who is waiting on me, what do I load, is anything live") and are all
    derived from the same routed state and ledger that ``prospects status`` prints from.
    """
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
            "list_rows": list_rows(seq_dir, src["csv"]),
        }
        for src in cellmodel["sources"]
    ]

    # ONE SOURCE PER CLAIM (UX-02 / PSK-029). The accounts card is the routed state joined to
    # the ledger — the same call, with the same arguments, `prospects status` makes — so the
    # page and the terminal cannot disagree about "Held" or "Ready". Until 2026-09-21 this
    # passed no routed state, so every account was classified from its ledger row's own
    # wording and the page read "Held 0" under a terminal block reading "Held 3".
    # `routed=None` only when `lanes-state.jsonl` does not exist at all (never sorted): the
    # receipt then falls back to the ledger's wording, and the card says that it did.
    from ..prospect_status_cli import (  # deferred: it imports the lanes package
        _routed_contacts,
        _sheet_name,
    )

    lane_records = _read_lane_state(profile, content_root)
    sorted_once = (evals_dir(profile, content_root) / "lanes-state.jsonl").is_file()
    ledger_items = [
        it for it in load_latest(profile, content_root).get("items", []) if isinstance(it, dict)
    ]
    routed = _routed_contacts(lane_records) if sorted_once else None
    receipt = compute_attrition_receipt(ledger_items, routed)
    attrition_receipt = receipt.to_dict()
    # PS15: the discrepancy lines, with the same call and arguments `prospects status` makes.
    # The receipt object carries what `cross_check` needs; the dict above does not.
    contact_counts = Counter(c["status"] for c in routed or [])
    problems = cross_check(receipt, dict(contact_counts), len(lane_records)) if routed else []
    readiness = load_readiness(profile, content_root)
    prospect_status = prospect_status_model(profile, content_root)

    # GO-LIVE IS EVIDENCE, NEVER A DEFAULT (UX-05) — `health.page_go_live` (PS20 T1.10).
    figures = _scope_figures({"campaigns": campaigns, "status": status})
    go_live_status = page_go_live(campaigns, status, figures["contacted"][0])
    now = datetime.now(UTC)
    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
    # Computed once for `reconciliation`/`warnings` below — skipped when unreadable, so an
    # empty snapshot reads as unreadable rather than "every sequence vanished".
    reconciliation = {"ok": True, "in_ledger_only": [], "in_snapshot_only": []}
    if not status["snapshot"]["unreadable"]:
        reconciliation = reconcile_snapshot(campaigns, status)
    return {
        "profile": profile,
        "generated_at": generated_at,
        "status": status,
        "campaigns": campaigns,
        # The rollup gets a roster too, built from every campaign that declares one. Before
        # 2026-09-10 only `scope_to_campaign` set this, so the profile page's worklist read
        # "this scope has no campaign roster" — true, and fixable. Campaigns declaring no
        # `roster_globs` are dropped by `roster_sources` and NAMED by `format.roster_partial`,
        # never silently folded in.
        "roster": roster_model(profile, roster_sources(campaigns.get("campaigns")), content_root),
        "cells": cellmodel,
        "supply": supply,
        "intent": intent,
        "messages": messages,
        "outcome_rows": len(rows),
        "market": market_split(profile, content_root),
        "packs": packs_model(profile, content_root),
        # Carried so `scope_to_campaign` can read the campaign's roster from the SAME tree
        # this model was built from — a backend run is pinned to a workspace root, and
        # re-deriving it from the profile name alone would cross that boundary.
        "_content_root": content_root,
        "runs": prospecting_runs(profile, content_root),
        # Profile-wide, like `runs`: the inbound lane is not scoped to one campaign.
        "inbound": inbound_health(profile, content_root),
        "reconciliation": reconciliation,
        **page_extras(profile, content_root, status, reconciliation, figures["sum_ok"], now),
        # Profile-wide, like `market`/`supply`/`intent` above — the router's last route
        # is not scoped to one campaign, so `scope_to_campaign` leaves this key untouched.
        "prospect_status": prospect_status,
        "attrition_receipt": attrition_receipt,
        # What to load, one file per sending list — see `loadfiles` for the rule (UX-04).
        "load_files": load_files(
            profile, content_root, lane_records, waiting=prospect_status["counts"]["waiting_on_you"]
        ),
        "go_live_status": go_live_status,
        # PS15. `lede` is `compose_lede`'s output — the SAME function the terminal prints —
        # with the one input only this page can observe: the sequencer's go-live state.
        "readiness": readiness,
        "cross_check": problems,
        "lede": compose_lede(
            readiness,
            counts=dict(contact_counts),
            buckets=attrition_receipt,
            sheet=_sheet_name(profile, content_root),
            now=generated_at,
            go_live=go_live_status,
        ),
    }
