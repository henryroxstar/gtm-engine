from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from .audio import room_tone
from .burn import burn_captions
from .cli_narration import _cmd_narration_track
from .cli_voice_polish import _cmd_voice_polish
from .confine import _confined_dir, _confined_output
from .errors import FfmpegUnavailable, PlanError, PolishError
from .execute import execute
from .mux import frames_to_video, mux, overlay_frames
from .overlays import apply_overlays
from .parser import build_parser
from .plan import plan
from .polish import grade_shot, predictor_trim
from .ratio import pad_to_ratio
from .sfx import mix_sfx_cues
from .sfx_cues import SfxCue
from .sfx_detect import find_transient
from .shots import ShotSegment, transitions_from_shots
from .split import TakeCut, split
from .stitch import stitch


def _cmd_contact_sheet(args) -> int:
    from ..cover_frame import contact_sheet

    print(
        json.dumps(
            contact_sheet(args.src, args.dst, count=args.count, tile_w=args.tile_w), indent=2
        )
    )
    return 0


def _cmd_run(args) -> int:
    spec = json.loads(args.spec.read_text())
    p = plan(
        profile=args.profile,
        slug=args.slug,
        ratio=args.ratio,
        source=str(args.source),
        spec=spec,
    )
    if args.dry_run:
        print(json.dumps(p.to_json(), indent=2))
        return 0
    workdir = args.workdir or (args.out_dir / "_work")
    repo_root = args.repo_root or Path.cwd()

    # Resolve the caption font (and any brand palette accent) through the same brand-kit
    # lookup every other skill uses — a caption_text spec has nowhere else to get a font
    # path from, and captions.load_face() never falls back to a bitmap font.
    from ..brandkit import load_brand_kit
    from ..paths import PathConfig

    profiles_root = args.profiles_root or PathConfig.from_env().profiles_root
    try:
        kit = load_brand_kit(profiles_root, args.profile, args.product)
    except ValueError as exc:  # unsafe segment, or tomllib.TOMLDecodeError (a ValueError subclass)
        print(f"video-finish: could not resolve brand kit: {exc}", file=sys.stderr)
        return 2

    try:
        result = execute(
            p, workdir=workdir, out_dir=args.out_dir, kit=kit, repo_root=repo_root, spec=spec
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    if args.as_json:
        print(json.dumps({"out_path": str(result.out_path), "skipped": result.skipped}, indent=2))
    else:
        print(f"video-finish: wrote {result.out_path} (skipped={result.skipped})")
    return 0


def _cmd_predictor_trim(args) -> int:
    try:
        predictor_trim(args.src, args.dst)
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    print(f"video-finish: wrote {args.dst}")
    return 0


def _cmd_frames_to_video(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        frames_to_video(args.frames_glob, fps=args.fps, out_path=out)
    except (FfmpegUnavailable, ValueError) as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 3
    if args.as_json:
        print(json.dumps({"out_path": str(out), "fps": args.fps}, indent=2))
    else:
        print(f"video-finish: wrote {out}")
    return 0


def _cmd_room_tone(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        written = room_tone(out_path=out, duration_s=args.duration_s, amplitude=args.amplitude)
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(json.dumps({"out_path": str(written), "duration_s": args.duration_s}, indent=2))
    else:
        print(f"video-finish: wrote {written}")
    return 0


def _cmd_mux(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    workdir = args.workdir or (out.parent / "_work")
    try:
        result = mux(
            args.video,
            args.audio,
            out_path=out,
            workdir=workdir,
            atempo_max=args.atempo_max,
            lip_synced=args.lip_synced,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except PlanError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(json.dumps(asdict(result) | {"out_path": str(result.out_path)}, indent=2))
    else:
        detail = f"atempo={result.atempo:.4f}" if result.atempo else result.strategy
        print(
            f"video-finish: wrote {result.out_path} "
            f"(video {result.video_duration_s:.2f}s, audio {result.audio_duration_s:.2f}s, "
            f"{detail})"
        )
    return 0


def _cmd_split(args) -> int:
    try:
        out_dir = _confined_dir(args.out_dir, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    payload = json.loads(args.cuts.read_text())
    raw_cuts = payload.get("cuts")
    if not raw_cuts:
        print("video-finish: --cuts JSON has no non-empty 'cuts' array", file=sys.stderr)
        return 2
    try:
        cuts = [
            TakeCut(
                shot_id=str(c["shot_id"]),
                start_s=float(c["start_s"]),
                end_s=float(c["end_s"]),
            )
            for c in raw_cuts
        ]
    except (KeyError, TypeError, ValueError) as exc:
        print(f"video-finish: malformed entry in --cuts JSON: {exc}", file=sys.stderr)
        return 2
    workdir = args.workdir or (out_dir / "_work")
    try:
        split_result = split(args.src, cuts, out_dir=out_dir, workdir=workdir)
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(
            json.dumps(
                {
                    "out_paths": [str(p) for p in split_result.out_paths],
                    "source_duration_s": split_result.source_duration_s,
                    "cuts": [
                        {
                            "shot_id": c.shot_id,
                            "start_s": c.start_s,
                            "end_s": c.end_s,
                            "duration_s": round(c.duration_s, 6),
                        }
                        for c in split_result.cuts
                    ],
                },
                indent=2,
            )
        )
    else:
        print(
            f"video-finish: wrote {len(split_result.out_paths)} cuts from a "
            f"{split_result.source_duration_s:.2f}s take into {out_dir}"
        )
    return 0


def _cmd_find_transient(args) -> int:
    try:
        t = find_transient(
            args.src, search_s=args.search_s, window_s=args.window_s, hop_s=args.hop_s
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(json.dumps(asdict(t), indent=2))
    else:
        print(
            f"video-finish: {args.src.name} onset {t.onset_s:.3f}s "
            f"(peak {t.peak_dbfs:.1f} dBFS at {t.peak_s:.3f}s) — {t.rule}"
        )
    return 0


def _cmd_mix_sfx(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(args.cues.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"video-finish: could not read --cues: {exc}", file=sys.stderr)
        return 2
    raw_cues = payload.get("cues") if isinstance(payload, dict) else None
    if not raw_cues:
        print("video-finish: --cues JSON has no non-empty 'cues' array", file=sys.stderr)
        return 2
    try:
        cues = [
            SfxCue(
                path=str(c["path"]),
                at_s=float(c["at_s"]),
                gain_db=float(c.get("gain_db", 0.0)),
                trim_s=float(c.get("trim_s", 0.0)),
                label=str(c.get("label", "")),
            )
            for c in raw_cues
        ]
    except (KeyError, TypeError, ValueError) as exc:
        print(f"video-finish: malformed cue in --cues: {exc}", file=sys.stderr)
        return 2
    workdir = args.workdir or (out.parent / "_work")
    try:
        mix_result = mix_sfx_cues(
            args.src,
            cues,
            out_path=out,
            workdir=workdir,
            verify=args.verify,
            shortfall_max_db=args.shortfall_max_db,
            keep_intermediates=args.keep_intermediates,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(
            json.dumps(
                {
                    "out_path": str(mix_result.out_path),
                    "base_duration_s": mix_result.base_duration_s,
                    "verified": mix_result.verified,
                    "filtergraph": mix_result.filtergraph,
                    "cues": [asdict(m) for m in mix_result.cues],
                },
                indent=2,
            )
        )
    else:
        worst = max((m.shortfall_db for m in mix_result.cues), default=0.0)
        note = (
            f"worst shortfall {worst:.1f} dB" if mix_result.cues else "NOT VERIFIED (--no-verify)"
        )
        print(f"video-finish: wrote {mix_result.out_path} ({len(cues)} cues, {note})")
    return 0


def _cmd_burn_captions(args) -> int:
    try:
        out_dir = _confined_dir(args.out_dir, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"video-finish: could not read --shots: {exc}", file=sys.stderr)
        return 2
    raw_shots = doc.get("shots") if isinstance(doc, dict) else None
    if not raw_shots:
        print("video-finish: --shots JSON has no non-empty 'shots' array", file=sys.stderr)
        return 2

    from ..brandkit import load_brand_kit
    from ..paths import resolve_profiles_root

    profiles_root = args.profiles_root or resolve_profiles_root()
    try:
        kit = load_brand_kit(profiles_root, args.profile, args.product)
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim, never swallowed
        print(f"video-finish: could not resolve brand kit: {exc}", file=sys.stderr)
        return 2

    workdir = args.workdir or (out_dir / "_work")
    shot_ids = [s.strip() for s in args.shot_ids.split(",") if s.strip()] if args.shot_ids else None
    try:
        burn_result = burn_captions(
            raw_shots,
            ratio=args.ratio,
            kit=kit,
            out_dir=out_dir,
            workdir=workdir,
            shots_root=args.shots.resolve().parent,
            repo_root=args.repo_root,
            max_words=args.max_words,
            video_bitrate=args.video_bitrate,
            shot_ids=shot_ids,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(
            json.dumps(
                {
                    "ratio": burn_result.ratio,
                    "burned": [asdict(b) for b in burn_result.burned],
                    "skipped": [{"shot_id": sid, "why": why} for sid, why in burn_result.skipped],
                },
                indent=2,
            )
        )
    else:
        print(
            f"video-finish: burned captions into {len(burn_result.burned)} shots "
            f"-> {out_dir} ({len(burn_result.skipped)} skipped)"
        )
        for sid, why in burn_result.skipped:
            print(f"  skipped {sid}: {why}")
    return 0


def _cmd_overlays(args) -> int:
    try:
        out_dir = _confined_dir(args.out_dir, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"video-finish: could not read --shots: {exc}", file=sys.stderr)
        return 2
    raw_shots = doc.get("shots") if isinstance(doc, dict) else None
    if not raw_shots:
        print("video-finish: --shots JSON has no non-empty 'shots' array", file=sys.stderr)
        return 2

    from ..brandkit import load_brand_kit
    from ..paths import resolve_profiles_root

    profiles_root = args.profiles_root or resolve_profiles_root()
    try:
        kit = load_brand_kit(profiles_root, args.profile, args.product)
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim, never swallowed
        print(f"video-finish: could not resolve brand kit: {exc}", file=sys.stderr)
        return 2

    workdir = args.workdir or (out_dir / "_work")
    shot_ids = [s.strip() for s in args.shot_ids.split(",") if s.strip()] if args.shot_ids else None
    try:
        result = apply_overlays(
            raw_shots,
            ratio=args.ratio,
            kit=kit,
            fps=args.fps,
            shots_root=args.shots_root or args.shots.resolve().parent,
            out_dir=out_dir,
            workdir=workdir,
            repo_root=args.repo_root,
            shot_ids=shot_ids,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(
            json.dumps(
                {
                    "ratio": result.ratio,
                    "overlaid": [asdict(o) for o in result.overlaid],
                    "skipped": [{"shot_id": sid, "why": why} for sid, why in result.skipped],
                },
                indent=2,
            )
        )
    else:
        print(
            f"video-finish: composited overlays onto {len(result.overlaid)} shots "
            f"-> {out_dir} ({len(result.skipped)} skipped)"
        )
        for sid, why in result.skipped:
            print(f"  skipped {sid}: {why}")
    return 0


def _cmd_overlay_scene(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    try:
        overlay_frames(args.src, args.frames_glob, fps=args.fps, out_path=out)
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    if args.as_json:
        print(json.dumps({"out_path": str(out), "fps": args.fps}, indent=2))
    else:
        print(f"video-finish: wrote {out}")
    return 0


def _cmd_stitch(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    payload = json.loads(args.segments.read_text())
    raw_segments = payload.get("segments")
    if not raw_segments:
        print(
            "video-finish: --segments JSON has no non-empty 'segments' array",
            file=sys.stderr,
        )
        return 2
    segments = [
        ShotSegment(path=str(s["path"]), reframed=bool(s.get("reframed", False)))
        for s in raw_segments
    ]
    transitions = None
    if args.transitions_from_shots is not None:
        try:
            doc = json.loads(args.transitions_from_shots.read_text())
            transitions = transitions_from_shots(doc)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 2
    workdir = args.workdir or (out.parent / "_work")
    try:
        out_path = stitch(
            segments,
            ratio=args.ratio,
            out_path=out,
            workdir=workdir,
            crossfade_s=args.crossfade_s,
            transitions=transitions,
            normalize=args.normalize,
            video_bitrate=args.video_bitrate,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 4
    re_encoded = args.crossfade_s > 0 or any(d for _, d in transitions or [])
    method = "xfade re-encode" if re_encoded else "concat demuxer (stream copy)"
    if args.as_json:
        print(
            json.dumps(
                {
                    "out_path": str(out_path),
                    "segments": len(segments),
                    "method": method,
                },
                indent=2,
            )
        )
    else:
        print(f"video-finish: wrote {out_path} ({len(segments)} segments, {method})")
    return 0


def _cmd_grade(args) -> int:
    """Apply one eq grade to one shot. `--eq brightness=0.02 --eq contrast=1.04`."""
    eq: dict[str, float] = {}
    for term in args.eq:
        key, _, value = term.partition("=")
        if not key or not value:
            print(f"video-finish: --eq takes key=value, got {term!r}")
            return 2
        try:
            eq[key] = float(value)
        except ValueError:
            print(f"video-finish: --eq value must be a number, got {value!r}")
            return 2
    try:
        out = grade_shot(Path(args.source), Path(args.out), eq=eq, crf=args.crf, force=args.force)
    except (PolishError, FfmpegUnavailable) as exc:
        print(f"video-finish: {exc}")
        return 2
    print(json.dumps({"graded": str(out), "eq": eq}, indent=2))
    return 0


def _cmd_reframe(args) -> int:
    try:
        out = _confined_output(args.out, content_root=args.content_root)
    except PolishError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    workdir = args.workdir or (out.parent / "_work")
    try:
        result = pad_to_ratio(
            args.src,
            ratio=args.ratio,
            out_path=out,
            workdir=workdir,
            background=args.background,
            video_bitrate=args.video_bitrate,
            crop_top_px=args.crop_top,
        )
    except FfmpegUnavailable as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"video-finish: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"out_path": str(result), "ratio": args.ratio}, indent=2))
    return 0


_HANDLERS = {
    "contact-sheet": _cmd_contact_sheet,
    "reframe": _cmd_reframe,
    "run": _cmd_run,
    "predictor-trim": _cmd_predictor_trim,
    "frames-to-video": _cmd_frames_to_video,
    "room-tone": _cmd_room_tone,
    "voice-polish": _cmd_voice_polish,
    "mux": _cmd_mux,
    "split": _cmd_split,
    "find-transient": _cmd_find_transient,
    "mix-sfx": _cmd_mix_sfx,
    "grade": _cmd_grade,
    "burn-captions": _cmd_burn_captions,
    "overlays": _cmd_overlays,
    "overlay-scene": _cmd_overlay_scene,
    "stitch": _cmd_stitch,
    "narration-track": _cmd_narration_track,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _HANDLERS.get(args.cmd)
    return handler(args) if handler is not None else 2
