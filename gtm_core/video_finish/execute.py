from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..video_lint import SAFE_AREAS
from .errors import FfmpegUnavailable, PlanError
from .ffmpeg import _run_ffmpeg
from .plan import FinishPlan, _ratio_slug, _screens_for_caption_stage


@dataclass(frozen=True)
class FinishResult:
    finish_plan: FinishPlan
    out_path: Path
    executed: bool
    sidecar_path: Path
    caption_manifest_path: Path | None = None
    skipped: bool = False  # True when short-circuited by a matching plan_id


def _existing_plan_id(sidecar: Path) -> str | None:
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text()).get("plan_id")
    except (json.JSONDecodeError, OSError):
        return None


def _carried_suppressions(sidecar: Path, asset_name: str) -> list[dict]:
    """``lint_suppressions`` entries from the PREVIOUS ``finish-<ratio>.json`` worth carrying
    into this write: only ``dict`` entries whose ``asset`` names this run's own final asset.

    Never raises — a missing, malformed, or oddly-shaped prior manifest carries nothing rather
    than blocking the new write; re-validating what IS carried is
    ``video_lint.suppress._validate_suppressions``'s job alone, not this one's. A re-run whose
    plan_id changed (a graded param tweaked, a caption edited) used to drop every suppression a
    human had already recorded, so V1/V10 fired again on findings that were already reviewed and
    accepted — this is what stops that.
    """
    if not sidecar.is_file():
        return []
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("lint_suppressions")
    if not isinstance(raw, list):
        return []
    return [
        entry
        for entry in raw
        if isinstance(entry, dict) and str(entry.get("asset", "")) == asset_name
    ]


