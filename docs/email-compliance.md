# Email compliance — the operator's checklist

**Read this before your first send, and any time you add a market or a sending domain.**

## This is not legal advice

Email law differs by country and changes over time. You are responsible for the email you send.
Before your first campaign, and before you enter a new country, take advice from someone qualified
to give it.

This document describes how this software works. It does not tell you what the law requires of you.
Nothing here creates a lawyer-client relationship or any warranty, the software is provided as is
under its license, and a passing check is not a compliance opinion.

The automated checks in this system look at **mechanics, not legality**. They can confirm that a
signature is not empty, that a setting is switched on, and that a contact's country is on your list.
They cannot confirm that your address is current, that your message is honest, or that your legal
basis holds. That part is yours.

---

## What the words mean, and what lives where

Most of what makes an email compliant sits in **other companies' tools**, not in this repo. That is
the single most common surprise here, so this file marks external systems as you go.

| Word used here | What it is | Where it runs |
|---|---|---|
| **this system** / **the pipeline** | the software in this repository — it drafts email, checks it, and hands it over | your machine / your server |
| **sequencer** | the third-party product that stores your campaigns and actually sends them, on a schedule, one email at a time. Yours is **Saleshandy** (Apollo and GMass are alternatives) | **external — a company you pay** |
| **mailbox** | one email address you send from, e.g. `you@yourcompany.com`, hosted on Google Workspace or Microsoft 365 | **external** |
| **mailbox provider** | the company that runs the **recipient's** inbox — Gmail, Yahoo, Outlook. It decides whether your email reaches them or lands in spam | **external — and not your customer** |
| **sending domain** | the domain your mailboxes sit on, e.g. `yourcompany.com` | **external** (registrar + DNS) |
| **contact data tool** | where prospect names and work addresses come from — RocketReach, Vibe Prospecting, Apollo | **external** |
| **DNC list** ("do not contact") | your own suppression list of people who opted out. It lives **inside the sequencer**; this repo keeps a copy | **external is the master** |

Two consequences worth internalising:

- **Settings you never see in this repo can make you non-compliant.** Your opt-out and your postal
  address are switches and text fields inside the sequencer. They are not in the email draft you
  proofread, and they arrive switched off or empty by default.
- **Anyone can change them, from anywhere, at any time.** Someone editing a setting in the sequencer's
  website changes what your next email sends. That is why this system re-checks before every load
  instead of trusting an earlier answer.

Where the rest of the detail lives: **this** file owns compliance end to end — what gets checked, by
whom, using what, before anything sends. [`email-optimization.md`](email-optimization.md) is the
craft playbook (how to write mail that gets replies) and carries no compliance guidance; its §1 does
own the **inbox-provider rules**, which are not law but decide whether your mail is delivered at all.

---

## 1. What every commercial send must carry

Four things that commercial-email regimes generally require in some form. Which of them apply to
you, and in exactly what form, depends on where your recipients are — confirm that before you treat
this list as complete.

| # | Requirement | Where it actually lives | In this repo? |
|---|---|---|---|
| 1 | **Truthful identity** — `From`, `Reply-To`, routing and subject should not mislead about who is writing or what the message is | the mailbox you send from, plus the subject line in your draft | mailbox: **no, external**. Subject: yes |
| 2 | **A working opt-out** — easy to find, free, and asking nothing of the recipient beyond the request itself | a setting inside the **sequencer**, per campaign — *not* the email text | **no — external** |
| 3 | **A valid physical postal address** for the sender | the **mailbox signature** in the sequencer, added to every email automatically — *not* the email text | **no — external** |
| 4 | **Identifiable as an advertisement** — a commercial message should not be engineered to read as personal correspondence | the wording of your first email | yes — it's the draft |

All four are **your** responsibility, wherever they physically live. So is a deadline you do not
set: opt-outs have to be honoured within a fixed window, and the window differs by market. Suppress
the same day, permanently, across every address you hold for that person, and the question never
comes up.

Note what items 2 and 3 have in common: they are **switches and text boxes inside someone else's
website**. They never appear in the email draft you review, and they start off empty or switched off.
That is why this system checks them by machine before every load instead of trusting a read-through.

## 2. The automated pre-load check

This runs inside this repo, before any contact is loaded into the sequencer, and you can run it by
hand any time. It reads two files of live data pulled **out of the sequencer** — the list of your
mailboxes, and the settings of the campaign you are about to load — plus your local contact list:

