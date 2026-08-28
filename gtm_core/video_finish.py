"""The finishing layer that has never existed as code. `grep -rl ffmpeg gtm_core/ agent/ tests/`
returned zero hits before this module — the one asset this system has ever shipped looks decent
only because of an untested, unversioned, brand-unaware script living in `content/` (state, not
code).

Phase A ships the minimal chain the one real asset needs:

    normalize → upscale 1080 → cuts → grade → captions → loudness → encode → lint → manifest

(F8's corrected stage order matters for exactly what ships here: a crop/reframe cut *before* the
caption burn keeps every recorded caption bbox valid; a grade *before* the burn means a brand-
colour check on the graded frame is honest, because it measures pixels that were never
composited with white caption glyphs.)

Phase E adds three additive, separately-callable pieces for the long-form case — none of them
touch `plan()`/`execute()`, which stay exactly as Phase A shipped them:

    stitch()          — join finished per-shot files (concat demuxer, re-encode only where a
                         shot was reframed) into one source BEFORE it reaches execute()
    duck_music_bed()  — sidechain-compress a music bed under a voice track into one AAC mix
    (cover-frame extraction lives in :mod:`gtm_core.cover_frame`, not here — it needs Pillow,
    which this module deliberately does not import; see that module's own boundary reasoning.)

THE PIPELINE IS A VALUE BEFORE IT IS A PROCESS
------------------------------------------------
:func:`plan` returns a :class:`FinishPlan` — an ordered tuple of :class:`Stage` plus a
:meth:`FinishPlan.census` — built with **no ffmpeg present and no filesystem writes**. The
single-grade invariant ("never grade twice") is checked inside `plan()` itself and is therefore
assertable in a pure unit test; the census is also written verbatim into `finish-<ratio>.json`,
so `assert plan(...).census()["grade"] == 1` is checkable from the artifact after the fact, not
just from a code review.

IDEMPOTENCY (run twice → byte-identical, captions never double-burned)
-------------------------------------------------------------------------
Three things, all required:

1. ``plan_id = sha256(canonical_json(plan))[:16]``. :func:`execute` short-circuits to **zero**
   ffmpeg invocations when the output already exists with a matching ``plan_id`` recorded in its
   sidecar ``finish-<ratio>.json``.
2. ``-fflags +bitexact -flags +bitexact -map_metadata -1`` plus a ``creation_time`` derived from
   ``plan_id`` (never wall-clock — an ``mvhd`` timestamp alone breaks byte-equality across runs).
3. Pinned ``-preset``/``-crf``/``-g``/``-threads N`` (``-threads 0`` makes output depend on the
   host's core count).

Every ffmpeg call is ``subprocess.run([...], check=True)`` — an **arg list**, never
``shell=True`` (bandit B602 is HIGH and scans this package). Intermediates land in
``workdir/*.part``; the finished file is placed via one atomic ``os.replace`` — a crash never
leaves a plausible-looking half-finished mp4 where a caller might mistake it for done.

FFMPEG ABSENT
-------------
:func:`plan` still succeeds — it is pure data and touches no subprocess. :func:`execute` renders
captions if the spec has any (rendering is Pillow, not ffmpeg — see :mod:`gtm_core.captions`),
writes ``finish-<ratio>.json`` with ``"executed": false`` and the full ordered stage list, then
raises :class:`FfmpegUnavailable` naming the missing binary. The CLI turns that into exit 3.
Never a silent partial output, never exit 0.

WHY THE CLI CARRIES mux/stitch AND NOT JUST run
------------------------------------------------
``Bash(ffmpeg:*)`` is DENIED in ``.claude/settings.json`` (2026-08-18) so a hand-authored filter
chain cannot bypass this module's grading / caption / loudness invariants. That denial covers the
whole binary, but only ``run`` (grade+caption+encode) had a CLI verb — leaving the multi-shot
render's other two ffmpeg steps with no sanctioned path at all, which is how ``video-render``'s
prose ended up telling callers to shell out to raw ffmpeg for them. ``mux`` and ``stitch`` close
that gap: every ffmpeg invocation still originates inside ``gtm_core`` via :func:`_run_ffmpeg`
(arg list, never ``shell=True``), and both verbs confine their output to the resolved content root
(:func:`_confined_output`) so the boundary is real rather than nominal.

CLI::

    uv run python -m gtm_core.video_finish run --profile P --slug S --ratio 9x16 \\
        --spec finish-spec.json [--dry-run] [--json]
    uv run python -m gtm_core.video_finish predictor-trim --in draft.mp4 --out draft-14s.mp4
    uv run python -m gtm_core.video_finish mux --video shot1.mp4 --audio vo1.mp3 \\
        --out muxed/shot1.mp4 [--atempo-max 1.15] [--json]
    uv run python -m gtm_core.video_finish stitch --segments segments.json --ratio 4x5 \\
        --out joined.mp4 [--crossfade-s 0] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess  # nosec B404 — ffmpeg/ffprobe orchestration, arg lists only, never shell=True
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .video_lint import SAFE_AREAS

#: 15.041667s (361 frames @ 24fps) is the observed predictor-cap failure duration. A keyframe-
#: accurate stream copy at -t 14.9 lands right back on a GOP boundary near 15.04s, so the trim
#: MUST re-encode — never -c copy.
PREDICTOR_TRIM_S = 14.9

#: Pinned encode parameters — part of the idempotency contract (F: "-threads 0" is host-dependent).
DEFAULT_CRF = 20
DEFAULT_PRESET = "medium"
DEFAULT_GOP = 48
DEFAULT_THREADS = 4
DEFAULT_LOUDNESS_LUFS = -14.0

#: How far :func:`mux` may speed a VO up to fit its shot before refusing. atempo is transparent at
#: small ratios and audibly distorts a cloned voice well before its own 2.0 limit, so the ceiling is
#: a craft bound, not a technical one. Past it, the mismatch is a script-length problem to fix at
#: the source (a longer render, or a shorter line) — see :func:`mux`.
MUX_ATEMPO_MAX = 1.15

#: Ceiling for a shot whose mouth was animated against this exact VO. `atempo` rescales the audio
#: AFTER the model has already committed the mouth to the original timing, so any value but 1.0
#: slides the words off the lips by exactly that factor — 1.12 on a 3.36s shot is ~400 ms of drift
#: by the end of the line, which is the "lip sync isn't working" the operator reported on
#: 2026-08-19. A speed-up that is inaudible on a disembodied voice-over is NOT harmless once a
#: face is speaking it, which is why this is a second, much tighter number rather than a re-tune
#: of the one above.
MUX_ATEMPO_MAX_LIP_SYNCED = 1.02

#: How much silent video tail ``mux`` will trim before it treats the mismatch as a render defect
#: rather than a rounding artefact.
#:
#: The ``truncate-video`` branch looked free — it only ever discarded silence. It is not. A video
#: model spreads mouth motion across the WHOLE duration it was asked to generate, so a 5.04s shot
#: carrying a 4.08s VO performs 5.04s of phonemes against 4.08s of audio: the mouth runs slow and
#: drifts further out of step every second, and cutting the tail does not fix the 4.08s already
#: shown. Verified 2026-08-18 — 5 of 6 shots of a shipped asset truncated this way, and the
#: operator's first note on the result was "lip sync wasn't working, it looks really off".
#:
#: A small trim is still legitimate (provider durations are integers, VO lengths are not), which is
#: why this is a tolerance and not zero.
MUX_TRUNCATE_MAX_S = 0.35


class FfmpegUnavailable(RuntimeError):
    """ffmpeg is not on PATH. Never silently skipped — the caller must know encoding did not run."""


class PlanError(ValueError):
    """A FinishPlan would violate an invariant (e.g. more than one grade stage)."""


class LintError(RuntimeError):
    """A polish step was refused because the asset failed a shipped lint tier."""


class PolishError(RuntimeError):
    """A Reap polish call was refused or misconfigured."""


@dataclass(frozen=True)
class Stage:
    name: str
    args: dict


#: Mirrors render_manifest.CAPTION_ROUTES — an independent literal, not an import, for the same
#: reason that module keeps its own copy of the identity vocabulary: a drift between the two is
#: something a shared test should catch, not something a shared import should hide.
_CAPTION_ROUTES = frozenset({"reap", "local", "none"})


@dataclass(frozen=True)
class FinishPlan:
    profile: str
    slug: str
    ratio: str
    source: str
    stages: tuple[Stage, ...]
    plan_id: str
    #: Identity declared by the SPEC, for an out-of-pipeline synthetic render that has no
    #: render-<ratio>.json of its own (a HeyGen avatar video). None when the spec said nothing —
    #: which is different from [], "explicitly nothing". Never used when a render manifest
    #: exists; that file remains the single owner. See _identity_used_from_render.
    spec_identity: list[str] | None = None
    #: Which renderer burned the captions ("reap" | "local" | "none"), and — when the tenant had
    #: a preset configured and the local path was taken anyway — why. Resolved at plan time so a
    #: silently-skipped Reap route fails BEFORE any encode, not after an asset exists.
    caption_route: str = ""
    caption_route_suppression: str = ""
    #: The tenant's `captions.preset` as the CALLER read it from the brand kit ("" = none
    #: configured, or the caller never looked). What makes `caption_route` checkable at all.
    captions_preset: str = ""
    #: Facts about the MIX that only the producer knows — whether a music bed is present, whether
    #: it was ducked, and at what levels. Lifted from the spec at plan time (like spec_identity)
    #: and written to the finish manifest, where video_lint's CLI merges its own MEASURED keys on
    #: top before evaluating V5 and V10. Nothing populated this before 2026-08-28, which is why
    #: V5 was structurally dead: the CLI read a key no producer ever wrote.
    audio_context: dict | None = None
    #: The voice-over line the captions were cut from, for V8's caption/voice divergence check.
    #: Dormant for the same reason and fixed the same way.
    spoken_text: str = ""

    def census(self) -> dict[str, int]:
        counts = {"grade": 0, "overlay": 0, "loudnorm": 0, "concat": 0}
        for s in self.stages:
            if s.name == "grade":
                counts["grade"] += 1
            elif s.name == "captions":
                counts["overlay"] += s.args.get("num_screens", 0)
            elif s.name == "loudnorm":
                counts["loudnorm"] += 1
            elif s.name == "concat":
                counts["concat"] += 1
        return counts

    def to_json(self) -> dict:
        return {
            "profile": self.profile,
            "slug": self.slug,
            "ratio": self.ratio,
            "source": self.source,
            "plan_id": self.plan_id,
            "stages": [asdict(s) for s in self.stages],
            "census": self.census(),
        }


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _ratio_slug(ratio: str) -> str:
    """Filename-safe ratio: '9:16' -> '9x16'. A literal ':' in an ffmpeg output filename is
    parsed as a protocol separator ('Unable to choose an output format') — never write one raw
    into a path."""
    return ratio.replace(":", "x")


def _screens_for_caption_stage(args: dict):
    """Rebuild the exact screen list a captions Stage's args describe — shared by plan() (to
    count num_screens for the census) and execute() (to actually render), so the two can never
    silently disagree about what a disclosure_line-bearing stage produces. Lazy import: only
    touches Pillow-adjacent code when a caption stage actually exists."""
    from .captions import split_screens, split_screens_segmented, with_disclosure

    segments = args.get("segments")
    if segments:
        screens = split_screens_segmented(segments)
    else:
        screens = split_screens(args["text"], total_s=args.get("total_s"))
    disclosure_line = args.get("disclosure_line")
    if disclosure_line:
        screens = with_disclosure(screens, disclosure_line, total_s=args["total_s"])
    return screens


def plan(*, profile: str, slug: str, ratio: str, source: str, spec: dict) -> FinishPlan:
    """Build the ordered stage list for one finish run. Pure — no ffmpeg, no filesystem writes.

    ``spec`` (finish-spec.json-shaped):
        cuts: list[{"start": float, "end": float}]   — reframe/trim segments, empty for Phase A
        grade: dict                                   — eq filter params (brightness/contrast/...)
        caption_text: str | None                      — burned-in caption source text
        total_s: float | None                         — duration captions are apportioned across
        captions_preset: str | None                   — the tenant's captions.preset as the
                                                          caller read it from the brand kit; what
                                                          makes caption_route checkable
        caption_route: str | None                     — "reap" | "local" | "none"; derived from
                                                          the stages when omitted
        caption_route_suppression: str | None          — why local was taken while a preset
                                                          resolved. Required in exactly that case
        disclosure_line: str | None                   — Article 50 line, burned onto the asset's
                                                          own last ~2.5s as one more caption
                                                          screen (never a separately appended
                                                          segment); REQUIRES total_s to be set —
                                                          raises PlanError otherwise, since there
                                                          is no duration to anchor the hold window
                                                          to
        loudness_target: float                        — LUFS target, default -14.0
        crf / preset / gop / threads                  — encode overrides
    """
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]

    stages: list[Stage] = [
        Stage("normalize", {}),
        Stage("upscale", {"width": area.width, "height": area.height}),
    ]

    cuts = spec.get("cuts") or []
    if cuts:
        stages.append(Stage("cuts", {"segments": cuts}))

    stages.append(Stage("grade", dict(spec.get("grade") or {})))

    spec_identity = spec.get("identity_used")
    if spec_identity and not spec.get("disclosure_line"):
        raise PlanError(
            f"spec declares identity_used={spec_identity!r} but no disclosure_line. A render "
            "carrying a trained likeness or a cloned voice owes an EU AI Act Art. 50 "
            "disclosure; refusing rather than producing an undisclosed synthetic asset. Set "
            "disclosure_line from the brand kit's [disclosure].line, verbatim."
        )

    caption_text = spec.get("caption_text")
    if spec.get("caption_segments") and not caption_text:
        # 2026-08-28: a spec with segments and no caption_text produced a fully linted,
        # completely uncaptioned asset — the whole stage was skipped and nothing said so. The
        # segments ARE the caption text; refuse rather than silently drop them.
        raise PlanError(
            "spec has caption_segments but no caption_text, so the captions stage would be "
            "skipped entirely and the asset would ship with no burned captions at all. Set "
            "caption_text (the joined segment texts are the obvious value), or drop "
            "caption_segments if the asset is genuinely meant to have none."
        )
    if caption_text:
        total_s = spec.get("total_s")
        disclosure_line = spec.get("disclosure_line")
        if disclosure_line and not total_s:
            raise PlanError(
                "spec has disclosure_line but no total_s — disclosure timing needs a known "
                "duration to anchor the hold window to the asset's own final seconds, never a "
                "guess"
            )
        caption_args = {
            "text": caption_text,
            "total_s": total_s,
            "disclosure_line": disclosure_line,
        }
        # Per-shot caption windows, when the script is a shot list. Keeps each caption inside the
        # shot whose voice-over speaks it instead of chunking one blob across the whole asset.
        if spec.get("caption_segments"):
            caption_args["segments"] = spec["caption_segments"]
        screens = _screens_for_caption_stage(caption_args)
        stages.append(Stage("captions", {**caption_args, "num_screens": len(screens)}))

    stages.append(
        Stage("loudnorm", {"target_lufs": spec.get("loudness_target", DEFAULT_LOUDNESS_LUFS)})
    )
    stages.append(
        Stage(
            "encode",
            {
                "crf": spec.get("crf", DEFAULT_CRF),
                "preset": spec.get("preset", DEFAULT_PRESET),
                "gop": spec.get("gop", DEFAULT_GOP),
                "threads": spec.get("threads", DEFAULT_THREADS),
            },
        )
    )

    grade_count = sum(1 for s in stages if s.name == "grade")
    if grade_count != 1:
        raise PlanError(f"exactly one grade stage is required, got {grade_count}")

    plan_id_payload = {
        "profile": profile,
        "slug": slug,
        "ratio": ratio,
        "source": source,
        "stages": [asdict(s) for s in stages],
    }
    plan_id = hashlib.sha256(_canonical_json(plan_id_payload).encode()).hexdigest()[:16]

    # --- caption route -------------------------------------------------------------------
    # Same shape as the identity_used/disclosure_line pairing above, and for the same reason: the
    # rule was already written in the video-finish body, in prose, and was skipped anyway. A
    # tenant that configured `captions.preset` has chosen the Reap route; taking the local burn-in
    # instead is allowed but must be WRITTEN DOWN. Resolved here rather than at manifest-write
    # time so it fails before the encode, while the fix is still cheap.
    captions_preset = str(spec.get("captions_preset") or "").strip()
    caption_route = str(spec.get("caption_route") or "").strip()
    caption_suppression = str(spec.get("caption_route_suppression") or "").strip()
    if not caption_route:
        # Derive the honest default from what this plan will actually DO. Reap captions are burned
        # by the vendor outside this module, so they can only ever be declared, never inferred.
        caption_route = "local" if any(s.name == "captions" for s in stages) else "none"
    if caption_route not in _CAPTION_ROUTES:
        raise PlanError(
            f"spec caption_route={caption_route!r} is not one of {sorted(_CAPTION_ROUTES)}"
        )
    if caption_route != "local" and caption_suppression:
        raise PlanError(
            f"spec sets caption_route={caption_route!r} with a caption_route_suppression "
            f"({caption_suppression!r}) — a suppression only means something when the configured "
            "route was not taken."
        )
    if caption_route == "local" and captions_preset and not caption_suppression:
        raise PlanError(
            f"spec resolves captions_preset={captions_preset!r} but burns captions locally with "
            "no caption_route_suppression. The tenant configured a Reap preset, so that is the "
            "route: `transcribe` for real per-word timings, then `add_captions` with the preset. "
            "This is the bypass that put 24 caption screens over the speaker's face on "
            "2026-08-18 while the Reap plan sat at 0 of 600 credits used — it was already "
            "forbidden in prose and skipped anyway, which is why it is checked here. To override "
            "deliberately, set caption_route_suppression to the reason."
        )

    return FinishPlan(
        profile=profile,
        slug=slug,
        ratio=ratio,
        source=source,
        stages=tuple(stages),
        plan_id=plan_id,
        spec_identity=list(spec_identity) if spec_identity else spec_identity,
        caption_route=caption_route,
        caption_route_suppression=caption_suppression,
        captions_preset=captions_preset,
        audio_context=_audio_context_from_spec(spec),
        spoken_text=str(spec.get("spoken_text") or ""),
    )


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


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True)  # nosec B603 — arg list, never shell=True


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


def _audio_context_from_spec(spec: dict) -> dict | None:
    """The DECLARED half of the audio context — what the producer knows and measurement cannot.

    Whether a music bed exists under a finished mono-sum, and whether it was sidechain-ducked,
    are properties of the mix that no analysis of the rendered file can recover. They are
    declared here; ``gtm_core.video_lint``'s CLI merges the measured half (silence runs,
    integrated loudness, true peak) on top before evaluating V5 and V10.

    Returns ``None`` when the spec declares nothing, which is honestly different from
    "declared no bed" — a tier that cannot tell those apart would either nag every asset or
    excuse every asset."""
    declared = {
        key: spec[key]
        for key in ("has_music_bed", "has_voice", "ducking_applied", "music_lufs", "voice_lufs")
        if key in spec
    }
    if not declared:
        return None
    declared.setdefault("loudness_target", spec.get("loudness_target", DEFAULT_LOUDNESS_LUFS))
    return declared


def _write_manifest(
    finish_plan: FinishPlan,
    *,
    asset_path: Path,
    executed: bool,
    out_dir: Path,
    captions_payload: dict | None,
    repo_root: Path | None,
) -> Path:
    """Write finish-<ratio>.json through the canonical render_manifest.FinishManifest schema —
    never an ad-hoc dict. FinishPlan.to_json() (with its 'source' key and dict-shaped stages) is
    the plan PREVIEW shown by --dry-run; it is a different contract from the on-disk manifest
    gtm_core.outcomes/gtm_core.video_lint read back via render_manifest.load_finish(), which
    expects stage NAMES only and has no 'source' field."""
    from .render_manifest import FinishManifest, write_finish_manifest

    ratio_slug = _ratio_slug(finish_plan.ratio)
    manifest = FinishManifest(
        profile=finish_plan.profile,
        slug=finish_plan.slug,
        ratio=ratio_slug,
        asset_path=str(asset_path),
        stages=tuple(s.name for s in finish_plan.stages),
        census=finish_plan.census(),
        executed=executed,
        plan_id=finish_plan.plan_id,
        captions=captions_payload,
        identity_used=_identity_used_from_render(out_dir, ratio_slug, finish_plan.spec_identity),
        caption_route=finish_plan.caption_route,
        caption_route_suppression=finish_plan.caption_route_suppression,
        audio_context=finish_plan.audio_context,
        spoken_text=finish_plan.spoken_text,
    )
    return write_finish_manifest(
        manifest,
        out_dir=out_dir,
        repo_root=repo_root,
        preset_resolved=bool(finish_plan.captions_preset),
    )


def execute(
    finish_plan: FinishPlan,
    *,
    workdir: Path,
    out_dir: Path,
    kit: dict | None = None,
    repo_root: Path | None = None,
) -> FinishResult:
    """Run a FinishPlan against the real source file. Raises FfmpegUnavailable when ffmpeg is not
    on PATH — captions still render (Pillow-only) and finish-<ratio>.json still gets written with
    executed=False before the raise, so the degraded state is always on disk, never silent."""
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
    captions_payload = None
    if caption_stage is not None:
        from .captions import render as render_captions
        from .captions import resolve_placement, sidecar_payload, write_sidecar

        screens = _screens_for_caption_stage(caption_stage.args)
        caption_rendered = render_captions(
            screens,
            ratio=finish_plan.ratio,
            kit=kit or {},
            out_dir=workdir / "captions",
            repo_root=repo_root,
            # ratio= is what makes an upper-placement preset resolve to lower on 9:16/16:9,
            # where the face band leaves upper no room clear of a presenter's head.
            placement=resolve_placement(kit or {}, ratio=finish_plan.ratio),
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
    )

    return FinishResult(
        finish_plan=finish_plan,
        out_path=out_path,
        executed=True,
        sidecar_path=sidecar_path,
        caption_manifest_path=caption_manifest_path,
    )


@dataclass(frozen=True)
class ShotSegment:
    """One finished per-shot file (already muxed video + its own VO) feeding :func:`stitch`.
    ``reframed=True`` is the caller's own signal that this shot's ratio/crop diverged from the
    others and therefore MUST be re-encoded before the join — never inferred silently."""

    path: str
    reframed: bool = False


def _probe_fps(path: Path) -> float:
    """Real output frame rate, as an r_frame_rate fraction resolved to float (e.g. '24/1' -> 24.0,
    '24000/1001' -> 23.976...). Needed by :func:`predictor_trim` to compute a safety margin — see
    that function's docstring for why a plain ``-t`` target isn't reliable on its own."""
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = json.loads(out.stdout)["streams"][0]["r_frame_rate"]
    num, _, den = raw.partition("/")
    return float(num) / float(den or 1)


def _probe_dims(path: Path) -> tuple[int, int]:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _probe_duration(path: Path, *, stream_selector: str | None = "v:0") -> float:
    """Return a media duration in seconds.

    ``stream_selector`` picks which stream's own duration is preferred — ``"v:0"`` (the default,
    used by the crossfade math so it fails closed when a transition outruns its shortest segment)
    or ``None`` to skip stream selection entirely, which is what an audio-only VO track needs.
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise FfmpegUnavailable("ffprobe is not on PATH")
    select_args = ["-select_streams", stream_selector] if stream_selector else []
    out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
        [
            ffprobe_bin,
            "-v",
            "error",
            *select_args,
            "-show_entries",
            "stream=duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(out.stdout)
    # Stream duration first, container duration as the fallback — this preserves the historical
    # behavior exactly. Two latent bugs were fixed here on 2026-08-19 while wiring the mux path,
    # both of which only ever bit an audio-only input:
    #   1. `-show_entries stream=duration,format=duration` never requested format.duration at all —
    #      ffprobe separates SECTIONS with ':', not ','. The format fallback was dead code.
    #   2. `payload["streams"][0]` on a file with no matching stream raised IndexError.
    # A video file always has a v:0 duration, so neither surfaced until a .mp3 was probed.
    streams = payload.get("streams") or [{}]
    raw = streams[0].get("duration") or payload.get("format", {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise FfmpegUnavailable(f"could not probe duration from {path}: {raw!r}") from exc


def _normalize_shot(
    src: Path, *, target_w: int, target_h: int, workdir: Path, force_reencode: bool
) -> Path:
    """Return a shot file guaranteed to match ``target_w``x``target_h``. Re-encodes ONLY when
    ``force_reencode`` (the shot was reframed) or the shot's own dims already differ from
    target — everything else passes through untouched. This is what makes "re-encode only where
    reframed" actually cheaper than re-encoding the whole batch: a shot rendered at the correct
    ratio to begin with costs zero extra encode work here."""
    w, h = _probe_dims(src)
    if not force_reencode and (w, h) == (target_w, target_h):
        return src
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / f"norm_{src.stem}.mp4"
    part = dst.with_suffix(dst.suffix + ".part")
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale={target_w}:{target_h}",
        "-c:v",
        "libx264",
        "-crf",
        str(DEFAULT_CRF),
        "-preset",
        str(DEFAULT_PRESET),
        "-g",
        str(DEFAULT_GOP),
        "-threads",
        str(DEFAULT_THREADS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "mp4",
        str(part),
    ]
    _run_ffmpeg(args)
    os.replace(part, dst)
    return dst


@dataclass(frozen=True)
class MuxResult:
    """What :func:`mux` actually did — recorded rather than inferred, because the audio/video
    duration relationship decides whether the spoken line survives intact."""

    out_path: Path
    video_duration_s: float
    audio_duration_s: float
    strategy: str  # "truncate-video" | "atempo"
    atempo: float | None = None


def _suggest_duration(
    audio_s: float,
    *,
    atempo_max: float = MUX_ATEMPO_MAX,
    truncate_max_s: float = MUX_TRUNCATE_MAX_S,
) -> int | None:
    """The integer render duration that lets :func:`mux` accept this VO, or ``None`` if none does.

    Providers take an integer duration, so the two ceilings above bracket a *band* of legal
    lengths rather than a point: a duration is legal when the leftover silence is within
    ``truncate_max_s`` (``d >= audio_s - truncate_max_s``) or the VO compresses to fit within
    ``atempo_max`` (``d >= audio_s / atempo_max``).

    That band can be empty. A 3.60s VO admits no integer: 3s needs atempo 1.18 (past 1.15) and 4s
    leaves a 0.40s tail (past 0.35). Returning ``None`` rather than a plausible-looking number is
    the point — the earlier message computed ``round(audio_s)``, which for 3.60s suggested the
    very 4s duration that had just been refused, sending the reader in a circle.
    """
    lo = max(2, math.ceil(audio_s / atempo_max - 1e-9))
    hi = math.floor(audio_s + truncate_max_s)
    return lo if lo <= hi else None


def mux(
    video_path: Path,
    audio_path: Path,
    *,
    out_path: Path,
    workdir: Path,
    audio_bitrate: str = "192k",
    atempo_max: float = MUX_ATEMPO_MAX,
    truncate_max_s: float = MUX_TRUNCATE_MAX_S,
    lip_synced: bool = False,
) -> MuxResult:
    """Attach one VO track to one silent rendered shot, refusing to lose words silently.

    ``video-render`` generates each shot with ``generate_audio: false`` and passes the VO only as
    ``audio_references`` (which drives mouth motion, not the output audio track), so every rendered
    shot arrives SILENT and its real audio has to be attached here. Video comes from input 0 and
    audio from input 1 explicitly, so a source with no audio stream at all is the normal case, not
    an error.

    The duration relationship is the whole reason this is a function and not a one-line ffmpeg call:

    * **audio <= video, within ``truncate_max_s``** → ``strategy="truncate-video"``. ``-shortest``
      ends the output when the VO ends, trimming the short silent tail rather than holding a
      motionless presenter through dead air.
    * **audio <= video, tail past ``truncate_max_s``** → :class:`PlanError`. This is the *other*
      half of the lip-sync defect and it used to pass silently, because discarding silence looks
      lossless. It isn't: the model already spread a full shot's worth of mouth motion across a
      duration the audio never fills, so the visible mouth runs slow from frame one and trimming
      the end cannot recover it. The fix is upstream — re-render the shot at the VO's own length.
    * **audio > video, within ``atempo_max``** → ``strategy="atempo"``. The VO is time-compressed to
      fit. ``atempo`` is transparent at small ratios, which is what makes this safe *only* while
      bounded.
    * **audio > video, past ``atempo_max``** → :class:`PlanError`. This is a script-length problem,
      not a mux-time one: ``-shortest`` here would cut the end off the spoken line (on a payoff beat
      that silently deletes the whole point of the video), and a large ``atempo`` audibly distorts
      the cloned voice. The error names both real fixes — re-render the shot longer, or shorten the
      line — instead of picking a lossy one.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    if atempo_max < 1.0:
        raise ValueError(f"atempo_max must be >= 1.0, got {atempo_max}")
    if lip_synced:
        atempo_max = min(atempo_max, MUX_ATEMPO_MAX_LIP_SYNCED)

    video_s = _probe_duration(video_path)
    audio_s = _probe_duration(audio_path, stream_selector=None)

    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = workdir / f"{out_path.name}.part"

    args = [ffmpeg_bin, "-y", "-i", str(video_path), "-i", str(audio_path)]

    if audio_s <= video_s:
        tail_s = video_s - audio_s
        if tail_s > truncate_max_s:
            suggestion = _suggest_duration(
                audio_s, atempo_max=atempo_max, truncate_max_s=truncate_max_s
            )
            if suggestion is None:
                remedy = (
                    f"No integer duration fits this VO: {math.floor(audio_s + truncate_max_s)}s "
                    f"would need atempo>{atempo_max} and "
                    f"{math.floor(audio_s + truncate_max_s) + 1}s leaves a tail past "
                    f"{truncate_max_s}s. Re-roll the VO (TTS length varies run to run) or adjust "
                    "the line — do not widen a ceiling to land in the gap between them."
                )
            else:
                remedy = (
                    f"Re-render this shot at duration={suggestion}s (integer, matching the VO) "
                    "instead of a fixed length"
                )
            raise PlanError(
                f"the shot is {video_s:.2f}s but its VO is only {audio_s:.2f}s — a {tail_s:.2f}s "
                f"silent tail, past the {truncate_max_s}s ceiling. Trimming it does NOT fix the "
                "result: the model spread a full 5s-shot's worth of mouth motion across a "
                "duration the audio never fills, so the mouth runs slow from the first frame and "
                f"reads as broken lip sync however the tail is cut. {remedy} — see "
                "gtm_core.shots_lint's VO/duration parity check, which catches this before any "
                "spend."
            )
        strategy = "truncate-video"
        atempo = None
        args += ["-map", "0:v", "-map", "1:a", "-shortest"]
    else:
        required = audio_s / video_s
        if required > atempo_max:
            raise PlanError(
                f"VO is {audio_s:.2f}s but the shot is only {video_s:.2f}s — fitting it would need "
                f"atempo={required:.3f}, past the {atempo_max} transparency ceiling"
                + (
                    " for a LIP-SYNCED shot — the mouth was animated against this VO's original "
                    "timing, so rescaling the audio slides the words off the lips by that same "
                    "factor"
                    if lip_synced
                    else ""
                )
                + ". This is a script-length problem, not a mux problem: re-render the shot "
                "longer (an integer duration the model accepts) or shorten the spoken line. "
                "Refusing to truncate the end of the line or audibly distort the cloned voice to "
                "paper over it."
            )
        strategy = "atempo"
        atempo = required
        args += [
            "-filter:a",
            f"atempo={required:.6f}",
            "-map",
            "0:v",
            "-map",
            "1:a",
        ]

    args += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)

    return MuxResult(
        out_path=out_path,
        video_duration_s=video_s,
        audio_duration_s=audio_s,
        strategy=strategy,
        atempo=atempo,
    )


def frames_to_video(
    frames_glob: str,
    *,
    fps: int,
    out_path: Path,
    silent_audio: bool = True,
) -> Path:
    """Encode a numbered PNG frame sequence (e.g. ``gtm_core.screen_ui``'s output) into a
    silent mp4, via the sole sanctioned ffmpeg entry point (:func:`_run_ffmpeg`).

    ``frames_glob`` is an ffmpeg-style pattern (``shot2-booking-%04d.png``), matching the
    zero-padded naming :mod:`gtm_core.screen_ui` writes. This is a THIRD reason a raw frame
    sequence must not be turned into video from outside this module (alongside grading and
    captions): ``Bash(ffmpeg:*)`` is denied precisely so a hand-authored encode cannot bypass
    this module's invariants, and a locally-drawn UI mockup is no more exempt from that than a
    provider's render is.

    ``silent_audio`` adds a silent stereo track (ffmpeg's ``anullsrc``) matched to the video's
    duration. `mux()` and `stitch()` both assume every segment carries an audio stream — a video-
    only file desyncs a downstream concat that expects one, so this defaults to on rather than
    leaving that failure for someone to rediscover at stitch time.
    """
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    args = [ffmpeg_bin, "-y", "-framerate", str(fps), "-i", frames_glob]
    if silent_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16"]
    if silent_audio:
        args += ["-c:a", "aac", "-shortest"]
    args += ["-f", "mp4", str(part_path)]

    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path


def stitch(
    segments: list[ShotSegment],
    *,
    ratio: str,
    out_path: Path,
    workdir: Path,
    crossfade_s: float = 0.0,
) -> Path:
    """Concatenate finished per-shot files into one asset.

    By default this uses the ffmpeg CONCAT DEMUXER with ``-c copy`` — the cheapest path, and
    correct when every segment came from the same locked encode settings. A segment already at the
    target ratio's pixel dimensions and not flagged ``reframed`` is returned untouched, so only
    shots that actually need it pay a re-encode cost.

    When ``crossfade_s > 0`` the join is re-encoded via ``filter_complex`` using ``xfade`` for
    video and ``acrossfade`` for audio. This is intentionally more expensive: crossfades cannot be
    done with stream copy. The requested duration is validated against the shortest normalized
    segment — if a segment is too short to hold the transition, the function raises rather than
    silently producing a malformed output.
    """
    if not segments:
        raise ValueError("stitch() needs at least one segment")
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    workdir.mkdir(parents=True, exist_ok=True)

    normalized = [
        _normalize_shot(
            Path(seg.path),
            target_w=area.width,
            target_h=area.height,
            workdir=workdir / "normalized",
            force_reencode=seg.reframed,
        )
        for seg in segments
    ]

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    if crossfade_s <= 0.0 or len(normalized) == 1:
        # Hard-cut path: concat demuxer + stream copy.
        list_path = workdir / "concat_list.txt"
        list_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in normalized) + "\n"
        )
        args = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-f",
            "mp4",
            str(part_path),
        ]
        _run_ffmpeg(args)
        os.replace(part_path, out_path)
        return out_path

    # Crossfade path: filter_complex re-encode.
    durations = [_probe_duration(p) for p in normalized]
    min_segment = min(durations)
    min_supported = crossfade_s * 2
    if min_segment < min_supported:
        raise ValueError(
            f"requested crossfade_s={crossfade_s}s requires each segment to be at least "
            f"{min_supported}s, but the shortest normalized segment is {min_segment:.3f}s"
        )

    inputs: list[str] = []
    for p in normalized:
        inputs.extend(["-i", str(p)])

    # Video: chain xfade filters. Transition k starts at sum(durations[0..k]) - (k+1)*crossfade_s.
    v_parts: list[str] = []
    v_label = "0:v"
    for i in range(1, len(normalized)):
        offset = sum(durations[:i]) - i * crossfade_s
        out_label = f"vf{i}"
        if i == len(normalized) - 1:
            out_label = "vout"
        v_parts.append(
            f"[{v_label}][{i}:v]xfade=transition=fade:duration={crossfade_s}:"
            f"offset={offset:.6f}[{out_label}]"
        )
        v_label = out_label

    # Audio: chain acrossfade filters. Each transition overlaps the last `crossfade_s` seconds
    # of the accumulated output with the start of the next input. acrossfade has no offset param:
    # it always crossfades over the overlapping tail/head of its two inputs.
    a_parts: list[str] = []
    a_label = "0:a"
    for i in range(1, len(normalized)):
        out_label = f"af{i}"
        if i == len(normalized) - 1:
            out_label = "aout"
        a_parts.append(f"[{a_label}][{i}:a]acrossfade=d={crossfade_s}[{out_label}]")
        a_label = out_label

    filtergraph = ";".join(v_parts + a_parts)

    args = [
        ffmpeg_bin,
        "-y",
        *inputs,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-crf",
        str(DEFAULT_CRF),
        "-preset",
        str(DEFAULT_PRESET),
        "-g",
        str(DEFAULT_GOP),
        "-threads",
        str(DEFAULT_THREADS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path


def duck_music_bed(
    voice_path: Path,
    music_path: Path,
    *,
    out_path: Path,
    workdir: Path,
    duck_threshold: float = 0.05,
    duck_ratio: float = 8.0,
) -> Path:
    """Sidechain-compress ``music_path``'s audio against ``voice_path``'s audio (ffmpeg's
    ``sidechaincompress`` filter — present in the operator's local build, verified live, unlike
    F1's drawtext/libass gap) so the music ducks under speech, then mix the ducked music with the
    voice into one AAC stream. Reads only the AUDIO stream from each input (either may be a full
    video file); returns an audio-only ``.m4a``, not a muxed video — the caller mixes it onto the
    finished visual separately.

    ``sidechaincompress`` expects matching sample rate / channel layout on both inputs; this
    function does not resample or probe for that — mismatched inputs fail loudly at the ffmpeg
    call rather than silently producing wrong-sounding output.
    """
    if out_path.suffix != ".m4a":
        raise ValueError(f"out_path must end in .m4a, got {out_path.name!r}")
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = out_path.with_suffix(out_path.suffix + ".part")

    filtergraph = (
        f"[1:a]sidechaincompress=threshold={duck_threshold}:ratio={duck_ratio}:"
        "attack=5:release=300[ducked];[0:a][ducked]amix=inputs=2:duration=first:"
        "dropout_transition=0[aout]"
    )
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(voice_path),
        "-i",
        str(music_path),
        "-filter_complex",
        filtergraph,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "mp4",
        str(part_path),
    ]
    _run_ffmpeg(args)
    os.replace(part_path, out_path)
    return out_path


def _confined_output(out_path: Path, *, content_root: Path | None) -> Path:
    """Resolve a CLI ``--out`` and refuse anything outside the content root.

    The write-side mirror of :func:`_safe_asset_path` (which guards reads of an existing file):
    this one's target does not exist yet, so it checks the resolved *parent* instead. CLAUDE.md's
    "the only writable state is the resolved content root" is the rule being enforced — the `mux`
    and `stitch` verbs exist so ffmpeg runs from inside this module rather than from a raw Bash
    call, and that boundary would be theatre if the module then wrote anywhere it was pointed.
    """
    from .paths import resolve_content_root

    root = (content_root if content_root is not None else resolve_content_root()).resolve()
    resolved = out_path.expanduser().resolve()
    try:
        resolved.parent.relative_to(root)
    except ValueError as exc:
        raise PolishError(
            f"refusing to write outside the resolved content root: {resolved} (root: {root})"
        ) from exc
    return resolved


def _safe_asset_path(asset_path: Path, *, content_root: Path) -> Path:
    """Refuse an asset outside the resolved content root, missing, or non-file."""
    resolved = asset_path.expanduser().resolve()
    root = content_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PolishError(
            f"refusing to polish a file outside the resolved content root: {resolved} "
            f"(root: {root})"
        ) from exc
    if not resolved.is_file():
        raise PolishError(f"source file does not exist: {resolved}")
    return resolved


def polish_with_reap(
    asset_path: Path,
    profile: str,
    *,
    ratio: str,
    content_root: Path | None = None,
    reap_caller: Callable[[Path], Any] | None = None,
    purpose: str | None = None,
) -> Any:
    """Lint-gated Reap video-polish pass.

    The file must resolve under the active profile's content root; absolute paths, ``..``
    segments, or paths outside the root are refused. The asset is probed and evaluated with
    :mod:`gtm_core.video_lint`; any shipped ERROR tier finding raises :class:`LintError` before
    the Reap caller is invoked. This keeps expensive Reap minutes from being spent on assets that
    would fail the local quality gate anyway.

    ``reap_caller`` is the skill-layer hook that actually invokes the Reap MCP tool(s). The
    signature is ``reap_caller(asset_path: Path) -> Any``. ``gtm_core`` modules do not call MCP
    tools directly (CLAUDE.md §R6), so this parameter is required — passing ``None`` is a
    configuration error and raises :class:`PolishError`.
    """
    from . import video_lint
    from .paths import resolve_content_root

    if reap_caller is None:
        raise PolishError(
            "reap_caller is required for polish_with_reap — gtm_core modules do not invoke MCP tools"
        )

    root = content_root if content_root is not None else resolve_content_root()
    source = _safe_asset_path(asset_path, content_root=root)

    if ratio not in video_lint.SAFE_AREAS:
        raise PolishError(f"unknown ratio {ratio!r} for lint")

    probe = video_lint.probe(source)
    findings = video_lint.evaluate(probe, ratio=ratio, purpose=purpose)
    errors = [f for f in findings if f.severity == video_lint.ERROR]
    if errors:
        summary = "; ".join(f"{f.tier} {f.rule}: {f.excerpt}" for f in errors[:3])
        raise LintError(
            f"refusing Reap polish for {source.name}: {len(errors)} lint ERROR(s) — {summary}"
        )

    return reap_caller(source)


def predictor_trim(src: Path, dst: Path, *, duration_s: float = PREDICTOR_TRIM_S) -> None:
    """Trim to <=duration_s for the virality_predictor's hard input cap. Always re-encodes — a
    stream copy at -t 14.9 lands back on a GOP boundary near the observed 15.041667s failure.

    **`-t duration_s` alone is not reliable here** — verified live 2026-08-15: `-t 14.9` on a
    24fps source re-encoded to 14.916667s (358 frames = ceil(14.9 * 24), not floor), i.e. ffmpeg
    rounds a partial final frame UP rather than dropping it, so the naive call can still exceed
    the cap it was meant to enforce. Fixed by probing the source's real fps and subtracting one
    full frame's duration from the requested target before handing it to `-t` — the rounded-up
    result then lands at or under `duration_s`, never over it."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise FfmpegUnavailable("ffmpeg is not on PATH")
    fps = _probe_fps(src)
    safe_duration_s = duration_s - (1.0 / fps)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    args = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(src),
        "-t",
        f"{safe_duration_s:.6f}",
        "-c:v",
        "libx264",
        "-crf",
        str(DEFAULT_CRF),
        "-preset",
        str(DEFAULT_PRESET),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "mp4",
        str(part),
    ]
    _run_ffmpeg(args)
    os.replace(part, dst)


def main(argv: list[str] | None = None) -> int:
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
        "--crossfade-s",
        type=float,
        default=0.0,
        help="0 (default) = hard cut via concat demuxer + stream copy; >0 re-encodes with xfade",
    )
    stitch_p.add_argument("--content-root", type=Path, default=None)
    stitch_p.add_argument("--json", action="store_true", dest="as_json")

    sheet_p = sub.add_parser(
        "contact-sheet", help="evenly spaced frames as one image, for looking at a finished asset"
    )
    sheet_p.add_argument("--in", dest="src", type=Path, required=True)
    sheet_p.add_argument("--out", dest="dst", type=Path, required=True)
    sheet_p.add_argument("--count", type=int, default=8)
    sheet_p.add_argument("--tile-w", type=int, default=320)

    args = parser.parse_args(argv)

    if args.cmd == "contact-sheet":
        from .cover_frame import contact_sheet

        print(
            json.dumps(
                contact_sheet(args.src, args.dst, count=args.count, tile_w=args.tile_w), indent=2
            )
        )
        return 0

    if args.cmd == "run":
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
        from .brandkit import load_brand_kit
        from .paths import PathConfig

        profiles_root = args.profiles_root or PathConfig.from_env().profiles_root
        try:
            kit = load_brand_kit(profiles_root, args.profile, args.product)
        except (
            ValueError
        ) as exc:  # unsafe segment, or tomllib.TOMLDecodeError (a ValueError subclass)
            print(f"video-finish: could not resolve brand kit: {exc}", file=sys.stderr)
            return 2

        try:
            result = execute(p, workdir=workdir, out_dir=args.out_dir, kit=kit, repo_root=repo_root)
        except FfmpegUnavailable as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 3
        if args.as_json:
            print(
                json.dumps({"out_path": str(result.out_path), "skipped": result.skipped}, indent=2)
            )
        else:
            print(f"video-finish: wrote {result.out_path} (skipped={result.skipped})")
        return 0

    if args.cmd == "predictor-trim":
        try:
            predictor_trim(args.src, args.dst)
        except FfmpegUnavailable as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 3
        print(f"video-finish: wrote {args.dst}")
        return 0

    if args.cmd == "frames-to-video":
        try:
            out = _confined_output(args.out, content_root=args.content_root)
        except PolishError as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 2
        try:
            frames_to_video(args.frames_glob, fps=args.fps, out_path=out)
        except FfmpegUnavailable as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 3
        if args.as_json:
            print(json.dumps({"out_path": str(out), "fps": args.fps}, indent=2))
        else:
            print(f"video-finish: wrote {out}")
        return 0

    if args.cmd == "mux":
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

    if args.cmd == "stitch":
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
        workdir = args.workdir or (out.parent / "_work")
        try:
            out_path = stitch(
                segments,
                ratio=args.ratio,
                out_path=out,
                workdir=workdir,
                crossfade_s=args.crossfade_s,
            )
        except FfmpegUnavailable as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 3
        except ValueError as exc:
            print(f"video-finish: {exc}", file=sys.stderr)
            return 4
        method = "xfade re-encode" if args.crossfade_s > 0 else "concat demuxer (stream copy)"
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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
