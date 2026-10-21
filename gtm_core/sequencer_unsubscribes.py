"""A sequencer unsubscribe → an OPEN opt-out row, for the approval gate to act on.

**The gap this closes (2026-10-06).** 49 people used the unsubscribe link in the sending tool and
not one of them was in the opt-out ledger: only a typed reply ever wrote an ``optout_detected`` row
(:mod:`agent.optout_sweep`). The DNC dispatcher (:mod:`agent.dnc_dispatch`) intersects an approved
draft with the ledger's OPEN opt-out rows and refuses every other address, so the mirror to the
provider's Do Not Contact list had nothing to act on for them — the sending tool had stopped
emailing them inside the sequence, and nothing else in the system knew they had left.

**What this does, and the boundary.** It reads per-email reports the agent already fetched
(``get_consolidated_stats``), and for each address whose email is flagged ``Unsubscribed: Yes`` it
appends one ``optout_detected`` row per address and ONE ``optout_detected`` signal per batch. The signal is the existing
route to the ``inbound/optout-suppress`` pack: it drafts the addresses with this row as their
evidence and **stops at a human gate**; ``agent/dnc_dispatch.py`` adds them only after the operator
approves the exact addresses, behind its own kill switch, with a read-back. This module is the
sibling of ``agent.optout_auto_add.record_enrolled_alias`` and holds the same line: the row is
``clear: False``, so it can never reach the gateless automatic add, which stays reserved for a
short typed "stop" from the replying address (CLAUDE.md, "One deliberate gateless path").
It holds no provider call and no egress; the module cannot add anyone to anything.

**One signal per batch, not per address.** ``agent.signal_dispatch`` starts one pack run per
signal and passes the run nothing from the signal; the pack then lists EVERY open row in the
ledger. Forty-nine signals would be forty-nine identical, model-backed approval runs (ten a day,
under the daily cap), and the first would already have listed all of them.

**What the row does and does not claim.** The sending tool records that the unsubscribe LINK in an
email was used. It does not record who used it. A mail scanner following links in a follow-up
produces the same flag as a person (on 2026-10-06, 45 of 49 flags sat on the Step 2 follow-up and 4
on Step 1). The row says exactly that, and the operator decided to suppress either way: the cost of
a false add is one prospect, and no automated path can undo it (DNC is add-only).

**Untrusted input (§R5).** The report is data. An address must be shaped like one; a row whose
sequence is not registered in ``sequences/cells.toml`` is refused and counted; a report that cannot
be read, or that holds no rows, is an error — never an empty plan that reads as "nobody
unsubscribed".

CLI (dry-run unless ``--apply``)::

    python -m gtm_core.sequencer_unsubscribes --profile P --report page1.json --report page2.json
    python -m gtm_core.sequencer_unsubscribes --profile P --report page1.json --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .ledgers import Ledgers
from .optout_sets import LINK_SOURCE, optout_key, optout_sets
from .paths import PathConfig, resolve_content_root
from .signals import build_signal

SKILL = "sequencer-unsubscribe"
SOURCE = LINK_SOURCE
#: The batch signal's source prefix, and the address-less "who": a signal cannot name where
#: anything goes (§R5), and the pack it opens lists the ledger, not the signal.
SIGNAL_SOURCE = "saleshandy-unsub:"
SIGNAL_WHO = "sequencer-unsubscribes"
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_ADDRESS = 254


class ReportError(ValueError):
    """A report that cannot be trusted to say "nobody unsubscribed"."""


@dataclass(frozen=True)
class Candidate:
    email: str
    sequence_id: str
    sequence: str
    step: int
    sent_at: str


@dataclass
class Plan:
    #: No opt-out row exists for these: each needs the row AND its signal.
    new: list[Candidate] = field(default_factory=list)
    #: This module wrote the row, but its signal is not in the ledger (a crash between the two
    #: writes). Only the signal is missing; a second row would double-count the opt-out.
    unsignalled: list[Candidate] = field(default_factory=list)
    already: int = 0  # has an opt-out row from somewhere else (a reply), or is already mirrored
    malformed: int = 0
    unregistered: int = 0
    rows_read: int = 0
    flagged: int = 0  # rows carrying Unsubscribed: Yes


def read_reports(paths: list[Path]) -> list[dict]:
    """Every row of every saved report. The tool's own saved reply is accepted as it came
    (``{"payload": {"data": [...]}}``, possibly after a line of tool text), as is a bare list or
    ``{"data": [...]}``."""
    rows: list[dict] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
            start = min(i for i in (text.find("{"), text.find("[")) if i != -1)
            doc = json.JSONDecoder().raw_decode(text[start:])[0]
        except (OSError, ValueError) as exc:  # min() of nothing, bad JSON, unreadable file
            raise ReportError(f"{path}: cannot read this report ({type(exc).__name__})") from exc
        if isinstance(doc, dict) and isinstance(doc.get("payload"), dict):
            doc = doc["payload"]
        if isinstance(doc, dict):
            doc = doc.get("data")
        if not isinstance(doc, list):
            raise ReportError(f"{path}: no list of email rows in this report")
        rows += [r for r in doc if isinstance(r, dict)]
    if not rows:
        raise ReportError(
            "the reports hold no email rows, so nothing can be said about unsubscribes"
        )
    return rows


def _registered(profile: str, content_root: Path) -> set[str]:
    path = content_root / profile / "prospects" / "sequences" / "cells.toml"
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return set()  # fail closed: every row then counts as unregistered, loudly
    return {str(r["id"]) for r in doc.get("sequence", []) if r.get("id")}


def _step(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**6  # an unreadable step sorts last, never first


def plan(profile: str, rows: list[dict], content_root: Path | None = None) -> Plan:
    """What :func:`apply` would write. Reads the ledger; writes nothing."""
    root = content_root or resolve_content_root()
    known = _registered(profile, root)
    ledgers = Ledgers(PathConfig(root, root, profile), profile)
    history = list(ledgers.iter_history())
    detected, unreadable, added, _ = optout_sets(history)
    have_row = detected | unreadable | added
    own_rows = {
        optout_key(r.get("email"))
        for r in history
        if r.get("event") == "optout_detected" and r.get("skill") == SKILL
    }
    # An address is "signalled" when a batch signal this module raised names it in its meta.
    signalled = {
        optout_key(a)
        for r in history
        if r.get("event") == "signal"
        and r.get("signal_type") == "optout_detected"
        and str(r.get("source") or "").startswith(SIGNAL_SOURCE)
        for a in (r.get("meta") or {}).get("addresses") or []
    }

    out = Plan(rows_read=len(rows))
    best: dict[str, Candidate] = {}
    for r in rows:
        if r.get("Unsubscribed") != "Yes":
            continue
        out.flagged += 1
        email = optout_key(r.get("Recipient Email"))
        if len(email) > _MAX_ADDRESS or not _ADDRESS.match(email):
            out.malformed += 1
            continue
        if str(r.get("Sequence Id") or "") not in known:
            out.unregistered += 1
            continue
        cand = Candidate(
            email=email,
            sequence_id=str(r["Sequence Id"]),
            sequence=str(r.get("Sequence Title") or ""),
            step=_step(r.get("Step Number")),
            sent_at=str(r.get("Email Sent At") or ""),
        )
        if email not in best or cand.step < best[email].step:
            best[email] = cand
    for email, cand in best.items():
        if email in own_rows:
            if email not in signalled:
                out.unsignalled.append(cand)
            else:
                out.already += 1
        elif email in have_row:
            out.already += 1
        else:
            out.new.append(cand)
    return out


def _row(c: Candidate) -> dict:
    return {
        "event": "optout_detected",
        "skill": SKILL,
        "source": SOURCE,
        "email": c.email,
        "sequence_id": c.sequence_id,
        "sequence": c.sequence,
        "step": c.step,
        "email_sent_at": c.sent_at,
        "snippet": (
            f"The sending tool marks the step {c.step} email to this address, sent {c.sent_at}, "
            "as unsubscribed: the unsubscribe link in it was used. It records that the link was "
            "used, not who used it."
        ),
        "direction_known": True,
        "clear": False,
        "escalated": False,
        "action_required": (
            "operator approves adding this address to Global DNC at the optout-suppress gate "
            "(DNC has no removal API). It is never added automatically: a mail scanner following "
            "the link sets the same flag as a person."
        ),
    }


def _signal(batch: list[Candidate]) -> dict:
    addresses = sorted(c.email for c in batch)
    digest = hashlib.sha256("\n".join(addresses).encode()).hexdigest()[:16]
    return build_signal(
        SIGNAL_WHO,
        "optout_detected",
        f"{SIGNAL_SOURCE}{digest}",
        meta={"addresses": addresses, "count": len(addresses), "source": SOURCE},
    )


def apply(p: Plan, ledgers: Ledgers) -> tuple[int, int]:
    """Write what ``p`` says: ``(rows written, signals written)``. The row goes first, so a crash
    leaves an open row with no signal — which the next :func:`plan` reports as ``unsignalled`` and
    restores — and never a signal for an address with no row to intersect with."""
    seen = {
        sid
        for r in ledgers.iter_history()
        if r.get("event") == "signal"
        for sid in (r.get("source_items") or [])
    }
    # Re-read the ledger rather than trust the plan: a plan applied twice (or by two processes) must
    # not write a second opt-out row for the same address.
    detected, unreadable, added, _ = optout_sets(ledgers.iter_history())
    have_row = detected | unreadable | added
    rows = signals = 0
    for c in p.new:
        if c.email in have_row:
            continue
        ledgers.append_history(_row(c))
        have_row.add(c.email)
        rows += 1
    batch = [*p.new, *p.unsignalled]
    if batch:
        s = _signal(batch)
        if s["id"] not in seen:
            ledgers.append_history(
                {
                    "event": "signal",
                    "skill": "inbound-triage",
                    "signal_type": s["signal_type"],
                    "who": s["who"],
                    "source": s["source"],
                    "suggested_action": s["suggested_action"],
                    "source_items": [s["id"]],
                    "meta": s["meta"],
                }
            )
            signals += 1
    return rows, signals


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--report", action="append", required=True, type=Path)
    ap.add_argument("--content-root", type=Path)
    ap.add_argument("--apply", action="store_true", help="write the rows (default: plan only)")
    args = ap.parse_args(argv)
    root = args.content_root or resolve_content_root()
    try:
        p = plan(args.profile, read_reports(args.report), root)
    except ReportError as exc:
        print(f"error: {exc}")
        return 2
    print(
        f"{p.rows_read} email row(s) read, {p.flagged} flagged unsubscribed: "
        f"{len(p.new)} new open opt-out(s), {len(p.unsignalled)} to re-signal, "
        f"{p.already} already on record"
    )
    refused = p.malformed + p.unregistered
    if refused:
        print(
            f"refused {refused}: {p.malformed} with a malformed address, {p.unregistered} from a "
            "sequence not registered in cells.toml (register it, do not widen this check)"
        )
    if not args.apply:
        print("(plan only — pass --apply to write the ledger)")
        return 1 if refused else 0
    rows, signals = apply(p, Ledgers(PathConfig(root, root, args.profile), args.profile))
    print(
        f"wrote {rows} opt-out row(s) and {signals} signal(s). Nothing was added to DNC: the "
        "approval gate lists these addresses next, and only an operator-approved list is mirrored."
    )
    return 1 if refused else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
