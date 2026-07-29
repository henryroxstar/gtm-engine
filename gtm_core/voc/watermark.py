"""Per-source pull watermarks + minimum-window policy for the market-intelligence harvest.

The collector (:mod:`gtm_core.voc.collect`) answers *how old is the newest artifact* — a
**staleness** verdict on what is already on disk. This module answers the question that comes
one step earlier: **what window should the next pull actually request?**

Running ad hoc makes fixed cadences the wrong abstraction (PRD §8.3). Each pull lane stores
``last_pulled_at`` and asks for everything since that watermark — a run three days later gets
three days, a run three months later gets three months.

Two corrections to naive "since last time":

1. **Minimum windows.** The real failure mode is not a wrong cadence, it is *a short window on a
   slow source reading as an absence of signal*. A 7-day window on SEC EDGAR returns nothing,
   which renders as "no enterprise signal" when it means "wrong window." Slow lanes therefore
   carry a floor: the requested window is widened to it even when the watermark is recent.
2. **Archive caps.** Syften's Standard plan keeps 30 days. A watermark older than that cannot be
   served — the missed window is gone for good. The plan reports ``data_loss`` rather than
   quietly clipping, because a silent clip is indistinguishable from "nothing happened."

**A failed pull never advances the watermark.** Advancing on failure would swallow the window:
the next run would ask only for the time since the failure, and everything the failed run should
have covered would never be requested again by anything. ``record()`` enforces this.

Coverage honesty (PRD §8.3): a lane that returns nothing must be distinguishable from a lane that
was never pulled and from a lane whose pull failed. Three different facts; one blank renders all
three identically and quietly overstates coverage. :func:`coverage_state` names which one applies.

CLI::

    python -m gtm_core.voc.watermark --profile P                    # the pull plan (all lanes)
    python -m gtm_core.voc.watermark --profile P --lane standards_watch
    python -m gtm_core.voc.watermark --profile P --record standards_watch --status ok --items 12
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..paths import PathConfig, _safe_segment

# Pull outcomes. `ok` and `empty` both mean *the window was covered*; only `failed` means it
# was not. That distinction is the whole reason this vocabulary is closed rather than free text.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
VALID_STATUSES = frozenset({STATUS_OK, STATUS_EMPTY, STATUS_FAILED})

# Statuses that mean the window really was covered, so the watermark may advance past it.
_ADVANCING_STATUSES = frozenset({STATUS_OK, STATUS_EMPTY})


@dataclass(frozen=True)
class WindowPolicy:
    """How wide a window one pull lane should request.

    ``min_days`` is a **floor** (widen a too-recent watermark — slow sources); ``max_days`` is a
    **cap** imposed by the upstream archive (we cannot ask for more, however long the gap).
    ``first_run_days`` is the cold-start window when no watermark exists yet.
    """

    lane: str
    source_id: str  # the gtm_core.voc.collect source this lane feeds
    label: str
    first_run_days: int
    min_days: int | None = None
    max_days: int | None = None
    note: str = ""


# The lanes of the market harvest, mapped onto the collector's source ids. Lanes are FINER than
# sources on purpose: `category_frameworks` and `vendor_watch` are pulled from different places at
# different velocities but land in the one `market_intel_digest` artifact, so they need separate
# watermarks and a shared destination. Windows are PRD §8.3.
POLICIES: tuple[WindowPolicy, ...] = (
    WindowPolicy(
        lane="syften_market_signals",
        source_id="syften_market_signals",
        label="Syften social listening",
        first_run_days=30,
        max_days=30,
        note="Standard-plan archive is 30 days. A watermark older than that cannot be served — "
        "the intervening window is unrecoverable, so the plan reports data_loss instead of "
        "silently clipping. Raw pulls kept on disk are the only mitigation.",
    ),
    WindowPolicy(
        lane="standards_watch",
        source_id="standards_watch",
        label="Standards & spec watch (MCP / A2A / W3C)",
        first_run_days=30,
        min_days=14,
        note="Spec repos move weekly; a floor of 14 days keeps a same-week re-run from returning "
        "an empty diff that would read as 'the standards went quiet'.",
    ),
    WindowPolicy(
        lane="enterprise_filings",
        source_id="enterprise_filings",
        label="Enterprise filings (SEC EDGAR full-text)",
        first_run_days=300,
        min_days=90,
        note="Filings are quarterly/annual and cluster Feb–Apr. Any window under a quarter is "
        "meaningless — it returns nothing, which reads as 'no enterprise signal'.",
    ),
    WindowPolicy(
        lane="category_frameworks",
        source_id="market_intel_digest",
        label="Category frameworks (OWASP / CSA / NHI Mgmt Group)",
        first_run_days=180,
        min_days=90,
        note="Framework bodies publish roughly monthly and round up quarterly.",
    ),
    WindowPolicy(
        lane="vendor_watch",
        source_id="market_intel_digest",
        label="Competitor changelogs & vendor blogs",
        first_run_days=90,
        note="Git-backed and continuous — no floor needed; whatever the gap is, is the window.",
    ),
)

_BY_LANE: dict[str, WindowPolicy] = {p.lane: p for p in POLICIES}


def policy(lane: str) -> WindowPolicy:
    """Look up a lane's window policy. Raises ``KeyError`` for an unknown lane — a typo must
    fail loudly rather than silently pull an unbounded window."""
    return _BY_LANE[lane]


def state_path(content_root: Path, profile: str) -> Path:
    """Where *profile*'s watermarks live."""
    prof = _safe_segment(profile, "profile")
    return content_root / prof / "market-signals" / "watermarks.json"


