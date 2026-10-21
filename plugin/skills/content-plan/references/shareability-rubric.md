# Shareability rubric — deciding what to lead with

> **Company-neutral.** This is the *mechanism* for turning a pile of weekly signals into a ranked
> shortlist. It carries no company facts. The weights, the pillars, the theme-radar tiers, the
> personas and their fears, and the ownable wedge all come from the **active profile's**
> `knowledge/content-priority.md`. Used by the weekly drafting routine
> (`scripts/prompts/weekly-content-draft.md`) and reusable by `content-plan` / `carousel-auto`.

## The idea

Strong news desks and the best B2B creators do not lead with what is *most true* — they lead with
what is most **worth spreading**: timely, consequential, concrete, and *ownable*. This rubric
fuses the classic **news values** (timeliness, magnitude, prominence, proximity, conflict, human
interest) with what actually makes B2B content get **forwarded and saved** (proof, a
differentiated POV, and practical utility). It **generalizes the scoring table already in**
`plugin/skills/carousel-auto/body_template.md` (Step 2) rather than inventing a parallel system.

## Score each candidate signal

Read every signal in the latest `content/<active>/market-signals/*-signals.md`. Score each on
these nine dimensions (0-2 each unless noted). Higher = leads.

| Dimension | What it rewards | Where the signal comes from |
|---|---|---|
| **Timeliness / velocity** | Breaking or rising *this week*; decays fast if not caught | scan date, "why now", rising interest |
| **Magnitude / impact** | How big — dollar figures, scope, enforcement teeth | numbers in the signal |
| **Prominence** | Named, recognizable players or people | named entities |
| **Proximity / relevance** | Hits the active profile's persona's *live fear or ambition* | profile personas + pillars |
| **Conflict / contrarian tension** | Challenges consensus; has a "villain" (an incident, a failure) | contrarian claim, incident |
| **Proof strength** | Defensible + quotable: real incident, shipped artifact, case study, hard data | evidence available |
| **Ownable POV fit** | Lets the company say something *only it* can (its wedge), not a generic take | profile wedge |
| **Utility / save-worthiness** | A framework / checklist / "what to do now" people bookmark + forward | derivable asset |
| **Novelty (anti-dup), −3 penalty** | Penalize angles already shipped recently | `history.jsonl`, recent `linkedin/`/`articles/` |

Sum the score. Note the natural **format/shape** each high signal implies (from carousel-auto):
clear myth to correct → myth-bust · step-by-step available → how-to · customer proof → case-study ·
broad angle → framework · hard data → data infographic.

## Rank, do not just sort by score

1. **Theme-radar tier is the tie-break.** At equal score, a `LEAD NOW` signal beats `CREDIBILITY`
   beats `DEFINE IT` beats `WATCH` (tiers defined in the profile's `content-priority.md`).
2. **Enforce a pillar / theme spread.** Do not let all 3-5 posts collapse onto one pillar or one
   signal. Aim to cover the profile's active pillars across the week's set.
3. **Match format to shape.** Pick the format each signal scores highest for; do not force
   everything into one format.
4. **Articles vs posts.** The two long-form picks should be the signals with the strongest
   *argument/framework/prediction* potential (high magnitude + ownable-POV + proof), not just the
   two highest raw scores — long-form needs something to *say*, not just report.

## Output

A ranked shortlist. For each pick keep a one-line **"why this scores"** (the top 2-3 dimensions
and the tier). That line becomes **section 2 (Prioritization rationale)** of the artifact's
`.audit.md` — so the call is auditable evidence, never a black box.
