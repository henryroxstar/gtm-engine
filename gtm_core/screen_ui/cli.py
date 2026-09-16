from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .. import frame_sequence
from ..video_lint import SAFE_AREAS
from . import build_record, fit
from .base import SceneError
from .fit import fit_tolerance
from .registry import _SCENE_EXTRAS, _SCENES
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


def _build_parser() -> argparse.ArgumentParser:
    """Every flag this CLI accepts.

    Split out of ``main`` because an argparse setup is a wall of statements that says
    nothing about the dispatch logic underneath it — and with both in one function the
    complexity ceiling fires on the flag list rather than on any real branching.
    """
    parser = argparse.ArgumentParser(prog="gtm_core.screen_ui")
    parser.add_argument("scene", choices=[*sorted(_SCENES), "audit-fit", "fit-layout"])
    parser.add_argument("--kit-json", type=Path, required=True, help="resolved brand kit JSON")
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--fps", type=int, default=24)
    # Not `required=True`: `audit-fit` renders throwaway frames into a temp dir to measure them,
    # so demanding a duration and an out-dir would be asking for two answers it does not use.
    # Validated below for the scenes that do need them.
    parser.add_argument("--duration-s", type=float, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--font-role", default="caption")
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="source image for still-push (the asset being reused); phone-walkthrough's first screen",
    )
    parser.add_argument(
        "--crop-frac",
        default=None,
        help=(
            "still-push only: LEFT,TOP,RIGHT,BOTTOM as fractions of the source, applied BEFORE "
            "scaling. Crop a full application window to the one region carrying the fact the "
            "shot is for; a whole window cover-fitted to 1920 is unreadable at any size."
        ),
    )
    parser.add_argument(
        "--words-json",
        type=Path,
        default=None,
        help=(
            "a narration-cued scene only: the `<id>.words.json` sidecar written by "
            "`gtm_core.vo_timings ingest`. The scene's own cue spec does the phrase matching; "
            "this supplies only the measured timings."
        ),
    )
    parser.add_argument(
        "--timing-json",
        type=Path,
        default=None,
        help=(
            'a literal window map, e.g. {"DROP":[0.145,0.215],...} — the escape hatch for a '
            "hand-timed card, and what tests use. Mutually exclusive with --words-json."
        ),
    )
    parser.add_argument(
        "--label",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "checkpoint-flow only, repeatable: the tenant's own name for a drawn product layer "
            "(gateway, gateway_sub, stream). Unset keys keep the scene's generic defaults — a "
            "product name is data the profile supplies, never a literal in engine code."
        ),
    )
    parser.add_argument(
        "--logo",
        action="store_true",
        help=(
            "title-claim/title-close only: composite the brand mark top-left, background-matched "
            "via the kit's [assets]. Refused on every other scene — see _SCENE_EXTRAS."
        ),
    )
    parser.add_argument(
        "--still",
        action="append",
        default=None,
        type=Path,
        metavar="PNG",
        help=(
            "caller-row only, repeatable and ORDER-SIGNIFICANT: the four callers' stills in row "
            "order. The row order is a fact of the composition, so it is stated by the caller "
            "rather than inferred from a filename. phone-walkthrough: the further screens a "
            "`swap` action slides to, numbered 1.. in order (--image is screen 0)."
        ),
    )
    parser.add_argument(
        "--actions",
        type=Path,
        default=None,
        help=(
            "phone-walkthrough (enter|exit|scroll|zoom|highlight|tap|swap) and hero-reveal "
            "(enter|exit|highlight) only: the timed action list, a JSON object "
            '{"actions": [{"type": ..., "start_s", "end_s", ...}], "layout": {...}}. Rects and '
            "points are in screenshot pixels for phone-walkthrough, source-image pixels for "
            "hero-reveal. Validated before any frame is drawn; see "
            "gtm_core/screen_ui/scenes/phone_actions.py and .../hero_reveal.py."
        ),
    )
    parser.add_argument(
        "--cta-text",
        default=None,
        help=(
            "title-close only: the card's call to action, when it is film copy rather than the "
            "tenant's [cta].url. Omit to keep the kit lookup."
        ),
    )
    parser.add_argument(
        "--logo-variant",
        default=None,
        help=(
            "title-claim/title-close only: which [assets].logo_<variant>_<background> to composite. "
            "Default 'horizontal'; pass 'symbol' for a brand whose end card is its mark."
        ),
    )
    parser.add_argument(
        "--title",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "title-claim/title-close only, repeatable: line_one=, line_two=. A card's two lines are "
            "one film's argument, so a second film states its own; unset keys keep the built-in spine."
        ),
    )
    parser.add_argument(
        "--message",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "message-card only, repeatable: sender=, role=, body=. The card's copy is film "
            "content, so it is stated by the caller; unset keys keep the scene's generic "
            "fictional defaults. Never a real person, company, handle, address or number."
        ),
    )
    parser.add_argument(
        "--bubble",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help=(
            "chat-bubble only, repeatable: kind= (outgoing|incoming|chip), text=, side= "
            "(left|right), y_frac=, w_frac=, arrive_s=. The words are film content, so the "
            "caller states them; unset keys derive from the kind. Never a real person, company, "
            "handle, address or number."
        ),
    )
    parser.add_argument(
        "--speak-from-s",
        type=float,
        default=None,
        help="caller-row only: when the agent starts answering — the aura idles before it.",
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument(
        "--min-tolerance",
        type=float,
        default=None,
        help="audit-fit only: exit 1 if any string survives a face less than this much wider "
        "than the kit's. Real sans faces vary by ~20%% in advance width, so 1.15 is a defensible "
        "floor and anything under it is holding by luck",
    )
    parser.add_argument(
        "--for-scene",
        choices=("hero-reveal", "phone-walkthrough"),
        default=None,
        help="fit-layout only: which scene's geometry to measure against the caption band.",
    )
    parser.add_argument(
        "--caption-lines",
        type=int,
        choices=(1, 2),
        default=2,
        help="fit-layout only: worst-case caption line count when no real text is known.",
    )
    parser.add_argument(
        "--margin-px",
        type=int,
        default=40,
        help="fit-layout only: clearance required on every side of the measured box.",
    )
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
    parser = _build_parser()
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

    missing = [
        f"--{n.replace('_', '-')}" for n in ("duration_s", "out_dir") if getattr(args, n) is None
    ]
    if missing:
        print(json.dumps({"scene": args.scene, "error": f"{' and '.join(missing)} required"}))
        return 2
    # Which extra kwargs each scene accepts, DECLARED. The old `if args.scene == "still-push"`
    # gave one flag the property that a typo in --image stayed harmless on every other scene;
    # stating the map keeps that property for every flag added since, instead of re-deriving it
    # per flag and eventually forgetting to.
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
