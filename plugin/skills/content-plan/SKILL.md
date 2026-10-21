---
name: content-plan
description: >-
  Propose the week's multi-platform content plan from radar digests, platform playbooks, and
  hook banks behind Gate 1 approval. Trigger when the user says "plan this week's content",
  "make a content plan", "what should we post this week", "draft the plan", or after a radar
  run.
metadata:
  version: "0.7.0"
  phase: "2"
  capability_tier: core
---

# Content Plan (Gate 1)

Propose a weekly content plan for the active company, then stop at **Gate 1** for the user's
approval in Telegram. Nothing is finalized until the user approves — this skill writes only a
*draft* and presents it; the cockpit's Approve/Edit/Reject buttons drive what happens next.

> Resolve the **active profile** (the agent provides it; everything company-specific loads from
> `profiles/<active>/`). The only writable state is `content/<active>/`.

## Step 0 — Read inputs

- `profiles/<active>/PROFILE.md` — `content_pillars`, `brand_name`, `social_handle`, `language`,
  `wedge`, and the budget fields.
- The last ~3 radar digests + clusters: `content/<active>/radar/*-clusters.json` (most recent
  first). These carry the scored `StoryCluster[]` — your ideas must reference real `cluster.id`s.
- The platform playbooks: `docs/linkedin-optimization.md`, `docs/x-optimization.md`,
  `docs/instagram-optimization.md` (Phase 1 ships LinkedIn only — see Guardrails).
- `content/<active>/history.jsonl` — what's already been published; don't repeat a recent angle.
- **`profiles/<active>/knowledge/hooks.toml` — the hook bank, and the only source of a
  `hook_id`.** List it rather than reading the file:
  ```bash
  uv run python -m gtm_core.hooks list --profile <active>
  ```
  Each row is `id · status · formats · angle`. When a planned item matches a hook's angle, copy the
  hook's `id` into the item's `hook_id`; this becomes the attribution key in `outcomes.jsonl`
  (`hook:<id>`) and the key every downstream gate joins on.

  **Not `hook-matrix.md`.** That file holds **1:1 outreach openers** (a named account × a why-now
  signal) and in some tenants is an id-less persona × signal grid with no `hook_id` in it at all —
  so on those tenants this step silently assigned nothing, every item shipped unattributed, and the
  composite `hook_score` gate downstream could never run. Reading the matrix for *angle inspiration*
  is fine; taking an id from it is not.

  **A fatigued hook is not assignable.** List them before you assign anything:
  ```bash
  uv run python -m gtm_core.hooks fatigued --profile <active> --content-root content/<active>
  ```
  This gate lives here because this is where a `hook_id` is *chosen*. `format-router` runs it on the
  cross-modal lane and `video-script` runs it on the three script-bearing video lanes, but
  `demo-clips`, `repurpose-clips` and `restyle-shorts` reach neither — on those graphs this step is
  the only fatigue check between the bank and a published asset. Assign a fatigued hook there and it
  ships: nothing downstream will stop it. Pick a different hook, or revive this one deliberately
  (`uv run python -m gtm_core.hooks revive`), which is an operator decision with recorded evidence,
  not a default.
- `profiles/<active>/knowledge/audience-psychology.md` (via `resolve_knowledge`, optional) — the
  per-persona psychological layer + founder-fit tags that shape `brief.angle` below.
- `profiles/<active>/knowledge/social-tuning.md` (via `resolve_knowledge`, optional) — the
  company's per-platform tuning (posting clocks, lead formats, voices bench, the "never" list).
  The playbooks above hold the generic method; this file holds what's specific to the company.
- `profiles/<active>/knowledge/content-priority.md` (via `resolve_knowledge`, optional) — the
  pillars and theme-radar tiers the shareability rubric scores against, **plus** the "Planning
  axes" section (goal mix, journey mix, cadence) if the profile declares one. When that section is
  absent, plan exactly as before: pillar spread only, and the default 3–5 items.
- `plugin/skills/content-plan/references/retention-rubric.md` — read this **only** when an item is
  a candidate for video (`reel`, or any motion treatment). It decides whether an idea survives as
  short-form video and what the opening must do; a high-shareability idea that scores badly there
  should ship as a carousel or text post rather than as a worse video.
