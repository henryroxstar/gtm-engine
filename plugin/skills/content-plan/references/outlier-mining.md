# Outlier mining — reading what already worked, without copying it

> **Company-neutral.** This is the *mechanism* for extracting a reusable STRUCTURE from pieces
> that outperformed their own baseline. It carries no company facts and names no creator or
> channel. What counts as an outlier for a given account comes from the **active profile's**
> `knowledge/content-priority.md`. Sibling of `retention-rubric.md` and `shareability-rubric.md`,
> which judge an idea; this one supplies the shape an idea gets poured into.

## The idea

A rubric can tell you whether an idea is worth making. It cannot tell you what SHAPE to make it
in — and shape is most of why two pieces about the same thing perform ten to one apart. Outlier
mining answers the shape question from evidence rather than from taste: find the pieces that beat
their own baseline, work out what they have in common structurally, and reuse that.

The output is **one named structure**, and the name matters. "Something punchy with a hook" is not
a structure; *"objection first, then the concession, then the number that settles it"* is. A
structure you can name is one the script stage can be held to and the brief can record as
`outlier_structure`; a structure you can only gesture at is a preference wearing a rubric's
clothes.

---

## Step 1 — select by RATIO, never by absolute reach

An outlier is a piece that beat **its own channel's baseline**, not a piece with a big number.
This is the step that goes wrong most often, and it goes wrong in a way that feels like rigour.

A large account's median post out-reaches a small account's best post by an order of magnitude. Sort
by absolute views and you get a list of large accounts — you have measured audience size, which you
already knew and cannot copy. Sort by ratio-to-baseline and you get a list of pieces that did
something their own audience did not expect, which is the only thing in the data that is about the
work.

So: for each candidate, take the channel's own median over a recent window, and rank by
`piece / median`. Three to five outliers is the working set. Fewer than three and you are reading
one accident; many more and you are averaging away the thing you came for.

**Two selection traps.**

- **A piece can beat its baseline for reasons that do not transfer.** A platform test, a
  collaboration, an external link, a moment in the news. Check for an obvious external cause before
  concluding the structure did it, and drop the piece if you find one — a structure credited with
  someone else's traffic is worse than no structure.
- **Recency is not performance.** A piece published yesterday has not finished accumulating. Use a
  window that lets every candidate finish, or the ratio measures publication date.

## Step 2 — extract STRUCTURE, and only structure

For each outlier, write down four things and nothing else:

1. **Beat order.** What happens first, second, third. Not what is said — what KIND of thing is
   said. "Objection, concession, number" is beat order; "he says the tool is expensive" is content.
2. **Hook shape.** What the first beat does mechanically: contradicts an assumption, states a
   number, poses a question, shows an end state before its cause.
3. **Payoff placement.** Where the thing the viewer came for actually lands — and how much is
   withheld before it. This is usually the sharpest difference between an outlier and a median
   piece, and the one most often mistaken for "pacing".
4. **Cover shape.** What the thumbnail or first frame promises, and how that promise relates to
   the payoff. A cover that promises the payoff directly and a cover that promises the QUESTION
   are different structures with different retention curves.

## Step 3 — the prohibition

**Lift the structure. Never lift the content.** No phrasing, no example, no specific claim, no
visual composition, no sequence of words. If a sentence from an outlier could be recognised in the
output, the line has been crossed.

Two reasons, and the second is the one that bites.

It is somebody's work, and taking it is taking it. That would be sufficient on its own.

But it also does not function. A structure works because it fits an argument the audience has not
heard settled; the borrowed content arrives already settled, so the shape has nothing to do. The
result reads as derivative precisely where it was meant to read as inevitable — the audience
recognises the frame and discounts the piece. Every piece of this that has been tried has produced
something worse than an original piece with a worse structure.

The test to apply before writing: **could this outlier be swapped for a different one on the same
structural notes without changing a line of the output?** If not, content has come across.

## Step 4 — name it, and hand it on

Write the structure as one sentence naming its beats in order, and record it as the brief's
`outlier_structure` decision (`gtm_core.creator_brief`, decision 2). That is the field
`video-script` reads, so a structure that never reaches the brief never reaches the script.

Record alongside it **how many outliers it came from and over what window** — a structure derived
from three pieces in a fortnight and one derived from five across a quarter are different
strengths of claim, and the difference is invisible once only the sentence survives.
