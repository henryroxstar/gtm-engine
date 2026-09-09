# Performance lexicon — the face, the body and the voice, written small enough to be believed

> **Company-neutral.** This is the *mechanism* for directing a person on screen: what an emotion
> actually does to a face, how much of it a person lets show, and how to write that as prompt text.
> It carries no company facts and names no creator, channel or practitioner. It is the performance
> half of `story-graph.md`'s story half — that file decides what happens to whom, this one decides
> what it looks like on them. Sibling of `content-plan/references/retention-rubric.md` in standing:
> directional craft, not measurement.

## When this file applies

**Any shot with a person in frame.** A presenter talking to camera, a story item's actors, a pair
of hands on a table. It is *not* gated on `brief.protagonist` — the failure this file exists to
stop was found on `role: broll` shots, in a film whose presenter shots had no expression at all.

Load it when you are writing or reviewing: `visual_hook.expression` (decision 9), the payoff entry
of `broll_list` (decision 7), any shot's `expression`, `motion_prompt` or `visual`, a storyboard
still prompt, or a voice `instruction`.

## The defect, in two directions

A rendered face fails two ways, and they need opposite fixes.

**Under-direction.** An `expression` left empty inherits whatever the identity anchor's source
photo happens to hold, and a tone word copied from the profile's prose voice ("calm, dry") renders
as an absent face. One shipped film had every shot set to "calm"; the operator's read was *it looks
like I don't want to be there*.

**Over-direction**, which is what this file adds, arrives in two costumes:

- **An adjective nobody translated.** "Amused", "shocked", "thrilled" name a *category*, not a
  size. A model resolves the size from what the word is attached to in its training data, and that
  is a stock photograph. Every unspecified magnitude renders at the maximum.
- **A stack of true observations delivered at once.** A shipped payoff beat asked for *"eyes
  closing, brows lifted at the inner ends and held wide apart, mouth closed and still, jaw loose,
  chin steady, eyes wet and holding"*. Every clause in it is a real marker of being moved. Together
  they are anguish. One clause also asks the brow for two shapes at once: the moved brow lifts at
  the **inner** corners and usually draws slightly together, while a brow lifting as a whole is the
  surprise family — so *"lifted at the inner ends and held wide apart"* pulls in both directions and
  the render had to pick, and picked the bigger. The stack is not the writer's invention: `story-graph.md` lists six reliable
  markers of a moved face in one breath, and the writer used all six.

The rest of this file is one grammar, four lenses on why it is shaped that way, and a table of what
to write at each beat.

## 1. The grammar

```
expression := [register —] <one region> <small action> <magnitude>; <what holds still>; <gaze>[; <when it changes>]
```

Seven rules, in the order they bite:

1. **One primary moving region. Two at most.** A face does not move everywhere at once, and a
   render asked for six regions plays all six at full size. The linter warns past its own ceiling
   of *named* regions — run `python -m gtm_core.shots_lint --rules` for the number and the refused
   vocabulary, which live in the code rather than on this page so they cannot drift apart from it.

2. **A magnitude word is mandatory, and it is small.** *A fraction, a few millimetres, faintly, a
   beat too long, just, barely, slightly.* This is the single highest-leverage word in the field.

3. **Say what holds still, and say it positively.** "mouth closed and still", "hands flat on the
   table", "head level". Never *"not smiling"* — negation leaves the banned thing in play, and the
   linter refuses it in this field outright.

4. **Gaze is its own clause.** Direction (the lens, the other person, down to the table, the middle
   distance) and duration (held a beat too long, one slow blink). Where a person looks is the
   cheapest and most reliable emotional signal there is.

5. **A register label may open the clause but never stand alone.** *"recognition — stillness, a
   small exhale through the nose, gaze held a beat too long"* is the form. The label alone is the
   adjective defect.

6. **Video adds the timing; the change IS the performance.** *"at 1.5s the inner brows lift a
   fraction and the gaze drops; the mouth stays closed; a swallow at 2.5s."* A still takes the
   frame **after** the change lands, never the apex — the after-face is what reads as felt.

