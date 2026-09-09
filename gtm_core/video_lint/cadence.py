"""Rhythm tiers — how often the picture changes, and whether the bed lets the voice through.

Split out of :mod:`gtm_core.video_lint.evaluate` by C6, which was at 482 of its 500-line ceiling.
Moving V5 and V6 here leaves that file smaller than it was found, and gives the two new cadence
tiers somewhere to live that is about rhythm rather than about everything.

**Constants are read by attribute** (``thresholds.MAX_SECONDS_WITHOUT_CHANGE``), never
from-imported. That is deliberate and it is what makes these rules testable: a from-import binds a
copy at module load, so a test that monkeypatches the threshold module changes nothing and reads
as passing while measuring nothing at all.

Three numbers describe cadence and they are not interchangeable:

* **V6** is the AVERAGE interval, and the absence of any change at all.
* **V13** is the single LONGEST hold. A film can average a healthy interval and still park for
  eleven seconds in the middle — the average is exactly what hides that, which is why this is a
  separate tier rather than a tighter V6.
* **V12** is transition DENSITY, counting multi-frame blends rather than cuts. A dissolve-heavy
  edit and a cut-heavy one have the same scene-change count and completely different rhythms.

All three are WARN. They describe films that shipped acceptably; error-level would be
over-claiming on evidence this thin, and V12's number rests on the smallest sample of the three.
"""

from __future__ import annotations

from . import thresholds
from .model import WARN, Finding
from .probe import Probe

__all__ = ["bed_findings", "cadence_findings"]


def bed_findings(audio_context: dict | None) -> list[Finding]:
    """V5 — a music bed that competes with the voice it is supposed to sit under."""
    findings: list[Finding] = []
    if audio_context is None:
        return findings

    has_music = bool(audio_context.get("has_music_bed"))
    has_voice = bool(audio_context.get("has_voice"))
    ducking_applied = bool(audio_context.get("ducking_applied"))
    music_lufs = audio_context.get("music_lufs")
    voice_lufs = audio_context.get("voice_lufs")

    if has_music and has_voice and not ducking_applied:
        findings.append(
            Finding(
                tier="V5",
                rule="music_bed_without_ducking",
                severity=WARN,
                asset="",
                excerpt="music bed present alongside dialogue without sidechain ducking",
                fix="run gtm_core.video_finish.duck_music_bed() before final mux, or suppress "
                "with a reason if the mix was controlled another way",
            )
        )
    elif (
        has_music
        and has_voice
        and ducking_applied
        and music_lufs is not None
        and voice_lufs is not None
        and music_lufs > voice_lufs - thresholds.V5_BED_SEPARATION_DB
    ):
        findings.append(
            Finding(
                tier="V5",
                rule="music_bed_too_loud",
                severity=WARN,
                asset="",
                excerpt=f"music bed {music_lufs:.1f} LUFS within "
                f"{thresholds.V5_BED_SEPARATION_DB:.0f} dB of voice {voice_lufs:.1f} LUFS",
                fix="increase duck ratio or lower the music bed level so dialogue sits clearly "
                "on top",
            )
        )
    return findings