- `plugin/skills/content-plan/references/outlier-mining.md` — read this when an item is a
  candidate for video and you need its SHAPE, not just its verdict. The rubrics above judge
  whether an idea is worth making; this one supplies the structure it gets poured into, derived
  from pieces that beat their own channel's baseline. It produces exactly one **named** structure
  ("objection first, then the concession, then the number that settles it"), which is what the
  brief records as `outlier_structure` and what `video-script` is then held to. Two things it is
  strict about, both easy to get wrong: outliers are selected by **ratio to their own baseline**,
  never by absolute reach — sorting by views measures audience size, which you already knew and
  cannot copy — and the STRUCTURE is lifted while the CONTENT never is. A structure you can only
  gesture at is a preference wearing a rubric's clothes; if it cannot be named in a sentence, it
  has not been extracted yet.
- `docs/direct-response-patterns.md` — the 5 B2B Direct-Response Desire Frameworks and dual-action
  platform bridge rules. Read whenever planning items with `goal: "conversion"` (lead magnets,
  calculators, prompt packs, teardowns).

If there are no radar clusters yet, tell the user to run `content-radar` first — **unless the node
prompt for this run says the plan is sourced from something else** (e.g. the creator pack's restyle
lane, which plans from footage the operator brings rather than a radar digest and has no `radar`
node upstream at all). Follow the node prompt's stated source in that case; radar clusters are the
default source, not the only one.

## Step 1 — Propose the plan

Pick a **weekly theme** that ties the top-scoring clusters to the active company's `wedge`, then
propose the week's content ideas. **How many:** if `content-priority.md` declares a cadence, use the
sum of its per-platform posts/week; otherwise **3–5**. The Phase-1 LinkedIn-only guardrail below
still caps this — a cadence naming other platforms does not override a guardrail, so drop those
items and say so at the gate rather than silently planning something that cannot ship.

Each idea is a `ContentItem` (schema: `schemas/content-item.schema.json`) — required fields:

- `id` — stable, e.g. `ci-<YYYYWW>-01`
- `pillar` — from the cluster / profile pillars
- `journey_stage` (optional) — `awareness` | `consideration` | `conversion`. Where the reader is.
  Set it only when the profile declares a journey mix; omit otherwise.
- `goal` (optional) — `reach` | `engagement` | `conversion`. What this item optimizes for. **Not the
  same axis as `journey_stage`** — a conversion-stage reader can be served a reach-goal item. Set it
  only when the profile declares a goal mix.

  **Information pushes out emotion, and that decides the shape.** To be *informed*, an audience must
  already have standing with you or an interest in the topic; where it has neither, emotion is the
  only door. So a reach-goal item aimed at a cold, awareness-stage persona is **not** an explainer —
  it is where the story spine below earns its place. A conversion-stage item for a reader who already
  knows you is the opposite case, and an explainer is right there.
- `story_id` — the `StoryCluster.id` it draws on
- `platform` — `linkedin` | `x` | `instagram`. One item = one platform; if a story should run on
  more than one surface, emit one item per platform (each its own `id`, all sharing the same
  `story_id` and research). Choose formats native to the platform:
  - `linkedin` → `carousel | infographic | infographic-handwritten | text`
  - `x` → `thread | single`
  - `instagram` → `reel | carousel`
- `format` — native to the chosen platform (see above). For LinkedIn, choose based on story type:
  - `infographic` — data-dense stories (stats, survey results, risk rankings); stops the scroll
  - `infographic-handwritten` — frameworks, formulas, or "how to think about X" stories; feels personal
  - `carousel` — multi-step arguments or listicles that need slide-by-slide development
  - `text` — opinion, narrative, or hot-take posts where the prose is the product
