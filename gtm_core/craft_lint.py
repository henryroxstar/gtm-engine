"""Craft and performance lint rules (10x Video PRD §2, §4).

Provides pre-spend advisory and validation checks for:
- Character identity consistency (_lint_element_ref, _lint_visual_invariant)
- AI look prevention (_lint_skin_scaffold, _lint_shot_size_magnitude)
"""

from __future__ import annotations

__all__ = [
    "_lint_element_ref",
    "_lint_visual_invariant",
    "_lint_skin_scaffold",
    "_lint_shot_size_magnitude",
]


def _lint_element_ref(shot: dict, prefix: str, errors: list[str]) -> None:
    """A shot with a character in frame (expression non-empty) must declare element_ref.

    Character identity cannot cross the I2V boundary without an explicit element anchor.
    B-roll, screen capture, and shots without facial expressions do not require element_ref.
    """
    if not isinstance(shot, dict):
        return
    role = str(shot.get("role", "") or "").strip().lower()
    if role in ("broll", "screen"):
        return
    expression = str(shot.get("expression", "") or "").strip()
    if expression:
        eref = str(shot.get("element_ref", "") or "").strip()
        if not eref:
            errors.append(
                f"{prefix} has a person in frame (expression is set) but is missing required "
                "`element_ref`. Anchor the character to an element library slug for identity consistency."
            )


def _lint_visual_invariant(shots: list[dict], warnings: list[str]) -> None:
    """Shots referencing the same element_ref should share the same visual_invariant.

    Mismatched wardrobe or lighting cues across the same character cause visible identity drift.
    """
    seen: dict[str, str] = {}
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        eref = str(shot.get("element_ref", "") or "").strip()
        if not eref:
            continue
        inv = str(shot.get("visual_invariant", "") or "").strip()
        if eref in seen:
            if seen[eref] != inv:
                warnings.append(
                    f"Mismatched visual_invariant across shots for element {eref!r}: "
                    f"saw {seen[eref]!r} and {inv!r}. Keep visual_invariant consistent to prevent identity drift."
                )
                break
        else:
            seen[eref] = inv


def _lint_skin_scaffold(
    shot: dict,
    prefix: str,
    warnings: list[str],
    *,
    skin_scaffold: str | None = None,
    kit: dict | None = None,
) -> None:
    """A shot directing a face should carry a skin-scaffold counter-phrase.

    AI video/image models default to plasticky, smoothed skin. Carrying a counter-phrase
    in visual or motion_prompt preserves natural skin texture.
    """
    if not isinstance(shot, dict):
        return
    expression = str(shot.get("expression", "") or "").strip()
    if not expression:
        return

    scaffold = skin_scaffold
    if scaffold is None and kit is not None:
        scaffold = kit.get("identity", {}).get("skin_scaffold")

    visual = str(shot.get("visual", "") or "").lower()
    motion = str(shot.get("motion_prompt", "") or "").lower()
    combined = f"{visual} {motion}"

    if scaffold and str(scaffold).strip():
        phrase = str(scaffold).strip().lower()
        if phrase not in combined:
            warnings.append(
                f"{prefix} directs a face expression but lacks the configured "
                f"skin_scaffold counter-phrase ({phrase!r}). Add it to visual or motion_prompt."
            )
    else:
        common_markers = ("skin texture", "visible pores", "unretouched", "natural skin")
        if not any(m in combined for m in common_markers):
            warnings.append(
                f"{prefix} directs a face expression but does not carry a skin_scaffold "
                "counter-phrase (e.g. 'natural skin texture, visible pores')."
            )


def _lint_shot_size_magnitude(shot: dict, prefix: str, warnings: list[str]) -> None:
    """Shot size and facial movement magnitude must remain congruent.

    In extreme close-ups, multi-region movement exaggerates into caricature.
    In wide shots, facial cues are invisible without body motion.
    """
    if not isinstance(shot, dict):
        return
    shot_size = str(shot.get("shot_size") or shot.get("camera") or "").lower()
    expression = str(shot.get("expression") or "").strip()
    motion_prompt = str(shot.get("motion_prompt") or "").strip()

    is_ecu = any(k in shot_size for k in ("extreme_closeup", "extreme close-up", "extreme closeup"))
    if is_ecu and expression:
        from .shots_lint.expression import _normalize, _regions_named

        text = _normalize(expression)
        regions = _regions_named(text)
        if len(regions) >= 3:
            warnings.append(
                f"{prefix} has shot_size={shot_size!r} with {len(regions)} moving facial regions. "
                "Magnitude mismatch: in extreme close-ups, subtle movements play as huge; "
                "restrict to a single subtle cue."
            )

    is_wide = (
        "wide" in shot_size.split() or "wide_shot" in shot_size or shot_size.startswith("wide")
    )
    if is_wide and expression and not motion_prompt:
        warnings.append(
            f"{prefix} has shot_size='wide' with face-only direction and no motion_prompt. "
            "At wide framing, subtle facial cues are invisible; the body carries it at this distance."
        )
