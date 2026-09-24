---
source: manual
refreshed: 2026-09-24
review: evergreen
---
# Voice — the craft layer: registers, long-form build, and the on-camera performance

Replace this file with your own craft layer. It is the *video* half of the voice split, and it
ships as a stub rather than as nothing on purpose: `voice.md`'s pointer block, the `video-render`
prompt recipes and the `video-storyboard` hero-still step all name
`profiles/<active>/knowledge/voice-video.md` by path, and a named file that does not exist is a
skill silently running on whatever the source photo happened to hold.

One home per fact. Judgement about *outreach* stays in `voice.md`; anything a linter could check
(word counts, banned constructions, sign-off shape) stays in `voice-rules.toml`. What belongs here
is everything about how this voice sounds **spoken** and how it reads **on camera**.

## Registers

One row per register you actually write in, with its length and where it is used — for example a
short social caption, a long-form post, a spoken script, and a narration bed. Two registers that
differ only in length are one register; cut it down to the ones a writer can tell apart.

## How I build a piece

The order a long-form piece is assembled in: what opens it, what earns the general point, what the
close is allowed to do. Written as a procedure, not as adjectives.

## The Spoken register

How a line is written to be *said* rather than read — sentence length, where a breath lands, which
constructions survive being spoken aloud and which only work on a page.

## The Narration register

Voice-over against footage the viewer is already watching: what the narration adds rather than
restates, and how it hands off to what is on screen.

## How emotion shows on this face

**The section the render skills read by name.** A nonverbal cue means nothing on its own — it means
something as a *departure* from how this person's face normally sits. So record the baseline first
(the ordinary working expression, not the biggest beat), then, per beat, which single region moves,
how far, what holds still, and where the eyes go. A stack of markers across a whole face renders as
anguish rather than restraint, and an adjective with no stated magnitude renders at the maximum.

Where a person layer exists (`identity/<person>/VOICE.md`), this section is propagated from it — do
not hand-edit the generated block; edit the source and re-run the sync.
