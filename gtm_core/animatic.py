"""The animatic — hear the pacing before paying to render it.

Stills the storyboard already produced, held for each shot's declared duration, with the read laid
over the top. **Zero render spend**: nothing here calls a provider, and the module makes no
network call of any kind (§R6). Its whole purpose is to move one class of judgement earlier — the
class you cannot make from a contact sheet.

What a contact sheet cannot tell you
-------------------------------------
A set of approved stills is silent on pacing: whether the opening line lands before a viewer's
attention drifts, whether the voiceover keeps up with the cuts, or whether one beat runs twice as
long on screen as it read on the page. Those are questions about TIME, and the only honest way to
answer them is to watch the thing at length.
Until now the first chance to do that was after the render budget was spent.

What it is not
---------------
Not a deliverable, and structurally prevented from becoming one: the output may only land under
``build/``, never ``deliver/``. It carries no identity marker and emits no disclosure — it cannot,
because nothing in it is synthesised beyond stills the operator has already approved at the same
gate. This module does not import :mod:`agent.publish` and a test asserts that it never does.

The verdict is a report, not a gate
------------------------------------
``verdict()`` answers three questions and refuses to adjudicate any of them. Whether a hook that
lands 0.4s late is a problem depends on the piece, and a build that failed on that would teach
people to stop building animatics — which costs the whole benefit to save nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .confine import ConfinementError, confined_dir, confined_output_path, confined_source_file
from .paths import resolve_content_root

__all__ = ["AnimaticError", "Segment", "plan", "build", "verdict", "main"]

#: The shortest gap a line may leave between its last word and the CUT that follows. Under this
#: the word is landing on the cut, which reads as clipped even when the audio technically fits.
#:
#: Deliberately measured against the cut, not against the clip file. `NarrationLineMeasurement`
#: also exposes a `tail_s` — silence INSIDE the audio file — and the two answer different
#: questions. A line with a second of room in its own file still sounds clipped if the picture
#: cuts a frame after the last word, and that is the failure an animatic exists to expose.
MIN_TAIL_S = 0.15

#: How far the sum of shot durations may drift from a declared `total_duration_s` before it is
#: worth reporting. Reported, never corrected — a drift means two files disagree, and silently
#: preferring one is how the disagreement survives.
TOTAL_DRIFT_TOLERANCE_S = 0.05


class AnimaticError(ValueError):
    """The storyboard and the shot list do not describe the same film."""


@dataclass(frozen=True)
class Segment:
    """One shot's still, and how long it holds."""

    n: int
    still: Path
    len_s: float
    ratio: str


def plan(storyboard: dict, shots: dict) -> list[Segment]:
    """Join storyboard entries to shots by ``n``, or refuse and say which side is short.

    Every duration comes from the SHOT LIST and is never adjusted. That is the point of the
    exercise: an animatic whose timings were massaged to look right tells you nothing about the
    film that will actually be built.
    """
    entries = storyboard.get("entries") if isinstance(storyboard, dict) else None
    shot_list = shots.get("shots") if isinstance(shots, dict) else None
    if not isinstance(entries, list) or not entries:
        raise AnimaticError("storyboard has no `entries` array to build from")
    if not isinstance(shot_list, list) or not shot_list:
        raise AnimaticError("shot list has no `shots` array to build from")

    by_n: dict[int, dict] = {}
    for i, shot in enumerate(shot_list, 1):
        if isinstance(shot, dict):
            by_n[int(shot.get("n", i))] = shot

    segments: list[Segment] = []
    seen: set[int] = set()
    ratios: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        n = entry.get("shot") if entry.get("shot") is not None else entry.get("n")
        if n is None:
            raise AnimaticError(
                f"storyboard entry {entry.get('image_path', '?')!r} names no shot, so there is "
                "nothing to hold it for. Every entry the animatic uses needs a `shot` number."
            )
        n = int(n)
        shot = by_n.get(n)
        if shot is None:
            raise AnimaticError(
                f"storyboard entry for shot {n} has no matching shot in the shot list "
                f"(shots present: {sorted(by_n)}). The two files describe different films."
            )
        image = str(entry.get("image_path") or "")
        if not image:
            raise AnimaticError(f"storyboard entry for shot {n} records no `image_path`")
        duration = shot.get("duration_s")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise AnimaticError(f"shot {n} has no usable `duration_s` ({duration!r})")
        ratios.add(str(entry.get("aspect_ratio") or shots.get("aspect_ratio") or "9:16"))
        seen.add(n)
        segments.append(Segment(n=n, still=Path(image), len_s=float(duration), ratio=""))

    missing = sorted(set(by_n) - seen)
    if missing:
        raise AnimaticError(
            f"shot(s) {missing} have no storyboard still, so the animatic would silently be "
            "shorter than the film. Generate the missing still(s), or say which shots this "
            "animatic deliberately covers."
        )
    if len(ratios) > 1:
        raise AnimaticError(
            f"the storyboard mixes aspect ratios {sorted(ratios)}. Refused BEFORE any encode: a "
            "stitch would pad the odd ones out and the animatic would misrepresent the framing "
            "of the very shots it exists to let you judge."
        )
    ratio = ratios.pop() if ratios else "9:16"
    return [Segment(n=s.n, still=s.still, len_s=s.len_s, ratio=ratio) for s in segments]


