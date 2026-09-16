"""The `python -m gtm_core.video_finish` argument surface, whole and by itself.

Every subcommand's ``--help`` is a committed golden transcript
(``tests/goldens/cli/video_finish.*.help.txt``), so this file IS that contract: a
reworded help string or a moved flag is a golden diff, not a silent change. ``prog`` is
pinned so the transcript reads the same under ``python -m`` and under a console script.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..video_lint import SAFE_AREAS
from .constants import MUX_ATEMPO_MAX, MUX_ATEMPO_MAX_LIP_SYNCED
from .sfx import SFX_SHORTFALL_MAX_DB
from .sfx_detect import (
    SFX_TRANSIENT_HOP_S,
    SFX_TRANSIENT_SEARCH_S,
    SFX_TRANSIENT_WINDOW_S,
)


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Kept whole and separate: `--help` for every subcommand is a
    golden transcript (tests/goldens/cli/), so this is the one place its text lives."""
    parser = argparse.ArgumentParser(prog="python -m gtm_core.video_finish")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run")
    run_p.add_argument("--profile", required=True)
    run_p.add_argument("--product", default=None, help="active product slug (brand-kit lookup)")
    run_p.add_argument("--slug", required=True)
    run_p.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    run_p.add_argument("--spec", type=Path, required=True)
    run_p.add_argument("--source", type=Path, required=True)
    run_p.add_argument("--out-dir", type=Path, required=True)
    run_p.add_argument("--workdir", type=Path, default=None)
    run_p.add_argument("--repo-root", type=Path, default=None)
    run_p.add_argument(
        "--profiles-root",
        type=Path,
        default=None,
        help="override profiles root for brand-kit lookup",
    )
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--json", action="store_true", dest="as_json")

    trim_p = sub.add_parser("predictor-trim")
    trim_p.add_argument("--in", dest="src", type=Path, required=True)
    trim_p.add_argument("--out", dest="dst", type=Path, required=True)

    grade_p = sub.add_parser(
        "grade",
        help="apply ONE eq grade to one shot, audio stream-copied (per-shot assembly path)",
    )
    grade_p.add_argument("--source", required=True)
    grade_p.add_argument("--out", required=True)
    grade_p.add_argument(
        "--eq",
        action="append",
        required=True,
        metavar="KEY=VALUE",
        help="an ffmpeg eq term, repeatable: brightness / contrast / saturation / gamma_r / "
        "gamma_b. Grade the PICTURE only; the shot's audio is stream-copied.",
    )
    grade_p.add_argument("--crf", type=int, default=16)
    grade_p.add_argument(
        "--force",
        action="store_true",
        help="re-grade a file that already carries a .grade.json sidecar. Almost always wrong: "
        "grade from the ungraded source instead of stacking a second eq on a moved picture.",
    )

    frames_p = sub.add_parser(
        "frames-to-video",
        help="encode a numbered PNG frame sequence (gtm_core.screen_ui output) into a silent mp4",
    )
    frames_p.add_argument(
        "--frames-glob",
        required=True,
        help="ffmpeg-style pattern, e.g. 'out/shot2-booking-%%04d.png'",
    )
    frames_p.add_argument("--fps", type=int, required=True)
    frames_p.add_argument("--out", dest="out", type=Path, required=True)
    frames_p.add_argument("--content-root", type=Path, default=None)
    frames_p.add_argument("--json", action="store_true", dest="as_json")

    mux_p = sub.add_parser(
        "mux",
        help="attach one VO track to one silent rendered shot (multi-shot render, per shot)",
    )
    mux_p.add_argument("--video", type=Path, required=True)
    mux_p.add_argument("--audio", type=Path, required=True)
    mux_p.add_argument("--out", dest="out", type=Path, required=True)
    mux_p.add_argument("--workdir", type=Path, default=None)
    mux_p.add_argument(
        "--lip-synced",
        action="store_true",
        help="this shot's mouth was animated against this VO; clamps atempo to "
        f"{MUX_ATEMPO_MAX_LIP_SYNCED} because rescaling the audio desyncs the lips",
    )
    mux_p.add_argument(
        "--atempo-max",
        type=float,
        default=MUX_ATEMPO_MAX,
        help=f"max VO speed-up allowed to fit the shot (default {MUX_ATEMPO_MAX}); "
        "past this the mismatch is refused as a script-length problem",
    )
    mux_p.add_argument("--content-root", type=Path, default=None)
    mux_p.add_argument("--json", action="store_true", dest="as_json")

    split_p = sub.add_parser(
        "split",
        help="cut one continuous take into its per-beat files (the inverse of stitch)",
    )
    split_p.add_argument("--in", dest="src", type=Path, required=True)
    split_p.add_argument(
        "--cuts",
        type=Path,
        required=True,
        help='JSON: {"cuts":[{"shot_id":"shot-2","start_s":0.0,"end_s":4.2}, ...]} '
        "— out-points MEASURED off the delivered take, not the shot list's asked durations",
    )
    split_p.add_argument("--out-dir", dest="out_dir", type=Path, required=True)
    split_p.add_argument("--workdir", type=Path, default=None)
    split_p.add_argument("--content-root", type=Path, default=None)
    split_p.add_argument("--json", action="store_true", dest="as_json")

    stitch_p = sub.add_parser(
        "stitch", help="concatenate finished per-shot files into one asset (hard cut by default)"
    )
    stitch_p.add_argument(
        "--segments",
        type=Path,
        required=True,
        help='JSON: {"segments":[{"path":"...","reframed":false}, ...]} in shot order',
    )
    stitch_p.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    stitch_p.add_argument("--out", dest="out", type=Path, required=True)
    stitch_p.add_argument("--workdir", type=Path, default=None)
    stitch_p.add_argument(
        "--video-bitrate",
        default=None,
        help='e.g. "8M" — encode to a bitrate target instead of CRF; for a final deliverable that '
        "must clear video_lint's 1080p bitrate floor",
    )
    stitch_p.add_argument(
        "--normalize",
        action="store_true",
        help=(
            "re-encode every segment to uniform settings before joining; needed when segments "
            "come from different encoders (a provider render beside a local one)"
        ),
    )
    stitch_p.add_argument(
        "--crossfade-s",
        type=float,
        default=0.0,
        help="0 (default) = hard cut via concat demuxer + stream copy; >0 re-encodes with xfade",
    )
    stitch_p.add_argument(
        "--transitions-from-shots",
        dest="transitions_from_shots",
        type=Path,
        default=None,
        help="a shot list whose per-shot production.transition_in says how each shot is cut into "
        "(dissolve/fade/push, absent = hard cut); its shots must be the --segments, in order. Not "
        "combinable with --crossfade-s, which dissolves every join alike",
    )
    stitch_p.add_argument("--content-root", type=Path, default=None)
    stitch_p.add_argument("--json", action="store_true", dest="as_json")

    burn_p = sub.add_parser(
        "burn-captions",
        help="burn each shot's caption into that shot's own file, BEFORE the stitch",
    )
    burn_p.add_argument(
        "--shots",
        type=Path,
        required=True,
        help="the shot list JSON; each shot's 'file' is resolved relative to this file's folder",
    )
    burn_p.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    burn_p.add_argument("--profile", required=True, help="active profile (brand-kit lookup)")
    burn_p.add_argument("--product", default=None, help="active product slug (brand-kit lookup)")
    burn_p.add_argument(
        "--profiles-root",
        type=Path,
        default=None,
        help="override profiles root for brand-kit lookup",
    )
    burn_p.add_argument("--out-dir", dest="out_dir", type=Path, required=True)
    burn_p.add_argument("--workdir", type=Path, default=None)
    burn_p.add_argument(
        "--shot-ids",
        default=None,
        help="comma-separated shot ids to burn; omit for every shot that has a 'spoken' line",
    )
    burn_p.add_argument("--max-words", type=int, default=5)
    burn_p.add_argument(
        "--video-bitrate",
        default="16M",
        help="bitrate target for the re-encode (default 16M — a caption burn re-encodes the "
        "picture, so this is the one knob that keeps it off video_lint's 1080p floor)",
    )
    burn_p.add_argument("--repo-root", type=Path, default=None)
    burn_p.add_argument("--content-root", type=Path, default=None)
    burn_p.add_argument("--json", action="store_true", dest="as_json")

    ov_p = sub.add_parser(
        "overlays",
        help="draw each shot's production.overlay chat bubble beside the phone and composite "
        "it into that shot's own file, BEFORE the stitch",
    )
    ov_p.add_argument(
        "--shots",
        type=Path,
        required=True,
        help="the shot list JSON; each shot's production.overlay is its bubble spec",
    )
    ov_p.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    ov_p.add_argument("--profile", required=True, help="active profile (brand-kit lookup)")
    ov_p.add_argument("--product", default=None, help="active product slug (brand-kit lookup)")
    ov_p.add_argument(
        "--profiles-root",
        type=Path,
        default=None,
        help="override profiles root for brand-kit lookup",
    )
    ov_p.add_argument(
        "--shots-root",
        type=Path,
        default=None,
        help="root each shot's 'file' is relative to (default: the shot list's own folder)",
    )
    ov_p.add_argument("--out-dir", dest="out_dir", type=Path, required=True)
    ov_p.add_argument("--workdir", type=Path, default=None)
    ov_p.add_argument(
        "--shot-ids",
        default=None,
        help="comma-separated shot ids; omit for every shot carrying a production.overlay",
    )
    ov_p.add_argument(
        "--fps",
        type=int,
        default=30,
        help="frame rate the bubble sequence is drawn at (default 30); the overlay filter "
        "matches it to the shot by timestamp, so it need not equal the shot's own",
    )
    ov_p.add_argument("--repo-root", type=Path, default=None)
    ov_p.add_argument("--content-root", type=Path, default=None)
    ov_p.add_argument("--json", action="store_true", dest="as_json")

    ovs_p = sub.add_parser(
        "overlay-scene",
        help="composite an RGBA PNG sequence (a gtm_core.screen_ui overlay scene) over one "
        "shot at 1:1, keeping the shot's own audio",
    )
    ovs_p.add_argument("--in", dest="src", type=Path, required=True)
    ovs_p.add_argument(
        "--frames-glob",
        required=True,
        help="ffmpeg-style pattern, e.g. 'out/chat-bubble-%%04d.png' — written WITH alpha, or "
        "it composites as an opaque rectangle over the picture",
    )
    ovs_p.add_argument("--fps", type=int, required=True)
    ovs_p.add_argument("--out", dest="out", type=Path, required=True)
    ovs_p.add_argument("--content-root", type=Path, default=None)
    ovs_p.add_argument("--json", action="store_true", dest="as_json")

    trans_p = sub.add_parser(
        "find-transient",
        help="where a cue file's sound actually starts (relative level sweep, not silencedetect)",
    )
    trans_p.add_argument("--in", dest="src", type=Path, required=True)
    trans_p.add_argument("--search-s", type=float, default=SFX_TRANSIENT_SEARCH_S)
    trans_p.add_argument("--window-s", type=float, default=SFX_TRANSIENT_WINDOW_S)
    trans_p.add_argument("--hop-s", type=float, default=SFX_TRANSIENT_HOP_S)
    trans_p.add_argument("--json", action="store_true", dest="as_json")

    sfx_p = sub.add_parser(
        "mix-sfx",
        help="mix N timed SFX cues onto a shot's existing audio (normalize=0, verified by measurement)",
    )
    sfx_p.add_argument("--in", dest="src", type=Path, required=True)
    sfx_p.add_argument(
        "--cues",
        type=Path,
        required=True,
        help='JSON: {"cues":[{"path":"...","at_s":4.2,"gain_db":-8,"trim_s":0.12,"label":"tick"}]} '
        "— at_s is where the cue's TRANSIENT lands, trim_s is its offset inside the file",
    )
    sfx_p.add_argument("--out", dest="out", type=Path, required=True)
    sfx_p.add_argument("--workdir", type=Path, default=None)
    sfx_p.add_argument(
        "--no-verify",
        action="store_false",
        dest="verify",
        help="skip the post-mix measurement pass. The measurement is the point of this verb; "
        "this exists for a deliberate, recorded exception, not for a faster run",
    )
    sfx_p.add_argument("--shortfall-max-db", type=float, default=SFX_SHORTFALL_MAX_DB)
    sfx_p.add_argument("--keep-intermediates", action="store_true")
    sfx_p.add_argument("--content-root", type=Path, default=None)
    sfx_p.add_argument("--json", action="store_true", dest="as_json")

    tone_p = sub.add_parser(
        "room-tone", help="synthesize a low room-tone bed (.m4a) — the noise floor, never a score"
    )
    tone_p.add_argument("--out", dest="out", type=Path, required=True)
    tone_p.add_argument("--duration-s", type=float, required=True)
    tone_p.add_argument("--amplitude", type=float, default=0.014)
    tone_p.add_argument("--content-root", type=Path, default=None)
    tone_p.add_argument("--json", action="store_true", dest="as_json")

    polish_p = sub.add_parser(
        "voice-polish",
        help="clean up one narration take: highpass, de-mud, presence lift, de-ess, gentle "
        "compression — lossless WAV, duration-preserving",
    )
    polish_p.add_argument("--in", dest="src", type=Path, required=True)
    polish_p.add_argument("--out", dest="out", type=Path, required=True)
    polish_p.add_argument("--content-root", type=Path, default=None)
    polish_p.add_argument("--json", action="store_true", dest="as_json")

    narr_p = sub.add_parser(
        "narration-track",
        help=(
            "place a shot list's `narration` read onto one voice-only master (.m4a). Refuses if "
            "the lane fails its lint, or if a line's `len_s` disagrees with its audio."
        ),
    )
    narr_p.add_argument("--shots", type=Path, required=True, help="the <slug>.shots.json")
    narr_p.add_argument("--out", dest="out", type=Path, required=True)
    narr_p.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help=(
            "root the lines' `file` paths are relative to (default: the working directory). Every "
            "clip is confined to it, as a shot's `file` is repo-relative."
        ),
    )
    narr_p.add_argument("--content-root", type=Path, default=None)
    narr_p.add_argument("--json", action="store_true", dest="as_json")

    sheet_p = sub.add_parser(
        "contact-sheet", help="evenly spaced frames as one image, for looking at a finished asset"
    )
    sheet_p.add_argument("--in", dest="src", type=Path, required=True)
    sheet_p.add_argument("--out", dest="dst", type=Path, required=True)
    sheet_p.add_argument("--count", type=int, default=8)
    sheet_p.add_argument("--tile-w", type=int, default=320)

    reframe_p = sub.add_parser(
        "reframe",
        help="crop OS chrome off a raw screen recording, then pad it to an exact platform ratio",
    )
    reframe_p.add_argument("--in", dest="src", type=Path, required=True)
    reframe_p.add_argument("--out", dest="out", type=Path, required=True)
    reframe_p.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    reframe_p.add_argument(
        "--crop-top",
        type=int,
        default=0,
        metavar="PX",
        help="pixels to remove off the top before padding — a status bar's clock/battery/carrier "
        "glyphs, never a face or a designed frame (see pad_to_ratio's docstring: crop chrome, "
        "pad content). 0 (default) is a plain reframe with no chrome removed.",
    )
    reframe_p.add_argument("--background", default="black")
    reframe_p.add_argument("--video-bitrate", default=None)
    reframe_p.add_argument("--workdir", type=Path, default=None)
    reframe_p.add_argument("--content-root", type=Path, default=None)

    return parser
