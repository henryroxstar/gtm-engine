# Sequence spec — template

**Who this is for:** whoever is reviewing or approving a campaign before it goes live — a sales rep,
a sales manager, or whoever hits Activate. Read top to bottom: what we're sending, who it's going to,
then whether it's cleared to launch. The send-mechanics detail (mailbox config, unsubscribe headers)
is in its own section at the end, for whoever configures the sequencer.

This is the reviewable, on-disk record — nothing gets staged in the sequencer until this is filled in
and approved. Fill every field; keep it scannable. Save to:
- single account → `content/<active>/accounts/<account-slug>/email-sequence-<slug>-<YYYY-MM-DD>.md`
- cross-account campaign → `content/<active>/prospects/sequences/<campaign-slug>-<YYYY-MM-DD>.md`

---

## Campaign snapshot

| | |
|---|---|
| **Campaign** | `<name>` |
| **Selling** | `<default_product or the product this campaign targets>` |
| **Why now** | `<the dated 🔥 trigger this campaign is built on, + source URL>` |
| **Sending as** | `<from address / mailbox>` · mailbox health: `<score/status>` |
| **Schedule** | `<days>` · `<time window>` · `<IANA timezone>` |
| **Status** | DRAFT → staged PAUSED · Sequence link: `<filled in after staging>` |
| **Profile / tool** | `<active>` via `<email_tool: saleshandy/apollo/gmass/manual>` |

## What we're sending

| # | Day | New thread or reply? | Subject | The ask | Variant |
|---|-----|--------|---------|-----|---------|
| 1 | 0 | new | `<1–4 words, lowercase>` | offer (gates the gift) | A |
| 2 | +2–3 | reply | … | … | A |
| 3 | +5 | reply | … | … | A |
| 4 | +7–10 | new | … | … | A |

**Touch 1 — day 0 — `<subject>`**
```
Hi <first name>,
<signal → one clause of credit → likely structural gap → one proof sentence → one CTA>
<signature from PROFILE email_signature>
```
Word count: `<n>`. Plain text · ≤1 untracked link · no time-ask.

**Touch 2 — day +2–3 (reply, same thread) — `<subject>`**
```
…
```

_(repeat per touch; include any A/B variant body directly under its touch)_

## Who's on this list

| Name | Title | Company | Email | Verified? | Segment | Notes |
|------|-------|---------|-------|-----------|---------|-------|
| … | … | … | … | ✅ / ⚠️ unverified | startup/enterprise | 🆕 new-in-role, etc. |

Only ✅-verified emails get enrolled. ⚠️ rows are listed here for visibility but **excluded** from
sending — we never guess an address.

## Before this goes live

- [ ] Copy reviewed and approved, touch by touch
- [ ] Lead list looks right; anyone unverified (⚠️ above) is understood to be excluded, not sent to
- [ ] Schedule + timezone correct
- [ ] Compliance checks below are confirmed (address · opt-out · markets in scope)
- [ ] Sending mailbox is warm and healthy — see `docs/email-deliverability.md`
- [ ] **Someone activates / resumes the sequence in the provider** ← this is the actual send switch.
      Nothing sends until a human does this — not this spec, not the skill that built it.

---

## For the ops team — compliance preflight

Checked live against the sequencer before any lead is enrolled — confirmed by whoever is activating
the campaign, not assumed from a previous run. Full procedure: `docs/email-compliance.md`. Produced
by `uv run python -m gtm_core.email_compliance preflight … --markdown` (exit code 1 = do not load).

| Check | Source of truth | Found | Confirmed |
|---|---|---|---|
| Physical postal address | mailbox `signature` setting (`list_email_accounts`) | `<legal entity + street address, per mailbox>` | ☐ |
| One-click unsubscribe header | sequence setting code 13 | `<0 / 1>` | ☐ |
| Opt-out link / text | sequence settings codes 1 / 2 | `<text or link>` | ☐ |
| Markets in scope | PROFILE `target_markets` | `<markets>` · `<n>` leads dropped as out-of-market | ☐ |
| Live test send (once per sending domain) | received mail carries signature + `List-Unsubscribe` | `<date checked, or NOT YET>` | ☐ |

What markets in scope actually require is a legal question, decided with counsel — neither this spec
nor this repo answers it; `target_markets` is that standing decision. Confirmed on `<date>` — a
confirm does not carry across runs.
