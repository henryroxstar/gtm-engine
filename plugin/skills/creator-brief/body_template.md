# Creator Brief (the pre-spend planning surface)

Decide, record and cross-examine the nine decisions that govern one video run — **before anything is generated**. This is *how to make it*, sitting between the plan item's own `brief` (*what to say*, unchanged by this skill) and `video-script` (*shot by shot how it goes*).

> Resolve the **active profile** (the agent provides it). The only writable state is `content/<active>/`.

**This skill adds no gate.** Gate 1 (plan) and Gate 2 (publish) are the only human gates and are unchanged. The brief is an artifact produced under the Gate 1 envelope, never approved on its own. Do **not** emit a gate marker of any kind from this skill.

**Never edit `brief.json` by hand.** Every write goes through `python -m gtm_core.creator_brief`, which validates before it writes: a brief that does not validate leaves no file behind. A half-written brief on disk is read downstream as a complete one, and the contradiction then surfaces *after* the spend this brief exists to precede.

## Step 0 — Read inputs, and answer as much as you can without asking

- **The plan item** — its `brief` sub-object (`angle`, `hook_direction`, `trigger_stack`, `key_points`, `tone`, `avoid`, `audience`). That is what to say. You are not re-deciding it.
- **The lane and the modality that were already decided.** `format-router` owns the modality decision and `gtm_core.video_preflight` owns the lane. Decision 4 **records** both answers and re-decides neither:
  ```bash
  uv run python -m gtm_core.video_preflight --profile <active> [--product <slug>] --json \
    [--item-json <path to the plan item>]
  ```
  Pass `--item-json` when you have the plan item on disk: it is the only way `constraints.story_capture`
  is populated, and that field is what decides decision 5 for a story item. Without it the preflight
  reports `null` there, which reads as "not a story item" — correct for most runs, wrong and silent
  for a run whose item you simply did not hand over.
- **The brand kit** — palette, look, disclosure line, identity handles:
  ```bash
  uv run python -m gtm_core.brandkit --profile <active> [--product <slug>]
  ```
- **The profile's content priorities** — `knowledge/content-priority.md`, resolved product-first:
  ```bash
  uv run python -m gtm_core.resolve_knowledge content-priority.md --profile <active> [--product <slug>]
  ```
- **The outlier-mining procedure** — `plugin/skills/content-plan/references/outlier-mining.md`, which is where decision 2's structure comes from.
- **The story graph** — `plugin/skills/creator-brief/references/story-graph.md`, read **only when the plan item carries `brief.protagonist`**. It supplies decision 2's beat order from the form rather than from this run's outliers, says what decision 7's payoff b-roll has to contain, and gives decision 9 its story-shaped default. An item with no protagonist is not a story item and does not load it.
- **The performance lexicon** — `plugin/skills/creator-brief/references/performance-lexicon.md`, read whenever **a person will be in frame** — a presenter, or a story item's actors. Unlike the story graph it is not gated on `brief.protagonist`. It supplies the grammar for decision 9's `expression` field, the per-beat emotional register behind decision 7's payoff entry, and the reason both are written as muscles at a stated size rather than as an adjective.

Compute the run's `script_slug` as `<YYYY-MM-DD>-<slug>`, the same stem `video-script` will use for `content/<active>/scripts/<YYYY-MM-DD>-<slug>.shots.json`. The brief mints the run folder `content/<active>/video/<script-slug>/` and is its first artifact.

## Step 1 — Fill the nine decisions, default-first

Each decision carries a **`source`**: `operator`, `profile_default`, `derived`, or `model`.

- A decision with a default is **filled and labelled, never asked**. That is what keeps a routine "make a short about X" one question long.
- Ask the operator **only** for a decision with no available default — in practice the capture contract on a first run of a **non-story** item, and nothing on a routine one. A story item derives its capture contract (decision 5) and is not asked.
- `derived` means you computed it from another artifact (the preflight, the kit, the plan item). `model` means you chose it yourself. Both are honest; neither is `operator`.

