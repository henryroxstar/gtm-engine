# Market Scan — Output Template & Source Reference

Supporting reference for the `market-scan` skill. Contains the canonical output file template (§4) and the content format quick-reference. The SKILL.md contains the full workflow; use this file for the template and the source list when building the weekly output.

---

## Output file template

Save each week's output to: `market-signals/YYYY-WW-signals.md`

```markdown
# {brand_name} GTM — Market Signals — Week YYYY-WW (Mon YYYY-MM-DD)
# (resolve {brand_name} from profiles/<active>/PROFILE.md)

**Generated:** YYYY-MM-DD
**Signal count:** [n H / n M / n L]
**Focus:** [cohorts · personas · geo priority] — source: [config `## ICP Focus` | discovered artifact(s): <name(s)> | operator-resolved conflict]
**Focus split:** [n On-focus / n Adjacent / n Off-focus]
**Priority-geo gaps:** [priority markets that returned nothing this week, or "none"]
**Prospecting feed:** [n regulatory signals flagged for §7.3 hook matrix update]

---

## Top signals this week

### [Signal N — H/M] [Headline]
- **Source:** [Publication | Company blog | Regulator] — [URL] — [Date]
- **What happened:** [2–3 sentences, factual]
- **What it means for us:** [1–2 sentences — competitive impact, outreach angle, or narrative validation]
- **Focus:** [On-focus | Adjacent | Off-focus] · cohort: [cluster or —] · persona: [seat or —] · use-case: [workflow or —]
- **Verbatim quote (if strong):** "[quote]" — [attribution]

[repeat for all H and M signals; omit L unless noteworthy — list On-focus first]

---

## Direct-sales alignment

Which signals feed which part of the current sales motion (from the effective focus, Step 0.5).

| Signal | Focus | Cohort | Persona (seat) | Use-case | Content queued |
|---|---|---|---|---|---|
| [Signal N] | On-focus | [cluster] | [seat] | [workflow] | [post / campaign / brief / none] |

**Off-focus (logged, not drafted):** [signals that were loud but outside the focus — or "none"]

---

## Competitor landscape

| Competitor | Status | Move | Implication |
|---|---|---|---|
| AWS | No change / Moved / Threat | [what shipped] | [1-line implication] |
| Microsoft | … | … | … |
| Google Cloud | … | … | … |
| [others from PROFILE-scaled list] | | | |

---

## Regulatory pulse

| Body | Signal | Enforcement / Deadline | ICP impact |
|---|---|---|---|
| [Regulator] | [filing / paper / advisory] | [date if relevant] | [which ICP accounts this creates urgency for] |

**§7.3 hook updates — paste into prospecting routine before this week's run:**
[List each new hook as a ready-to-paste block, or "None this week"]

---

## Content pipeline — this week's ideas

### LinkedIn posts (draft-ready)

**Post N — [Signal reference]**
> [Full draft — 150–250 words. Ready to publish after colleague reviews.]

[repeat for each draft-ready post]

---

### Campaign idea *(if applicable)*
**Theme:** [Name]
**Trigger:** [Which signals cluster into the theme]
**Arc:**
- Post 1: [topic + angle]
- Post 2: [topic + angle]
- Post 3: [topic + angle]
**Cadence:** Mon / Wed / Fri, week of [date]
**Status:** Idea — needs approval before drafting

---

### Blog post brief *(if applicable)*
**Title options:**
1. [Option A]
2. [Option B]
3. [Option C]
**Angle:** [2 sentences]
**Unique claim:** [What we're asserting that others aren't saying]
**Outline:** [5–7 bullets]
**Proof points:** [2–3 citations]
**Status:** Brief — full draft on request

---

### Carousel / visual concept *(if applicable)*
**Title:** [Name]
**Frames:** [6–8 bullet outline, one bullet per frame]
**Data sources:** [What to pull]
**Production:** pptx skill / Canva / image generation
**Status:** Concept — visual brief before production

---

### Technical POC post *(if applicable)*
**Title:** [Name]
**Business hook:** [1 sentence]
**Technical angle:** [What to build/show]
**Compliance tie-in:** [1 sentence]
**Estimated build:** [hours / days]
**Status:** Sprint item — not this week

---

## Carry-forward from last week
[Unbuilt ideas from last week's scan — blog brief, POC idea, campaign — with updated priority.
Park anything > 30 days old without action.]
```

---

## Source list quick reference

### 1A — News & developer sources

The news & developer source list (publications, communities, developer feeds) and its query
templates are **company config** — read them from `profiles/<active>/knowledge/market-scan-config.md`
(§1A), scaled per PROFILE `target_markets`.

### 1B — Competitor core list

The competitor watchlist (core list + market-scaled additions) is **company config** — read it from
`profiles/<active>/knowledge/market-scan-config.md`, scaled per PROFILE `target_markets`.

Search pattern per company:
```
"{company}" agent OR agentic OR "agent identity" OR governance site:techcrunch.com OR site:venturebeat.com
```
Also check the company's official blog / newsroom directly.

### 1C — Regulatory bodies

The regulatory & standards bodies list (always-scan + market-scaled regulators) is **company config** —
read it from `profiles/<active>/knowledge/market-scan-config.md`, scaled per PROFILE `target_markets`.
Add the relevant regulator for any market in the colleague's PROFILE that the config does not yet list.

---

## Content format cheat-sheet

| Format | When | Output now | Build later |
|---|---|---|---|
| LinkedIn post | Any H-signal | Full draft (150–250 w) | — |
| Campaign idea | 2–3 signals cluster around a theme | Name + 3-post outline | Posts (after approval) |
| Blog post brief | H-signal with structural/regulatory angle | Title options + outline + unique claim | Full draft (on request) |
| Carousel concept | Signal lends itself to data viz or comparison | Frame outline + data sources | Visual brief → production |
| Technical POC | H-signal with MCP/developer angle | Title + hook + code idea + est. build time | Sprint item |
