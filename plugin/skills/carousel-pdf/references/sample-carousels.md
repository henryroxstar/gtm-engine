# Sample Carousel Library

Four pre-built arc shapes. Each is a complete 9-card script template with hook options, per-card copy guidance, and suggested trigger word (used only in the lead-magnet close). Fill in the `[brackets]` from the knowledge pack for the topic at hand.

Use these as starting points — adapt card count (8–10), swap layouts, or extend the middle if the topic needs depth.

**Momentum rules (all shapes — playbook §5):** ≤50 words per card; every middle card ends with a
forward pull (open loop, transition phrase, or visible sequence number); the card-4/5 slot is the
mid-deck re-hook — put the deck's most surprising beat there. Card 9 is a CLOSE: recap + ONE ask +
identity block by default; the "Comment [TRIGGER] → DM" close only when the colleague opts into
lead-magnet mode (playbook §2).

---

## Shape 1 — Myth-Bust

**When to use:** Correcting a widespread misconception about agentic AI, identity, or governance. Performs well with technical and security audiences.

**Trigger word:** `TRUST` or `AGENTS`  
**Best theme:** Dark (contrarian energy)  
**Page value:** `fabric`

### Hook options (pick one, show to colleague)

```
Option A (Factual gap):
"[Common tool/practice] can't [thing everyone assumes it can]. Yet."
e.g. "OAuth can't govern AI agents. Yet."

Option B (Contrarian):
"Everyone is [doing X]. Nobody is [doing the thing that actually matters]."
e.g. "Everyone is adding guardrails. Nobody is adding identity."

Option C (Numbered promise):
"[N] things your [topic] gets wrong about [subject]."
e.g. "4 things your AI security team gets wrong about agent identity."
```

### Arc

```
Card 1  HOOK     [Chosen hook — specific, provable, 4–6 words in h1]
Card 2  MYTH 1   "Most people think [X]." — state the myth clearly
Card 3  REALITY  "Here's what's actually true: [Y]." — the correction
Card 4  STAT     Hard evidence that supports the reality (from profiles/<active>/knowledge/)
Card 5  MYTH 2   A second, related misconception (optional — can skip to Bridge)
Card 6  BRIDGE   "The real question isn't [myth]. It's [reframe]."
Card 7  SOLUTION [Product answer — one feature, one sentence]
Card 8  QUOTE    Verbatim customer voice that validates the reality (case-studies.md)
Card 9  CLOSE    Recap + ONE ask (save/follow/question) + identity — or "Comment [TRIGGER] → I'll DM you [what they get]" in lead-magnet mode
```

### Copy notes

- Name the myth explicitly on Card 2 — don't hedge. "Most teams assume..." is weak. "The default assumption is..." is stronger.
- Card 4 stat: if no hard number exists, use a qualitative authority claim: "Every enterprise AI governance framework reviewed by [body] identified [gap] as the #1 unresolved risk."
- Card 6 Bridge: the reframe should feel like a unlock, not a gotcha. "It's not about blocking agents — it's about knowing which agent did what."

---

## Shape 2 — How-To

**When to use:** Explaining a concrete process, integration pattern, or implementation approach. Works for developer, architect, and ops personas.

**Trigger word:** `GATEWAY` or `AGENTS`  
**Best theme:** Light (procedural clarity) or Dark (technical depth)  
**Page value:** `fabric` or `website` (multi-product)

### Hook options

```
Option A (Numbered promise):
"[N] steps to [outcome] without [common pain]."
e.g. "3 steps to add verifiable identity to any AI agent — no custom auth."

Option B (Factual gap):
"There's no standard way to [thing]. Here's how to build one."
e.g. "There's no standard way to audit an AI agent's decisions. Here's how to build one."

Option C (Contrarian):
"[Teams] are [doing X the hard way]. The [easy way] is [Y]."
e.g. "Teams are building agent auth from scratch. The fast path is [flagship product]."
```

### Arc

```
Card 1  HOOK     [Chosen hook]
Card 2  PROBLEM  Why the current approach is broken / why this is hard today
Card 3  STEP 1   First step — brief, concrete, one action
Card 4  STEP 2   Second step
Card 5  STEP 3   Third step (add Step 4 card if needed; max 4 steps total)
Card 6  RESULT   "When you're done, you get: [outcome A], [outcome B], [outcome C]"
Card 7  PROOF    How a real customer/use case used this pattern (case-studies.md shape)
Card 8  QUOTE    Verbatim customer voice (case-studies.md)
Card 9  CLOSE    Recap + ONE ask + identity — or "Comment [TRIGGER] → I'll DM you [guide / checklist / diagram]" in lead-magnet mode. How-to decks are save-magnets: "Save this for your next [task]" is the natural default ask.
```

### Copy notes

- Step cards (3–5) use `carousel-point` with `accent: true` — the left gradient bar signals progression.
- Each step card: one h2 (the step name) + one short paragraph (what you do and why). No sub-bullets — one idea per card.
- Card 6 Result: use 3 parallel outcomes ("You get: governance baked in. An immutable audit trail. Zero custom auth code."). The parallelism lands.
- If the how-to is a developer integration, the `GATEWAY` trigger word outperforms `TRUST` for click-through.

---

## Shape 3 — Case Study

**When to use:** Leading with customer proof to build credibility before the product pitch. Works especially well post-event, when publishing a case study, or for accounts/industries that respond to evidence.

