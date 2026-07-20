# Sequence spec — template

The reviewable, on-disk source of truth the operator approves **before** anything is staged, and the
record of what was staged. Fill every field; keep it scannable. Save to:
- single account → `content/<active>/accounts/<account-slug>/email-sequence-<slug>-<YYYY-MM-DD>.md`
- cross-account campaign → `content/<active>/prospects/sequences/<campaign-slug>-<YYYY-MM-DD>.md`

---

```
Campaign:        <name>
Profile:         <active>
Product:         <default_product or the product this targets>
Provider:        <email_tool>        (saleshandy | apollo | gmass | manual)
Sending account: <from address / mailbox>          Health: <score/status>
Schedule:        <days> · <time window> · <IANA timezone>
Status:          DRAFT → staged PAUSED   ·   Sequence id/link: <filled after staging>
Signal basis:    <the dated 🔥 "why now" this campaign is built on, + source URL>
```

## Touches

| # | Day | Thread | Subject | CTA | Variant |
|---|-----|--------|---------|-----|---------|
| 1 | 0 | new | `<1–4 words, lowercase>` | offer (gates the gift) | A |
| 2 | +2–3 | same | … | … | A |
| 3 | +5 | same | … | … | A |
| 4 | +7–10 | new | … | … | A |

### Touch bodies

**Touch 1 — day 0 — <subject>**
```
Hi <first name>,
<signal → one clause of credit → likely structural gap → one proof sentence → one CTA>
<signature from PROFILE email_signature>
```
Word count: <n>. Plain text · ≤1 untracked link · no time-ask.

**Touch 2 — day +2–3 (same thread) — <subject>**
```
…
```

_(repeat per touch; include any A/B variant body under its touch)_

## Leads

| Name | Title | Company | Email | Verified? | Segment | Notes |
|------|-------|---------|-------|-----------|---------|-------|
| … | … | … | … | ✅ / ⚠️ unverified | startup/enterprise | 🆕 new-in-role, etc. |

Only ✅-verified emails enroll; ⚠️ rows are listed but **excluded** from enrollment (never guess an
address).

## Activation checklist (the operator does this — the skill does not)

- [ ] Copy reviewed and approved touch-by-touch
- [ ] Sending account is warm + healthy; SPF/DKIM/DMARC in place (see `docs/email-optimization.md`)
- [ ] Lead list correct; unverified rows understood as excluded
- [ ] Schedule + timezone correct
- [ ] **Operator activates / resumes the sequence in <provider>** ← the send gate; never the skill
