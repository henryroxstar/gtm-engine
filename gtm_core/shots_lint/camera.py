from __future__ import annotations

import re
from pathlib import Path

from ..storyboard import MAX_EDIT_DEPTH
from .keyframe import is_keyframe_shot

#: Named compounds are matched (and consumed) first so "dolly zoom" counts as ONE move family,
#: not dolly + zoom. Order matters within this list.
_COMPOUND_MOVES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("dolly zoom", r"dolly[\s-]?zoom(?:s|ing|ed)?"),
        ("crash zoom", r"crash[\s-]?zoom(?:s|ing|ed)?"),
        ("whip pan", r"whip[\s-]?pan(?:s|ning|ned)?"),
        ("push-in", r"push(?:es|ing|ed)?[\s-]?in"),
        ("pull-out", r"pull(?:s|ing|ed)?[\s-]?(?:out|back)"),
    )
)

_SIMPLE_MOVES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("zoom", r"\bzoom(?:s|ing|ed)?\b"),
        ("pan", r"\bpan(?:s|ning|ned)?\b"),
        ("tilt", r"\btilt(?:s|ing|ed)?\b"),
        ("dolly", r"\bdoll(?:y(?:ing)?|ies)\b"),
        ("truck", r"\btruck(?:s|ing)?\b"),
        ("orbit", r"\borbit(?:s|ing)?\b"),
        ("arc", r"\barc(?:s|ing)?\b"),
        ("crane", r"\bcrane(?:s)?\b"),
        ("jib", r"\bjib\b"),
        ("pedestal", r"\bpedestal(?:s)?\b"),
        ("tracking", r"\btrack(?:s|ing)?\b"),
        ("hyperlapse", r"\bhyperlapse\b"),
        ("timelapse", r"\btime[\s-]?lapse\b"),
        ("roll", r"\broll(?:s|ing)?\b"),
        ("fpv/drone", r"\bfpv\b|\bdrone\b"),
    )
)

#: Non-move camera vocabulary — framing, angle, rig, and stillness terms. Recognition only:
#: matching one of these (or a move) is what makes a camera string "recognized".
_FRAMING_TERMS = re.compile(
    r"\bstatic\b|\blocked\b|\bhandheld\b|\bsteadicam\b|\bgimbal\b|\bwide(?:r|st)?\b"
    r"|\bclose[\s-]?up\b"
    r"|\bextreme close\b|\bmedium\b|\bfull shot\b|\bmacro\b|\boverhead\b|\baerial\b"
    r"|\blow angle\b|\bhigh angle\b|\beye level\b|\bpov\b|\bdutch\b|\bover[\s-]the[\s-]shoulder\b"
    r"|\bestablishing\b|\btwo[\s-]shot\b|\bscreen (?:capture|record(?:ing)?)\b|\bfisheye\b"
    r"|\bremains still\b|\bstill\b|\bbird'?s[\s-]eye\b|\bworm'?s[\s-]eye\b"
    # Reverse coverage — the shot from the other side of the subject. Standard grammar, and the
    # one angle the hosted re-angle tools name explicitly ("a new angle from directly behind the
    # person"), so a script that asks for it was warning on a term the models do understand.
    r"|\breverse (?:angle|shot)\b|\bfrom behind\b|\bbehind the subject\b"
)

#: Negation phrasing that must not appear in a prompt field (positive-phrasing rule).
_NEGATION_RE = re.compile(
    r"\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bavoid(?:s|ing)?\b|\bdon'?t\b|\bdo not\b"
    r"|\bdoesn'?t\b|\bwon'?t\b",
    re.IGNORECASE,
)

#: Per-shot free-text fields the negation rule scans. ``spoken`` (dialogue) and the designated
#: exclusion surfaces (``negative``, at scaffold level) are exempt by design.
_NEGATION_SCANNED_SHOT_FIELDS = (
    "camera",
    "visual",
    "motion_prompt",
    "lighting",
    "lens",
    "wardrobe",
    "expression",
    "stability",
    "sfx",
)


def _move_families(camera_text: str) -> list[str]:
    """Distinct camera-move families named in the text, compounds counted once."""
    text = camera_text.lower()
    found: list[str] = []
    for name, pattern in _COMPOUND_MOVES:
        if pattern.search(text):
            found.append(name)
            text = pattern.sub(" ", text)
    for name, pattern in _SIMPLE_MOVES:
        if pattern.search(text):
            found.append(name)
    return found


def _lint_camera(camera_text: str, prefix: str, errors: list[str], warnings: list[str]) -> None:
    """One camera move per shot, in vocabulary the models recognise.

    Stacked moves ("dolly in while zooming and panning left") are the documented cause of warped
    geometry and unstable framing across every current model; a named compound ("dolly zoom") is
    one move, not two. An unrecognised camera term warns rather than blocks.
    """
    moves = _move_families(camera_text)
    if len(moves) > 1:
        errors.append(
            f"{prefix} stacks {len(moves)} camera moves ({', '.join(moves)}) in one shot — "
            "one move per shot; split the beat instead (documented warped-geometry failure "
            "mode on every current video model)"
        )
    if not moves and not _FRAMING_TERMS.search(camera_text.lower()):
        warnings.append(
            f"{prefix} camera {camera_text!r} matches no known move or framing term — "
            "prefer the controlled vocabulary (video-render/references/prompt-recipes.md)"
        )


