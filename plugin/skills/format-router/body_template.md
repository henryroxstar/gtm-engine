
# Format Router

Cross-modal format dispatcher. Given an approved hook and pillar, decide which `(format,
platform)` tuples to produce. This skill does **not** render anything and does **not** spend
credits — it emits a fan-out plan and, when video is selected, hands off to `video-router` to pick
the video lane.

> Resolve the **active profile** (the agent provides it). Do not write under `profiles/<active>/`.
> The only writable state is `content/<active>/plans/`.

## Step 0 — Read inputs

- The approved plan item or hook. Prefer the item's `hook_id`; if absent, resolve the hook from
  `profiles/<active>/knowledge/hooks.toml` via `python -m gtm_core.resolve_knowledge hooks.toml`.
- The hook row from `hooks.toml`: `angle`, `payoff_promise`, `formats`, `max_impressions`,
  `fatigue_window_days`, and the matching `opening_beat` for each declared format.
- `profiles/<active>/knowledge/content-priority.md` (via `resolve_knowledge`) — the target mix of
  text / image / video for this cycle.
- `profiles/<active>/PROFILE.md` — `monthly_tool_budget_usd` and spend so far.
- `content/<active>/ledgers/costs.jsonl` — recent spend by skill/tool.
- `plugin/skills/content-plan/references/retention-rubric.md` Part B for platform-objective fit.

Check fatigue before considering a hook:

```bash
uv run python -m gtm_core.hooks fatigued --profile <active> --content-root content/<active>
```

If the hook is listed as fatigued, stop and tell the operator it needs revival before re-use.

## Step 1 — Ask the one question (if not already decided)

If the request already names the desired formats/platforms, skip this step and route directly.
Otherwise ask exactly:

> **Given this approved hook and pillar, which formats should we produce?**

Accept explicit answers like "LinkedIn text + carousel" or "Instagram reel only" and map them to
the tuples below.

## Step 2 — Score each declared format

For every format declared in `hook.formats`, prepare the asset text from the matching
`opening_beat` (or a sensible draft for that format) and run the composite scorer:

```bash
uv run python -m gtm_core.hook_score --profile <active> \
  --hook <hook_id> --format <format> --platform <platform> \
  --text <path-to-draft-text> --retention-raw <part_a_score>
```

Record each result: `score`, `band`, `weakest_dim`, `fix`. Treat `band` values as:

| Band | Action |
|---|---|
| `high` | Select for render; preferred if budget allows. |
| `medium` | Select for render if it fits the content mix. |
| `uncertain` | Select only the cheapest format(s) as a test; avoid video unless budget is loose. |
| `low` | Do not select; note the weakest dimension for the operator. |

## Step 3 — Respect constraints and emit the fan-out plan

Map selected formats to platforms and skills:

| Format | Platform | Skill / hand-off | Cost |
|---|---|---|---|
| `linkedin-text` | `linkedin` | `content-studio` | lowest |
| `thread` | `x` | `content-studio` | lowest |
| `single` | `x` | `content-studio` | lowest |
| `carousel` | `linkedin` / `instagram` | `content-studio` → `carousel-visuals` / `carousel-pdf` | medium |
| `infographic` | `linkedin` | `content-studio` → `infographic-data` | medium |
| `infographic-handwritten` | `linkedin` | `content-studio` → `infographic-handwritten` | medium |
| `reel` / `short` | `instagram` / `linkedin` / `youtube` | `video-script` → `video-router` | highest |

Apply these rules in order:

1. **Hook formats affinity.** Never produce a format the hook does not declare.
2. **Fatigue.** A fatigued hook is excluded from auto-rotation.
3. **Content mix.** Bias toward the text/image/video ratio in `content-priority.md`; do not let a
   single hook dominate the cycle.
4. **Budget.** Video is selected only when the hook score is `high` and monthly budget remains;
   otherwise default to the cheapest high-scoring format.
5. **Platform fit.** Use Part B of the retention rubric to avoid pairing a format with a platform
   where the objective fit is poor (e.g. a dense carousel on X).

For each selected tuple, write a `ContentItem` to the plan JSON with:

```json
{
  "id": "<uuid>",
  "hook_id": "<hook_id>",
  "format": "<format>",
  "platform": "<platform>",
  "modal_family": "<text|image|video>",
  "status": "planned",
  "brief": { "angle": "...", "hook_direction": "...", "key_points": [...], "tone": "...", "avoid": [...] },
  "hook_score_report": { "score": 72, "band": "medium", "weakest_dim": "...", "fix": "..." }
}
```

`modal_family` is derived from `format` (do not guess): `text` for `linkedin-text`/`thread`/`single`;
`image` for `carousel`/`infographic`/`infographic-handwritten`; `video` for `reel`/`short`/`clip`.

When video is selected, emit the hand-off directive:

> Hand off video item(s) to `video-router` to pick the lane (scripted short-form, repurpose clips,
> restyle, or demo-capture).

## Step 4 — Report

Show the operator:

- Hook id and angle.
- Selected tuples with `format`, `platform`, `modal_family`, and `band`.
- Skipped formats with reason (fatigue, low score, budget, mix).
- Total estimated cost if all selected items render.
- The path to the updated plan JSON.

## Guardrails

- **Never guess a format** not declared in `hook.formats`.
- **Never spend credits** from this skill; it plans only.
- **Respect fatigue.** A fatigued hook is not auto-rotated.
- **Default below-threshold** to the cheapest high-scoring format, not rejection, when a viable
  cheaper format exists.
- **Hand off video** to `video-router`; do not pick the video lane yourself.
- Only write under `content/<active>/plans/`. `profiles/<active>/` is read-only.
