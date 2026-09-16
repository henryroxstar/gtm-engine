from __future__ import annotations

from .cadence import bed_findings, cadence_findings
from .fixes import _face_band_fix, _screens_phrase
from .measure import _safe_box
from .model import ERROR, SAFE_AREAS, WARN, Finding, face_band
from .probe import Probe
from .thresholds import (
    ASPECT_TOLERANCE,
    AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU,
    AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION,
    AUDIO_FLOOR_ONLY_MIN_DURATION_S,
    MAX_DEAD_AIR_FRACTION,
    MAX_DEAD_AIR_RUN_S,
    MAX_WORDS_PER_SEC_ASSET,
    MAX_WORDS_PER_SEC_SCREEN,
    MIN_BITRATE_1080P_BPS,
    MIN_CAPTION_CONTRAST_RATIO,
    MIN_FPS_ACCEPTABLE,
    MIN_FPS_CLEAN,
    MIN_SHOT_MOTION,
    PREDICTOR_MAX_DURATION_S,
    SILENCE_FLOOR_DBFS,
)


def evaluate(
    p: Probe,
    *,
    ratio: str,
    manifest: dict | None = None,
    edge_stats: dict | None = None,
    purpose: str | None = None,
    audio_context: dict | None = None,
    scene_changes: list[float] | None = None,
    transitions: list[dict] | None = None,
    spoken_text: str | None = None,
    motion_stats: list[dict] | None = None,
    identity_used: list[str] | None = None,
    caption_contrast: list[dict] | None = None,
    caption_route: str | None = None,
    captions_preburned: bool | None = None,
) -> list[Finding]:
    """Pure. Takes a Probe (real or fabricated) plus optional context; returns findings with no
    I/O. ``manifest`` is a captions.json-shaped dict: {"frame": [w, h], "screens": [{"box": {"x",
    "y", "w", "h"}}, ...]}. ``edge_stats`` is {"left_ymax": float, "right_ymax": float, ...} —
    the V3 fallback for an asset with no caption sidecar. ``audio_context`` is {"has_music_bed":
    bool, "has_voice": bool, "ducking_applied": bool, "music_lufs": float|None,
    "voice_lufs": float|None} for V5. ``scene_changes`` is a list of cut timestamps in seconds
    for V6 and V13. ``transitions`` is [{"start", "end", "frames"}, ...] — the MULTI-FRAME
    blends, counted separately from cuts because a dissolve-heavy edit and a cut-heavy one
    have the same scene-change count and completely different rhythms — for V12; ``None``
    means it was never measured (``--fast``, or no ffmpeg) and the tier is skipped rather
    than reported clean. ``spoken_text`` is the voice-over line the captions were cut from, for V8.
    ``motion_stats`` is [{"index": int, "start": float, "end": float, "motion": float}, ...] —
    one entry per shot, ``motion`` being that shot's mean normalised inter-frame delta — for V9.
    ``identity_used`` is the finish manifest's top-level list (["element", "voice"], …); a
    non-empty value means a rendered person is on screen, which promotes V3's face-band overlap
    from WARN to ERROR. It is passed separately rather than read off ``manifest`` because
    ``manifest`` is the *captions* sub-payload and never carried this field.
    ``audio_context`` additionally carries the MEASURED keys ``gtm_core.video_lint.measure_audio``
    produces ({"silent_runs", "silent_fraction", "integrated_lufs", ...}) for V10 — the declared
    keys above and the measured keys share one dict because they answer one question between them.
    ``caption_contrast`` is [{"index": int, "ratio": float, "bg_luma": float}, ...] — one entry
    per sampled caption screen — for V11. ``caption_route`` / ``captions_preburned`` are the
    finish manifest's top-level declarations of whether captions were burned at all; when they
    say yes and ``manifest`` carries no screens, V11 refuses rather than skipping — an
    unreadable gate is a missing gate."""
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    findings: list[Finding] = []

    # V1a — resolution + fps (fixture-provable).
    if p.width < area.width or p.height < area.height:
        findings.append(
            Finding(
                tier="V1",
                rule="resolution",
                severity=ERROR,
                asset="",
                excerpt=f"{p.width}x{p.height} — below the {area.width}x{area.height} floor for {ratio}",
                fix=f"re-render or upscale to at least {area.width}x{area.height}",
            )
        )
    if p.fps < MIN_FPS_ACCEPTABLE:
        findings.append(
            Finding(
                tier="V1",
                rule="fps",
                severity=ERROR,
                asset="",
                excerpt=f"{p.fps:g}fps — below the {MIN_FPS_ACCEPTABLE:g}fps floor",
                fix=f"re-render or reframe at >= {MIN_FPS_CLEAN:g}fps",
            )
        )
    elif p.fps < MIN_FPS_CLEAN:
        findings.append(
            Finding(
                tier="V1",
                rule="fps",
                severity=WARN,
                asset="",
                excerpt=f"{p.fps:g}fps — cinematic-only, below the {MIN_FPS_CLEAN:g}fps clean floor",
                fix=f"prefer >= {MIN_FPS_CLEAN:g}fps unless the cinematic look is deliberate",
            )
        )

    # V1b — bitrate. Only asserted when a bitrate is actually reported; a short CBR fixture's
    # measured rate is dominated by its I-frame, so this sub-check is honestly meaningful only
    # against a real (or calibration-recorded) asset — see the module docstring.
    if p.bit_rate is not None and p.height >= area.height and p.bit_rate < MIN_BITRATE_1080P_BPS:
        findings.append(
            Finding(
                tier="V1",
                rule="bitrate",
                severity=ERROR,
                asset="",
                excerpt=f"{p.bit_rate / 1_000_000:.2f} Mbps — below the "
                f"{MIN_BITRATE_1080P_BPS / 1_000_000:g} Mbps floor @1080p",
                fix="re-encode at a higher target bitrate",
            )
        )

    # V2 — aspect exact. Checks SHAPE (width/height ratio), independent of SIZE (V1's job). This
    # is what catches the soul_2 4:5->3:4 coercion — a different aspect entirely, not merely a
    # low-resolution version of the right one.
    expected_ratio = area.width / area.height
    actual_ratio = p.width / p.height if p.height else 0.0
    if abs(actual_ratio - expected_ratio) > ASPECT_TOLERANCE:
        findings.append(
            Finding(
                tier="V2",
                rule="aspect_exact",
                severity=ERROR,
                asset="",
                excerpt=f"{p.width}x{p.height} (ratio {actual_ratio:.4f}) — requested {ratio} "
                f"(ratio {expected_ratio:.4f})",
                fix="reframe/crop to the exact requested aspect; do not ship the provider's raw output",
            )
        )

    # V3 — caption geometry. Primary: the sidecar bbox, pure arithmetic. Fallback: edge luma WARN.
    if manifest is not None:
        frame = manifest.get("frame")
        if frame and tuple(frame) != (p.width, p.height):
            findings.append(
                Finding(
                    tier="V3",
                    rule="stale_sidecar",
                    severity=ERROR,
                    asset="",
                    excerpt=f"captions.json frame {tuple(frame)} != probed frame {(p.width, p.height)}",
                    fix="regenerate captions against the finished asset's actual dimensions",
                )
            )
        else:
            x0, y0, x1, y1 = _safe_box(area, p.width, p.height)
            band_top, band_bottom = face_band(ratio)
            face_y0 = round(p.height * band_top)
            face_y1 = round(p.height * band_bottom)
            # Both V3 rules are properties of a caption's GEOMETRY, and captions.py places every
            # screen at one of very few positions — so a per-screen finding emits the same defect
            # 64 times and buries every other tier under it. Group by the offending geometry and
            # report the screen count instead: one line per distinct defect, which is what makes
            # the report readable enough to act on.
            outside: dict[tuple, list] = {}
            on_face: dict[tuple, list] = {}
            for screen in manifest.get("screens", []):
                box = screen.get("box", {})
                bx0, by0 = box.get("x", 0), box.get("y", 0)
                bx1, by1 = bx0 + box.get("w", 0), by0 + box.get("h", 0)
                idx = screen.get("index", "?")
                if bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1:
                    outside.setdefault((bx0, by0, bx1, by1), []).append(idx)
                # A caption inside the safe area can still be squarely on the speaker's face —
                # the safe area reserves edges only, so "as far from every margin as possible"
                # and "on the mouth" are the SAME position. Severity depends on whether a
                # rendered identity is actually on screen to be covered.
                if by0 < face_y1 and by1 > face_y0:
                    on_face.setdefault((by0, by1), []).append(idx)

            for (bx0, by0, bx1, by1), idxs in sorted(outside.items()):
                findings.append(
                    Finding(
                        tier="V3",
                        rule="caption_outside_safe_area",
                        severity=ERROR,
                        asset="",
                        excerpt=f"{_screens_phrase(idxs)} box ({bx0},{by0},{bx1},{by1}) "
                        f"outside safe area ({x0},{y0},{x1},{y1})",
                        fix="re-fit via gtm_core.captions (shrink/wrap inside the safe area)",
                    )
                )

            has_identity = bool(identity_used)
            for (by0, by1), idxs in sorted(on_face.items()):
                findings.append(
                    Finding(
                        tier="V3",
                        rule="caption_over_face",
                        severity=ERROR if has_identity else WARN,
                        asset="",
                        excerpt=f"{_screens_phrase(idxs)} box y {by0}-{by1} overlaps the face "
                        f"band {face_y0}-{face_y1} "
                        f"({band_top:.0%}-{band_bottom:.0%} of frame height for {ratio})"
                        + (" with a rendered identity on screen" if has_identity else ""),
                        fix=_face_band_fix(area, band_top),
                    )
                )
    elif edge_stats is not None:
        threshold = edge_stats.get("threshold", 60.0)
        hot_edges = [
            edge
            for edge in ("left_ymax", "right_ymax", "top_ymax", "bottom_ymax")
            if edge_stats.get(edge, 0.0) > threshold
        ]
        if hot_edges:
            findings.append(
                Finding(
                    tier="V3",
                    rule="edge_luma_fallback",
                    severity=WARN,
                    asset="",
                    excerpt=f"no caption manifest — edge luma elevated on {', '.join(hot_edges)} "
                    "(possible caption/graphic bleeding to the frame edge)",
                    fix="attach the asset's captions.json sidecar for an exact geometry check, or "
                    "confirm the edge content is intentional and suppress with a reason",
                )
            )

    # V4 — predictor input duration. Scoped by purpose, not by asset: a long-form asset is
    # legitimately >14.9s and must not trip this.
    if purpose == "predictor" and p.duration_s > PREDICTOR_MAX_DURATION_S:
        findings.append(
            Finding(
                tier="V4",
                rule="predictor_duration_over_cap",
                severity=ERROR,
                asset="",
                excerpt=f"{p.duration_s:.6f}s — over the {PREDICTOR_MAX_DURATION_S:g}s predictor cap",
                fix="trim via gtm_core.video_finish predictor-trim (re-encode with -t 14.9; a "
                "keyframe-accurate stream copy will not move the duration)",
            )
        )

    # V5 (bed vs voice) and V6/V12/V13 (cadence) live in `cadence.py` — moved there by C6,
    # which needed somewhere for the two new rhythm tiers and found this file at 482 of its
    # 500-line ceiling. Called at the position the inline blocks occupied, so finding ORDER is
    # unchanged and every golden that pins it stays valid.
    findings.extend(bed_findings(audio_context))
    findings.extend(
        cadence_findings(
            p, scene_changes=scene_changes, transitions=transitions, motion_stats=motion_stats
        )
    )

    # V7 — caption reading load (WARN candidate). Needs per-screen text AND timing; a screen
    # with no duration is skipped rather than guessed at, and the asset-wide check still runs.
    screens = (manifest or {}).get("screens") or []
    if screens:
        total_words = 0
        for screen in screens:
            text = str(screen.get("text", "") or "")
            words = len(text.split())
            total_words += words
            span = float(screen.get("end_s", 0.0) or 0.0) - float(screen.get("start_s", 0.0) or 0.0)
            if words and span > 0:
                wps = words / span
                if wps > MAX_WORDS_PER_SEC_SCREEN:
                    findings.append(
                        Finding(
                            tier="V7",
                            rule="caption_screen_too_dense",
                            severity=WARN,
                            asset="",
                            excerpt=f"screen {screen.get('index', '?')}: {words} words in "
                            f"{span:.2f}s = {wps:.1f} w/s (ceiling {MAX_WORDS_PER_SEC_SCREEN:g})",
                            fix="cut words from this screen or hold it longer",
                        )
                    )
        if total_words and p.duration_s > 0:
            asset_wps = total_words / p.duration_s
            if asset_wps > MAX_WORDS_PER_SEC_ASSET:
                findings.append(
                    Finding(
                        tier="V7",
                        rule="caption_load_too_high",
                        severity=WARN,
                        asset="",
                        excerpt=f"{total_words} on-screen words across {p.duration_s:.1f}s = "
                        f"{asset_wps:.1f} w/s (ceiling {MAX_WORDS_PER_SEC_ASSET:g}); reading it "
                        f"needs ~{total_words / MAX_WORDS_PER_SEC_ASSET:.0f}s",
                        fix="cut on-screen words, or lengthen the asset to match the reading load",
                    )
                )

    # V8 — caption/voice divergence (WARN candidate). Captions cut from the spoken line are a
    # subset of it; anything else is a second text stream competing for the same attention.
    if screens and spoken_text:
        spoken_words = {w.strip(".,;:!?\"'“”‘’()").lower() for w in spoken_text.split()}
        spoken_words.discard("")
        for screen in screens:
            caption_words = [
                w.strip(".,;:!?\"'“”‘’()").lower()
                for w in str(screen.get("text", "") or "").split()
            ]
            caption_words = [w for w in caption_words if w]
            if not caption_words:
                continue
            foreign = [w for w in caption_words if w not in spoken_words]
            # A stray word is normal (a burned-in unit or a stylised contraction); a screen that
            # is mostly foreign is a separate stream.
            if len(foreign) > len(caption_words) / 2:
                findings.append(
                    Finding(
                        tier="V8",
                        rule="caption_diverges_from_voice",
                        severity=WARN,
                        asset="",
                        excerpt=f"screen {screen.get('index', '?')}: {len(foreign)}/"
                        f"{len(caption_words)} words are not in the spoken line "
                        f"({', '.join(foreign[:4])}…)",
                        fix="cut captions from the spoken line; move citations to a persistent "
                        "lower-third or the post caption",
                    )
                )

    # V9 — within-shot motion (WARN candidate). V6 counts cuts; this counts whether anything
    # moves BETWEEN them. An asset can cut often and still be a slideshow.
    if motion_stats:
        still = [s for s in motion_stats if float(s.get("motion", 0.0)) < MIN_SHOT_MOTION]
        if still:
            still_s = sum(float(s.get("end", 0.0)) - float(s.get("start", 0.0)) for s in still)
            share = still_s / p.duration_s if p.duration_s > 0 else 0.0
            findings.append(
                Finding(
                    tier="V9",
                    rule="static_shots",
                    severity=WARN,
                    asset="",
                    excerpt=f"{len(still)} of {len(motion_stats)} shots are motionless "
                    f"({still_s:.1f}s, {share:.0%} of the asset); "
                    f"quietest {min(float(s.get('motion', 0.0)) for s in still):.4f} "
                    f"vs floor {MIN_SHOT_MOTION:g}",
                    fix="add real motion inside the shot (camera move, staged build, live "
                    "footage) — a sub-perceptual zoom does not count",
                )
            )

    # V10 — dead air. Checked in two independent ways because one number cannot describe both a
    # long film with one dead stretch and a short film that is mostly dead.
    if not p.has_audio:
        findings.append(
            Finding(
                tier="V10",
                rule="no_audio_stream",
                severity=ERROR,
                asset="",
                excerpt="the asset carries no audio stream at all",
                fix="mux a mix (voice, room tone, bed) before encoding — gtm_core.video_finish "
                "pads a shot with no audio to silence, so a missing stream means every shot "
                "was silent",
            )
        )
    elif audio_context is not None:
        runs = audio_context.get("silent_runs") or []
        fraction = audio_context.get("silent_fraction")
        longest = max((float(r.get("duration", 0.0)) for r in runs), default=0.0)
        if fraction is not None and float(fraction) > MAX_DEAD_AIR_FRACTION:
            findings.append(
                Finding(
                    tier="V10",
                    rule="dead_air_fraction",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{float(fraction):.0%} of runtime is below "
                    f"{SILENCE_FLOOR_DBFS:g} dBFS ({audio_context.get('silent_total_s', '?')}s "
                    f"across {len(runs)} run{'' if len(runs) == 1 else 's'}), over the "
                    f"{MAX_DEAD_AIR_FRACTION:.0%} ceiling",
                    fix="give every shot an audio bed — room tone under everything, and a voice, "
                    "SFX or music cue where the shot is carrying an idea. Digital silence under "
                    "a shot is the loudest amateur tell there is",
                )
            )
        if longest >= MAX_DEAD_AIR_RUN_S:
            worst = max(runs, key=lambda r: float(r.get("duration", 0.0)))
            findings.append(
                Finding(
                    tier="V10",
                    rule="dead_air_run",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{longest:.1f}s of continuous silence at "
                    f"{float(worst.get('start', 0.0)):.1f}-{float(worst.get('end', 0.0)):.1f}s, "
                    f"over the {MAX_DEAD_AIR_RUN_S:g}s ceiling",
                    fix="a deliberate beat is under two seconds; anything longer reads as a "
                    "broken file. Lay room tone or a bed under the stretch",
                )
            )
        floor_only = _floor_only(audio_context, p.duration_s)
        if floor_only is not None:
            findings.append(floor_only)

    # V11 — burned-type contrast. Geometry (V3) says the caption is in a legal place; this says
    # it can actually be read once it is there.
    if caption_contrast:
        failed = [
            c
            for c in caption_contrast
            if c.get("ratio") is not None and float(c["ratio"]) < MIN_CAPTION_CONTRAST_RATIO
        ]
        if failed:
            worst = min(failed, key=lambda c: float(c["ratio"]))
            findings.append(
                Finding(
                    tier="V11",
                    rule="caption_contrast",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{len(failed)} of {len(caption_contrast)} sampled caption screens "
                    f"below {MIN_CAPTION_CONTRAST_RATIO:g}:1 against their own backdrop; worst "
                    f"screen {worst.get('index', '?')} at {float(worst['ratio']):.1f}:1",
                    fix="give the caption a scrim, plate or stroke (gtm_core.captions), or move "
                    "it over a darker part of frame — unplated white type over mid-luminance "
                    "footage never clears AA",
                )
            )
    geometry_missing = _caption_geometry_missing(manifest, caption_route, captions_preburned)
    if geometry_missing is not None:
        findings.append(geometry_missing)

    return findings