**Trigger word:** `PROOF` or `TRUST`  
**Best theme:** Dark (authority)  
**Page value:** Match the customer's product shape (see product → page mapping in SKILL.md)

### Hook options

```
Option A (Factual gap — customer-led):
"[Customer] had [specific problem]. They solved it in [timeframe]. Here's how."

Option B (Outcome-first):
"[Metric or outcome]: that's what [customer/use case] achieved with [product]."
e.g. "Zero identity incidents across 40,000 agent calls: that's what [customer]'s integration delivered."

Option C (Contrarian):
"[Industry] kept [doing X the old way]. [Customer] didn't."
```

### Arc

```
Card 1  HOOK     [Chosen hook — outcome or problem in h1]
Card 2  CONTEXT  Who the customer is, what they were trying to do (one sentence each)
Card 3  PROBLEM  The specific pain they had — quantified if possible
Card 4  STAT     The scale of the problem or the outcome metric (from case-studies.md)
Card 5  APPROACH What they did — the company's solution shape they used (one sentence per element)
Card 6  RESULT   What changed — before/after or three outcomes
Card 7  LESSON   The transferable principle: "What [customer] learned applies if you [condition]"
Card 8  QUOTE    Verbatim quote from case-studies.md for this customer
Card 9  CLOSE    Recap + ONE ask + identity — or "Comment [TRIGGER] → I'll DM you the full [case study / breakdown]" in lead-magnet mode
```

### Copy notes

- Use the exact customer names and proof points from `profiles/<active>/knowledge/case-studies.md` — never paraphrase or invent.
- Card 2 context should be 2 sentences max: "They do X. They needed Y."
- Card 7 Lesson is the most reusable card — make it abstract enough that a reader in a different industry recognises themselves. "What [customer] learned applies to any regulated workflow where multiple parties need to verify the same fact."
- If the colleague's market differs from the case study's market (e.g., US colleague posting an SG case study), add a localisation note on Card 7: "This pattern applies across markets where [condition] holds."

### Case study selection (from `profiles/<active>/knowledge/case-studies.md`)

Read the available case studies from `profiles/<active>/knowledge/case-studies.md`. For each, note the customer's vertical and workflow shape (e.g. platform builders / vertical SaaS, regulated data exchange / payments / fintech, healthcare / cross-org data sharing, multi-party workflows / supply chain). Pick the case study whose shape is closest to the audience's industry, not the audience's geography.

---

## Shape 4 — Framework

**When to use:** Positioning the company's thinking on a bigger topic — agentic AI governance, trust infrastructure, machine identity. Drives follows and saves; performs well for thought-leadership audiences.

**Trigger word:** `FABRIC` or `TRUST`  
**Best theme:** Light (intellectual authority) or Dark (gravitas)  
**Page value:** `fabric` or `website`

### Hook options

```
Option A (Framework promise):
"The [N]-part framework for [topic]."
e.g. "The 4-part framework for governing AI agents at enterprise scale."

Option B (Contrarian setup):
"[Everyone is talking about X]. The [real conversation] is about [Y]."
e.g. "Everyone is talking about AI guardrails. The real conversation is about agent identity."

Option C (Factual gap):
"[Field/industry] doesn't have a [framework/standard] for [topic]. So we built one."
```

### Arc

```
Card 1  HOOK       [Chosen hook]
Card 2  SETUP      "Before we get to the framework: here's why the old approach breaks."
Card 3  PILLAR 1   [First pillar name] — one sentence definition, one sentence why it matters
Card 4  PILLAR 2   [Second pillar name]
Card 5  PILLAR 3   [Third pillar name]
Card 6  PILLAR 4   [Fourth pillar] (optional — drop if 3 pillars is cleaner)
Card 7  HOW        How the company's product embodies this framework — one layer per product
Card 8  PROOF      A customer that ran this framework in production (case-studies.md)
Card 9  CLOSE      Recap + ONE ask + identity — or "Comment [TRIGGER] → I'll DM you the full [framework / cheat sheet]" in lead-magnet mode. Frameworks drive saves and follows — "Follow for the next framework" is a natural default ask.
```

### Copy notes

- Pillar cards (3–6) use `carousel-point`. Keep each pillar to: h2 (pillar name) + 1 sentence (what it means) + 1 sentence (why without it, things break). No more.
- Card 7 How: match each pillar to a product capability from `profiles/<active>/knowledge/product.md`. Don't invent. e.g. "Pillar 1 (Identity) → [flagship product] identities. Pillar 2 (Policy) → [platform product] attestations." (use the active company's real product names from the knowledge pack).
- The framework should feel like it was derived from first principles, not reverse-engineered from a product spec sheet. Write Card 2 (setup) before writing the pillars — the setup reveals why the old categories don't work, and the pillars are the new categories.
- Good frameworks are named. If the topic warrants it, give the framework a proper name on Card 1: "The Agent Trust Stack." Naming it makes it quotable.

---

## Choosing a shape

| Signal | Best shape |
|---|---|
| Audience is skeptical of the company | Myth-bust or Case study |
| Audience wants to take action this week | How-to |
| Audience just attended a conference or event | Case study |
| Colleague is establishing thought-leadership | Framework |
| Post tied to a market-scan H-signal | Myth-bust or Framework |
| Post for a technical persona (engineer, architect) | How-to or Myth-bust |
| Post for a commercial persona (CISO, CDO, CFO) | Case study or Framework |
| Colleague has a great customer quote | Case study |
| Topic is brand-new (regulatory change, product launch) | Framework |
