# Approval pack — template

Written alongside the case study as
`case-study-approval-<account-slug>-<YYYY-MM-DD>.md`. This is the internal control document and the
thing that actually goes to the customer's comms or legal contact. Most named case studies stall for
months because nobody assembled exactly this; assembling it is the difference between a draft and a
publishable asset.

**Never send it yourself.** It is a draft for the operator to review and send.

Copy the structure below, fill every `[bracket]`, delete nothing. A section with no entries reads
"None — [why]", never an empty heading.

---

```markdown
# Approval pack — [Customer] case study
**Artifact:** case-study-[account-slug]-[YYYY-MM-DD] · **Tier:** [Deployed | Pilot | Design-stage]
**Prepared:** [DD Mon YYYY] · **Status:** Draft — not cleared for external use

## 1. What we are asking for
[One short paragraph in plain language, addressed to the customer contact: what the document is,
where it would be used (website, sales conversations, deck), and what specifically needs their
sign-off. No legalese, no vendor marketing.]

[Then, in two or three sentences: **the premise the document rests on**, stated as the customer's
own reviewer would state it — what the document concedes is already solved, and the narrower claim
it actually makes. A customer reviewer who can see the argument's boundary reviews faster and
argues less. State plainly whose limits the document concedes: **ours, not theirs.**]

**Requested from:** [Name, Title, Customer] · **Route:** [comms | legal | the champion first]
**Requested by:** [date] · **Blocking:** [what cannot ship until this clears]

## 2. Name and logo usage
| Item | Requested use | Status |
|---|---|---|
| Company name in full | [Named in title and body] | ☐ Pending ☐ Approved ☐ Declined |
| Logo | [Yes/No — where] | ☐ Pending ☐ Approved ☐ Declined |
| Individual named + titled | [Name, Title] | ☐ Pending ☐ Approved ☐ Declined |

**If declined:** the fallback is a de-identified pass — the customer becomes a descriptor
("[a regulated <sector> operator in <region>]") and every identifying detail in the challenge
section is generalised. Note here which specific details would have to change.

## 3. Quotes — verbatim, for confirmation
> "[Exact quote as it appears in the document.]"
> — [Name], [Title]

**Source:** [where it came from — call recording DD Mon, email DD Mon, written response]
**Status:** ☐ Verbatim, confirmed ☐ Awaiting confirmation ☐ PENDING — not yet provided
**Edits accepted:** [Yes — any edit the speaker makes is taken as-is]

[Repeat per quote. A quote marked PENDING must still be a `[QUOTE PENDING …]` placeholder in the
document itself — never a composed substitute.]

## 4. Claims requiring customer confirmation
| # | Claim as written | Number | Basis | Confirm? |
|---|---|---|---|---|
| 1 | [Claim] | [Number] | [measured / customer-reported / modeled / (~unverified~)] | ☐ |
| 2 | […] | | | ☐ |

**Every `customer-reported` and `modeled` figure appears in this table.** A `measured` figure the
vendor instrumented still appears if it describes the customer's operations. A claim resting on
**public law, a public standard, or a published specification** is marked `n/a` — the customer has
nothing to confirm, and asking them to is what makes these packs long enough to be ignored.

**Mark inferred claims as inferred, in the row itself.** Anything the vendor deduced — the pattern
the customer "must have been" using, the tool they "presumably" replaced — is labelled
*inferred by [vendor] — not stated by [customer]*, and the redline instructions ask about it
explicitly. An inferred claim presented as reported is the fastest way to lose a reviewer's trust
in the whole document.

For each `modeled` figure, state the baseline and the assumption in one line so the customer can
challenge the arithmetic rather than the conclusion:

- **[Claim]:** baseline `[value, source, date]` × assumption `[stated assumption]` → `[result]`.

**Claims withdrawn on review.**
[Anything an earlier draft asserted that research contradicted, and what replaced it. State it
plainly — this is the record that stops the claim being reinstated later by someone who assumes it
was dropped by accident. "None" if none.]

**Figures excluded for lack of primary confirmation.**
[Numbers that appeared in secondary sources or search summaries but did not survive a check at the
primary source. Name the figure, the source that carried it, the primary source that did not, and
the date checked. These are the ones that come back — record them so the same figure is not
re-adopted next quarter. "None" if none.]

## 5. Sensitive detail check
Confirm none of the following is present without explicit approval:

- ☐ Named third parties (their vendors, customers, regulators) — [list any]
- ☐ Contract value, pricing, or commercial terms
- ☐ Volumes, headcount, or capacity that could be commercially sensitive
- ☐ Security architecture detail beyond what the customer publishes
- ☐ Anything under NDA, or discussed on a call marked confidential
- ☐ Regulatory status stated as fact rather than as the customer describes it
- ☐ Individuals who did not consent to being named
- ☐ **Guidance cited as corroboration, not endorsement** — where a regulator, government advisory,
  standards body, or large vendor's documentation is cited to show the risk class is recognised or
  the design follows a recommended pattern, confirm that neither the prose nor any one-line summary
  derivable from it reads as approval of *this* product. Label voluntary frameworks voluntary. Note
  here the compression to guard against in derived decks and posts ("X-recommended", "Y-compliant",
  "government-approved") — that compression is the most likely distortion downstream.
- ☐ **Scope of borrowed evidence** — list which cited sources are local to the customer's
  jurisdiction and which are imported. If the majority of the evidence is imported, say so; a
  reader who works out the mismatch themselves discounts the whole document.
- ☐ **Third-party products named as evidence rather than as targets** — where the document cites a
  competitor's or a large vendor's specification to locate a boundary, confirm the framing describes
  that vendor's *documented scope*, not a defect, and that the same framing survives into any
  derived deck or post. Naming a large vendor approvingly still merits a comms read.
- ☐ **Competitive positioning the customer has to live with** — if the document positions the
  customer against named alternatives in their own market, they approve that, not just the facts.

## 6. Product-claim audit (internal — do not send)
| Capability referenced | Status | Appears in |
|---|---|---|
| [Capability] | [SHIPPED / CONDITIONAL / ROADMAP] | [section] |

**Gate:** nothing tagged ROADMAP may appear in a results section, in any tier. If one does, it is
fixed before the pack goes out — not noted as a known issue.

## 7. Redline instructions to send with the draft
[Short and specific — vague asks come back slow:]
1. Edit anything factually wrong directly in the document.
2. Any quote may be reworded or withdrawn — no justification needed.
3. Flag anything commercially sensitive even if technically accurate.
4. Confirm the three headline numbers, or give us the right ones.
5. Confirm the claims marked *inferred* in §4 — what you were actually doing. If we guessed wrong,
   that passage gets rewritten rather than softened.
6. Tell us the confidentiality status of any material you shared with us.
7. Return by [date] — after that we hold the draft rather than assume approval.

**Silence is not approval.** Nothing ships without an explicit yes.

## 8. Sign-off record
| Date | Who | Decision | Notes |
|---|---|---|---|
| | | | |

**Cleared for:** ☐ Website ☐ Sales use ☐ Deck ☐ Social ☐ Nothing yet
Once cleared, update the document's closing line from the draft warning to the cleared line.
```

---

## Operator notes

- **Tier drives the ask.** A design-stage Solution Story is usually a lighter conversation — you are
  confirming a design and modeled outcomes, not publishing results. Say so in §1; it materially
  raises the response rate.
- **Champion first, legal second,** unless the customer's process says otherwise. A champion who has
  already agreed to the numbers makes legal review shorter.
- **A declined name is not a dead asset.** The de-identified fallback in §2 keeps the shape, the
  why-now, and the applicability panel — which is most of the selling value.
- The pack stays in the account folder next to the case study. It is internal; only §1–§5 and §7 are
  ever sent.
