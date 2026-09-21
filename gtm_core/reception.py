"""The video reception measurement instrument (Q3) — PRD §2.3 / Phase 4.

Measures unprompted 24-hour audience message recall, felt response, and conversion actions
against a mandatory human control:
  - CONTROL_ACT_GAP_MAX = 0.15 (15% conversion gap ceiling between our film and human control)
  - PASS_MOVED_MIN = 3.5 (Mean "did this move you" 1-5 across our films)
  - KILL_DEFECT_SHARE = 0.60 (>= 60% of judges naming the same defect kills the film)
  - MIN_JUDGES = 5 (Minimum 5 judges required)
  - MIN_ICP_SHARE = 0.60 (At least 60% of judges must belong to ICP)
  - CONDITIONS = ("control_human", "ours")
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = [
    "CONTROL_ACT_GAP_MAX",
    "PASS_MOVED_MIN",
    "KILL_DEFECT_SHARE",
    "MIN_JUDGES",
    "MIN_ICP_SHARE",
    "CONDITIONS",
    "ReceptionError",
    "AudienceResponse",
    "ReceptionVerdict",
    "evaluate_reception",
    "write_reception_manifest",
    "record_reception_outcome",
]

# ── Pre-Registered Thresholds (PRD §2.3) ──────────────────────────────────────────────────────────

#: Share of judges who say they would take the film's own named next action.
#: What is a pass bar is the GAP to the human-made control: losing to control by >15% fails.
CONTROL_ACT_GAP_MAX: float = 0.15

#: Mean "did this move you" (1-5) across our films. 3 is the shrug; 3.5+ required.
PASS_MOVED_MIN: float = 3.5

#: If >= 60% of judges independently name the SAME defect ("the faces", "the voice"),
#: the film fails regardless of the means.
KILL_DEFECT_SHARE: float = 0.60

#: Below 5 judges, one person's taste dominates the result.
MIN_JUDGES: int = 5

#: Share of judges who must be in the ICP.
MIN_ICP_SHARE: float = 0.60

#: Conditions. control_human is NOT optional (§R12 positive control).
CONDITIONS: tuple[str, str] = ("control_human", "ours")


class ReceptionError(ValueError):
    """Raised when reception dataset is malformed or insufficient."""


@dataclass
class AudienceResponse:
    """One judge's response to a film condition."""

    judge_id: str = ""
    condition: str = "ours"
    took_action: bool = False
    is_icp: bool = True
    moved_score: float = 3.5
    defect: str = ""
    unprompted_recall_24h: bool = False
    recall_note: str = ""
    # Backwards-compatibility aliases
    audience_id: str = ""
    recalled_message: bool = False

    def __post_init__(self) -> None:
        if not self.judge_id and self.audience_id:
            self.judge_id = self.audience_id
        if not self.unprompted_recall_24h and self.recalled_message:
            self.unprompted_recall_24h = self.recalled_message

        # Normalize legacy conditions
        if self.condition == "human":
            self.condition = "control_human"
        elif self.condition == "synthetic":
            self.condition = "ours"

        if self.condition not in CONDITIONS:
            raise ReceptionError(
                f"invalid condition {self.condition!r}; must be one of {CONDITIONS}"
            )
        if not (1.0 <= float(self.moved_score) <= 5.0):
            raise ReceptionError(f"moved_score {self.moved_score} out of range; must be 1.0 to 5.0")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop legacy aliases from serialized output
        d.pop("audience_id", None)
        d.pop("recalled_message", None)
        return d


