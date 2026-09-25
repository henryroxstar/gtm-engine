"""Where you stand: how many can go out, and if none, why — one answer for both surfaces.

PS15, the prospecting status lede. Three parts, one home:

* :func:`compute_readiness` — every row on the send list gets exactly one **fate**, found by
  the enrollment gate's own functions in the gate's own order (``account_integrity.main``):
  lane state → account status → suppression → verdict filter → content audit. The gate is
  NOT modified and NOT re-implemented; this calls it piece by piece, per lane, because the
  send list is mixed-lane and the gate is run once per lane. Conservation is asserted —
  ``rows`` equals the sum of the fates — so a row can never disappear from the count.
  ``preflight_report`` stores the result in its report; nothing else calls this.
* :func:`load_readiness` — reads that stored result back, and says **stale** when any file
  the gate reads has changed since. A stale pass is never rendered as a pass.

The operator's WORDS for all of this — the lede both surfaces print, and the sentence for
each refusal — live in :mod:`gtm_core.prospect_lede`. This module computes, stores and
loads; that one only says.

Why the answer lives in the report and not in the status command: the status block used to
re-run the audit itself (``_checked_count``) and keep only a number. A lane that failed its
audit contributed zero, so a refused batch and an unchecked one both read "0", and the
reasons the audit had just computed were thrown away. That was the zero a first-time reader
could not act on.

Rule ids in the stored report are data (§R5): the lede only ever LOOKS THEM UP
(``prospect_lede.REFUSAL_COPY``). An id not in the table renders a fixed sentence, never the id.
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .paths import _safe_segment, resolve_content_root
from .prospect_paths import evals_dir, ready_to_load, suppression_ledger
from .prospects_state import latest_path

#: Every fate a row on the send list can have, in the order the lede lists them. Exactly
#: one per row; :func:`verify_readiness_conservation` holds the sum to the row count.
FATES: tuple[str, ...] = (
    "admitted",  # would be admitted by the gate today
    "refused",  # in a batch the gate refuses — the reason is recorded per batch
    "not_scored",  # no score yet, and this kind of email needs one
    "not_admitted",  # scored, but for a different kind of email than its batch sends
    "judge_dropped",  # removed by a calibrated email judge
    "set_aside",  # waiting on the operator's decision, or deliberately not emailed
    "suppressed",  # asked not to be contacted
    "not_sorted",  # the list was built before these rows were sorted
)

_PARKED = frozenset({"hold", "excluded"})

#: Refusal classes that are not content-audit rules: the gate refuses the whole batch
#: before reading any content.
ACCOUNT_STATUS = "account-status"
LANE_STATE = "lane-state"
RECORD_COLUMNS = "record-columns-missing"
WARNING_BUDGET = "warning-budget"

# --- the files the gate reads, fingerprinted -------------------------------------------


def _today() -> datetime.date:
    """The day an answer is for. One function, so a test can pin it and both halves agree."""
    return datetime.date.today()


def input_paths(profile: str, content_root: Path) -> dict[str, Path]:
    """The files whose change most often changes the gate's answer: the list, how it was
    sorted, the account ledger, and the suppression ledger. One list, used to stamp the
    answer and to test it for staleness, so the two cannot name different files.

    Not every input is here, and the gap is stated rather than hidden. Account research
    files and profile knowledge (competitor lists, the rubric) also feed the audit, and the
    date does too — a reason to write ages out day by day. The date is covered separately:
    an answer measured on another day reads as stale (:func:`load_readiness`). Research and
    knowledge edits are not fingerprinted — there are thousands of research files — so a
    same-day edit there can leave a count out of date until the checks run again. That can
    only mislead the lede, never the send: the enrollment gate runs again, on its own, at
    the moment anything is loaded."""
    return {
        "list": ready_to_load(profile, content_root),
        "sorted": evals_dir(profile, content_root) / "lanes-state.jsonl",
        "ledger": latest_path(profile, content_root),
        "suppression": suppression_ledger(profile, content_root),
    }


def _fingerprint(path: Path) -> list[int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size]


def fingerprints(profile: str, content_root: Path) -> dict[str, list[int] | None]:
    return {k: _fingerprint(p) for k, p in input_paths(profile, content_root).items()}


# --- compute (called by preflight_report only) -----------------------------------------


_BLOCKED_COUNT_RE = re.compile(r"^REFUSED: (\d+) row\(s\) belong to accounts")


def _fates() -> dict[str, int]:
    return dict.fromkeys(FATES, 0)


def _refuse_all(lane: str, group: list[dict], suppressed: int, rule: str, count: int) -> dict:
    fates = _fates()
    fates["suppressed"] = suppressed
    fates["refused"] = len(group) - suppressed
    return {
        "lane": lane,
        "rows": len(group),
        "fates": fates,
        "refused_because": [{"rule": rule, "count": count, "unit": "row"}],
    }


def _lane_readiness(
    lane: str,
    group: list[dict],
    *,
    profile: str,
    content_root: Path,
    profiles_root: Path | None,
    fieldnames: list[str],
    acked: tuple[str, ...],
    budget: int,
    as_of: datetime.date | None,
    suppression_index,
) -> dict:
    """One batch's fates, found by the gate's functions in ``account_integrity.main``'s order."""
    from .account_integrity import _ROW_LEVEL_RULES, LANE_VERDICTS, audit_rows, filter_by_verdict
    from .enrollment_gate import check_account_status, check_enrollment_lanes
    from .finding_budget import group_by_rule

    rows = [dict(r) for r in group]  # the gate stamps `lane` onto rows; never mutate the caller's
    is_suppressed = [
        bool((r.get("suppression") or "").strip()) or bool(suppression_index.match(r)) for r in rows
    ]
    suppressed = sum(is_suppressed)

    if lane == "":
        fates = _fates()
        fates["suppressed"] = suppressed
        fates["not_sorted"] = len(rows) - suppressed
        return {"lane": lane, "rows": len(rows), "fates": fates, "refused_because": []}
    if lane in _PARKED:
        fates = _fates()
        fates["suppressed"] = suppressed
        fates["set_aside"] = len(rows) - suppressed
        return {"lane": lane, "rows": len(rows), "fates": fates, "refused_because": []}
    if lane not in LANE_VERDICTS:
        return _refuse_all(lane, rows, suppressed, LANE_STATE, len(rows) - suppressed)

    # 1-2. Whole-batch refusals, before any content is read — exactly as the gate.
    refusal, _resolved = check_enrollment_lanes(
        rows, profile, lane, fieldnames, want="send", content_root=content_root
    )
    if refusal:
        return _refuse_all(lane, rows, suppressed, LANE_STATE, len(rows) - suppressed)
    refusal = check_account_status(rows, profile, content_root)
    if refusal:
        hit = _BLOCKED_COUNT_RE.match(refusal)
        count = int(hit.group(1)) if hit else len(rows) - suppressed
        return _refuse_all(lane, rows, suppressed, ACCOUNT_STATUS, count)

    # 3. Suppression.
    live = [r for r, s in zip(rows, is_suppressed, strict=True) if not s]

    # 4. The verdict filter. It drops an inadmissible row without counting it, so the two
    # kinds of drop are counted here, against the lane's admissible set, to keep every row.
    kept, vstats = filter_by_verdict(live, "send", lane=lane)
    wanted = LANE_VERDICTS[lane]
    inadmissible = [r for r in live if (r.get("verdict") or "").strip().lower() not in wanted]
    not_scored = sum(1 for r in inadmissible if not (r.get("verdict") or "").strip())

    # 5. The content audit — on the kept rows only, as the gate.
    audit = audit_rows(
        kept,
        profile,
        content_root,
        profiles_root,
        fieldnames=fieldnames,
        acked=acked,
        budget=budget,
        as_of=as_of,
        lane=lane,
    )
    fates = _fates()
    fates["suppressed"] = suppressed
    fates["not_scored"] = not_scored
    fates["not_admitted"] = len(inadmissible) - not_scored
    fates["judge_dropped"] = vstats.judge_dropped
    because: list[dict] = []
    if audit.failed:
        fates["refused"] = vstats.kept
        ranked = sorted(
            ((rule, len(found)) for rule, found in group_by_rule(audit.errors).items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
        # Domain and record findings are per row; dossier, competitor and leadership
        # findings are per account (``AccountAudit``). The unit travels with the count.
        because = [
            {"rule": rule, "count": n, "unit": "row" if rule in _ROW_LEVEL_RULES else "account"}
            for rule, n in ranked
        ]
        if audit.record_missing_columns:
            because.insert(0, {"rule": RECORD_COLUMNS, "count": vstats.kept, "unit": "row"})
        if audit.warn_verdict.blocked:
            because.append(
                {"rule": WARNING_BUDGET, "count": audit.warn_verdict.unacked, "unit": "warning"}
            )
    else:
        fates["admitted"] = vstats.kept
    return {"lane": lane, "rows": len(rows), "fates": fates, "refused_because": because[:5]}


def verify_readiness_conservation(readiness: dict) -> bool:
    """Every row has exactly one fate: per batch and in total. Raises ``ValueError``.

    The same shape as ``prospect_status_receipt.verify_funnel_conservation``, one level
    down: that one holds accounts to their buckets, this one holds send-list rows to their
    fates. A count that silently lost rows is the failure this exists to make impossible.
    """
    total = 0
    for lane in readiness.get("lanes", []):
        fates = lane["fates"]
        if sum(fates.values()) != lane["rows"]:
            raise ValueError(
                f"Readiness conservation violated in batch {lane['lane']!r}: "
                f"{lane['rows']} rows but the fates sum to {sum(fates.values())} ({fates})"
            )
        total += lane["rows"]
    if total != readiness["rows"] or sum(readiness["fates"].values()) != readiness["rows"]:
        raise ValueError(
            f"Readiness conservation violated: {readiness['rows']} rows on the list, "
            f"batches hold {total}, fates sum to {sum(readiness['fates'].values())}"
        )
    return True


def compute_readiness(
    profile: str,
    rows: list[dict],
    fieldnames: list[str],
    groups: Sequence[tuple[str, list[dict]]],
    *,
    content_root: Path,
    profiles_root: Path | None = None,
    acked: tuple[str, ...] = (),
    budget: int | None = None,
    as_of: datetime.date | None = None,
) -> dict:
    """Every row's fate on the send list, per batch, with the refusal classes of any batch
    the gate would refuse. ``groups`` is ``preflight_report._lane_groups(rows)`` — passed
    in, so the partition has one implementation and this module no import cycle."""
    from .finding_budget import WARN_BUDGET
    from .suppression import load_index

    index = load_index(suppression_ledger(profile, content_root))
    lanes = [
        _lane_readiness(
            lane,
            group,
            profile=profile,
            content_root=content_root,
            profiles_root=profiles_root,
            fieldnames=fieldnames,
            acked=acked,
            budget=WARN_BUDGET if budget is None else budget,
            as_of=as_of,
            suppression_index=index,
        )
        for lane, group in groups
    ]
    fates = _fates()
    for lane in lanes:
        for k, v in lane["fates"].items():
            fates[k] += v
    readiness = {
        "as_of": (as_of or _today()).isoformat(),
        "rows": len(rows),
        "fates": fates,
        "lanes": lanes,
        "inputs": fingerprints(profile, content_root),
    }
    verify_readiness_conservation(readiness)
    return readiness


def readiness_or_error(*args, **kwargs) -> dict:
    """:func:`compute_readiness`, never raising — for the check report, where one failure must
    not hide the rest. A failure is RECORDED as ``{"error": …}``, so every reader shows
    "unknown" and never a count that quietly shrank."""
    try:
        return compute_readiness(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed into a number
        return {"error": f"readiness could not be computed: {type(exc).__name__}: {exc}"}


# --- load (status CLI + dashboard) -----------------------------------------------------


@dataclass
class Readiness:
    """What the lede needs. ``state`` is the one field every renderer branches on:

    * ``ok`` — the stored answer is current;
    * ``stale`` — a file the gate reads changed after the checks ran;
    * ``none`` — no answer is stored (the checks have not run, or no list exists);
    * ``unreadable`` — an answer is stored but cannot be trusted (``problem`` says why).

    Only ``ok`` may render a number of emails that can go out.
    """

    state: str
    ran_at: str = ""
    rows: int = 0
    fates: dict[str, int] = field(default_factory=dict)
    #: ``(batch, rows held, [(rule, count, unit), …])`` per refused batch — EVERY class the
    #: gate refused it on, ranked. Showing only the largest would imply that fixing it unblocks
    #: the batch; on the first live run a batch of 413 was held by one error AND a warning pile.
    refusals: list[tuple[str, int, list[tuple[str, int, str]]]] = field(default_factory=list)
    problem: str = ""

    @property
    def admitted(self) -> int | None:
        return self.fates.get("admitted") if self.state == "ok" else None


def report_path(profile: str, content_root: Path) -> Path:
    """Where ``preflight_report.write_report`` puts the latest report — one spelling.

    The profile is guarded like every other path segment (``prospect_paths`` does the same):
    a profile name is operator input, and this path is both read and written."""
    return content_root / _safe_segment(profile, "profile") / "preflight" / "latest.json"


def _int(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"expected a non-negative count, got {v!r}")
    return v


_UNITS = frozenset({"row", "account", "warning"})


def _parse(block: object) -> tuple[int, dict[str, int], list]:
    """Strictly validate the stored block. Any wrong shape raises — it is data (§R5)."""
    if not isinstance(block, dict):
        raise ValueError("readiness is not an object")
    rows = _int(block.get("rows"))
    raw = block.get("fates")
    if not isinstance(raw, dict) or set(raw) != set(FATES):
        raise ValueError("readiness fates are missing or unexpected")
    fates = {k: _int(raw[k]) for k in FATES}
    lanes = block.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("readiness batches are missing")
    refusals: list[tuple[str, int, list[tuple[str, int, str]]]] = []
    for lane in lanes:
        if not isinstance(lane, dict) or not isinstance(lane.get("lane"), str):
            raise ValueError("a readiness batch is malformed")
        lane_fates = lane.get("fates")
        if not isinstance(lane_fates, dict):
            raise ValueError("a readiness batch has no fates")
        refused = _int(lane_fates.get("refused", 0))
        because = lane.get("refused_because") or []
        if not isinstance(because, list):
            raise ValueError("a refusal list is malformed")
        classes: list[tuple[str, int, str]] = []
        for entry in because:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("rule"), str)
                or entry.get("unit") not in _UNITS
            ):
                raise ValueError("a refusal entry is malformed")
            classes.append((entry["rule"], _int(entry.get("count")), entry["unit"]))
        if refused:
            # A refused batch with no recorded class still says it was refused (the lede
            # renders the fixed "cannot name" sentence) rather than vanishing from the reasons.
            refusals.append((lane["lane"], refused, classes))
    if sum(fates.values()) != rows:
        raise ValueError("readiness fates do not add up to the list")
    return rows, fates, refusals


def load_readiness(profile: str, content_root: Path | None = None) -> Readiness:
    """The stored answer, checked for staleness. Never raises: every failure is a state."""
    root = content_root if content_root is not None else resolve_content_root()
    path = report_path(profile, root)
    if not path.is_file():
        return Readiness(state="none")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("the report is not an object")
        block = report.get("readiness")
        if block is None:
            # A report from before PS15, or one written with no list staged.
            return Readiness(state="none")
        if isinstance(block, dict) and "error" in block:
            return Readiness(state="unreadable", problem=str(block["error"])[:200])
        rows, fates, refusals = _parse(block)
        ran_at = report.get("ran_at")
        if not isinstance(ran_at, str):
            raise ValueError("the report has no run time")
    except (OSError, ValueError) as exc:  # ValueError covers JSON and unicode errors
        return Readiness(state="unreadable", problem=f"{path.name}: {exc}"[:200])
    current = (
        block.get("inputs") == fingerprints(profile, root)
        and block.get("as_of") == _today().isoformat()
    )
    state = "ok" if current else "stale"
    return Readiness(state=state, ran_at=ran_at, rows=rows, fates=fates, refusals=refusals)
