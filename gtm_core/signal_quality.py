from __future__ import annotations

import argparse
import csv
import datetime
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import minischema
from .fsio import atomic_write_csv
from .merge_hygiene.signal_dates import signal_latest_date
from .paths import resolve_knowledge_file, resolve_profiles_root
from .role_vocabulary.level import is_cxo  # noqa: F401

DEFAULT_SIGNAL_QUALITY_CONFIG = {
    "rules": [
        {"tier": 1, "min_fit": 3, "min_recency": 0.8, "min_virality": 1},
        {"tier": 2, "min_fit": 3, "min_recency": 0.5, "min_virality": 1},
        {"tier": 2, "min_fit": 2, "min_recency": 0.8, "min_virality": 1},
        {"tier": 3, "min_fit": 2, "min_recency": 0.3, "min_virality": 0},
        {"tier": 3, "min_fit": 1, "min_recency": 0.8, "min_virality": 1},
    ]
}

SIGNAL_QUALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tier": {"type": "integer", "minimum": 1, "maximum": 4},
                    "min_fit": {"type": "integer", "minimum": 0, "maximum": 3},
                    "min_recency": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "min_virality": {"type": "integer", "minimum": 0, "maximum": 3},
                },
                "required": ["tier", "min_fit", "min_recency"],
            },
        },
    },
    "required": ["rules"],
}


def _matches_source(item: Any, sid: str) -> bool:
    if isinstance(item, dict):
        return item.get("id") == sid or item.get("url") == sid
    return getattr(item, "id", None) == sid or getattr(item, "url", None) == sid


def _lookup_source(source_id_or_url: str, registry: Any) -> Any | None:
    if registry is None or not source_id_or_url:
        return None
    sid = str(source_id_or_url).strip()
    if hasattr(registry, "by_id") and isinstance(registry.by_id, dict):
        if sid in registry.by_id:
            return registry.by_id[sid]
    if isinstance(registry, dict):
        if sid in registry:
            return registry[sid]
        for v in registry.values():
            if _matches_source(v, sid):
                return v
    sources = getattr(registry, "sources", None)
    if sources is None and isinstance(registry, (list, tuple)):
        sources = registry
    if sources:
        for s in sources:
            if _matches_source(s, sid):
                return s
    return None


def derive_signal_fit(
    source_id_or_url: str,
    registry: Any,
    agent_kind: str,
    recorded_fit: int | None = None,
) -> int:
    """Deterministically derive signal_fit (0-3) from registry or fall back to recorded."""
    source = _lookup_source(source_id_or_url, registry)
    if source is not None:
        if isinstance(source, dict):
            attestation = str(source.get("attestation") or "pool").lower().strip()
            premise = str(source.get("premise") or "").lower().strip()
        else:
            attestation = str(getattr(source, "attestation", "pool") or "pool").lower().strip()
            premise = str(getattr(source, "premise", "") or "").lower().strip()

        ak = str(agent_kind or "").lower().strip()
        if not premise:
            return 0
        if attestation == "agentic" and ak == "ai":
            return 3
        if ak == "ai":
            return 2
        return 1

    if recorded_fit is not None:
        try:
            val = int(recorded_fit)
            if 0 <= val <= 3:
                return val
        except (ValueError, TypeError):
            pass
    return 0


def load_signal_quality_config(
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
    profiles_root: Path | None = None,
) -> dict:
    """Load signal-quality.toml or fall back to defaults."""
    if not profile:
        return DEFAULT_SIGNAL_QUALITY_CONFIG
    root = profiles_root or resolve_profiles_root()
    try:
        path = resolve_knowledge_file(
            root, profile, "signal-quality.toml", product=product, overlay=overlay
        )
    except Exception:
        return DEFAULT_SIGNAL_QUALITY_CONFIG

    if not path.is_file():
        return DEFAULT_SIGNAL_QUALITY_CONFIG

    with open(path, "rb") as f:
        data = tomllib.load(f)

    errs = minischema.validate(data, SIGNAL_QUALITY_SCHEMA)
    if errs:
        raise ValueError(f"Invalid signal-quality.toml in {path}: {'; '.join(errs)}")
    return data


def _is_cluster_tier1(event_type: str | None, cluster_size: Any) -> bool:
    if event_type == "cluster_expansion":
        return True
    if cluster_size is not None:
        try:
            return int(cluster_size) >= 2
        except (ValueError, TypeError):
            pass
    return False


def derive_signal_quality_tier(
    recency: float | str = 0.0,
    fit: int | str = 0,
    virality: int | str | None = 1,
    config: dict | None = None,
    cluster_size: int | str | None = None,
    event_type: str | None = None,
) -> int:
    """Evaluate routing rules sorted by tier ASC taking min(matched_tiers) or default tier 4.

    Cluster expansion events (>= 2 roles within 30 days) automatically promote to Tier 1 priority.
    """
    if _is_cluster_tier1(event_type, cluster_size):
        return 1

    try:
        r_val = float(recency) if recency is not None and str(recency).strip() != "" else 0.0
    except (ValueError, TypeError):
        r_val = 0.0

    try:
        f_val = int(fit) if fit is not None and str(fit).strip() != "" else 0
    except (ValueError, TypeError):
        f_val = 0

    if virality is None or str(virality).strip() == "":
        v_val = 1
    else:
        try:
            v_val = int(virality)
        except (ValueError, TypeError):
            v_val = 1

    if not (0.0 <= r_val <= 1.0):
        r_val = 0.0
    if not (0 <= f_val <= 3):
        f_val = 0
    if not (0 <= v_val <= 3):
        v_val = 0

    if r_val <= 0.0 or f_val <= 0:
        return 4

    cfg = config or DEFAULT_SIGNAL_QUALITY_CONFIG
    rules = cfg.get("rules", [])
    matched_tiers: list[int] = []

    for rule in rules:
        t = int(rule.get("tier", 4))
        min_f = int(rule.get("min_fit", 0))
        min_r = float(rule.get("min_recency", 0.0))
        min_v = int(rule.get("min_virality", 0))

        if f_val >= min_f and r_val >= min_r and v_val >= min_v:
            matched_tiers.append(t)

    if matched_tiers:
        return min(matched_tiers)
    return 4