```bash
uv run python -m gtm_core.email_compliance preflight --profile <active> \
  --accounts-json /tmp/accounts.json \
  --settings-json /tmp/settings.json \
  --leads-csv content/<active>/prospects/sequences/ready-to-load.csv
```

`accounts.json` and `settings.json` are the sequencer's own responses (`list_email_accounts` and
`get_sequence_settings`), saved verbatim — no editing, no tidying. **Exit code 1 means do not load.**

| Check | Passes when | Notes |
|---|---|---|
| postal address | every mailbox you are about to send from has a signature that is not empty and looks like an address | prints each signature — *you* confirm it is real and current |
| opt-out | the campaign's one-click unsubscribe header is on **and** an unsubscribe link or line is set | sequencers commonly ship this off — check it, never assume it |
| markets | every contact's country is inside `target_markets` in your profile | blank country = **warning**, never a pass; `--strict-market` makes it a failure |

Useful flags: `--markdown` (emits the table for the sequence spec), `--market` (override
`target_markets` ad hoc), `--strict-market`.

**What it cannot check, and you must:** whether the address is accurate; whether the copy is
identifiable as an ad rather than disguised as personal mail; whether you have a documented lawful
basis in a consent-based market; whether an opt-out from three weeks ago was actually suppressed;
and whether these three checks are even the right list for the markets you send to.

## 3. Prove it once per sending domain

Configuration flags are not proof that a recipient sees anything. Before the first activation on a
**new sending domain**, send one live email from that mailbox to an address you control and confirm
the received message actually carries:

- the signature block, with the legal entity and postal address;
- a `List-Unsubscribe` header (in Gmail: **Show original**);
- a visible opt-out line in the body.

Record the date in the sequence spec. Re-do it whenever you add a domain or change a signature.

## 4. Markets are not interchangeable

`target_markets` in `profiles/<active>/PROFILE.md` is the allowed-jurisdiction list, enforced twice:

1. **When the contact pool is built.** The consolidation sweep
   (`python -m gtm_core.prospects_consolidate consolidate --profile <active>`) drops out-of-market
   contacts from both the ready list and the hold queue, so they never become something you have to
   remember to strip later. They stay in the master list — this is a load gate, not a delete, so
   changing the market list re-admits them. Add `--strict-market` to also drop contacts whose
   country is unknown.
2. **Before loading into the sequencer.** The preflight re-checks the actual list being loaded, in
   case it was hand-built or predates a market change.

**Adding a market is a legal decision, not a targeting one.** Consent rules, disclosure duties,
opt-out deadlines and penalties all differ, and some markets are materially stricter than whatever
is already on your list. Before you add one, establish what it requires — from the regulator's own
published guidance, and from counsel. Keep the list as short as the business genuinely needs.

**`target_markets` accepts either bare or quoted list items** — `[United States, Singapore]` and
`['United States', 'Singapore']` both parse the same way. A per-item leading/trailing `'` or `"` is
stripped; use whichever style is easiest to write.

**`Global` is a wildcard, not a country**, and it is an exception to "keep the list short" above: if
`target_markets` contains the literal word `Global` (any case), the jurisdiction check is disabled
entirely — every country passes. This is a deliberate, all-or-nothing opt-out of the gate, not a
shorthand for "the countries I happen to sell in": adding it means you have separately confirmed
compliance across every jurisdiction you actually send to, since the automated check can no longer
do that confirming for you. Prefer an explicit country list when the business realistically sends to
a bounded set of markets — `Global` is for the case where it does not.

Primary regulators, for you or your counsel to read. This repo does not summarise them and does not
track their changes:

| Market | Regulator |
|---|---|
| United States | [FTC — CAN-SPAM business guidance](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) |
| Canada | [Government of Canada — CASL](https://ised-isde.canada.ca/site/canada-anti-spam-legislation/en) |
| European Union | your national data-protection authority; [EDPB](https://www.edpb.europa.eu/) |
| United Kingdom | [ICO — direct marketing and PECR](https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/) |
| Singapore | [PDPC](https://www.pdpc.gov.sg/) · [IMDA — unsolicited communications](https://www.imda.gov.sg/infocomm-regulation-and-guides/unsolicited-communications/best-practices-for-organisations) |
| Australia | [ACMA — spam rules for business](https://www.acma.gov.au/spam-rules-businesses) |

A lead with **no country on file is not in-market** — it is unresolved. Resolve or exclude it.
"No one has complained yet" is not a lawful basis.

## 5. Opt-outs

Any reply that reads as "stop", "unsubscribe", "remove me", or "not interested" is an opt-out — in
the body or the subject, however grumpy or ambiguous. Never reply to confirm one, and never ask the
person to justify it or to give you anything beyond the request itself. Treat every ambiguous reply
as an opt-out; the cost of being wrong runs one way only.

**The master copy of your suppression list lives in the sequencer, not here.** This repo keeps a
mirror so it can filter contacts before they are ever loaded, but the sequencer's list is what stops
a send. An opt-out that never reaches that list will be emailed again. `inbound-triage` therefore
either writes it there (when the connected tools allow a write) or hands you the address as an open
action item — it does not treat "noticed" as "handled".

Do not assume some registry, list broker, or sequencer default is doing this for you. **There is no
email suppression feed to subscribe to** — the national registries we checked (US, Singapore,
Australia) cover telephone and SMS channels, not email, so there is nothing external to query. The
only controls that exist are your own list and the sequencer's, which is why the list is a
first-class object in this pipeline rather than a spreadsheet someone keeps.

**Suppression is an export-time obligation, not just a send-time one.** The sequencer's list applies
when *it* sends. Anything this pipeline exports, hands to another system, or re-imports has to be
suppressed before it leaves — "the sequencer will catch it" is not true of a list that never passes
through the sequencer's send path.

**Related control — never guess an address.** Addresses inferred from a pattern
(`first.last@company.com`) rather than resolved are blocked outright: `prospects_consolidate.py`
classifies RocketReach `F(pattern)` results as `blocked`, never loadable, logged to
`.blocked-log.jsonl`. Do not relax it for yield. (Why it also wrecks deliverability:
[`email-optimization.md` §1.2](email-optimization.md).)

## 6. Which tool does what — and what none of them do

Everything in the first four rows is **external**: another company's product, changed through its own
website, by anyone with access.

| Tool | What it does for you | What it does **not** do |
|---|---|---|
| **Sequencer** — Saleshandy (or Apollo, GMass) · *external* | stores campaigns, sends on a schedule, attaches your signature, adds the unsubscribe header, holds the master suppression list | make you compliant by default. Defaults vary between products and change over time — the unsubscribe header may well be off until you switch it on |
| **Mailbox + sending domain** — Google Workspace, Microsoft 365 · *external* | hosts the address you send from, carries your signature and authentication records | tell you whether the address in that signature is current or correct |
| **Contact data** — RocketReach, Vibe Prospecting, Apollo · *external* | finds and verifies work email addresses | give you permission to email anyone. Their terms also govern what you may do with the data — read them |
| **Inbox providers** — Gmail, Yahoo, Outlook · *external, and on the recipient's side* | decide whether your email reaches the inbox at all, using their own published rules | enforce the law. Their rules are their own, separate from it, and stricter in places — see [`email-optimization.md` §1](email-optimization.md) |
| **This repo's preflight** — runs locally | checks the mechanics deterministically, before every load, and blocks the load if one fails | replace legal review, or verify that what it read is actually true |

Warmup and deliverability tooling is worth having, but note what it is: it builds sending reputation.
It has no effect on legality. A well-warmed domain sending non-compliant email is just sending
non-compliant email more efficiently.

## 7. Before you activate — the operator's confirm

This system builds the campaign and stops. **Activating it is a button you press, in the sequencer's
website — nothing in this repo can send email.** Before you press it, you personally confirm:

- [ ] I have established what my markets require, and this campaign meets it.
- [ ] The preflight passed (address · opt-out · markets), and I read the signature it printed back.
- [ ] The postal address in that signature is my current registered address.
- [ ] I sent one test email from this domain to myself and saw the address and the unsubscribe header arrive.
- [ ] The email is honest about who I am and that I am selling something.
- [ ] Every contact is in a market I can defend; unknown-country contacts are resolved or removed.
- [ ] Everyone who opted out before is suppressed, at every address I hold for them.

None of this carries over to the next campaign. The settings live in another company's product and
can change between runs, so the check is repeated every time.
