# Scope-check question types (generic, reusable)

A **domain-agnostic bank** of scope-validation question *types*. Each type maps to a **design decision
it unlocks** — that is the whole point: a scope-check question with no decision behind it is noise.

**How to use it.** Read the source's **open decisions** — a `solution-design`'s assumptions box +
open-questions appendix (post-design), or a `solution-discovery` brief's requirements question bank
(pre-design; its must-ask items are already tagged with the design decision each unlocks). For each
*real* open decision, pick the matching type below, and instantiate its template questions with the
actual systems/actors/terms. Aim for **4–8 groups** — one per decision that genuinely matters, ordered
by leverage (the one that most reshapes what comes next first; definition-of-done last). Not every type
applies every time; skip the ones with no open decision behind them.

**Each instantiated group carries:** a title phrased *as a question* · a `→ unlocks:` tag naming the
decision · 1–3 filled-in questions · and `Our assumption today: …` (the assumption the design is
resting on, so a blank answer becomes an explicit assumption, not a silent guess).

`[bracketed]` text = fill from the source design. Keep questions concrete and answerable in a sentence.

---

### 1. Architecture reality  — *validate the load-bearing assumption*
**Unlocks:** the whole V1 shape / whether the design's foundation holds.
**Use when:** the design rests on a system, integration, or capability it did not verify exists.
- Does `[key system / integration]` exist and work as we assumed today — or would part of this build be standing it up?
- What are `[the key components / actors]`, concretely — `[tech, models, how they run]`?
- Is `[the target]` reachable as `[the interface the design assumes]`, or would we adapt it?

*Assumption to state:* "`[the core system]` exists roughly as described on the call."

### 2. Deployment & residency  — *where it runs, and what's acceptable*
**Unlocks:** hosted vs self-hosted, and region / data-boundary exceptions.
**Use when:** the design assumes a hosting model or the data location is unconfirmed.
- Where does `[the data / workload]` live today, and in which region?
- Is `[the assumed hosting model, e.g. managed vs self-hosted]` acceptable, given `[the trade-off it implies]`?
- Is there anything that must **not** `[leave your boundary / transit a managed layer]` — i.e. an exception?

*Assumption to state:* "`[the assumed hosting model]` is the assumed V1; region/residency to confirm."

### 3. Identity & authorization inputs  — *who acts, and how we bind them*
**Unlocks:** how the design authenticates / attributes / delegates.
**Use when:** the design binds an actor (a person, a service, an agent) and the identity source is unconfirmed.
- Is there an authenticated identity / IdP for `[the actors]` we can bind and rely on today?
- Is `[the authorization / delegation]` explicit today, or implicit — and do you want it made provable?

*Assumption to state:* "some authenticated `[actor]` exists and can present an identity, even if implicit today."

### 4. Data & context available  — *what the design can key on*
**Unlocks:** whether the design's logic/policy can actually run on the data present.
**Use when:** the design's behaviour depends on metadata/attributes/signals it hasn't confirmed exist.
- What `[context / metadata / fields / signals]` do `[the requests / records]` carry today, or could carry?
- Is `[the specific signal the design needs]` present, and reliable enough to `[drive the decision]`?

*Assumption to state:* "`[the needed context]` is available, or can be added, on `[the requests/records]`."

### 5. Integration & interfaces  — *the connection surface*
**Unlocks:** the integration effort and the protocol/API choices.
**Use when:** the design connects to external systems whose interfaces are unconfirmed.
- What does `[the system]` connect to, and over which protocols / APIs?
- Are `[the targets]` reachable as `[the interface]`, with `[auth]`, at the rate the design assumes?

*Assumption to state:* "`[the targets]` expose `[the interface]` the design connects through."

### 6. Scope cut (V1 / V2)  — *the first-version boundary*
**Unlocks:** what ships first, and the single proving win.
**Use when:** always — the V1/V2 line is the most common thing to confirm or move.
- What's in V1 vs deferred to later — do `[the V1 items]` match your priority?
- Which single `[flow / task / path]`, proven end-to-end, is the clearest win for you?

*Assumption to state:* "`[the V1 cut]` is the current draft cut — to align on, not final."

### 7. Rules & policy scope  — *what the rules must cover*
**Unlocks:** the specific allow/deny (or transform) rules the design must encode.
**Use when:** the design enforces policy/rules over actions, data, or access.
- Which `[actions / data / resources]` should be allowed or denied, for whom, on which `[task]`?
- Are there `[exceptions / escalations / human-gated]` cases the rules must handle?

*Assumption to state:* "the rules cover `[the first concrete target]`; the rest follow the same shape."

### 8. Constraints  — *the feasibility bounds*
**Unlocks:** what's realistic for V1 (and what forces something to V2).
**Use when:** budget, timeline, existing infra, or regulatory exposure could bound the build.
- What are the hard constraints — budget, timeline, existing infrastructure we must fit?
- Any `[regulatory / contractual]` exposure the design must respect from day one?

*Assumption to state:* "no hard constraint rules out the V1 scope as drawn."

### 9. Stakeholders & decision  — *who decides, and who else must sign off*
**Unlocks:** the approval path (and hidden blockers).
**Use when:** the buyer in the room isn't the only approver.
- Who owns this decision, and who else must approve — `[security, legal, procurement, data owner]`?
- What would each of them need to see to say yes?

*Assumption to state:* "`[the buyer]` is the economic sponsor; other sign-offs to confirm."

### 10. Compliance & evidence  — *what must be provable*
**Unlocks:** the evidence artifacts and the standards the design must map to.
**Use when:** the design's value is partly "provable / auditable", or a framework applies.
- What must you be able to show `[an auditor / reviewer / regulator / a data subject]`, and in what form?
- Which `[frameworks / obligations]` does this have to satisfy — and which are must-haves vs nice-to-haves?

*Assumption to state:* "`[the named frameworks]` are the ones that matter; control-ID mapping firms up later."

### 11. Risks & unknowns  — *what most likely reshapes the design*
**Unlocks:** which assumption, if wrong, changes the target architecture.
**Use when:** one or more load-bearing assumptions are genuinely uncertain.
- Of everything we've assumed, what's most likely to land differently — and what would that change?
- Is there a `[dependency / vendor / timeline]` risk we should design around now?

*Assumption to state:* "the load-bearing assumptions in the design hold; this question tests the riskiest."

### 12. Success criteria / definition of done  — *what makes this a yes*
**Unlocks:** the POC/V1 acceptance bar — always the last group.
**Use when:** always — a scope check without a "what makes this a yes" leaves the goal implicit.
- What would make this `[POC / V1]` a clear yes — a working `[demoable outcome]`, a specific `[artifact]`, something else?
- Who signs off that it's done, and against what?

*Assumption to state:* "success = `[the design's implied outcome]`; confirm the bar."

---

**Composition note.** Page 1 restates the solution (see `references/scope-check-template.md` and
`body_template.md` Step 2); page 2 is these groups. Keep the two pages tight — pick the groups that
carry a real open decision, fill them with the design's own vocabulary, and cut the rest.
