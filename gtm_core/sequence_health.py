"""A stop-line watcher for live sequences: it reads the sequencer's numbers and says when to stop.

On 2026-09-29 a stats check reported "12 bounces and 4 unsubscribes" and nobody compared them with
a line. Two days later step 2 went to 156 people and 45 of them unsubscribed within hours. The
step-1 figure that day (4 of 232) was fine; the step-2 figure was not, and only a *per-step* rate
could have shown it, ahead of the send.

**It only recommends.** Output is plain-words lines ("Pause <title>: 21 of 49 people unsubscribed
after step 2 (43%); the line is 5%"). This module makes no call to the sequencer and contains no
pause, resume or activate verb: sending, pausing and resuming stay with a person
(``tests/unit/test_sequence_health.py`` scans the source for exactly that, §R13). It is MCP-free,
like :mod:`gtm_core.email_compliance`; the caller pipes the replies in as JSON files.

    python -m gtm_core.sequence_health --profile P --stats-dir DIR \\
        [--consolidated FILE ...] [--sequences list.json] \\
        [--mailbox-daily-cap N] [--mailbox-daily-volume N] [--json]

Inputs: one ``get_sequence_stats`` reply per sequence (``--stats-dir``); optionally the pages of a
``get_consolidated_stats`` reply (``--consolidated``), which gives per-step counts; optionally a
``list_sequences`` reply (``--sequences``), so a sequence that is already off reads "already paused"
instead of asking for a pause that has happened.

**Stop lines** come from ``content/<profile>/settings.json`` and default as below. A value present
but not a fraction between 0 and 1 stops the run (exit 3): a typo that quietly fell back to the
default could loosen the line it meant to tighten.

======================  =======  ============================================================
settings key            default  breach when ... (strictly over the line)
======================  =======  ============================================================
``unsubscribe_stop_line``  0.05  unsubscribed / contacted, per sequence and per step
``bounce_stop_line``       0.05  bounced / sent, per sequence and per step
``block_bounce_stop_line`` 0.02  blocked-by-the-receiving-server / sent, per sequence only
======================  =======  ============================================================

A rate line applies only once at least :data:`MIN_SAMPLE` (20) people or emails exist at the level
it measures; below that one unsubscribe is 5% of nothing. **The early warning has no such floor:**
:data:`EARLY_COUNT` (3) or more unsubscribes among a step's first :data:`EARLY_WINDOW` (20) sent
breaches however few have gone, because three in the first twenty is the pattern that preceded the
45, and it is visible before the rest are sent. Block-bounce is per sequence only because a
consolidated row says ``Bounced: Yes/No`` and carries no bounce *type*.

``mailbox_daily_cap`` / ``mailbox_daily_volume`` (settings keys or flags; a flag wins): when both
are known, a mailbox that sent more than the cap in one day is a breach. The volume is the peak
sent by one mailbox in one day, counted in the day the row's own timestamp is written in (the
account's day), taken from the consolidated rows unless given. A cap with no volume available is
noted, never read as under the cap.

Exit **0** no breach, **2** any breach on a sequence that is not already paused, **3** an input is
unreadable or lacks a field a rate divides by.

**Scheduled use** (nothing here creates a schedule): a daily run fetches ``get_sequence_stats`` for
each live sequence into one directory, pages ``get_consolidated_stats`` for the last day or two
until ``hasMore`` is false, fetches ``list_sequences``, then calls this command and reads its exit
code; on 2 it surfaces the lines to the operator, who decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.sequencer_stats_read import (
    PayloadError,
    SentEmail,
    StatsFigures,
    consolidated_emails,
    list_rows,
    load_stats_dir,
    read_json,
    shown,
)

DEFAULT_LINES = {
    "unsubscribe_stop_line": 0.05,
    "bounce_stop_line": 0.05,
    "block_bounce_stop_line": 0.02,
}
MIN_SAMPLE = 20
EARLY_COUNT = 3
EARLY_WINDOW = 20


def _pct(part: int, whole: int) -> str:
    rate = 100 * part / whole
    return f"{rate:.1f}%" if rate < 10 else f"{rate:.0f}%"


def _line(value: float) -> str:
    return f"{value * 100:g}%"


@dataclass(frozen=True)
class Breach:
    code: str
    sequence_id: str
    title: str
    detail: str
    already_paused: bool
    step: int | None = None

    @property
    def text(self) -> str:
        lead = "Already paused" if self.already_paused else "Pause"
        return f"{lead} {shown(self.title, 80)}: {self.detail}"


@dataclass
class StepFigures:
    step: int
    sent: int = 0
    unsubscribed: int = 0
    bounced: int = 0
    replied: int = 0
    early_unsubscribed: int = 0
    early_sent: int = 0


@dataclass
class SeqReport:
    sequence_id: str
    title: str
    already_paused: bool = False
    stats: StatsFigures | None = None
    steps: list[StepFigures] = field(default_factory=list)


# ── settings ────────────────────────────────────────────────────────────────────────────────


def _fraction(value, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
        raise PayloadError(
            f"settings.json {key} is {shown(value)}; a stop line is a fraction above 0 and at "
            "most 1 (0.05 means 5%)"
        )
    return float(value)


def _whole(value, key: str, *, floor: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < floor:
        raise PayloadError(f"{key} is {shown(value)}; it must be a whole number of {floor} or more")
    return value


def load_settings(
    profile: str, content_root: Path
) -> tuple[dict[str, float], int | None, int | None]:
    """``(stop lines, mailbox_daily_cap, mailbox_daily_volume)`` from ``settings.json``; a missing file is the defaults."""
    path = content_root / profile / "settings.json"
    doc: dict = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PayloadError(
                f"settings.json is not readable JSON ({type(exc).__name__})"
            ) from exc
        if not isinstance(doc, dict):
            raise PayloadError("settings.json is not a JSON object")
    lines = {k: _fraction(doc[k], k) if k in doc else d for k, d in DEFAULT_LINES.items()}
    cap = (
        _whole(doc["mailbox_daily_cap"], "mailbox_daily_cap", floor=1)
        if "mailbox_daily_cap" in doc
        else None
    )
    vol = (
        _whole(doc["mailbox_daily_volume"], "mailbox_daily_volume", floor=0)
        if "mailbox_daily_volume" in doc
        else None
    )
    return lines, cap, vol


# ── figures ─────────────────────────────────────────────────────────────────────────────────


def step_figures(emails: list[SentEmail]) -> dict[str, list[StepFigures]]:
    """Per sequence, per step: sent, unsubscribed, bounced, replied, and the first-20 window."""
    grouped: dict[tuple[str, int], list[SentEmail]] = defaultdict(list)
    for e in emails:
        grouped[(e.sequence_id, e.step)].append(e)
    out: dict[str, list[StepFigures]] = defaultdict(list)
    for (sid, step), group in sorted(grouped.items()):
        group.sort(key=lambda e: (e.sent_at, e.recipient))  # the first sent, not the first listed
        window = group[:EARLY_WINDOW]
        out[sid].append(
            StepFigures(
                step=step,
                sent=len(group),
                unsubscribed=sum(e.unsubscribed for e in group),
                bounced=sum(e.bounced for e in group),
                replied=sum(e.replied for e in group),
                early_unsubscribed=sum(e.unsubscribed for e in window),
                early_sent=len(window),
            )
        )
    return out


def _mailbox_peak(emails: list[SentEmail]) -> tuple[int, str, str] | None:
    """``(most sent by one mailbox in one day, mailbox, day)`` — the day as the row writes it."""
    per_day: dict[tuple[str, str], int] = defaultdict(int)
    for e in emails:
        if e.sender:
            per_day[(e.sender, e.sent_at.date().isoformat())] += 1
    if not per_day:
        return None
    (mailbox, day), n = max(per_day.items(), key=lambda kv: (kv[1], kv[0]))
    return n, mailbox, day


# ── the stop lines ──────────────────────────────────────────────────────────────────────────


def _over(part: int, whole: int, line: float) -> bool:
    return whole >= MIN_SAMPLE and part / whole > line


def _rate_breaches(rep: SeqReport, lines: dict[str, float], rows, step: int | None = None):
    """The rows are ``(code, part, whole, settings key, sentence)``; a breach is strictly over the line."""
    out = []
    for code, part, whole, key, sentence in rows:
        if _over(part, whole, lines[key]):
            detail = sentence.format(
                n=part, d=whole, p=_pct(part, whole), l=_line(lines[key]), k=step
            )
            out.append(Breach(code, rep.sequence_id, rep.title, detail, rep.already_paused, step))
    return out


def sequence_breaches(rep: SeqReport, lines: dict[str, float]) -> list[Breach]:
    fig = rep.stats
    if fig is None:
        return []
    return _rate_breaches(
        rep,
        lines,
        [
            ("unsubscribe-rate", fig.unsubscribed, fig.contacted, "unsubscribe_stop_line",
             "{n} of {d} people unsubscribed so far ({p}); the line is {l}"),
            ("bounce-rate", fig.bounced, fig.sent, "bounce_stop_line",
             "{n} of {d} emails bounced ({p}); the line is {l}"),
            ("block-bounce-rate", fig.block_bounced, fig.sent, "block_bounce_stop_line",
             "{n} of {d} emails were refused outright by the receiving server ({p}); "
             "the line is {l}"),
        ],
    )  # fmt: skip


def step_breaches(rep: SeqReport, lines: dict[str, float]) -> list[Breach]:
    out = []
    for st in rep.steps:
        out += _rate_breaches(
            rep,
            lines,
            [
                ("step-unsubscribe-rate", st.unsubscribed, st.sent, "unsubscribe_stop_line",
                 "{n} of {d} people unsubscribed after step {k} ({p}); the line is {l}"),
                ("step-bounce-rate", st.bounced, st.sent, "bounce_stop_line",
                 "{n} of {d} emails bounced at step {k} ({p}); the line is {l}"),
            ],
            step=st.step,
        )  # fmt: skip
        if st.early_unsubscribed >= EARLY_COUNT:
            detail = (
                f"{st.early_unsubscribed} of the first {st.early_sent} people emailed at step "
                f"{st.step} unsubscribed; {EARLY_COUNT} or more is the early warning"
            )
            out.append(
                Breach(
                    "step-early-warning", rep.sequence_id, rep.title, detail,
                    rep.already_paused, st.step,
                )
            )  # fmt: skip
    return out


def mailbox_breach(cap: int | None, volume) -> tuple[list[str], list[str]]:
    """``(breach lines, notes)`` for the daily cap. ``volume`` is ``(n, mailbox, day)`` or ``None``."""
    if cap is None:
        return [], []
    if volume is None:
        return [], [f"mailbox_daily_cap is {cap} but no mailbox volume was available to compare"]
    n, mailbox, day = volume
    if n <= cap:
        return [], []
    who = (
        f"mailbox {shown(mailbox, 80)} sent {n} emails on {day}"
        if mailbox
        else f"a mailbox sent {n} emails in a day"
    )
    return [f"Slow down: {who}; its daily cap is {cap}"], []


# ── assemble ────────────────────────────────────────────────────────────────────────────────


def build_reports(
    stats: dict[str, StatsFigures],
    steps: dict[str, list[StepFigures]],
    live: dict[str, tuple[str, bool]],
) -> list[SeqReport]:
    """One report per sequence in either input; title and on/off state from the list when given."""
    reports = []
    for sid in sorted(set(stats) | set(steps)):
        title, active = live.get(sid, ("", True))  # not in the list given: treated as live
        fig = stats.get(sid)
        name = title or (fig.name if fig else "") or sid
        reports.append(SeqReport(sid, name, not active, fig, steps.get(sid, [])))
    return reports


def _report_dict(rep: SeqReport) -> dict:
    fig = rep.stats
    pick = ("contacted", "unsubscribed", "sent", "bounced", "block_bounced", "replied")
    return {
        "sequence_id": rep.sequence_id,
        "title": rep.title,
        "already_paused": rep.already_paused,
        **{k: getattr(fig, k) if fig else None for k in pick},
        "steps": [vars(s) for s in rep.steps],
    }


def check(reports: list[SeqReport], lines: dict[str, float], *, cap: int | None, volume) -> dict:
    breaches: list[Breach] = []
    for rep in reports:
        breaches += sequence_breaches(rep, lines) + step_breaches(rep, lines)
    breaches.sort(key=lambda b: b.already_paused)  # the ones needing action first (stable sort)
    mailbox_lines, notes = mailbox_breach(cap, volume)
    blind = [r for r in reports if r.stats is not None and r.stats.sent and not r.steps]
    if (
        blind
    ):  # one line, not one per sequence: a sequence that has sent nothing has no steps to read
        names = ", ".join(shown(r.title, 40) for r in blind[:3]) + (
            ", ..." if len(blind) > 3 else ""
        )
        notes.append(
            f"no per-step figures were given for {len(blind)} sequence(s) that have sent mail "
            f"({names}), so only their whole-sequence rates were checked"
        )
    entries = [
        {"code": b.code, "sequence_id": b.sequence_id, "step": b.step, "text": b.text,
         "already_paused": b.already_paused}
        for b in breaches
    ] + [{"code": "mailbox-daily-cap", "text": t, "already_paused": False} for t in mailbox_lines]  # fmt: skip
    return {
        "stop_lines": {**lines, "mailbox_daily_cap": cap},
        "breach_count": sum(not e["already_paused"] for e in entries),
        "lines": [e["text"] for e in entries],
        "breaches": entries,
        "sequences": [_report_dict(r) for r in reports],
        "notes": notes,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # a usage mistake is exit 3: argparse's own 2 would read as a breach
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(3)


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="gtm_core.sequence_health",
        description="Recommend a pause when a sequence crosses its unsubscribe or bounce stop line.",
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--stats-dir", required=True, type=Path, help="get_sequence_stats replies")
    ap.add_argument("--consolidated", nargs="+", type=Path, help="get_consolidated_stats pages")
    ap.add_argument("--sequences", type=Path, help="list_sequences reply (marks paused ones)")
    ap.add_argument("--mailbox-daily-cap", type=int)
    ap.add_argument("--mailbox-daily-volume", type=int)
    ap.add_argument("--json", action="store_true")
    return ap


def _render(result: dict, profile: str) -> str:
    out = [
        f"sequence_health - profile {shown(profile)}: {len(result['sequences'])} sequence(s), "
        f"{result['breach_count']} breach(es) needing action"
    ]
    out += result["lines"] or ["OK: no sequence is over a stop line."]
    out += [f"note: {n}" for n in result["notes"]]
    return "\n".join(out)


def run(args: argparse.Namespace) -> tuple[int, dict]:
    try:
        profile = _safe_segment(args.profile, "profile")
    except ValueError as exc:
        raise PayloadError(str(exc)) from exc
    root = resolve_content_root()
    if not (root / profile).is_dir():
        raise PayloadError(
            f"no content folder for profile {shown(profile)} under {shown(str(root))}"
        )
    lines, cap, volume = load_settings(profile, root)
    cap = args.mailbox_daily_cap if args.mailbox_daily_cap is not None else cap
    stats = load_stats_dir(args.stats_dir, need_emails=True)
    emails, steps = [], {}
    if args.consolidated:
        pages = [read_json(p, "--consolidated") for p in args.consolidated]
        emails, _ = consolidated_emails(pages, "--consolidated")
        steps = step_figures(emails)
    live = {}
    if args.sequences:
        rows = list_rows(read_json(args.sequences, "--sequences"), "--sequences")
        live = {r["id"]: (r.get("title") or "", r["active"]) for r in rows}
    given = args.mailbox_daily_volume if args.mailbox_daily_volume is not None else volume
    peak = _mailbox_peak(emails)
    seen = (given, "", "") if given is not None else peak
    result = check(build_reports(stats, steps, live), lines, cap=cap, volume=seen)
    return (2 if result["breach_count"] else 0), {"profile": profile, **result}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, result = run(args)
    except PayloadError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(
        json.dumps(result, indent=2, ensure_ascii=False)
        if args.json
        else _render(result, result["profile"])
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
