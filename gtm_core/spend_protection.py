"""Spend protection and credit ceiling enforcement (10x Video PRD §1).

Guards against runaway credit burn before provider API dispatch:
- Evaluates declared `max_spend_credits` ceiling against cumulative run spend
- Logs per-call provider spend into `costs.jsonl`
- Preflight spend refusal happens BEFORE provider dispatch (pure arithmetic, zero model judgement)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ledgers import Ledgers
from .paths import _safe_segment, resolve_content_root, resolve_profiles_root
from .video_presets import load_presets

__all__ = [
    "SpendCeilingExceeded",
    "check_spend_ceiling",
    "get_cumulative_run_spend",
    "log_provider_call_cost",
    "evaluate_preflight_spend",
]


class SpendCeilingExceeded(Exception):
    """Raised when an operation would cause cumulative spend to exceed the credit ceiling."""


def check_spend_ceiling(
    current_spend: int | float,
    next_call_credits: int | float,
    ceiling: int | float,
) -> None:
    """Deterministic ceiling check — arithmetic only, zero model judgement.

    Raises SpendCeilingExceeded if current_spend + next_call_credits > ceiling.
    """
    total = current_spend + next_call_credits
    if total > ceiling:
        raise SpendCeilingExceeded(
            f"Spend ceiling exceeded: cumulative spend of {current_spend} credits + "
            f"next call ({next_call_credits} credits) = {total} credits, "
            f"exceeding ceiling of {ceiling} credits."
        )


def get_cumulative_run_spend(content_root: Path, profile: str, run_id: str) -> int:
    """Sum credits spent for a given run_id from content/<profile>/costs.jsonl."""
    _safe_segment(profile, "profile")
    costs_file = content_root / profile / "costs.jsonl"
    if not costs_file.is_file():
        return 0

    total_credits = 0
    try:
        with costs_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    # Match by run_id or slug
                    if data.get("run_id") == run_id or data.get("slug") == run_id:
                        # credits or units
                        credits_val = data.get("credits")
                        if credits_val is None:
                            credits_val = data.get("units", 0)
                        total_credits += int(credits_val or 0)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError:
        return 0

    return total_credits


def log_provider_call_cost(
    content_root: Path,
    profile: str,
    *,
    run_id: str,
    shot_index: int,
    provider: str,
    credits: int,
    timestamp: str | None = None,
    usd_estimate: float = 0.0,
) -> dict[str, Any]:
    """Append exactly one entry to content/<profile>/costs.jsonl for a provider call."""
    _safe_segment(profile, "profile")

    class _ConfigAdapter:
        def __init__(self, root: Path) -> None:
            self.content_root = root

    ledgers = Ledgers(_ConfigAdapter(content_root), profile)
    record: dict[str, Any] = {
        "run_id": run_id,
        "shot_index": shot_index,
        "provider": provider,
        "credits": credits,
        "units": credits,
        "unit_kind": "credits",
        "usd_estimate": usd_estimate,
        "tool": f"{provider}_video",
        "op": "render_shot",
    }
    if timestamp:
        record["ts"] = timestamp

    return ledgers.append_cost(record)


def evaluate_preflight_spend(
    profile: str,
    preset_key: str,
    run_id: str,
    next_call_credits: int,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    ceiling_override: int | None = None,
) -> None:
    """Preflight check: refuse before provider call if cumulative spend exceeds ceiling."""
    c_root = resolve_content_root() if content_root is None else Path(content_root)
    p_root = resolve_profiles_root() if profiles_root is None else Path(profiles_root)

    ceiling = ceiling_override
    if ceiling is None:
        presets = load_presets(profile, profiles_root=p_root)
        preset = presets.get(preset_key)
        ceiling = preset.max_spend_credits if preset else 50

    current_spend = get_cumulative_run_spend(c_root, profile, run_id)
    check_spend_ceiling(current_spend, next_call_credits, ceiling)