def _confined_build_path(out_path: Path, run_dir: Path, *, content_root: Path) -> Path:
    """``out_path`` must sit under ``run_dir/build/``, and all of it inside the content root.

    A preview is not a deliverable — hence ``build/`` — and a tenant's preview is still that
    tenant's file, hence the root. The first version confined only to ``run_dir``, which the
    caller supplies, so ``--run-dir /tmp/x`` wrote outside every content root. Found in review.
    """
    build_dir = (run_dir / "build").resolve()
    resolved = out_path.resolve() if out_path.is_absolute() else (run_dir / out_path).resolve()
    try:
        resolved.relative_to(build_dir)
    except ValueError as exc:
        raise AnimaticError(
            f"the animatic may only be written under {build_dir}, not {resolved}. It is a "
            "preview, not a deliverable: anything in deliver/ is a candidate for shipping, and "
            "an animatic that reached an audience would be six stills where a film was promised."
        ) from exc
    return confined_output_path(resolved, content_root=content_root)


def verdict(
    segments: list[Segment],
    *,
    measurements: list | None = None,
    brief: dict | None = None,
    declared_total_s: float | None = None,
    mux_result: object | None = None,
) -> dict:
    """Three questions about TIME, answered and never adjudicated.

    Each answer distinguishes ``False`` from *not measurable*: a read that does not exist and a
    read that does not fit are different states, and collapsing them is how "absent" gets read as
    "fine".
    """
    total = sum(s.len_s for s in segments)
    out: dict = {
        "shots": len(segments),
        "total_s": round(total, 3),
    }

    # 1. Does the hook land inside the first shot?
    if measurements:
        onset = float(measurements[0].voice_onset_s)
        out["hook_in_first_beat"] = onset < segments[0].len_s
        out["hook_onset_s"] = round(onset, 3)
    else:
        out["hook_in_first_beat"] = None
        out["read"] = "absent"

    # 2. Does every line finish before the CUT that follows it?
    #
    # Cut boundaries are the running sum of shot durations. For each line, the relevant boundary
    # is the first one at or after the line's last word; a line whose last word sits inside the
    # final shot is measured against the end of the film. Attribute access throughout, never a
    # getattr default — a default of "fine" on a renamed field reports every line as fitting,
    # which is the one answer this check exists to disprove.
    if measurements:
        boundaries: list[float] = []
        running = 0.0
        for seg in segments:
            running += seg.len_s
            boundaries.append(running)

        tight = []
        for i, m in enumerate(measurements, 1):
            ends_at = float(m.voice_onset_s) + float(m.declared_len_s)
            cut = next((b for b in boundaries if b >= ends_at), boundaries[-1])
            gap = cut - ends_at
            if gap < MIN_TAIL_S:
                tight.append(
                    {
                        "line": i,
                        "ends_at_s": round(ends_at, 3),
                        "cut_at_s": round(cut, 3),
                        "gap_s": round(gap, 3),
                    }
                )
        out["read_fits"] = not tight
        out["tight_lines"] = tight
        out["read"] = "present"

    # 3. Is the cover the first frame?
    cover = ((brief or {}).get("decisions", {}).get("cover", {}) or {}).get("value") or {}
    cover_still = (
        str(cover.get("still") or cover.get("image_path") or "") if isinstance(cover, dict) else ""
    )
    if cover_still and segments:
        out["cover_is_frame_one"] = Path(cover_still).name == segments[0].still.name
    else:
        out["cover_is_frame_one"] = None
        out["cover"] = "absent"

    if declared_total_s is not None:
        drift = abs(declared_total_s - total)
        out["declared_total_vs_sum"] = {
            "declared_s": declared_total_s,
            "sum_s": round(total, 3),
            "drift_s": round(drift, 3),
            "agrees": drift <= TOTAL_DRIFT_TOLERANCE_S,
        }

    if mux_result is not None:
        # A non-1.0 atempo means the narration master and the stitched picture disagreed by more
        # than rounding. Reported rather than hidden: on an animatic the durations are ours, so a
        # stretch means one of our own numbers is wrong.
        out["mux"] = {
            "strategy": getattr(mux_result, "strategy", None),
            "atempo": getattr(mux_result, "atempo", None),
        }
    return out


