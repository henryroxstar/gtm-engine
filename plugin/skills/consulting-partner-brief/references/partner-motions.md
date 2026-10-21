# Partner motions — the generic method

Company-agnostic reference for the `consulting-partner-brief` skill. Nothing here is
tenant-specific: it is the *shape* of partner relationships and the discipline for talking about
them. Whether any of it is **true for the active company** — which shapes are offered, what has
actually been agreed — lives in `profiles/<active>/knowledge/partner-program.md`, never here.

## Who this is about

A **consulting partner** is a systems integrator, dev shop, boutique consultancy, or
professional-services firm that **both advises clients and builds custom solutions for them**. They
run discovery, design the solution, and implement it. Their revenue is design and delivery.

**They are not resellers.** A reseller's motion is buying and reselling licences at margin; a
consulting partner's is billing for the work. Some consulting partners *also* resell — that is a
separate motion to establish per partner, never an assumption.

## The rule that governs everything else

**Never invent a commercial term.** Margin, referral fee, tier name, MDF, exclusivity, lead
registration. If the active profile does not record an answer, say *"that's not something I can
commit to in this conversation, let me come back to you"* rather than improvising.

A partner quoted a number that later changes will not take a second meeting, and an artifact
implying a programme that does not exist is worse than one that stays silent. **If the profile has
no `partner-program.md`, treat every commercial term as undecided.**

## The three shapes (descriptive, not contractual)

| Shape | What the partner does | What the vendor does | Fits a partner who… |
|---|---|---|---|
| **Refer** | Spots the fit in their client work, introduces the vendor, stays out of delivery | Sells, delivers, supports | …has relationships but no appetite to carry implementation risk |
| **Co-deliver** | Designs and implements the solution for their client, on the vendor's product as infrastructure | Supplies the product, enables their engineers, backstops architecture | …bills for design and build — the consulting-partner shape |
| **Embed** | Builds the vendor's product into their own product or repeatable offering | Supplies the product and a self-host or managed path | …ships software of their own, not just engagements |

A consulting partner is almost always **co-deliver**, sometimes drifting to **embed** if they
productise the pattern. **Refer** is the fallback when they will not carry delivery.

Deployment route follows shape: a managed/hosted route suits refer and most co-deliver; a self-host
route suits co-deliver into a sovereignty- or residency-constrained client. **Never offer a route the
profile marks as unavailable or coming-soon.**

## What a consulting partner actually wants — in priority order

Their currency is **authority and client outcomes**, not resale margin:

1. **Something concrete to hand a client.** They can specify an architecture; they usually cannot hand
   over a deployable component. That gap is why the conversation works at all.
2. **Authority.** Co-authored reference architecture, a citation in the next revision of their own
   published framework, a joint write-up. Cheap for the vendor, disproportionately valuable to them.
3. **A referenceable pilot.** One named engagement they can point at.
4. **Team enablement.** Their engineers able to design against the product without the vendor in the room.
5. **Margin.** Real, but rarely the opener for a firm whose business is billable design work.

**Pick one deliberately and offer it.** An artifact that argues technical fit and offers nothing in
return reads as a vendor pitch with extra steps.

## The questions a serious partner will ask

Check each against the profile before answering. Any without a recorded answer is a take-away, not an
improvisation:

- **Commercial model** — referral fee, reseller margin, or co-delivery rate card?
- **Who holds the customer contract** in a co-delivery — vendor, partner, or both?
- **Lead registration / conflict rules** — what happens when partner and vendor are in the same account?
- **Support boundary** — who answers the client's 2am page during a pilot?
- **Certification / enablement** — is there a partner training or accreditation path?
- **Pre-GA liability** — what does the vendor commit to when a partner puts pre-GA software in front
  of a regulated client? *A serious partner's lawyer asks this first.*
- **Is there a programme at all**, versus bespoke agreements per partner?

## The honest position while those are open

> "We don't have a partner programme with tiers and a margin schedule, and I'd rather tell you that
> than invent one. What we do have is a product that fills the layer your framework specifies, and an
> appetite to prove it on one scoped pilot together. If that works, the commercial shape follows the
> evidence — and I'd rather design it around what we both actually did than sign a template first."

Stronger than a fabricated programme, honest about an early product stage, and it fails safe: nothing
is promised that a later programme contradicts.
