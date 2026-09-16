"""The video reception measurement instrument (Q3) — PRD Phase 4.

Measures unprompted 24-hour audience message recall and conversion actions:
  - CONTROL_ACT_GAP_MAX = 0.15 (15% conversion gap ceiling between synthetic and human controls)
  - Records ReceptionVerdict with empirical findings
  - Integrates with content/<active>/outcomes.jsonl through gtm_core.outcomes
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "CONTROL_ACT_GAP_MAX",
    "MIN_AUDIENCE_SAMPLE",
    "ReceptionError",
    "AudienceResponse",
    "ReceptionVerdict",
    "evaluate_reception",
    "record_reception_outcome",
]

#: Max allowable drop in audience conversion/action rate for synthetic vs human control.
#: If |Act_synthetic - Act_human| > 0.15, the synthetic lane fails closed.
CONTROL_ACT_GAP_MAX: float = 0.15

#: Minimum audience sample per condition to compute a valid reception verdict.
MIN_AUDIENCE_SAMPLE: int = 20


class ReceptionError(ValueError):
    """Raised when reception dataset is malformed or insufficient."""


@dataclass(frozen=True)
class AudienceResponse:
    """One audience member's unprompted recall and downstream action response."""

    audience_id: str
    condition: str  # "synthetic" or "human"
    recalled_message: bool
    took_action: bool
    recall_note: str = ""

    def __post_init__(self) -> None:
        if self.condition not in ("synthetic", "human"):
            raise ReceptionError(
                f"invalid condition {self.condition!r}; must be 'synthetic' or 'human'"
            )


@dataclass
class ReceptionVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    synthetic_sample_size: int = 0
    human_sample_size: int = 0
    synthetic_recall_rate: float = 0.0
    human_recall_rate: float = 0.0
    synthetic_act_rate: float = 0.0
    human_act_rate: float = 0.0
    control_act_gap: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


def evaluate_reception(responses: list[AudienceResponse]) -> ReceptionVerdict:
    """Evaluate audience reception across synthetic and human control groups."""
    if not responses:
        raise ReceptionError("no audience responses provided")

    synthetic = [r for r in responses if r.condition == "synthetic"]
    human = [r for r in responses if r.condition == "human"]

    reasons: list[str] = []
    verdict = ReceptionVerdict(
        passed=False,
        synthetic_sample_size=len(synthetic),
        human_sample_size=len(human),
    )

    if len(synthetic) < MIN_AUDIENCE_SAMPLE or len(human) < MIN_AUDIENCE_SAMPLE:
        reasons.append(
            f"sample size too small: {len(synthetic)} synthetic, {len(human)} human. "
            f"Minimum {MIN_AUDIENCE_SAMPLE} required per condition."
        )

    if not synthetic or not human:
        verdict.reasons = reasons
        verdict.passed = False
        return verdict

    verdict.synthetic_recall_rate = sum(1 for r in synthetic if r.recalled_message) / len(synthetic)
    verdict.human_recall_rate = sum(1 for r in human if r.recalled_message) / len(human)

    verdict.synthetic_act_rate = sum(1 for r in synthetic if r.took_action) / len(synthetic)
    verdict.human_act_rate = sum(1 for r in human if r.took_action) / len(human)

    # Control Act Gap = Human Act Rate - Synthetic Act Rate (drop in conversion)
    verdict.control_act_gap = max(0.0, verdict.human_act_rate - verdict.synthetic_act_rate)

    if verdict.control_act_gap > CONTROL_ACT_GAP_MAX:
        reasons.append(
            f"control act gap {verdict.control_act_gap:.1%} exceeds {CONTROL_ACT_GAP_MAX:.1%} ceiling "
            f"(human act: {verdict.human_act_rate:.1%}, synthetic act: {verdict.synthetic_act_rate:.1%}). "
            "Synthetic presentation significantly suppresses audience follow-through."
        )

    verdict.reasons = reasons
    verdict.passed = not reasons
    return verdict


def record_reception_outcome(
    content_root: Path,
    profile: str,
    verdict: ReceptionVerdict,
    *,
    ref: str,
    tags: list[str] | None = None,
) -> None:
    """Record reception verdict into content/<profile>/outcomes.jsonl."""
    from .outcomes import append_outcome

    meta = {
        "passed": verdict.passed,
        "synthetic_act_rate": verdict.synthetic_act_rate,
        "human_act_rate": verdict.human_act_rate,
        "control_act_gap": verdict.control_act_gap,
        "reasons": verdict.reasons,
    }
    outcome_tag = "reception_pass" if verdict.passed else "reception_fail"
    final_tags = list(tags or []) + [outcome_tag, "q3_reception"]

    append_outcome(
        content_root=content_root,
        profile=profile,
        record={
            "channel": "reception",
            "outcome": "reception_eval",
            "ref": ref,
            "value": 1,
            "tags": final_tags,
            "meta": meta,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.reception")
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="JSON file with list of audience responses [{'audience_id': ..., 'condition': ..., 'recalled_message': ..., 'took_action': ...}]",
    )
    parser.add_argument("--json", action="store_true", help="output as JSON")
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        responses = [AudienceResponse(**r) for r in data]
        verdict = evaluate_reception(responses)
        if args.json:
            print(json.dumps(verdict.to_json(), indent=2))
        else:
            status = "PASS" if verdict.passed else "FAIL"
            print(f"Reception Verdict: {status}")
            print(f"Synthetic Act Rate: {verdict.synthetic_act_rate:.1%}")
            print(f"Human Act Rate: {verdict.human_act_rate:.1%}")
            print(f"Control Act Gap: {verdict.control_act_gap:.1%}")
            if verdict.reasons:
                print("\nReasons:")
                for r in verdict.reasons:
                    print(f"  - {r}")
        return 0 if verdict.passed else 1
    except (ReceptionError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