| # | decision | what it fixes |
|---|---|---|
| 1 | `cover` | The cover/thumbnail, designed **before** the content — subject and headline at least. It is the binding constraint on framing, palette and headline, and it is a *separate artifact* from the first frame. |
| 2 | `outlier_structure` | 3–5 outlier performers mined for **structure** — beat order, hook shape, payoff placement. Commit to one and **name** it. Lifting an outlier's *content* is prohibited. On a `brief.protagonist` item, `references/story-graph.md` offers the nine-beat order as a second candidate, from the form rather than the outliers — recorded `source: model`. |
| 3 | `slot_schema` | The fixed per-modality slot set this format fills. Coverage of the slots beats prose length. These are also the scene boundaries a presenter render cuts to. |
| 4 | `cheapest_medium` | Could this be a carousel or a still? Record the answer **and the reason**, from what `format-router` and the preflight already decided. |
| 5 | `capture_mode` | `rendered` or `live_action`. `live_action` means the `live-action-video` graph and a shoot the operator does themselves. **When the preflight reports `constraints.story_capture`, this decision is already made**: take its value, record `source: derived`, and copy its `reason` verbatim into the decision's `reason` field. Never `operator` — nobody was asked. |
| 6 | `invariant` | What stays constant across every shot (grade, depth of field, camera character), plus the aspect ratio — chosen from what the composition must contain, never a house default. Seeds `style_scaffold.look`. |
| 7 | `broll_list` | The planned b-roll selection list. Selection *is* the storytelling layer, so it is planned, not scavenged. On a story item, check the payoff beat's entry against the five ingredients in `references/story-graph.md`: two of them are *visible* (people coming together, a face moved), and a payoff list holding neither cannot fire the beat however the script reads. What a moved face looks like at the size an adult actually shows it — **one** marker, not the list of them — is `references/performance-lexicon.md`. |
| 8 | `sampling_curve` | N per shot — highest on the hook shot, 1 on the tail — **plus the selection criterion**. Declared before spend so the whole curve is priced against the cap at once. |
| 9 | `visual_hook` | The first frame as a shot spec: subject, framing, focus plane, expression, and the familiar thing beside the novel one. Distinct from the cover — same subject, different job. No text in frame. On a story item the default is the flash-forward to the emotional peak (`references/story-graph.md`) — the outcome beside the person it happened to. Write `expression` as muscles at a stated size — one region moving, what holds still, where the eyes go — never as an adjective, which renders at full magnitude (`references/performance-lexicon.md`). |

Where the element library (`python -m gtm_core.elements list --profile <active>`) holds a character, environment or product this run uses, **name it by slug** in decisions 6 and 7 rather than describing it in prose. A re-described subject is a re-invented subject.

## Step 2 — Write it through the CLI

Compose the document, then:

```bash
uv run python -m gtm_core.creator_brief write --profile <active> --doc <path-to-your-json>
```

It prints the two paths it wrote: `brief.json` and its markdown twin `brief.md`. The twin is **generated** — nine lines, one per decision, each with its source. Editing the twin changes nothing any skill reads.

A non-zero exit means nothing was written. Read the errors and fix the document; do **not** work around a refusal by writing the file another way.

### The creative brief, when a person will read it

`brief.json` is for the skills downstream and `brief.md` is its generated echo; neither is a document a director can work from. When the piece will be handed to a human — a shoot, an editor, an outside collaborator — hand-author `creative-brief.md` beside them in the same run folder, to the shape in `references/creative-brief-template.md`: two pages, the brief and the beat sheet. It is the one artifact here written by hand, so it carries no derived figures — captions, timings, gate results and claim counts live in the script and the shot list, and a brief that copies them disagrees with the film within the hour. Skip it on a routine run nobody else touches.

## Step 3 — Check it against itself, then say what is weak

```bash
uv run python -m gtm_core.creator_brief hollow content/<active>/video/<script-slug>/brief.json
```

Deterministic findings for a brief that validates and is still empty — an "outlier structure" called *the usual*, a cover with no headline, a one-word invariant. These run everywhere, with no API key.

Then the fresh-context reader, which has judgement the checker does not:

```
score_drafts(kind="brief", path="content/<active>/video/<script-slug>/brief.json", profile="<active>")
```

It scores the nine decisions on five named axes — `cover_is_a_promise`, `structure_is_named_not_described`, `the_invariant_is_visible`, `first_frame_is_recognisable`, `cheapest_medium_reason_holds` — and returns **notes, never a rank and never a gate**. Without an API key it returns `unavailable`; say so plainly in the report rather than implying the brief was read.

The brief is the cheapest point in the lane to be wrong. Fix what the reader flags **now**, before a single frame is generated.

## Step 4 — Report

State, in this order: the run folder, the capture mode, the named structure, the cover headline, the sampling curve total (Σ n), and which decisions carry `source: operator`. If every decision defaulted, say that out loud — the twin does too.

Then hand off: `video-script` reads this brief and **fails closed** if its shot list contradicts it.

## Guardrails

- **No gate marker.** This skill emits no `⟦GATE:…⟧` of any kind. The brief is approved *with* the plan, never on its own.
- **Never edit `brief.json` or `brief.md` directly** — the CLI is the only writer, and the twin is derived.
- **Do not re-decide the modality or the lane.** Decision 4 records what `format-router` and `video_preflight` already said, with the reason. If you disagree with them, say so in the report; do not silently record a different answer.
- **Structure, never content.** Decision 2 takes an outlier's beat order. Lifting its wording, its claims or its footage is out of scope and stays out.
- **The brief governs one run.** It lives in that run's folder. It is not a tenant-level document, and `item_id` is a field inside it, never part of its path.