def score_quality_inputs(inputs: dict[str, Any], config: dict | None = None) -> int:
    """Score a dictionary of quality inputs (e.g. from BusinessEvent.to_quality_inputs())."""
    event_type = inputs.get("event_type")
    cluster_size = inputs.get("cluster_size")
    if _is_cluster_tier1(event_type, cluster_size):
        return 1
    recency = inputs.get("recency", inputs.get("signal_recency_score", 0.0))
    fit = inputs.get("fit", inputs.get("signal_fit", 0))
    virality = inputs.get("virality", inputs.get("signal_virality", 1))
    return derive_signal_quality_tier(
        recency=recency,
        fit=fit,
        virality=virality,
        config=config,
        cluster_size=cluster_size,
        event_type=event_type,
    )


def touch2_archetype_for_fit(fit: int | str | None) -> str:
    """Map signal_fit to Touch 2 follow-up archetype."""
    if fit is None or str(fit).strip() == "":
        return "None"
    try:
        f = int(fit)
    except (ValueError, TypeError):
        return "None"

    if f == 3:
        return "Gift"
    if f == 2:
        return "Friction Point"
    if f == 1:
        return "Sanity Check"
    return "None"


def backfill_legacy_csv(
    csv_path: Path,
    profile: str = "",
    out_path: Path | None = None,
    as_of: datetime.date | None = None,
) -> int:
    """Infer baseline signal_fit and set signal_virality = 1 for legacy rows."""
    target = Path(csv_path)
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")

    with open(target, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    if "signal_fit" not in fieldnames:
        fieldnames.append("signal_fit")
    if "signal_virality" not in fieldnames:
        fieldnames.append("signal_virality")

    today = as_of or datetime.date.today()
    backfilled_count = 0

    for r in rows:
        modified = False
        raw_fit = r.get("signal_fit")
        if raw_fit is None or str(raw_fit).strip() == "":
            why_now = r.get("why_now") or ""
            dt = signal_latest_date(why_now)
            if dt is None and r.get("signal_observed"):
                obs = str(r["signal_observed"]).strip().split("T")[0]
                try:
                    dt = datetime.date.fromisoformat(obs)
                except ValueError:
                    pass

            if dt is not None:
                age = (today - dt).days
                is_recent = 0 <= age <= 180
                ak = str(r.get("signal_agent_kind") or "").lower().strip()
                if ak == "ai" and is_recent:
                    r["signal_fit"] = "2"
                else:
                    r["signal_fit"] = "1"
            else:
                r["signal_fit"] = "0"
            modified = True

        raw_virality = r.get("signal_virality")
        if raw_virality is None or str(raw_virality).strip() == "":
            r["signal_virality"] = "1"
            modified = True

        if modified:
            backfilled_count += 1

    dest = Path(out_path) if out_path else target
    atomic_write_csv(dest, fieldnames, rows)
    return backfilled_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.signal_quality",
        description="Signal quality scoring and legacy backfill tool.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    backfill_parser = subparsers.add_parser(
        "backfill", help="Backfill legacy CSV with baseline quality scores"
    )
    backfill_parser.add_argument("--csv", required=True, type=Path, help="Path to CSV to backfill")
    backfill_parser.add_argument("--profile", default="", help="Profile name")
    backfill_parser.add_argument(
        "--out", type=Path, default=None, help="Output CSV path (default: overwrite in place)"
    )
    backfill_parser.add_argument(
        "--as-of", default=None, help="As-of date (YYYY-MM-DD) for recency evaluation"
    )

    # Shorthand root arguments
    parser.add_argument(
        "--csv", type=Path, default=None, help="Path to CSV to backfill (shorthand)"
    )
    parser.add_argument("--profile", default="", help="Profile name")
    parser.add_argument("--out", type=Path, default=None, help="Output CSV path")
    parser.add_argument("--as-of", default=None, help="As-of date (YYYY-MM-DD)")

    args = parser.parse_args(argv)

    csv_path = getattr(args, "csv", None)
    if not csv_path:
        parser.print_help()
        return 1

    as_of_date: datetime.date | None = None
    if args.as_of:
        try:
            as_of_date = datetime.date.fromisoformat(args.as_of)
        except ValueError:
            print(f"Error: Invalid --as-of date: {args.as_of}", file=sys.stderr)
            return 2

    try:
        count = backfill_legacy_csv(
            csv_path=csv_path,
            profile=args.profile,
            out_path=args.out,
            as_of=as_of_date,
        )
        dest = args.out if args.out else csv_path
        print(f"Backfilled {count} row(s) into {dest}.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
