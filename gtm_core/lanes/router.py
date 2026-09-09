"""Route every pooled row to exactly one lane. Deterministic; the judge advises.

The five lanes and the order of precedence (see ``docs/prds/2026-09-03-email-lanes-and-
judge-feedback.md``):

1. **excluded** — a deterministic reason the row can never be sent this wave (suppressed,
   opt-out, already in a registered list, direct competitor). No decision to make.
2. **hold** — a trigger a human must answer (``gtm_core.lanes.triggers``), unless a prior
   decision or an accepted policy line already answered it for this (trigger, account).
3. **personalised** — researcher ``send`` ∧ fresh clause ∧ judge ``send`` ∧ grounding clean.
4. **repair** — the judge rejected the ARGUMENT (re-angle, or a drop whose defect scope is
   argument/contact/unknown), or the grounding pre-pass flagged the body, or the operator
   said ``salvage``. Capped by ``REPAIR_ATTEMPT_CAP``; over the cap → generic.
5. **generic** — everything else: re-angle or empty research verdict, a stale or missing
   clause, a contested judge verdict, repair exhausted.

Two properties every test pins: the lanes PARTITION the input (each row in exactly one),
and an unknown defect scope never reaches ``hold`` or ``excluded``. Judge verdicts come from
the adjudication JSONL (joined by email, touch 1), never from the CSV's own ``judge_*``
columns, which are one run stale on the live pool.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..adjudication import REPAIR_ATTEMPT_CAP, Adjudication, worst_verdict
from ..adjudication.defects import defect_scope, normalize_defect_class
from ..merge_hygiene import row_signal_freshness
from ..prospects_consolidate.columns import MASTER_COLS
from ..prospects_consolidate.confidence import org_token
from .context import RouterContext
from .model import HOLD_ORDER, LANE_COLUMNS, LANES, Routed
from .triggers import first_exclude, first_hold

#: Lanes a prior decision or policy may route a held row INTO. ``suppress`` is deliberately
#: not a lane: a suppressed account is written to the ledger by ``hold-apply`` and then lands
#: in ``excluded`` through the ledger, so the exclusion is durable rather than remembered.
_DECISION_LANE = {"generic": "generic", "salvage": "repair"}


@dataclass
class RoutingResult:
    routed: list[Routed] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    hold_counts: Counter = field(default_factory=Counter)
    exclude_counts: Counter = field(default_factory=Counter)
    ambiguous: int = 0
    contested: int = 0
    decided: int = 0
    #: Rows that had a judge record on file, and researcher-send rows with a fresh clause
    #: that had NONE — the latter can never reach ``personalised`` however good they are,
    #: so a run where it dominates is "the judge has not scored this list", not "nothing
    #: qualifies".
    judged: int = 0
    unjudged_sendable: int = 0
    notes: list[str] = field(default_factory=list)

    def lane(self, name: str) -> list[Routed]:
        return [r for r in self.routed if r.lane == name]


def judge_index(records: Iterable[Adjudication]) -> dict[str, list[Adjudication]]:
    """email → touch-1 records. Several records per email are normal (one per cell the
    person was drafted into); ``worst_verdict`` collapses them and the router flags the
    join as ambiguous when the bodies differ."""
    out: dict[str, list[Adjudication]] = {}
    for rec in records:
        if rec.unscored or int(rec.touch or 1) != 1:
            continue
        out.setdefault((rec.email or "").strip().lower(), []).append(rec)
    return out


def _attach_judge(
    routed: Routed, recs: Sequence[Adjudication] | None, source: str
) -> Adjudication | None:
    if not recs:
        return None
    rec = worst_verdict(recs)
    routed.judge_verdict = rec.verdict
    routed.judge_defect_class = normalize_defect_class(rec.defect_class)
    routed.judge_scope = defect_scope(rec.defect_class)
    routed.judge_note = rec.note or rec.evidence
    routed.grounding = rec.grounding or ""
    routed.body_hash = rec.body_hash or ""
    routed.source = source
    if len({r.body_hash for r in recs if r.body_hash}) > 1:
        routed.flags.append("ambiguous-judge")
    return rec


def _verdict_lane(
    row: dict, judge: Adjudication | None, ctx: RouterContext, cap: int
) -> tuple[str, str]:
    """The lane a row earns on verdicts alone (no hold/exclude fired)."""
    verdict = (row.get("verdict") or "").strip().lower()
    clause, fresh = row_signal_freshness(row, as_of=ctx.as_of)
    if judge is not None and judge.verdict in ("re-angle", "drop"):
        if judge.repair_attempt is not None and judge.repair_attempt >= cap:
            return "generic", f"repair cap reached ({judge.repair_attempt})"
        return (
            "repair",
            f"judge {judge.verdict} ({normalize_defect_class(judge.defect_class) or 'unclassed'})",
        )
    if judge is not None and (judge.grounding or "").startswith("research="):
        return "repair", f"grounding {judge.grounding}"
    if verdict == "send" and clause and fresh and judge is not None and judge.verdict == "send":
        return "personalised", "researcher send · fresh clause · judge send"
    if verdict == "send" and clause and fresh:
        return "generic", "researcher send but NO judge verdict on file"
    if verdict == "send":
        return "generic", "stale or unusable clause" if clause else "no signal clause"
    return "generic", f"research verdict {verdict or '(empty)'}"


def _apply_decision(
    routed: Routed, trigger: str, detail: str, ctx: RouterContext, decisions: dict
) -> bool:
    """A recorded decision or a policy line for (trigger, account) answers the hold."""
    key = (trigger, account_key(routed.row))
    rec = decisions.get(key)
    if rec and rec.get("detail", detail) == detail and rec.get("decision") in _DECISION_LANE:
        routed.lane = _DECISION_LANE[rec["decision"]]
        routed.decided = f"decided:{rec['decision']}:{trigger}"
        return True
    if rec and rec.get("decision") == "suppress":
        routed.lane = "excluded"
        routed.decided = f"decided:suppress:{trigger}"
        return True
    choice = ctx.policy_auto.get(trigger)
    if choice:
        routed.lane = _DECISION_LANE[choice]
        routed.decided = f"policy:{choice}:{trigger}"
        return True
    return False


def account_key(row: dict) -> str:
    """The decisions ledger's account identity: stamped id, else company domain, else name.
    Same precedence as ``eval_writeback`` — never the email's own domain."""
    acct = (row.get("account_id") or "").strip().lower()
    if acct:
        return f"a:{acct}"
    dom = (row.get("company_domain") or "").strip().lower()
    if dom:
        return f"d:{dom}"
    return f"c:{(row.get('company') or '').strip().lower()}"


