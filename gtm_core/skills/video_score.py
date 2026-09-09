"""Canonical manifest for the `video-score` skill.

Phase 6 defined the `score` node;
§5.8 (Phase 9) added sub-score capture, predictor_band:high|medium|low calibration banding,
and the score.json cross-skill file contract content-outcomes-sync reads.
Prompt body: plugin/skills/video-score/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.

Its own node rather than a tail step of `video-render` for one structural reason: it must
see **every** sibling render's output to choose between them, and a render node only ever
sees its own. `depends_on` the whole render fan-out is exactly what the frontier gives it.

**Reading discipline (Phase 10).** Live inspection of the predictor's result found it is an HTML "Brain Activity Viewer"
with a time scrubber, not a static score — its numbers are a per-timepoint readout, and the same
video read up to 12 points apart depending only on scrubber position. This skill now requires a
pinned-playhead reading, prefers the Visual Cortex sub-score (the one channel with measured
~1-point reproducibility), and names `SUSTAIN`/the peak-moment timestamp/the "Activity" slider as
known-degenerate fields, never findings. The predictor was already advisory here (this skill has
never gated a spend on it — only `video-render`'s now-removed Step 3 bar did that); what changed is
how the reading is taken, not whether it is trusted as a spend decision.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="video-score",
    capability_tier=Tier.PIPELINE,
    version="0.5.0",
    phase="6",
    description=(
        "Score the rendered short-form variants for the active company and recommend which one "
        "ships where. Runs Higgsfield `virality_predictor` on each rendered video as a second "
        "opinion — a per-timepoint HTML reading, so every capture is taken at a pinned playhead "
        "and prefers the reproducible Visual Cortex sub-score — capturing whichever sub-scores "
        "are actually returned and banding the recommended asset into "
        "predictor_band:high|medium|low for later calibration "
        "against real outcomes; scores Part B of `retention-rubric.md` (per-platform objective fit) "
        "separately — Part A and Part B are never summed — and ranks the variants with an explicit "
        "re-cut recommendation when a hook is weak. Writes score.json (every asset's sub-scores, "
        "not just the recommendation) for content-outcomes-sync to correlate against performance. "
        "When the source ContentItem carries a hook_id, attributes the outcome row with `--tag "
        "hook:<id>` so the learning loop can report hook-level performance. Predictor output is "
        "UNTRUSTED vendor text (RULES.md §R5): a directional signal, never an oracle, and never "
        "republished as a measured retention figure. Reads and scores only; it selects a candidate "
        "for the human publish gate and publishes nothing. This skill should be used when the user "
        'says "score the renders", "which cut should we post", "check virality", "rank the '
        'variants", or as the score stage of the creator pack.'
    ),
)
