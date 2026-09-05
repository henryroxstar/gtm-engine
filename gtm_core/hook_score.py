"""Cheap pre-render virality scorer for hooks.

Composite score made of three signals:

1. Retention-rubric model — a ``brain_radar`` model scores the script/storyboard
   against ``retention-rubric.md`` Part A.
2. Hook-bank prior — historical performance per ``hook_id × format`` from
   ``outcomes.jsonl``.
3. Pattern linter — deterministic checks from :mod:`gtm_core.hooks_lint`.

The module is stdlib + gtm_core only. The optional model call is isolated in
:func:`score_with_model`, which receives an injected ``model_caller`` so unit tests
can exercise the pure :func:`score` path without ``claude-agent-sdk`` installed and
so ``gtm_core`` never imports ``agent``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import hooks as hk
from . import hooks_lint as hl
from . import outcomes as oc
from .paths import _safe_segment, resolve_content_root, resolve_profiles_root

#: Default conservative weights (must sum to 1.0).
DEFAULT_WEIGHTS: dict[str, float] = {
    "retention": 0.35,
    "prior": 0.35,
    "pattern": 0.30,
}

#: Minimum impressions before a hook×format prior is considered reliable.
_MIN_PRIOR_IMPRESSIONS = 500

#: Score thresholds per band.
_BAND_HIGH = 80
_BAND_MEDIUM = 60
_BAND_LOW = 40

#: Maximum Part A retention score.
_RETENTION_MAX = 14


@dataclass(frozen=True)
class ScoreResult:
    """Composite hook score report."""

    hook_id: str
    format: str
    score: int  # 0–100 blended score
    band: str  # high | medium | low | uncertain
    components: dict[str, int]  # retention / prior / pattern on 0–100
    weakest_dim: str
    fix: str
    prior_has_data: bool = True  # False iff the band is "uncertain" purely for cold-start
    # (no hook×format prior — see _compute_prior). Distinguishes the bootstrap case, where
    # an override is meaningful, from a genuine low-confidence score, where it is not.


class ScoreError(ValueError):
    """Raised when a required input is missing or malformed."""


# --------------------------------------------------------------------------- #
# Weights
# --------------------------------------------------------------------------- #


def _weights_path(content_root: Path, profile: str) -> Path:
    return content_root / _safe_segment(profile, "profile") / "models" / "hook_score_weights.json"


def load_weights(
    content_root: Path,
    profile: str,
    *,
    defaults: dict[str, float] | None = None,
) -> dict[str, float]:
    """Load profile-local scoring weights, falling back to conservative defaults."""
    path = _weights_path(content_root, profile)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                weights = {k: float(v) for k, v in data.items() if k in DEFAULT_WEIGHTS}
                if len(weights) == len(DEFAULT_WEIGHTS):
                    total = sum(weights.values())
                    if abs(total - 1.0) < 1e-6:
                        return weights
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    return dict(defaults or DEFAULT_WEIGHTS)


def save_weights(
    content_root: Path,
    profile: str,
    weights: dict[str, float],
) -> None:
    """Persist profile-local scoring weights."""
    path = _weights_path(content_root, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(weights, indent=2), encoding="utf-8")


def record_score(
    content_root: Path,
    profile: str,
    result: ScoreResult,
    *,
    platform: str | None = None,
) -> None:
    """Append a ``hook_score`` row to ``outcomes.jsonl`` for later calibration.

    The row is tagged ``hook:<id>``, ``format:<format>``, and
    ``predictor_band:<band>`` so the distiller and calibration job can correlate
    score components with real performance. Score rows do not count as
    impressions or engagements — they are analytics metadata.
    """
    tags = [
        f"hook:{result.hook_id}",
        f"format:{result.format}",
        f"predictor_band:{result.band}",
    ]
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": platform or "unknown",
            "outcome": "hook_score",
            "value": 1,
            "tags": tags,
            "meta": {
                "score": result.score,
                "components": result.components,
                "weakest_dim": result.weakest_dim,
                "fix": result.fix,
            },
        },
    )


def record_override(
    content_root: Path,
    profile: str,
    result: ScoreResult,
    reason: str,
    *,
    platform: str | None = None,
) -> None:
    """Append an audited ``hook_score_override`` row for a cold-start bootstrap decision.

    This exists because the ``uncertain`` band is fail-closed **by design**
    (:func:`_band_for` returns it before any threshold comparison whenever
    ``prior_has_data`` is False — a deliberate anti-injection property, not a bug,
    and it must never be relaxed: see ``tests/test_hook_score.py::
    test_score_with_retention_and_no_prior_is_uncertain``). But a hook with zero
    outcome rows can *never* clear ``uncertain`` on its own merits, because a script
    rewrite cannot manufacture the 500 impressions ``_compute_prior`` requires — and
    those impressions can only accrue by publishing, which the gate is blocking. The
    band stays exactly what it was; this only records that a human, not the model,
    decided to proceed anyway, with a reason, the same way ``gtm_core.hooks``
    ``promote_hook``/``demote_hook``/``revive_hook`` require ``--evidence`` for their
    own fail-closed overrides.

    Raises ``ValueError`` if ``reason`` is blank, and if ``result.prior_has_data`` is
    True — an override is only meaningful for the cold-start case; a genuinely
    ``low``/``uncertain`` score from real signal has no "cold start" to bypass.
    """
    if not reason or not reason.strip():
        raise ValueError("--override-reason must not be blank — the decision must be audited")
    if result.prior_has_data:
        raise ValueError(
            "record_override is for the cold-start case only (prior_has_data=False); "
            f"this score has prior data (band={result.band!r}) — an override here would "
            "silently paper over a genuine low-confidence or low-quality score instead of "
            "the bootstrap deadlock it exists to fix"
        )

    tags = [
        f"hook:{result.hook_id}",
        f"format:{result.format}",
        "band_override:cold_start",
    ]
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": platform or "unknown",
            "outcome": "hook_score_override",
            "value": 1,
            "tags": tags,
            "meta": {
                "reason": reason.strip(),
                "score": result.score,
                "band": result.band,
                "components": result.components,
            },
        },
    )


# --------------------------------------------------------------------------- #
# Prior
# --------------------------------------------------------------------------- #


def _tag_prefix(rows: list[dict], hook_id: str, format: str | None) -> dict[str, Any]:
    """Aggregate impressions and engagements for ``hook:<id>`` (and format if given)."""
    impressions = 0.0
    engagements = 0.0
    for row in rows:
        tags = row.get("tags") or []
        if not isinstance(tags, list):
            continue
        tag_set = {str(t) for t in tags}
        if f"hook:{hook_id}" not in tag_set:
            continue
        if format is not None and f"format:{format}" not in tag_set:
            continue
        outcome = str(row.get("outcome", "")).strip().lower()
        try:
            value = float(row.get("value", 1) or 0)
        except (TypeError, ValueError):
            value = 1.0
        if outcome in oc.IMPRESSION_OUTCOMES:
            impressions += value
        if outcome in oc.CONTENT_ENGAGEMENT_OUTCOMES:
            engagements += value
    return {"impressions": impressions, "engagements": engagements}


def _profile_baseline(rows: list[dict], hook_id: str) -> dict[str, float]:
    """Aggregate total impressions and engagements for content outcomes.

    Rows already tagged with ``hook:<id>`` are excluded so the hook is compared to
    the rest of the profile, not to itself.
    """
    impressions = 0.0
    engagements = 0.0
    for row in rows:
        tags = row.get("tags") or []
        if isinstance(tags, list) and f"hook:{hook_id}" in {str(t) for t in tags}:
            continue
        outcome = str(row.get("outcome", "")).strip().lower()
        try:
            value = float(row.get("value", 1) or 0)
        except (TypeError, ValueError):
            value = 1.0
        if outcome in oc.IMPRESSION_OUTCOMES:
            impressions += value
        if outcome in oc.CONTENT_ENGAGEMENT_OUTCOMES:
            engagements += value
    return {"impressions": impressions, "engagements": engagements}


def _compute_prior(
    hook_id: str,
    format: str,
    content_root: Path,
    profile: str,
) -> tuple[int, bool, str]:
    """Return (score 0–100, has_data, fix_or_note)."""
    rows = oc.read_outcomes(content_root, profile)
    hook_stats = _tag_prefix(rows, hook_id, format)
    baseline_stats = _profile_baseline(rows, hook_id)

    hook_impressions = hook_stats["impressions"]
    hook_engagements = hook_stats["engagements"]
    base_impressions = baseline_stats["impressions"]
    base_engagements = baseline_stats["engagements"]

    if hook_impressions < _MIN_PRIOR_IMPRESSIONS:
        return (
            40,
            False,
            "No reliable historical data for this hook × format; ship as a test to collect outcomes.",
        )

    hook_rate = hook_engagements / hook_impressions if hook_impressions else 0.0
    base_rate = base_engagements / base_impressions if base_impressions else 0.0

    if base_rate <= 0:
        # No profile baseline yet — treat this hook as the baseline.
        return (
            50,
            True,
            "Profile baseline unavailable; this hook's own rate is the reference.",
        )

    ratio = hook_rate / base_rate
    score = int(min(100.0, max(0.0, ratio * 50)))

    if ratio >= 1.5:
        note = "Hook is outperforming baseline strongly."
    elif ratio >= 1.1:
        note = "Hook is outperforming baseline."
    elif ratio >= 0.9:
        note = "Hook is tracking baseline."
    elif ratio >= 0.6:
        note = "Hook is underperforming baseline; consider new variants."
    else:
        note = "Hook is well below baseline; revise angle or retire."

    return score, True, note


# --------------------------------------------------------------------------- #
# Pattern
# --------------------------------------------------------------------------- #


def _compute_pattern(
    hook: hk.Hook,
    text: str,
    bank: hk.HookBank,
) -> tuple[int, str, str]:
    """Return (score 0–100, weakest_dim, fix)."""
    errors = hl.lint_text(text, hook, bank.banned)
    if not text or not text.strip():
        return 0, "empty-text", "Text is empty; nothing to score."

    score = max(0, 100 - len(errors) * 20)

    if not errors:
        return score, "pattern-compliance", "Pattern checks passed."

    # Map the first error to a dimension name and the full list to a fix.
    first = errors[0].lower()
    if "payoff" in first:
        weakest = "payoff-presence"
    elif "banned" in first or "stem" in first:
        weakest = "banned-language"
    elif "caption load" in first:
        weakest = "caption-load"
    elif "angle" in first:
        weakest = "hook-angle-presence"
    else:
        weakest = "pattern-compliance"

    fix = "; ".join(errors)
    return score, weakest, fix


# --------------------------------------------------------------------------- #
# Retention (model-facing helpers)
# --------------------------------------------------------------------------- #


def normalize_retention(raw: int) -> int:
    """Convert a Part A score (0–14) to a 0–100 scale."""
    return int(round(max(0, min(_RETENTION_MAX, raw)) * 100.0 / _RETENTION_MAX))


def build_retention_prompt(
    hook: hk.Hook,
    format: str,
    text: str,
    *,
    platform: str | None = None,
) -> str:
    """Build a ``brain_radar`` prompt that scores ``text`` against Part A of the rubric."""
    beat = next(
        (b for b in hook.opening_beats if b.format == format),
        None,
    )
    beat_note = ""
    if beat is not None:
        beat_note = (
            f"\nOpening beat for {format}: {beat.text or beat.video or beat.image or '(none)'}\n"
        )

    platform_note = f"Target platform: {platform}\n" if platform else ""

    return (
        "You are a short-form content retention grader. Score the following script/storyboard "
        "against Part A of the retention rubric.\n\n"
        "Rubric Part A — Retention craft (0–2 each, intro counts double, total 0–14):\n"
        "1. Intro retention (first ~3s) — states stakes, shows a result, or poses a live question. Weight double.\n"
        "2. Cold open — starts mid-action or mid-sentence, already inside the argument.\n"
        "3. Micro-retention beats — something changes at ~1s, ~3s, then every few seconds.\n"
        "4. Completion realism — length matches how much the idea actually needs.\n"
        "5. Silent legibility — lands with sound off; captions/text carry the claim.\n"
        "6. Payoff density — delivers the promised thing and earns a re-watch or save.\n\n"
        "Hook angle: " + hook.angle + "\n"
        "Payoff promise: "
        + hook.payoff_promise
        + "\n"
        + beat_note
        + platform_note
        + "\nScript/storyboard to score:\n---\n"
        + text
        + "\n---\n\n"
        "Return ONLY a JSON object with this exact shape (no markdown, no prose):\n"
        '{"part_a_score": <0-14 int>, "weakest_dims": ["dim1", "dim2"], "fix": "one-sentence fix"}'
    )


def parse_retention_response(text: str) -> dict[str, Any]:
    """Extract the JSON grading object from a model response.

    Tolerates JSON wrapped in markdown fences.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    # If there is prose around the JSON, grab the first JSON object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScoreError(f"retention response is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ScoreError("retention response JSON is not an object")

    raw = data.get("part_a_score")
    try:
        raw = int(raw)
    except (TypeError, ValueError) as err:
        raise ScoreError(f"part_a_score is not an integer: {raw!r}") from err

    weakest = data.get("weakest_dims") or []
    if isinstance(weakest, str):
        weakest = [weakest]
    if not isinstance(weakest, list):
        weakest = []

    fix = data.get("fix") or "Revise the weakest retention dimensions."
    if not isinstance(fix, str):
        fix = str(fix)

    return {
        "part_a_score": raw,
        "weakest_dims": [str(w) for w in weakest],
        "fix": fix.strip(),
    }


