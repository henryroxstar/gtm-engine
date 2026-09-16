from __future__ import annotations

from .model import ERROR, WARN, Tier
from .thresholds import (
    AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU,
    AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION,
    AUDIO_FLOOR_ONLY_MIN_DURATION_S,
    MAX_DEAD_AIR_FRACTION,
    MAX_DEAD_AIR_RUN_S,
    MAX_SECONDS_WITHOUT_CHANGE,
    MAX_TRANSITIONS_PER_MINUTE,
    MAX_WORDS_PER_SEC_ASSET,
    MAX_WORDS_PER_SEC_SCREEN,
    MIN_BITRATE_1080P_BPS,
    MIN_CAPTION_CONTRAST_RATIO,
    MIN_SHOT_MOTION,
)

SHIPPED: tuple[Tier, ...] = (
    Tier(
        "V1",
        "resolution >= target ratio's frame size; fps >= 30 (warn 24-29); "
        f"bitrate >= {MIN_BITRATE_1080P_BPS / 1_000_000:g} Mbps @1080p",
        ERROR,
        evidence=(
            "2026-08-15 shipped asset measured 720x1280 (below the 1080x1920 target for 9:16)",
            "2026-08-15 shipped asset measured 24fps/1.42 Mbps (below the 30fps/6 Mbps floor)",
        ),
    ),
    Tier(
        "V2",
        "frame dims match the requested ratio's target exactly (±1px), not merely close",
        ERROR,
        evidence=(
            "identity/henry/IDENTITY.toml [render_behaviour]: soul_2 coerces a requested 4:5 "
            "render to an actual 3:4 frame",
            "2026-08-15 verification log: the same coercion is documented as needing a reframe "
            "pass in render-9x16.json's downstream consumers",
        ),
    ),
    Tier(
        "V3",
        "no caption glyph within the reserved safe-area margin on any edge",
        ERROR,
        evidence=(
            "2026-08-15 shipped asset: caption clipped at both frame edges at t≈10s "
            "(extracted frame, inspected)",
            "_make_captions_and_reframe.py:29-45 (superseded): make_caption_png applies no "
            "max-width, wrap, or shrink before drawing",
            "2026-08-28 three-questions-p1 (both masters): all 64 caption screens at a single "
            "fixed y=192-283 of 1920 (0.100-0.147) sat on the presenter's forehead and this tier "
            "passed clean — the 4:5-derived 0.22 band floor began exactly where the eyes do. "
            "Fixed by FACE_BANDS, not by a new tier.",
        ),
    ),
    Tier(
        "V4",
        "predictor input duration <= 14.9s (scoped by purpose='predictor', not by asset)",
        ERROR,
        evidence=(
            "render-9x16.json variant 1: virality_predictor returned analysis=null at "
            "duration=15.041667s",
            "render-9x16.json variant 2: identical failure at the identical duration — 4/4 "
            "observed, both variants",
        ),
    ),
    Tier(
        "V10",
        f"no dead air: silence <= {MAX_DEAD_AIR_FRACTION:.0%} of runtime and no single run "
        f">= {MAX_DEAD_AIR_RUN_S:g}s (measured signal, not stream presence); and no "
        "floor-only soundtrack — level whose momentary loudness never steps (abruptness < "
        f"{AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU:g} LU and under "
        f"{AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION:.0%} event frames) on an asset >= "
        f"{AUDIO_FLOOR_ONLY_MIN_DURATION_S:g}s",
        ERROR,
        evidence=(
            "2026-09-07 a 30.2s 9:16 film: 0% silence, -13.9 LUFS integrated, and a fully green "
            "V10 — because its entire soundtrack was a synthesized pink-noise room-tone floor "
            "laid so the dead-air rules would pass. None of the shot list's designed cues or "
            "bed existed. Momentary-loudness abruptness measured 0.57 LU against 2.3 on two "
            "voice-led films; event frames 3.1% against 36%. LRA and crest factor, the summary "
            "numbers first proposed, did not separate them (4.7 LU / 13.7 dB vs 2.7-4.8 LU / "
            "14.5-14.6 dB) because loudnorm flattens both — the rule reads the 100ms series "
            "instead.",
            "2026-08-28 three-questions-p1-9x16-final.mp4: 39.6s of 100.7s (39%) below -50 dBFS, "
            "in runs of 10.4s / 25.1s / 4.1s. Ten of eighteen shots were concatenated with no "
            "audio stream at all, so the entire dramatised call and the whole social cut played "
            "silent. Probe.has_audio was True throughout — the container had an AAC track "
            "carrying nothing, which is exactly why this tier measures the signal.",
            "2026-08-28 three-questions-p1-16x9-final.mp4: the same three runs on the landscape "
            "master, independently probed and independently linted — 39% of 100.8s.",
        ),
    ),
    Tier(
        "V11",
        f"burned caption type clears {MIN_CAPTION_CONTRAST_RATIO:g}:1 against the luminance "
        "actually behind it (WCAG AA); and a manifest that declares burned captions "
        "(caption_route local/reap, or captions_preburned) must carry their geometry, or the "
        "asset is refused rather than passed unmeasured",
        ERROR,
        evidence=(
            "2026-09-07 a 30.2s 9:16 film shipped with unreadable captions (~1.10:1, white over "
            "a bright plate) past a clean lint: the captions had been burned per shot before "
            "the stitch, nothing carried the per-shot sidecars forward, and the finish manifest "
            "passed via --manifest read `captions: null` — so V3 and V11 both skipped, which "
            "reads identical to both passing. An unreadable gate is a missing gate; a manifest "
            "that says captions were burned and carries no boxes is now an ERROR, not a skip.",
            "2026-08-28 three-questions-p1-9x16-final.mp4: unplated white glyphs over the bright "
            "wooden-desk opening plate measured 3.67:1 at t=1.0s — the film's first frame, below "
            "AA. The same caption style cleared on every darker shot (6.3-8.1:1 over the "
            "presenter, 12-20:1 over the navy graphics), which is exactly why an unplated style "
            "is a latent defect rather than an obvious one: it passes until the backdrop is "
            "bright, and nobody re-checks per shot.",
            "2026-08-28 three-questions-p1-16x9-final.mp4: the same opening plate measured "
            "independently at 3.5:1 on the landscape master.",
        ),
    ),
)

