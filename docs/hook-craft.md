# Hook craft — first lines for organic social

Cross-profile, **de-branded** craft reference for the opening line of an organic post (LinkedIn text,
X, Facebook, carousel cover). Loaded by `content-studio`, `builder-studio`, and `carousel-pdf` at draft
time.

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