def _floor_only(audio_context: dict, duration_s: float) -> Finding | None:
    """V10 ``audio_floor_only`` — level with no events. Skipped (never "clean") when the
    momentary series was not measured, when the asset is too short for a held bed to be a
    defect, or when there is no level at all (the dead-air rules own that case)."""
    abruptness = audio_context.get("loudness_abruptness_lu")
    events = audio_context.get("loudness_event_fraction")
    integrated = audio_context.get("integrated_lufs")
    if abruptness is None or events is None or duration_s < AUDIO_FLOOR_ONLY_MIN_DURATION_S:
        return None
    if integrated is None or float(integrated) <= -70.0:
        return None
    if (
        float(abruptness) >= AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU
        or float(events) >= AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION
    ):
        return None
    lra = audio_context.get("loudness_range_lu")
    crest = audio_context.get("crest_db")
    return Finding(
        tier="V10",
        rule="audio_floor_only",
        severity=ERROR,
        asset="",
        excerpt=f"{duration_s:.1f}s of level with no events: momentary loudness moves "
        f"{float(abruptness):.2f} LU/frame² (ceiling {AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU:g}) "
        f"with {float(events):.1%} of frames stepping (ceiling "
        f"{AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION:.0%}); integrated {float(integrated):.1f} LUFS"
        + (f", LRA {float(lra):.1f} LU" if lra is not None else "")
        + (f", crest {float(crest):.1f} dB" if crest is not None else ""),
        fix="lay the designed cues/bed (see gtm_core.audio_plan) — a flat floor is not a "
        "soundtrack. A deliberate drone score is the one legitimate shape here; suppress it "
        "with a reason",
    )


def _caption_geometry_missing(
    manifest: dict | None, caption_route: str | None, captions_preburned: bool | None
) -> Finding | None:
    """V11 ``caption_geometry_missing`` — the manifest says captions were burned but carries no
    boxes, so V3 and V11 both went quiet on a caption nobody measured."""
    burned = caption_route in {"local", "reap"} or bool(captions_preburned)
    if not burned or (manifest is not None and manifest.get("screens")):
        return None
    how = (
        f"caption_route={caption_route!r}"
        if caption_route in {"local", "reap"}
        else "captions_preburned=true"
    )
    return Finding(
        tier="V11",
        rule="caption_geometry_missing",
        severity=ERROR,
        asset="",
        excerpt=f"manifest declares burned captions ({how}) but carries "
        + ("no captions payload" if manifest is None else "a captions payload with no screens")
        + " — V3 (placement) and V11 (contrast) measured nothing",
        fix="carry the per-shot captions sidecar through stitch/run so the manifest holds the "
        "burned boxes; bare geometry-less manifests silence V3/V11",
    )