- `status` — `planned`
- `brief` — pre-generation steering (operator can edit at Gate 1):
  - `angle` — the specific take or argument (e.g. "why enterprises stall on AI agents — it's a trust gap, not a model problem"). When `audience-psychology.md` covers the target persona, pick an angle the persona *feels* (its emotional stakes / believed-but-never-said) **and** that passes the persona's **founder-fit** filter — never a **do-not-drive** angle, and honour any **partial** constraint.
  - `hook_direction` — how to open (e.g. "stat-first → reframe as a solvable infra problem"); archetypes in `docs/hook-craft.md`. On conversion items (`goal: "conversion"`), name the direct-response pattern from `docs/direct-response-patterns.md` (e.g. `dr-symptom-root-cause`, `dr-earned-authority`, `dr-gap-roadblock`, `dr-empirical-test`, `dr-industry-benchmark`).
  - `trigger_stack` — the 2–3 emotional triggers this item should stack (from `docs/virality-engineering.md`, e.g. "curiosity + productive discomfort"), so studio drafts for a felt experience, not just an informative one
  - `key_points` — 3–5 bullets the asset must land. On conversion items, explicitly name the giveaway or lead-magnet asset and its format (template, sheet, repo, PDF brief).
  - `tone` — voice note specific to this item (e.g. "strategic informality, peer not vendor")
  - `avoid` — phrases, claims, or framings to exclude
  - `audience` (optional) — who this piece is for: a segment name from
    `profiles/<active>/knowledge/icp-personas.md` (e.g. "CISO / security lead") or a short
    free-form description. Steers studio drafting; the operator can set or change it at Gate 1.
  - `core_value`, `opposite`, `protagonist` (optional, all three or none) — the story spine, when
    this item is meant to carry one. Derive them in order rather than inventing them: the **pillar**
    is a corporate theme, so name the **human value** it stands for (`core_value`, one word —
    innovation stands for perseverance); name that value's **opposite** (`opposite` — perseverance's
    opposite is giving up); then name **who the journey from opposite to value costs most**
    (`protagonist` — someone expected to embody the value and privately living its opposite).
    `opposite` is not yours to invent: it is the persona's **believed-but-never-said** line in
    `profiles/<active>/knowledge/audience-psychology.md`, read per persona — a security lead who
    privately believes "I cannot attribute an agent's action" is a protagonist expected to embody
    control and living its absence. The persona block is the protagonist template. `core_value` must
    be one the company can **honestly claim**; that judgement is the operator's edit at Gate 1.
    Fill all three or leave all three unset — a `protagonist` with no value behind it steers nothing,
    and its presence is what marks the item as story-format for every stage downstream. **Leave unset
    on conversion items** (`goal: "conversion"`), which bypass the story graph and use the 4-shot DR sequence.
- optional: `slot` (e.g. "Mon AM"), `locale` (default the profile `language`)
- optional: `hook_id` — a stable id from **`hooks.toml`** (see Step 0). Set it when the item's
  angle matches a hook; leave it absent only when no hook fits. This is the attribution key used
  later in `outcomes.jsonl` (`hook:<id>`).

  **The hook must declare this item's `format`.** `formats` in the bank is a real constraint, not a
  label: `hook_score` refuses a pair it does not cover, and `content_quality script` blocks a video
  script on it. Pair the two fields deliberately — if the angle you want is on a hook that does not
  declare the format, either pick a different hook, change the item's format, or declare the format
  on the hook **with an opening beat for it** (a declared format with no beat cannot be scheduled).
  Do not resolve the mismatch by dropping the `hook_id`: that trades a fixable gap for an item that
  can never earn a prior. And do not invent an id — a `hook_id` that is not in the bank fails the
  gate exactly like a typo, because that is what it is.

> **Localized variants (two-clock rule).** `locale` defaults to the profile's primary `language`. To
> target a second market (e.g. an APAC variant alongside the US one), emit a **separate** item with a
> non-primary `locale` (a BCP-47 tag like `en-IN` or `zh-CN`) — content-studio will then produce a
> genuinely re-framed variant for that market, not a translation. The operator can request this when
> steering at Gate 1 (e.g. "also do an APAC variant of #2"). Keep it deliberate — only add localized
> items the operator asks for or the plan clearly warrants; don't fan out every item by default.

Bias toward the highest-scoring, on-pillar clusters; vary the format; keep it realistically small —
a cadence the operator cannot actually review at the gates is a backlog, not a plan.

**Enforce the declared mixes the same way you already enforce pillar spread.** When
`content-priority.md` declares a goal and/or journey mix, check the *set* against it before
presenting, and rebalance by swapping in a lower-scoring candidate that fills the gap. The quota is
the point: reach items are *supposed* to convert poorly and conversion items are *supposed* to
underperform on reach, so ranking on score alone will collapse the week onto one goal. Treat the
shares as targets for the set, not a rule per item — and when the set cannot hit them (too few
candidates for a stage), say which quota you missed in the gate message instead of silently
ignoring it.

## Step 2 — Write the DRAFT (do NOT finalize)

Write the proposed plan to the pending draft path — NOT the final plan:

```
content/<active>/plans/.pending/<YYYY-WW>.draft.json
```

as a JSON array of `ContentItem` objects (status `planned`), including all `brief` fields. Use the
**Write tool** to write this file — it creates the `.pending/` parent directory automatically, so
you do **not** need a separate step to make the directory. Do **not** create the directory or write
the file with `python -c …`, `python - <<…`, or a chained shell command: the least-privilege policy
denies raw code-exec, and that is what blocks the draft (a prior run failed exactly here). If you
genuinely need a shell step, only a bare `mkdir -p content/<active>/plans/.pending` is permitted —
but the Write tool alone is enough. Do **not** write `content/<active>/plans/<YYYY-WW>-plan.md`
yet — that happens only on approval.

