"""``gtm_core.screen_ui.build`` — the screen_ui rebuild driver (plan change #6, glittery-sleeping-yao).

Composes the pieces that already exist as separate CLIs into one loop: shots.json
(``production.screen_ui``) -> fit-layout check -> render (+ build record) -> frames-to-video
(sequence-checked) -> stitch -> mux -> run (spec saved) -> video_lint.

Each shot is either a screen_ui render request (``production.screen_ui: {"scene", "image"?,
"stills"?, "actions"?, "duration_s"?}``) or an already-rendered ``file``. Neither present is a
build failure (exit 2), not a silently-stale asset. Every shot is validated BEFORE anything
renders, and the fit-layout check (plan #5) runs before any render too — a clipped caption is
refused while the fix is still free. Per-shot fingerprints (plan #3's
``build_record.fingerprint``) decide render-vs-reuse; a master's own fingerprint is the sha256 of
its INPUTS (per-shot fingerprints + declared transitions + the audio track's digest), never of its
muxed bytes — ffmpeg's output is not bit-reproducible, so naming the master by its own bytes would
defeat the reuse this driver exists to give, in the opposite direction from a fixed path defeating
it by never changing name at all.

CLI::

    uv run python -m gtm_core.screen_ui.build \\
        --shots S.shots.json --kit-json K --ratio 9:16 --fps 30 --audio A.mp4 \\
        --profile P --slug SLUG --work-dir W --out-dir O --spec SPEC.json \\
        [--content-root C] [--force] [--dry-run]

Exit codes: 0 clean, 1 lint error, 2 refusal/confinement/validation, 3 no ffmpeg,
4 stitch/mux/finish refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .. import frame_sequence, page_inputs
from ..captions import FontMissing, load_face
from ..captions import fit as _caption_fit
from ..confine import ConfinementError, confined_source_file
from ..paths import _safe_segment, resolve_content_root
from ..render_manifest import ManifestError
from ..video_finish.confine import _confined_dir
from ..video_finish.errors import FfmpegUnavailable, PlanError, PolishError
from ..video_finish.execute import execute
from ..video_finish.mux import frames_to_video, mux
from ..video_finish.plan import (
    SIDECAR_KINDS,
    TRANSITIONS_KIND,
    _screens_for_caption_stage,
    sidecar_path,
)
from ..video_finish.plan import plan as _plan_finish
from ..video_finish.shots import ShotSegment, transitions_from_shots
from ..video_finish.stitch import stitch

try:
    from ..video_lint import SAFE_AREAS
    from ..video_lint import cli as video_lint_cli
    from ..video_lint.probe import ProbeFailed, ProbeUnavailable
    from ..video_lint.probe import probe as _probe_clip
except ImportError:
    from . import SAFE_AREAS

    video_lint_cli = None
    ProbeFailed = ProbeUnavailable = Exception  # type: ignore[assignment,misc]
    _probe_clip = None  # type: ignore[assignment]

try:  # video craft pre-spend gate (PRD 2026-10-19 R13); absent in the public cut
    from ..video_craft.require import gate as _craft_gate
except ModuleNotFoundError as exc:
    if exc.name != "gtm_core.video_craft":
        raise
    _craft_gate = None

from . import build_record, fit_layout
from .base import SceneError
from .cli import render_scene
from .registry import (
    _HTML_SCENE_EXTRAS,
    _HTML_SCENE_REQUIRED,
    _HTML_SCENES,
    _SCENE_EXTRAS,
    _SCENES,
    html_scene_is_external,
)
from .scene_prepare import preflight_shots

#: The only two scenes fit-layout (plan #4/#5) knows how to measure.
_FIT_SCENES = frozenset({"hero-reveal", "phone-walkthrough"})


class BuildError(Exception):
    """A refusal this driver decided on itself, carrying the exit code ``main`` returns."""

    def __init__(self, message: str, *, code: int) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScreenUiShot:
    id: str
    scene: str | None
    extras: dict[str, object]  # resolved Path / list[Path] / str values
    duration_s: float
    backend: str = "pillow"
    composition: Path | None = None


@dataclass(frozen=True)
class FileShot:
    id: str
    path: Path


# ── step 1: validate + resolve every shot before anything renders ────────────────────────────


def _resolve_input(shots_dir: Path, value: object, *, content_root: Path, shot_id: str) -> Path:
    if not isinstance(value, str) or not value:
        raise BuildError(f"shot {shot_id}: expected a file path string, got {value!r}", code=2)
    candidate = shots_dir / value
    try:
        return confined_source_file(
            candidate, content_root=content_root, action="read a screen_ui shot input"
        )
    except (ConfinementError, ValueError) as exc:  # ValueError: an embedded NUL in the path
        raise BuildError(f"shot {shot_id}: {exc}", code=2) from exc


def _resolve_shot(  # noqa: C901, PLR0912  # PRD 2026-10-16-html-motion-scenes
    shot: object, index: int, *, shots_dir: Path, content_root: Path
) -> ScreenUiShot | FileShot:
    if not isinstance(shot, dict):
        raise BuildError(f"shots[{index}] must be an object", code=2)
    raw_id = shot.get("id")
    shot_id = str(raw_id) if raw_id else f"shot-{index:02d}"
    try:
        # shot_id becomes a bare path segment under --work-dir (frames/<id>, clips/<id>.mp4) — a
        # shots.json is untrusted content (§R5), so an absolute or `..`-bearing `id` must be
        # refused here, before it is ever joined onto a Path, rather than trusted to stay inside
        # the work directory this driver already confined once at the top level.
        shot_id = _safe_segment(shot_id, "shot id")
    except ValueError as exc:
        raise BuildError(f"shots[{index}]: {exc}", code=2) from exc
    production = shot.get("production")
    screen_ui_spec = production.get("screen_ui") if isinstance(production, dict) else None
    file_value = shot.get("file")

    if isinstance(screen_ui_spec, dict):
        raw_backend = screen_ui_spec.get("backend")
        if raw_backend is not None and raw_backend not in ("pillow", "html"):
            raise BuildError(
                f"shot {shot_id}: backend {raw_backend!r} is not supported; choose from 'pillow', 'html'",
                code=2,
            )
        scene = screen_ui_spec.get("scene")
        comp = screen_ui_spec.get("composition")
        if comp is not None and scene is not None:
            raise BuildError(
                f"shot {shot_id}: scene and composition are mutually exclusive", code=2
            )
        if comp is not None:
            if not isinstance(comp, str) or not comp:
                raise BuildError(f"shot {shot_id}: composition must be a non-empty string", code=2)
            comp_path = _resolve_input(shots_dir, comp, content_root=content_root, shot_id=shot_id)
            backend, scene = "html", None
        elif raw_backend == "html" or (
            isinstance(scene, str) and scene in _HTML_SCENES and scene not in _SCENES
        ):
            backend, comp_path = "html", None
            if not isinstance(scene, str) or (scene not in _HTML_SCENES and scene not in _SCENES):
                raise BuildError(
                    f"shot {shot_id}: production.screen_ui.scene {scene!r} is not a known HTML scene",
                    code=2,
                )
        else:
            backend, comp_path = "pillow", None
            if not isinstance(scene, str) or scene not in _SCENES:
                raise BuildError(
                    f"shot {shot_id}: production.screen_ui.scene {scene!r} is not a known scene",
                    code=2,
                )

        if os.environ.get("GTM_SCREEN_UI_HTML_ENABLED", "1") == "0" and backend == "html":
            if scene is not None and scene in _SCENES:
                backend = "pillow"
            else:
                raise BuildError(
                    f"shot {shot_id}: HTML backend is disabled via GTM_SCREEN_UI_HTML_ENABLED=0",
                    code=2,
                )

        allowed = (
            _HTML_SCENE_EXTRAS.get(
                scene,
                frozenset(
                    {
                        "headline",
                        "subline",
                        "eyebrow",
                        "badge",
                        "kicker",
                        "image",
                        "stills",
                        "actions",
                    }
                ),
            )
            if backend == "html"
            else _SCENE_EXTRAS.get(scene, frozenset())
        )
        extras: dict[str, object] = {}
        for key in (
            "image",
            "stills",
            "actions",
            "headline",
            "subline",
            "eyebrow",
            "badge",
            "kicker",
            "diagram",
            "build",
        ):
            if key not in screen_ui_spec:
                continue
            if key not in allowed:
                raise BuildError(f"shot {shot_id}: scene {scene!r} does not accept {key!r}", code=2)
            raw = screen_ui_spec[key]
            if key in ("headline", "subline", "eyebrow", "badge", "kicker"):
                if not isinstance(raw, str):
                    raise BuildError(f"shot {shot_id}: {key} must be a string", code=2)
                extras[key] = raw
            elif key == "stills":
                if not isinstance(raw, list):
                    raise BuildError(f"shot {shot_id}: stills must be a list of paths", code=2)
                extras["stills"] = [
                    _resolve_input(shots_dir, v, content_root=content_root, shot_id=shot_id)
                    for v in raw
                ]
            else:
                extras[key] = _resolve_input(
                    shots_dir, raw, content_root=content_root, shot_id=shot_id
                )

        if scene == "hero-reveal" and "image" not in extras:
            raise BuildError(
                f"shot {shot_id}: scene 'hero-reveal' requires an 'image' input", code=2
            )
        if scene == "kinetic-type" and "headline" not in extras:
            raise BuildError(
                f"shot {shot_id}: scene 'kinetic-type' requires a 'headline' input", code=2
            )

        if html_scene_is_external(scene):
            # a registered scene's input is closed: a key it does not name is refused, not ignored
            for stray in sorted(set(screen_ui_spec) - allowed - {"scene", "backend", "duration_s"}):
                raise BuildError(
                    f"shot {shot_id}: scene {scene!r} does not accept {stray!r}", code=2
                )
        for need in sorted(_HTML_SCENE_REQUIRED.get(scene, ())):
            if need not in extras:
                raise BuildError(
                    f"shot {shot_id}: scene {scene!r} requires a {need!r} input", code=2
                )

        duration_s = screen_ui_spec.get("duration_s", shot.get("duration_s"))
        if (
            not isinstance(duration_s, int | float)
            or isinstance(duration_s, bool)
            or duration_s <= 0
        ):
            raise BuildError(f"shot {shot_id}: no usable duration_s", code=2)
        return ScreenUiShot(
            id=shot_id,
            scene=scene,
            extras=extras,
            duration_s=float(duration_s),
            backend=backend,
            composition=comp_path,
        )

    if isinstance(file_value, str) and file_value:
        path = _resolve_input(shots_dir, file_value, content_root=content_root, shot_id=shot_id)
        return FileShot(id=shot_id, path=path)

    raise BuildError(f"shot {shot_id}: neither production.screen_ui nor file is present", code=2)


# ── step 2: fit-layout check (plan #5) — before any render ───────────────────────────────────


def _caption_lines(spec: dict, *, kit: dict, ratio: str, repo_root: Path | None) -> int:
    """The real max caption line count from ``spec``'s caption fields, or 2 when none declared."""
    caption_text = spec.get("caption_text")
    if not caption_text:
        return 2
    args = {
        "text": caption_text,
        "total_s": spec.get("total_s"),
        "disclosure_line": spec.get("disclosure_line"),
    }
    if spec.get("caption_segments"):
        args["segments"] = spec["caption_segments"]
    screens = _screens_for_caption_stage(args)
    if not screens:
        return 2
    try:
        face = load_face(kit, repo_root=repo_root)
    except FontMissing as exc:
        raise BuildError(f"fit-layout caption check: {exc}", code=2) from exc
    return max(len(_caption_fit(s.text, area=SAFE_AREAS[ratio], face=face)[0]) for s in screens)


