"""First/last-frame (keyframe) shot rules — the end frame is a capability, not a decoration.

A keyframe shot hands the model BOTH ends of its move: a start still and an end still, with the
render interpolating between them. Two things follow, and both are checked here.

**The end frame belongs to b-roll only.** A presenter shot resolves an identity-faithful engine
through `roles.presenter`; a keyframe shot resolves `roles.keyframe_broll`. Those are different
engines with different capability requirements, and an `end_frame` on a presenter shot is a
request the presenter lane cannot serve. Refusing it here — at plan time, before spend — is the
same trade the registry makes: a type error rather than a judgement call.

**A shown move needs no described one.** This is the rule that is easy to get backwards. Elsewhere
in this linter a thin `motion_prompt` is a defect, because an image-to-video model handed a start
frame and no action renders a person sitting still. A keyframe shot is the exception: the move is
already specified by the two frames, so a long prose motion prompt now COMPETES with the
interpolation rather than directing it. Hence a WARN, not an ERROR, and only on a shot that
actually carries an `end_frame` — the thin-prompt rule keeps its full force everywhere else.
"""

from __future__ import annotations

import re

#: The only shot role a last frame is legal on. `screen` maps to the same registry role as
#: `broll`, but a screen recording has no interpolable move — its content IS the change.
_KEYFRAME_ROLE = "broll"

#: Past this, a keyframe motion prompt is describing what the two frames already show. Not a hard
#: ceiling: a terse prompt still earns its place naming the QUALITY of the move ("slow push,
#: settling") rather than its geometry.
KEYFRAME_MOTION_MAX_WORDS = 12

#: Two MOVES in a keyframe prompt, not two clauses of prose. The distinction cost a false
#: positive: keyed on the comma, this rule warned on "slow push, settling" — the exact terse form
#: the render body recommends, where the comma separates a move from its MANNER rather than from a
#: second move. What actually marks a second move is a SEQUENCER, so that is what is matched.
_CLAUSE_SPLIT_RE = re.compile(
    r";|\s+—\s+|\s+--\s+|\bthen\b|\bafter which\b|\bfollowed by\b|\bbefore\b",
    re.IGNORECASE,
)


def is_keyframe_shot(shot: dict) -> bool:
    """The ONE definition of "this shot carries a last frame".

    Read by both rules here, by the thin-motion_prompt exemption in `camera.py`, and by
    `gtm_core.keyframe_request` — five sites that used to each restate the predicate. When the
    field grows (an object with a hold, a role condition), this is the only line that changes.
    """
    return bool(str(shot.get("end_frame", "") or "").strip())


def _lint_end_frame_role(shot: dict, prefix: str, errors: list[str]) -> None:
    """An `end_frame` is legal only on a b-roll shot; any other role cannot resolve an engine."""
    if not is_keyframe_shot(shot):
        return
    role = str(shot.get("role", "presenter") or "")
    if role != _KEYFRAME_ROLE:
        errors.append(
            f"{prefix} carries `end_frame` on role {role!r}, and a last frame is legal only on "
            f"role {_KEYFRAME_ROLE!r}. A keyframe shot resolves `roles.keyframe_broll`, which "
            "requires an engine that accepts an end image; the presenter lane resolves an "
            "identity-faithful, lip-syncing engine instead, and no engine serves both. Either "
            f"set `role: {_KEYFRAME_ROLE}`, or drop `end_frame` and direct the move in "
            "`motion_prompt`"
        )


def _lint_keyframe_motion_prompt(shot: dict, prefix: str, warnings: list[str]) -> None:
    """On a keyframe shot the move is SHOWN by the two frames, so its motion prompt stays terse."""
    if not is_keyframe_shot(shot):
        return
    motion = str(shot.get("motion_prompt", "") or "").strip()
    if not motion:
        return  # the missing-motion_prompt ERROR is _lint_motion_prompt's to raise, not ours

    words = len(motion.split())
    if words > KEYFRAME_MOTION_MAX_WORDS:
        warnings.append(
            f"{prefix} has both an `end_frame` and a {words}-word `motion_prompt` (over "
            f"{KEYFRAME_MOTION_MAX_WORDS}) — the two stills already specify where the move starts "
            "and ends, so prose describing the same geometry competes with the interpolation "
            "instead of directing it. Keep what the frames cannot say (pace, easing, texture) and "
            "cut what they already show"
        )

    clauses = [c for c in _CLAUSE_SPLIT_RE.split(motion) if c.strip()]
    if len(clauses) > 1:
        warnings.append(
            f"{prefix} has an `end_frame` and a {len(clauses)}-clause `motion_prompt` — a "
            "keyframe shot has one interval to spend and can only arrive once. Two moves means "
            "either a second shot with its own pair of frames, or one move stated once"
        )