You **must** actually persist this file to disk before continuing — a draft that exists only in
your reply is a failure, not a saved plan. There is **no** lock, harness restriction, or permission
rule preventing this write: the directory is writable and the Write tool is allowed. If a write
appears to fail, simply call the Write tool again — do **not** invent a "lock"/"permission" reason
and skip the write, and do **not** present the gate with an unsaved draft.

## Step 2.5 — Run the pre-generation quality gate

After the draft is on disk, run the deterministic pre-check for every item and surface any warnings
in the Gate 1 message (do **not** block the operator from approving):

```bash
uv run python -m gtm_core.content_quality pre --profile <active> --item <item-id>
```

A warning such as a missing `hook_id`, an unknown platform playbook, or an unmatched audience persona
is advisory — show it under the item so the operator can steer. Treat `hook_id ... not found in the
hook bank` as a real finding rather than noise: it now means the id genuinely is not in
`hooks.toml`, not that the tenant happens to keep an id-less matrix. A **blocking** result (unknown
pillar, missing disclosure config for a reel, budget exceeded) means the item cannot be generated
yet; note it as a hard blocker and ask the operator to fix the source (profile facts, BRAND.toml, or
the plan) before approving.

## Step 3 — Present Gate 1

First confirm the Step 2 draft file is actually on disk (you wrote it with the Write tool). Only
then reply with a **rich, scannable brief** the user can steer before approving. Include:

- A header that **restates the active company** and the week.
- The theme in one line.
- Per item — use this exact format so the user can read and edit each field before approving:

```
<N> · <format> · <pillar> pillar
   Angle: <angle>
   Hook: <hook_direction>
   Goal / stage: <goal> · <journey_stage>   ← omit the line entirely if the profile declares no mix
   Key points: <bullet 1>; <bullet 2>; <bullet 3>
   Tone: <tone>
   Audience: <audience — omit the line if unset>
   Hook id: <hook_id · declared formats — omit the line if unset>
   Avoid: <avoid item 1>, <avoid item 2>
```

When the profile declares a mix, follow the items with one line showing the **set** against quota
(e.g. `Mix: reach 2/3 · engagement 1/1 · conversion 0/1 — short a conversion item, no candidate
scored`), so the operator can steer the balance at the gate rather than discovering it after the
week ships.

Then end your message with this EXACT marker line on its own (the cockpit turns it into the
Approve / Edit / Reject buttons and strips the marker from the visible message):

```
⟦GATE:plan⟧
```

When the plan contains any item with `format: "reel"`, add a single line near the top of the
message: *"This item is scripted for video — approving will queue it for render."* Approving a
reel plan routes it through `content-studio` → `video-script` → the creator pack's
`short-form-video` render nodes; it does not stay a text-only asset.

Do not write the final plan, do not append history, and do not proceed past the gate on your own.

## Step 4 — Resolve the gate (driven by the user's button)

The user's button press comes back as a follow-up instruction:

- **Approve** → promote the draft to the final plan:
  - Validate every item against `schemas/content-item.schema.json`.
  - Write `content/<active>/plans/<YYYY-WW>-plan.md` (human: theme + the ideas) AND
    `content/<active>/plans/<YYYY-WW>-plan.json` (the `ContentItem[]` machine contract).
  - Append history and write the run-manifest stage:
    ```bash
    python -m gtm_core.ledger_cli append-history --profile <active> \
      --json '{"event":"plan_approved","skill":"content-plan","week":"<YYYY-WW>","items":<N>}'
    python -m gtm_core.ledger_cli write-run-manifest --profile <active> \
      --json '{"run_id":"<run-id>","trigger":"telegram","stages":[{"name":"plan","status":"ok","outputs":["<plan-path>"]}]}'
    ```
  - Remove the `.pending` draft and confirm exactly what you wrote.
- **Edit** → the user sends notes; revise the draft, rewrite `.pending/<YYYY-WW>.draft.json`, and
  re-present (Step 3) ending again with the `⟦GATE:plan⟧` marker.
- **Reject** → delete the `.pending` draft and confirm. Write nothing final.

## Guardrails

- Phase 1 is **LinkedIn only** (`platform: linkedin`, `format: carousel`/`infographic`/`infographic-handwritten`/`text`). No X/IG/podcast.
- Never write the final plan or append history before the user approves at Gate 1.
- Only write under `content/<active>/`. `profiles/<active>/` and `plugin/` are read-only.
- Every `story_id` must reference a real cluster id from the radar output — never invent one.
- Always restate the active company in the gate message (the cockpit also prefixes the profile).

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