def _lint_negation(value: str, where: str, errors: list[str]) -> None:
    """Prompt fields are phrased POSITIVELY — a negation leaves the banned noun in play.

    "no logo" keeps "logo" in the prompt the model conditions on. Exclusions belong in
    ``style_scaffold.negative`` as plain nouns, or in the shot's ``stability`` clause stated as
    what must stay constant. ``spoken`` and ``negative`` are exempt by design.
    """
    m = _NEGATION_RE.search(value)
    if m:
        errors.append(
            f"{where} uses negation phrasing ({m.group(0)!r}) — prompt fields are "
            "positive-only; move exclusions to style_scaffold.negative as plain nouns, or "
            "state the constraint positively in `stability`"
        )


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _lint_motion_prompt(shot: dict, prefix: str, errors: list[str], warnings: list[str]) -> None:
    """``motion_prompt`` is the field that says what HAPPENS. Absent it, an image-to-video model
    is handed a start frame, a camera note, and no action — and renders a person sitting still.

    Not hypothetical: on 2026-08-18 all six shots of a shipped asset omitted this field, V9 then
    found 3 of 6 motionless (47% of runtime), and the operator's verdict was "it feels boring,
    it's almost entirely just face shots". The field existed in the schema the whole time; nothing
    required it.
    """
    raw = shot.get("motion_prompt")
    motion = str(raw or "").strip()
    if not motion:
        errors.append(
            f"{prefix} missing required field `motion_prompt` — the model needs an ACTION, not "
            "just a start frame and a camera note, or it renders a person sitting still "
            "(documented 2026-08-18: 6/6 shots omitted this, V9 found 3/6 motionless)"
        )
        return

    camera = str(shot.get("camera", "") or "").strip()
    if camera and _normalize(motion) == _normalize(camera):
        errors.append(
            f"{prefix} motion_prompt merely restates `camera` ({motion!r}) — camera is how the "
            "LENS moves, motion_prompt is what the SUBJECT does; a restatement leaves the "
            "subject with nothing to do"
        )
        return

    # A KEYFRAME shot is the one place a three-word motion prompt is right: it carries an
    # `end_frame`, so the geometry of the move is already fixed by the two stills and the prompt
    # only has to name what they cannot show (pace, easing, texture). Without this exemption the
    # thin-prompt WARN and the keyframe brevity WARN in `keyframe.py` contradict each other, and
    # every keyframe shot ships carrying a warning that is wrong — which is how a warning column
    # stops being read at all.
    if len(motion.split()) < 4 and not is_keyframe_shot(shot):
        warnings.append(
            f"{prefix} motion_prompt {motion!r} is {len(motion.split())} words — too thin to "
            "direct a render; name the subject's action, and what changes over the shot"
        )

    _lint_gesture_is_not_a_diagram(motion, prefix, errors)


#: Gesture phrasings that ask the presenter to ILLUSTRATE a concept with their hands. An avatar
#: engine renders the metaphor, not the meaning, and the reliable output is a prop.
#:
#: This shipped twice on the same film. First as counting: "two fingers raised" on "Two, what can
#: it touch?" produced a peace sign, and the fix removed counting from three prompts. Then the
#: same class survived in a different costume — "one flat hand resting above the other to show two
#: stacked layers" rendered as the presenter holding a **wooden board**, which the operator caught
#: in the delivered cut. The first fix targeted the WORD (counting) instead of the SHAPE
#: (hands-as-diagram), so it could not see the second one.
#:
#: The rule: a motion prompt directs a PERSON. Numbers, layers, comparisons and structure are the
#: graphics' job — the film's own cards already carry all of them.
_GESTURE_AS_DIAGRAM = (
    ("finger", "fingers raised, held up or counted — the engine renders a counted hand shape"),
    ("stacked", "hands arranged to depict stacked layers — this rendered as a held board"),
    ("to show", "a gesture justified by what it SHOWS is a diagram, not a movement"),
    ("to represent", "a gesture standing in for a concept is a diagram, not a movement"),
    ("to indicate", "a gesture standing in for a concept is a diagram, not a movement"),
    ("one above the other", "hands positioned to depict layers — this rendered as a held board"),
    ("counting", "counting on the hands — the numerals belong in the graphics"),
    ("counts on", "counting on the hands — the numerals belong in the graphics"),
)


def _lint_gesture_is_not_a_diagram(motion: str, prefix: str, errors: list[str]) -> None:
    """A motion prompt describes a person moving, never a concept being illustrated."""
    low = _normalize(motion)
    for needle, why in _GESTURE_AS_DIAGRAM:
        if needle in low:
            errors.append(
                f"{prefix} motion_prompt contains {needle!r}: {why}. An avatar engine renders "
                f"the metaphor literally and hands the presenter a prop — counted fingers "
                f"(2026-08-29) and a wooden board (2026-08-30) both shipped this way. Describe "
                f"what the PERSON does ('hands resting still', 'one open palm toward camera') "
                f"and let the graphics carry the count, the layers and the comparison"
            )
            return


def _lint_reference_edit_depth(
    shot: dict, prefix: str, warnings: list[str], *, depth_by_path: dict[str, int]
) -> None:
    """Warn when a shot's reference_images point at a still deep in a delta-edit chain."""
    if not depth_by_path:
        # No storyboard was supplied, so no depth is knowable. Inert rather than guessing —
        # a lint that fires on absent evidence teaches the operator to ignore it.
        return
    for position, raw in enumerate(shot.get("reference_images") or [], start=1):
        depth = depth_by_path.get(Path(str(raw)).name)
        if depth is None or depth <= MAX_EDIT_DEPTH:
            continue
        warnings.append(
            f"{prefix}.reference_images[{position}] ({Path(str(raw)).name}) is {depth} edit(s) "
            f"from its hero still (cap {MAX_EDIT_DEPTH}). Reference quality is the ceiling on "
            "output quality and it degrades with every pass, so a shot conditioned this far down "
            "a chain no longer looks like the frame the operator approved — re-anchor from the "
            "hero, or accept the drift deliberately."
        )
