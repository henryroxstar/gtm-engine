"""Parse and validate a tenant ``scorecard.toml``. Every refusal happens HERE, before any run.

A wrong weight is a number nobody can tell is wrong by looking at the output — the 2026-09-21
rubric produced 806 Tier-A rows out of 1000 and read as a working scorer. So this module refuses
a card that could produce a plausible-but-meaningless number, and it reports **every** problem at
once rather than first-fail, because fixing a card one exception at a time is how a card ends up
half-migrated.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ..paths import resolve_knowledge_file, resolve_profiles_root
from .model import COVERAGE_PROXY, Axis, ScoreCard, ScoreCardError

#: Flat filename, resolved product-first through the knowledge resolver. Never a subpath —
#: ``_safe_segment`` rejects one, and directory traversal here is the highest-risk tenant error.
SCORECARD_FILE = "scorecard.toml"


def scorecard_path(
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> Path:
    """Where this profile's card lives — overlay, then product, then profile knowledge."""
    root = profiles_root or resolve_profiles_root()
    return resolve_knowledge_file(root, profile, SCORECARD_FILE, product=product, overlay=overlay)


def load(
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> ScoreCard:
    """The tenant's card, or :class:`ScoreCardError`. A missing card is a hard error.

    Scoring against a card that is not there would mean scoring against a default, and a default
    rubric is exactly the undeclared rubric this whole feature exists to make impossible.
    """
    path = scorecard_path(profile, profiles_root, product, overlay)
    if not path.is_file():
        raise ScoreCardError(
            f"no scorecard for profile {profile!r} at {path}. Scoring needs a declared rubric — "
            f"add knowledge/{SCORECARD_FILE}, naming the tenant document it comes from."
        )
    return parse(path.read_text(encoding="utf-8"), str(path))


def parse(text: str, source: str) -> ScoreCard:
    """Validate a card's whole contents. Every message is prefixed with ``source``."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ScoreCardError(f"{source}: not valid TOML — {exc}") from exc

    problems: list[str] = []
    axes = tuple(_axis(block, i, problems) for i, block in enumerate(raw.get("axis", []), start=1))
    if not axes:
        problems.append("no [[axis]] blocks — a card that scores nothing is not a card")

    ceiling = _int(raw, "ceiling", problems)
    tiers = {str(k): int(v) for k, v in dict(raw.get("tiers", {})).items()}
    required = tuple(str(x) for x in raw.get("required_inputs", []))
    categories = {str(k): str(v) for k, v in dict(raw.get("category", {})).items()}
    exclusions = {str(k): str(v) for k, v in dict(raw.get("exclusion", {})).items()}

    _check_coverage_proxy(axes, problems)
    _check_ceiling(axes, ceiling, problems)
    _check_inputs_resolve(axes, required, problems)
    _check_categories(required, categories, problems)
    _check_tiers(tiers, ceiling, raw.get("bottom_tier"), problems)
    _check_modulators(axes, problems)

    if problems:
        raise ScoreCardError(f"{source}: " + "; ".join(problems))

    return ScoreCard(
        version=str(raw.get("scorecard_version", "")),
        source=str(raw.get("source", "")),
        ceiling=ceiling,
        tiers=tiers,
        bottom_tier=str(raw.get("bottom_tier", "D")),
        required_inputs=required,
        axes=axes,
        categories=categories,
        exclusions=exclusions,
    )


# --------------------------------------------------------------------------------------------
# The individual refusals. Each one exists because its absence produced a real, plausible,
# wrong number — the comment says which.
# --------------------------------------------------------------------------------------------


def _check_coverage_proxy(axes: tuple[Axis, ...], problems: list[str]) -> None:
    """§R13. An axis may not award points for a fact about our own research coverage."""
    for axis in axes:
        for read in axis.reads:
            if read in COVERAGE_PROXY:
                problems.append(
                    f"axis {axis.name!r} awards points for {read!r}, which measures OUR RESEARCH "
                    "and not the account (coverage-proxy denylist). Gate on it if you must; "
                    "never score it"
                )


def _check_ceiling(axes: tuple[Axis, ...], ceiling: int, problems: list[str]) -> None:
    """Axis maxima must sum to the declared ceiling, and no axis may exceed its own max.

    Without this, a card silently scores out of 87 while every tier threshold and every
    rendered "N/100" still says 100.
    """
    total = sum(a.max for a in axes)
    if ceiling and total != ceiling:
        problems.append(
            f"axis maxima sum to {total} but ceiling is {ceiling} — every rendered score would "
            "be out of a denominator the card does not use"
        )
    for axis in axes:
        if axis.weights and max(axis.weights.values(), default=0) > axis.max:
            problems.append(f"axis {axis.name!r} has a weight above its own max of {axis.max}")
        if axis.components and sum(axis.components.values()) != axis.max:
            problems.append(
                f"axis {axis.name!r} components sum to {sum(axis.components.values())}, not its "
                f"declared max of {axis.max}"
            )


def _check_inputs_resolve(
    axes: tuple[Axis, ...], required: tuple[str, ...], problems: list[str]
) -> None:
    """Every required input must be read by some axis — as a weights input, a component, or the
    precondition of one. An orphan is a typo that silently gates nothing."""
    readable = {r for a in axes for r in a.reads} | {v for a in axes for v in a.requires.values()}
    for name in required:
        if name not in readable:
            problems.append(
                f"required_input {name!r} is read by no axis — it would gate nothing, so a row "
                "missing it would score anyway"
            )
    for axis in axes:
        for comp, gate in axis.requires.items():
            if comp not in axis.components:
                problems.append(
                    f"axis {axis.name!r} gates {comp!r} on {gate!r}, but {comp!r} is not one of "
                    "its components"
                )


def _check_categories(
    required: tuple[str, ...], categories: dict[str, str], problems: list[str]
) -> None:
    """A required input with no category text would refuse the row and say nothing useful."""
    for name in required:
        if not categories.get(name, "").strip():
            problems.append(f"required_input {name!r} has no [category] text naming its unlock")


def _check_tiers(tiers: dict[str, int], ceiling: int, bottom: Any, problems: list[str]) -> None:
    if not tiers:
        problems.append("no [tiers] — every score would be untiered")
        return
    if ceiling and max(tiers.values()) > ceiling:
        problems.append(f"a tier floor exceeds the ceiling of {ceiling} — it is unreachable")
    if bottom is not None and str(bottom) in tiers:
        problems.append(
            f"bottom_tier {bottom!r} is also a named tier floor — a score cannot be in both"
        )


def _check_modulators(axes: tuple[Axis, ...], problems: list[str]) -> None:
    """A modulator that names a value the axis has no weight for scales nothing."""
    for axis in axes:
        if axis.modulated_by and not axis.modulates:
            problems.append(
                f"axis {axis.name!r} declares modulated_by but no 'modulates' value to scale"
            )
        if axis.modulates and axis.modulates not in axis.weights:
            problems.append(
                f"axis {axis.name!r} modulates {axis.modulates!r}, which is not one of its weights"
            )


# --------------------------------------------------------------------------------------------


def _axis(block: Any, index: int, problems: list[str]) -> Axis:
    where = f"axis #{index}"
    if not isinstance(block, dict):
        problems.append(f"{where} is not a table")
        return Axis(name=where, max=0)
    name = str(block.get("name", "") or where)
    weights = {str(k): float(v) for k, v in dict(block.get("weights", {})).items()}
    components = {str(k): float(v) for k, v in dict(block.get("components", {})).items()}
    if bool(weights) == bool(components):
        problems.append(f"axis {name!r} must declare exactly one of 'weights' or 'components'")
    if weights and not block.get("input"):
        problems.append(f"axis {name!r} has weights but names no input to look them up by")
    return Axis(
        name=name,
        max=int(block.get("max", 0)),
        input=str(block["input"]) if block.get("input") else None,
        weights=weights,
        components=components,
        requires={str(k): str(v) for k, v in dict(block.get("requires", {})).items()},
        phrases={str(k): str(v) for k, v in dict(block.get("phrases", {})).items()},
        modulated_by=str(block["modulated_by"]) if block.get("modulated_by") else None,
        modulates=str(block["modulates"]) if block.get("modulates") else None,
    )


def _int(raw: dict, key: str, problems: list[str]) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        problems.append(f"{key} must be an integer, got {value!r}")
        return 0
    return value
