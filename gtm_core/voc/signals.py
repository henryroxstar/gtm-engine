"""Signal records — the issue-shaped artifacts that ``market-intelligence`` emits.

A signal is one dated market event (funding, launch, enforcement, incident, etc.) that
``gtm_core.voc.delta`` compares issue-to-issue. Signals are **judged**, not computed:
``direction`` and ``triage`` are model/operator assessments and render with that label
attached, while breadth/confidence stay mechanical.

This module owns the schema, validation, and persistence. It does not fetch anything.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from ..paths import _safe_segment

#: Capture lanes / sources a signal can belong to. Includes Phase-3 lanes so the schema
#: stays valid as new harvest lanes come online.
VALID_LANES = frozenset(
    {
        "web_sweep",
        "syften_market_signals",
        "enterprise_filings",
        "market_intel_digest",
        "standards_watch",
        "category_frameworks",
        "vendor_watch",
        "funding_and_ma",
        "regulatory_enforcement",
        "incidents_benchmarks",
        "own_product_watch",
        "customer_moves",
    }
)

VALID_SPEAKERS = frozenset(
    {
        "customer-voice",
        "bd-focus",
        "expert-lens",
        "standards-voice",
        "vendor-voice",
        "mixed",
        "own-voice",
        "regulator-voice",
        "account-event",
    }
)

VALID_DIRECTIONS = frozenset({"threat", "validation", "opportunity", "neutral"})
VALID_TRIAGES = frozenset({"act-now", "watch", "ignore"})
VALID_FUNCTIONS = frozenset({"product", "marketing", "sales", "partnerships"})

# ``disposition`` records **what a reader should do next** about a claim — not what we concluded.
#
# An earlier three-value version (`confirmed` / `unconfirmed-lead` / `do-not-use`) was replaced
# because it failed in three ways at once: `confirmed` was just ``verified: true`` restated, so
# the field advertised information it did not carry; `unconfirmed-lead` hid three unrelated
# situations (no primary exists / not published yet / primary exists but blocked and we chose not
# to pay), so a reader had to ask a human which applied; and there was no slot at all for a claim
# a better source had **corrected** — the case where trade press said a round was "led by" an
# investor whose own site said "joining as a strategic investor".
#
# The replacement records the ACTION, and each non-empty value REQUIRES the field that makes it
# actionable. "Go read this document" is useful; "unconfirmed" is not.
DISPOSITION_NONE = ""  # verified and uncontested — the ordinary case, nothing to annotate
DISPOSITION_OPEN = "open"  # not usable yet; a NAMED document would settle it
DISPOSITION_REFUTED = "refuted"  # we looked; nothing supports it; stop repeating it
DISPOSITION_SUPERSEDED = "superseded"  # a better source corrected the claim as stated
VALID_DISPOSITIONS = frozenset(
    {DISPOSITION_NONE, DISPOSITION_OPEN, DISPOSITION_REFUTED, DISPOSITION_SUPERSEDED}
)

#: Which companion field each disposition requires. The point of the split: a label that does not
#: say *what to go read* or *what we already checked* leaves the next reader exactly where the
#: previous one started.
DISPOSITION_REQUIRES: dict[str, str] = {
    DISPOSITION_OPEN: "settled_by",
    DISPOSITION_REFUTED: "checked",
    DISPOSITION_SUPERSEDED: "superseded_by",
}

#: A signal's judged direction is always labelled as such — never merged with computed
#: confidence bands.
DIRECTION_BASIS_JUDGED = "judged"

_SIGNALS_DATE_RE = re.compile(r"signals-(\d{4}-\d{2}-\d{2})\.json$")


@dataclass(frozen=True)
class SignalRecord:
    """One market signal.

    ``verified`` follows the evidence-store rule: the primary source was actually read.
    ``disposition`` says what a reader should **do next** — empty (nothing to do, the ordinary
    case), ``open`` (go read ``settled_by``), ``refuted`` (stop repeating it; ``checked`` says
    what we already searched), or ``superseded`` (``superseded_by`` names the better source).
    Each non-empty value requires its companion field and forbids the other two, so a record
    can never say both "go read X" and "there is nothing to read".
    ``material`` means the signal is strong enough to appear in the issue body (as
    opposed to a log-only entry).
    """

    id: str
    title: str
    date: str  # ISO date of the event itself
    lane: str
    speaker: str
    entity: str
    url: str
    direction: str
    direction_basis: str = DIRECTION_BASIS_JUDGED
    material: bool = True
    verified: bool = False
    disposition: str = DISPOSITION_NONE
    settled_by: str = ""  # `open`: the exact document that would settle this, and its cost
    checked: str = ""  # `refuted`: what we actually checked, so nobody repeats the search
    superseded_by: str = ""  # `superseded`: the better source and what it says instead
    evidence_ids: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    decay_days: int = 30
    triage: str = "watch"

    @property
    def citable(self) -> bool:
        """Safe to quote in customer-facing material.

        Only a verified, un-annotated record qualifies. A ``superseded`` record stays
        uncitable even though it is ``verified``, because what was verified is that the claim
        **as stated** is wrong — the correction is citable, the record is not.
        """
        return self.verified and self.disposition == DISPOSITION_NONE

    def to_dict(self) -> dict:
        return asdict(self)


class SignalValidationError(ValueError):
    """A signal record failed schema validation."""


#: Shown for a rejected record whose ``id`` is itself missing or not a string — the reject still
#: has to be findable in the file, which is what ``SignalReject.index`` is for.
UNKNOWN_ID = "<no id>"


@dataclass(frozen=True)
class SignalReject:
    """One record that did not validate, and why.

    ``index`` is its position in the JSON array, because a record whose ``id`` is the broken
    field cannot be located by id.
    """

    index: int
    id: str
    error: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LoadReport:
    """What a signal file actually contained: what validated, what did not, and why.

    ``file_error`` is set when the file itself is unusable (missing, unreadable, not a JSON
    array) — distinct from a readable file whose records failed, which yields ``rejects``.
    """

    records: list[SignalRecord]
    rejects: list[SignalReject]
    file_error: str | None = None

    @property
    def total(self) -> int:
        """Records found in the file — validated plus rejected."""
        return len(self.records) + len(self.rejects)

    @property
    def ok(self) -> bool:
        return self.file_error is None and not self.rejects


def _raw_id(item: object) -> str:
    """Best-effort id for a record that failed to build."""
    if isinstance(item, dict):
        raw = item.get("id")
        if isinstance(raw, str) and raw.strip():
            return raw
    return UNKNOWN_ID


def _validate(signal: SignalRecord) -> None:
    """Raise ``SignalValidationError`` if *signal* violates the schema."""
    errors: list[str] = []
    if not signal.id:
        errors.append("id is required")
    try:
        date.fromisoformat(signal.date)
    except ValueError:
        errors.append(f"date must be ISO (YYYY-MM-DD), got {signal.date!r}")
    if signal.lane not in VALID_LANES:
        errors.append(f"unknown lane {signal.lane!r}")
    if signal.speaker not in VALID_SPEAKERS:
        errors.append(f"unknown speaker {signal.speaker!r}")
    if signal.direction not in VALID_DIRECTIONS:
        errors.append(f"unknown direction {signal.direction!r}")
    if signal.direction_basis != DIRECTION_BASIS_JUDGED:
        errors.append(
            f"direction_basis must be {DIRECTION_BASIS_JUDGED!r}, got {signal.direction_basis!r}"
        )
    if signal.triage not in VALID_TRIAGES:
        errors.append(f"unknown triage {signal.triage!r}")
    if signal.disposition not in VALID_DISPOSITIONS:
        errors.append(
            f"unknown disposition {signal.disposition!r} "
            f"(expected one of {sorted(VALID_DISPOSITIONS)})"
        )
    else:
        # An unverified claim must say WHICH kind of unusable it is. Leaving it blank is how a
        # retraction and a to-do came to render identically in the first place.
        if not signal.verified and signal.disposition == DISPOSITION_NONE:
            errors.append(
                "verified=False requires a disposition of "
                f"{DISPOSITION_OPEN!r} (a named document would settle it) or "
                f"{DISPOSITION_REFUTED!r} (we looked and nothing supports it) — an unusable "
                "claim must say which, because the reader's next action differs completely"
            )
        if signal.verified and signal.disposition == DISPOSITION_OPEN:
            errors.append(
                f"disposition={DISPOSITION_OPEN!r} contradicts verified=True — if the primary "
                "was read there is no document left to open"
            )
        # Each annotation must carry the field that makes it actionable, and must NOT carry the
        # others: a record saying both "go read X" and "there is nothing to read" is the exact
        # contradiction this vocabulary exists to prevent.
        companions = set(DISPOSITION_REQUIRES.values())
        required = DISPOSITION_REQUIRES.get(signal.disposition)
        if required and not getattr(signal, required, "").strip():
            errors.append(
                f"disposition={signal.disposition!r} requires a non-empty {required!r} — "
                "a label that does not say what to read, what was checked, or what corrected "
                "it leaves the next reader where the last one started"
            )
        for name in sorted(companions - {required} if required else companions):
            if getattr(signal, name, "").strip():
                errors.append(
                    f"{name!r} is set but disposition is {signal.disposition!r} — stale "
                    "annotations make a record contradict itself"
                )
    if signal.decay_days < 0:
        errors.append("decay_days must be non-negative")
    invalid_functions = [f for f in signal.functions if f not in VALID_FUNCTIONS]
    if invalid_functions:
        errors.append(f"unknown functions: {invalid_functions}")
    if not all(isinstance(e, str) for e in signal.evidence_ids):
        errors.append("evidence_ids must be strings")
    if errors:
        raise SignalValidationError(f"signal {signal.id!r}: " + "; ".join(errors))


def from_dict(raw: dict) -> SignalRecord:
    """Build a ``SignalRecord`` from a dict, validating every field."""
    if not isinstance(raw, dict):
        raise SignalValidationError(f"expected dict, got {type(raw).__name__}")
    required = (
        "id",
        "title",
        "date",
        "lane",
        "speaker",
        "entity",
        "url",
        "direction",
    )
    missing = [k for k in required if not isinstance(raw.get(k), str) or not raw.get(k)]
    if missing:
        raise SignalValidationError(f"missing required fields: {missing}")

    def _strs(key: str) -> list[str]:
        value = raw.get(key, [])
        if not isinstance(value, list):
            raise SignalValidationError(f"{key} must be a list")
        return [str(v) for v in value]

    signal = SignalRecord(
        id=raw["id"],
        title=raw["title"],
        date=raw["date"],
        lane=raw["lane"],
        speaker=raw["speaker"],
        entity=raw["entity"],
        url=raw["url"],
        direction=raw["direction"],
        direction_basis=str(raw.get("direction_basis", DIRECTION_BASIS_JUDGED)),
        material=bool(raw.get("material", True)),
        verified=bool(raw.get("verified", False)),
        disposition=str(raw.get("disposition", DISPOSITION_NONE)),
        settled_by=str(raw.get("settled_by", "")),
        checked=str(raw.get("checked", "")),
        superseded_by=str(raw.get("superseded_by", "")),
        evidence_ids=_strs("evidence_ids"),
        functions=_strs("functions"),
        decay_days=int(raw.get("decay_days", 30)),
        triage=str(raw.get("triage", "watch")),
    )
    _validate(signal)
    return signal


def to_dicts(signals: list[SignalRecord]) -> list[dict]:
    return [s.to_dict() for s in signals]


def store_dir(content_root: Path, profile: str) -> Path:
    """Directory that holds ``signals-<date>.json`` for *profile*."""
    prof = _safe_segment(profile, "profile")
    return content_root / prof / "plans" / "market-intelligence"


def store_path(content_root: Path, profile: str, as_of: date | None = None) -> Path:
    """Path for a signal file dated *as_of* (default today)."""
    as_of = as_of or date.today()
    return store_dir(content_root, profile) / f"signals-{as_of.isoformat()}.json"


def write(path: Path, signals: list[SignalRecord]) -> None:
    """Write *signals* as a JSON array, validating each record first."""
    for s in signals:
        _validate(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_dicts(signals), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def content_signals_path(content_root: Path, profile: str, as_of: date | None = None) -> Path:
    """Path for the content-radar handoff file dated *as_of* (default today)."""
    as_of = as_of or date.today()
    return store_dir(content_root, profile) / f"content-signals-{as_of.isoformat()}.json"


def load_content_signals(path: Path) -> list[dict]:
    """Read the content-radar handoff file. Missing or unreadable file → empty list.

    Each item must carry at least ``signal_id``, ``pillar``, and ``angle``;
    ``verified`` content signals are the ones content-radar should surface.
    """
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        if not all(
            isinstance(raw.get(k), str) and raw.get(k) for k in ("signal_id", "pillar", "angle")
        ):
            continue
        out.append(
            {
                "signal_id": str(raw["signal_id"]),
                "pillar": str(raw["pillar"]),
                "angle": str(raw["angle"]),
                "title": str(raw["title"]) if isinstance(raw.get("title"), str) else "",
                "url": str(raw["url"]) if isinstance(raw.get("url"), str) else "",
                "evidence_ids": [str(x) for x in raw.get("evidence_ids", []) if isinstance(x, str)],
                "verified": bool(raw.get("verified", False)),
                "decay_days": int(raw.get("decay_days", 30)),
            }
        )
    return out


def load_reporting(path: Path) -> LoadReport:
    """Read a signal file, keeping BOTH the records that validated and the ones that did not.

    ``load`` throws the rejects away, which is right for the read path and wrong for a gate:
    a caller that only ever sees survivors cannot tell a clean 70-record file from a 76-record
    file with 6 errors in it. Every reason a record or a file is unusable surfaces here, so
    ``--validate`` can report it instead of re-implementing the loop.
    """
    if not path.is_file():
        return LoadReport([], [], f"no such file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return LoadReport([], [], f"unreadable: {exc}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        return LoadReport([], [], f"not valid JSON: {exc}")
    if not isinstance(data, list):
        return LoadReport([], [], f"expected a JSON array of records, got {type(data).__name__}")
    records: list[SignalRecord] = []
    rejects: list[SignalReject] = []
    for index, item in enumerate(data):
        try:
            records.append(from_dict(item))
        # ValueError covers SignalValidationError; TypeError covers the coercions in from_dict
        # (``int(raw["decay_days"])`` on a list, say). A malformed record in a hand-edited file
        # must not crash the issue — including when it is malformed in a way the schema checks
        # never reach.
        except (ValueError, TypeError) as exc:
            rejects.append(SignalReject(index=index, id=_raw_id(item), error=str(exc)))
    return LoadReport(records, rejects)


def load(path: Path) -> list[SignalRecord]:
    """Read a signal file, keeping only the records that validate.

    Missing, unreadable or malformed file → empty list; an individual bad record is skipped.
    This is the forgiving READ path — a hand-edited file must never crash issue generation.
    Anything that needs to *gate* on the file wants ``load_reporting``, which also returns
    what was skipped and why.
    """
    return load_reporting(path).records


def _file_date(path: Path) -> str | None:
    m = _SIGNALS_DATE_RE.search(path.name)
    return m.group(1) if m else None


def list_files(content_root: Path, profile: str) -> list[Path]:
    """All ``signals-<date>.json`` files for *profile*, oldest first."""
    d = store_dir(content_root, profile)
    if not d.is_dir():
        return []
    files = [p for p in d.glob("signals-*.json") if p.is_file() and _file_date(p)]
    files.sort(key=lambda p: (_file_date(p), p.name))
    return files


def load_latest(content_root: Path, profile: str) -> tuple[Path | None, list[SignalRecord]]:
    """Load the newest signal file. Returns ``(path, signals)``; path is None if absent."""
    files = list_files(content_root, profile)
    if not files:
        return None, []
    latest = files[-1]
    return latest, load(latest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.signals",
        description="Validate and inspect the signal store for a profile.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument(
        "--validate",
        type=Path,
        default=None,
        help="Validate a JSON file of signal records and exit non-zero on failure.",
    )
    args = parser.parse_args(argv)

    from ..paths import PathConfig

    cfg = PathConfig.from_env(repo_root=args.repo_root)

    if args.validate:
        # A gate reports what FAILED, not what survived. Printing only the survivor count made a
        # 76-record file with 6 errors read exactly like a clean 70-record one, and exit 0 either
        # way — so the six contradictions in it went unnoticed until each record was re-validated
        # by hand.
        report = load_reporting(args.validate)
        payload: dict = {
            "file": str(args.validate),
            "total": report.total,
            "valid": len(report.records),
            "rejected": len(report.rejects),
            "ok": report.ok,
        }
        if report.file_error:
            payload["file_error"] = report.file_error
        if report.rejects:
            payload["rejects"] = [r.to_dict() for r in report.rejects]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    path, signals = load_latest(cfg.content_root, args.profile)
    summary = {
        "latest": str(path) if path else None,
        "count": len(signals),
        "by_triage": {},
        "by_direction": {},
    }
    for s in signals:
        summary["by_triage"][s.triage] = summary["by_triage"].get(s.triage, 0) + 1
        summary["by_direction"][s.direction] = summary["by_direction"].get(s.direction, 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
