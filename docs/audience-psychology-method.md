# Audience-psychology method

Cross-profile, **de-branded** method for building a profile's
`knowledge/audience-psychology.md` — the psychological layer that sits underneath `icp-personas.md`. The
personas file says *who the buyer is and what they need* (Pain·Claim·Gain). This method excavates *what
they feel, believe, and fear* — the layer that decides whether a post lands or bounces — and then filters
it for what the company can **credibly** say.

This doc is the reusable *how*. The output is per-profile and lives at
`profiles/<active>/knowledge/audience-psychology.md` (product-overridable, resolved via
`python -m gtm_core.resolve_knowledge`). `profile-onboard` scaffolds it for every new profile.

## The hard rule: anchor to source, never invent

Every line in an audience-psychology file must trace to a real source — `icp-personas.md`, `company.md`,
`case-studies.md`, or a cited market fact. **No invented emotions, no manufactured vulnerability.** If the
source material is thin on a persona, say so ("thin — needs a real customer conversation") rather than
guessing a feeling. Fabricated psychology is worse than none: it's wrong, it reads as presumptuous, and
for a security/compliance brand it's a credibility risk. This is the discipline that keeps the layer
honest.

## Five excavation targets (per persona)

Push past the surface restatement of the persona's stated pain. For each persona capture:

1. **Emotional stakes** — what this person is actually afraid of, usually reputational or personal, not
   the tidy business pain. Not "the CISO faces audit pressure" but "the CISO fears being the name on the
   incident when an agent does something nobody authorised." Anchor: the persona's role + the real risk in
   `company.md`.
2. **Believed but never said** — the conviction they hold privately and won't state, because saying it
   feels risky or like an admission. Highest-value target: naming it makes a reader feel seen. Anchor:
   the tension between the persona's goal and the problem the product solves.
3. **The contrarian thesis that reframes them** — the company's genuine non-obvious belief that changes
   how this persona sees their own problem. Drawn **verbatim in spirit from `company.md`**, never a new
   position invented here.
4. **Reasoning style** — how they actually decide: control/trust (security frame), cost/benefit (finance
   frame), or speed/risk (builder frame). Each persona needs a different path to the same conclusion.
5. **Proof as belief-shift** — the customer's *change of mind*, not just the outcome. "They thought
   compliance was a cost centre; they found it was a moat; that's what moved the deal." Anchor:
   `case-studies.md`.

## The founder-fit filter (what we're allowed to say)

An excavated truth is only usable if the company can **credibly own it**. A powerful insight the company
can't stand behind is a trap — it forces off-positioning or a claim we can't defend. Run each angle
through four quick tests, then tag it:

- **Expertise** — can we speak to this *as the expert*, from real domain/evidence, not just an opinion?
- **Understanding** — does engaging it prove we genuinely get this buyer?
- **Value** — does the reader leave better off, before any pitch?
- **Association** — does owning this reinforce our actual positioning and what we sell, or train the
  audience to think of us for the wrong thing?

Tag: **Strong** (passes all four — priority) · **Partial** (usable with a stated constraint) ·
**Do-not-drive** (true about the audience but off-positioning or beyond our credible expertise — recorded
so the writer isn't tempted by it).

This filter is where the repo's standing guardrails become operational: **complementary-positioning**
(never win by attacking a named vendor), **verify-against-canonical-refs** (facts come from the company's
own canonical docs, not propagated sibling-doc errors), and the profile's **cert posture** (state only
certifications the company actually holds — never imply one it doesn't, e.g. don't claim SOC 2 when the
profile lists only ISO 27001).

## Output shape (per persona block)

```
### <Persona name / title>
Emotional stakes: <one line, anchored>
Believed but never said: <one line>
Contrarian thesis that reframes them: <from company.md>
Reasoning style: control-trust | cost-benefit | speed-risk
Proof as belief-shift: <from case-studies.md, or "thin — needs a real story">
Founder-fit angles: <Strong: … · Partial (constraint: …): … · Do-not-drive: …>
```

## Who reads the output

- **content-plan** — shapes `brief.angle` to hit a truth the persona *feels* and the company can own; the
  founder-fit tag becomes part of the Gate-1 brief.
- **builder-studio** Step 0 — loads it beside `icp-personas.md` to aim each asset at the segment.
