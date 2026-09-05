from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from ..video_lint import SAFE_AREAS
from .constants import (
    DEFAULT_CRF,
    DEFAULT_GOP,
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PRESET,
    DEFAULT_THREADS,
)
from .errors import PlanError


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


#: The named ways the vendor caption route can fail, so a suppression says WHICH failure rather
#: than "reap didn't work". Each is a distinct decision downstream: a marshalling fault is a
#: client/server contract bug that will recur until someone fixes the schema; an exhausted quota
#: is a billing decision; an unresolved preset is a tenant-config gap that no retry will clear.
#: Free text was the old shape, and it produced suppressions that recorded a mood.
VENDOR_FALLBACK_REASONS = frozenset(
    {
        # The tool is not callable at all in this session — absent from the tool surface, or it
        # loads with a schema the client cannot satisfy. Verified server-side on 2026-08-30:
        # `add_captions` advertises an empty `{"type":"object"}` input schema, so its two-step
        # `confirm` argument is marshalled as the STRING "true" and rejected by a zod
        # `literal(true)` ("Invalid literal value, expected true"). Not credits, not the upload,
        # not the token — a type-marshalling boundary, and the integer `resolution` failed the
        # same way. A fresh session saw the same empty schema.
        "tool_unavailable",
        "schema_marshalling",
        # The account has no caption/transcription credit left. Distinct from a fault: retrying
        # costs money, and the operator decides that, not this pass.
        "quota_exhausted",
        # The call was made and the vendor returned an error or an unusable result.
        "vendor_error",
        # No `captions.preset` resolved from the brand kit. The local burn is then the ONLY route
        # and there is nothing to suppress — recorded for symmetry, refused by plan() if used.
        "preset_unresolved",
    }
)


def vendor_fallback_suppression(code: str, *, detail: str, observed: str) -> str:
    """Build the `caption_route_suppression` string for an AUTOMATIC fall back to the local burn.

    ``plan()`` already refuses to burn locally against a resolving preset unless the spec records
    a suppression — that gate is what stopped 24 caption screens going over a speaker's face while
    the paid route sat untouched. What it could not do is check that the suppression SAYS
    anything: any non-empty string satisfied it, so "local burn" or "reap failed" passed.

    THIS IS THE SEAM, AND IT IS DELIBERATELY SPLIT. The Reap call happens over MCP from the skill,
    not from this module — a library cannot attempt a tool call, so the attempt/fallback flow is
    prompt-side (`video-finish` Step 3.5). What belongs in code is the VOCABULARY and the FORMAT:
    which failures are recognised, and what a recorded one must contain. That way the skill cannot
    invent a category, and a later reader of `finish-<ratio>.json` gets a failure class they can
    act on rather than a sentence somebody typed once.

    ``detail`` is the vendor's own words — the error text, verbatim, not a paraphrase. ``observed``
    is an ISO-8601 date: a route that failed six months ago is not evidence about today, and an
    undated suppression quietly becomes permanent.
    """
    code = code.strip()
    if code not in VENDOR_FALLBACK_REASONS:
        raise PlanError(
            f"unknown caption fallback reason {code!r} — expected one of "
            f"{sorted(VENDOR_FALLBACK_REASONS)}. A new failure class is a change to this "
            "frozenset with its own comment, not a free-text string."
        )
    detail = detail.strip()
    if not detail:
        raise PlanError(
            f"caption fallback {code!r} needs a `detail` — the vendor's own error text, verbatim. "
            "A suppression with no evidence in it is the bypass this gate exists to refuse."
        )
    observed = observed.strip()
    if not observed:
        raise PlanError(
            "caption fallback needs an `observed` date (ISO-8601). An undated suppression "
            "silently becomes permanent — a route that failed once is not a route that is broken."
        )
    return f"vendor-fallback:{code} ({observed}): {detail}"


def _screens_for_caption_stage(args: dict):
    """Rebuild the exact screen list a captions Stage's args describe — shared by plan() (to
    count num_screens for the census) and execute() (to actually render), so the two can never
    silently disagree about what a disclosure_line-bearing stage produces. Lazy import: only
    touches Pillow-adjacent code when a caption stage actually exists."""
    from ..captions import split_screens, split_screens_segmented, with_disclosure

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
