# Build-Deck — Slide Outlines

Twelve templates covering every GTM situation for the active company. Each is a skeleton — adapt
headlines and proof to the named account and primary persona. Substitute `[brand_name]` and
`[product name]` from `profiles/<active>/PROFILE.md`; take the one-line `wedge` and
`content_pillars` from the same file; ground all product narrative — solution themes,
differentiators, capabilities, deployment options, protocol/standard support — in
`profiles/<active>/knowledge/product.md`. Persona pains and regulatory drivers come from
`profiles/<active>/knowledge/icp-personas.md`.

Two placeholder styles appear below: `[square brackets]` are fill-in slots (a name, a case study,
an incumbent); text in `『corner brackets』` is an **instruction to write that element from profile
knowledge** — never paste it literally. "Content" slides = any slide that isn't title or CTA.

**Quick routing — match the primary persona to a template:**

| Primary persona / role signals | Template |
|---|---|
| Legal, Privacy, Compliance, Risk, Audit, Counsel, CLO, GC, DPO | **A5** |
| CTO, Head of Platform, VP Eng, Enterprise/Cloud Architect, Platform Infra | **A6** |
| Partner Platform Owner, Enterprise Architect with cross-org focus, BD (partner) | **A7** |
| Senior/Staff Engineer, Platform Engineer, SA, Solutions Architect, technical evaluator | **A8** |
| Head of Product, BD/Partnerships, Monetisation, Revenue (commercial angle on the product) | **A9** |
| Functional executive accountable for scaling the product's domain from pilot to production (Head/VP of the function, platform or product lead for the domain) | **A10** |
| Startup CEO or CPO (Seed–Series C; the product's wedge as a sales differentiator) | **A-S1** |
| Startup CTO or Lead Engineer (Seed–Series C; build-vs-buy) | **A-S2** |
| Generic enterprise discovery (mixed committee, persona unknown) | **A1** |
| Leave-behind one-pager | **A2** |
| Scoped PoC proposal (Champion + Economic buyer) | **A3** |
| Channel / integration partner | **A4** |

If the primary persona fits A5–A10 or A-S1/A-S2, **always prefer the persona-specific template
over A1.** A1 is the fallback when persona is genuinely unclear. A7 and A9 additionally require the
product to actually carry that story in `product.md` (a cross-org mechanism for A7, a
commercial/monetisation theme for A9) — if it doesn't, route to A6 or A1 instead.

---

## A1 — Discovery Deck (8–10 slides)

Use for first/second discovery calls with Enterprise or Startup prospects.

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『a provocative question built from the product's wedge (PROFILE.md `wedge`) — name the shift, ask who owns the consequence』 | Company name + date + colleague name/title |
| 2 | **Problem setup** | 『name the gap the product closes, as the prospect experiences it』 | The 3 pains that block the prospect in the product's domain — take them from this persona's pain points in icp-personas.md. One striking, sourced number per pain. |
| 3 | **Why now** | 『a "the moment is here" headline』 | The 1–2 regulatory/compliance drivers most relevant to this prospect's market (from icp-personas.md). One market-adoption or ecosystem signal from product.md or account research that shows the shift is already underway. |
| 4 | **Company intro** | 『the company's positioning line from company.md』 | One-paragraph company narrative from company.md. Products overview (lead with the flagship `default_product`; add the rest of `PROFILE.md` products[]). Product-maturity note (beta / GA) if applicable. |
| 5 | **Product focus** | 『[product name] + its one-line wedge』 | The product's 3–5 solution themes from product.md — lead with the 2–3 most relevant to this persona. |
| 6 | **Proof** | "From Pilot to Production — [Case Study]" | Apply case-study selection map. Case name + shape + 2-sentence narrative + outcome metric. "Why this maps to you" personalisation line if account context is known. |
| 7 | **Differentiation** | "Why Not [Incumbent]?" | Comparison table from product.md differentiators: 4–6 dimensions where the product beats the default alternative (the prospect's existing platform, an incumbent suite, or building in-house). Anchor each row in a stated differentiator, not adjectives. |
| 8 | **How it works** | 『headline the lowest-friction adoption path』 | Deployment options from product.md (managed / self-hosted / other). Integration path: what changes in the prospect's stack, and what explicitly doesn't. |
| 9 | **What we'd like to explore** | "A Scoped Proof of Concept" | Propose a PoC shape: one defined scope (3–4 weeks, one system, one workflow or boundary). Call to action: agree PoC success criteria at end of this meeting. |
| 10 | **CTA** | "Next Step" | One specific ask. Colleague name + contact + `[brand_name]` logo. |

---

## A2 — Tailored One-Pager (1 slide / 1-page PDF layout)

Use as a leave-behind after a call, or as a first-touch email attachment.

**Layout (portrait, dense):**
- **Top section:** "[brand_name] [product name] — [Company Name] Brief" · colleague name + title
- **Pain (3 bullets):** the 3 most relevant pains for this prospect's persona, in their language
- **Our answer (1 paragraph):** 2–3 sentences, grounded in product.md — no hyperbole
- **Proof (1 case study):** name + one sentence outcome
- **Why now (1 bullet):** the specific why-now signal or trigger
- **Next step:** one ask, bolded
- **Footer:** colleague email + `[brand_name]` logo + "Confidential · [date]"

---

## A3 — POC Proposal (6–8 slides)

Use when proposing a scoped pilot to a technical + commercial audience (Champion + Economic buyer).

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | "[brand_name] [product name] — POC Proposal for [Company]" | Colleague name + date |
| 2 | **What we heard** | "Your Situation" | 3 specific points from the discovery call — their pain, their systems/stack, their timeline. Confirm alignment before the POC starts. |
| 3 | **What we're proposing** | "A Scoped 3-Week PoC" | Exact scope: which system(s), which workflow or boundary in scope, success criteria. What `[brand_name]` provides. What the prospect provides. |
| 4 | **What you'll prove** | "Success Criteria" | 3–5 measurable outcomes derived from the product's core value claims in product.md — each one testable inside the PoC scope (a latency/throughput bound, a coverage or completeness claim, a before/after workflow metric). |
| 5 | **How we'll work** | "Timeline & Roles" | Week 1: setup + integration · Week 2: test scenarios · Week 3: review. Named roles: their Champion / their technical owner / `[brand_name]` SA. |
| 6 | **Proof this works** | "Evidence from [Case Study]" | The closest case study (shape match). Outcome metric. "We can put you in touch with [reference if available]." |
| 7 | **Investment** | "What It Takes" | Effort on their side (engineer-days). `[brand_name]` involvement (SA + PM). Timeline to production post-PoC. |
| 8 | **Next Step** | "Let's Start" | One ask: agree scope + kick-off date by [specific date]. Colleague contact. |

---

## A4 — Partner Brief (6 slides)

Use for channel partners, integration partners, or system integrators.

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | "[brand_name] [product name] — Partner Overview" | Colleague name + date |
| 2 | **The problem** | 『name the wall the partner's end-customers are hitting — the product's core pain, from icp-personas.md』 | The partner's end-customers' pain, framed in the partner's vertical vocabulary. |
| 3 | **The product** | "What [product name] Does" | One-paragraph from product.md. Key integration points: the product's protocol/standard/API support from product.md. |
| 4 | **The joint motion** | "How We Work Together" | Referral / OEM / co-sell — leave options open for discussion. |
| 5 | **Proof** | "Already Running in Production" | 1–2 case studies most relevant to the partner's vertical. |
| 6 | **Next Step** | "What We're Asking For" | One specific ask: a named technical evaluation or a joint pilot customer. |

---

## A5 — Regulated Enterprise: Legal + Compliance Persona (12 slides)

**Best for:** CISO · General Counsel · Privacy Officer · Head of Compliance · Risk/Audit · DPO · any role whose primary lens is regulatory accountability, evidence, and audit defensibility.

**Narrative spine:** *You already committed to a governance/compliance posture (name their
certifications, frameworks, or public policy commitments). The activity the product governs is
growing faster than your controls. Here's the gap — and the one layer that closes it.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『"[Company]'s X is happening. Who's accountable?" — X = the activity the product governs』 | Persona name + date + colleague name/title. Dark background. |
| 2 | **Company moment** | "[Company]'s 『domain』 Moment" | 3 specific signals that [company] already lives the problem — job posts, product announcements, public roadmap. One stat (growth, scale, breadth). Frame: their stated compliance commitment now needs *runtime / operational* evidence. |
| 3 | **The gap** | "The Governance Gap" | What their existing frameworks/tools cover vs. what they miss — take the 2–3 gap dimensions from product.md's problem framing. Quote or paraphrase analyst language if research provides it ("you can't govern what you can't see"-shaped). |
| 4 | **Three threats** | "Three Live Threats — Specific to [Company / Industry]" | Pick the 3 most relevant from `recent-news-research.md`: an industry incident, a regulatory deadline from the prospect's market, relevant case law or enforcement action. One per card, specific to their context. |
| 5 | **Legal landscape** | "The Legal Landscape Is Shifting Fast" | 4 regulatory/legal signals from `recent-news-research.md` most relevant to their market and industry. Left column: signals with dates. Right column: "what this means for [company]" framing. |
| 6 | **Product intro** | "Introducing [brand_name] [product name]" | One-paragraph product narrative framed in the buyer's lens (a control/compliance layer, not a tech product). Render the product's protocol/standard support from product.md as pills. One-line positioning from the PROFILE.md wedge. **[Visual: hero-infographic.png]** — lead with the brand hero infographic; caption it with the product's 3 pillar-level benefits from product.md. |
| 7 | **Core capabilities** | 『"N Capabilities. One Control Point." — N and the noun from product.md』 | The product's 3–5 core capabilities from product.md. One paragraph + outcome statement per capability. **[Visual: ss-product-dashboard.png]** — anchor right on the capability the dashboard evidences. |
| 8 | **Compliance mapping** | "[Framework] → [product name] Controls" | Table: 5–6 requirements from the framework most live for this company (pick from icp-personas.md regulatory drivers). Three columns: Requirement → What It Demands → How [product name] Delivers. |
| 9 | **Use cases** | "[product name] in [Company]'s World" | 3 scenarios specific to the company's known operations. Each card: THE GAP (problem) + THE FIX (product answer) + outcome callout. |
| 10 | **Competitive** | "Why Not Just Use [Incumbent]?" | Comparison table: 5–6 dimensions from product.md differentiators. Two columns: [Incumbent / the prospect's existing platform] vs. [product name]. Close: *"[product name] complements [Incumbent], not replaces it."* |
| 11 | **Timing** | "The Timing Is Right" | 3 date-anchored moments: a company-specific event (conference, launch, audit cycle) + a regulatory deadline from their market + a product availability milestone. |
| 12 | **Next steps** | "Let's Talk" | 3 numbered steps: Discovery Call → Pilot Scoping → 『the product's availability program from product.md (early access, pilot, GA onboarding)』. Contact box right. |

---

## A6 — Platform / Infrastructure Leader (10 slides)

**Best for:** CTO · Head of Platform Engineering · VP Engineering · Enterprise Architect · Cloud Architect · anyone who owns the stack the product plugs into and lives its fragmentation daily.

**Narrative spine:** *Every team in your org is solving 『the product's problem domain』 differently.
That debt compounds with every new system. Here's how to standardise once and govern everywhere.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『a standardisation promise built from the wedge — "one standard / one layer for your whole estate"』 | Persona name + date + colleague name/title. |
| 2 | **Fragmentation problem** | "The Stack Looks Like This" | Visual or table of the prospect's actual sprawl: which teams run which tools/frameworks in the product's domain (from account research), each with its own workaround, its own logging, its own policy. No unified view. |
| 3 | **Hidden cost** | "The Cost of Custom Workarounds" | 3 consequences: risk debt (each workaround is a gap); reporting/audit impossibility (no unified view); velocity drag (every new tool = new integration). One sourced stat on engineering time lost to the DIY equivalent. |
| 4 | **Product intro** | "[product name] — One Layer for Your Entire Estate" | The product's integration model from product.md: how it drops into an existing stack, what it requires teams to change, and what it explicitly doesn't. **[Visual: product-flow.png]** — show the product's end-to-end flow diagram. |
| 5 | **Coverage** | "Native Across Your Stack" | Table: the product's protocol/standard/integration support from product.md, mapped to the frameworks/tools this prospect runs. Roadmap for what's next. |
| 6 | **Architecture fit** | "Where [product name] Lives in Your Architecture" | Deployment topology from product.md deployment options. Integration with the prospect's existing platform/IAM. What does NOT need to be rewritten. **[Visual: ecosystem-diagram.svg]** — how the product fits within the broader suite (PROFILE.md products[]). |
| 7 | **Operational view** | "Single Pane of Glass for the Whole Estate" | What platform teams see day-to-day vs. what leadership/compliance sees — from product.md's reporting/observability capabilities. Per-team attribution or cost visibility if the product supports it. **[Visual: ss-product-dashboard.png]** — anchor right; caption "[brand_name] [product name] — 『dashboard name』". |
| 8 | **Integration path** | 『"From Zero to [outcome] in One Sprint" — outcome from the wedge』 | Integration timeline: Week 1 / Week 2 / Week 3 milestones from product.md deployment guidance. SDK/tooling quality and overhead data if the product publishes them. |
| 9 | **Proof** | "Running in Production — [Case Study]" | Closest case study from `case-studies.md` for a platform-engineering context. Outcome metric. |
| 10 | **Next steps** | "Let's Talk Architecture" | 3 steps: Architecture Walkthrough → PoC Scope → 『availability program』. Offer: sandbox/trial environment for the platform team. |

---

## A7 — Cross-Organisation (9 slides)

**Best for:** Enterprise Architect · Partner Platform Owner · BD leads (partner-led motion) · anyone whose workflows in the product's domain cross organisational or jurisdictional boundaries. Requires a real cross-org mechanism in product.md — otherwise route to A6 or A1.

**Narrative spine:** *Your 『workflows/systems in the product's domain』 routinely cross org lines —
to partners, suppliers, customers. Today that traffic runs on ad-hoc arrangements. One failure at
the boundary and you own the consequence. Here's how 『the product's guarantee』 travels across it.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『"Your X Crosses Boundaries. Does Your Y?" — X = the thing that crosses, Y = the guarantee the product provides』 | Persona name + date + colleague name/title. |
| 2 | **Cross-boundary problem** | "What Actually Happens at the Edge" | Describe the prospect's cross-boundary scenario concretely: two orgs, two stacks, two sets of controls — and exactly what is lost at the handoff today (visibility, enforcement, evidence). |
| 3 | **How it breaks** | "The Three Ways This Fails" | The 3 boundary failure modes from product.md's problem framing. One incident example from `recent-news-research.md` relevant to their industry. |
| 4 | **Cross-boundary mechanism** | 『headline the product's cross-org mechanism from product.md』 | How the product carries its guarantee across the boundary: what each side runs, what is exchanged at the handoff, what is verified / enforced / logged per hop. **[Visual: ecosystem-diagram.svg]** — the cross-org / federation view. |
| 5 | **Scoped delegation** | "Delegating Authority Without Delegating Everything" | How the product scopes what crosses the boundary — what the counterparty can do, on behalf of whom, under what conditions — from product.md. |
| 6 | **Scenarios** | "Three Cross-Org Scenarios" | Pick the 3 most relevant to the prospect from icp-personas.md verticals (e.g. supply-chain handoffs · financial settlement across institutions · partner data exchange · regulated-sector data sharing). Per scenario: problem + product answer. |
| 7 | **Supporting product** | 『the companion product/component that anchors the cross-org story』 | If PROFILE.md products[] includes a component that anchors cross-org trust or coordination, give it this slide: what it anchors, and why the prospect doesn't have to build it. If none exists, fold the point into slide 4 and drop this slide. |
| 8 | **Why open / portable** | 『"Why Not a Proprietary [alternative]?"』 | The product's open-standards / portability differentiators from product.md vs. a proprietary or incumbent-locked alternative: portability, no lock-in, third-party verifiability. |
| 9 | **Next steps** | "Map Your First Cross-Boundary Use Case" | 3 steps: Discovery → Pilot (one boundary, one partner) → 『availability program』. |

---

## A8 — Developer / Builder (9 slides)

**Best for:** Senior/Staff Engineer · Platform Engineer · Solutions Architect · technical evaluator running the PoC. Audience reads code, cares about latency, integration friction, and SDK quality.

**Narrative spine:** *You're running 『the systems the product touches』 in production and you're
missing 『the capability the product adds』. Drop it in — with the integration effort product.md
actually claims, no more. See value in 30 minutes. Then turn on more.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『a blunt capability promise in ≤6 words, from the product's strongest developer-facing claim in product.md』 | Persona name + date + colleague name/title. |
| 2 | **The gap** | "What You Can't Do Right Now" | What the evaluator's team cannot see or do today without the product — 3–4 concrete, daily-workflow examples in the evaluator's language. Close the loop: the capability you don't have = the outcome you can't produce. |
| 3 | **How it works** | 『the product's integration mechanism, stated plainly』 | Diagram: prospect's stack → [product name] → downstream. One-paragraph technical description from product.md — precise about what is intercepted / instrumented / added, and what stays untouched. **[Visual: product-flow.png]** — the full flow; label each step beneath. |
| 4 | **Coverage** | "Works with the Stack You Already Run" | Table: the product's protocol/standard/integration support from product.md, mapped to the frameworks/tools this team runs. Roadmap callout. |
| 5 | **Day-one value** | "What You Get Immediately" | The out-of-the-box value before any deep configuration, from product.md. Standard export / interop points. No custom instrumentation or rework required (only if product.md supports the claim). **[Visual: ss-product-dashboard.png]** — anchor right; show it, don't just describe it. |
| 6 | **Then turn on more** | 『the product's adoption ladder from product.md, as "X → Y → Z — incrementally"』 | The opt-in layers in order: what turns on first, what each subsequent layer adds. No lock-in at any layer (claim only what product.md supports). **[Visual: ss-product-feature.png]** — anchor right on the layer the screenshot evidences. |
| 7 | **Integration** | "From Zero to Live in Under a Sprint" | Packaging (container / marketplace / self-hosted), SDK languages, a minimal config example, deployment variants — all from product.md. |
| 8 | **Performance** | "What Does This Cost You?" | The product's published overhead/benchmark numbers, throughput, resource footprint, migration cost framing. Cite only numbers product.md contains — an invented benchmark dies in this room. |
| 9 | **Next steps** | "Get a Sandbox in 48 Hours" | 3 steps: Technical Walkthrough (30 min) → Sandbox Access → PoC Scope (one system, one workflow). Offer: `[brand_name]` SA on the call. |

---

## A9 — Commercial / Monetisation (8 slides)

**Best for:** Head of Product · BD / Partnerships · Monetisation Lead · Revenue Leader — any role that wants to monetise, attribute, or commercially de-risk the workflows the product touches. Requires a commercial/monetisation theme in product.md — otherwise route to A1.

**Narrative spine:** *The workflows the product touches are already producing value. You can't
attribute it, price it, or prove it cleanly. Here's how to turn that activity into governed,
billable value.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『"You're already doing X. Now get paid for it." — X from the prospect's context』 | Persona name + date + colleague name/title. |
| 2 | **The commercial gap** | "Why the Current Stack Can't Monetise" | The 2–3 missing prerequisites, taken from product.md's commercial framing as a causal chain (e.g. no attribution → no billing → no dispute resolution). One sourced stat if research provides it. |
| 3 | **The enabling shift** | 『name the standard / rail / mechanism that newly makes this possible』 | What changed: the emerging standard or mechanism from product.md that the commercial story rides on. How it differs from the traditional flow. Status: where the product supports it today. |
| 4 | **Product + commerce** | 『headline the product's commercial mechanism』 | The causal chain from product.md: what the product establishes → what becomes attributable → what becomes billable / provable / disputable. **[Visual: product-card.png]** — anchor right. |
| 5 | **Monetisation models** | "Three Ways to Monetise" | Three models the product supports, from product.md (e.g. pay-per-use · subscription gating · metered attribution). One concrete example per model. |
| 6 | **Cross-boundary commerce** | "Selling to Other Organisations" | How the product's cross-org capability (if product.md has one) enables transacting with partners/customers: verified counterparty, scoped authority, provable record. Drop this slide if the product has no cross-org story. |
| 7 | **Use cases** | "Three Commerce Scenarios" | Pick the 3 most relevant to the prospect from icp-personas.md verticals. Per scenario: the workflow, the money left on the table, the product's answer. |
| 8 | **Next steps** | "Map Your First Monetisable Workflow" | 3 steps: Scoping Call → Integration Review → 『availability program』. Contact. |

---

## A10 — Pilot to Production: Functional Executive (10 slides)

**Best for:** the executive accountable for moving the product's domain from pilot to safe production — Head/VP of the function, platform lead, or product manager for the domain.

**Narrative spine:** *You've got pilots. Scaling them is harder than building them: failures in
production, invisible sprawl, fragmented tooling, governance debt. Here's the layer that lets you
move fast without losing control.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『"[Pilots] Are Easy. Production Is Hard." — written in the prospect's domain vocabulary』 | Persona name + date + colleague name/title. |
| 2 | **The production gap** | 『"Why Most [domain] Projects Stall Before Production"』 | The three failure modes that stall the domain, from product.md's problem framing + icp-personas.md. Use a researched stat for the headline number if one exists; never invent one. |
| 3 | **De-risking capability** | 『the capability or companion product that addresses failure mode #1』 | From PROFILE.md products[] / product.md: what it does, and why it beats the DIY equivalent the prospect would otherwise assemble. |
| 4 | **Core product** | 『the flagship's answer to failure mode #2』 | The flagship's 3 core capabilities from product.md and why each matters in production, not just in a pilot. **[Visual: hero-infographic.png]** — hero visual; caption with the product's pillar-level benefits. |
| 5 | **Day-one visibility** | "See Your Entire Estate — No Extra Project" | What the exec sees immediately after adoption, from product.md — the "ready for scrutiny without a separate instrumentation project" framing. **[Visual: ss-product-dashboard.png]** — anchor right. |
| 6 | **Control without slowing velocity** | 『"[Control] as Config, Not Code"』 | How the product puts management in one place while teams keep shipping: change once, applies everywhere behind the product — from product.md. Use this frame only if the product genuinely works this way. |
| 7 | **Many teams, one layer** | "One Layer for All Your Teams" | Table: which teams run which tools/stacks (from account research) and how the product sits across all of them uniformly. Per-team attribution / reporting if supported. |
| 8 | **Proof** | "From Pilot to Production — [Case Study]" | Most relevant case study from `case-studies.md`. Timeline: pilot → integration → production. Outcome: a specific metric improvement. |
| 9 | **Why now** | "The Window Is Now" | The 1–2 regulatory/compliance drivers most relevant to this prospect's market (from icp-personas.md) + internal board/audit pressure + product availability. Waiting = compounding debt. |
| 10 | **Next steps** | "From Ambition to Accountability" | 3 steps: Pilot Review (map the current estate) → Scoped PoC (one team, one system) → 『availability program』. |

---

## A-S1 — Startup: CEO / CPO (7 slides)

**Best for:** Founders · CEOs · CPOs at Seed–Series C selling into enterprises. Primary hook: the product's wedge as a sales differentiator, not a cost centre.

**Narrative spine:** *You're selling to enterprises. Procurement keeps asking 『the hard question
the product answers』. You could spend 2–4 engineers building the answer. Or get it out of the box.
First mover on 『the wedge』 = defensible moat.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『"Enterprise-Ready. From Day One." — sharpened with the product's domain』 | Company name + date + colleague name/title. Short. |
| 2 | **The enterprise wall** | "The Question That's Blocking Your Deals" | The real procurement/security question the product answers — phrase it exactly as a buyer would (from icp-personas.md). How many cycles have stalled or lengthened because of it? One researched data point on this requirement as a buying gate. |
| 3 | **Build vs. buy** | "2–4 Engineers. 3–6 Months. Or None of the Above." | What building the product's capability in-house actually takes — the component list from product.md capabilities. What they'd rather be building instead. [product name] = drop-in, not a rewrite (match product.md's actual integration claim). |
| 4 | **Wedge as differentiation** | 『"[the wedge] Is Your Competitive Moat"』 | Startups that lead on the product's value dimension: faster procurement cycles, higher ASPs, an investor/PR narrative ("ready by design"). Any credibility assets from product.md (standards participation, certifications, open source) the startup inherits for free. |
| 5 | **What you get** | "Enterprise-Ready in One Integration" | The 3–5 capability bullets from product.md that answer the slide-2 question, including any compliance alignment out of the box. What the next enterprise RFP looks like with vs. without. **[Visual: ecosystem-diagram.svg]** — frames the offer as "the whole stack, not just a feature". |
| 6 | **Proof + traction** | "Already Running in [Relevant Sector]" | Closest case study. One-sentence outcome. Product-maturity status (beta / GA) and who's using it, if citable. |
| 7 | **Next steps** | "30 Minutes to Know If This Fits" | One ask: a scoping call. What `[brand_name]` needs from them: their stack, one target use case, one enterprise-customer scenario. Program terms. |

---

## A-S2 — Startup: CTO / Lead Engineer (8 slides)

**Best for:** CTOs · Lead Engineers at Seed–Series C who are the technical decision-maker on whether to build or buy the capability the product provides.

**Narrative spine:** *You're building in the product's domain. You know you'll need 『the
capabilities the product provides』 eventually. The question is when, and whether to build them.
Here's why now — and why the hard parts are already solved.*

| # | Slide type | Title direction | Key content |
|---|---|---|---|
| 1 | **Title** | 『capability promise + "Enterprise-Ready", ≤7 words』 | Company name + date + colleague name/title. |
| 2 | **The build-vs-buy calc** | "What It Actually Takes to Build This" | Honest breakdown of the in-house build: each major component from product.md capabilities, with the hidden hard part named per component. Engineer-months. Maintenance forever. |
| 3 | **The moving-target problem** | "The Ground Is Moving. Don't Build to Freeze." | The standards / protocols / dependencies in the product's domain that are still evolving — list them from product.md. Build to one version = rebuild when they change. [product name] tracks them for you. |
| 4 | **How it works** | "Works with Your Stack Today." | Technical diagram: the integration mechanism from product.md. Protocol/standard support table. SDK languages. Overhead data if published. **[Visual: product-flow.png]** — full flow diagram; label each step beneath. |
| 5 | **Value first, control later** | "See Value, Then Turn On More" | The adoption ladder from product.md: Day 1 out-of-the-box value → the later opt-in layers, in order. No lock-in at any layer — opt out cleanly (claim only what product.md supports). **[Visual: ss-product-feature.png]** — anchor right on the layer the screenshot evidences. |
| 6 | **Enterprise readiness** | "What This Means for Your Procurement Cycles" | What enterprise security/procurement reviews ask for in this domain — and how the product answers each, from product.md. Time saved in procurement. |
| 7 | **Proof** | "Running in [Relevant Tech Stack]" | Closest case study from `case-studies.md` for a startup or developer-led deployment. Integration time. Outcome. |
| 8 | **Next steps** | "Sandbox in 48 Hours" | 3 steps: Technical Walkthrough (30 min) → Sandbox Access → PoC Scope. Offer: `[brand_name]` SA pairing session to get it live. |