def route_row(
    row: dict,
    ctx: RouterContext,
    recs: Sequence[Adjudication] | None,
    *,
    source: str = "",
    cap: int = REPAIR_ATTEMPT_CAP,
    decisions: dict | None = None,
    previous: dict | None = None,
) -> Routed:
    routed = Routed(row=row, lane="generic")
    judge = _attach_judge(routed, recs, source)
    hit = first_exclude(row, ctx)
    if hit:
        routed.lane, (routed.trigger, routed.detail) = "excluded", hit
        return routed
    hit = first_hold(row, ctx, judge)
    if hit:
        routed.trigger, routed.detail = hit
        if not _apply_decision(routed, hit[0], hit[1], ctx, decisions or {}):
            routed.lane = "hold"
        return routed
    routed.lane, routed.detail = _verdict_lane(row, judge, ctx, cap)
    _apply_stickiness(routed, previous or {})
    return routed


def _apply_stickiness(routed: Routed, previous: dict) -> None:
    """A judge that changed its mind on the SAME body does not promote a row: a lane that
    was repair/generic last run and would be personalised now on identical bytes is a
    contested verdict, and contested routes to generic — never to personalised."""
    prev = previous.get(routed.email)
    if not prev or not routed.body_hash or prev.get("body_hash") != routed.body_hash:
        return
    if prev.get("judge_verdict") and prev["judge_verdict"] != routed.judge_verdict:
        routed.flags.append("contested")
        if routed.lane == "personalised":
            routed.lane, routed.detail = "generic", "contested judge verdict on an unchanged body"


def _second_pass(result: RoutingResult, decisions: dict, ctx: RouterContext) -> None:
    """Holds that depend on the provisional lane or on the batch as a whole."""
    seen_accounts: dict[str, str] = {}
    for r in result.routed:
        if r.lane in ("hold", "excluded"):
            continue
        tok = org_token(r.row.get("company_domain", ""), r.row.get("company", "")) or r.email
        if r.lane == "generic" and (r.row.get("tier") or "").strip().upper() == "A":
            _hold_or_decide(r, "tier-a-generic", "tier A on the generic email", ctx, decisions)
        if r.lane in ("hold", "excluded"):
            continue
        if tok in seen_accounts:
            _hold_or_decide(
                r,
                "duplicate-contact",
                f"another contact at this account is already in the {seen_accounts[tok]} lane",
                ctx,
                decisions,
            )
        else:
            seen_accounts[tok] = r.lane


