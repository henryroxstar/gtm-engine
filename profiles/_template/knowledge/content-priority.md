---
source: manual
refreshed: 2026-07-19
review: 90d
---
# Content Priority — editorial map

> Read by the generic **shareability rubric**
> (`plugin/skills/content-plan/references/shareability-rubric.md`) and the weekly drafting routine
> (`scripts/prompts/weekly-content-draft.md`). The rubric is company-neutral; this file supplies
> the company facts it scores against. Content pillars mirror `PROFILE.md` (`content_pillars`) —
> PROFILE wins on conflict. Fill every `<…>` before the first drafting run.

## Content pillars

- **<Pillar 1>** — <one line: what this pillar is about>.
- **<Pillar 2>** — <one line>.
- **<Pillar 3>** — <one line>.

**Wedge (say this, not a generic take):** <the one thing only this company can claim — the
differentiated point of view every piece should ladder up to>.

## Theme radar (tie-break tier for the rubric)

At equal shareability score, higher tier leads. LEAD NOW > CREDIBILITY > DEFINE IT > WATCH.

| Tier | Theme | Why |
|---|---|---|
| **LEAD NOW** | <theme to lead with now> | <why it is timely + ownable> |
| **CREDIBILITY** | <theme that builds authority> | <why it earns trust, not differentiation> |
| **DEFINE IT** | <whitespace theme only you can name> | <the term you want to own> |
| **WATCH** | <early theme; monitor, do not lead> | <why it is too early> |

## Planning axes — journey × goal × cadence

> Read by `content-plan` alongside the pillar spread it already enforces. The **vocabularies below
> are fixed by the engine** (they are enums in `schemas/content-item.schema.json`); what a profile
> supplies is the **mix**. Delete this whole section to keep the previous behaviour — planning falls
> back to pillar-spread-only and the documented default cadence.

### Goal mix (quota across the week's set)

The quota exists because **these goals trade off against each other on purpose**: reach content is
*supposed* to convert poorly, and conversion content is *supposed* to underperform on reach. Without
a declared mix, any scoring rubric drifts monotonically toward whichever metric it is fed, and the
portfolio quietly collapses onto one goal.

| Goal | Optimizes for | Share of the week |
|---|---|---|
| `reach` | New audience — first-time viewers, follows | <e.g. 50%> |
| `engagement` | Depth with the existing audience — replies, saves, shares | <e.g. 30%> |
| `conversion` | Action — installs, signups, demos, replies to a CTA | <e.g. 20%> |

Shares are a target for the *set*, not a rule per item; state them so a plan can be checked against
them. A launch week and a steady-state week usually want different mixes.

### Journey mix

Where the reader is, which is **not** the same question as what the item optimizes for — a
`conversion`-stage reader can be served a `reach`-goal item. The two axes share the word
"conversion" by industry convention; keep them distinct when planning.

| Stage | Serves | Share of the week |
|---|---|---|
| `awareness` | Doesn't know the problem is theirs yet | <e.g. 50%> |
| `consideration` | Knows the problem, weighing approaches | <e.g. 35%> |
| `conversion` | Choosing — needs proof, specifics, a reason now | <e.g. 15%> |

### Cadence

Replaces the hardcoded "3–5 items per week" assumption. Omit a platform to not post there.

| Platform | Posts / week | Notes |
|---|---|---|
| `linkedin` | <e.g. 3> | <lead formats, best slots — detail lives in social-tuning.md> |
| `x` | <e.g. 5> | <…> |
| `instagram` | <e.g. 2> | <…> |

- **Fan-out width:** <how many format/platform variants one approved angle may spawn — the
  concurrency knob for the `creator` pack's render stage. Start at 1–2; every variant is a paid
  render.>
- **Sustainability check:** the cadence must be what the operator can actually review at the gates.
  A cadence nobody approves is a backlog, not a plan.

## Personas + live fear/ambition (proximity/relevance axis)

Score "proximity" against whether a signal hits one of these. Emphasize the personas listed in
`PROFILE.md` (`emphasize_personas`).

- **<Persona 1>** — fear/ambition: <what keeps them up / what they want>. Opener that lands: <angle>.
- **<Persona 2>** — fear/ambition: <…>. Opener: <…>.
- **<Persona 3>** — fear/ambition: <…>. Opener: <…>.

## Ownable segments (the wedge in concrete form)

Signals touching these are high ownable-POV fit — <the specific segments / use cases where this
company wins and competitors cannot follow>. Verticals: <…>. Geo: <…>.

## Publishing identity + guardrails

- **Posts publish under <person or brand>** (see `knowledge/social-tuning.md`).
- Voice per `knowledge/voice.md`; honor `knowledge/voice-bans.txt`; reduce em-dashes. No links in
  the LinkedIn body (first comment instead); ≤2 hashtags.
- Verify named people, quotes, and external stats against a fresh source before shipping under a
  real identity.
