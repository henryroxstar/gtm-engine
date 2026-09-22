# Battlecard

Build a one-page competitor battlecard a seller can hold during a live call: where they genuinely
win, where we do, where we sit alongside rather than instead, the trap questions, and the claims we
must not make. Built from the profile's competitive-positioning pack — **a competitor with no entry
there gets a card that says so**, not one assembled from recollection.

---

## Step 1 — Load context

1. **PROFILE** — `profiles/<active>/PROFILE.md`: `name`, `brand_name`, `language`.
2. **`profiles/<active>/knowledge/competitive-positioning.md`** — the pack. This is the source.
3. **`profiles/<active>/knowledge/product.md`** — our own capabilities and their maturity tags.
4. **`profiles/<active>/knowledge/case-studies.md`** — where a head-to-head is actually evidenced,
   versus where we are reasoning from the datasheet.
5. **`profiles/<active>/knowledge/competitors.toml`** if the profile ships one.

**If the competitor has no entry**, produce the card's skeleton with every row marked *no entry*,
plus the questions whoever owns the pack needs to answer. Do not fill the rows from general
knowledge: a competitive claim that turns out to be a year out of date is how a seller loses a room
they were winning, and the buyer has usually used both products.

## Step 2 — Write "where they win" first

Not as a courtesy — as the test of whether the card is usable. A card with no honest "where they
win" row is a brochure, and the first buyer who has used both will know within a sentence.

Writing it first also produces the most valuable row on the card: the cases where the honest
recommendation is *them*. A seller who can say that out loud is believed on everything else.

## Step 3 — Then where we win, and where we sit alongside

- **Where we win** — the reason, never the adjective. "Better governance" is not a differentiator;
  "policy is evaluated per request rather than at deploy time, so a revoked credential stops the
  next call" is. If the reason cannot be stated in one mechanism sentence, it is not a
  differentiator, it is a preference.
- **Overlap** — what both genuinely do. Naming it is what makes the rest credible.
- **Complement** — where we sit alongside rather than instead. Usually the most useful row on the
  card, and almost always the one that survives the call where the competitor is already installed
  and nobody is ripping it out.

## Step 4 — Trap questions and the questions worth asking

**Trap questions** — the ones where a careless answer loses the deal: *"isn't this just `<them>`
with extra steps?"*, *"they say they already do this"*, *"why would we add a second vendor?"* Write
the answer we actually stand behind, in the seller's own voice, short enough to say.

**Questions worth asking the buyer** — not attacks; questions that surface the real requirement.
The strongest are the ones whose answer is useful to the buyer whichever product they pick.

## Step 5 — What we must not claim

The section that keeps the card safe to hand to a new seller:

- Comparisons that are out of date, unprovable, or legally risky.
- Any claim that the competitor *lacks* a certification, control or capability. State what we hold;
  never assert what they do not — we cannot see their evidence, and being wrong about it is both a
  credibility loss and a legal exposure.
- Anything sourced from a private conversation, a shared customer, or a prior employer.
- Any of our own capabilities tagged CONDITIONAL or ROADMAP, presented as shipped
  (`docs/product-accuracy.md`).

## Step 6 — Output

Save as **`battlecard-[competitor]-[YYYY-MM-DD].md`** in `content/<active>/battlecards/` — this is
a **competitor** artifact, reusable across accounts, not an account deliverable, so it does not go
in an account folder. Use `python -m gtm_core.slugify "<competitor name>"` for the slug; never
hand-kebab-case it.

One page, in this order: **what they are (as they describe themselves)** · **where they genuinely
win** · **where we win, with the mechanism** · **overlap** · **complement** · **trap questions and
answers** · **questions worth asking** · **must not claim** · **last verified, with sources**.

Then append the `⟦FILE:…⟧` sentinel with the real absolute path.

## Guardrails

- **Never trash their solution.** A seller who runs a competitor down tells the buyer their
  shortlist showed poor judgement, and that we would talk about *them* this way to the next
  account. Complementary framing wins more rooms and survives the installed-base call.
- **No entry, no card.** Produce the skeleton and the questions instead; say plainly that is what
  you did.
- **Never assert what a competitor lacks.** State what we hold.
- **Every competitive fact carries a date and a source.** An undated one is unverified: a feature
  gap closed six months ago and quoted on a call is the fastest way to lose the room.
- **Read-only.** Writes a card; contacts nobody.
