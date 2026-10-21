"""The level mix report: realised levels vs [level_mix] targets (R7.7).

One shared implementation called by `prospects status`, `list-fit`, and `send-cards`.
Reports realised percentages against target, counts 'send anyway' overrides on
their own line, and states next steps when a target is missed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import RoleVocabulary


@dataclass(frozen=True)
class LevelMixReport:
    """The computed level mix report across segments."""

    realised: dict[str, dict[str, int]]
    target: dict[str, dict[str, int]]
    percentages: dict[str, dict[str, float]]
    total_counts: dict[str, int]
    overrides_count: int
    next_steps: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Render plain-text report."""
        lines: list[str] = []
        for segment, levels in sorted(self.realised.items()):
            tot = self.total_counts.get(segment, 0)
            target_map = self.target.get(segment, {})
            target_str = " / ".join(f"{v}% {k}" for k, v in sorted(target_map.items()))
            lines.append(f"Level mix for {segment} (N={tot}, target: {target_str}):")
            for level, count in sorted(levels.items()):
                pct = self.percentages.get(segment, {}).get(level, 0.0)
                tgt = target_map.get(level, 0)
                lines.append(f"  {level:<16} {count:>3} ({pct:>5.1f}%) [target: {tgt}%]")
        lines.append(f"Send anyway overrides: {self.overrides_count}")
        if self.next_steps:
            for step in self.next_steps:
                lines.append(f"Next step: {step}")
        else:
            lines.append("Level mix on target.")
        return "\n".join(lines)


def _format_step(lvl: str, needed: int, seat_name: str) -> str:
    if lvl == "champion":
        return f"find {needed} more Heads/Directors in {seat_name}"
    if lvl == "evaluator":
        return f"find {needed} more Architects/Engineers in {seat_name}"
    if lvl == "economic-buyer":
        return f"find {needed} more C-level/VPs in {seat_name}"
    return f"find {needed} more {lvl} in {seat_name}"


def _compute_next_steps_for_segment(
    seg: str,
    levels: dict[str, int],
    tot: int,
    percentages: dict[str, float],
    target_map: dict[str, float],
    vocab: RoleVocabulary,
) -> list[str]:
    steps: list[str] = []
    for lvl, target_pct in target_map.items():
        cur_pct = percentages.get(lvl, 0.0)
        if cur_pct < target_pct:
            cur_cnt = levels.get(lvl, 0)
            if 100 - target_pct > 0:
                needed = math.ceil((target_pct * tot - 100 * cur_cnt) / (100 - target_pct))
            else:
                needed = 1
            if needed <= 0:
                needed = 1

            wedge = (
                getattr(vocab, "wedge_seats", {}).get(seg, ())
                if hasattr(vocab, "wedge_seats")
                else ()
            )
            seat_name = wedge[0] if wedge else "partnerships"
            steps.append(_format_step(lvl, needed, seat_name))
    return steps


def level_mix_report(
    rows: Sequence[dict[str, Any]],
    vocab: RoleVocabulary,
    profile: str | None = None,
    decisions: list[dict[str, Any]] | None = None,
) -> LevelMixReport:
    """Computes realised level mix vs target and next-step recommendations (R7.7)."""
    from ..hook_coverage.config import seat_of
    from .level import level_of

    realised: dict[str, dict[str, int]] = {}
    total_counts: dict[str, int] = {}
    overrides_count = 0

    # Count overrides from decisions if provided
    if decisions is not None:
        for d in decisions:
            dec = str(d.get("decision") or "").strip().lower()
            if dec == "send" or dec.startswith("send"):
                overrides_count += 1

    for r in rows:
        seg = str(r.get("segment") or "enterprise").strip().lower()
        title = str(r.get("title") or "")
        seat = str(r.get("seat") or "") or seat_of(title, profile) or ""
        lvl = str(r.get("contact_level") or "").strip().lower() or level_of(title, seat, vocab)

        realised.setdefault(seg, {}).setdefault("champion", 0)
        realised[seg].setdefault("evaluator", 0)
        realised[seg].setdefault("economic-buyer", 0)
        realised[seg].setdefault("unknown", 0)

        realised[seg][lvl] = realised[seg].get(lvl, 0) + 1
        total_counts[seg] = total_counts.get(seg, 0) + 1

        # Also count row-level overrides if decisions was not passed
        if decisions is None:
            dec = str(r.get("decision") or r.get("decided") or "").strip().lower()
            lane_reason = str(r.get("lane_reason") or "").strip().lower()
            if dec == "send" or dec.startswith("decided:send:") or "decided:send:" in lane_reason:
                overrides_count += 1

    target = getattr(vocab, "level_mix", {}) or {}
    percentages: dict[str, dict[str, float]] = {}
    next_steps: list[str] = []

    for seg, levels in realised.items():
        tot = total_counts.get(seg, 0)
        percentages[seg] = {}
        for lvl, cnt in levels.items():
            percentages[seg][lvl] = round((cnt / tot * 100), 1) if tot > 0 else 0.0

        target_map = target.get(seg, {})
        next_steps.extend(
            _compute_next_steps_for_segment(seg, levels, tot, percentages[seg], target_map, vocab)
        )

    return LevelMixReport(
        realised=realised,
        target=target,
        percentages=percentages,
        total_counts=total_counts,
        overrides_count=overrides_count,
        next_steps=next_steps,
    )