def _write_spec_sidecar(
    spec: dict | None, *, out_dir: Path, ratio_slug: str, finish_plan: FinishPlan
) -> None:
    """Save the exact spec this run was given as ``finish-spec-<ratio>.json`` beside the
    manifest — the replay input ``video-finish/body_template.md`` already names at
    ``--spec content/<active>/video/<script-slug>/finish-spec-<ratio>.json``, now backed by code
    that actually writes it.

    Written verbatim (``plan()`` never mutates its ``spec`` argument), plus one extra recoverable
    fact neither ``--source`` nor ``--product`` is recorded anywhere else once this runs:
    ``_invocation``. ``product`` is read from the spec dict itself, never a CLI flag — this
    function has no channel to ``--product`` — so a caller that wants it on record can declare it
    there; ``plan()`` silently ignores unknown top-level spec keys, so neither this key nor its
    absence ever perturbs ``plan_id``.

    No-op when ``spec`` is falsy (a caller that never had one), and no-op when the target file
    already holds byte-identical content — the case a caller hits by replaying
    ``--spec <this exact file's own path>``: skip the write entirely rather than touch a file
    that already says exactly this.
    """
    if not spec:
        return
    payload = {
        **spec,
        "_invocation": {"source": finish_plan.source, "product": spec.get("product")},
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    target = out_dir / f"finish-spec-{ratio_slug}.json"
    if target.is_file() and target.read_text(encoding="utf-8") == text:
        return
    tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _build_filtergraph(
    finish_plan: FinishPlan,
    *,
    num_video_inputs: int,
    caption_rendered=None,
) -> str:
    """Chain scale (normalize+upscale) -> eq (grade) -> N overlays (captions) into one filter_complex."""
    area = SAFE_AREAS[finish_plan.ratio]
    parts = [f"[0:v]scale={area.width}:{area.height}[norm]"]
    label = "norm"

    grade_stage = next(s for s in finish_plan.stages if s.name == "grade")
    eq_args = grade_stage.args
    eq_terms = ":".join(f"{k}={v}" for k, v in eq_args.items()) if eq_args else "contrast=1.0"
    parts.append(f"[{label}]eq={eq_terms}[graded]")
    label = "graded"

    if caption_rendered:
        # Each caption PNG is a FULL-FRAME-sized transparent canvas — captions.render() already
        # draws the text at its computed absolute position within that canvas. The overlay must
        # sit at (0, 0); positioning it at r.box['x']/['y'] would double-apply the offset and push
        # the caption off the right edge of the frame for a wide line.
        for i, r in enumerate(caption_rendered, start=1):
            out_label = f"vcap{i}" if i < len(caption_rendered) else "vout"
            enable = f"between(t,{r.screen.start_s},{r.screen.end_s})"
            parts.append(f"[{label}][{i}:v]overlay=x=0:y=0:enable='{enable}'[{out_label}]")
            label = out_label
    else:
        parts.append(f"[{label}]null[vout]")

    return ";".join(parts)


def _identity_used_from_render(
    out_dir: Path, ratio_slug: str, spec_identity: object = None
) -> tuple[str, ...]:
    """Resolve ``identity_used`` — from the sibling ``render-<ratio>.json`` when there is one,
    else from the finish spec's own ``identity_used``.

    The render manifest stays the SINGLE OWNER wherever it exists: a spec that also declares
    identity alongside a render manifest is refused rather than merged, because two hand-kept
    copies of the same fact are how they silently diverge.

    The spec fallback exists because absence used to conflate two very different things. When
    this was written the only manifest-less asset was real footage (the operator's own camera, a
    video-clip trim), which genuinely has nothing to disclose — so () was the right answer. An
    out-of-pipeline SYNTHETIC render is a third case that did not exist then: HeyGen returns a
    finished avatar video with a trained likeness and a cloned voice and no render manifest at
    all. Under the old rule that asset resolved to (), the Article 50 duty never fired, and the
    finish pass produced an undisclosed synthetic asset without a single warning (observed
    2026-08-20 — the disclosure only landed because it was passed by hand).

    Fail-closed, deliberately: declaring identity commits you to disclosing it. A spec that names
    an identity but carries no ``disclosure_line`` is refused here rather than quietly rendered,
    which is the same posture ``validate_disclosure`` takes at the publish gate.
    """
    candidate = out_dir / f"render-{ratio_slug}.json"
    payload: object = None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        if spec_identity:
            raise PlanError(
                f"spec declares identity_used={spec_identity!r} but {candidate.name} already "
                "owns that fact. Remove it from the spec — the render manifest is the single "
                "owner, and a second copy is how the two silently diverge."
            )
        values = payload.get("identity_used")
        return tuple(str(v) for v in values) if isinstance(values, list) else ()

    if spec_identity is None:
        return ()
    if not isinstance(spec_identity, list) or not all(isinstance(v, str) for v in spec_identity):
        raise PlanError('spec identity_used takes a list of strings (e.g. ["avatar", "voice"])')
    return tuple(spec_identity)


def _write_manifest(
    finish_plan: FinishPlan,
    *,
    asset_path: Path,
    executed: bool,
    out_dir: Path,
    captions_payload: dict | None,
    repo_root: Path | None,
    spec: dict | None = None,
) -> Path:
    """Write finish-<ratio>.json through the canonical render_manifest.FinishManifest schema —
    never an ad-hoc dict. FinishPlan.to_json() (with its 'source' key and dict-shaped stages) is
    the plan PREVIEW shown by --dry-run; it is a different contract from the on-disk manifest
    gtm_core.outcomes/gtm_core.video_lint read back via render_manifest.load_finish(), which
    expects stage NAMES only and has no 'source' field.

    Also carries forward any ``lint_suppressions`` scoped to this asset from the manifest this
    write is about to replace (see :func:`_carried_suppressions`), and — when ``spec`` is given —
    saves it beside the manifest as ``finish-spec-<ratio>.json`` (see :func:`_write_spec_sidecar`).
    """
    from ..render_manifest import FinishManifest, write_finish_manifest

    ratio_slug = _ratio_slug(finish_plan.ratio)
    sidecar_path = out_dir / f"finish-{ratio_slug}.json"
    carried = _carried_suppressions(sidecar_path, asset_path.name)
    if carried:
        tiers = ", ".join(dict.fromkeys(str(e.get("tier", "")) for e in carried))
        print(
            f"carried {len(carried)} lint suppression(s) ({tiers}) from the previous manifest — "
            "re-verify with video_lint --no-suppress",
            file=sys.stderr,
        )
    manifest = FinishManifest(
        profile=finish_plan.profile,
        slug=finish_plan.slug,
        ratio=ratio_slug,
        asset_path=str(asset_path),
        stages=tuple(s.name for s in finish_plan.stages),
        census=finish_plan.census(),
        executed=executed,
        plan_id=finish_plan.plan_id,
        lint_suppressions=tuple(carried),
        captions=captions_payload,
        identity_used=_identity_used_from_render(out_dir, ratio_slug, finish_plan.spec_identity),
        caption_route=finish_plan.caption_route,
        caption_route_suppression=finish_plan.caption_route_suppression,
        audio_context=finish_plan.audio_context,
        spoken_text=finish_plan.spoken_text,
        captions_preburned=finish_plan.captions_preburned,
        overlays=finish_plan.preburned_overlays,
        transitions=finish_plan.transitions,
    )
    written = write_finish_manifest(
        manifest,
        out_dir=out_dir,
        repo_root=repo_root,
        preset_resolved=bool(finish_plan.captions_preset),
        declared_route=finish_plan.captions_route,
    )
    _write_spec_sidecar(spec, out_dir=out_dir, ratio_slug=ratio_slug, finish_plan=finish_plan)
    return written


def execute(
    finish_plan: FinishPlan,
    *,
    workdir: Path,
    out_dir: Path,
    kit: dict | None = None,
    repo_root: Path | None = None,
    spec: dict | None = None,
) -> FinishResult:
    """Run a FinishPlan against the real source file. Raises FfmpegUnavailable when ffmpeg is not
    on PATH — captions still render (Pillow-only) and finish-<ratio>.json still gets written with
    executed=False before the raise, so the degraded state is always on disk, never silent.

    ``spec`` — the raw dict this plan was built from — is saved beside the manifest as
    ``finish-spec-<ratio>.json`` whenever the manifest itself is written (see
    :func:`_write_spec_sidecar`); pass ``None`` to skip it.
    """
    if any(s.name == "cuts" for s in finish_plan.stages):
        raise NotImplementedError(
            "execute() does not implement the 'cuts' stage yet (Phase A ships with cuts=[] only "
            "— reframe/trim execution is a later phase). Refusing to silently claim a plan as "
            "'executed' when part of it was never applied."
        )
    workdir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    ratio_slug = _ratio_slug(finish_plan.ratio)
    out_path = out_dir / f"{finish_plan.slug}-{ratio_slug}-final.mp4"
    sidecar_path = out_dir / f"finish-{ratio_slug}.json"

    if _existing_plan_id(sidecar_path) == finish_plan.plan_id and out_path.is_file():
        return FinishResult(
            finish_plan=finish_plan,
            out_path=out_path,
            executed=True,
            sidecar_path=sidecar_path,
            skipped=True,
        )

    caption_stage = next((s for s in finish_plan.stages if s.name == "captions"), None)
    caption_rendered = None
    caption_manifest_path = None
    # A pre-burned source's geometry (scaled at plan time) is the manifest's captions payload;
    # a plan never carries both this and a captions stage (plan() refuses the pair).
    captions_payload = finish_plan.preburned_captions
    if caption_stage is not None:
        from ..captions import render as render_captions
        from ..captions import resolve_placement, sidecar_payload, write_sidecar
        from .burn import _measure_backdrop_luma

        screens = _screens_for_caption_stage(caption_stage.args)
        placement = resolve_placement(kit or {}, ratio=finish_plan.ratio)
        # One glyph choice covers the whole asset (render_captions takes a single
        # backdrop_luma), so sample the span's midpoint rather than per-screen — the same
        # single-measurement shape burn.py's per-shot stage already uses, just spanning the
        # whole video instead of one shot. Unmeasured (ffmpeg/Pillow missing) falls back to
        # render()'s prior default (light glyphs) exactly as before this measurement existed.
        backdrop_luma = _measure_backdrop_luma(
            Path(finish_plan.source),
            ratio=finish_plan.ratio,
            placement=placement,
            at_s=(screens[0].start_s + screens[-1].end_s) / 2 if screens else 0.0,
        )
        caption_rendered = render_captions(
            screens,
            ratio=finish_plan.ratio,
            kit=kit or {},
            out_dir=workdir / "captions",
            repo_root=repo_root,
            # ratio= is what makes an upper-placement preset resolve to lower on 9:16/16:9,
            # where the face band leaves upper no room clear of a presenter's head.
            placement=placement,
            backdrop_luma=backdrop_luma,
        )
        caption_manifest_path = write_sidecar(
            caption_rendered, ratio=finish_plan.ratio, out_dir=out_dir
        )
        captions_payload = sidecar_payload(caption_rendered, ratio=finish_plan.ratio)

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        _write_manifest(
            finish_plan,
            asset_path=out_path,
            executed=False,
            out_dir=out_dir,
            captions_payload=captions_payload,
            repo_root=repo_root,
            spec=spec,
        )
        raise FfmpegUnavailable("ffmpeg is not on PATH — captions rendered, encoding did not run")

    inputs = ["-i", finish_plan.source]
    if caption_rendered:
        for r in caption_rendered:
            inputs += ["-i", str(r.png_path)]

    filtergraph = _build_filtergraph(
        finish_plan,
        num_video_inputs=1 + len(caption_rendered or []),
        caption_rendered=caption_rendered,
    )
    loudnorm_stage = next(s for s in finish_plan.stages if s.name == "loudnorm")
    encode_stage = next(s for s in finish_plan.stages if s.name == "encode")
    target_lufs = loudnorm_stage.args["target_lufs"]

    # creation_time derived from plan_id, never wall clock — an mvhd timestamp alone would break
    # byte-for-byte idempotency across runs of the identical plan.
    creation_time = f"2000-01-01T00:00:{int(finish_plan.plan_id[:8], 16) % 60:02d}Z"

    part_path = workdir / f"{out_path.name}.part"
    args = [
        ffmpeg_bin,
        "-y",
        *inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "0:a?",
        "-af",
        f"loudnorm=I={target_lufs}:TP=-1:LRA=11",
        "-c:v",
        "libx264",
        "-crf",
        str(encode_stage.args["crf"]),
        "-preset",
        str(encode_stage.args["preset"]),
        "-g",
        str(encode_stage.args["gop"]),
        "-threads",
        str(encode_stage.args["threads"]),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-fflags",
        "+bitexact",
        "-flags",
        "+bitexact",
        "-map_metadata",
        "-1",
        "-metadata",
        f"creation_time={creation_time}",
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)

    _write_manifest(
        finish_plan,
        asset_path=out_path,
        executed=True,
        out_dir=out_dir,
        captions_payload=captions_payload,
        repo_root=repo_root,
        spec=spec,
    )

    return FinishResult(
        finish_plan=finish_plan,
        out_path=out_path,
        executed=True,
        sidecar_path=sidecar_path,
        caption_manifest_path=caption_manifest_path,
    )
