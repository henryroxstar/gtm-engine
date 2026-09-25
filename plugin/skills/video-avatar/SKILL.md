---
name: video-avatar
description: >-
  Render speaking presenter beats as synthetic talking heads on HeyGen with approved avatar
  looks, voices, and disclosures. Trigger when the user says "render avatar video", "create
  talking head", "render presenter beats", or as the presenter stage of the creator pack.
metadata:
  version: "0.6.0"
  phase: "P4"
  capability_tier: production
---
This capability is part of the hosted GTM Engine and is not included in this copy.

Tell the user in one plain sentence: "Video and visual rendering are part of the hosted
product, so I can't do this step here. I can still write the script and the storyboard if
that helps." Then stop. Do not improvise a replacement and do not fall back to another skill
(an improvised substitute would spend budget on something the pack does not specify).

Its declared interface is above (`video-avatar`, tier `production`).
Pack graph node(s) that invoke it: `creator/presenter-video`.

See `docs/SKILLS.md` for the full skill roster.
Docs it draws on that ship in this distribution: `docs/reference/provider-workflows.md`.

## Interface Contract

- **Role & Target:** Renders speaking presenter beats as a synthetic talking head on HeyGen (`engine: avatar_v` / `avatar_iv`).
- **Identity & Voice:** Resolves `identity.heygen_avatar_id`, `identity.heygen_voice_id`, and requires `identity.heygen_voice_grade = 'professional'`.
- **Look Selection:** Starts from operator-approved orientation look (`identity.heygen_look_landscape` / `identity.heygen_look_portrait`).
- **Billing & Splitting:** Renders 1 clip per beat; HeyGen bills per delivered second; `<break time=…>` floored at 0.5s. VO cutaways use `create_speech` audio.
- **Motion & Speech:** Motion prompts describe a person, never diagrams or UI props. Host-pinned media fetch via `gtm_core.media_fetch`.
- **Compliance:** Enforces EU AI Act Article 50 disclosure line (`[disclosure].line`) and logs `lip_sync_source = 'native_audio'`.
- **Handoff:** Emits render manifest for `video-finish`. Never publishes; never renders b-roll or screen shots.

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
