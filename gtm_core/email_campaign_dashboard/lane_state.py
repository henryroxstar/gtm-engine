"""Reading `lanes-state.jsonl` for the page — the one file every lane-derived figure on it
comes from. Split out of `model.py` (§R10 ceiling), not rewritten.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..prospect_paths import evals_dir


class LaneStateUnreadable(ValueError):
    """`lanes-state.jsonl` exists but cannot be read. Every lane-derived number on the page
    comes from it, so the page is refused rather than rendered short."""


def _read_lane_state(profile: str, content_root: Path | None) -> list[dict]:
    """The router's last routing per email — `lanes route`'s own `lanes-state.jsonl`.

    Read directly rather than through `gtm_core.lanes.decisions.read_state`: that helper
    lives in the `lanes` package, whose `__init__` pulls in the router, the hold-decisions
    ledger and the hold sheet — dragging that in here to read one JSONL is the opposite of
    what this leaf-ish read needs. `evals_dir` is the same path resolver
    `prospect_status_cli` and `account_integrity` already use for this file — reused rather
    than re-spelled.

    A missing file returns no rows rather than raising: the page already has a "no data
    yet" contract for this (`views_status`'s status tiles), and a profile that has never
    run `lanes route` is not a broken page, it is a page with nothing routed yet.

    A MALFORMED line raises. It used to be skipped, on the grounds that `prospect_status_cli`
    skipped one too — that stopped being true on 2026-09-21, when the terminal block started
    refusing rather than printing a confident undercount. Leaving the page lenient would make
    the two surfaces disagree about the same file in exactly the way this whole change set
    exists to stop: every number the page derives from this file would be short by the
    unreadable rows, with nothing on the page saying so.
    """
    path = evals_dir(profile, content_root) / "lanes-state.jsonl"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        raise LaneStateUnreadable(f"{path} is not UTF-8 text (byte {err.start})") from err
    out: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError as err:
            raise LaneStateUnreadable(f"{path} line {number} is not JSON: {err}") from err
        if not isinstance(val, dict):
            raise LaneStateUnreadable(f"{path} line {number} is not a JSON object")
        out.append(val)
    return out
