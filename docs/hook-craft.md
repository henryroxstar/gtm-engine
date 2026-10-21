# Hook craft — first lines for organic social

Cross-profile, **de-branded** craft reference for the opening line of an organic post (LinkedIn text,
X, Facebook, carousel cover) — and, in the last section, for the **cover frame** that carries the same
job when the surface is an image. Loaded by `content-studio`, `builder-studio`, and `carousel-pdf` at
draft time.

**This is not the outreach hook-matrix.** A profile's `knowledge/hook-matrix.md` produces **1:1**
opening lines for cold outreach (persona × a specific "why-now" signal about *that account*). This doc is
for **1:many** feed content, where the reader is a stranger scrolling and the first line either earns the
next one or loses them. Different job, different rules.

## The one rule underneath all of it

A hook has ~2 seconds and one line to open a gap the reader needs closed. It must be **specific enough to
be un-fake-able** and **self-contained enough to land cold**. Everything below serves those two.

**Concrete-anchor rule (hard).** Line 1 must contain at least one concrete anchor: a number, a named
entity (person, company, tool), a direct quote, or a shipped artifact. A hook built only from category
abstractions ("X is becoming Y", "the future of X", "connected AI", "the power of X") is not a hook yet,
find the specific thing and lead with it. (Recognition/curiosity hooks that name a *felt, specific* state,
e.g. "you're not being replaced; the busywork is", satisfy this; vague category theses do not.)

**What the specificity is *for*: filtering.** A hook's job is not to be read by the most people, it is to
be read by the right ones — attract the reader you can actually help and repel everyone else, on purpose.
Reach is a byproduct of that fit, not the target. This is why the concrete-anchor rule bites: a named
number or a shipped artifact is legible to the person living that problem and invisible to everyone else,
while a category abstraction is mildly agreeable to everyone and disqualifying to no one. If a hook could
have been written by any of your competitors, or could be nodded at by someone who will never buy, it is
filtering nothing. Prefer the version that loses the wrong reader faster.

## Ground every hook in the evidence pack

Specificity is the whole game, and in this system specificity is **not invented** — it is pulled from the
evidence pack the studio stage already has. Every number, name, or claim in a hook must trace to that
pack, and it rides the same adversarial claim check as the body. A hook that needs a figure you can't
source is a hook you can't use — pick a different angle, don't round up a guess. (This is the improvement
over generic hook advice: the specificity is real by construction.)

## Where an archetype comes from when none of these fit

The list below is a starting set, not a closed one, and the honest way to extend it is to derive a
shape from work that already beat its own baseline rather than to invent one. That procedure —
select by **ratio to the channel's own median** and never by absolute reach, extract beat order,
hook shape, payoff placement and cover shape, and lift the STRUCTURE while never lifting the
content — is written down once, in
[`plugin/skills/content-plan/references/outlier-mining.md`](../plugin/skills/content-plan/references/outlier-mining.md).
It is cited here rather than restated, so the two cannot drift.

The output it produces is a **named** structure, and that is the same bar an archetype has to
clear: every entry below can be stated in one sentence naming what happens in what order. A shape
you can only gesture at has not been extracted yet.

## Archetypes

Pick the one the material actually supports. Don't force a story into a shape.

1. **Shipped artifact** — a concrete thing you built or shipped. *"I gave the content engine a git-log
   reader. It now writes posts about itself."*
2. **Counterintuitive decision** — a choice that sounds wrong and worked. *"We turned off the
   best-performing part of the pipeline. Throughput went up."*
3. **Named number** — one figure that reframes. *"Four frameworks. One auth wrapper each. We deleted all
   four."*
4. **Receipts first** — lead with the outcome, explain after. The proof is the hook.
5. **Status-quo fault-line** — name the lazy consensus you reject. The "enemy" is **a belief or a default
   way of doing things — never a named company** (see brand-safety below).
6. **Before / after** — the delta, stated as two states.
7. **The concession** — open on the hard part or what didn't work. Credibility through honesty.
8. **Deep-cut insider** — a reference the target audience nods at and everyone else learns from. Earns
   saves from the people you most want.
9. **The stakes** — what breaks if the reader keeps ignoring this. B2B risk framing, stated plainly, not
   as fear-mongering.
10. **Reveal the ending** — name the starting point, give away the outcome, withhold the *mechanism*.
    It reads like giving everything away and does the opposite: it opens a large, specific gap where
    the "how" should be, and recolours every beat the reader then sees, because they now watch each
    one knowing where it lands. Distinct from *receipts first*, which states only the outcome half.
    Strongest on thread openers and video cold opens. *"In March I couldn't get a single reply. By
    June the same list booked eleven calls. The list never changed."*

## Zero-context self-containment

The reader knows nothing about you, your product, or your last post. Every noun in the hook must be
**introduced in that same line**, not referenced. No "the funnel", "they", "it", "the change" pointing at
something the reader was never shown. If the hook only makes sense to someone who already followed the
story, it isn't a hook yet.

## The resonance lens (why they keep reading)

A hook works when it trips at least one of these. Name which one before you write, so the line is built to
pull that lever — not decorated after the fact. B2B-calibrated: credible over provocative.

- **Curiosity gap** — a specific outcome stated, the mechanism withheld.
- **Recognition** — you name something the reader has felt but never seen said plainly. "That's us."
- **Productive discomfort** — a gap between where they are and where they could be, with the exit visible
  so it motivates rather than nags.
