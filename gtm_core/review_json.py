"""Post-render review.json and prompt-render delta measurement (10x Video PRD §5 / Q4).

Implements:
- Post-render review.json generation (directed vs observed facial Action Units)
- Description-never-evaluation posture (RANKS and never GATES)
- Prompt decay tracking across shot indices
- Seed stability regression hash verification
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ReviewEntry",
    "ReviewManifest",
    "build_review_entry",
    "log_prompt_decay",
    "check_seed_stability",
]

EVALUATIVE_WORDS = frozenset(
    {"good", "bad", "correct", "wrong", "poor", "excellent", "flawless", "terrible"}
)


@dataclass
class ReviewEntry:
    shot_index: int
    directed: str
    observed: str
    delta: str
    delta_score: float = 0.0
    verdict: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewManifest:
    run_id: str
    entries: list[ReviewEntry]

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entries": [e.to_dict() for e in self.entries],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")


def _sanitize_description(desc: str) -> str:
    """Ensure observed descriptions contain anatomical muscle descriptions, not judgements."""
    words = desc.split()
    cleaned = [w for w in words if w.lower().strip(".,!?:;") not in EVALUATIVE_WORDS]
    return " ".join(cleaned)


def build_review_entry(
    shot_index: int,
    directed_expression: str,
    vlm_description: str,
) -> ReviewEntry:
    """Build a review entry following the Q4 description-only posture.

    Prompt instructed to describe muscles (AU), never to evaluate.
    """
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
        directed=directed_expression,
        observed=observed,
        delta=delta,
        delta_score=round(delta_score, 2),
        verdict=None,
    )


def log_prompt_decay(entries: list[ReviewEntry]) -> list[dict[str, Any]]:
    """Log prompt adherence delta score per shot index to track degradation over time."""
    return [
        {
            "shot_index": e.shot_index,
            "delta_score": e.delta_score,
        }
        for e in sorted(entries, key=lambda x: x.shot_index)
    ]


def check_seed_stability(hash1: str, hash2: str, max_drift_hamming: int = 0) -> bool:
    """Check that identical seeds and prompts generate identical frame hashes within tolerance."""
    if hash1 == hash2:
        return True
    if len(hash1) != len(hash2):
        return False
    hamming = sum(c1 != c2 for c1, c2 in zip(hash1, hash2, strict=True))
    return hamming <= max_drift_hamming
