# Email Deliverability — sending infrastructure

**For: GTM · Scope: the machinery that decides whether your mail arrives at all — authentication,
provider rules, domains, warmup, complaint rate. Company-agnostic.**

> **You probably do not need to read this.** Set it up once per sending domain, confirm it in your
> sequencer, and never think about it again. It is DNS records and vendor settings, owned by your
> sequencer and whoever runs your mail — not by whoever writes the email. Jump to the
> [pre-flight checklist](#pre-flight-checklist), work through it when you stand up a new domain, and
> come back only when a provider changes the rules or your inbox placement drops.

Its three siblings, and where each boundary falls:

| Doc | Owns | Enforced by |
|---|---|---|
| **this file** | authentication, provider requirements, domains, warmup, reputation | **Gmail, Yahoo, Outlook** — they decide delivery |
| [`email-optimization.md`](email-optimization.md) | subject lines, first-touch anatomy, cadence, follow-ups, personalisation, measurement | **the recipient** — they decide whether to reply |
| [`email-compliance.md`](email-compliance.md) | postal address, opt-out, market gating, suppression, the pre-load check | **regulators, and you** |

Nothing here is legal guidance. Provider rules are not law: they are private policy, they are
stricter than law in places, and meeting them says nothing about whether you may email someone.
That question lives in [`email-compliance.md`](email-compliance.md).

Everything below is verified against the primary sources — Google, Yahoo and Microsoft postmaster
documentation and the RFCs. Where a figure is practitioner consensus rather than provider-published,
it says so inline.

---

## 1. The three tiers of rules (know which apply to you)

There is a common misconception that the 2024 sender rules "only apply at 5,000 emails/day." Half of
them apply to **everyone**. Google's guidelines are explicitly two-tier
([Gmail Email sender guidelines, in force since 1 Feb 2024 — support.google.com/a/answer/81126](https://support.google.com/a/answer/81126)):

| Tier | Who | Must do |
|---|---|---|
| **All senders** (any volume to Gmail) | You, always | SPF **or** DKIM; valid forward + reverse DNS (PTR); TLS for SMTP; spam rate <0.3% in Postmaster Tools; RFC 5322-valid formatting; don't spoof Gmail `From:` headers |
| **Bulk senders** (5,000+/day to Gmail) | High-volume programs | Everything above **plus**: SPF **and** DKIM (both); DMARC (`p=none` minimum); DMARC alignment; one-click unsubscribe (RFC 8058) on marketing/promo mail |
| **Cold-outbound hygiene** (practitioner layer) | Anyone doing outbound | Dedicated domain, warmup, per-inbox volume caps, plain text, list verification — *not* provider-mandated, but what keeps you off the reputation cliff |

The practical read: even a low-volume outbound operator sending 40 emails/inbox/day is judged on
authentication, complaint rate, and reputation exactly like a big sender — the aggregate reputation of
your domain and IP is what the filter scores, not any single send.

## 2. SPF, DKIM, DMARC (and BIMI, briefly)

These three are the identity layer. All three, aligned, before you send a single cold email.

- **SPF (Sender Policy Framework)** — a DNS TXT record listing which servers may send for your domain.
  Answers "is this server allowed to send as us?" Publish it for every sending domain/subdomain.
- **DKIM (DomainKeys Identified Mail)** — a cryptographic signature on each message; the public key
  lives in DNS. Answers "was this message altered, and does the domain vouch for it?" Use a modern key
  (2048-bit).
- **DMARC (Domain-based Message Authentication, Reporting & Conformance)** — the policy that ties SPF
  and DKIM to the visible `From:` domain (**alignment**) and tells receivers what to do on failure
  (`none` → monitor, `quarantine` → spam, `reject` → block). Providers require **at least `p=none`**;
  moving to `quarantine`/`reject` once your reports are clean is stronger protection against spoofing
  ([Gmail sender guidelines FAQ, updated 2025 — support.google.com/a/answer/14229414](https://support.google.com/a/answer/14229414)).
  **Alignment is the part people miss:** the `From:` organizational domain must match the SPF or DKIM
  domain. Mail can pass SPF and still fail DMARC if it isn't aligned.
- **BIMI (Brand Indicators for Message Identification)** — *not* a deliverability requirement; it
  displays your verified logo next to authenticated mail. It requires DMARC at **`quarantine` or
  `reject`** (`p=none` is insufficient) plus a Verified Mark Certificate (VMC), or a Common Mark
  Certificate (CMC) at Gmail
  ([BIMI Group FAQ, 2025 — bimigroup.org](https://bimigroup.org/faqs-for-senders-esps/)). Relevant to
  brand/marketing mail; largely irrelevant to 1:1 cold outbound from a throwaway domain.

## 3. The Google & Yahoo bulk-sender requirements (Feb 2024, tightened through 2025–26)

Google and Yahoo announced aligned requirements that took effect **1 February 2024**. Yahoo's mirror
Google's almost exactly (SPF + DKIM + DMARC, one-click unsubscribe, complaint rate <0.3%)
([Yahoo Sender Hub best practices, 2024–25 — senders.yahooinc.com](https://senders.yahooinc.com/best-practices/)).
The core bulk requirements: authenticate with SPF **and** DKIM, publish DMARC (`p=none`+), keep the
Postmaster-reported spam rate under 0.3%, and offer one-click unsubscribe on marketing mail with the
request honored within **2 days / 48 hours**
([Gmail sender guidelines FAQ, updated 2025](https://support.google.com/a/answer/14229414)).

**2025–26 escalation — enforcement got real:**
- Google **retired the legacy Postmaster Tools dashboard in October 2025** and shipped Postmaster Tools
  v2 with a binary **Compliance Status** (you pass or you fail) — no more soft signals
  ([Security Boulevard summary of Google/Yahoo 2025 updates — securityboulevard.com, Nov 2025](https://securityboulevard.com/2025/11/google-and-yahoo-updated-email-authentication-requirements-for-2025/)).
- From **November 2025**, Gmail ramped enforcement: non-compliant mail now gets temporary and
  **permanent 5xx rejections at the SMTP level**, not just spam-foldering
  ([Red Sift, Gmail enforcement ramps up, 2025 — redsift.com](https://redsift.com/resources/blog/gmails-enforcement-ramps-up-what-bulk-senders-need-to-know)).

## 4. Microsoft / Outlook's 2025 requirements

Microsoft followed with its own high-volume rules. For domains sending **5,000+ messages/day** to
consumer mailboxes (**outlook.com, hotmail.com, live.com**), Outlook now requires SPF **and** DKIM
**and** DMARC (`p=none` minimum, aligned to SPF or DKIM). Effective **5 May 2025**, non-compliant mail
was first routed to **Junk**, then hard-**rejected** with `550 5.7.515 Access denied, sending domain
… does not meet the required authentication level`
([Microsoft Tech Community, Outlook's new requirements for high-volume senders, Apr 2025](https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730);
[Mailgun, Microsoft sender requirements, 2025 — mailgun.com](https://www.mailgun.com/blog/deliverability/microsoft-sender-requirements/)).
Microsoft additionally recommends a valid, reply-capable `From`/`Reply-To` and a visible unsubscribe —
but notably has **not** mandated RFC 8058 one-click unsubscribe the way Gmail/Yahoo have, and has **not**
published a numeric complaint-rate threshold. Treat Gmail's 0.1%/0.3% as your universal guide anyway.

## 5. One-click unsubscribe (RFC 8058) and the List-Unsubscribe header

For marketing/promotional bulk mail, Gmail and Yahoo require **one-click unsubscribe** per
[RFC 8058 (IETF, 2017 — datatracker.ietf.org/doc/html/rfc8058)](https://datatracker.ietf.org/doc/html/rfc8058).
Mechanically:

- **`List-Unsubscribe`** header MUST contain one HTTPS URI (and MAY also contain a `mailto:`).
- **`List-Unsubscribe-Post`** header MUST contain exactly `List-Unsubscribe=One-Click`.
- The message MUST carry a valid **DKIM signature covering both headers**.
- A **POST** to the URI performs the unsubscribe; a **GET** must **not** silently unsubscribe (at most
  show an info page) — this prevents mail-scanners' link-prefetching from mass-unsubscribing people.

Two nuances for cold outbound: (1) these headers are a *marketing/bulk* requirement — a genuine 1:1
plain-text cold email isn't the same category, but this repo carries a plain-text opt-out line on
every send regardless ([`email-compliance.md`](email-compliance.md)); (2) a visible
`List-Unsubscribe` header can slightly *help* cold
deliverability by signaling good-faith list management. Don't confuse the header with a tracked
"unsubscribe" pixel-link — the header is the clean mechanism.

## 6. Spam-complaint rate: run to 0.1%, fear 0.3%

The single reputation number that will sink you. Google's guidance: keep the Postmaster-reported spam
rate **below 0.1%** and **never** let it reach **0.3%** — that's fewer than ~5 complaints per 5,000
messages, and hitting 0.3%+ makes you ineligible for reliable delivery
([Gmail sender guidelines FAQ, updated 2025](https://support.google.com/a/answer/14229414)).

Caveat for cold outbound: you often **can't see** this number, because Google Postmaster Tools reports
require meaningful daily volume to a domain and cold operators spread low volume across many inboxes.
So the complaint rate is managed *indirectly* — tight targeting, honest subject lines, an easy opt-out,
and immediate suppression of anyone who asks to stop. One complaint per few hundred sends is already a
warning sign at cold-outbound volumes.

## 7. Domains: dedicated sending domain, subdomains, custom tracking domain

- **Never send cold from your primary domain.** A complaint spike or blacklisting on a cold campaign
  can wreck the deliverability of the domain your whole business runs on. Use a **separate, dedicated
  domain** for outbound
  ([Growleads, subdomain vs primary, 2026 — growleads.io](https://growleads.io/blog/subdomain-for-cold-email-protect-main-domain/)).
- **Subdomain vs separate lookalike domain.** A subdomain (`go.yourco.com`) still shares some
  reputation lineage with the root, so problems can bleed upward; a separately registered lookalike
  (`try-yourco.com`) isolates risk most cleanly. Practitioner rule of thumb: subdomains are acceptable
  under ~500/day or when the root has years of positive history; otherwise prefer separate domains.
  *(Reputation-inheritance specifics here are practitioner consensus, not provider-published — treat as
  directional.)*
- **Custom tracking domain (CTD).** If you track opens/clicks, route them through a CNAME on *your*
  domain (`track.yourco.com`), not the tool's shared `track.provider.com` — a shared tracking domain
  means a spammer elsewhere on the platform can get it blacklisted and drag your mail down with it
  ([Instantly, tracking pixels & deliverability, 2025 — instantly.ai](https://instantly.ai/blog/email-tracking-and-deliverability-why-tracking-pixels-can-hurt-your-inbox-placement/)).
  Better still for cold: **turn open-tracking off entirely** (see §1.9 and §8) and skip the pixel.

## 8. Domain & IP warmup

New domains and IPs have no reputation; blasting from cold looks exactly like a spammer. Warm up over
**2–4 weeks minimum**: start at ~5–10 sends/day per inbox and ramp gradually (roughly +5/day or
doubling every few days), settling around **~30–50/inbox/day** for sustained cold volume. Scale total
output by adding inboxes/domains, not by cranking one inbox
([Ozigi, warm up a sending domain, 2026 — blog.ozigi.app](https://blog.ozigi.app/blog/how-to-warm-up-sending-domain-2026)).
Warmup also means seeding genuine-looking engagement (replies, folder moves) early. *(Exact ramp
schedules and per-inbox caps are vendor/practitioner heuristics — no mailbox provider publishes a
number. The direction — slow ramp, low per-inbox volume — is universally agreed; the specific figures
are not authoritative.)*

## Pre-flight checklist

Run this when you stand up a new sending domain, not per campaign.

| ✔ | Item |
|---|---|
| ☐ | Dedicated sending domain (not primary); MX + DNS configured |
| ☐ | SPF published for every sending domain/subdomain |
| ☐ | DKIM signing enabled (2048-bit) |
| ☐ | DMARC record published (`p=none`+), **aligned** to SPF or DKIM |
| ☐ | Valid PTR (reverse DNS) matching forward DNS; TLS enabled |
| ☐ | Domain + mailboxes warmed 2–4 weeks; per-inbox volume capped (~30–50/day) |
| ☐ | Custom tracking domain (or open-tracking off entirely) |
| ☐ | Plain text; ≤1 untracked link; no images/attachments in touch 1 |
| ☐ | Every address verified (bounce rate near zero); **none pattern-guessed** (§1.10) |
| ☐ | Working opt-out + valid physical postal address — both sequencer config ([`email-compliance.md`](email-compliance.md)) |
| ☐ | Message identifiable as an advertisement, not disguised as personal mail |
| ☐ | Suppression list checked at **export/import** time, not just send time ([`email-compliance.md`](email-compliance.md)) |
| ☐ | Complaint rate monitored; suppress opt-outs immediately |

---

## Sources & verification log

Legend: **Verified** = confirmed against the primary/authoritative source (provider docs, RFC).
**Single-source** = one vendor/dataset, directionally credible but not independently corroborated.

### Verified against primary sources

| Claim | Source(s) | Confidence |
|---|---|---|
| Google two-tier rules: all senders need SPF **or** DKIM, PTR, TLS, spam <0.3%, RFC 5322; bulk (5,000+/day) need SPF **and** DKIM, DMARC `p=none`+ aligned, one-click unsub | [Gmail Email sender guidelines (support.google.com/a/answer/81126)](https://support.google.com/a/answer/81126) + [FAQ (14229414)](https://support.google.com/a/answer/14229414) | **Verified** (primary) |
| Spam rate: keep <0.1%, never reach 0.3%; unsub honored within 48h | [Gmail sender guidelines FAQ](https://support.google.com/a/answer/14229414) | **Verified** (primary) |
| Google/Yahoo aligned requirements effective 1 Feb 2024; Yahoo mirrors Google | [Yahoo Sender Hub](https://senders.yahooinc.com/best-practices/) | **Verified** (primary) |
| Postmaster Tools v2 (binary compliance) replaced legacy dashboard Oct 2025; Nov 2025 enforcement → 5xx SMTP rejections | [Security Boulevard (Nov 2025)](https://securityboulevard.com/2025/11/google-and-yahoo-updated-email-authentication-requirements-for-2025/); [Red Sift](https://redsift.com/resources/blog/gmails-enforcement-ramps-up-what-bulk-senders-need-to-know) | **Single-source** (secondary reporting of Google changes; directionally consistent across vendors) |
| Microsoft/Outlook: 5,000+/day to outlook/hotmail/live → SPF+DKIM+DMARC (`p=none`+, aligned); effective 5 May 2025; non-compliant → Junk then reject `550 5.7.515`; no numeric complaint threshold; no RFC 8058 mandate | [Microsoft Tech Community (Apr 2025)](https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730); [Mailgun](https://www.mailgun.com/blog/deliverability/microsoft-sender-requirements/) | **Verified** (primary blog + corroborating secondary; MS page is JS-rendered so quoted via Mailgun) |
| RFC 8058 mechanics: `List-Unsubscribe` HTTPS URI + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; DKIM must cover both headers; POST unsubscribes, GET must not | [RFC 8058 (IETF)](https://datatracker.ietf.org/doc/html/rfc8058) | **Verified** (primary/RFC) |
| BIMI requires DMARC `quarantine`/`reject` (`p=none` insufficient) + VMC (or CMC at Gmail) | [BIMI Group FAQ](https://bimigroup.org/faqs-for-senders-esps/) | **Verified** (primary) |

### Practitioner heuristics — directional, not provider-published

| Claim | Source(s) | Confidence |
|---|---|---|
| Warmup 2–4 weeks; ~5–10/day ramping to ~30–50/inbox/day; scale by adding inboxes | [Ozigi warmup guide (2026)](https://blog.ozigi.app/blog/how-to-warm-up-sending-domain-2026) | **Single-source / practitioner consensus** — no provider publishes numbers; direction agreed, figures not authoritative |
| Dedicated domain not primary; subdomain vs separate-domain risk isolation; ~500/day subdomain rule of thumb | [Growleads (2026)](https://growleads.io/blog/subdomain-for-cold-email-protect-main-domain/) | **Single-source / practitioner** |
| Custom tracking domain isolates reputation vs shared `track.provider.com` | [Instantly (2025)](https://instantly.ai/blog/email-tracking-and-deliverability-why-tracking-pixels-can-hurt-your-inbox-placement/) | **Single-source** — mechanism is sound |

**Explicitly unverified:** exact warmup ramps, per-inbox caps and subdomain volume thresholds. No
mailbox provider publishes these. The direction (slow ramp, low per-inbox volume, add inboxes rather
than crank one) is universally agreed; the specific numbers are not authoritative.