# --------------------------------------------------------------------------- #
# Composite score
# --------------------------------------------------------------------------- #


def _band_for(score: int, prior_has_data: bool) -> str:
    """Return band. Missing prior data forces 'uncertain' (fail-closed)."""
    if not prior_has_data:
        return "uncertain"
    if score >= _BAND_HIGH:
        return "high"
    if score >= _BAND_MEDIUM:
        return "medium"
    if score >= _BAND_LOW:
        return "low"
    return "uncertain"


def score(
    hook_id: str,
    format: str,
    text: str,
    profile: str,
    *,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    retention_component: dict[str, Any] | None = None,
    platform: str | None = None,
    weights: dict[str, float] | None = None,
) -> ScoreResult:
    """Compute the composite hook score.

    ``retention_component`` is the parsed output of :func:`parse_retention_response`.
    If omitted, the retention signal is treated as missing and the band is capped
    to ``medium`` (the scorer refuses a "high" band without historical data).
    """
    profiles_root = profiles_root or resolve_profiles_root()
    content_root = content_root or resolve_content_root()

    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        raise ScoreError(f"hook {hook_id!r} not found")
    if format not in hook.formats:
        raise ScoreError(
            f"format {format!r} is not declared for hook {hook_id!r}; "
            f"declared formats: {hook.formats!r}"
        )

    _safe_segment(profile, "profile")

    # Retention
    if retention_component is not None:
        raw = int(retention_component.get("part_a_score", 0))
        retention_score = normalize_retention(raw)
        retention_weakest = retention_component.get("weakest_dims") or []
        retention_fix = retention_component.get("fix", "")
    else:
        retention_score = 0
        retention_weakest = []
        retention_fix = "No retention-rubric model score available."

    # Prior
    prior_score, prior_has_data, prior_fix = _compute_prior(hook_id, format, content_root, profile)

    # Pattern
    pattern_score, pattern_weakest, pattern_fix = _compute_pattern(hook, text, bank)

    # Weights
    w = weights or load_weights(content_root, profile)
    total_w = sum(w.values())
    if total_w <= 0:
        w = DEFAULT_WEIGHTS
        total_w = sum(w.values())

    blended = int(
        round(
            retention_score * (w["retention"] / total_w)
            + prior_score * (w["prior"] / total_w)
            + pattern_score * (w["pattern"] / total_w)
        )
    )
    blended = max(0, min(100, blended))

    # Weakest dimension + fix
    component_map = {
        "retention": (
            retention_score,
            retention_weakest[0] if retention_weakest else "retention-craft",
            retention_fix,
        ),
        "prior": (prior_score, "prior", prior_fix),
        "pattern": (pattern_score, pattern_weakest, pattern_fix),
    }
    weakest_component = min(component_map, key=lambda k: component_map[k][0])
    _, weakest_dim, fix = component_map[weakest_component]
    band = _band_for(blended, prior_has_data)

    return ScoreResult(
        hook_id=hook_id,
        format=format,
        score=blended,
        band=band,
        components={
            "retention": retention_score,
            "prior": prior_score,
            "pattern": pattern_score,
        },
        weakest_dim=weakest_dim,
        fix=fix,
        prior_has_data=prior_has_data,
    )


