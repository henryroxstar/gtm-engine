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
vendor instrumented still appears if it describes the customer's operations.

For each `modeled` figure, state the baseline and the assumption in one line so the customer can
challenge the arithmetic rather than the conclusion:

- **[Claim]:** baseline `[value, source, date]` × assumption `[stated assumption]` → `[result]`.

## 5. Sensitive detail check
Confirm none of the following is present without explicit approval:

- ☐ Named third parties (their vendors, customers, regulators) — [list any]
- ☐ Contract value, pricing, or commercial terms
- ☐ Volumes, headcount, or capacity that could be commercially sensitive
- ☐ Security architecture detail beyond what the customer publishes
- ☐ Anything under NDA, or discussed on a call marked confidential
- ☐ Regulatory status stated as fact rather than as the customer describes it
- ☐ Individuals who did not consent to being named

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
5. Return by [date] — after that we hold the draft rather than assume approval.

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
