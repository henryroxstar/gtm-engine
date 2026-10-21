from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .. import frame_sequence

try:
    from ..video_lint import SAFE_AREAS
except ImportError:
    from . import SAFE_AREAS
from . import build_record, fit
from .base import SceneError
from .fit import fit_tolerance
from .registry import _HTML_SCENES, _SCENE_EXTRAS, _SCENES
from .scenes.checkpoint_timing import _CHECKPOINT_DEFAULT_TIMING_SOURCE, SCENE_CUES


def audit_fit(
    *, kit: dict, ratio: str, repo_root: Path | None = None, scenes: Sequence[str] | None = None
) -> list[dict]:
    """Every fitted string in every scene, with the widest face its box survives.

    Answers the question `_fit_or_refuse` cannot: not "does this fit" but "how close is it". A
    refusal is loud; a string one step above its floor is silent, and it stays silent right up
    until a font is substituted, a word is added, or a tenant's kit points somewhere else. That
    is what happened here — a card holding at x1.03 read as fine for months.

    Tenant-bound by nature (it needs the real brand face), so it is a CLI, not a test. The test
    beside it pins this function's behaviour, not any particular tenant's numbers.

    ``fit._FIT_LOG`` is reached through the module object on purpose: it is REBOUND here
    (list → None), and an imported name would rebind only this module's copy while the
    fit helpers kept appending to theirs. One home, one binding (PRD 2026-09-01 §5).
    """
    names = sorted(scenes) if scenes else [n for n in sorted(_SCENES) if n != "still-push"]
    collected: list[dict] = []
    for name in names:
        fit._FIT_LOG = []
        try:
            _SCENES[name](
                kit=kit,
                ratio=ratio,
                fps=2,
                duration_s=0.5,
                out_dir=Path(tempfile.mkdtemp(prefix="fit-audit-")),
                repo_root=repo_root,
            )
        except SceneError:
            # A refusal is already the loudest possible signal; whatever it logged before raising
            # is still worth reporting, so this records rather than aborting the sweep.
            pass
        except Exception:  # noqa: BLE001 # nosec B110 — an unrelated scene fault must not hide the audit
            pass
        for row in fit._FIT_LOG:
            collected.append(
                {
                    "scene": name,
                    "ratio": ratio,
                    "tolerance": round(fit_tolerance(row["width_at_floor_px"], row["box_w"]), 4),
                    **row,
                }
            )
        fit._FIT_LOG = None
    seen: set[tuple] = set()
    unique = []
    for row in sorted(collected, key=lambda r: r["tolerance"]):
        key = (row["scene"], row["where"], row["text"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _parse_kv(items: list[str], *, flag: str, allow_empty: bool = False) -> dict[str, str]:
    """``KEY=VALUE`` pairs from a repeatable flag. Raises ``ValueError`` naming the bad item.

    Shared by ``--label``, ``--message`` and ``--title`` rather than written three times: they
    differ only in which scene reads the result, and a second copy is where they quietly stop
    validating the same way.

    ``allow_empty`` passes an empty VALUE through for a flag where emptiness is a request rather
    than a slip — ``--title line_two=`` asks for a name-only card. The scene, not this parser, is
    what knows whether a given key may be empty, so the rule lives there and this only decides
    whether the shape reaches it at all.
    """
    parsed: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or (not value.strip() and not allow_empty):
            raise ValueError(f"{flag} must be KEY=VALUE, got {item!r}")
        parsed[key.strip()] = value.strip()
    return parsed


def _simple_extras(args: argparse.Namespace, allowed: frozenset[str]) -> dict[str, object]:
    """The extras that are pure flag-reading, keyed by what the scene declared it accepts.

    Only the two that need a file read or a cross-flag rule (``timing``) stay in ``main``.
    Raises ``ValueError`` with the operator-facing message; the caller prints it as JSON.
    """
    extra: dict[str, object] = {}
    if "image" in allowed:
        extra["image"] = args.image
    if "crop_frac" in allowed:
        if args.crop_frac is not None:
            try:
                parts = tuple(float(p) for p in args.crop_frac.split(","))
            except ValueError as exc:
                raise ValueError("--crop-frac must be 4 numbers") from exc
            if len(parts) != 4:
                raise ValueError("--crop-frac must be 4 numbers")
            extra["crop_frac"] = parts
    if "logo" in allowed:
        extra["logo"] = args.logo
        if args.cta_text is not None:
            extra["cta_text"] = args.cta_text
        if args.logo_variant is not None:
            extra["logo_variant"] = args.logo_variant
    if "stills" in allowed:
        extra["stills"] = args.still
    if "speak_from_s" in allowed and args.speak_from_s is not None:
        extra["speak_from_s"] = args.speak_from_s
    if "actions" in allowed:
        extra["actions"] = args.actions
    if "title" in allowed and args.title:
        extra["title"] = _parse_kv(args.title, flag="--title", allow_empty=True)
    if "message" in allowed and args.message:
        extra["message"] = _parse_kv(args.message, flag="--message")
    if "bubble" in allowed and args.bubble:
        extra["bubble"] = _parse_kv(args.bubble, flag="--bubble")
    if "labels" in allowed and args.label:
        extra["labels"] = _parse_kv(args.label, flag="--label")
    return extra


def _build_parser(argv: list[str] | None = None) -> argparse.ArgumentParser:
    has_comp = "--composition" in (argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(prog="gtm_core.screen_ui")
    choices = [*sorted(_SCENES), *sorted(_HTML_SCENES), "audit-fit", "fit-layout"]
    if has_comp:
        parser.add_argument("scene", nargs="?", default=None, choices=choices, help="scene name")
    else:
        parser.add_argument("scene", choices=choices, help="scene name")
    parser.add_argument(
        "--backend", choices=("pillow", "html"), default=None, help="render backend"
    )

    parser.add_argument(
        "--composition", type=Path, default=None, help="one-off HTML composition path"
    )
    parser.add_argument("--preview-frame", action="store_true", help="render only preview frame")
    for k in ("headline", "subline", "eyebrow", "badge", "kicker"):
        parser.add_argument(f"--{k}", default=None, help=f"{k} text for HTML motion")
    parser.add_argument("--kit-json", type=Path, required=True, help="resolved brand kit JSON")
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration-s", type=float, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--font-role", default="caption")
    parser.add_argument("--image", type=Path, default=None, help="source image")
    parser.add_argument("--crop-frac", default=None, help="still-push crop: LEFT,TOP,RIGHT,BOTTOM")
    parser.add_argument("--words-json", type=Path, default=None, help="measured timings sidecar")
    parser.add_argument("--timing-json", type=Path, default=None, help="cue window map JSON")
    parser.add_argument("--label", action="append", default=None, metavar="KEY=VALUE")
    parser.add_argument("--logo", action="store_true", help="composite brand mark top-left")
    parser.add_argument("--still", action="append", default=None, type=Path, metavar="PNG")
    parser.add_argument("--actions", type=Path, default=None, help="timed actions JSON")
    parser.add_argument("--cta-text", default=None, help="title-close CTA copy")
    parser.add_argument("--logo-variant", default=None, help="logo variant name")
    parser.add_argument("--title", action="append", default=None, metavar="KEY=VALUE")
    parser.add_argument("--message", action="append", default=None, metavar="KEY=VALUE")
    parser.add_argument("--bubble", action="append", default=None, metavar="KEY=VALUE")
    parser.add_argument("--speak-from-s", type=float, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--min-tolerance", type=float, default=None)
    parser.add_argument("--for-scene", choices=("hero-reveal", "phone-walkthrough"), default=None)
    parser.add_argument("--caption-lines", type=int, choices=(1, 2), default=2)
    parser.add_argument("--margin-px", type=int, default=40)
    return parser


def render_scene(
    scene: str,
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    extras: dict[str, object],
    font_role: str = "caption",
    repo_root: Path | None = None,
    timing_source: str | None = None,
) -> dict:
    """Render one scene into ``out_dir`` and write its build record. No argv/stdout/exit side
    effects (``main`` prints this function's result) so the rebuild driver (`glittery-sleeping-yao`
    change #6) can call it directly per shot, instead of re-invoking ``main([...])``.

    Refuses (dir untouched) before drawing a frame when ``out_dir`` holds a record for a
    DIFFERENT scene. PNGs with no record (predates this record) only warn on stderr and render
    over. Any existing record is deleted before rendering, so a crash mid-render never leaves an
    old fingerprint describing half-replaced frames.
    """
    out_dir = Path(out_dir)
    scene_fn = _SCENES[scene]
    existing = build_record.read(out_dir)
    if existing is not None and existing.get("scene") != scene:
        raise SceneError(
            f"{out_dir} already holds a {build_record.RECORD_NAME} for scene "
            f"{existing.get('scene')!r} — render {scene!r} into a fresh --out-dir"
        )
    if existing is None and out_dir.is_dir() and any(out_dir.glob("*.png")):
        print(
            f"WARN: {out_dir} has PNGs but no {build_record.RECORD_NAME} — rendering untracked",
            file=sys.stderr,
        )
    build_record.delete(out_dir)
    count = scene_fn(
        kit=kit,
        ratio=ratio,
        fps=fps,
        duration_s=duration_s,
        out_dir=out_dir,
        font_role=font_role,
        repo_root=repo_root,
        **extras,
    )

    seqs = frame_sequence.sequences(out_dir)
    prefix = next((p for p, numbers in seqs.items() if len(numbers) == count), None)
    frame_pattern = (
        frame_sequence.pattern(prefix, frame_sequence.digit_width(out_dir, prefix))
        if prefix is not None
        else None
    )
    record = build_record.build_record(
        scene=scene,
        ratio=ratio,
        fps=fps,
        duration_s=duration_s,
        extras=extras,
        kit=kit,
        font_role=font_role,
        repo_root=repo_root,
        frames=count,
        frame_pattern=frame_pattern,
    )
    build_record.write(out_dir, record)

    return {
        "scene": scene,
        "frames": count,
        "out_dir": str(out_dir),
        "fps": fps,
        "duration_s": duration_s,
        # Records which timing source was used — "measured" and "assumed" must not look the same.
        **({"timing_source": timing_source} if timing_source is not None else {}),
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m gtm_core.screen_ui <scene> --kit-json <path> --ratio 4:5 --fps 24
    --duration-s 3 --out-dir <dir>`` — write the numbered PNG sequence for one scene.

    Skills are markdown executed by the brain; they cannot import Python, so every module here
    needs a CLI to be reachable from a skill body at all (same convention as
    ``render_engines.main``). ``--kit-json`` takes the brand kit as a JSON file rather than a
    ``--profile`` flag: this module has no business resolving profiles itself — the caller reads
    the kit via ``gtm_core.brandkit`` and hands the resolved dict over, the same separation
    ``captions.render`` already keeps from its own callers.
    """
    parser = _build_parser(argv)
    args = parser.parse_args(argv)

    kit = json.loads(args.kit_json.read_text(encoding="utf-8"))

    if args.scene == "audit-fit":
        rows = audit_fit(kit=kit, ratio=args.ratio, repo_root=args.repo_root)
        thin = [r for r in rows if args.min_tolerance and r["tolerance"] < args.min_tolerance]
        print(
            json.dumps(
                {
                    "ratio": args.ratio,
                    "strings": len(rows),
                    "below_min_tolerance": len(thin),
                    "rows": rows,
                },
                indent=2,
            )
        )
        return 1 if thin else 0

    if args.scene == "fit-layout":
        from . import fit_layout

        doc, code = fit_layout.run_cli(args, kit)
        print(json.dumps(doc, indent=2))
        return code

    if not args.scene and not args.composition:
        print(json.dumps({"error": "either scene or --composition is required"}))
        return 2
    if args.scene and args.composition:
        print(json.dumps({"error": "pass scene or --composition, not both"}))
        return 2

    missing = [
        f"--{n.replace('_', '-')}" for n in ("duration_s", "out_dir") if getattr(args, n) is None
    ]
    if missing:
        print(
            json.dumps(
                {
                    "scene": args.scene or str(args.composition),
                    "error": f"{' and '.join(missing)} required",
                }
            )
        )
        return 2

    is_html = (
        (args.backend == "html")
        or bool(args.composition)
        or (args.scene in _HTML_SCENES and args.scene not in _SCENES and args.backend != "pillow")
    )
    if is_html:
        from .html_backend import render_html_scene

        extra = {
            k: getattr(args, k)
            for k in ("headline", "subline", "eyebrow", "badge", "kicker", "image", "actions")
            if getattr(args, k) is not None
        }
        try:
            res = render_html_scene(
                args.scene,
                composition=args.composition,
                out_dir=args.out_dir,
                kit=kit,
                ratio=args.ratio,
                fps=args.fps,
                duration_s=args.duration_s,
                extras=extra,
                preview_frame=args.preview_frame,
            )
            print(json.dumps(res, indent=2, default=str))
            return 0
        except Exception as exc:
            print(json.dumps({"scene": args.scene or str(args.composition), "error": str(exc)}))
            return 2

    allowed = _SCENE_EXTRAS.get(args.scene, frozenset())
    try:
        extra: dict[str, object] = _simple_extras(args, allowed)
    except ValueError as exc:
        print(json.dumps({"scene": args.scene, "error": str(exc)}))
        return 2

    timing_source = _CHECKPOINT_DEFAULT_TIMING_SOURCE
    if "timing" in allowed:
        if args.words_json and args.timing_json:
            print(
                json.dumps(
                    {"scene": args.scene, "error": "pass --words-json or --timing-json, not both"}
                )
            )
            return 2
        if args.timing_json:
            try:
                raw = json.loads(args.timing_json.read_text(encoding="utf-8"))
                extra["timing"] = {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                print(json.dumps({"scene": args.scene, "error": f"bad --timing-json: {exc}"}))
                return 2
            timing_source = f"timing-json:{args.timing_json}"
        elif args.words_json:
            from ..vo_timings import VoTimingError, read_words_sidecar, timing_map

            try:
                words, audio_s = read_words_sidecar(args.words_json)
                # A correct matcher cannot notice that you re-cut the VO and passed the OLD
                # sidecar — every phrase still matches, and every window is silently stale. The
                # durations are what disagree, so that is what is checked.
                if abs(audio_s - args.duration_s) > 0.05:
                    print(
                        json.dumps(
                            {
                                "scene": args.scene,
                                "error": (
                                    f"{args.words_json} was measured against a {audio_s:.3f}s VO but this "
                                    f"scene is being rendered for {args.duration_s:.3f}s — that is a "
                                    "different cut. Re-ingest the timings for the VO you are rendering."
                                ),
                            }
                        )
                    )
                    return 2
                extra["timing"] = timing_map(words, SCENE_CUES[args.scene], total_s=audio_s)
            except VoTimingError as exc:
                print(json.dumps({"scene": args.scene, "error": str(exc)}))
                return 2
            timing_source = f"words-json:{args.words_json}"
    try:
        result = render_scene(
            args.scene,
            kit=kit,
            ratio=args.ratio,
            fps=args.fps,
            duration_s=args.duration_s,
            out_dir=args.out_dir,
            extras=extra,
            font_role=args.font_role,
            repo_root=args.repo_root,
            timing_source=(
                timing_source
                if args.scene in _SCENE_EXTRAS and "timing" in _SCENE_EXTRAS[args.scene]
                else None
            ),
        )
    except SceneError as exc:
        print(json.dumps({"scene": args.scene, "error": str(exc)}))
        return 2

    print(json.dumps(result, indent=2))
    return 0
