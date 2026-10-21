"""Monthly calibration job for the internal hook scorer (PRD Phase 9).

Joins persisted ``hook_score`` rows in ``outcomes.jsonl`` with real content
performance for the same hook × format, fits a small constrained linear model,
and writes updated weights back to
``content/<profile>/models/hook_score_weights.json``.

Until enough data exists the job returns conservative defaults and a report
saying so. The scorer never degrades to worse-than-default weights: the fitted
weights are only adopted when their in-sample error beats the default weights'
error on the same data.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import hook_score as hs
from . import outcomes as oc
from .paths import resolve_content_root, resolve_profiles_root

#: Minimum score/performance pairs before we trust a fit.
MIN_RECORDS = 5

#: Scale engagement rate (0–1+) to the 0–100 score axis for the linear fit.
_PERFORMANCE_SCALE = 500.0

#: Grid step for the constrained weight search (1/n).
_GRID_STEP = 20


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_score_records(rows: list[dict]) -> list[dict]:
    """Return hook_score rows with parseable component metadata."""
    out = []
    for row in rows:
        if str(row.get("outcome", "")).strip().lower() != "hook_score":
            continue
        meta = row.get("meta") or {}
        components = meta.get("components")
        if not isinstance(components, dict):
            continue
        if not all(k in components for k in hs.DEFAULT_WEIGHTS):
            continue
        hook_id = _tag_value(row, "hook")
        fmt = _tag_value(row, "format")
        if hook_id is None or fmt is None:
            continue
        out.append(
            {
                "hook_id": hook_id,
                "format": fmt,
                "platform": str(row.get("channel", "unknown")).strip().lower(),
                "score": meta.get("score"),
                "components": {k: int(components.get(k, 0)) for k in hs.DEFAULT_WEIGHTS},
                "band": _tag_value(row, "predictor_band") or "uncertain",
                "ts": row.get("ts"),
            }
        )
    return out


def actual_performance(
    rows: list[dict],
    hook_id: str,
    format: str,
) -> dict[str, Any]:
    """Aggregate real content outcomes for ``hook:<id>`` + ``format:<format>``.

    Returns impressions, content_engagements, posts, and engagement_rate.
    """
    impressions = 0.0
    engagements = 0.0
    posts = 0.0
    for row in rows:
        tags = row.get("tags") or []
        if not isinstance(tags, list):
            continue
        tag_set = {str(t) for t in tags}
        if f"hook:{hook_id}" not in tag_set or f"format:{format}" not in tag_set:
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
        if outcome == "published":
            posts += value
    return {
        "impressions": impressions,
        "engagements": engagements,
        "posts": posts,
        "engagement_rate": round(engagements / impressions, 4) if impressions else None,
    }


def _normalized_performance(engagement_rate: float | None) -> float:
    """Map an engagement rate to the 0–100 score axis used by components."""
    if engagement_rate is None:
        return 50.0
    return min(100.0, engagement_rate * _PERFORMANCE_SCALE)


def _predicted_score(record: dict, weights: dict[str, float]) -> float:
    components = record["components"]
    return sum(components[k] * weights[k] for k in hs.DEFAULT_WEIGHTS)


def _sse(records: list[dict], weights: dict[str, float]) -> float:
    return sum(
        (_predicted_score(r, weights) - _normalized_performance(r["actual_engagement_rate"])) ** 2
        for r in records
    )


def fit_weights(records: list[dict]) -> tuple[dict[str, float], dict[str, Any]]:
    """Search the 3-weight simplex for weights that best predict performance.

    Returns the fitted weights and a diagnostics dict. Falls back to defaults
    when data is too sparse or the fit is worse than defaults.
    """
    diagnostics: dict[str, Any] = {"records_used": len(records)}

    if len(records) < MIN_RECORDS:
        diagnostics["reason"] = f"need at least {MIN_RECORDS} score/performance pairs"
        diagnostics["fallback"] = True
        return dict(hs.DEFAULT_WEIGHTS), diagnostics

    default_sse = _sse(records, hs.DEFAULT_WEIGHTS)
    diagnostics["default_sse"] = round(default_sse, 4)

    best = dict(hs.DEFAULT_WEIGHTS)
    best_sse = default_sse

    n = _GRID_STEP
    for wr in range(n + 1):
        for wp in range(n + 1 - wr):
            wa = n - wr - wp
            weights = {
                "retention": wr / n,
                "prior": wp / n,
                "pattern": wa / n,
            }
            error = _sse(records, weights)
            if error < best_sse:
                best_sse = error
                best = weights

    diagnostics["fitted_sse"] = round(best_sse, 4)
    diagnostics["fallback"] = best_sse >= default_sse
    if diagnostics["fallback"]:
        diagnostics["reason"] = "fitted weights did not beat defaults; keeping defaults"
        best = dict(hs.DEFAULT_WEIGHTS)

    return best, diagnostics


def _band_precision(
    records: list[dict],
    band: str,
    baseline_rate: float | None,
) -> dict[str, Any] | None:
    """Precision/recall-style metrics for one predicted band."""
    if baseline_rate is None:
        return None
    band_records = [r for r in records if r["band"] == band]
    if not band_records:
        return None

    outperformed = sum(
        1 for r in band_records if (r["actual_engagement_rate"] or 0) >= baseline_rate
    )
    underperformed = sum(
        1 for r in band_records if (r["actual_engagement_rate"] or 0) < baseline_rate
    )
    total = len(band_records)
    return {
        "band": band,
        "total": total,
        "outperformed": outperformed,
        "underperformed": underperformed,
        "precision": round(outperformed / total, 4) if total else None,
        "false_positive_rate": round(underperformed / total, 4) if total else None,
    }


def calibrate(
    content_root: Path,
    profile: str,
    *,
    profiles_root: Path | None = None,
    since_month: str | None = None,
) -> dict[str, Any]:
    """Run the monthly calibration and write weights + report.

    Returns the calibration report dict.
    """
    profiles_root = profiles_root or resolve_profiles_root()
    rows = oc.read_outcomes(content_root, profile, since_month=since_month)

    score_records = load_score_records(rows)

    # Join each score record with actual performance for the same hook × format.
    joined: list[dict] = []
    for record in score_records:
        perf = actual_performance(rows, record["hook_id"], record["format"])
        if perf["impressions"] <= 0:
            continue
        joined.append({**record, "actual_engagement_rate": perf["engagement_rate"]})

    weights, diagnostics = fit_weights(joined)

    # Compute profile baseline for band precision.
    baseline_rows = [r for r in rows if not _has_hook_tag(r)]
    baseline_bucket = _blank_content_bucket()
    for row in baseline_rows:
        _add_content(baseline_bucket, row)
    baseline_rate = _finalize_content(baseline_bucket).get("engagement_rate")

    band_metrics = []
    for band in ("high", "medium", "low", "uncertain"):
        metric = _band_precision(joined, band, baseline_rate)
        if metric:
            band_metrics.append(metric)

    report = {
        "calibrated_at": _utc_now_iso(),
        "period": since_month,
        "records_considered": len(score_records),
        "records_matched": len(joined),
        "baseline_engagement_rate": baseline_rate,
        "weights": weights,
        "diagnostics": diagnostics,
        "band_metrics": band_metrics,
    }

    # Persist weights and report under content/<profile>/models/.
    models_dir = content_root / profile / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    hs.save_weights(content_root, profile, weights)
    (models_dir / "hook_score_calibration.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


# --- helpers duplicated lightly so this module stays stdlib + gtm_core only ---


def _has_hook_tag(row: dict) -> bool:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        return False
    return any(isinstance(t, str) and t.startswith("hook:") for t in tags)


def _tag_value(row: dict, prefix: str) -> str | None:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(f"{prefix}:"):
            return tag.split(":", 1)[1]
    return None


def _blank_content_bucket() -> dict:
    return {
        "posts": 0,
        "impressions": 0.0,
        "content_engagements": 0.0,
        "clicks": 0.0,
        "retention_seconds": 0.0,
        "counts": {},
        "predictor_bands": {},
    }


def _add_content(bucket: dict, row: dict) -> None:
    outcome = str(row.get("outcome", "")).strip().lower()
    if not outcome or outcome == "hook_score":
        return
    try:
        value = float(row.get("value", 1) or 0)
    except (TypeError, ValueError):
        value = 1.0
    bucket["counts"][outcome] = bucket["counts"].get(outcome, 0) + value
    if outcome == "published":
        bucket["posts"] += value
    if outcome in oc.IMPRESSION_OUTCOMES:
        bucket["impressions"] += value
    if outcome in oc.CONTENT_ENGAGEMENT_OUTCOMES:
        bucket["content_engagements"] += value
    if outcome in oc.CONTENT_CLICK_OUTCOMES:
        bucket["clicks"] += value
    if outcome == "retention_seconds":
        bucket["retention_seconds"] += value


def _finalize_content(bucket: dict) -> dict:
    impressions = bucket["impressions"]
    bucket["engagement_rate"] = (
        round(bucket["content_engagements"] / impressions, 4) if impressions else None
    )
    return bucket


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.calibrate_hook_score",
        description="Calibrate hook_score weights from real content outcomes.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profiles-root", default=None)
    parser.add_argument("--content-root", default=None)
    parser.add_argument("--since-month", default=None, help="YYYY-MM window")
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

    report = calibrate(
        content_root,
        args.profile,
        profiles_root=profiles_root,
        since_month=args.since_month,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
