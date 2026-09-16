"""Pixel-level vision inspection gate (Q4) — PRD Phase 4.

Inspects rendered video frames at beat boundaries:
  - Extracts boundary frames or analyzes static stills
  - Action Unit analysis (AU12 lip corner pull, AU4 brow lowerer)
  - Checks congruence between directed script emotion and rendered facial markers
  - Refuses contradictory or dead-eyed synthetic takes
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

__all__ = [
    "ActionUnitFinding",
    "PixelGateVerdict",
    "inspect_boundary_frames",
]


@dataclass(frozen=True)
class ActionUnitFinding:
    frame_time_s: float
    au_detected: list[str]  # e.g. ["AU12", "AU4"]
    directed_emotion: str
    is_congruent: bool
    notes: str = ""


@dataclass
class PixelGateVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    findings: list[ActionUnitFinding] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def inspect_boundary_frames(
    shots: list[dict],
    extracted_frames: list[dict],
) -> PixelGateVerdict:
    """Evaluate pixel congruency at beat boundaries.

    Each shot specifies duration and target expression/emotion.
    Each frame in extracted_frames provides frame_time_s, detected action units, etc.
    """
    findings: list[ActionUnitFinding] = []
    reasons: list[str] = []

    # Map time to shot
    current_time = 0.0
    shot_map: list[tuple[float, float, str, str]] = []  # (start, end, emotion, role)
    for s in shots:
        dur = float(s.get("duration_s", 0.0))
        expr = str(s.get("expression") or "neutral").lower()
        role = str(s.get("role") or "broll").lower()
        shot_map.append((current_time, current_time + dur, expr, role))
        current_time += dur

    for f in extracted_frames:
        t = float(f.get("frame_time_s", 0.0))
        aus = [str(au).upper() for au in f.get("au_detected", [])]
        target_shot = next(
            (item for item in shot_map if item[0] <= t <= item[1]),
            None,
        )
        if not target_shot:
            continue

        _, _, expr, role = target_shot
        if role != "presenter":
            # B-roll / screen shots do not enforce facial AU congruency
            continue

        congruent = True
        notes = "congruent"
        # Check high mismatch cues:
        # If directed smile / breakthrough (AU12), but AU4 (brow furrow / anger / confusion) dominates without AU12
        if (
            any(w in expr for w in ("smile", "joy", "relief", "lifted"))
            and "AU4" in aus
            and "AU12" not in aus
        ):
            congruent = False
            notes = "Directed smile/relief but detected brow furrow (AU4) without lip corner pull (AU12)"
            reasons.append(f"At {t:.1f}s: {notes}")
        elif (
            any(w in expr for w in ("concern", "furrow", "tension"))
            and "AU12" in aus
            and "AU4" not in aus
        ):
            congruent = False
            notes = "Directed tension/concern but detected smile (AU12) without brow lowerer (AU4)"
            reasons.append(f"At {t:.1f}s: {notes}")

        findings.append(
            ActionUnitFinding(
                frame_time_s=t,
                au_detected=aus,
                directed_emotion=expr,
                is_congruent=congruent,
                notes=notes,
            )
        )

    # Check blink rate for long presenter shots (>= 5.0 seconds)
    for start, end, expr, role in shot_map:
        dur = end - start
        if role == "presenter" and dur >= 5.0:
            shot_frames = [
                f for f in extracted_frames if start <= float(f.get("frame_time_s", 0.0)) <= end
            ]
            has_blink = any(
                f.get("blink", False)
                or "AU45" in [str(au).upper() for au in f.get("au_detected", [])]
                for f in shot_frames
            )
            if shot_frames and not has_blink:
                note = f"review: static_gaze (zero blinks detected over {dur:.1f}s presenter shot)"
                reasons.append(f"At {start:.1f}s: {note}")
                findings.append(
                    ActionUnitFinding(
                        frame_time_s=start,
                        au_detected=[],
                        directed_emotion=expr,
                        is_congruent=False,
                        notes=note,
                    )
                )

    passed = len(reasons) == 0
    return PixelGateVerdict(passed=passed, reasons=reasons, findings=findings)
