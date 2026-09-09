"""Capture rules — what a shot list owes a CAMERA, and only when there is one.

A rendered shot list instructs a model; a live-action one instructs a person with a camera and a
finite amount of time. The failure modes are different in kind: a bad render costs credits and is
re-rendered, a bad shoot costs a second shoot, because the light has changed and the shirt is in
the wash. These rules exist to make the second shoot unnecessary.

Every rule here is gated on ``live_action``, in both directions. The capture fields are refused on
a rendered list — a render has no camera to lock — and the lock-off rules never fire on one, so
adding this module changes nothing about how a rendered list lints today.

The composite regex is keyed on SHAPE, never on a wordlist of effects. What makes a shot a
composite is that TWO PIECES OF FOOTAGE HAVE TO SIT IN ONE FRAME, and that shows up as a verb of
joining ("comped in", "keyed over", "replaced with") or as a named plate. It deliberately does not
fire on "the effect of the change on revenue", which is prose about a subject, not a direction to
a camera — that class of false positive is what a wordlist produces, and the negative controls
below pin each one.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "CAPTURE_FIELDS",
    "_lint_capture_fields_mode",
    "_lint_lockoff_for_composites",
    "_lint_some_shot_is_locked_off",
    "render_shotlist_markdown",
    "write_shotlist_markdown",
]

#: The four fields that carry the capture contract. Legal only under `live_action`.
CAPTURE_FIELDS: tuple[str, ...] = ("lockoff", "dead_zone", "coverage", "frame_margin")

#: Fields scanned for composite intent. `spoken` is exempt: a line may legitimately mention an
#: effect, and it is read aloud rather than executed by a camera operator.
_SCANNED_FIELDS: tuple[str, ...] = ("visual", "motion_prompt", "camera", "stability")

#: Two pieces of footage in one frame. Keyed on the JOIN, not on a vocabulary of effects.
_COMPOSITE_RE = re.compile(
    r"\bcomp(?:ed|osited|ositing)\b"
    r"|\bkey(?:ed|ing)\s+(?:in|out|over|onto)\b"
    r"|\bgreen\s?screen\b|\bchroma\s?key\b"
    r"|\b(?:clean\s+)?plate\b"
    r"|\breplac(?:e|ed|ing)\s+(?:\S+\s+){0,3}?with\b"
    r"|\b(?:overlaid|overlay|matted|matte)\s+(?:on|onto|over|into)\b"
    r"|\binsert(?:ed)?\s+(?:into|over)\s+(?:the\s+)?(?:frame|shot|monitor|screen)\b"
    r"|\bscreen\s+(?:keyed|comped|replaced)\b"
    r"|\bmotion[\s-]?track(?:ed|ing)?\b",
    re.IGNORECASE,
)


def _composite_hit(shot: dict) -> str:
    for field_name in _SCANNED_FIELDS:
        value = shot.get(field_name)
        if isinstance(value, str) and value:
            match = _COMPOSITE_RE.search(value)
            if match:
                return f"{field_name}: {match.group(0)!r}"
    return ""


def _declared(shot: dict) -> list[str]:
    return [f for f in CAPTURE_FIELDS if shot.get(f) not in (None, "", [])]


def _lint_capture_fields_mode(
    shot: dict, prefix: str, errors: list[str], *, live_action: bool
) -> None:
    """The capture fields are direction for a camera, so a rendered list may not carry them."""
    if live_action:
        return
    present = _declared(shot)
    if present:
        errors.append(
            f"{prefix} carries capture field(s) {present} on a RENDERED shot list. A render has "
            "no camera to lock, no take to cover and no continuity to break, so these are "
            "directions nobody can carry out — and a shot list that reads as though someone will "
            "film it is one an operator may try to. Set top-level "
            '`capture_mode: "live_action"` if this really is a shoot, or drop the fields.'
        )


def _lint_lockoff_for_composites(
    shot: dict, prefix: str, warnings: list[str], *, live_action: bool
) -> None:
    """A shot that will be composited must be locked off — stabilisation cannot rescue it later."""
    if not live_action:
        return
    hit = _composite_hit(shot)
    if hit and not shot.get("lockoff"):
        warnings.append(
            f"{prefix} describes a composite ({hit}) and does not set `lockoff: true`. Two "
            "handheld pieces will never line up in one frame, and stabilising afterwards makes it "
            "worse rather than better: stabilisation crops and warps, which changes the very "
            "geometry the composite depends on. One tripod shot on the day beats an hour in post."
        )


def _lint_some_shot_is_locked_off(shots: list, warnings: list[str], *, live_action: bool) -> None:
    """If anything in this cut is composited, at least one shot has to be on a tripod."""
    if not live_action or not isinstance(shots, list):
        return
    composites = [i for i, s in enumerate(shots, 1) if isinstance(s, dict) and _composite_hit(s)]
    if composites and not any(isinstance(s, dict) and s.get("lockoff") for s in shots):
        warnings.append(
            f"shot(s) {composites} describe a composite and NO shot in this list is locked off. "
            "A composite needs at least two pieces that share a frame; if every one of them "
            "moves, there is nothing to align them against. Lock the plate at minimum."
        )


# ── the phone-readable twin ───────────────────────────────────────────────────────────────────


def _coverage_text(value: object) -> str:
    if isinstance(value, dict):
        takes = value.get("takes")
        parts = [f"{takes} takes" if takes else ""]
        if value.get("plate"):
            parts.append("plus a clean plate")
        return " ".join(p for p in parts if p) or "—"
    return str(value) if value else "—"


def render_shotlist_markdown(doc: dict, *, source: str = "") -> str:
    """The shot list a person reads ON SET, derived from the JSON and never edited.

    The operator does not read `.shots.json` on a phone at seven in the morning, so the contract
    that only exists in JSON is a contract that does not reach the shoot. This is a projection of
    that file — regenerate it, never edit it; nothing reads the markdown back.
    """
    live = str(doc.get("capture_mode", "rendered") or "") == "live_action"
    lines = [
        f"# Shot list — {doc.get('source_item', 'untitled')}",
        "",
        f"> **Generated** from `{source or 'the shot list JSON'}` by "
        "`python -m gtm_core.shots_lint --render-shotlist`. Edit the JSON and re-render; nothing "
        "reads this file back.",
        "",
        f"- mode: **{'live action — you are filming this' if live else 'rendered'}**",
        f"- total: {doc.get('total_duration_s', '?')}s",
    ]
    scaffold = doc.get("style_scaffold")
    if isinstance(scaffold, dict) and scaffold.get("look"):
        lines.append(f"- look: {scaffold['look']}")
    lines.append("")

    for i, shot in enumerate(doc.get("shots") or [], 1):
        if not isinstance(shot, dict):
            continue
        lines += [
            f"## Shot {i} — {shot.get('duration_s', '?')}s · {shot.get('role', 'presenter')}",
            "",
            f"**Set up.** {shot.get('visual', '—')}",
            "",
            f"**Camera.** {shot.get('camera', '—')}",
        ]
        if live:
            lines += [
                "",
                f"**Lock off.** {'YES — tripod, do not move' if shot.get('lockoff') else 'not required'}",
                f"**Dead zone.** {shot.get('dead_zone') or '—'}",
                f"**Coverage.** {_coverage_text(shot.get('coverage'))}",
                f"**Frame margin.** {shot.get('frame_margin') or '—'}",
            ]
        action = shot.get("motion_prompt")
        if action:
            lines += ["", f"**Action.** {action}"]
        spoken = shot.get("spoken")
        if spoken:
            lines += ["", f"**Line.** “{spoken}”"]
        for label, key in (("Wardrobe", "wardrobe"), ("Continuity", "stability")):
            if shot.get(key):
                lines.append(f"**{label}.** {shot[key]}")
        lines.append("")

    if live:
        lines += [
            "---",
            "",
            "**Before you wrap:** every beat over-shot, a clean plate for anything composited, "
            "handles at both ends of every take, and the continuity notes written down while the "
            "set still looks the way they describe.",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_shotlist_markdown(doc: dict, json_path: Path) -> Path:
    """Write the twin beside its JSON and return the path."""
    dst = json_path.with_suffix("")
    if dst.suffix == ".shots":
        dst = dst.with_suffix("")
    dst = dst.with_name(dst.name + ".shotlist.md")
    dst.write_text(render_shotlist_markdown(doc, source=json_path.name), encoding="utf-8")
    return dst
