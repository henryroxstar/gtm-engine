"""``python -m gtm_core.ledger_cli dispatch-intents …`` — the operator's side of the intent ledger (F4).

Reached through :mod:`gtm_core.ledger_cli` (the one place skills and runbooks already look). Stdlib only.

    dispatch-intents --profile P --unresolved
    dispatch-intents --profile P resolve BATCH (--sent | --not-sent | --sent-recipients FILE)
    dispatch-intents --profile P removed --sequence SEQ --recipient EMAIL [--recipient EMAIL ...]
    dispatch-intents --profile P --restored
    dispatch-intents --profile P --reconcile --sequence SEQ --sequencer-export FILE [--record]

``resolve`` is how a human settles an "outcome unknown": ``--sent`` (it went out: stays blocked),
``--not-sent`` (it did not: released), or ``--sent-recipients FILE`` for a partial send (the listed
recipients are marked sent, the rest released). It only ever APPENDS — a row is never edited or removed.
``removed`` lets a recipient the operator deliberately took out of the sequencer be enrolled again.

After restoring from a backup the record can be BEHIND the sequencer (the snapshot predates sends made
since). ``--restored`` puts dispatch ON HOLD; ``--reconcile`` lists the recipients the sequencer has that the
ledger forgot (from an export you give it — this CLI makes no network call), and ``--record`` marks them sent
and lifts the hold.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from gtm_core.dispatch_intents import BLOCKING, FileIntentStore, recipient_key
from gtm_core.ledgers import Ledgers
from gtm_core.paths import PathConfig


def _read_recipients(path: Path) -> list[str]:
    """Emails from a CSV with an ``email`` column, or one per line. De-duplicated, normalised."""
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip().lower() for h in next(csv.reader([lines[0]]))]
    found: list[str] = []
    if "email" in header:
        idx = header.index("email")
        for row in csv.reader(lines[1:]):
            if len(row) > idx and row[idx].strip():
                found.append(recipient_key(row[idx]))
    else:
        for ln in lines:
            cell = next(csv.reader([ln]))[0]
            if cell.strip() and cell.strip().lower() != "email":
                found.append(recipient_key(cell))
    return list(dict.fromkeys(found))


def _cmd_unresolved(store: FileIntentStore) -> int:
    open_batches = asyncio.run(store.unresolved())
    if not open_batches:
        print("dispatch-intents: none open")
        return 0
    for b in open_batches:
        what = f" «{b.label}»" if b.label else ""
        print(f"{b.batch_id}\t{b.scope}\t{b.tool}\t{b.count} recipient(s)\t{b.state}\t{b.ts}{what}")
    return 0


def _cmd_resolve(store: FileIntentStore, ns: argparse.Namespace) -> int:
    if len(ns.rest) != 2:
        raise SystemExit("[dispatch-intents] resolve needs exactly one BATCH id")
    chosen = [bool(ns.sent), bool(ns.not_sent), ns.sent_recipients is not None]
    if sum(chosen) != 1:
        raise SystemExit(
            "[dispatch-intents] say which: --sent, --not-sent, or --sent-recipients FILE (exactly one)"
        )
    batch = ns.rest[1]
    resolution = "sent" if ns.sent else "not_sent" if ns.not_sent else "sent_recipients"
    listed = _read_recipients(ns.sent_recipients) if ns.sent_recipients is not None else None
    try:
        res = asyncio.run(store.resolve(batch, resolution, sent_recipients=listed, actor="cli"))
    except KeyError:
        raise SystemExit(f"[dispatch-intents] no such batch {batch!r}") from None
    print(f"dispatch-intents: {res['sent']} marked sent, {res['released']} released")
    return 0


def _cmd_removed(store: FileIntentStore, ns: argparse.Namespace) -> int:
    if not ns.sequence or not ns.recipient:
        raise SystemExit("[dispatch-intents] removed needs --sequence and at least one --recipient")
    asyncio.run(
        store.mark_removed(ns.sequence, [recipient_key(r) for r in ns.recipient], actor="cli")
    )
    print(
        f"dispatch-intents: {len(ns.recipient)} recipient(s) may be enrolled again in {ns.sequence}"
    )
    return 0


def _cmd_restored(store: FileIntentStore) -> int:
    asyncio.run(store.set_hold("restored from backup — reconcile before sending"))
    print(
        "dispatch-intents: dispatch is ON HOLD. Export the sequence's recipients from the sequencer, then run\n"
        "  dispatch-intents --reconcile --sequence SEQ --sequencer-export FILE [--record]"
    )
    return 0


def _cmd_reconcile(store: FileIntentStore, ns: argparse.Namespace) -> int:
    if not ns.sequence or not ns.sequencer_export:
        raise SystemExit(
            "[dispatch-intents] --reconcile needs --sequence and --sequencer-export FILE"
        )
    exported = _read_recipients(ns.sequencer_export)
    known = asyncio.run(store.states(ns.sequence, exported))
    forgotten = [r for r in exported if known.get(r) not in BLOCKING]
    for r in forgotten:
        print(r)
    print(
        f"dispatch-intents: {len(forgotten)} recipient(s) in the sequencer that the ledger forgot"
    )
    if not ns.record:
        return 0
    if forgotten:
        claimed = asyncio.run(
            store.claim(ns.sequence, forgotten, tool="reconcile", ignore_hold=True)
        )
        if not claimed.ok:  # cannot happen (we only claim the unblocked), but never guess
            raise SystemExit(f"[dispatch-intents] could not record: {claimed.message}")
        asyncio.run(
            store.record_outcome(claimed.batch_id, "dispatched", "reconciled from sequencer")
        )
    asyncio.run(store.clear_hold())
    print("dispatch-intents: recorded as sent; hold lifted")
    return 0


def main(argv: list[str], repo_root: Path | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gtm_core.ledger_cli dispatch-intents")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--unresolved", action="store_true")
    ap.add_argument("--restored", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--sequence")
    ap.add_argument("--sequencer-export", type=Path)
    ap.add_argument("--sent", action="store_true")
    ap.add_argument("--not-sent", action="store_true")
    ap.add_argument("--sent-recipients", type=Path)
    ap.add_argument("--recipient", action="append", default=[])
    ap.add_argument("rest", nargs="*")
    ns = ap.parse_args(argv)

    store = FileIntentStore(Ledgers(PathConfig.from_env(repo_root=repo_root), ns.profile))
    action = ns.rest[0] if ns.rest else None
    if ns.unresolved:
        return _cmd_unresolved(store)
    if action == "resolve":
        return _cmd_resolve(store, ns)
    if action == "removed":
        return _cmd_removed(store, ns)
    if ns.restored:
        return _cmd_restored(store)
    if ns.reconcile:
        return _cmd_reconcile(store, ns)
    ap.error("nothing to do: give --unresolved, resolve, removed, --restored or --reconcile")
    return 2  # unreachable


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
