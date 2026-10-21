"""Power and interval arithmetic — a stdlib-only leaf with no first-party imports.

These two functions used to live in :mod:`gtm_core.cells`, and were extracted on
2026-09-21 because of what ``cells`` costs to import. ``cells`` mutates ``sys.path``
at import time to reach ``seat_of`` in ``tests/linter/`` — deliberate there, since
two implementations of "which seat is this person" would drift and the drift would be
invisible, but it is import plumbing that has nothing to do with statistics. A module
that only wants to ask *"is this n powered?"* — :mod:`gtm_core.gtm_distill`, which
must refuse to promote an underpowered candidate — should not have to take on the
linter path insert to ask it.

So the arithmetic lives here, in a leaf that imports ``math`` and nothing else, and
:mod:`gtm_core.cells` re-exports ``Z_CONF``, ``Z_POWER``, :func:`wilson` and
:func:`detectable_lift` so every existing caller is unchanged. Same rationale as
:mod:`gtm_core.assignment`'s: **one** implementation, read by the producer and by
anything auditing it. A second copy of a power calculation is worse than no copy —
two modules would disagree about whether the same n supports the same claim.
"""

from __future__ import annotations

import math

#: z for a 95% two-sided interval and for 80% power. Hardcoded so this module stays
#: stdlib-only (no scipy) — both are fixed constants at the confidence/power levels
#: the campaign manifest's own power table already assumes.
Z_CONF = 1.96
Z_POWER = 0.84


def wilson(successes: float, n: float) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion. ``None`` when there is no
    denominator — an unrateable cell must read as unknown, never as 0%."""
    if n <= 0:
        return None
    p = successes / n
    z2 = Z_CONF * Z_CONF
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    margin = (Z_CONF * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def detectable_lift(n_per_arm: float, baseline: float) -> float | None:
    """Smallest relative lift over ``baseline`` detectable at this per-arm n
    (80% power, 95% confidence, two-proportion normal approximation).

    Returned as a multiplier: 2.0 means "only a doubling would show". ``None`` when
    the inputs cannot support the question at all. This replaces the campaign
    manifest's static power table with a reading against the n actually in hand.
    """
    if n_per_arm <= 0 or not (0 < baseline < 1):
        return None
    # Solve for p2 such that the standard two-proportion test is powered at n_per_arm.
    # Iterate rather than invert: cheap, stdlib-only, and monotone in p2.
    lo, hi = baseline, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        pbar = (baseline + mid) / 2
        se_null = math.sqrt(2 * pbar * (1 - pbar) / n_per_arm)
        se_alt = math.sqrt((baseline * (1 - baseline) + mid * (1 - mid)) / n_per_arm)
        if se_alt <= 0:
            break
        powered = (mid - baseline) >= (Z_CONF * se_null + Z_POWER * se_alt)
        if powered:
            hi = mid
        else:
            lo = mid
    if hi >= 1.0:
        return None
    return round(hi / baseline, 2)