7. **One channel per cue.** The face goes in `expression`. The body, hands and orientation go in
   `motion_prompt` (video) or `visual` (still). The voice goes in the TTS `instruction`, which
   `video-render` derives from `expression`. Body cues written into `expression` do not just sit in
   the wrong field — they count against the region ceiling and crowd out the face.

**Shot size sets magnitude.** This extends `video-storyboard`'s "shot size is emotional distance":

| shot size | how much face | magnitude |
|---|---|---|
| extreme close-up | one region | the smallest thing that reads: a blink, a swallow, a lid |
| close-up | one, two at a push | a fraction of a normal movement; the eyes carry it |
| medium close-up | face plus one body cue | small; the hand or shoulder does half the work |
| medium / wide | the body carries it | the face may stay level and still read |

**Stills need a skin scaffold.** Generators smooth and idealise a face, and smoothed skin reads as
posed. Carry the counter-phrase the brand kit records for the identity ("natural skin texture,
visible pores, unretouched") on any shot with a face in frame.

### The grammar applied

```expression
recognition — stillness, a small exhale through the nose, gaze held a beat too long
```
```expression
the inner brows lifting a fraction, mouth closed and still, gaze going down to the table
```
```expression
curiosity — brows up a few millimetres, lips parted, head tilting slightly, gaze steady on him
```
```expression
a set jaw, lips pressing together, stillness, gaze level and staying there
```
```expression
at 1.5s the lids close a beat too long and open again; the mouth stays closed; a swallow at 2.5s
```
```expression
warmth arriving in the eyes before the mouth — lower lids pushing up, the smile staying small
```
```expression
a small deflation — the half-smile going quiet, one short breath out through the nose
```
```expression
eyes wet and holding, nothing falling; mouth closed; the gaze steady on her
```

And the same beats written the way that fails:

```expression-bad
beaming as she reads the message
```
```expression-bad
conversational and a little amused
```
```expression-bad
shocked, eyes wide, jaw dropping
```
```expression-bad
eyes closing, brows lifted at the inner ends and held wide apart, mouth closed and still, jaw loose, chin steady, one shoulder dropping
```
```expression-bad
overjoyed, grinning from ear to ear
```
```expression-bad
tears streaming down her face as she turns
```

## 2. The psychologist — what an emotion is, and what a face reliably does

**An emotion is an event, not a state.** Onset, apex, offset — a few seconds at most for a felt
expression. Anything held longer reads as posed, which is why "he looks sad throughout" is a
direction that cannot be performed. Felt expressions come on smoothly and are roughly symmetric;
posed ones snap on, hold, and are often lopsided.

**The reliable signatures.** These are the movements that survive a close-up. Written as muscles,
they are directly usable; written as their labels they are the defect.

| what it is | what actually moves | the subtle form |
|---|---|---|
| felt warmth | cheeks lift, lower lids push up, corners of the eyes crease | the eyes change and the mouth barely does |
| a social smile | mouth only, eyes unchanged | politeness, distance, or a mask — use it on purpose |
| being moved / sadness | **inner** brow corners lift and draw slightly together; gaze drops | the inner corners lifting a fraction with the **outer** brows staying level — the smallest form, and the one that cannot be mistaken for surprise; one slow blink, a swallow, wet eyes with nothing falling |
| attention / surprise | brows lift **as a whole** — the outer ends too — and the upper lids raise | the head stops, a held blink, eyes open a millimetre |
| unease | brows lift **and** pull together; lip corners pull back or press | breath shortens, gaze goes to the middle distance |
| anger | brows lower and pull together, lids tighten, lips press | a set jaw and stillness; speech slows |
| contempt | **one** lip corner tightens and lifts | the only asymmetric signature there is — potent, so deliberate or never |
| curiosity | brows a few millimetres up, lips parted, head tilt | gaze steady, everything else quiet |

**Combinations flip meaning, which is how a stack goes wrong.** Inner brow lift plus outer brow
lift is surprise, not sadness. Wide eyes plus open mouth is shock. Closed eyes plus lifted brows is
anguish or prayer. A smile plus raised brows is greeting, not warmth. Before stacking two cues,
check you have not written a third thing.

**Almost all of this work lives in the low-arousal half.** Feelings sit on two axes — pleasant to
unpleasant, and calm to activated. Business communication is nearly all low-arousal, and models
default to high. Reach for the sibling, not the headline:

| the word you reached for | the one that is true, and renders |
|---|---|
| happy, thrilled, excited | content, relieved, quietly pleased, warm |
| sad, devastated | disappointed, weary, deflated, wistful |
| surprised, shocked | caught out, re-reading it, arrested mid-thought |
| angry, furious | unimpressed, hardened, done being patient |
| proud | quietly pleased, unwilling to say so |
| grateful | unable to answer for a second |
| afraid | wary, checking, unwilling to commit |

**Emotion follows a thought.** The strongest direction names the trigger, not the label: *"hears
her own line quoted back to her"*, *"realises the number is not a typo"*. The face follows a thought
in a way it never follows an adjective.

## 3. The behavioural psychologist — how much of it a person lets show

Between the feeling and the face sits a decision, mostly unconscious, about what is allowed here.
Six moves: **amplify, de-amplify, neutralize, mask** (cover with a different emotion), **qualify**
(add a smile to soften), **simulate**. Among adults in front of people they are not intimate with,
de-amplify, neutralize and qualify dominate.

**So the default for a professional on camera is the felt thing at a fraction of its size, usually
with a qualifier.** That is not an aesthetic preference. It is what the room actually produces.

**Modulators — declared by the person, never inferred from a name or a photograph.** These vary
what "a fraction" means, and getting them from a face is exactly the error this section prevents.

- **Culture and market.** In many East and Southeast Asian professional settings, negative affect
  is de-amplified or masked in public, a smile qualifies a disagreement rather than signalling
  agreement, and sustained direct gaze is less normative. In lower-context settings, more
  amplification reads as sincere. This cuts both ways: it governs the presenter's face *and* how an
  audience reads it, so the same performance is warm in one market and theatrical in another.
- **Age.** Older adults regulate more, show fewer and smaller expressions, and skew positive.
  Younger adults amplify more. A single "energetic" direction ages a presenter wrongly.
- **Gender.** Socialised expectations, not biology: some settings permit sadness more from women
  and anger more from men, and expect women to smile more. **Never write to these defaults.** They
  are here so you recognise a stereotype arriving in a prompt. The person's own declared baseline
  overrides them, always.
- **Setting and relationship.** Public or private; stranger or intimate; higher status shows less
  reactive affect; seated is smaller than standing; a person who knows the camera is there is
  managing more than one who does not.

**The leak.** When a feeling is managed, it does not vanish — it escapes in sub-second flickers and
in the body. This is why video gets its subtlety from *timing* and stills get theirs from
*incongruence between channels*: a social smile with a hand at the collar says more than either.

## 4. The nonverbal analyst — the body is more honest than the face

**Baseline first.** No behaviour means anything on its own; it means something as a *deviation*
from how this person normally sits. The hero still is the baseline frame, and every shot after it
is a departure from that. A person's own baseline lives in the person layer of the profile's voice
card, not in this file.

**Limbic responses** — write these into `motion_prompt` or `visual`:

- **Freeze.** Stillness, the hands stopping mid-task, breath held, the whole body going quiet. The
  most under-used and most readable cue in this list.
- **Flight.** The torso or the feet turning away, leaning back, placing an object between, blocking
  the eyes — lids closed a beat too long, a hand going to the face.
- **Fight.** Leaning in, jaw forward, chest squared, hands planted.

**Pacifiers** (self-soothing, and the reliable tell of discomfort): a hand to the neck or the base
of the throat, pulling at a collar, stroking the jaw, lips compressed until they thin, a lip lick,
hands rubbing each other, palms pushed down the thighs, a touch to the hair.

**Comfort displays**: torso open toward the other person, relaxed hands with thumbs visible, a
quick eyebrow flash on greeting, a heel lifting.

**Two rules for using them.** The face gets one thing and the body gets one thing — and they either
agree, or they deliberately disagree, because *masking is that disagreement*. And when you cannot
decide what a beat's face is doing, ask instead whether the person is **comfortable or
uncomfortable**, and write that. It renders as real because it is what people actually do.

## 5. The screen actor — how it is performed for a lens

- **React to something specific.** Name the stimulus in the prompt. "Sad" is unplayable; "reads the
  last line again" is playable and renders.
- **The lens magnifies.** What carries to the back of a room is mugging in a close-up. The tighter
  the frame, the smaller the movement — see the shot-size table.
- **The camera sees thought.** Write what the person is deciding. A still face with a thought
  behind it is the strongest close-up available, and it is free.
- **The eye does the work.** Blink rate and how long a gaze is held are the two most controllable
  emotional dials in a close-up.
- **Suppression plays stronger than release.** The effort to contain a feeling gives the viewer the
  feeling *and* the effort. It is also just what adults do in front of people. One marker of
  containment per shot, not the list of them.
- **Play the objective, not the emotion.** The person wants something in the scene. The feeling is
  a by-product of not getting it, or getting it.
- **One gesture carries the inner state**, and repeating it at a later beat is the point.
- **Subtext: the face never illustrates the line.** If the words say the small thing, the face
  carries the big one. A face that acts out its own dialogue reads as an advertisement.
- **Listening is acting.** The reaction shot is usually the emotional frame, which is why a payoff
  is so often the *other* person's face.

## 6. The beat map

The nine-beat order is `story-graph.md`'s; the four narrative functions below it are what a
non-story script uses. One region per cell. The last column is what the render will do if you leave
the magnitude unstated.

| beat | the thought | face — one region | what holds | body → `motion_prompt` | voice → `instruction` | refuse |
|---|---|---|---|---|---|---|
| main character | at ease, mid-task | the face level, gaze on the work | the hands busy and unhurried | open, settled, unhurried | even, unhurried | "happy", a smile at rest |
| inciting incident | *that is not what I expected* | the head stopping; one blink held | mouth closed | the hands stop mid-task | a beat of silence before the line | wide eyes, a gasp |
| goal | *right, then* | the jaw setting, lips pressing lightly | gaze level and staying | one exhale, the shoulders squaring | quieter, a half-step slower | "determined", a jutted chin |
| debate | two answers at once | one lip corner tucking in | the brows quiet | gaze away and back; a hand to the chin | trailing slightly at the clause end | a furrowed scowl |
| decision 1 | certainty, with a cost not yet counted | a short nod, the gaze steady | the face otherwise level | a pacifier — a touch at the collar | firmer, a touch too quick | triumph; the tell is the point |
| escalation · external | quiet unfairness | lower lids tightening a fraction | mouth closed, brows level | leaning away; an object placed between | flat, giving nothing | indignation, a glare |
| escalation · interpersonal | *that was mine to say* | stillness, a small exhale through the nose | the brows level, jaw relaxed | gaze staying on the speaker a beat too long | nothing — do not give them a line | envy; narrowed eyes make them small |
| escalation · internal | *I did it again* | the face level; nothing on it | everything | the repeated gesture from the earlier beat | none — this beat needs no face at all | a wince, a self-aware look |
| the low point | the invoice arriving, on the witness | gaze dropping and going to the side | the mouth closed | the hand stops moving; shoulders drop a centimetre | a swallow before the next word | a collapse, a head in hands |
| decision 2 | new information, not willpower | the brows releasing, the jaw softening | the gaze steady once it lands | the head lifting; eyes going to the other person | warmer, slower, unhurried | resolve music on the face |
| goal achieved | the message, arriving | inner brows a fraction **or** eyes wet and holding — one | mouth closed and still | turning toward, leaning in | a catch before the noun, once | tears falling, eyes closed, a handshake at the table |
| *setup* | curiosity | brows a few millimetres up, lips parted | the head still | leaning slightly in | light, unhurried | brightness, presenter energy |
| *reveal* | *there it is* | a blink held, then the gaze coming back | the mouth quiet | the head stopping | a half-beat of silence first | eyebrows-up delight |
| *stakes* | this costs something | the jaw set, lids tightening a fraction | stillness | the hands going still | slower, quieter, lower | severity, a frown |
| *payoff* | warmth, earned | lower lids pushing up, the smile going no wider | the brows level | the shoulders dropping | warm, and no faster | a grin, an ear-to-ear smile |

The payoff row is the one this system gets wrong most, because it is the beat where a writer most
wants to reach for the label. `story-graph.md` says a face visibly moved is one of the five payoff
ingredients; this file says it is **one marker of it**, at a fraction of its size, with the rest of
the face holding still.

## 7. The refused vocabulary

A closed list of stock reaction-shot words is **refused by the linter**, not by this page: run
`python -m gtm_core.shots_lint --rules`, and the rule's own message names what it caught and why.
It is a fence, not a correction — no expression written on this system's own shot lists has ever
tripped it. It exists because those words are one hurried draft away, and because the release form
of a feeling ("tears streaming") is exactly what the restraint form ("eyes wet with nothing
falling") is most likely to be replaced by under deadline.

The region ceiling is a **warning**, not a refusal: a fifth region is usually a stack, but it is a
craft judgement, and the operator may still make it.

## 8. Engine mapping

| surface | the face | the body | the voice | the subtlety default |
|---|---|---|---|---|
| storyboard still | `expression` as its own phrase, plus the skin scaffold | `visual` | — | the hero still is the baseline; every later shot is a departure from it |
| image-to-video | `expression`, with the timing of the change | `motion_prompt` | the TTS `instruction`, derived from `expression` | one change per shot |
| synthetic talking head | none — the engine owns the face | its motion prompt, body and hands only, describing a **person** and never a diagram | voice settings on the clone: low style, high stability | the engine's own expressiveness setting stays at its default; raising it is an operator decision, recorded |
| voice only | — | — | the voice column of the beat map | slower and quieter for gravity and for being moved; a catch before the noun, never a tremor |

A synthetic talking head cannot carry the payoff beat of a story. Its face is a performance on a
disclosed synthetic, which is the opposite of the ingredient — the lane preflight already routes
that beat to live action, and this table does not argue with it.

## What this file does not do

- **It does not score a face.** There is no metric here, no predictor of emotional response, and
  no threshold. The linter checks *shape* — vocabulary and how many regions move — and shape is
  not quality.
- **It does not decide who is in the shot.** That is decision 7 and `story-graph.md`'s five
  ingredients.
- **It does not infer a person's display norms.** Culture, age and gender appear here so that a
  stereotype is recognisable when it arrives in a prompt. The modulators that apply to a real
  presenter are the ones that presenter declared.
- **It is not a substitute for looking.** Every claim here is about what *usually* reads. The
  render either looks like a person or it does not, and that judgement is the operator's.

*Provenance: the muscle signatures and the onset/apex/offset shape are restated from the
facial-action-coding tradition in applied psychology; the six management moves and the cultural,
age and gender modulators from the display-rules literature that grew out of it; the two-axis
valence/arousal framing from the circumplex model of affect; the freeze/flight/fight reading,
pacifiers and comfort displays from the applied nonverbal-behaviour tradition used in investigative
interviewing; the performance rules in §5 from mainstream screen-acting craft. Directional craft,
same standing as `content-plan/references/retention-rubric.md`'s dimensions — see that file's
evidence note. Claim strength differs by lens and is worth knowing: the muscle signatures and the
existence of display rules are replicated experimental findings; the specific cultural, age and
gender tendencies are population-level and say nothing about any individual, which is why this file
requires them to be declared rather than assumed; the nonverbal and acting material is
practitioner-derived observation, useful for direction and not evidence of anything. No number on
this page comes from any of those sources. The two-direction framing of the defect, the grammar,
the beat map, and the one-marker-per-shot correction to the moved face are this system's own,
arrived at from its own rendered output and recorded here so the same argument is not had twice.*