#: WARN-only candidates. Promoted to SHIPPED/ERROR only after a Tier() with >=2 evidence entries
#: can be constructed for it — see the module docstring.
CANDIDATES: tuple[Tier, ...] = (
    Tier(
        "V5",
        "audio ducking applied when a music bed is present alongside dialogue",
        WARN,
        evidence=(),
    ),
    Tier(
        "V6",
        "scene-change cadence is neither too sparse nor too frantic for the asset duration",
        WARN,
        evidence=(),
    ),
    Tier(
        "V7",
        f"caption reading load <= {MAX_WORDS_PER_SEC_SCREEN:g} words/s on any screen and "
        f"<= {MAX_WORDS_PER_SEC_ASSET:g} words/s across the asset",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): 150 on-screen words across "
            "15.55s = 9.6 w/s, with individual screens at 14.5 and 17.3 w/s. Reading it at a "
            "comfortable rate needs ~60s; the asset runs 15.55s. Operator reported being unable "
            "to read it and feeling overloaded — the first defect they named.",
        ),
    ),
    Tier(
        "V8",
        "burned caption text is drawn from the spoken line, not a second independent text stream",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): captions carried filing citations "
            "disjoint from the voice-over, so the viewer parsed two unrelated text streams at "
            "once. This was introduced deliberately, to 'save VO words', and reported as a virtue.",
        ),
    ),
    Tier(
        "V9",
        f"every shot carries visible motion (mean inter-frame delta >= {MIN_SHOT_MOTION:g})",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): five card shots animated with a "
            "1.08x zoom spread over ~3s (~0.03%/frame) read as fully static; 7.95s of 15.55s "
            "(51%) was motionless type on a flat ground. V6 passed it because V6 counts CUTS, "
            "of which there were five — within-shot stillness is a different defect.",
        ),
    ),
    Tier(
        "V12",
        f"transitions stay under {MAX_TRANSITIONS_PER_MINUTE:g}/min (multi-frame blends, "
        "counted separately from cuts)",
        WARN,
        evidence=(
            "A dissolve-heavy edit and a cut-heavy one produce the SAME scene-change count and "
            "feel nothing alike, so V6 cannot see this at all: it counts boundaries without "
            "asking what shape they are. Past this density the transitions are what the viewer "
            "is watching, rather than the thing being cut between. The weakest of the three "
            "cadence numbers — WARN, and recorded as advisory, because it rests on the smallest "
            "sample of the three.",
        ),
    ),
    Tier(
        "V13",
        f"no single stretch over {MAX_SECONDS_WITHOUT_CHANGE:g}s with nothing changing on screen",
        WARN,
        evidence=(
            "V6 measures the AVERAGE interval, and an average is exactly what hides one long "
            "hold: a 40s film with 20 changes averages 2s and still loses the viewer if eleven "
            "of those seconds are a single motionless block. Head and tail count, because a "
            "static opening is the most expensive place of all to lose someone. Distinct from "
            "V9, which asks whether a shot has internal motion — this asks how long the picture "
            "goes without changing at all, which a sequence of static shots fails and a single "
            "moving shot does not.",
        ),
    ),
)

_TIERS_BY_ID: dict[str, Tier] = {t.id: t for t in SHIPPED + CANDIDATES}