# --------------------------------------------------------------------------- #
# Optional model call
# --------------------------------------------------------------------------- #


async def score_with_model(
    hook_id: str,
    format: str,
    text: str,
    profile: str,
    *,
    model_caller: Callable[[str], Awaitable[str]],
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    platform: str | None = None,
    weights: dict[str, float] | None = None,
) -> ScoreResult:
    """Full score including a ``brain_radar`` retention-rubric call.

    ``model_caller`` must be provided by the caller (typically ``agent/session.py``),
    so ``gtm_core`` never imports ``agent`` and the layering contract is preserved.
    Tests inject a fake caller to avoid SDK dependencies.
    """
    profiles_root = profiles_root or resolve_profiles_root()
    content_root = content_root or resolve_content_root()

    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        raise ScoreError(f"hook {hook_id!r} not found")

    prompt = build_retention_prompt(hook, format, text, platform=platform)
    response = await model_caller(prompt)

    retention_component = parse_retention_response(response)
    return score(
        hook_id,
        format,
        text,
        profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention_component,
        platform=platform,
        weights=weights,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.hook_score",
        description="Score a hook execution against retention, prior, and pattern signals.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--hook", required=True)
    parser.add_argument("--format", required=True)
    parser.add_argument("--text", required=True, help="script/storyboard text or path to a file")
    parser.add_argument("--platform", default=None)
    parser.add_argument(
        "--retention-raw", type=int, default=None, help="pre-computed Part A score 0-14"
    )
    parser.add_argument("--content-root", default=None)
    parser.add_argument("--profiles-root", default=None)
    parser.add_argument(
        "--override-reason",
        default=None,
        help=(
            "audited cold-start override: when the band is 'uncertain' purely because "
            "this hook×format has no outcome-history prior (prior_has_data=False), record "
            "this reason and proceed. Refused if blank. Has no effect — and is refused — "
            "when the score has real prior data; it is not a way to silence a genuine "
            "low/uncertain score. The band itself is never changed."
        ),
    )
    args = parser.parse_args(argv)

    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )
    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else resolve_profiles_root()
    )

    text = args.text
    path = Path(text).expanduser()
    if path.is_file():
        text = path.read_text(encoding="utf-8")

    retention_component = None
    if args.retention_raw is not None:
        retention_component = {"part_a_score": args.retention_raw, "weakest_dims": [], "fix": ""}

    result = score(
        args.hook,
        args.format,
        text,
        args.profile,
        profiles_root=profiles_root,
        content_root=content_root,
        retention_component=retention_component,
        platform=args.platform,
    )

    output: dict[str, Any] = {
        "hook_id": result.hook_id,
        "format": result.format,
        "score": result.score,
        "band": result.band,
        "components": result.components,
        "weakest_dim": result.weakest_dim,
        "fix": result.fix,
        "prior_has_data": result.prior_has_data,
    }

    if args.override_reason is not None:
        try:
            record_override(
                content_root,
                args.profile,
                result,
                args.override_reason,
                platform=args.platform,
            )
        except ValueError as exc:
            output["override"] = {"accepted": False, "error": str(exc)}
        else:
            output["override"] = {"accepted": True, "reason": args.override_reason.strip()}

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