def cadence_findings(
    p: Probe,
    *,
    scene_changes: list[float] | None,
    transitions: list[dict] | None = None,
    motion_stats: list[dict] | None = None,
) -> list[Finding]:
    """V6 (average cadence), V13 (longest STATIC hold) and V12 (transition density)."""
    findings: list[Finding] = []
    if scene_changes is None:
        return findings  # never measured; a clean verdict we could not earn is worse than none

    changes = sorted(scene_changes)
    num_changes = len(changes)

    # ── V6 — the average, and the total absence ───────────────────────────────────────────────
    nothing_changes_at_all = (
        p.duration_s > thresholds.V6_NO_CHANGE_MIN_DURATION_S and num_changes == 0
    )
    if nothing_changes_at_all:
        findings.append(
            Finding(
                tier="V6",
                rule="no_scene_changes",
                severity=WARN,
                asset="",
                excerpt=f"no scene changes in a {p.duration_s:.1f}s asset",
                fix="add at least one visual change (cut, camera move, or graphic) to hold "
                "attention",
            )
        )
    elif num_changes >= 2:
        avg_interval = (changes[-1] - changes[0]) / (num_changes - 1)
        if avg_interval < thresholds.V6_MIN_AVG_CUT_INTERVAL_S:
            findings.append(
                Finding(
                    tier="V6",
                    rule="scene_changes_too_frequent",
                    severity=WARN,
                    asset="",
                    excerpt=f"scene changes average {avg_interval:.2f}s apart",
                    fix="lengthen shots or remove unnecessary cuts to reduce visual churn",
                )
            )

    if (
        p.duration_s > thresholds.V6_SPARSE_WINDOW_S
        and num_changes < p.duration_s / thresholds.V6_SPARSE_WINDOW_S
    ):
        findings.append(
            Finding(
                tier="V6",
                rule="scene_changes_too_sparse",
                severity=WARN,
                asset="",
                excerpt=f"only {num_changes} scene change(s) across {p.duration_s:.1f}s "
                f"(fewer than one per {thresholds.V6_SPARSE_WINDOW_S:.0f}s)",
                fix="introduce additional visual changes or shorten the asset to keep attention",
            )
        )

    # ── V13 — the single longest STATIC hold, head and tail included ──────────────────────────
    #
    # An average cannot see this. A 40s film with 20 changes averages 2s and still fails a viewer
    # if 11 of those seconds are one motionless block, and a static OPENING is the most expensive
    # place to lose someone — which is why the head gap counts even though no change has happened
    # yet to bound it.
    #
    # "Nothing changing on screen" means NO CUT *and* NO MOTION. The first version measured cuts
    # alone, so an 8s single shot with a continuous push-in fired as "8.0s with nothing changing"
    # — a mislabel on the commonest single-shot input, and a direct contradiction of this tier's
    # own registry text. Motion comes from `motion_stats` (V9's per-shot mean inter-frame delta):
    # a gap is a hold only if every shot overlapping it sits under the V9 stillness floor.
    # Unmeasured — no motion_stats at all, or a gap no stat covers — means SKIP, never "fine": a
    # verdict this tier could not earn is worse than none.
    #
    # Suppressed when V6 has already said "nothing changes at all": with zero changes there is
    # exactly one hold, the whole film, and V6 phrases that better. Keyed on V6 actually FIRING,
    # not on zero changes — a 10s film with no changes is under V6's runtime floor, and V13 is
    # then the only tier that will say anything about it.
    if motion_stats is not None and not nothing_changes_at_all:
        boundaries = [0.0, *changes, p.duration_s]
        floor = thresholds.MIN_SHOT_MOTION
        holds: list[tuple[float, float]] = []
        # strict=False on purpose: boundaries[1:] is one shorter by construction.
        for a, b in zip(boundaries, boundaries[1:], strict=False):
            if b <= a:
                continue
            covering = [
                st
                for st in motion_stats
                if isinstance(st, dict)
                and float(st.get("start", 0.0)) < b
                and float(st.get("end", 0.0)) > a
            ]
            if not covering:
                continue  # an unmeasured stretch cannot be called a hold
            if all(float(st.get("motion", 0.0)) < floor for st in covering):
                holds.append((b - a, a))
        if holds:
            longest, at = max(holds)
            if longest > thresholds.MAX_SECONDS_WITHOUT_CHANGE:
                findings.append(
                    Finding(
                        tier="V13",
                        rule="longest_hold_exceeds_cadence",
                        severity=WARN,
                        asset="",
                        excerpt=f"{longest:.1f}s with nothing changing on screen — no cut and no "
                        f"motion — starting at {at:.1f}s (ceiling "
                        f"{thresholds.MAX_SECONDS_WITHOUT_CHANGE:.0f}s)",
                        fix="cut into that stretch, or give it a move — a push, a graphic, a "
                        "change of framing. The average cadence can look healthy while one block "
                        "like this loses the viewer",
                    )
                )

    # ── V12 — transition density ──────────────────────────────────────────────────────────────
    if transitions is not None and p.duration_s > 0:
        per_minute = len(transitions) * 60.0 / p.duration_s
        if per_minute > thresholds.MAX_TRANSITIONS_PER_MINUTE:
            findings.append(
                Finding(
                    tier="V12",
                    rule="transition_density_high",
                    severity=WARN,
                    asset="",
                    excerpt=f"{len(transitions)} transition(s) in {p.duration_s:.1f}s "
                    f"({per_minute:.1f}/min, ceiling "
                    f"{thresholds.MAX_TRANSITIONS_PER_MINUTE:.0f}/min)",
                    fix="replace most of them with straight cuts — a blend draws attention to "
                    "the edit rather than to what it is cutting between, and at this density "
                    "the transitions are the thing being watched",
                )
            )

    return findings
