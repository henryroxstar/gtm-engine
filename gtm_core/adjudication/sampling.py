from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .model import DEFAULT_STRATA, Adjudication


def stratum_of(row: dict, axes: Sequence[str] = DEFAULT_STRATA) -> str:
    """The stratum key for a row: one value per axis, joined.

    ``signal`` is derived rather than read from a column — a row either has a clause
    that can open a dated line or it does not, and that is the axis, not whatever the
    column happens to be named this month.
    """
    parts = []
    for axis in axes:
        if axis == "signal":
            parts.append("signal" if (row.get("signal_clause") or "").strip() else "generic")
        else:
            parts.append((row.get(axis) or "-").strip() or "-")
    return "|".join(parts)


def stratify(rows: Iterable[dict], axes: Sequence[str] = DEFAULT_STRATA) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[stratum_of(r, axes)].append(r)
    return dict(out)


def collapsed_axes(rows: Sequence[dict], axes: Sequence[str] = DEFAULT_STRATA) -> list[str]:
    """Axes that do not vary across the list — either absent from the schema, or
    constant.

    A collapsed axis is not an error (a per-seat list has one seat by construction),
    but it silently shrinks what the sample covers, and an unreported one turns "I
    sampled across seat, signal and tier" into a claim about one axis wearing three
    names.
    """
    if not rows:
        return list(axes)
    out = []
    for axis in axes:
        values = {stratum_of(r, [axis]) for r in rows}
        if len(values) <= 1:
            out.append(axis)
    return out


def _stable_key(row: dict, seed: str = "") -> str:
    """A deterministic per-row ordering key.

    Not ``random.shuffle(seed=...)``: that reorders under a Python version change, and
    a sample nobody can reproduce is a sample nobody can check. A hash of the row's own
    identity is stable across machines, runs, and years.

    ``seed`` varies WHICH exemplar a stratum contributes without making the draw
    unreproducible. Both properties are wanted and they pull against each other: a fixed
    key means consecutive evals re-read the same rows and never exercise the rest of the
    list, while true randomness means a sheet cannot be rebuilt or two labelers compared.
    Mixing a caller-chosen seed into the hash gives variety ACROSS evals and determinism
    WITHIN one — pass the campaign plus the sheet date and every sheet is reproducible
    forever while no two sheets read the same exemplars.
    """
    ident = (row.get("email") or row.get("company") or "").strip().lower()
    return hashlib.sha256(f"{seed}|{ident}".encode()).hexdigest()


def sample(
    rows: Sequence[dict], n: int, axes: Sequence[str] = DEFAULT_STRATA, seed: str = ""
) -> list[dict]:
    """A covering sample of ``n`` rows: every stratum gets one before any gets two.

    Round-robin across strata rather than proportional allocation, on purpose. A
    proportional sample of a list that is 80% one seat spends 80% of a scarce reading
    budget re-reading that seat's single template, and the rare stratum — where the
    copy is least exercised and most likely wrong — goes unread. Once every stratum has
    one, the round-robin naturally weights the large ones anyway.

    Because ``DEFAULT_STRATA`` now carries ``cell``, that same one-before-two rule spreads
    the draw across the hook-matrix coordinates present in ``rows`` — the sampler maximises
    argument variety for free, and needs no cell-specific logic to do it. What it cannot do
    is invent coverage: a draw can only span the cells the input actually contains, so
    breadth is bought upstream by drafting across more cells, not here.
    """
    if n <= 0 or not rows:
        return []
    buckets = stratify(rows, axes)
    for key in buckets:
        buckets[key].sort(key=lambda r: _stable_key(r, seed))
    order = sorted(buckets)
    picked: list[dict] = []
    depth = 0
    while len(picked) < n:
        added = False
        for key in order:
            if depth < len(buckets[key]):
                picked.append(buckets[key][depth])
                added = True
                if len(picked) == n:
                    return picked
        if not added:
            break  # every stratum exhausted: the list is smaller than n
        depth += 1
    return picked


@dataclass
class Coverage:
    strata: int = 0
    read: int = 0
    rows: int = 0
    adjudicated: int = 0
    unread_strata: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return (self.read / self.strata) if self.strata else 0.0


def coverage(
    rows: Sequence[dict],
    records: Sequence[Adjudication],
    axes: Sequence[str] = DEFAULT_STRATA,
) -> Coverage:
    """What the read actually covered — and, more usefully, what it did not."""
    buckets = stratify(rows, axes)
    by_email = {(r.get("email") or "").strip().lower(): stratum_of(r, axes) for r in rows}
    seen = {by_email.get((a.email or "").strip().lower(), "") for a in records}
    seen.discard("")
    unread = sorted(set(buckets) - seen)
    return Coverage(
        strata=len(buckets),
        read=len(buckets) - len(unread),
        rows=len(rows),
        adjudicated=len({a.email for a in records}),
        unread_strata=unread,
    )
