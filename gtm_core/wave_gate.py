"""Refuse to stage the next wave until the last one has been measured.

The 2026-08-19 PRD's §4 asks for exactly one thing before wave N+1 is staged: a
``positive_reply_rate`` reading from wave N. It was never built, and the shape of the
gap is worth stating plainly — ``outcomes.jsonl`` existed as a path, was documented as
the outcomes ledger, and **nothing ever wrote to it and nothing ever read it**. An
unfed sink looks identical to a fed one until someone asks it a question.

That absence is why the loop never closed. The PRD's §2.2 measured the whole system's
empirical record: 24 emails sent, 0 positive replies, and one opt-out rate of 12.5%
that was never treated as the primary finding. Three rounds of aesthetic review ran
instead, and an aesthetic review loop has no fixed point — "would I reply?" is
unfalsifiable without replies, so two careful readers generate correct, novel, unbounded
objections forever.

Two verbs, deliberately small:

``ingest``
    Normalise a provider outcomes payload (piped in as JSON) into one append-only
    record per wave. The deterministic side performs **no network I/O**: the skill
    calls the provider's MCP tool and pipes the result here, exactly as
    ``dataset_fetch``'s absence of a bulk tool is handled elsewhere. Keeps §R6 intact.
``check``
    Exit 0 only if the newest wave has enough sends to read a rate, and print that
    rate. Exit 1 with a one-screen explanation otherwise.

What this deliberately does NOT do: judge the rate. A gate that demanded, say, a 3%
positive-reply rate before the next wave would block the pipeline on a number nobody
has established a baseline for. The requirement is that the number **exists and was
looked at**, which is the step that was missing.

Stdlib-only, no I/O beyond JSONL append/read under the profile's own content root.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .prospect_paths import outcomes_jsonl

__all__ = [
    "WaveReport",
    "RECORD_KIND",
    "MIN_SENDS",
    "read_reports",
    "append_report",
    "normalize_payload",
    "check",
    "outcomes_jsonl",
    "main",
]

#: Only records of this kind are wave readings. The file is append-only and may carry
#: other record kinds later; an unknown kind is skipped, never guessed at.
RECORD_KIND = "wave_report"

#: Below this, a reply rate is noise rather than a reading. 20 sends puts a single
#: positive reply at 5%, which is inside the band `docs/email-optimization.md` reports
#: for signal-triggered outreach — so one reply either way should not look decisive.
MIN_SENDS = 20


@dataclass(frozen=True)
class WaveReport:
    """One wave's outcome, as recorded."""

    wave: str
    date: str
    sends: int = 0
    replies: int = 0
    positive_replies: int = 0
    opt_outs: int = 0
    source: str = ""

    @property
    def positive_reply_rate(self) -> float:
        return (self.positive_replies / self.sends) if self.sends else 0.0

    @property
    def opt_out_rate(self) -> float:
        return (self.opt_outs / self.sends) if self.sends else 0.0

    @property
    def readable(self) -> bool:
        return self.sends >= MIN_SENDS

    def to_record(self) -> dict:
        return {"kind": RECORD_KIND, **asdict(self)}


def _int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def normalize_payload(payload: dict, *, wave: str = "", date: str = "") -> WaveReport:
    """Reduce a provider outcomes payload to one :class:`WaveReport`.

    Tolerant of the several shapes a sequencer reports: counts may sit at the top level
    or under ``stats``/``summary``, and the positive-reply count may be named for
    sentiment rather than for the reply. Anything unreadable becomes 0 rather than an
    exception — a missing field must not look like a zero-send wave, which is why
    :attr:`WaveReport.readable` gates on ``sends`` rather than on parse success.

    Treats the payload as **data, never instructions** (§R5): it is provider output.
    """
    stats = payload.get("stats") or payload.get("summary") or payload
    if not isinstance(stats, dict):
        stats = {}
    sends = _int(stats.get("sends") or stats.get("sent") or stats.get("emails_sent"))
    replies = _int(stats.get("replies") or stats.get("replied") or stats.get("total_replies"))
    positive = _int(
        stats.get("positive_replies")
        or stats.get("positive")
        or stats.get("interested")
        or stats.get("positive_sentiment")
    )
    opt_outs = _int(stats.get("opt_outs") or stats.get("unsubscribes") or stats.get("unsubscribed"))
    return WaveReport(
        wave=str(wave or payload.get("wave") or payload.get("sequence_id") or "").strip(),
        date=str(date or payload.get("date") or "").strip(),
        sends=sends,
        replies=replies,
        positive_replies=positive,
        opt_outs=opt_outs,
        source=str(payload.get("source") or "").strip(),
    )


