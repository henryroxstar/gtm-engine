# Product-accuracy discipline (shared)

> **One home for a cross-cutting rule.** Any skill that puts a claim about the active company's **product
> capability**, or a **cited external fact**, into a deliverable that goes out under a real person's or the
> company's name references this doc — it is not a per-skill rule. Skills link here; they never restate the
> taxonomy. Consumers include `linkedin-reply`, `solution-design`, `build-deck`, `deck-research`,
> `account-dossier`, `call-prep`, `content-studio`, `draft-outreach`, `email-sequence`, `market-scan`,
> `reddit-reply`, `campaign-plan`, `voice-of-customer`, `airq-scan`, `gateway-runbook`,
> `carousel-visuals`, `infographic-data`, `infographic-handwritten` — any skill that makes a capability
> claim, **whether the claim ships as prose or as text baked into a generated image**. Owns the SHIPPED /
> CONDITIONAL / ROADMAP taxonomy and the verify-before-it-ships rule.

## Why this exists

The costly errors here are not voice errors — they are **accuracy** errors:

- stating a capability that is **roadmap or conditional** as if it ships — the reader diffs the datasheet
  against reality and then discounts everything else you said;
- **inheriting a wrong premise** from the counterparty — a real incident, standard, or statistic described
  the wrong way — and building on it under our name.

A technical evaluator forgives *"here is exactly where the shipped line ends."* They do not forgive being
sold past it. Precision **is** the persuasion — volunteering the boundary reads as rigor, not weakness.

## The three states — tag every product-capability claim before it ships

Every claim that the active company's product *does* something gets one tag, sourced to a profile
product/reference doc, and the tag is carried **into the copy** — not kept in your head:

| Tag | Means | How it must read in the deliverable |
|---|---|---|
| **SHIPPED** | live + verifiable today (doc pointer / demonstrable on the instance) | state it plainly |
| **CONDITIONAL** | ships, but only under a config or precondition | name the condition ("once an identity is configured…", "when the deployer enables it") |
| **ROADMAP** | designed / in-implementation, not wired | "in build" / "the design we're moving to" — never as live |

"The docs mention it" is **not** a SHIPPED tag. The single recurring failure is stating a CONDITIONAL or
ROADMAP capability as flatly live. When unsure, drop to the weaker tag and say so.

## Treat the product docs as possibly stale

Profile product/reference docs carry their own status tags (VERIFIED / DESIGN-TARGET / SHIPPED / ROADMAP —
e.g. a product's `architecture-faq`-style claim-status table, or a design-pillar crosswalk that maps each
concept to shipped-vs-roadmap). They drift **in either direction**: a shipped feature still
marked roadmap, or a roadmap one written up as shipped. So for a **load-bearing** claim in a technical or
customer-facing deliverable, **re-verify against the current product docs / code / live UI before stating
it live.** A tag that was VERIFIED months ago is a hint, not proof. When you find drift, **fix the doc**
(one home per fact) — don't just work around it in the deliverable.

## Verify cited facts — theirs and ours

Any external **incident, standard, statistic, or number** — named in the source (a prospect's post, a
brief, a competitor claim) or going into our deliverable — is verified before we build on it. Do not
inherit the counterparty's framing of a real fact. A precise, credited correction reads as rigor.

## Generated images carry the same discipline as prose

A headline, sub-line, chip, or callout baked into a rendered carousel card or infographic **is a
product-capability claim**, exactly like a sentence in a LinkedIn post — the reader can't tell it
came from an image-generation prompt instead of a paragraph. The taxonomy applies **before the
generation call**, not after the pixels come back:

- Tag every mechanism/protocol/feature named in the card copy SHIPPED / CONDITIONAL / ROADMAP
  *while drafting the prompt* — the same re-verify-against-current-docs step as prose, not a vibe
  check against memory (memory drifts; see "Treat the product docs as possibly stale" above).
- A ROADMAP claim needs its honest boundary **on the card itself** — a visible tag ("roadmap",
  "preview", "where this is headed"), not only in the caption or a footnote the reader won't see
  at carousel size. A caveat that lives only in the post text doesn't travel with a re-shared image.
- The per-skill "does the rendered text match the spec" accuracy check (`carousel-visuals`,
  `infographic-data`, `infographic-handwritten`) verifies **legibility**, not **truth** — it catches
  a misspelled word, not an overclaimed mechanism. Both checks are required; neither substitutes
  for the other.
- Incident: a LinkedIn carousel shipped cards claiming a dry-run/rehearsal consent mechanism was
  demonstrable and TSP moved proof across any network live — both were roadmap (one had zero code
  presence). Caught by an external repo audit after publish-ready, not before. Don't repeat this —
  run the tag pass on the card copy before the first paid render, every time.

## The check (run before a deliverable is called done)

- [ ] Every product-capability claim tagged **SHIPPED / CONDITIONAL / ROADMAP**, with a source doc —
      including claims baked into a generated image, checked before the render call.
- [ ] CONDITIONAL claims name their condition; ROADMAP claims say "in build" — neither reads as live.
      For an image, the ROADMAP tag is visible on the card, not only in the surrounding caption.
- [ ] Load-bearing claims **re-verified** against current docs / code / UI, not an old tag.
- [ ] Every cited external fact (incident / standard / stat / number) verified, not inherited.
- [ ] Where a claim can't reach SHIPPED, the copy states the **honest boundary** rather than overclaiming.

## Where product truth lives

`profiles/<active>/products/<slug>/` (PRODUCT.md + `references/`) and the profile knowledge pack. Those
docs' own status tags are the source of truth; **this doc governs how a *deliverable* uses them.** Resolve
per-product knowledge via `python -m gtm_core.resolve_knowledge <file> --profile <active> --product <slug>`.
