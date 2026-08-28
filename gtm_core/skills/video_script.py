"""Canonical manifest for the `video-script` skill.

Prompt body: plugin/skills/video-script/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

This is the **credit-protecting gate** of the creator pack. §5.3 originally proposed
cross-checking Higgsfield `virality_predictor` before spending render credits; that tool
requires a rendered video as input (`medias[].role = "video"`), so it cannot run
pre-render. The free, pre-render gate is therefore the retention rubric's Part A scored
by the brain against the script — and `video-score` runs the paid cross-check afterwards,
on assets that exist, as recorded advisory (never a spend gate itself — see
`video-render`'s Phase 17 correction).

**Cold-start override (Phase 17, 2026-08-17).** The composite `hook_score` band is
`"uncertain"` whenever a hook×format has no outcome-history prior — deliberately
fail-closed, and that property is never relaxed. But a brand-new hook can *never* earn a
prior on its own merits: the 500 impressions `_compute_prior` requires can only accrue by
publishing, and this gate was blocking every publish. Distinguishing the two failure
shapes (`ScoreResult.prior_has_data`) turns the deadlock into an audited operator
decision: genuine low quality still blocks after one rewrite; a cold-start `uncertain`
surfaces to the operator and, on explicit yes, records a reasoned
`hook_score_override` outcomes row (excluded from every future prior computation) rather
than blocking forever.

**Long-form multi-shot scripts.** A
single Higgsfield call is capped at 5-15s, so a script longer than that is written as a
sequence of shots, each carrying an explicit `[CAMERA]` beat tag (camera type + motion,
never implicit) alongside the existing `[VISUAL]`/`[SPOKEN]` tags. A script requiring more
than one clip also writes a `<slug>.shots.json` shot list — the switch `video-render` keys
off to render shot-by-shot instead of one clip. Still spends no credits and calls no
generation tool; the shot list is free planning output, same as the prose script.

**Engineered-prompt layer (Phase 18).**
The gap assessment found the brand kit's `[imagery].style`/`[imagery].negative` were declared
as prompt text and read by no video skill, and that prompt-craft rules lived only in prose. Now:
`style_scaffold.look` is seeded from `[imagery].style` and a new `style_scaffold.negative`
(plain nouns, never "no X" phrasing) from `[imagery].negative`; the shot schema gained optional
per-shot `lighting`/`lens`/`wardrobe`/`stability`/`sfx` fields (wardrobe became prompt text
after a 22.5-credit discard traced to an unwritten one); and hand-off requires
`python -m gtm_core.shots_lint` to exit 0 — the deterministic encoding of the two rules every
current video model's own guide agrees on (one camera move per shot; positive phrasing only),
plus vocabulary and duration advisories. The linter is the last free gate before render spends.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-script",
    capability_tier=Tier.PIPELINE,
    version="0.11.0",
    phase="6",
    description=(
        "Turn an approved short-form video item into a shot-by-shot script for the active "
        "company: cold open, hook line, timecoded beats (each tagged [VISUAL]/[SPOKEN]/[CAMERA], "
        "camera direction always explicit), burned-in caption text, payoff and CTA, in the "
        "profile's voice and against the brand kit (`python -m gtm_core.brandkit --profile "
        "<active> [--product <slug>]`). Loads the hook matrix and aligns the cold open with the "
        "item's hook_id when set. Runs deterministic pre-generation checks before drafting. "
        "Opens the item's `research_ref` rather than writing from the `brief.angle` paraphrase, "
        "then verifies every external claim that will reach a viewer as VO or caption against a "
        "primary (or a named secondary quoting it) — an internal harvest file or intelligence "
        "brief is a pointer, never evidence — and records claim/source/verbatim-quote/verdict in "
        "a `## Verification log` section plus a mandatory `claims_verified: n/n` front-block "
        "field, cutting or explicitly marking anything that will not verify rather than softening "
        "it into vagueness. Before saving, runs a comprehension-and-fingerprint self-check the "
        "retention rubric and claim verification don't cover: reconstructs the story from the "
        "burned-in captions alone to catch a caption that dropped its grounding noun, caps "
        "unglossed technical terms at one per script (moving the rest on-screen-only, borrowed "
        "from `linkedin-reply`'s 'one technical term unless the ICP demonstrably speaks it'), and "
        "checks the payoff against `content-priority.md`'s wedge so the ending carries this "
        "profile's own angle, veiled, rather than a generic point any competitor could post — "
        "'no product pitch' constrains what gets named, not how relevant the point is; and lists "
        "every presenter shot's `expression` field to catch a missing or repeated adjective "
        "(e.g. 'calm' on every beat), since a script-wide tone word from `voice.md` copied "
        "unchanged onto every shot is what renders a presenter as flat or disengaged regardless "
        "of how correct the words are — expression is per-beat prompt text, the same lesson "
        "wardrobe already carries, not a script-wide setting. "
        "Scores the draft against Part A of `retention-rubric.md` (retention craft), feeds that "
        "score into `python -m gtm_core.hook_score` to blend in hook-bank prior and pattern "
        "compliance, and **refuses to hand off to render on genuine low quality, or on a "
        "cold-start `uncertain` without an audited `--override-reason`** — this is the free gate "
        "that protects the render budget, because the paid "
        "it. Writes `content/<active>/scripts/<date>-<slug>.md` carrying the source item id, its "
        "hook_id, and its pillar/journey-stage/goal/format tags so performance can be attributed "
        "later. For a request longer than one Higgsfield clip (up to ~5 minutes, operator-stated), "
        "also writes a `<slug>.shots.json` shot list — a shared style scaffold (look + negative "
        "clause, seeded from the brand kit's [imagery] fields) plus one entry per "
        "shot (camera, motion, visual, spoken line, optional lighting/lens/wardrobe/stability/sfx "
        "prompt-layer fields, and an optional role: presenter/broll/screen, "
        "default presenter), plus the source item's hook_id at the top level — which is what "
        "`video-render` uses to render and stitch a long-form video shot by shot, skipping the "
        "identity anchor and lip-sync check entirely for a broll/screen shot. Validated against "
        "schemas/shots.schema.json and gated on `python -m gtm_core.shots_lint` exit 0 (stacked "
        "camera moves and negation phrasing are errors; unknown camera terms and duration drift "
        "are advisories). Spends no credits and calls no generation tool. This skill "
        'should be used when the user says "write the video script", "script this reel", '
        '"turn the plan into a short-form script", "write the hook and beats", "script a '
        '90-second video", or as the script stage of the creator pack.'
    ),
)
