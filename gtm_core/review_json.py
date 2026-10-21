"""Post-render review.json and prompt-render delta measurement (PRD §2.4 / Q4).

Implements:
- Post-render review.json generation (directed vs observed facial Action Units)
- Description-never-evaluation posture (RANKS and never GATES)
- Path confinement wiring via gtm_core.video_finish.confine
- Prompt decay tracking across shot indices
- Seed stability regression hash verification
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "EVALUATIVE_WORDS",
    "VISION_PROMPT_INSTRUCTIONS",
    "ReviewEntry",
    "ReviewManifest",
    "build_review_entry",
    "validate_frame_path",
    "log_prompt_decay",
    "check_seed_stability",
]

EVALUATIVE_WORDS = frozenset(
    {
        "good",
        "bad",
        "correct",
        "wrong",
        "poor",
        "excellent",
        "flawless",
        "terrible",
        "effective",
    }
)

VISION_PROMPT_INSTRUCTIONS = (
    "Describe what this face is doing in anatomical muscle terms (Action Units); "
    "do not evaluate. Do not use evaluative words (good, bad, terrible, effective)."
)


@dataclass
class ReviewEntry:
    shot_index: int = 1
    beat_index: int = 1
    directed: str = ""
    observed: str = ""
    delta: str = ""
    delta_score: float = 0.0
    human_verdict: str | None = None
    verdict: str | None = None

    def __post_init__(self) -> None:
        if self.shot_index and not self.beat_index:
            self.beat_index = self.shot_index
        elif self.beat_index and not self.shot_index:
            self.shot_index = self.beat_index

        if self.verdict is not None and self.human_verdict is None:
            self.human_verdict = self.verdict
        elif self.human_verdict is not None and self.verdict is None:
            self.verdict = self.human_verdict

    def to_dict(self) -> dict[str, Any]:
        v = self.human_verdict if self.human_verdict is not None else self.verdict
        return {
            "beat_index": self.beat_index,
            "shot_index": self.shot_index,
            "directed": self.directed,
            "observed": self.observed,
            "delta": self.delta,
            "delta_score": self.delta_score,
            "human_verdict": v,
            "verdict": v,
        }


@dataclass
class ReviewManifest:
    run_id: str
    entries: list[ReviewEntry] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entries": [e.to_dict() for e in self.entries],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")


def validate_frame_path(
    frame_path: Path,
    project_dir: Path,
    *,
    content_root: Path | None = None,
) -> Path:
    """Ensure any frame path read for review is confined within project directory.

    Wires through gtm_core.video_finish.confine to guard against arbitrary absolute paths.
    """
    from .video_finish.confine import _safe_asset_path

    root = (content_root or project_dir).resolve()
    return _safe_asset_path(frame_path, content_root=root)


def _sanitize_description(desc: str) -> str:
    """Ensure observed descriptions contain anatomical muscle descriptions, not judgements."""
    words = desc.split()
    sanitized = [w for w in words if w.lower().strip(".,!?:;\"'“”‘’`") not in EVALUATIVE_WORDS]
    return " ".join(sanitized)


def build_review_entry(
    shot_index: int,
    directed_expression: str,
    vlm_description: str,
    *,
    beat_index: int | None = None,
) -> ReviewEntry:
    """Build a review entry following the Q4 description-only posture.

    Prompt instructed to describe muscles (AU), never to evaluate.
    """
    b_idx = beat_index if beat_index is not None else shot_index
    observed = _sanitize_description(vlm_description)
    directed_lower = directed_expression.lower()
    observed_lower = observed.lower()

    # Simple delta summary based on common action unit overlap
    delta = f"Directed: {directed_expression} | Observed: {observed}"
    # Delta score: 0.0 (aligned) to 1.0 (divergent)
    overlap = sum(1 for w in directed_lower.split() if w in observed_lower)
    total_words = max(1, len(directed_lower.split()))
    delta_score = max(0.0, min(1.0, 1.0 - (overlap / total_words)))

    return ReviewEntry(
        shot_index=shot_index,
        beat_index=b_idx,
        directed=directed_expression,
        observed=observed,
        delta=delta,
        delta_score=round(delta_score, 2),
        human_verdict=None,
        verdict=None,
    )


def log_prompt_decay(entries: list[ReviewEntry]) -> list[dict[str, Any]]:
    """Log prompt adherence delta score per shot index to track degradation over time."""
    return [
        {
            "beat_index": e.beat_index,
            "shot_index": e.shot_index,
            "delta_score": e.delta_score,
        }
        for e in sorted(entries, key=lambda x: x.beat_index)
    ]


def check_seed_stability(hash1: str, hash2: str, max_drift_hamming: int = 0) -> bool:
    """Check that identical seeds and prompts generate identical frame hashes within tolerance."""
    if hash1 == hash2:
        return True
    if len(hash1) != len(hash2):
        return False
    hamming = sum(c1 != c2 for c1, c2 in zip(hash1, hash2, strict=True))
    return hamming <= max_drift_hamming