def load(path: Path) -> dict:
    """Read the watermark state. Missing/corrupt file → empty state, never an error: a first
    run and an unreadable file both mean "we have no watermark", which is a valid starting point."""
    empty: dict = {"kind": "voc-watermarks", "lanes": {}}
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict):
        return empty
    data.setdefault("kind", "voc-watermarks")
    return data


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entry(state: dict, lane: str) -> dict:
    lanes = state.get("lanes")
    entry = lanes.get(lane) if isinstance(lanes, dict) else None
    return entry if isinstance(entry, dict) else {}


def _iso(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def window_for(state: dict, lane: str, today: date | None = None) -> dict:
    """The window the next pull of *lane* should request.

    Returns the resolved ``since``/``until`` plus **why** it is that width, so the harvest can
    state its own window honestly instead of implying it covered more than it asked for.
    """
    today = today or date.today()
    pol = policy(lane)
    entry = _entry(state, lane)
    mark = _iso(entry.get("last_pulled_at"))

    if mark is None:
        since = today - timedelta(days=pol.first_run_days)
        basis = "first-run"
    else:
        since = mark
        basis = "watermark"

    floor_applied = False
    if pol.min_days is not None:
        floor = today - timedelta(days=pol.min_days)
        if since > floor:
            since = floor
            floor_applied = True

    capped = False
    data_loss_days = 0
    if pol.max_days is not None:
        cap = today - timedelta(days=pol.max_days)
        if since < cap:
            data_loss_days = (cap - since).days
            since = cap
            capped = True

    warnings: list[str] = []
    if capped:
        warnings.append(
            f"{data_loss_days}d of this lane's gap is older than the {pol.max_days}d upstream "
            "archive and can no longer be served — that window is lost, not empty."
        )
    if entry.get("last_status") == STATUS_FAILED:
        warnings.append(
            "The previous pull FAILED; the watermark was deliberately not advanced, so this "
            "window still includes the period that failed."
        )

    return {
        "lane": lane,
        "source_id": pol.source_id,
        "label": pol.label,
        "since": since.isoformat(),
        "until": today.isoformat(),
        "days": (today - since).days,
        "basis": basis,
        "floor_applied": floor_applied,
        "capped": capped,
        "data_loss": capped,
        "data_loss_days": data_loss_days,
        "last_pulled_at": entry.get("last_pulled_at"),
        "last_status": entry.get("last_status"),
        "coverage_state": coverage_state(state, lane),
        "warnings": warnings,
        "policy_note": pol.note,
    }


def coverage_state(state: dict, lane: str) -> str:
    """Which of the four coverage facts applies to *lane* right now.

    ``not-pulled`` (never run) / ``pull-failed`` (ran, errored — the window is NOT covered) /
    ``nothing-new-since`` (ran, window covered, zero items) / ``current`` (ran, found items).
    The brief must name which; a single blank renders all four identically.
    """
    entry = _entry(state, lane)
    status = entry.get("last_status")
    if not entry or status is None:
        return "not-pulled"
    if status == STATUS_FAILED:
        return "pull-failed"
    if status == STATUS_EMPTY:
        return "nothing-new-since"
    return "current"


def record(
    state: dict,
    lane: str,
    *,
    status: str,
    today: date | None = None,
    items: int | None = None,
    note: str = "",
) -> dict:
    """Record a pull outcome for *lane*, returning the updated state (mutated in place).

    **The watermark advances only on ``ok``/``empty``.** A failed pull leaves it where it was, so
    the window it should have covered is re-requested next run instead of being silently skipped.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r} (expected one of {sorted(VALID_STATUSES)})")
    policy(lane)  # reject an unknown lane before writing anything
    today = today or date.today()
    stamp = today.isoformat()

    lanes = state.setdefault("lanes", {})
    entry = lanes.setdefault(lane, {})
    entry["last_status"] = status
    entry["last_attempt_at"] = stamp
    if note:
        entry["note"] = note
    if items is not None:
        entry["last_items"] = items

    if status in _ADVANCING_STATUSES:
        entry["last_pulled_at"] = stamp
        entry.pop("failed_since", None)
    else:
        # Remember when the covered window last ended so the gap is visible in the plan.
        entry.setdefault("failed_since", entry.get("last_pulled_at"))

    state["kind"] = "voc-watermarks"
    state["updated"] = stamp
    return state


def plan(state: dict, today: date | None = None) -> dict:
    """The full pull plan — one window per lane, plus the lanes needing attention."""
    today = today or date.today()
    windows = [window_for(state, p.lane, today) for p in POLICIES]
    return {
        "kind": "voc-pull-plan",
        "as_of": today.isoformat(),
        "lanes": windows,
        "summary": {
            "never_pulled": [w["lane"] for w in windows if w["coverage_state"] == "not-pulled"],
            "failed": [w["lane"] for w in windows if w["coverage_state"] == "pull-failed"],
            "data_loss": [w["lane"] for w in windows if w["data_loss"]],
            "floor_applied": [w["lane"] for w in windows if w["floor_applied"]],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.watermark",
        description="Per-lane pull windows for the market harvest; record pull outcomes.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--lane", default=None, help="Show the window for one lane only.")
    parser.add_argument("--record", default=None, metavar="LANE", help="Record a pull outcome.")
    parser.add_argument("--status", default=None, choices=sorted(VALID_STATUSES))
    parser.add_argument("--items", type=int, default=None)
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Date the pull actually covered up to (default today). Use when backfilling a pull "
        "that ran earlier — stamping it as today would claim a window that was never requested.",
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    try:
        path = state_path(cfg.content_root, args.profile)
    except ValueError as exc:
        raise SystemExit(f"[voc-watermark] {exc}") from exc

    state = load(path)

    if args.record:
        if not args.status:
            raise SystemExit("[voc-watermark] --record requires --status")
        try:
            stamp = date.fromisoformat(args.date) if args.date else None
        except ValueError as exc:
            raise SystemExit(f"[voc-watermark] --date must be YYYY-MM-DD: {exc}") from exc
        try:
            record(
                state,
                args.record,
                status=args.status,
                today=stamp,
                items=args.items,
                note=args.note,
            )
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"[voc-watermark] {exc}") from exc
        save(path, state)
        print(json.dumps(window_for(state, args.record), ensure_ascii=False, indent=2))
        return 0

    if args.lane:
        try:
            result = window_for(state, args.lane)
        except KeyError as exc:
            raise SystemExit(
                f"[voc-watermark] unknown lane {args.lane!r}; expected one of {sorted(_BY_LANE)}"
            ) from exc
    else:
        result = plan(state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