def _run_fit_checks(
    shots: list[ScreenUiShot | FileShot],
    *,
    spec: dict,
    kit: dict,
    ratio: str,
    repo_root: Path | None,
) -> dict[str, dict]:
    """Fit-check every hero-reveal/phone-walkthrough shot BEFORE any render."""
    checked = [
        s
        for s in shots
        if isinstance(s, ScreenUiShot) and s.backend == "pillow" and s.scene in _FIT_SCENES
    ]
    if not checked:
        return {}
    caption_lines = _caption_lines(spec, kit=kit, ratio=ratio, repo_root=repo_root)
    info: dict[str, dict] = {}
    failures: list[str] = []
    for shot in checked:
        try:
            doc = fit_layout.report(
                for_scene=shot.scene,
                kit=kit,
                ratio=ratio,
                image=shot.extras.get("image"),
                actions=shot.extras.get("actions"),
                caption_lines=caption_lines,
                repo_root=repo_root,
            )
        except fit_layout.FitLayoutError as exc:
            raise BuildError(f"shot {shot.id}: fit-layout check failed: {exc}", code=2) from exc
        current = doc["current"]
        info[shot.id] = {
            "fit_ok": current["ok"],
            "fit_clearance_px": current["caption_clearance_px"],
        }
        if not current["ok"]:
            failures.append(f"{shot.id} (current={current})")
    if failures:
        raise BuildError("fit-layout check failed for: " + "; ".join(failures), code=2)
    return info


