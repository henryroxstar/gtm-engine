
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
- `profiles/<active>/knowledge/audience-psychology.md` (via `resolve_knowledge`, optional) — the
  per-persona psychological layer + founder-fit tags that shape `brief.angle` below.
- `profiles/<active>/knowledge/social-tuning.md` (via `resolve_knowledge`, optional) — the
  company's per-platform tuning (posting clocks, lead formats, voices bench, the "never" list).
  The playbooks above hold the generic method; this file holds what's specific to the company.

If there are no radar clusters yet, tell the user to run `content-radar` first.

## Step 1 — Propose the plan

Pick a **weekly theme** that ties the top-scoring clusters to the active company's `wedge`, then
propose **3–5 content ideas**. Each idea is a `ContentItem` (schema:
`schemas/content-item.schema.json`) — required fields:

- `id` — stable, e.g. `ci-<YYYYWW>-01`
- `pillar` — from the cluster / profile pillars
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
  - `hook_direction` — how to open (e.g. "stat-first → reframe as a solvable infra problem"); archetypes in `docs/hook-craft.md`
  - `trigger_stack` — the 2–3 emotional triggers this item should stack (from `docs/virality-engineering.md`, e.g. "curiosity + productive discomfort"), so studio drafts for a felt experience, not just an informative one
  - `key_points` — 3–5 bullets the asset must land
  - `tone` — voice note specific to this item (e.g. "strategic informality, peer not vendor")
  - `avoid` — phrases, claims, or framings to exclude
  - `audience` (optional) — who this piece is for: a segment name from
    `profiles/<active>/knowledge/icp-personas.md` (e.g. "CISO / security lead") or a short
    free-form description. Steers studio drafting; the operator can set or change it at Gate 1.
- optional: `slot` (e.g. "Mon AM"), `locale` (default the profile `language`)

> **Localized variants (two-clock rule).** `locale` defaults to the profile's primary `language`. To
> target a second market (e.g. an APAC variant alongside the US one), emit a **separate** item with a
> non-primary `locale` (a BCP-47 tag like `en-IN` or `zh-CN`) — content-studio will then produce a
> genuinely re-framed variant for that market, not a translation. The operator can request this when
> steering at Gate 1 (e.g. "also do an APAC variant of #2"). Keep it deliberate — only add localized
> items the operator asks for or the plan clearly warrants; don't fan out every item by default.

Bias toward the highest-scoring, on-pillar clusters; vary the format; keep it realistically small
(3–5 items for one person to ship in a week).

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
   Key points: <bullet 1>; <bullet 2>; <bullet 3>
   Tone: <tone>
   Audience: <audience — omit the line if unset>
   Avoid: <avoid item 1>, <avoid item 2>
```

Then end your message with this EXACT marker line on its own (the cockpit turns it into the
Approve / Edit / Reject buttons and strips the marker from the visible message):

```
⟦GATE:plan⟧
```

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
