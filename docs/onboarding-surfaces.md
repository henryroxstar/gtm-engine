# Onboarding surfaces — parity and interaction fidelity

**This file owns the surface-comparison fact.** Any doc that mentions "why does Telegram feel
different from Claude Code onboarding?" should link here instead of restating this table. (Design
rationale for the underlying engine lives in an internal planning doc, not shipped here.)

## One engine, thin adapters

There is exactly one onboarding engine — [`agent/onboard.py`](../agent/onboard.py) — and one
contract, [`schemas/profile-draft.schema.json`](../schemas/profile-draft.schema.json)
(`ProfileDraft`). Every surface below calls the same `ingest → extract → render → stage → promote`
functions and produces the same staged bundle under `profiles/.staging/<slug>/`. What differs
between surfaces is **interaction fidelity** — how the interview and review gate are presented to a
human — never the output contract. That difference is by design, not a bug to fix.

## The four surfaces

| Surface | Adapter | Interview | Review gate | Notes |
|---|---|---|---|---|
| **Claude Code** (default) | `plugin/skills/setup/` + [`agent/onboard_cli.py`](../agent/onboard_cli.py) | `AskUserQuestion` (interactive, gap-only) | voice sample + open staged files in chat | richest surface — this is where a founder should be sent by default |
| VPS / Telegram | [`cockpit/onboarding.py`](../cockpit/onboarding.py) (`/onboard`, `/onboard_confirm`, `/onboard_cancel`) | text prompts, single-shot (no gap-by-gap interview) | text confirm (`/onboard_confirm <draft_id> <name>`) | inherits every render/placeholder/collision fix for free — same engine, no separate maintenance |
| API | `backend/routers/onboard.py` (`POST /onboard`, `GET /onboard/{id}/diff`, `POST /onboard/{id}/promote`) | programmatic — no gap-filling Q&A | `GET /onboard/{id}/diff` (staged vs. live) | caller `POST`s a source (`url`/`file`/`text`), the endpoint runs `ingest → extract → render → stage` server-side (same as Telegram) and returns `gaps[]` in the response for the caller to handle |
| MCP | not built — future ~1-task adapter | programmatic | — | same engine when added; not a new engine |

- **Claude Code** is the only surface with a real gap-interview (`AskUserQuestion`) and inline
  low-confidence flagging at the review gate — see "Step 3 — A few quick questions" and
  "Step 4 — Review together (the gate)" in
  [`plugin/skills/setup/body_template.md`](../plugin/skills/setup/body_template.md).
- **Telegram** collects a URL or pasted text, stages a draft, and asks for a one-line
  `/onboard_confirm` — there is no per-gap interview loop. A founder onboarding over Telegram will
  see more unresolved `gaps[]` in the staged files than one who went through Claude Code.
- **API** and **MCP** are programmatic: no human interview step at all. The API caller still just
  posts a source (a URL, an uploaded file, or pasted text) — the same `ingest → extract → render →
  stage` pipeline runs server-side and hands back `draft_id`, `staged_files`, `confidence`, and
  `gaps[]` in the response (`OnboardIngestResponse`). There is no endpoint that expects a
  pre-built `ProfileDraft` JSON from the caller; the caller decides what to do with any returned
  `gaps[]` (re-call `POST /onboard/{draft_id}/product/{slug}/extract` with another source, or just
  accept the gaps and promote).

No one should expect the Telegram flow to feel like Claude Code, or the API to prompt for
anything — same contract and output, different interaction richness.

## Cross-surface resume

A resume point is just a staged-but-unpromoted draft with a timestamped
`.onboard-meta.json` (see `agent/onboard.py:stage()`). **Resume only works within one
deployment**, because staging lives on a filesystem, not a shared service:

- A draft staged via the VPS Telegram bot is resumable from the VPS Telegram bot (`python -m
  agent.onboard_cli status` / the "Step 1 — Resume check" step in `body_template.md` both read the
  same disk).
- A draft staged in a laptop Claude Code session is **not** resumable from Telegram, and vice versa
  — a laptop and the VPS are different disks. There is no cross-deployment handoff today.

## Which surface to pick

- **Onboarding a new founder/company, and a human is available to answer a few questions** — use
  Claude Code (`setup` skill). It has the richest interview and the only inline
  confidence-flagging, which matters most for the tenant-boundary risk (wrong-company facts
  landing in a real profile).
- **A quick draft from the VPS, no laptop session open** — use Telegram `/onboard <url|text>`,
  then review the staged files yourself before `/onboard_confirm`. Expect more `gaps[]` to remain
  unresolved than the Claude Code path.
- **Scripted / bulk onboarding, or integrating onboarding into another product** — use the API.
  `POST` a URL/file/text source per company; there's no interactive interview, so plan to either
  re-extract on the returned `gaps[]` or accept them and promote as-is.
- **MCP** — not available yet.

## `/wizard` vs. `/onboard` (Telegram naming overlap)

These are different flows and don't conflict: `/onboard` (this doc) **creates** a new profile from
a URL/text source. `/wizard` (also in `cockpit/onboarding.py`) **updates** an existing profile via
a short guided Q&A. If you already have a profile and want to change a few answers, use `/wizard`;
if you're standing up a brand-new company profile, use `/onboard` (or, preferably, the Claude Code
`setup` skill).
