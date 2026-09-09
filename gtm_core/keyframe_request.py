"""Build the provider request for one shot — deterministically, so the brain never types it.

The single-frame request this produces is byte-identical to the one `video-render`'s body already
mandates. That is the point: the keyframe path is not a second request shape, it is the same shape
with one more entry in ``medias``, so a shot without an ``end_frame`` cannot be changed by this
module existing. The contract test asserts that equality against a literal rather than trusting it.

Why a module rather than a paragraph in a skill body
----------------------------------------------------
Three fields have to agree for a keyframe render to be what was planned: the model comes from the
registry (not from the body's memory of which model is cheap this month), the two media roles have
to arrive in the right order, and the audio toggle has to be sent EXPLICITLY because the provider's
own default for it is ``true`` on the models that expose it. A body that says "send these three
things" is a body that will send two of them. Nothing here opens a socket — it returns a dict the
caller passes to the MCP tool it already holds (§R6).
"""

from __future__ import annotations

from .render_engines import EngineSpec, resolve_engine
from .shots_lint.keyframe import is_keyframe_shot

__all__ = ["KeyframeRequestError", "build_request", "main"]

#: The registry role a keyframe b-roll shot resolves. Named once here so the module and the skill
#: body cannot drift on which role pays for the capability check.
KEYFRAME_ROLE = "keyframe_broll"

_START_ROLE = "start_image"
_END_ROLE = "end_image"


class KeyframeRequestError(ValueError):
    """The shot and the engine disagree about what is being rendered."""


def build_request(
    shot: dict,
    *,
    engine: EngineSpec,
    start_media_id: str,
    end_media_id: str | None = None,
) -> dict:
    """The provider request for ``shot``, as a plain dict.

    ``start_media_id`` / ``end_media_id`` are provider media ids from an upload — never paths and
    never URLs. Path confinement happens where bytes are read, one layer up; by the time a request
    is built the frames are already the provider's.

    Raises :class:`KeyframeRequestError` when the shot asks for something the engine cannot do, or
    when the caller's media ids do not match what the shot declares. Both are refusals BEFORE
    spend, which is the only place they are cheap.
    """
    if not str(start_media_id or "").strip():
        raise KeyframeRequestError("start_media_id is required — every shot begins somewhere")

    declares_end = is_keyframe_shot(shot)
    has_end_media = bool(str(end_media_id or "").strip())

    if declares_end and not has_end_media:
        raise KeyframeRequestError(
            "the shot declares `end_frame` but no end_media_id was supplied. Building the "
            "single-frame request instead would spend real credits arriving somewhere nobody "
            "chose, and the clip would look plausible — upload the end still first."
        )
    if has_end_media and not declares_end:
        raise KeyframeRequestError(
            "an end_media_id was supplied for a shot that declares no `end_frame`. The shot list "
            "is what the plan gate approved; a frame that reached the request without reaching "
            "the shot list is an unreviewed edit."
        )
    if declares_end and not engine.accepts_end_frame:
        raise KeyframeRequestError(
            f"engine {engine.name!r} ({engine.model!r}) does not accept an end frame, and this "
            "shot declares one. Sending it anyway is the silent failure this check exists to "
            f"stop: the provider drops the unknown role and returns a plausible clip that "
            f"arrives nowhere in particular. Resolve the {KEYFRAME_ROLE!r} role instead."
        )

    medias = [{"role": _START_ROLE, "value": str(start_media_id)}]
    if declares_end:
        medias.append({"role": _END_ROLE, "value": str(end_media_id)})

    request: dict = {"model": engine.model, "medias": medias}

    # The toggle is sent whenever the engine HAS one, whatever its default — an omitted value
    # inherits the provider's default (`true` on every model that exposes it), which quietly
    # scores a lane that is scored in post. An engine with no toggle at all gets no key, which is
    # a different state from a toggle that happens to default off.
    if engine.audio_toggle:
        request[engine.audio_toggle] = bool(str(shot.get("sfx", "") or "").strip())

    return request


def main(argv: list[str] | None = None) -> int:
    """`python -m gtm_core.keyframe_request --shots F --shot N --start-media ID [--end-media ID]`.

    Prints the request as JSON for the skill body to pass through verbatim. Exit 2 when the shot
    and the engine disagree; exit 1 when the inputs are unreadable.
    """
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.keyframe_request")
    parser.add_argument("--shots", required=True, type=Path, help="the .shots.json file")
    parser.add_argument("--shot", required=True, type=int, help="1-based shot number")
    parser.add_argument(
        "--start-media", required=True, help="provider media id for the start frame"
    )
    parser.add_argument("--end-media", default="", help="provider media id for the end frame")
    parser.add_argument("--role", default="", help="registry role (default: derived from the shot)")
    parser.add_argument("--registry", type=Path, default=None)
    args = parser.parse_args(argv)

    # Shot numbers are 1-based. Python's negative indexing means `--shot 0` would otherwise pick
    # the LAST shot and exit 0 — and the body passes this request through verbatim, so that is
    # credits spent rendering the wrong shot with the wrong keyframe. Found in review.
    if args.shot < 1:
        print(f"[keyframe-request] --shot must be >= 1 (shots are 1-based), got {args.shot}")
        return 1
    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
        shots = doc["shots"]
        shot = shots[args.shot - 1]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        print(f"[keyframe-request] cannot read shot {args.shot} of {args.shots}: {exc}")
        return 1
    if not isinstance(shot, dict):
        print(f"[keyframe-request] shot {args.shot} of {args.shots} is not an object")
        return 1

    role = args.role or (KEYFRAME_ROLE if is_keyframe_shot(shot) else "broll")
    try:
        engine = resolve_engine(role, registry_path=args.registry)
        request = build_request(
            shot,
            engine=engine,
            start_media_id=args.start_media,
            end_media_id=args.end_media or None,
        )
    except Exception as exc:  # noqa: BLE001 — a refusal (ours or the registry's) is a state, not a crash
        print(f"[keyframe-request] {exc}")
        return 2

    print(json.dumps({"role": role, "engine": engine.name, "request": request}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
