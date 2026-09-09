from __future__ import annotations

import re

#: Ceiling on the share of shots whose role is ``presenter``. Above this the asset is a talking
#: head with cutaways rather than a video, which is what "it feels boring, it's almost entirely
#: just face shots" describes.
MAX_PRESENTER_SHARE = 0.6

#: Below this many shots the presenter share is advisory — a 2-shot piece cannot be split finely
#: enough for a ratio to mean anything.
_PRESENTER_QUOTA_MIN_SHOTS = 4


def _lint_shot_mix(
    shots: list, errors: list[str], warnings: list[str], *, live_action: bool = False
) -> None:
    """Cap the presenter share, and ask for at least one shot that is not the presenter's face.

    A quota on ``presenter`` alone is not sufficient: 3 presenter shots plus 3 abstract b-roll
    inserts clears any ratio and is still six variations on "a man talking". So the presenter
    ceiling is an error, and the absence of a ``screen`` shot — a real capture of the thing being
    discussed — is a warning, because that is the shot that carries an enterprise story.
    """
    roles = [
        str((s.get("role") or "presenter") if isinstance(s, dict) else "presenter") for s in shots
    ]
    total = len(roles)
    presenters = sum(1 for r in roles if r == "presenter")
    share = presenters / total if total else 0.0

    if live_action:
        # The ceiling is render economics, not a format rule: it exists because a GENERATED
        # talking head is the expensive, low-quality shot. Nothing here is generated, so a
        # presenter-dominant list is exactly what this lane is for. The `screen` warning below
        # still applies — showing the thing you are talking about is good advice however the
        # shot is produced.
        pass
    elif total >= _PRESENTER_QUOTA_MIN_SHOTS and share > MAX_PRESENTER_SHARE:
        errors.append(
            f"{presenters} of {total} shots are role=presenter ({share:.0%}) — ceiling is "
            f"{MAX_PRESENTER_SHARE:.0%}. An asset that is almost entirely face shots reads as "
            "boring however good the script is; convert beats to role=screen (a capture of the "
            "actual thing) or role=broll (data, motion graphic, product surface)"
        )
    elif not live_action and total < _PRESENTER_QUOTA_MIN_SHOTS and share == 1.0 and total > 1:
        warnings.append(
            f"all {total} shots are role=presenter — too few shots for the "
            f"{MAX_PRESENTER_SHARE:.0%} ceiling to bind, but the asset is still all face"
        )

    if total >= _PRESENTER_QUOTA_MIN_SHOTS and not any(r == "screen" for r in roles):
        warnings.append(
            "no shot has role=screen — nothing in this asset shows the thing being talked about. "
            "For a product or incident story, a real screen capture is the highest-value shot in "
            "the piece and the cheapest to produce"
        )


#: Phrases that ask a VIDEO model to render legible text inside the frame. Diffusion video models
#: paint pixel patterns that *resemble* text — they do not typeset — so this reliably produces the
#: garbled signage the operator reported on 2026-08-19. Universal practice across every vendor
#: researched: generate clean video, burn text in post (which `gtm_core.captions` already does).
#:
#: ⚠ SCOPE: video shot lists ONLY, and only `visual` / `motion_prompt`. `carousel-visuals` Mode V5
#: renders full-text cards in-image ON PURPOSE via nano_banana_pro, with its own
#: verify-the-returned-text loop. That is a STILL — checkable before use — so none of the video
#: failure mode applies, and this rule must never be reachable from the carousel path.
_IN_FRAME_TEXT_RE = re.compile(
    r"\b("
    r"(?:sign|banner|placard|poster|screen|caption|subtitle|label|headline|title|logo|wordmark)"
    r"\s+(?:that\s+)?(?:read(?:s|ing)?|say(?:s|ing)?|display(?:s|ing)?|show(?:s|ing)?|with)"
    r"|text\s+(?:read(?:s|ing)?|say(?:s|ing)?|overlay|on\s+screen|appears)"
    r"|(?:the\s+)?words?\s+[\"\u201c]"
    r")",
    re.IGNORECASE,
)

#: Fields a text-in-frame instruction can actually reach the model through. Deliberately narrow,
#: for the reason `_lint_speech_cue` is narrow: scanning `expression` once passed on an incidental
#: "telling a friend". A rule that fires on prose nobody sends is a rule people learn to ignore.
_IN_FRAME_TEXT_SCANNED_FIELDS = ("visual", "motion_prompt")


def _lint_no_in_frame_text(shot: dict, prefix: str, errors: list[str]) -> None:
    """Refuse a prompt that asks the video model to render text inside the frame."""
    for field_name in _IN_FRAME_TEXT_SCANNED_FIELDS:
        value = str(shot.get(field_name, "") or "")
        match = _IN_FRAME_TEXT_RE.search(value)
        if not match:
            continue
        errors.append(
            f"{prefix}.{field_name} asks the video model to render in-frame text "
            f"({match.group(0)!r}). Diffusion video models paint shapes that resemble text rather "
            "than typesetting it, which is where the garbled frames came from — this is a model "
            "limitation, not a prompt-quality problem. Describe the scene without the text and let "
            "gtm_core.captions burn it in at finish time."
        )
