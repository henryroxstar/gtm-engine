"""Canonical manifest for the `creator-brief` skill.

Prompt body: plugin/skills/creator-brief/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

This is the planning surface for the video lanes — Block C of the video brief template. It runs
between `content-plan` (what to say) and `video-script` (shot by shot how it goes), and it writes
nine pre-spend decisions as machine-readable JSON to the run folder the video lane already uses.

**The story graph (0.2.0, 2026-09-07).** `references/story-graph.md` is read only when the plan item
carries `brief.protagonist`. It gives decision 2 a beat order taken from the form rather than from
this run's outliers (recorded `source: model`, never displacing outlier mining), names the five
ingredients decision 7's payoff b-roll must contain — two of which are visible in a single frame and
so can actually be checked before spend — and gives decision 9 the flash-forward-to-the-peak
default. The fourth of the nine-item change list in the 2026-09-07 storytelling-method PRD.

**The story capture default (0.3.0, 2026-09-07).** Decision 5 is derived rather than asked on a
story item: `gtm_core.video_preflight` reports `constraints.story_capture` when the plan item is
passed with `--item-json`, and the brief takes its value, `source: derived`, and its reason
verbatim. This is a capture decision only — it resolves no engine and touches no disclosure
path. The fifth of that change list.

**The performance lexicon (0.4.0, 2026-09-07).** `references/performance-lexicon.md` is read
whenever a person will be in frame — a presenter, or a story item's actors — and unlike the story
graph it is NOT gated on `brief.protagonist`, because the defect it addresses was found on b-roll
shots in a film whose presenter shots carried no expression at all. It supplies the grammar for
decision 9's `expression` field and the per-beat emotional register behind decision 7's payoff
entry. The prior rules all attacked an ABSENT face; this one attacks an oversized one, which
arrives either as an untranslated adjective or as a stack of individually-true markers rendered
all at once. The refused vocabulary and the region ceiling live in `gtm_core.shots_lint`, not in
the reference, so prose and enforcement cannot drift apart.

It introduces NO new human gate. Gate 1 (plan) and Gate 2 (publish) are unchanged; the brief is an
artifact produced under the existing Gate 1 envelope, and there is no `brief` gate kind anywhere in
the tree. The one thing it can refuse is itself: `video-script` cross-examines the brief against the
shot list it emitted and fails closed on a contradiction (§R11).
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="creator-brief",
    capability_tier=Tier.PIPELINE,
    version="0.4.0",
    phase="6",
    description=(
        "Decide, record and cross-examine the nine pre-spend decisions for one video run, before "
        "anything is generated. Writes `brief.json` (against `schemas/creator-brief.schema.json`) "
        "plus a phone-readable markdown twin into the run folder "
        "`content/<active>/video/<YYYY-MM-DD>-<slug>/`, through the deterministic CLI "
        "`python -m gtm_core.creator_brief` — never by editing the file. The nine: the cover "
        "designed BEFORE the content; the named outlier structure (structure only, never lifted "
        "content); the fixed per-modality slot schema; the cheapest-medium answer and its reason "
        "(recorded from `format-router` and `gtm_core.video_preflight`, re-decided by neither); "
        "the capture contract (`rendered` or `live_action`); the invariant that holds across every "
        "shot plus the aspect ratio the composition requires; the planned b-roll selection list; "
        "the sampling budget as a per-shot curve with its selection criterion, declared before "
        "spend so §R2 prices the whole curve at once; and the visual hook as a shot spec rather "
        "than a vibe. Every decision carries `source` (operator / profile_default / derived / "
        "model), so a routine run is one question long and an all-default brief says out loud that "
        "nobody chose any of it. Adds NO gate: the brief is produced under Gate 1, never approved "
        "on its own. `video-script` reads it and refuses a script that contradicts it — a "
        "`live_action` brief beside an engine-bearing shot list is a refusal, not a warning."
    ),
)