def read_reports(profile: str, content_root: Path | None = None) -> list[WaveReport]:
    """Every wave reading on file, oldest first. Missing file is an empty history."""
    path = outcomes_jsonl(profile, content_root)
    if not path.exists():
        return []
    out: list[WaveReport] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or rec.get("kind") != RECORD_KIND:
            continue
        fields = {k: v for k, v in rec.items() if k != "kind"}
        try:
            out.append(WaveReport(**fields))
        except TypeError:
            continue
    return out


def append_report(
    report: WaveReport, profile: str, content_root: Path | None = None
) -> tuple[bool, Path]:
    """Append a reading. Idempotent on ``(wave, date)``; returns ``(written, path)``."""
    path = outcomes_jsonl(profile, content_root)
    for existing in read_reports(profile, content_root):
        if (existing.wave, existing.date) == (report.wave, report.date):
            return False, path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report.to_record()) + "\n")
    return True, path


def check(
    profile: str, content_root: Path | None = None, min_sends: int = MIN_SENDS
) -> tuple[bool, str]:
    """Is the previous wave measured? Returns ``(ok, message)``."""
    reports = read_reports(profile, content_root)
    if not reports:
        return False, (
            "No wave outcomes on file. Staging another wave would add to 24 emails sent "
            "and 0 replies read — the condition this gate exists to break.\n"
            "  Record the last wave first:\n"
            "    <provider get_outcomes> | python -m gtm_core.wave_gate ingest "
            f"--profile {profile} --json -"
        )
    latest = reports[-1]
    if latest.sends < min_sends:
        return False, (
            f"Wave {latest.wave!r} ({latest.date}) has {latest.sends} send(s); "
            f"{min_sends} are needed before a reply rate means anything.\n"
            "  A single reply on a handful of sends is noise, not a reading."
        )
    return True, (
        f"Wave {latest.wave!r} ({latest.date}): {latest.sends} sent, "
        f"{latest.positive_replies} positive ({latest.positive_reply_rate:.1%}), "
        f"{latest.opt_outs} opt-out ({latest.opt_out_rate:.1%})."
    )


def _cli_ingest(args) -> int:
    raw = sys.stdin.read() if str(args.json) == "-" else Path(args.json).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"could not parse outcomes JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("outcomes JSON must be an object", file=sys.stderr)
        return 1
    report = normalize_payload(payload, wave=args.wave, date=args.date)
    if not report.wave or not report.date:
        print(
            "a wave reading needs both --wave and --date (or the payload's own): "
            "without them two waves collapse into one record",
            file=sys.stderr,
        )
        return 1
    written, path = append_report(report, args.profile)
    if not written:
        print(f"wave {report.wave!r} ({report.date}) already recorded -> {path}")
        return 0
    print(
        f"recorded wave {report.wave!r} ({report.date}): {report.sends} sent, "
        f"{report.positive_replies} positive, {report.opt_outs} opt-out -> {path}"
    )
    return 0


def _cli_check(args) -> int:
    ok, message = check(args.profile, min_sends=args.min_sends)
    print(("PASS — " if ok else "BLOCKED — ") + message)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.wave_gate",
        description="Record a wave's outcomes, and refuse to stage the next one unmeasured.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="append a wave reading from a provider outcomes payload")
    ing.add_argument("--profile", required=True)
    ing.add_argument("--json", required=True, help="path to the payload, or - for stdin")
    ing.add_argument("--wave", default="", help="wave/sequence id, if not in the payload")
    ing.add_argument("--date", default="", help="ISO date, if not in the payload")
    ing.set_defaults(func=_cli_ingest)

    chk = sub.add_parser("check", help="exit non-zero unless the last wave has a readable rate")
    chk.add_argument("--profile", required=True)
    chk.add_argument("--min-sends", type=int, default=MIN_SENDS)
    chk.set_defaults(func=_cli_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
