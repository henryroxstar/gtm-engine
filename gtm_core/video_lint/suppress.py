from __future__ import annotations

from dataclasses import dataclass

from .model import _TIER_RE, Finding
from .tiers import _TIERS_BY_ID

_MIN_SUPPRESSION_REASON_CHARS = 12
_PLACEHOLDER_REASONS = frozenset({"n/a", "na", "ok", "tbd", "wontfix", "-", ""})


class BadSuppression(ValueError):
    """A suppression entry is missing a real reason, or names an unknown tier."""


@dataclass(frozen=True)
class Suppression:
    tier: str
    asset: str
    reason: str


def _validate_suppressions(raw: list[dict]) -> list[Suppression]:
    out: list[Suppression] = []
    for entry in raw:
        # NOT stripped before the regex check: "V3 " must fail as malformed, not silently
        # normalize to "V3" — a suppression's tier id is compared by exact equality everywhere
        # else, so accepting whitespace-padded input here would be the one place that isn't true.
        tier = str(entry.get("tier", ""))
        asset = str(entry.get("asset", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if not _TIER_RE.match(tier):
            raise BadSuppression(f"suppression names an invalid tier id: {tier!r}")
        if tier not in _TIERS_BY_ID:
            raise BadSuppression(f"suppression names an unknown tier: {tier!r}")
        if not asset:
            raise BadSuppression("suppression is missing 'asset' — must be scoped to one file")
        if len(reason) < _MIN_SUPPRESSION_REASON_CHARS or reason.lower() in _PLACEHOLDER_REASONS:
            raise BadSuppression(
                f"suppression for {tier} on {asset!r} has no real reason "
                f"({reason!r}) — a suppression with no reason is worse than no rule"
            )
        out.append(Suppression(tier=tier, asset=asset, reason=reason))
    return out


def apply_suppressions(
    findings: list[Finding], suppressions: list[Suppression], *, asset: str
) -> tuple[list[Finding], dict[str, int]]:
    """Filter findings against suppressions scoped to (tier, asset). Returns the surviving
    findings AND a per-tier suppressed-count — suppressions are ALWAYS counted and reported, even
    at zero, never applied silently (deck_lint's suppressions were neither)."""
    suppressed_tiers = {s.tier for s in suppressions if s.asset == asset}
    counts: dict[str, int] = {}
    kept: list[Finding] = []
    for f in findings:
        if f.tier in suppressed_tiers:
            counts[f.tier] = counts.get(f.tier, 0) + 1
        else:
            kept.append(f)
    return kept, counts


def stale_suppressions(
    suppressions: list[Suppression], counts: dict[str, int], *, asset: str
) -> list[Suppression]:
    """Suppressions for ``asset`` whose tier RAN this pass and matched ZERO findings — a
    suppression nobody needs any more, and reporting it lets an operator delete it before it
    silently hides the next real regression of that tier.

    ``counts`` is not ``apply_suppressions``'s per-tier SUPPRESSED-count (that dict only ever
    carries a key for a tier that matched at least once, so a tier matching zero would be
    indistinguishable from one never even evaluated). It is the caller's per-tier RAW
    finding-count for every tier that actually ran this pass — key PRESENCE (even at value 0)
    means "ran"; a tier absent from ``counts`` is never a stale candidate, however long its
    suppression has sat unused. That is the line between "matched nothing this time" and "this
    rule simply did not run" — V11 with no caption manifest, or any tier ``--fast`` skips — and
    collapsing it would report a rule as safe to delete when it was never checked at all.

    Pure — no I/O, no CLI concerns; the caller decides what "ran" means. ``asset.strip()``
    mirrors :func:`_validate_suppressions`'s own normalization of the field, so this never
    disagrees with what a suppression's ``asset`` was validated to mean.
    """
    return [
        s
        for s in suppressions
        if s.asset.strip() == asset.strip() and s.tier in counts and counts[s.tier] == 0
    ]
