"""Verify the tamper-evident hash-chain on a JSONL ledger (NIST AU-9).

Each chained ledger line carries ``prev_sha256`` = SHA-256 of the *previous* raw line (newline
stripped), or ``"GENESIS"`` for the first chained line. Editing any earlier line changes its hash
and breaks the following link, so a silent after-the-fact edit is detectable.

Backwards compatible: ledgers written before the chain existed have a leading run of lines with no
``prev_sha256``. The verifier skips that legacy prefix and enforces every link from the first
chained record onward (its ``prev_sha256`` must match the hash of the immediately preceding raw
line, or ``"GENESIS"`` when it is the very first line of the file).

Pure stdlib; reads raw lines only (no ``Ledgers`` dependency), so it verifies any JSONL file.

Usage::

    python -m gtm_core.ledger_verify content/<profile>/history.jsonl [--json]

Exit codes: ``0`` chain intact · ``1`` chain break detected · ``2`` usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from gtm_core.ledgers import GENESIS_HASH, _line_sha256


@dataclass
class VerifyResult:
    """Outcome of verifying one ledger file."""

    path: str
    total_lines: int = 0
    chained_from: int | None = None  # 1-indexed line where the chain starts; None if unchained
    breaks: list[tuple[int, str]] = field(default_factory=list)  # (line_no, reason)

    @property
    def ok(self) -> bool:
        return not self.breaks


def verify_chain(path: Path) -> VerifyResult:
    """Verify the hash-chain of ``path``. Never raises for content issues — reports them as breaks."""
    result = VerifyResult(path=str(path))
    if not path.is_file():
        result.breaks.append((0, "file not found"))
        return result

    prev_raw: str | None = None
    started = False  # have we reached the first chained record yet?
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if not raw.strip():
                # A blank interior line alters the "previous raw line" seen by the next record.
                # The appender never emits one, so treat it as a break once the chain has started.
                if started:
                    result.breaks.append((line_no, "unexpected blank line inside chain"))
                continue
            result.total_lines += 1
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                if started:
                    result.breaks.append((line_no, "unparseable JSON inside chain"))
                prev_raw = raw
                continue

            declared = record.get("prev_sha256") if isinstance(record, dict) else None
            if declared is None:
                if started:
                    # A chained region must not regress to unchained records.
                    result.breaks.append((line_no, "missing prev_sha256 after chain started"))
                prev_raw = raw
                continue

            if not started:
                started = True
                result.chained_from = line_no

            expected = GENESIS_HASH if prev_raw is None else _line_sha256(prev_raw)
            if declared != expected:
                result.breaks.append(
                    (
                        line_no,
                        f"prev_sha256 mismatch (declared {declared[:12]}…, expected {expected[:12]}…)",
                    )
                )
            prev_raw = raw

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.ledger_verify", description=__doc__)
    parser.add_argument("path", help="Path to the JSONL ledger to verify")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON")
    args = parser.parse_args(argv)

    result = verify_chain(Path(args.path))

    if args.json:
        payload = asdict(result)
        payload["ok"] = result.ok
        print(json.dumps(payload, ensure_ascii=False))
    elif not Path(args.path).is_file():
        print(f"ledger-verify: {args.path}: file not found", file=sys.stderr)
        return 2
    elif result.ok:
        span = (
            f"from line {result.chained_from}"
            if result.chained_from
            else "no chained records (legacy file)"
        )
        print(f"OK — {result.total_lines} lines, chain intact ({span}).")
    else:
        print(f"BREAK — {len(result.breaks)} issue(s) in {args.path}:", file=sys.stderr)
        for line_no, reason in result.breaks:
            print(f"  line {line_no}: {reason}", file=sys.stderr)

    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
