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
This skill's implementation is part of the hosted product and is not included
in this distribution.

**Stop here.** Do not improvise a replacement procedure, and do not fall back to
another skill: report to the operator that this step is unavailable in this
distribution and end the run. A graph reaching this node cannot complete, and an
improvised substitute would spend budget producing something the pack does not
specify.

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