@dataclass
class ReceptionVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    judges: int = 0
    icp_share: float = 0.0
    control_act_rate: float = 0.0
    ours_act_rate: float = 0.0
    control_act_gap: float = 0.0
    mean_moved: float = 0.0
    dominant_defect: tuple[str, float] | None = None
    # Recorded as data; explicitly not thresholded
    ours_recall_rate: float = 0.0
    control_recall_rate: float = 0.0
    # Backwards compatibility attributes
    synthetic_sample_size: int = 0
    human_sample_size: int = 0
    synthetic_act_rate: float = 0.0
    human_act_rate: float = 0.0
    synthetic_recall_rate: float = 0.0
    human_recall_rate: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_reception(responses: list[AudienceResponse]) -> ReceptionVerdict:
    """Evaluate audience reception across our film and the human control.

    Enforces the 5 pre-registered thresholds committed in PRD §2.3:
    1. Human control must be present (§R12 fail-closed)
    2. MIN_JUDGES = 5
    3. MIN_ICP_SHARE = 0.60
    4. CONTROL_ACT_GAP_MAX = 0.15
    5. PASS_MOVED_MIN = 3.5
    6. KILL_DEFECT_SHARE = 0.60
    """
    if not responses:
        raise ReceptionError("no audience responses provided")

    control = [r for r in responses if r.condition == "control_human"]
    ours = [r for r in responses if r.condition == "ours"]

    if not control:
        raise ReceptionError(
            "missing required 'control_human' condition (§R12 positive control). "
            "A panel without human control measures only today's mood, not production impact."
        )
    if not ours:
        raise ReceptionError("missing required 'ours' condition in responses")

    judges = {r.judge_id for r in responses}
    total_judges = len(judges)

    icp_judges = {r.judge_id for r in responses if r.is_icp}
    icp_share = len(icp_judges) / total_judges if total_judges > 0 else 0.0

    control_act_rate = sum(1 for r in control if r.took_action) / len(control)
    ours_act_rate = sum(1 for r in ours if r.took_action) / len(ours)
    control_act_gap = max(0.0, control_act_rate - ours_act_rate)

    mean_moved = sum(float(r.moved_score) for r in ours) / len(ours)

    # 24h unprompted recall recorded as data, not thresholded (§2.3)
    ours_recall_rate = sum(1 for r in ours if r.unprompted_recall_24h) / len(ours)
    control_recall_rate = sum(1 for r in control if r.unprompted_recall_24h) / len(control)

    # Dominant defect on our films (counted once per judge)
    defects_by_judge: dict[str, set[str]] = {}
    for r in ours:
        d = r.defect.strip().lower()
        if d:
            defects_by_judge.setdefault(r.judge_id, set()).add(d)
    defect_counts = Counter(d for dset in defects_by_judge.values() for d in dset)

    dominant_defect: tuple[str, float] | None = None
    if defect_counts and total_judges > 0:
        d_name, d_count = defect_counts.most_common(1)[0]
        dominant_defect = (d_name, d_count / total_judges)

    reasons: list[str] = []
    if total_judges < MIN_JUDGES:
        reasons.append(
            f"only {total_judges} judge(s); {MIN_JUDGES} required. Below this one person's "
            "taste dominates the result."
        )

    if icp_share < MIN_ICP_SHARE:
        reasons.append(
            f"ICP share {icp_share:.1%} is below {MIN_ICP_SHARE:.1%} minimum. "
            "A panel of non-ICP members measures politeness, not founder response."
        )

    if control_act_gap > CONTROL_ACT_GAP_MAX:
        reasons.append(
            f"control act gap {control_act_gap:.1%} exceeds {CONTROL_ACT_GAP_MAX:.1%} ceiling "
            f"(control act: {control_act_rate:.1%}, ours act: {ours_act_rate:.1%}). "
            "Synthetic presentation significantly suppresses audience follow-through."
        )

    if mean_moved < PASS_MOVED_MIN:
        reasons.append(
            f"mean moved score {mean_moved:.2f} is below {PASS_MOVED_MIN:.2f} minimum. "
            "3 is the shrug; this needs to be better than 'fine, I guess'."
        )

    if dominant_defect is not None and dominant_defect[1] >= KILL_DEFECT_SHARE:
        reasons.append(
            f"{dominant_defect[1]:.0%} of judges independently named the same defect "
            f"({dominant_defect[0]!r}); ceiling is {KILL_DEFECT_SHARE:.0%}. "
            "An average cannot hide a tell that most judges noticed."
        )

    passed = len(reasons) == 0

    return ReceptionVerdict(
        passed=passed,
        reasons=reasons,
        judges=total_judges,
        icp_share=icp_share,
        control_act_rate=control_act_rate,
        ours_act_rate=ours_act_rate,
        control_act_gap=control_act_gap,
        mean_moved=mean_moved,
        dominant_defect=dominant_defect,
        ours_recall_rate=ours_recall_rate,
        control_recall_rate=control_recall_rate,
        synthetic_sample_size=len(ours),
        human_sample_size=len(control),
        synthetic_act_rate=ours_act_rate,
        human_act_rate=control_act_rate,
        synthetic_recall_rate=ours_recall_rate,
        human_recall_rate=control_recall_rate,
    )


def write_reception_manifest(
    path: Path,
    verdict: ReceptionVerdict,
    responses: list[AudienceResponse],
) -> Path:
    """Write reception.json containing individual judge rows and summary verdict."""
    payload = {
        "verdict": verdict.to_json(),
        "responses": [r.to_dict() for r in responses],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


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
        "judges": verdict.judges,
        "icp_share": verdict.icp_share,
        "ours_act_rate": verdict.ours_act_rate,
        "control_act_rate": verdict.control_act_rate,
        "control_act_gap": verdict.control_act_gap,
        "mean_moved": verdict.mean_moved,
        "dominant_defect": verdict.dominant_defect,
        "ours_recall_rate": verdict.ours_recall_rate,
        "control_recall_rate": verdict.control_recall_rate,
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
        help="JSON file with list of audience responses [{'judge_id': ..., 'condition': ..., 'took_action': ..., 'moved_score': ...}]",
    )
    parser.add_argument("--json", action="store_true", help="output as JSON")
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        items = data.get("responses", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ReceptionError(
                "input JSON must be a list of responses or an object with a 'responses' array"
            )
        allowed_fields = {f.name for f in fields(AudienceResponse)}
        responses = [
            AudienceResponse(**{k: v for k, v in r.items() if k in allowed_fields})
            if isinstance(r, dict)
            else r
            for r in items
        ]
        verdict = evaluate_reception(responses)
        if args.json:
            print(json.dumps(verdict.to_json(), indent=2))
        else:
            status = "PASS" if verdict.passed else "FAIL"
            print(f"Reception Verdict: {status}")
            print(f"Judges: {verdict.judges} (ICP share: {verdict.icp_share:.1%})")
            print(f"Ours Act Rate: {verdict.ours_act_rate:.1%}")
            print(f"Control Act Rate: {verdict.control_act_rate:.1%}")
            print(f"Control Act Gap: {verdict.control_act_gap:.1%}")
            print(f"Mean Moved: {verdict.mean_moved:.2f}")
            print(f"Unprompted 24h Recall (ours): {verdict.ours_recall_rate:.1%}")
            if verdict.dominant_defect:
                print(
                    f"Dominant Defect: {verdict.dominant_defect[0]} ({verdict.dominant_defect[1]:.1%})"
                )
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