def build(
    *,
    storyboard: dict,
    shots: dict,
    run_dir: Path,
    stills_root: Path | None = None,
    brief: dict | None = None,
    out_path: Path | None = None,
    content_root: Path | None = None,
) -> dict:
    """Build the animatic and return its verdict. Never calls a provider.

    Order matters and each step's failure mode drove it. Segments first, so a mixed-ratio
    storyboard is refused before any encode. Captions burned PER SEGMENT before the stitch (the
    silent path only), because :func:`burn_captions` composites rendered screens onto a file and
    cannot draw onto a concat. Stitch with ``crossfade_s=0`` and ``normalize=False``, because the
    segments were produced at one size on purpose and a normalize pass would hide a bug rather
    than fix one. The narration is muxed LAST, so a read that does not fit fails after the picture
    exists and can be looked at.
    """
    from .video_finish import mux, still_to_segment, stitch
    from .video_finish.narration import (
        NarrationError,
        build_narration_track,
        lines_from_shotlist,
    )
    from .video_finish.shots import ShotSegment

    segments = plan(storyboard, shots)

    # Everything the animatic reads or writes stays inside the tenant's content root — the same
    # predicate C1's reference images and C11's pose files pass through. `run_dir` is confined as
    # a DIRECTORY (many files land in it); each still as a source file before a byte is encoded.
    root_ct = (content_root if content_root is not None else resolve_content_root()).resolve()
    run_dir = confined_dir(run_dir, content_root=root_ct)
    build_dir = run_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    root = stills_root or run_dir
    target = _confined_build_path(
        out_path or (build_dir / "animatic.mp4"), run_dir, content_root=root_ct
    )

    seg_files: list[ShotSegment] = []
    for seg in segments:
        still = seg.still if seg.still.is_absolute() else (root / seg.still)
        still = confined_source_file(still, content_root=root_ct)
        dst = build_dir / f".animatic-shot-{seg.n:02d}.mp4"
        still_to_segment(still, len_s=seg.len_s, ratio=seg.ratio, out_path=dst)
        seg_files.append(ShotSegment(path=str(dst)))

    joined = build_dir / ".animatic-video.mp4"
    stitch(
        seg_files,
        ratio=segments[0].ratio,
        out_path=joined,
        workdir=build_dir / ".animatic-work",
        crossfade_s=0.0,
        normalize=False,
    )

    # No read at all is the NORMAL state at the storyboard gate — the stills exist and the VO does
    # not yet — so `lines_from_shotlist`'s refusal is caught and read as "silent", not as an error.
    # A read that IS declared but whose files are missing takes the same path deliberately: a
    # silent animatic still answers the pacing question, and failing the build would withhold the
    # picture over the half of it that is not ready.
    try:
        lines = lines_from_shotlist(shots)
    except NarrationError:
        lines = []

    measurements = None
    if lines and all((root / line.file).is_file() for line in lines if getattr(line, "file", "")):
        narration = build_narration_track(
            lines,
            base_dir=root,
            out_path=build_dir / ".animatic-vo.m4a",
            total_duration_s=sum(s.len_s for s in segments),
        )
        # `.lines`, read directly rather than through a getattr default. The first draft
        # used `getattr(narration, "measurements", None)`, which is not a field this result
        # has — so a read that was placed correctly was reported as ABSENT, and the two
        # checks that depend on it silently answered None. A wrong attribute name should
        # raise, not degrade into the one verdict nobody looks twice at.
        measurements = list(narration.lines)
        result = mux(
            joined,
            build_dir / ".animatic-vo.m4a",
            out_path=target,
            workdir=build_dir / ".animatic-work",
        )
    else:
        joined.replace(target)
        result = None

    report = verdict(
        segments,
        measurements=measurements,
        brief=brief,
        declared_total_s=(
            float(shots["total_duration_s"])
            if isinstance(shots.get("total_duration_s"), (int, float))
            else None
        ),
        mux_result=result,
    )
    report["path"] = str(target)
    (build_dir / "animatic.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    """`python -m gtm_core.animatic build --storyboard F --shots F --run-dir D [--brief F]`."""
    import argparse

    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.animatic")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="build the animatic and print its verdict")
    p.add_argument("--storyboard", required=True, type=Path)
    p.add_argument("--shots", required=True, type=Path)
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--stills-root", type=Path, default=None)
    p.add_argument("--brief", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true", help="print the plan; encode nothing")
    args = parser.parse_args(argv)

    try:
        storyboard = json.loads(args.storyboard.read_text(encoding="utf-8"))
        shots = json.loads(args.shots.read_text(encoding="utf-8"))
        brief = json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[animatic] {exc}")
        return 1

    try:
        if args.dry_run:
            segments = plan(storyboard, shots)
            print(
                json.dumps(
                    {
                        "shots": [
                            {"n": s.n, "still": str(s.still), "len_s": s.len_s, "ratio": s.ratio}
                            for s in segments
                        ],
                        "total_s": round(sum(s.len_s for s in segments), 3),
                    },
                    indent=2,
                )
            )
            return 0
        print(
            json.dumps(
                build(
                    storyboard=storyboard,
                    shots=shots,
                    run_dir=args.run_dir,
                    stills_root=args.stills_root,
                    brief=brief,
                    out_path=args.out,
                ),
                indent=2,
            )
        )
    except (AnimaticError, ConfinementError) as exc:
        print(f"[animatic] {exc}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"[animatic] {exc}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
