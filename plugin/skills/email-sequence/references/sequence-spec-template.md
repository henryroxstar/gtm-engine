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

## Front block (machine-read — keep it a fenced `Key: value` block)

This block is parsed, one regex per line: `outreach_linter render` reads `Sign-off:` and the five
`slot_*:` lines, and `gtm_core.hook_coverage` reads `angle:` — from which it derives the matrix
cell this spec implements, so the campaign coverage report counts the spec instead of listing it
as undeclared. Keep the fence and the `Key:   value` shape; a markdown table here is not readable
by those gates, and a declaration written outside this fenced block is not read as one.

```
Campaign:    <name>
Profile:     <active>        Product: <default_product or the product this campaign targets>
Provider:    <saleshandy|apollo|gmass|manual>   Status: STAGED PAUSED — activated by a human in the provider UI
Sign-off:    <name>
Variant:     1 <segment/seat>-scoped x <n> touches · <n> rows
angle:       <angle-id>
slot_signal: row.signal_evidence
slot_claim:  <claim-id the angle derives>
slot_pain:   <seat the angle derives>
slot_hedge:  voice-rules.hedge.cues
slot_proof:  <proof-id the angle derives>
Gate:        tests/linter/outreach_linter.py render must report 0 errors across every touch x every row
```

**The five `slot_*:` lines say where each sentence of the body came from.** A body has five
slots — signal, claim, pain, hedge, proof — and `slot-attribution` (ERROR) refuses a spec whose
`angle:` resolves while any of them names no source. Copy whose provenance cannot be checked is
copy nobody can stand behind, and this is the field that makes "where did this sentence come
from" answerable instead of a reviewer's guess. Two of the five are presence-only by
construction: `slot_signal` is the ROW's researched fact, which varies per recipient, and
`slot_hedge` is the tenant's own cue table — neither is a registry id. The other three are
**cross-checked against the angle**, so fill them from what it derives rather than by hand:

```bash
uv run python -m gtm_core.messaging resolve --profile <active> --csv <pool.csv>
```

Use `slot_proof: none` only on the no-anchor offer shape — a reader whose market the registry
records as having no anchor. It is the one sanctioned exception, and it is accepted nowhere else.

**`angle:` is the one declared field, and everything else derives from it.** Resolve it with
`uv run python -m gtm_core.messaging resolve --profile <active> --csv <pool.csv>`; the id names a
`[[angle]]` in the tenant's `knowledge/angles.toml`, which carries the seat, the premise, the
claim, the proof and the opener kind. It is the field that makes "which argument is this?"
answerable instead of a judgement call, and it travels into outcomes — so **do not change it once
a touch has shipped**. One spec file is one variant and one angle; two specs on the same angle are
one argument wearing two subjects.

> **Changed 2026-09-24 (FR2).** `hook_cell:` and `argument_id:` are gone from this block.
> `hook_cell`, `capability`, `premise` and `stakes` are now **derived** from the angle and are
> checked against it — a declared value that disagrees is an ERROR, not a warning, because one
> fact with two declarations is a fact that can be wrong in one place and right in the other.
> `argument_id` is superseded outright: the angle id *is* the stable slug. A spec with no `angle:`
> reports `angle-missing` (WARN, because 0 of the live fleet has migrated) and loses
> `slot-attribution` entirely; an id `angles.toml` does not hold is `angle-unknown` (ERROR). Both
> fire on pack shapes the old `hook_cell` regex could not read — that fail-open is the 2026-09-04
> lesson this replaces. `hook-matrix.md` is now a **generated view** of
> `angles.toml`, so its cells are no longer a vocabulary you write against by hand.
>
> **Changed 2026-09-24 (FR3).** The campaign coverage audit now resolves a spec's matrix cell
> from its `angle:` (`hook_coverage.declared.resolve_declared_cell`), falling back to a legacy
> `hook_cell:` only where a spec declares no angle. Until it did, a spec written to this
> template counted as undeclared in the report the same skill runs, and that report's
> `hook-cell-missing` finding (since renamed `angle-missing`, which is what you will see
> today) named every one of them. The five `slot_*:` lines landed in the same change, for the mirror
> reason: `slot-attribution` was raising five ERRORs on a spec written exactly to this template.
Verify the campaign as a whole with:

```bash
uv run python -m gtm_core.hook_coverage --profile <active> --campaign <campaign-slug>
```

## Campaign snapshot (human-read — the detail no gate parses)

| | |
|---|---|
| **Why now** | `<the dated 🔥 trigger this campaign is built on, + source URL>` |
| **Argument** | `<one sentence: the claim this variant asks the reader to accept>` |
| **Sending as** | `<from address / mailbox>` · mailbox health: `<score/status>` |
| **Schedule** | `<days>` · `<time window>` · `<IANA timezone>` |
| **Sequence link** | `<filled in after staging>` |

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