def _hold_or_decide(
    r: Routed, trigger: str, detail: str, ctx: RouterContext, decisions: dict
) -> None:
    r.trigger, r.detail = trigger, detail
    if not _apply_decision(r, trigger, detail, ctx, decisions):
        r.lane = "hold"


def route(
    rows: Sequence[dict],
    records: Iterable[Adjudication],
    ctx: RouterContext,
    *,
    source: str = "",
    cap: int = REPAIR_ATTEMPT_CAP,
    decisions: dict | None = None,
    previous: dict | None = None,
) -> RoutingResult:
    index = judge_index(records)
    result = RoutingResult(notes=list(ctx.notes))
    ordered = sorted(rows, key=lambda r: (-_score(r), (r.get("email") or "").lower()))
    for row in ordered:
        result.routed.append(
            route_row(
                row,
                ctx,
                index.get((row.get("email") or "").strip().lower()),
                source=source,
                cap=cap,
                decisions=decisions,
                previous=previous,
            )
        )
    _second_pass(result, decisions or {}, ctx)
    for r in result.routed:
        result.counts[r.lane] += 1
        if r.lane == "hold":
            result.hold_counts[r.trigger] += 1
        if r.lane == "excluded":
            result.exclude_counts[r.trigger] += 1
        result.ambiguous += "ambiguous-judge" in r.flags
        result.contested += "contested" in r.flags
        result.decided += bool(r.decided)
        result.judged += bool(r.judge_verdict)
        result.unjudged_sendable += "NO judge verdict" in r.detail
    assert sum(result.counts.values()) == len(rows), "lanes must partition the input"
    if rows and result.judged == 0:
        result.notes.append(
            "the judge records cover NONE of these rows — personalised needs a judge send, so run "
            "the judge on this list's send rows before reading these lanes as final"
        )
    elif result.unjudged_sendable:
        result.notes.append(
            f"{result.unjudged_sendable} researcher-send row(s) with a fresh clause have no judge "
            f"record and fell to generic — judge them to give personalised a chance"
        )
    return result


def _score(row: dict) -> float:
    try:
        return float(row.get("score") or 0)
    except ValueError:
        return 0.0


def write_lanes(result: RoutingResult, seq_dir: Path, stamp: str) -> dict[str, Path]:
    """One CSV per lane. ``signal_clause`` only where a personalised body will read it."""
    seq_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for lane in LANES:
        rows = result.lane(lane)
        cols = [*MASTER_COLS, *LANE_COLUMNS]
        if lane in ("personalised", "repair"):
            cols.append("signal_clause")
        path = seq_dir / f"ready-to-load-{lane}-{stamp}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                row = {
                    **r.row,
                    "lane": lane,
                    "lane_reason": r.reason,
                    "judge_defect_class": r.judge_defect_class,
                }
                if "signal_clause" in cols and not row.get("signal_clause"):
                    row["signal_clause"] = row_signal_freshness(r.row, as_of=None)[0]
                w.writerow({c: (row.get(c) or "") for c in cols})
        out[lane] = path
    return out


def summary(result: RoutingResult) -> str:
    total = sum(result.counts.values())
    lines = [
        f"{total} row(s) → " + " · ".join(f"{lane} {result.counts.get(lane, 0)}" for lane in LANES)
    ]
    if total:
        share = result.counts.get("personalised", 0) / total
        lines.append(
            f"personalised share {share:.0%} · judge records on file for {result.judged}/{total} row(s)"
        )
    if result.hold_counts:
        lines.append("hold by reason (risk order):")
        for trigger in HOLD_ORDER:
            if result.hold_counts.get(trigger):
                lines.append(f"  {result.hold_counts[trigger]:4}  {trigger}")
    if result.exclude_counts:
        lines.append(
            "excluded by reason: "
            + ", ".join(f"{k} {v}" for k, v in result.exclude_counts.most_common())
        )
    if result.decided:
        lines.append(f"{result.decided} row(s) answered by a prior decision or policy")
    if result.ambiguous:
        lines.append(
            f"{result.ambiguous} row(s) had judge records for more than one body (worst verdict used)"
        )
    if result.contested:
        lines.append(f"{result.contested} row(s) contested (judge changed on an unchanged body)")
    for note in result.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)
