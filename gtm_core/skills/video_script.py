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

**Story checks (0.15.0, 2026-09-07).** Step 1.6 grew two checks that apply only when the script
tells a story: a *story-washing* test (the value the beats are about must be the value the payoff
asserts, at the same scale — the fingerprint test proves the ending is ours, not that it belongs
to this story) and a *bragging* test (point at the first decision, the low point and the second
decision; a script missing any of the three is an accomplishment piece, reported as such rather
than shipped as a story). Both adapted from a third-party storytelling-method course, restated;
the first two of the nine-item change list in the 2026-09-07 storytelling-method PRD.
Check 5 reads `brief.core_value` when the item carries one (0.16.0, 2026-09-07, S3) and derives the
value from the beats only when it does not. On a `brief.protagonist` item the beats are the story
graph, sized to the duration, and the `## Emotional stack` section names the payoff beat and checks
it against that reference's five ingredients rather than against the trigger list, which is
unchanged (0.17.0, 2026-09-07, S4). A story script also declares which beats carry the message
(a `message` column on the caption-budget table) and records the ratio as a `message_share` front-
block line; `content_quality script` warns above 0.10 and at zero, never blocks, and never detects
— a regex over product terms under-matches and returns a plausible wrong number (0.18.0, S6).
**The performance lexicon (0.19.0, 2026-09-07).** `creator-brief/references/performance-lexicon.md`
is loaded whenever a person will be in frame — presenter or actor — and owns the grammar every
`expression` field is written in: one region moving and how far, what holds still, where the eyes
go, and on a video beat when the change lands. The authoring rule and Step 1.6 check 4 previously
taught adjective vocabulary and keyed on a repeated adjective, which addressed only the ABSENT
face. They now also read each entry for SIZE, because an unqualified adjective renders at the
maximum and a stack of markers across one face renders as anguish rather than restraint. The
refused vocabulary and the region ceiling are enforced by `gtm_core.shots_lint`.

"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-script",
    capability_tier=Tier.PIPELINE,
    version="0.19.0",
    phase="6",
    description=(
        "Turn an approved short-form video item into a shot-by-shot script for the active "
        "company: cold open, hook line, timecoded beats (each tagged [VISUAL]/[SPOKEN]/[CAMERA], "
        "camera direction always explicit), burned-in caption text, payoff and CTA, in the "
        "profile's voice and against the brand kit (`python -m gtm_core.brandkit --profile "
        "<active> [--product <slug>]`). Loads the hook BANK (`knowledge/hooks.toml`, which owns "
        "the hook_id namespace — not hook-matrix.md, which holds 1:1 outreach openers) and "
        "aligns the cold open with the hook's angle and its opening beat for this format, "
        "refusing a format the hook does not declare and a hook that is fatigued. Runs "
        "deterministic pre-generation checks before drafting. "
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
        "wardrobe already carries, not a script-wide setting. When the script tells a story, two "
        "further checks run: story-washing (the human value the beats are about must be the value "
        "the payoff asserts, at the same scale — an ending can be ours and still belong to a "
        "different story) and bragging (point at the first decision, the low point and the second "
        "decision; a script missing any of the three is an accomplishment piece, reported as such "
        "rather than shipped as a story). "
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
        "are advisories). Spends no credits and calls no generation tool. "
        "The script is also the creative brief, because the two were never separate documents: "
        "alongside the beats it writes who the asset is for (carrying `audience-psychology.md`'s own "
        "caveat that its rows are inferred, not interviewed), the single-minded proposition, the "
        "reasons to believe, the mandatories and prohibitions, the objective restated as something "
        "observable, an `## Emotional stack` mapping each beat to the trigger it fires from "
        "`docs/virality-engineering.md` (floor: two actively combined, plus a one-line answer to the "
        "share test), and a `## Caption budget` table giving per-beat words-per-second — an aggregate "
        "`on_screen_words` hides the single screen that breaks the 4.0 w/s ceiling. Takes "
        "`visual_template` and the caption preset from `python -m gtm_core.video_preflight --json` "
        "rather than deciding them freehand, so the script and the render cannot each make a "
        "different defensible choice. Writes a static, theme-aware HTML twin beside the markdown for "
        "the operator who has to approve the film before any credit is spent: a duration-proportional "
        "timeline, one 9:16 storyboard cell per beat with the caption rendered in its true "
        "lower-third position, and the caption budget with every over-ceiling row marked. This skill "
        'should be used when the user says "write the video script", "script this reel", '
        '"turn the plan into a short-form script", "write the hook and beats", "script a '
        '90-second video", or as the script stage of the creator pack.'
    ),
)
