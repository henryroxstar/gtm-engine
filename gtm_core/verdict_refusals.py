"""What the account-integrity gate says about the rows it did NOT keep.

:mod:`gtm_core.account_integrity` owns the rule (which verdicts may enrol in which lane,
when a judge's ``drop`` binds). This module owns the *honesty* of reporting it, split out so
the gate stays inside its §R10 ceiling:

* :func:`inadmissible_findings` — ``--lane`` without ``--require-verdict`` used to check no
  verdicts at all, so ``--lane generic`` printed PASS over a row at ``verdict: drop``. The
  operator asked for an audit of THIS file, so nothing is filtered: each inadmissible row is
  an ERROR that names it.
* :func:`refused_rows` / :func:`render_refused` — with ``--require-verdict`` the gate filters
  in memory and audits the survivors. It named none of the rows it removed.
* :func:`kept_path_refusal` / :func:`write_kept` / :func:`pass_line` — and then it printed
  ``PASS`` while the CSV on disk still held every refused row, so the natural next action
  (load the file that "passed") enrolled exactly the rows the gate had refused. The kept
  rows can now be written out, and the PASS line says which file it is a PASS *for*.

No rule lives here: every function is handed the admissible set or the already-filtered
rows. One filter implementation (``account_integrity.filter_by_verdict``), reported on.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from . import fsio

__all__ = [
    "EMPTY_LIST_FAIL",
    "REFUSED_CAP",
    "flag_refusal",
    "inadmissible_findings",
    "kept_path_refusal",
    "pass_line",
    "read_list",
    "refused_lines",
    "refused_rows",
    "render_refused",
    "write_kept",
]

#: The gate's last line for a list with no rows at enrollment. It used to end in ``PASS``,
#: which every reader — a person or the next step — takes to mean "enrol this".
EMPTY_LIST_FAIL = "FAIL — the list is empty; nothing to enrol"

#: How many refused rows are named before the listing collapses to "+N more". The point is
#: that the operator can see WHICH rows; past a screenful nobody reads them, and the kept
#: file is the complete answer anyway.
REFUSED_CAP = 20


def _verdict(row: dict) -> str:
    return (row.get("verdict") or "").strip().lower()


def _who(row: dict) -> str:
    return (row.get("email") or row.get("company") or "?").strip()


def _admits(wanted: frozenset[str]) -> str:
    return "/".join(sorted(v or "(empty)" for v in wanted)) or "nothing"


def inadmissible_findings(rows: list[dict], lane: str, wanted: frozenset[str]) -> list[str]:
    """One ``verdict-inadmissible`` ERROR line per row whose verdict may not enrol in ``lane``.

    An EMPTY verdict is deliberately not reported here: where the lane does not admit it, it
    is already ``verdict-missing`` (an ERROR in every lane that refuses it), and saying the
    same thing twice per row is how a findings block stops being read.
    """
    out: list[str] = []
    for r in rows:
        verdict = _verdict(r)
        if verdict and verdict not in wanted:
            out.append(
                f"verdict-inadmissible: {_who(r)} — verdict {verdict!r} may not enrol in lane "
                f"{lane!r} (admits {_admits(wanted)}); remove the row or re-route it"
            )
    return out


def refused_rows(
    before: list[dict], kept: list[dict], wanted: frozenset[str]
) -> list[tuple[str, str]]:
    """``(who, why)`` for every row the verdict filter removed, in file order.

    ``why`` is derived from the row, not re-decided: a row whose verdict is outside
    ``wanted`` was refused on its verdict; any other removed row can only have been the
    calibrated judge's ``drop`` (the filter has exactly those two exits).
    """
    kept_ids = {id(r) for r in kept}
    out: list[tuple[str, str]] = []
    for r in before:
        if id(r) in kept_ids:
            continue
        verdict = _verdict(r)
        if verdict not in wanted:
            out.append((_who(r), f"verdict {verdict or '(empty)'!r}"))
        else:
            out.append((_who(r), f"verdict {verdict!r}, judge drop (calibrated)"))
    return out


def render_refused(refused: list[tuple[str, str]], cap: int = REFUSED_CAP) -> list[str]:
    lines = [f"  refused: {who} — {why}" for who, why in refused[:cap]]
    if len(refused) > cap:
        lines.append(f"  +{len(refused) - cap} more refused row(s) not listed")
    return lines


def refused_lines(before: list[dict], kept: list[dict], wanted: frozenset[str]) -> list[str]:
    return render_refused(refused_rows(before, kept, wanted))


def read_list(csv_path: Path, enrolling: bool) -> tuple[list[str], list[dict], str | None]:
    """``(columns, rows, fail line)`` — the fail line is set only for an EMPTY list at
    enrollment (``--require-verdict``). A plain audit of an empty file finds nothing, which
    is true; "PASS" as the answer to "may this list be enrolled?" is not."""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames, rows = list(reader.fieldnames or []), list(reader)
    return fieldnames, rows, EMPTY_LIST_FAIL if enrolling and not rows else None


def _not_beside_the_list(csv_path: Path, kept_path: Path) -> str | None:
    """Why the kept file may not be written where it was asked for, or ``None``.

    The kept file is named people at named companies, and the path is chosen by whoever — or
    whatever — runs the gate. It must land in the SAME folder as the list it filters. That is
    where it belongs (the list's own tenant folder, beside the file it replaces), and it is
    what stops one tenant's list being written into another tenant's folder, where the next
    run there would find it and load it. Confining to the resolved content root would not:
    every tenant's tree sits under that one root.
    """
    here = Path(csv_path).expanduser().resolve().parent
    there = Path(kept_path).expanduser().resolve().parent
    if here == there:
        return None
    return (
        f"REFUSED: --write-kept {str(kept_path)!r} is not beside the list it filters — write it "
        f"into {here} (a kept list in another folder is loaded by whatever run finds it there)"
    )


def flag_refusal(args: argparse.Namespace) -> str | None:
    """Why the gate's flags cannot be honoured together, or ``None``.

    ``--write-kept`` is documented as "the file to load"; ``--include-suppressed`` keeps the
    suppressed rows IN the audited set, so together they wrote opted-out people into it.
    """
    if args.write_kept is not None and args.include_suppressed:
        return (
            "REFUSED: --write-kept cannot be combined with --include-suppressed — the kept "
            "file is the one to load, and it would contain the suppressed rows"
        )
    if args.write_kept is not None:
        beside = _not_beside_the_list(args.csv, args.write_kept)
        if beside:
            return beside
    return kept_path_refusal(args.csv, args.write_kept, bool(args.require_verdict))


def kept_path_refusal(csv_path: Path, kept_path: Path | None, filtering: bool) -> str | None:
    """Why ``--write-kept`` cannot be honoured as given, or ``None``.

    Overwriting the input would destroy the only record of which rows were refused — and a
    crash between truncate and write would destroy the list. Compared resolved, and by
    inode when both exist, so ``./list.csv`` and a symlink are the same file.
    """
    if kept_path is None:
        return None
    if not filtering:
        return (
            "REFUSED: --write-kept needs --require-verdict — without it the gate filters no "
            "verdicts, so there is no kept set to write"
        )
    same = kept_path.resolve() == csv_path.resolve() or (
        kept_path.exists() and csv_path.exists() and kept_path.samefile(csv_path)
    )
    if same:
        return (
            f"REFUSED: --write-kept {str(kept_path)!r} is the input file — the kept rows are "
            f"written beside it, never over it"
        )
    return None


def write_kept(kept_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """The rows that survived suppression and the verdict filter — same columns, atomically
    (temp file + ``os.replace``), so a reader sees the old file or the new one."""
    fsio.atomic_write_csv(kept_path, fieldnames, rows)


def pass_line(kept: int, total: int, kept_path: Path | None) -> str:
    """The gate's final PASS line. Bare ``PASS`` when nothing was filtered out — otherwise
    it says what the PASS covers, because the input file is not it."""
    if kept >= total:
        return "PASS"
    head = (
        f"PASS (kept {kept} of {total}) — the input file still contains the "
        f"{total - kept} refused row(s); "
    )
    if kept_path is not None:
        return head + f"load {kept_path}"
    return head + "re-run with --write-kept <path> and load that file"