# ── step 3: per-shot fingerprint / render / reuse ─────────────────────────────────────────────


def _shot_fingerprint(shot: ScreenUiShot | FileShot, *, ratio: str, fps: int, kit: dict) -> str:
    if isinstance(shot, FileShot):
        return f"sha256:{page_inputs.digest(shot.path)}"
    comp_hash = build_record.hash_composition_dir(shot.composition) if shot.composition else None
    hf_ver = "0.8.162" if shot.backend == "html" else None
    return build_record.fingerprint(
        shot.scene or "custom",
        ratio,
        fps,
        shot.duration_s,
        shot.extras,
        kit,
        "caption",
        None,
        backend=shot.backend,
        composition_hash=comp_hash,
        hyperframes_version=hf_ver,
    )


def _will_reuse_shot(shot: ScreenUiShot, *, fp: str, work_dir: Path, force: bool) -> bool:
    if force:
        return False
    out_dir = work_dir / "frames" / shot.id
    clip_path = work_dir / "clips" / f"{shot.id}.mp4"
    existing = build_record.read(out_dir)
    return existing is not None and existing.get("fingerprint") == fp and clip_path.is_file()


def _render_or_reuse_shot(
    shot: ScreenUiShot, *, fp: str, kit: dict, ratio: str, fps: int, work_dir: Path, force: bool,
    content_root: Path | None = None,
) -> Path:  # fmt: skip
    out_dir = work_dir / "frames" / shot.id
    clip_path = work_dir / "clips" / f"{shot.id}.mp4"
    if _will_reuse_shot(shot, fp=fp, work_dir=work_dir, force=force):
        existing = build_record.read(out_dir)
        frames_count = existing["frames"]
    else:
        try:
            if shot.backend == "html":
                from .html_backend import render_html_shot

                result = render_html_shot(
                    shot, out_dir, kit=kit, ratio=ratio, fps=fps, work_dir=work_dir,
                    content_root=content_root,
                )  # fmt: skip
            else:
                result = render_scene(
                    shot.scene, kit=kit, ratio=ratio, fps=fps, duration_s=shot.duration_s,
                    out_dir=out_dir, extras=shot.extras, font_role="caption", repo_root=None,
                )  # fmt: skip
        except SceneError as exc:
            raise BuildError(f"shot {shot.id}: {exc}", code=2) from exc
        record = build_record.read(out_dir)
        frame_pattern = record.get("frame_pattern") if record else None
        if not frame_pattern:
            raise BuildError(f"shot {shot.id}: render produced no usable frame sequence", code=2)
        try:
            frames_to_video(str(out_dir / frame_pattern), fps=fps, out_path=clip_path)
        except frame_sequence.FrameSequenceError as exc:
            raise BuildError(f"shot {shot.id}: {exc}", code=2) from exc
        frames_count = result["frames"]

    if _probe_clip is not None:
        try:
            probed = _probe_clip(clip_path)
        except (ProbeUnavailable, ProbeFailed) as exc:
            raise BuildError(
                f"shot {shot.id}: could not probe rendered clip: {exc}", code=2
            ) from exc
        expected_s = frames_count / fps
        if abs(probed.duration_s - expected_s) > (1 / fps) + 1e-6:
            raise BuildError(
                f"shot {shot.id}: clip duration {probed.duration_s:.3f}s does not match "
                f"{frames_count} frames at {fps}fps ({expected_s:.3f}s expected)",
                code=2,
            )
    return clip_path


