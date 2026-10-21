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

What this deliberately does NOT do: judge the positive reply rate. A gate that demanded,
say, a 3% positive-reply rate before the next wave would block the pipeline on a number
nobody has established a baseline for. The requirement is that the number **exists and
was looked at**.

What this DOES guard: domain safety via the opt-out rate. An opt-out rate >5.0% (on 30+ sends)
or >3 raw opt-outs (on <30 sends) indicates severe negative sentiment or list decay that will
burn sender reputation. It refuses wave N+1 unless explicitly acknowledged (--ack-high-optout).

Stdlib-only, no I/O beyond JSONL append/read under the profile's own content root.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import resolve_content_root
from .prospect_paths import outcomes_jsonl, suppression_ledger

__all__ = [
    "WaveReport",
    "RECORD_KIND",
    "MIN_SENDS",
    "MAX_OPTOUT_RATE",
    "MAX_OPTOUT_RAW_BELOW_SAMPLE",
    "SAMPLE_SIZE_THRESHOLD",
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

#: Safety thresholds for opt-out rate.
#: High opt-out rates burn domain reputation and deliverability permanently.
#: A rate > 5.0% on 30+ sends (or > 3 raw opt-outs on < 30 sends) refuses the next wave
#: unless acknowledged by the operator with --ack-high-optout.
MAX_OPTOUT_RATE = 0.05
MAX_OPTOUT_RAW_BELOW_SAMPLE = 3
SAMPLE_SIZE_THRESHOLD = 30


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
    #: WHO opted out, not just how many. Added 2026-09-21. A wave record was an aggregate
    #: by design, and the provenance of its count lived in the free-text ``source`` field --
    #: which is unparseable, so `optout_reconciliation` could never tell a fully-accounted
    #: wave from an unaccounted one. Three real opt-outs sat on the provider's DNC list for
    #: six weeks with nothing local naming them; the refusal that should have caught it fired
    #: only at `check` time, long after the next wave could have been staged.
    opt_out_emails: tuple[str, ...] = ()

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


def _emails(value: object) -> tuple[str, ...]:
    """Lowercased addresses from a list, or from a comma/space-separated string."""
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    if not isinstance(value, (list, tuple)):
        return ()
    seen: list[str] = []
    for item in value:
        addr = str(item or "").strip().lower()
        if addr and "@" in addr and addr not in seen:
            seen.append(addr)
    return tuple(seen)


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
    emails = _emails(
        stats.get("opt_out_emails")
        or stats.get("unsubscribed_emails")
        or payload.get("opt_out_emails")
    )
    return WaveReport(
        wave=str(wave or payload.get("wave") or payload.get("sequence_id") or "").strip(),
        date=str(date or payload.get("date") or "").strip(),
        sends=sends,
        replies=replies,
        positive_replies=positive,
        opt_outs=opt_outs,
        source=str(payload.get("source") or "").strip(),
        opt_out_emails=emails,
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
        if "opt_out_emails" in fields:
            # JSON has no tuple: a round-tripped record comes back as a list.
            fields["opt_out_emails"] = _emails(fields["opt_out_emails"])
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


def identifiable_optouts(profile: str, content_root: Path | None = None) -> set[str]:
    """Every person this system can NAME as having asked not to be contacted.

    Two sources, unioned, because they are populated by different paths and either alone
    under-counts:

    * ``history.jsonl`` ``optout_detected`` rows — written by the reply sweep
      (:mod:`gtm_core.optout_watch`), which is the only thing that catches an opt-out sent as
      a *reply* rather than a link click.
    * suppression-ledger rows carrying a reason in
      :data:`gtm_core.suppression.PROVIDER_DNC_REASONS` — a recipient-initiated do-not-contact,
      as distinct from the list-hygiene reasons (``out-of-market``, ``competitor``,
      ``eval-disqualified``) that make up most of that file and are OUR judgments, not theirs.

    Addresses are lowercased; the set is what :func:`optout_reconciliation` counts.
    """
    known: set[str] = set()

    history = (
        resolve_content_root() / profile / "history.jsonl"
        if content_root is None
        else (content_root / profile / "history.jsonl")
    )
    if history.is_file():
        for line in history.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") == "optout_detected" and row.get("email"):
                known.add(str(row["email"]).strip().lower())

    ledger = suppression_ledger(profile, content_root)
    if ledger.is_file():
        import csv as _csv

        from .suppression import PROVIDER_DNC_REASONS

        with ledger.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in _csv.DictReader(fh):
                if (row.get("reason") or "").strip().lower() in PROVIDER_DNC_REASONS:
                    email = (row.get("email") or "").strip().lower()
                    if email:
                        known.add(email)
    return known


def unsuppressed_optouts(
    emails: Iterable[str], profile: str, content_root: Path | None = None
) -> list[str]:
    """Which of ``emails`` the send path could still reach, in order.

    Naming an opt-out in a wave record does not stop the next send from reaching them —
    ``outcomes.jsonl`` is an audit ledger and nothing in the build path reads it. Only a
    row in the suppression ledger carrying a :data:`PROVIDER_DNC_REASONS` reason does that.
    So :func:`_cli_ingest` uses this to refuse a reading whose opt-outs have not landed
    somewhere that excludes them, rather than accepting a name as if it were a suppression.
    """
    known = identifiable_optouts(profile, content_root)
    return [e for e in (str(x).strip().lower() for x in emails) if e and e not in known]


def optout_reconciliation(profile: str, content_root: Path | None = None) -> tuple[int, int]:
    """``(counted, identifiable)`` opt-outs across every recorded wave.

    A wave report is an AGGREGATE by design — it carries ``opt_outs: 3``, never who. That is
    the right shape for a rate, and it is why this comparison has to exist separately: a
    count of people who asked not to be contacted is not the same artifact as a list of them,
    and only the list can stop the next send reaching them.

    The gap this surfaces is the live one. On 2026-09-21 a live tenant's ledger held 3 counted
    opt-outs and **0** identifiable: two link-click unsubscribes and one ``doNotContact`` that
    live only inside the provider, plus one sent as a REPLY — which the provider does not
    auto-suppress at all, and which this system had no record of in either source above.
    Nothing in the lane router could exclude any of the three, because ``suppressed`` reads
    the ledger and ``optout`` reads ``optout_detected``, and neither had a row.
    """
    counted = sum(max(0, r.opt_outs) for r in read_reports(profile, content_root))
    return counted, len(identifiable_optouts(profile, content_root))


def check(
    profile: str,
    content_root: Path | None = None,
    min_sends: int = MIN_SENDS,
    ack_high_optout: bool = False,
    ack_unreconciled_optouts: bool = False,
) -> tuple[bool, str]:
    """Is the previous wave measured, and is opt-out rate within safe limits? Returns ``(ok, message)``."""
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

    # Opt-out safety. The RATE always applies: by this point `check` has already required
    # `sends >= MIN_SENDS`, so a rate is readable. The raw ceiling is an ADDITIONAL backstop
    # on small waves, never a replacement for the rate.
    #
    # CHANGED 2026-09-21, reversing a behaviour that was pinned by a passing test. The two
    # branches used to be EXCLUSIVE — under SAMPLE_SIZE_THRESHOLD the raw ceiling ran
    # *instead of* the rate — so the guard was non-monotonic in the one direction that
    # matters: a smaller wave could walk under it. On the live ledger the day this was found:
    #
    #     3 opt-outs / 24 sends = 12.5%  -> PASS   (3 is not > 3, and the rate never ran)
    #     2 opt-outs / 30 sends =  6.7%  -> REFUSE
    #
    # The gate reported PASS on the exact wave the module docstring cites as the reason it
    # exists. The original intent was sound — a rate over ~20 sends is noisy, so a raw count
    # is the better read — but making the raw count a REPLACEMENT let a 10-12% wave through.
    #
    # The reachable hole was narrow, and worth stating precisely rather than dramatically:
    # `check` already refuses anything under MIN_SENDS (20), so only waves of 20-29 sends
    # carrying 2-3 opt-outs changed verdict. The noise concern is still handled, by the strict
    # `>`: one opt-out in 20 is exactly 5.0% and still passes. Two in 20 is 10% and no longer
    # does, which is the correct reading of two people in twenty asking to be left alone.
    over_rate = latest.opt_out_rate > MAX_OPTOUT_RATE
    over_raw = (
        latest.sends < SAMPLE_SIZE_THRESHOLD and latest.opt_outs > MAX_OPTOUT_RAW_BELOW_SAMPLE
    )
    high_optout = over_rate or over_raw
    if high_optout and not ack_high_optout:
        tripped = []
        if over_rate:
            tripped.append(f">{MAX_OPTOUT_RATE:.1%} threshold")
        if over_raw:
            tripped.append(f">{MAX_OPTOUT_RAW_BELOW_SAMPLE} raw opt-outs ceiling")
        threshold_desc = " and ".join(tripped)
        return False, (
            f"Wave {latest.wave!r} ({latest.date}) opt-out rate is too high: "
            f"{latest.opt_outs}/{latest.sends} opt-outs ({latest.opt_out_rate:.1%}), "
            f"exceeding the safety limit ({threshold_desc}).\n"
            "  Sending into high opt-outs burns sender reputation and deliverability.\n"
            "  Review messaging and targeting before staging another wave.\n"
            "  To acknowledge this risk and override, run with --ack-high-optout."
        )

    # Counting an opt-out is not the same as being able to avoid that person. A wave report
    # is an aggregate; the lane router excludes by ADDRESS. If the two disagree, somebody who
    # asked to be left alone is reachable by the next wave, and no rate check can see it.
    counted, identifiable = optout_reconciliation(profile, content_root)
    if counted > identifiable and not ack_unreconciled_optouts:
        return False, (
            f"{counted} opt-out(s) are counted across recorded waves but only {identifiable} "
            f"can be NAMED — {counted - identifiable} person(s) asked not to be contacted and "
            f"this system cannot exclude them.\n"
            "  A wave report carries a count, never an address. The lane router excludes by "
            "address (`suppressed` reads the suppression ledger, `optout` reads an\n"
            "  `optout_detected` history row), so an unnamed opt-out is reachable by the next "
            "wave.\n"
            "  Two ways a real opt-out goes unnamed:\n"
            "    * it was sent as a REPLY — the sequencer does not auto-suppress those at all. "
            "`gtm_core.optout_watch` is what catches them, on the\n"
            "      gtm-optout-watch timer; if that is not running, none are being caught.\n"
            "    * it is a link-click unsubscribe held only inside the provider, never mirrored "
            "into the ledger.\n"
            "  Fix: run the sweep, or add the person with reason `dnc-optout` "
            "(`python -m gtm_core.suppression ...`).\n"
            "  To proceed anyway, run with --ack-unreconciled-optouts."
        )

    return True, (
        f"Wave {latest.wave!r} ({latest.date}): {latest.sends} sent, "
        f"{latest.positive_replies} positive ({latest.positive_reply_rate:.1%}), "
        f"{latest.opt_outs} opt-out ({latest.opt_out_rate:.1%}); "
        f"{identifiable}/{counted} opt-out(s) identifiable and excludable."
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

    # An opt-out count with nobody attached to it is the shape of the 2026-07-29 failure:
    # `opt_outs: 3` was recorded, the three people were never written anywhere the send
    # path reads, and the mismatch surfaced only at `check` time six weeks later — where it
    # was also overridable. Ingest is the moment the provider payload is in hand, so it is
    # the moment to insist on who. Refusing here is cheap; refusing later is not.
    if report.opt_outs > 0 and not args.ack_unnamed_optouts:
        if not report.opt_out_emails:
            print(
                f"REFUSED — this reading counts {report.opt_outs} opt-out(s) but names none.\n"
                "  A count cannot stop the next send reaching them; only a suppression-ledger\n"
                "  row can. Add `opt_out_emails` to the payload (or re-read the provider's\n"
                "  unsubscribed/DNC list for this sequence), record each one with\n"
                "  `reason=dnc-optout`, then ingest again.\n"
                "  To record the count anyway, re-run with --ack-unnamed-optouts.",
                file=sys.stderr,
            )
            return 1
        loose = unsuppressed_optouts(report.opt_out_emails, args.profile)
        if loose:
            print(
                f"REFUSED — {len(loose)} named opt-out(s) are not suppressed anywhere the\n"
                "  send path reads, so a pool rebuild can put them back on a send list:\n"
                + "".join(f"    - {e}\n" for e in loose)
                + "  Add each to the suppression ledger with `reason=dnc-optout` (and to the\n"
                "  provider's DNC list), then ingest again.\n"
                "  To record the reading anyway, re-run with --ack-unnamed-optouts.",
                file=sys.stderr,
            )
            return 1
        if len(report.opt_out_emails) < report.opt_outs:
            print(
                f"REFUSED — this reading counts {report.opt_outs} opt-out(s) but names only "
                f"{len(report.opt_out_emails)}.\n"
                "  A partial list reconciles as if it were complete, which is how the "
                "unnamed ones stay unnamed.\n"
                "  Name the rest, or re-run with --ack-unnamed-optouts.",
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
    ok, message = check(
        args.profile,
        min_sends=args.min_sends,
        ack_high_optout=args.ack_high_optout,
        ack_unreconciled_optouts=args.ack_unreconciled_optouts,
    )
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
    ing.add_argument(
        "--ack-unnamed-optouts",
        action="store_true",
        default=False,
        help=(
            "record an opt-out count whose people are not named and suppressed — the reading "
            "is kept, but nothing stops the next send reaching them"
        ),
    )
    ing.set_defaults(func=_cli_ingest)

    chk = sub.add_parser("check", help="exit non-zero unless the last wave has a readable rate")
    chk.add_argument("--profile", required=True)
    chk.add_argument("--min-sends", type=int, default=MIN_SENDS)
    chk.add_argument(
        "--ack-high-optout",
        action="store_true",
        default=False,
        help="acknowledge high opt-out rate and override safety refusal",
    )
    chk.add_argument(
        "--ack-unreconciled-optouts",
        action="store_true",
        default=False,
        help=(
            "proceed although counted opt-outs exceed the ones this system can name and "
            "therefore exclude"
        ),
    )
    chk.set_defaults(func=_cli_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
