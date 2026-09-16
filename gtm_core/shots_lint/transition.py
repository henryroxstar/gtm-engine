from __future__ import annotations

# ── production.transition_in — how this shot is cut INTO ───────────────────────────────────────
#
# A shot may declare, under its free-form `production` namespace, the ONE editorial fact about a
# join that the picture itself cannot carry: whether the cut into this shot is a hard cut (the
# default, and the absence of the key), a DISSOLVE from the shot before it, a FADE through black,
# or a PUSH — a vertical feed-scroll where the outgoing shot slides up and out while the incoming
# shot slides up into frame from the bottom, both moving at once. `gtm_core.video_finish.stitch`
# consumes it per join; this module is the dependency-free home for the vocabulary, the same
# arrangement `overlay.py` has with the finish verb.
#
# WHY THIS RULE EXISTS. A shot list declared a 0.4s dissolve and a 0.5s fade and nothing in the
# tree read either: `stitch` had ONE global `crossfade_s` applied to every join, so the only ways
# to honour a per-shot transition were to dissolve every cut in the film or to stitch by hand.
# The declaration shipped as a note in a JSON file. A declared field needs a consumer, and a
# consumer needs a checked shape — that is what this module is.

#: The three kinds a shot list may ask for. A hard cut is the ABSENCE of the key, never a fourth
#: spelling: "transition_in": {"kind": "cut"} would be a way of writing the default twice.
TRANSITION_KINDS: frozenset[str] = frozenset({"dissolve", "fade", "push"})

#: Past a second the join stops being a join and becomes a beat of its own — and `stitch` must
#: refuse it anyway, because an overlap needs room in BOTH adjacent shots. Refusing at plan time
#: is free; refusing after the renders are paid for is not.
TRANSITION_MAX_S = 1.0


def transition_in(shot: dict) -> object | None:
    """This shot's raw ``production.transition_in``, or ``None`` when it declares none.

    Shared by the linter, the coverage lint and the finish helper so the three cannot disagree
    about whether a shot declares a transition at all. The value is returned UNVALIDATED —
    :func:`transition_errors` is the one place its shape is judged.
    """
    production = shot.get("production")
    if not isinstance(production, dict):
        return None
    return production.get("transition_in")


def transition_errors(entry: object, where: str) -> list[str]:
    """Everything wrong with one ``transition_in`` value, as messages — ``[]`` when it is sound."""
    if not isinstance(entry, dict):
        return [f"{where} is not an object"]
    errors: list[str] = []
    kind = entry.get("kind")
    if kind not in TRANSITION_KINDS:
        errors.append(
            f"{where}.kind {kind!r} is not one of {sorted(TRANSITION_KINDS)} — a hard cut is the "
            "absence of the key, not a kind"
        )
    duration = entry.get("duration_s")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not 0 < duration <= TRANSITION_MAX_S
    ):
        errors.append(
            f"{where}.duration_s {duration!r} must be a number in (0, {TRANSITION_MAX_S}] seconds"
        )
    return errors


def _lint_transition(shot: dict, prefix: str, errors: list[str], *, first: bool) -> None:
    """Check a shot's ``production.transition_in`` join spec, and refuse one on the first shot."""
    entry = transition_in(shot)
    if entry is None:
        return
    where = f"{prefix}.production.transition_in"
    if first:
        errors.append(
            f"{where} is declared on the first shot, which has no shot before it to be cut from "
            "— a transition describes a JOIN, and the first shot has none"
        )
        return
    errors.extend(transition_errors(entry, where))