# ── step 4-5: master build (stitch + mux, input-addressed name) ──────────────────────────────


def _master_hash(
    shots: list[ScreenUiShot | FileShot],
    fp_map: dict[str, str],
    transitions: list[tuple[str, float]],
    audio_path: Path,
) -> str:
    payload = {
        "shots": [fp_map[s.id] for s in shots],
        "transitions": [list(t) for t in transitions],
        "audio_sha256": page_inputs.digest(audio_path),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _build_master(
    segments: list[ShotSegment], *, ratio: str, work_dir: Path, audio_path: Path,
    transitions: list[tuple[str, float]], final_master: Path,
) -> None:  # fmt: skip
    stitch_dir = work_dir / "_stitch"
    silent_master = stitch_dir / "master-silent.mp4"
    try:
        stitch(
            segments, ratio=ratio, out_path=silent_master, workdir=stitch_dir,
            transitions=transitions, normalize=True,
        )  # fmt: skip
    except ValueError as exc:
        raise BuildError(str(exc), code=4) from exc
    muxed = stitch_dir / "master-muxed.mp4"
    try:
        mux(silent_master, audio_path, out_path=muxed, workdir=stitch_dir, atempo_max=1.0)
    except PlanError as exc:
        raise BuildError(str(exc), code=4) from exc
    final_master.parent.mkdir(parents=True, exist_ok=True)
    os.replace(muxed, final_master)
    # stitched_sidecars() alone misses the transitions sidecar — copy it explicitly too.
    for kind in (*SIDECAR_KINDS, TRANSITIONS_KIND):
        src = sidecar_path(silent_master, kind)
        if src.is_file():
            sidecar_path(final_master, kind).write_bytes(src.read_bytes())


# ── driver ─────────────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gtm_core.screen_ui.build")
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--kit-json", type=Path, required=True)
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--fps", type=int, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--content-root", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _checks_before_render(
    shots_path: Path, shots: list, *, spec: dict, kit: dict, ratio: str, fps: int,
    content_root: Path,
) -> dict:  # fmt: skip
    """Everything that may refuse the build before a frame is drawn; returns the fit info."""
    try:
        preflight_shots(shots, kit=kit, ratio=ratio, fps=fps, content_root=content_root)
    except SceneError as exc:
        raise BuildError(str(exc), code=2) from exc
    fit_info = _run_fit_checks(shots, spec=spec, kit=kit, ratio=ratio, repo_root=None)
    if _craft_gate is not None and _craft_gate(shots_path, entry="screen_ui build") != 0:
        raise BuildError("nothing was rendered: fix the cuts above first", code=4)
    return fit_info


def _run(args: argparse.Namespace) -> int:
    try:
        work_dir = _confined_dir(args.work_dir, content_root=args.content_root)
        out_dir = _confined_dir(args.out_dir, content_root=args.content_root)
    except PolishError as exc:
        raise BuildError(str(exc), code=2) from exc

    content_root = (
        args.content_root.resolve() if args.content_root is not None else resolve_content_root()
    )
    ratio, fps = args.ratio, args.fps

    kit = json.loads(args.kit_json.read_text(encoding="utf-8"))
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    shots_doc = json.loads(args.shots.read_text(encoding="utf-8"))
    shots_list = shots_doc.get("shots")
    if not isinstance(shots_list, list) or not shots_list:
        raise BuildError(f"{args.shots} has no non-empty shots array", code=2)
    shots_dir = Path(args.shots).resolve().parent

    shots = [
        _resolve_shot(shot, i, shots_dir=shots_dir, content_root=content_root)
        for i, shot in enumerate(shots_list)
    ]

    fit_info = _checks_before_render(
        args.shots, shots, spec=spec, kit=kit, ratio=ratio, fps=fps, content_root=content_root
    )

    fp_map: dict[str, str] = {
        s.id: _shot_fingerprint(s, ratio=ratio, fps=fps, kit=kit) for s in shots
    }
    decisions: list[dict] = []
    for shot in shots:
        if isinstance(shot, ScreenUiShot):
            reuse = _will_reuse_shot(shot, fp=fp_map[shot.id], work_dir=work_dir, force=args.force)
            entry = {"id": shot.id, "action": "reuse" if reuse else "render"}
        else:
            entry = {"id": shot.id, "action": "file"}
        entry.update(fit_info.get(shot.id, {}))
        decisions.append(entry)

    try:
        transitions = transitions_from_shots(shots_doc)
    except ValueError as exc:
        raise BuildError(str(exc), code=4) from exc

    master_hash = _master_hash(shots, fp_map, transitions, args.audio)
    final_master = work_dir / f"master-{master_hash}.mp4"
    master_will_reuse = final_master.is_file() and not args.force

    if args.dry_run:
        for entry in decisions:
            print(json.dumps(entry))
        print(json.dumps({"final_master": final_master.name, "reuse": master_will_reuse}))
        return 0

    by_id: dict[str, Path] = {}
    for shot in shots:
        if isinstance(shot, ScreenUiShot):
            by_id[shot.id] = _render_or_reuse_shot(
                shot, fp=fp_map[shot.id], kit=kit, ratio=ratio, fps=fps, work_dir=work_dir,
                force=args.force, content_root=content_root,
            )  # fmt: skip
        else:
            by_id[shot.id] = shot.path
    segments = [ShotSegment(path=str(by_id[s.id])) for s in shots]

    if not master_will_reuse:
        _build_master(
            segments, ratio=ratio, work_dir=work_dir, audio_path=args.audio,
            transitions=transitions, final_master=final_master,
        )  # fmt: skip

    try:
        finish_plan = _plan_finish(
            profile=args.profile, slug=args.slug, ratio=ratio, source=str(final_master), spec=spec
        )
    except (PlanError, ValueError) as exc:
        raise BuildError(str(exc), code=4) from exc

    try:
        result = execute(
            finish_plan, workdir=work_dir / "_finish", out_dir=out_dir, kit=kit, repo_root=None,
            spec=spec,
        )  # fmt: skip
    except (PlanError, ManifestError) as exc:
        raise BuildError(str(exc), code=4) from exc

    if video_lint_cli is not None:
        return video_lint_cli.main(
            [str(result.out_path), "--ratio", ratio, "--manifest", str(result.sidecar_path)]
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except BuildError as exc:
        print(json.dumps({"error": str(exc)}))
        return exc.code
    except FfmpegUnavailable as exc:
        print(json.dumps({"error": str(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