- **Aspiration** — a concrete, believable result that makes them think "I could do that", not "must be
  nice".

## Brand-safety (non-negotiable, ties to complementary-positioning)

- The fault-line/enemy is a **status-quo belief or default**, never a named competitor or a company whose
  stack we've analysed. We make a strong stack stronger; we don't throw anyone under the bus.
- **Augmentation framing:** "this helps me do more", never "this replaces X".
- No outrage, no manufactured controversy, no "dangerous to post" register. If a line only works by
  picking a fight, cut it.

## Constraints

- Length: obey the platform lint (LinkedIn hook ≤140 chars **hard**, ≤80 **target**; X first line
  stands alone; Facebook ≤~477 before "see more"). The 140 is the mobile fold — a constraint, not a
  performance number. Measured median engagement falls monotonically with hook length across 309,614
  LinkedIn posts (0–40 chars 2.61% → 200+ 2.08%), so a hook that merely clears the cap at 139 sits in
  the second-worst band. Shorter is a target, never a reason to cut meaning.
- Not a question. One idea. No em dash. Passes `docs/prose-craft.md` (no borrowed-vocabulary words, no
  antithetical parallelism). *The question ban now has outside support: across 31,564 posts, question
  openers rank last of five styles (2.16%). It was house taste; it is also the measured floor.*
- **When the material supports both a story and a result, open on the story.** Story openers rank 1st
  (2.60%) and contrarian 2nd (2.31%); results framing ranks 4th (2.19%), because a bare number is an
  overused shape. A tie-break for step 2 of the Workflow below — not a change to the archetype list,
  and not a licence to drop the number. Lead with why it happened; let the figure land second.

## Workflow

1. Read the evidence pack and the brief's `audience` + `hook_direction`.
2. Produce **3 candidates across distinct archetypes**, each grounded in a real detail from the evidence.
3. Self-check each: zero-context? evidence-grounded? **concrete anchor (number/name/quote/artifact)
   present?** one resonance lever named? passes prose-craft? brand-safe?
4. **Present all 3 as an operator choice** at review — never silently pick one (same pattern as the "3
   title variants" gate in builder-studio).
5. **Anti-repetition:** before finalizing, check `content/<active>/history.jsonl` and recent posts — if
   this archetype+angle shipped recently, vary it. Same voice, new line; not the same three takes on a
   loop.

## The cover frame (when the hook is an image)

A carousel cover, a video's first frame, and a thumbnail all do the line's job in a medium with no
words. Everything above still applies — the gap, the concrete anchor, the filtering — but the
constraint is harder: **a reader decides from the image before they have read anything**, so the
frame has to be *understood*, not just seen.

`carousel-pdf` requires the cover to "pass the 3-second test". This is that test.

**1. Recognition before beauty.** The first question is not "is this striking" but **"does a
stranger know what they are looking at?"** An unfamiliar object photographed well stops nobody,
because there is nothing to recognise. Pair the unfamiliar thing (what the post is *about*) with a
familiar one (what makes it *legible*) and the frame explains itself. If the viewer has to work out
what they are seeing, they have already scrolled.

**2. One subject. At most one supporting object.** Two competing subjects is not twice the interest,
it is nothing standing out. The primary subject carries the recognition; a single secondary object
earns its place only when it supplies the context the primary cannot — the "what is this about"
that turns a generic frame into a specific one. A third element is a subtraction.

**3. Text is a fallback, not the plan.** The strongest covers carry no text at all, because the
image already said it. That is expensive — it means finding a frame that does the whole job — so
text is a legitimate compromise. When you use it, **four words is the ceiling**, and the words must
add what the image cannot rather than caption what it already shows.

**4. One or two colours.** More reads as noise at thumbnail scale, where the frame is competing at
perhaps 5% of the size it was designed at.

**5. Give it one curiosity device, not three.** The reliable ones: a specific human expression
(recognition and curiosity in one element); two objects that do not belong together, so the
relationship is the question; or a pointer — an arrow, a circle — that singles out a detail and
implies the viewer would otherwise miss it. Pick one. Stacking them reads as a channel trying too
hard, which is its own disqualifying signal.

**6. A name is not a hook.** The most common failure on interview and podcast covers: the guest's
name, their title, the episode number. To a stranger those are not information — nobody is drawn in
by a name they do not recognise. Lead with **the claim the conversation produced**, and let the
name be the credential the caption carries. This is the visual restatement of *zero-context
self-containment* above, and it fails the same way.

**When the brand kit forbids faces or text on the cover** — several profiles' `[imagery]` enforcement
does, deliberately, in favour of an abstract treatment — points 3 and 5 lose their easiest tools and
the whole recognition burden falls on point 1. That is a real constraint, not a bug to route around:
resolve it by making the *object* unambiguous, not by quietly reintroducing a face. If the abstract
treatment cannot carry recognition for a given post, that is a finding worth raising with the
operator, not a licence to override the kit.

*Provenance: taxonomy adapted from third-party creator-education material on click-through
composition, restated here in original terms and reconciled with this system's brand constraints.
The underlying claims are directional craft heuristics from a single commercial source, not measured
results from this system — **do not publish a click-through figure sourced from it**, the same
standing the retention rubric's evidence note sets.*
