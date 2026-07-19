# Carousel Script Conventions

Copy rules and arc patterns for writing carousel scripts before rendering. Platform mechanics
(retention curve, CTA compliance, caption/hashtag facts) live in `platform-playbook.md` — this
file is the writing companion.

---

## The arc (always this order)

```
Card 1  HOOK     Earn the swipe. Promise the insight. 3-second test.
Card 2  PROBLEM  The specific pain — one owner persona.
Card 3  RISK     The consequence if nothing changes.
Card 4  STAT     Hard evidence from profiles/<active>/knowledge/. Never invented. Doubles as the mid-deck re-hook.
Card 5  BRIDGE   Reframe: "This isn't X, it's Y."
Card 6  SOLUTION Product answer — feature name + one sentence.
Card 7  HOW      Mechanism — one proof point or detail.
Card 8  QUOTE    Verbatim customer voice from case-studies.md.
Card 9  CLOSE    Recap + ONE ask + identity block (default) — or Comment TRIGGER → deliverable (lead-magnet opt-in).
```

Minimum 8 cards, maximum 10. Extend between BRIDGE and SOLUTION if the topic needs more depth.

---

## Swipe momentum — every card earns the next

- Middle cards (2–8) each end with a **forward pull**: an open loop ("That's not the worst
  part."), a transition phrase ("Here's why →"), or the next visible number in a sequence.
  A card that fully resolves is a stopping point — save resolution for the close.
- **Re-hook at card 4–5**: readers decay along the retention curve (~70–80% left at card 3,
  ~50–60% at card 7 — playbook §1). The STAT card usually carries this beat; make it the most
  surprising number in the deck.
- **≤50 words per card** (target 25–40); 6–8 words per line, broken at phrase boundaries.

---

## Hook options — always draft three

Draft 3 hook options for Card 1, using different formulas from the playbook §4 library. Judge
each against the 3-second test. Show to user before rendering. The six formulas:

### Shape A — Factual gap
```
"[Thing everyone has] doesn't have [thing they need]. Yet."
Examples:
  "AI agents don't have identity. Yet."
  "Your MCP server has no audit trail. Yet."
  "Agent-to-agent payments have no trust layer. Yet."
```

### Shape B — Contrarian
```
"Everyone is [doing X]. Nobody is [doing what actually matters]."
Examples:
  "Everyone is shipping AI agents. Nobody is governing them."
  "Everyone has a zero-trust policy. Almost nobody applies it to agents."
```

### Shape C — Numbered promise
```
"[N] things your [topic] is missing."   (odd numbers read credible; specific numbers lift CTR)
Examples:
  "5 things your AI governance stack is missing."
  "3 reasons your agent service account will fail an audit."
```

### Shape D — Outcome + timeframe
```
"[Customer/entity] fixed [problem] in [timeframe]. Here's how."
Example:
  "One integration closed their agent audit gap in 14 days. Here's how."
```

### Shape E — Stop doing X
```
"Stop [common practice]. Do [better thing] instead."
Example:
  "Stop giving agents service accounts. Give them identities."
```

### Shape F — How [known entity] does it
```
"How [recognisable company/standard/framework] handles [problem]."
Example:
  "How zero-trust architectures handle non-human identity."
```

---

## Per-card copy rules

### One idea per card
If a slide needs two ideas, make two slides. If it needs a paragraph, use two sentences.

### Specific over generic
Bad: "AI is transforming enterprise operations."
Good: "Your agent just processed a $50K payment. Did you approve that?"

### Language conventions (per the active company's brand)
Match the spelling/language conventions in `profiles/<active>/knowledge/company.md`. For a British-English brand: `organisations`, `authorised`, `recognise`, `personalised`, `favour`, `optimise`.

### Never invent stats
Every number must come from `profiles/<active>/knowledge/`. If no stat exists, use a qualitative claim or a verbatim quote from `profiles/<active>/knowledge/case-studies.md`.

### Bold emphasis
Use `<strong>` only for the 3–5 words that carry the most weight on the card. Not for decoration.

---

## Caption copy rules

The caption drives topic classification; the document drives dwell (playbook §7). Write two
variants per carousel:

### Caption A — Hook-led (cold audiences)
- Opens with the hook directly (same tension as Card 1, but not the cover text verbatim)
- **First ~140 characters carry the payoff promise** — that's the mobile fold
- Short sentences. One thought per line. 100–200 words.
- Name the topic and entities plainly — the classifier reads the caption
- Ends with the close: default = genuine question (invite real replies); lead-magnet mode = trigger word instruction + what they'll get
- Hashtags: **0–3, never stacked**
- Tone: direct, factual, problem-first

### Caption B — Story-led (warm audiences)
- Opens with context / narrative
- More editorial, slightly longer
- Same close as Caption A
- Tone: informed, slightly more conversational

### Caption structure template
```
[Hook / tension — 1–2 sentences, payoff inside the first 140 chars]

[Bridge — 1 sentence connecting problem to solution]

[Swipe prompt — "Swipe → for the X-minute version"]

[Close — default: "Save this for [use case]" + a genuine question]
[Close — lead-magnet mode: "Comment TRIGGER for the full [deliverable]"]

#tag1 #tag2 #tag3   (0–3 total — or none)
```

### Hashtag priority
0–3 tags maximum (hashtag following is gone; stacks read as spam — playbook §7). If using any,
pick by topic/persona relevance, e.g. one reach tag (`#AgenticAI`), one topic tag
(`#AIGovernance`), one persona tag (`#CISO`).

### Alt text
Add one plain sentence of alt text for the cover (what it shows + the takeaway) at the bottom of
`caption.md` — used when the cover PNG is posted natively (X / Instagram) and for accessibility.

---

## DM copy rules (lead-magnet mode only)

- Only produced when the colleague opted into the trigger-word close
- Short (under 80 words)
- Delivers the asset immediately (link in first message)
- Opens a conversation, does not push a demo
- One follow-up only — never three messages
- **Manual fulfilment only** — never wire the DM to an automation tool (account-level penalty risk)
- Match the recipient's industry to the closest case-study shape in
  `profiles/<active>/knowledge/case-studies.md` (e.g. regulated multi-party workflows,
  healthcare data exchange, payments, platform builders) and reference that story

---

## Trigger word selection (lead-magnet mode only)

Auto-pick based on topic angle:

The words are exemplars — prefer a word from the active company's own product vocabulary
(`profiles/<active>/knowledge/product.md`) when one is more on-theme.

| Trigger | Best for |
|---|---|
| `TRUST` | Identity, governance, compliance, audit |
| `GATEWAY` | Developer-facing, flagship product posts (swap for the product's own category word) |
| `AGENTS` | "What to do about agents" / platform framing |
| `IDENTITY` | DID, verifiable credentials, identity-first |
| `FABRIC` | Full-stack / platform story (swap for the platform's own name-word) |
| `PROOF` | Case study carousels, ROI, evidence-based |

The trigger word must appear:
1. On the CTA card (in `<p class="trigger-word">`)
2. In Caption A (bold)
3. In Caption B (bold)
4. In the DM subject line or first sentence

Cadence cap: at most one lead-magnet carousel every 3–4 weeks (playbook §2). In the default
compliant close, the `trigger-word` slot carries the single ask instead (`FOLLOW`, `SAVE`, or
the asset name) — the layout is unchanged.

---

## Reference carousel

`content/<active>/carousels/ai-agent-identity/` — the validated Phase 1 example (lead-magnet close).
Study Card 4 (stat structure), Card 8 (quote structure), Card 9 (close structure) before writing new carousels.
