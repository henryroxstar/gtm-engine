# Cold Email & Sequence Optimization Playbook

**For: GTM**
**Scope: cold/outbound email and multi-step sequences — deliverability, message craft, cadence, personalization, compliance, and measurement. Company-agnostic; tune voice, ICP, and signature to the active profile (`PROFILE.md`, `knowledge/voice.md`, `icp-personas.md`).**

> **Voice & targeting live in the profile, not here.** This doc is the *method* — how to land in the
> inbox and earn a reply. *Who* you write to, *what* signal you open on, and the *register* you write
> in come from the active profile's `voice.md` + `icp-personas.md`. Every principle below is written to
> layer under whatever voice the profile specifies.

This is a prescriptive playbook, not a survey. Deliverability rules are verified against the primary
sources (Google/Yahoo/Microsoft postmaster docs, the FTC, and the RFCs) — those are load-bearing and
you should treat them as hard. Performance numbers (reply rates, "best length," follow-up shares) come
from vendor datasets that disagree with each other; where the data is soft or contested, this doc says
so and marks it. Templates are in the back half. **Compliance section is general information, not legal
advice.**

---

## 0. The 10-second version (do these and you've captured most of the upside)

1. **Send from a separate domain, never your primary.** A dedicated cold-outbound domain (or a clean
   lookalike) isolates the reputation risk so a bad campaign can't burn your corporate mail.
2. **Authenticate everything: SPF + DKIM + DMARC, aligned.** This is now a hard gate at Gmail, Yahoo,
   and Outlook — not a nicety. Unauthenticated bulk mail is rejected, not just filtered.
3. **Warm the domain/mailboxes for 2–4 weeks and cap volume per inbox.** New domains have no
   reputation; ramp slowly (~5–10/day → ~30–50/day per inbox). Spread volume across inboxes, not one.
4. **Keep spam complaints under 0.1%, never near 0.3%.** The 0.3% figure is the ceiling that gets you
   throttled/blocked; 0.1% is the number to actually run to.
5. **Plain text. One link at most. No images, no tracking pixel if you can avoid it.** Cold mail should
   read like a person typed it, not a newsletter. Design-heavy HTML reads as bulk and filters harder.
6. **Win the subject line in ~3–5 words, lowercase, specific to them.** No spammy words, no ALL-CAPS,
   no `!!!`. It should look like an internal note, not a campaign.
7. **First touch: short (aim ~50–90 words), one idea, one CTA — and do NOT ask for time.** A hard
   meeting ask in the cold email depresses replies; ask for *interest*, not a calendar slot.
8. **Follow up. 3–4 touches, spaced out.** A large share of replies come from follow-ups, and the
   first follow-up is usually the best-performing single message. Add a new angle each time — never
   "just bumping this."
9. **Stop measuring opens — measure replies, positive replies, and meetings.** Apple Mail Privacy
   Protection made open rates noise; roughly half of "opens" are machine pre-fetches. Reply is the
   first trustworthy signal.
10. **Comply by default.** Real sender identity, a working opt-out, a physical postal address, honor
    unsubscribes fast. Cold B2B is legal in most markets *if* you do this; sloppiness is what's illegal.

---

## 1. Deliverability foundations (get this wrong and nothing else matters)

Deliverability is upstream of every other tactic in this doc. The best-written sequence in the world
scores zero replies from the spam folder. This section is the one place where you should follow the
rules literally — they come from the mailbox providers themselves.

### 1.1 The three tiers of rules (know which apply to you)

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

### 1.2 SPF, DKIM, DMARC (and BIMI, briefly)

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

### 1.3 The Google & Yahoo bulk-sender requirements (Feb 2024, tightened through 2025–26)

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

### 1.4 Microsoft / Outlook's 2025 requirements

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

### 1.5 One-click unsubscribe (RFC 8058) and the List-Unsubscribe header

For marketing/promotional bulk mail, Gmail and Yahoo require **one-click unsubscribe** per
[RFC 8058 (IETF, 2017 — datatracker.ietf.org/doc/html/rfc8058)](https://datatracker.ietf.org/doc/html/rfc8058).
Mechanically:

- **`List-Unsubscribe`** header MUST contain one HTTPS URI (and MAY also contain a `mailto:`).
- **`List-Unsubscribe-Post`** header MUST contain exactly `List-Unsubscribe=One-Click`.
- The message MUST carry a valid **DKIM signature covering both headers**.
- A **POST** to the URI performs the unsubscribe; a **GET** must **not** silently unsubscribe (at most
  show an info page) — this prevents mail-scanners' link-prefetching from mass-unsubscribing people.

Two nuances for cold outbound: (1) these headers are a *marketing/bulk* requirement — a genuine 1:1
plain-text cold email isn't the same category, but including a plain-text opt-out line is still best
practice and legally safer (see §7); (2) a visible `List-Unsubscribe` header can slightly *help* cold
deliverability by signaling good-faith list management. Don't confuse the header with a tracked
"unsubscribe" pixel-link — the header is the clean mechanism.

### 1.6 Spam-complaint rate: run to 0.1%, fear 0.3%

The single reputation number that will sink you. Google's guidance: keep the Postmaster-reported spam
rate **below 0.1%** and **never** let it reach **0.3%** — that's fewer than ~5 complaints per 5,000
messages, and hitting 0.3%+ makes you ineligible for reliable delivery
([Gmail sender guidelines FAQ, updated 2025](https://support.google.com/a/answer/14229414)).

Caveat for cold outbound: you often **can't see** this number, because Google Postmaster Tools reports
require meaningful daily volume to a domain and cold operators spread low volume across many inboxes.
So the complaint rate is managed *indirectly* — tight targeting, honest subject lines, an easy opt-out,
and immediate suppression of anyone who asks to stop. One complaint per few hundred sends is already a
warning sign at cold-outbound volumes.

### 1.7 Domains: dedicated sending domain, subdomains, custom tracking domain

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

### 1.8 Domain & IP warmup

New domains and IPs have no reputation; blasting from cold looks exactly like a spammer. Warm up over
**2–4 weeks minimum**: start at ~5–10 sends/day per inbox and ramp gradually (roughly +5/day or
doubling every few days), settling around **~30–50/inbox/day** for sustained cold volume. Scale total
output by adding inboxes/domains, not by cranking one inbox
([Ozigi, warm up a sending domain, 2026 — blog.ozigi.app](https://blog.ozigi.app/blog/how-to-warm-up-sending-domain-2026)).
Warmup also means seeding genuine-looking engagement (replies, folder moves) early. *(Exact ramp
schedules and per-inbox caps are vendor/practitioner heuristics — no mailbox provider publishes a
number. The direction — slow ramp, low per-inbox volume — is universally agreed; the specific figures
are not authoritative.)*

### 1.9 Plain text vs HTML, and link/image/attachment discipline

For **cold** outbound the consensus is decisive: **plain text wins.** It reads like a human wrote it,
it avoids Gmail's Promotions-tab classifier, and it strips the exact elements filters treat as
bulk-mail tells — tracking pixels, multiple links, embedded images, heavy styling
([Hunter.io, is HTML harming your cold deliverability, 2025 — hunter.io](https://hunter.io/blog/is-html-harming-your-cold-email-deliverability/)).

Discipline for a cold first touch:
- **No images, no logos, no attachments.** Corporate mail clients block external images by default, so
  a mockup renders as a broken-image box; attachments on a first send degrade the spam profile.
- **At most one link, untracked, never a shortener.** Shorteners (`bit.ly`) and redirect chains are
  classic spam signals. Zero links is often safer for touch 1 — describe the asset and gate it behind a
  reply.
- **No calendar links until they've replied.**
- If you must send HTML, use **multipart MIME** (a plain-text alternative alongside), keep it minimal,
  and keep the text-to-HTML ratio sane.

> ⚠️ **Source-quality note:** the eye-popping figures for plain-text superiority — e.g., "plain text
> gets 15–25% more replies," "HTML bounce rates 652% higher" — come from single deliverability-vendor
> studies with undisclosed methodology. The **direction is reliable and widely corroborated** (plain,
> link-light, image-free cold mail out-delivers designed HTML); the **exact multiples are marketing
> numbers** — don't quote them as fact.

### 1.10 Common spam-filter triggers (avoid)

- **Spammy words/phrases:** "free," "guarantee," "act now," "limited time," "risk-free," "make
  $$$," "100% free." Write the way you'd message a colleague.
- **Formatting tells:** ALL-CAPS, `!!!`, `$$$`, lots of bold/colored text, giant fonts.
- **Link/image tells:** multiple links, shortened links, image-only emails, mismatched display-vs-actual
  URLs.
- **List tells:** high bounce rate (verify every address before sending — dead addresses spike your
  bounce rate and torch reputation), spam-trap hits from scraped/purchased lists.
- **Volume tells:** sudden volume spikes, identical body text across thousands of sends (see spintax,
  §6), sending outside business hours in a machine-gun pattern.

### Deliverability pre-flight checklist

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
| ☐ | Every address verified (bounce rate near zero) |
| ☐ | Working opt-out + valid physical postal address (see §7) |
| ☐ | Complaint rate monitored; suppress opt-outs immediately |

---

## 2. Subject lines & preview text

The subject's only job is to earn the open without looking like a campaign. For cold B2B:

- **Length: short. ~3–5 words / under ~40 characters.** Mobile truncates around 30–35 characters, and
  the shortest subjects (2–4 words) tend to test best. A subject that reads like a personal/internal
  note beats a clever "marketing" line
  ([Belkins, B2B subject-line study, 2025 — belkins.io](https://belkins.io/blog/b2b-cold-email-subject-line-statistics)).
- **Lowercase or sentence case > Title Case.** Title Case reads like a broadcast. Lowercase reads like
  a human typed it in a hurry.
- **Specific to them, or a genuine question.** Name their signal, their company, their situation —
  `agents in production at [co]`, `your March launch`, `question on [their thing]`. Vague-but-curious
  ("Quick question") is overused and screens as sales.
- **Curiosity, not clickbait.** A curiosity gap the body actually pays off is fine; a subject that
  oversells ("This will 10x your pipeline") wins the open and kills the reply — worse than a boring
  subject.
- **Avoid the spam tells:** spam-trigger words, ALL-CAPS, emoji, multiple punctuation marks (`!!!`,
  `???`). Emoji in particular reads as marketing in B2B and can nudge filters.
- **Preview/preheader text** (the snippet clients show after the subject) is a real, underused second
  hook. If your tool exposes it, make it continue the subject rather than leak "View in browser" or an
  unsubscribe stub. In a plain-text cold email it's simply your first line — so make the **first
  sentence** do double duty as subject-support, not throat-clearing.

> ⚠️ **Source-quality note:** the widely quoted "personalized subject lines get 46% opens vs 35%," "+29%
> opens," "+202% vs generic" numbers are pre-Apple-MPP, open-rate-based vendor stats — and **open rate
> is now unreliable** (§8). The *principle* (specific/personal subjects out-reply generic ones) holds;
> the *percentages* are measuring a metric that's been broken since 2021. Don't A/B-test subjects on
> open rate — test on reply rate.

---

## 3. First-touch anatomy

The cold first touch has one goal: earn a reply. Not book a meeting, not explain everything — earn one
reply. Structure that works, and that mirrors this repo's internal outreach prior art:

```
[SIGNAL]  Open on the real, specific thing they did/shipped/said. Not a compliment. (1 sentence)
[BRIDGE]  Why it matters to what they're building — framed as a gap in their system, hedged. (1–2 sentences)
[PROOF]   The closest evidence that you can close it — a peer/company-type outcome. (1 sentence)
[ASK]     One low-friction CTA. Interest, not a calendar slot. (1 sentence)
```

**Word count: short. Aim ~50–90 words; 3–5 sentences.** The datasets disagree on the exact optimum but
all point short: Lavender's analysis favors **25–50 words**; Gong's favors **under ~100 words / 3–4
sentences**; some put the sweet spot at **50–125 words** with reply rates 30–50% higher than emails
over 200 words
([Lavender, best length for cold email — lavender.ai](https://www.lavender.ai/blog/best-length-cold-email);
[Gong, cold email stats — gong.io](https://www.gong.io/blog/cold-email-stats)).
The through-line: if an exec has to scroll, you've lost. Cut every sentence that doesn't carry
structural weight.

**One CTA only.** A second ask measurably cuts replies — it adds decision friction. Pick the single
most valuable next step and ask only for that.

**Do NOT hard-ask for a meeting in touch 1.** This is the best-evidenced craft rule in cold email. In
Gong Labs' analysis of **304,174 cold emails**, emails that asked for time upfront had **~44% lower
reply rates** at the cold stage; interest-based CTAs ("is this even relevant to you?", "want me to send
the breakdown?") outperformed. Specific-time/calendar CTAs only win **later**, once the prospect is in
active evaluation
([Gong, cold email CTA data — gong.io](https://www.gong.io/blog/this-surprising-cold-email-cta-will-help-you-book-a-lot-more-meetings);
[Growleads summary of the 304K-email study, 2026 — growleads.io](https://growleads.io/blog/interest-based-ctas-vs-meeting-requests-study/)).
Put the CTA **at the end** (a question in the last line, not buried mid-body), and make it a question —
questions pull materially more replies than statements.

**Relevance/personalization is the whole game.** The signal in line one is what separates a cold email
from spam. If you can't name a real, specific reason you're writing to *this* person *now*, the message
isn't ready — a generic "we help companies like yours" opener is the fastest way to the archive. (See §6
for doing this at scale without faking it.)

---

## 4. Sequence architecture

One email is a coin flip you usually lose. A sequence is the actual unit of outbound.

**How many touches: 3–4 for cold B2B.** Enough to catch people who missed or deferred touch 1; not so
many you become the thing they complain about. More touches keep adding replies with diminishing
returns, and there's evidence the **4th+ follow-up starts to correlate with rising spam/unsubscribe
signals** — so land around 4 and stop, or move the prospect to a long re-engage window
([Snov.io cold email analysis, 2026 — snov.io](https://snov.io/blog/cold-email-statistics/) — *single-source; treat the exact spam/unsub figures as directional*).

**Cadence: start tight, then widen.** First follow-up ~2–3 days after touch 1, then stretch the gaps
(an increasing/Fibonacci-style spacing). A workable default:

| Touch | Timing | Thread | What it adds |
|---|---|---|---|
| **1** | Day 0 | New | Signal → bridge → proof → interest ask |
| **2** | +2–3 days | **Same thread** (reply) | Deliver/point to the asset; name one capability + one outcome |
| **3** | +5 days | **Same thread** | A *different angle* on the same signal — a peer example, a threat/edge case, a new observation |
| **4** | +7–10 days | **New thread, new subject** | Fresh angle, or switch persona (e.g. the engineer instead of the exec). Then park. |

*(This cadence — Touch 1 → +2–3d → +5d → +7–10d, with touches 2–3 same-thread and touch 4 a new
thread — is distilled from this repo's internal outreach prior art and is consistent with external
practitioner guidance below. It's a sensible default, not a law.)*

**Same-thread vs new-thread follow-ups.** Keep touches 2–3 in the **same thread** (reply to your own
sent message): it preserves context, keeps the ask lightweight, and — once they've replied even once —
whitelists you so later touches render. Switch to a **new thread with a new subject** for the last
touch, because a fresh subject gives a genuinely new angle a fresh chance at the open. Endless "bump"
replies on one thread train the reader to ignore the thread.

**Multichannel (email + LinkedIn + call).** Coordinated multichannel out-replies email-only outreach —
vendors cite large lifts (one claims up to **287% more replies** for LinkedIn+email vs email alone;
RAIN Group's classic finding is that breaking through takes an average of **~8 touchpoints**). Treat
the exact multiplier as marketing; the direction is well supported. Practical rules:
- **LinkedIn first, then email** — a connection/visit before the email reads as genuine; two cold
  emails *then* a LinkedIn request reads as pressure.
- **Don't stack two channels on the same day** — space them so each lands.
- **Use conditional logic, not a fixed drip** — if they reply, the sequence **stops**; if they accept
  the LinkedIn request, the next email becomes a LinkedIn message
  ([SalesTarget, multichannel LinkedIn+email playbook, 2025 — salestarget.ai](https://salestarget.ai/blogs/multichannel-outreach-linkedin-email-b2b-reply-playbook)).
- Add **phone** later in the sequence for engaged/high-value prospects, not as a cold opener.

**When to stop / re-engage.** Stop the active sequence at ~4 touches. Log the reason. Then:
- **"Not now"** → log it, set a **~60-day re-show window**, and refresh the signal before re-approaching
  (a new launch, funding, hire, regulation).
- **"We're building it ourselves" / evaluating** → acknowledge, offer one genuinely useful thing (a
  peer's lesson, a gotcha), one shot, then park.
- **Hard no** → mark dead; never re-approach on the same motion.
- **Enterprise vs SMB timing:** enterprise cycles justify a longer overall arc (weeks to a couple of
  months across channels); SMB burns out faster — keep it inside ~30 days.

---

## 5. Follow-up craft (the most under-used lever)

**The headline claim — verify before repeating it.** You will see "40–65% of replies come from
follow-ups" everywhere. The *direction* is real and well-documented; the *exact share is contested and
source-dependent*:

- **Verified (Woodpecker, from its own send data):** campaigns **with** a follow-up out-reply
  single-send campaigns — general users go from **~9% → ~13%** average reply rate by adding one
  follow-up (experienced users **~16% → ~27%**), i.e. one follow-up converts **~22% more prospects**.
  And **the first follow-up is the single most effective message — roughly 40% higher reply rate than
  the initial email** ([Woodpecker, follow-up statistics — woodpecker.co](https://woodpecker.co/blog/follow-up-statistics/)).
- **Contested:** other datasets frame it the *opposite* way — e.g. "~58% of replies come from the first
  email, ~42% from follow-ups" — which would make the majority come from touch 1, not follow-ups
  ([reply-share figures vary widely across vendor datasets](https://woodpecker.co/blog/cold-email-statistics/)).

**What's safe to state:** follow-ups meaningfully increase total replies, and the **first follow-up is
typically the best-performing single follow-up**. The precise "X% of replies come from follow-ups"
figure (40%, 55%, 65%…) is **not reliably established** — different senders, lists, and definitions
produce different splits. Don't cite a specific percentage as fact; cite the mechanism.

**Add value per follow-up — never "just bumping."** A follow-up that says "circling back" / "did you
see this?" wastes the touch and nudges the reader toward the spam button. Each follow-up should carry a
**new reason to reply**:
- A **new angle** on the same signal (a different implication, a second-order consequence).
- A **peer proof point** — how a similar company handled the same problem.
- A **specific artifact** — the one-pager, a short teardown, a threat-model note, an edge case they'll
  recognize.
- A **persona switch** — if the exec is silent, the reachable engineer/champion often replies, and the
  artifact is high-status currency with them.
- A **soft reframe** on the final touch — a clean, no-guilt "should I close the loop?" that makes
  *not* replying easy (which, paradoxically, pulls replies).

Keep every follow-up as short as touch 1, still one CTA, still no time-ask until they've engaged.

---

## 6. Personalization at scale

The tension: relevance drives replies, but 1:1 handcrafting doesn't scale. Resolve it with **tiering**,
not by faking depth.

- **Tier by value.** *1:1* deep personalization for Tier-A named accounts (research the person, open on
  a real signal, custom proof). *1:many* templated-but-relevant for the broader list (segment-level
  signal + merge variables). Don't spend 1:1 effort on a 1:many list, or send 1:many mail to a
  strategic account.
- **Merge/custom variables** — `{{first_name}}`, `{{company}}`, `{{role}}`, and ideally a **custom
  variable that carries a real fact** (a recent launch, a hire, a job-post detail). A merge field that's
  just their first name is table stakes; a merge field that proves you looked is the actual lift. Always
  set **fallbacks** — a broken `{{first_name}}` ("Hi ,") is worse than no name.
- **Spintax** — templating that swaps phrasings (`{Hi|Hey} {first_name}`) so no two sends are
  byte-identical. Its value is partly deliverability: identical body text across thousands of sends is a
  bulk tell, and spintax breaks that fingerprint. In 2025–26 this matters *more* because AI-drafted
  copy is homogeneous — many senders using the same models produce structurally similar emails that
  filters increasingly detect
  ([Instantly, spintax explained, 2025 — instantly.ai](https://instantly.ai/blog/spintax/)).
- **The AI-personalization trap.** Naive AI personalization fails two ways: (1) **generic sameness** —
  "I was impressed by your work at {{company}}" is instantly recognizable as machine-generated and
  screens as spam; (2) **confident wrongness** — an LLM hallucinating a detail about the prospect ("loved
  your recent Series C") when it's false is worse than no personalization; it destroys trust on contact.
  Use AI to *research and draft*, but keep a human (or a verification step) on any specific claim about
  the prospect, and vary structure — not just tokens. The emerging standard is **"relevant scale":**
  high volume is only safe when personalization depth *and* list hygiene are equally high.

---

## 7. Compliance (general information — not legal advice)

Cold **B2B** email is legal in most major markets *if* you meet the rules. The rules differ by
jurisdiction; below is the operator-level summary. **This is general information, not legal advice —
confirm your specifics with counsel.**

**United States — CAN-SPAM.** The most permissive: no prior consent required for B2B cold email, but
you must ([FTC, CAN-SPAM Compliance Guide — ftc.gov](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business)):
- Not use **deceptive** `From`, `Reply-To`, routing, or subject lines; identify who you are.
- Include a **clear, conspicuous opt-out**; **honor opt-outs within 10 business days**; keep the opt-out
  mechanism working for **at least 30 days** after sending; don't charge or demand extra info to opt out.
- Include a **valid physical postal address** (street address, USPS PO box, or registered private
  mailbox).
- Penalties are steep — **up to $53,088 per violating email** (per-email, as of 2025).

**Canada — CASL.** Consent-based and stricter: generally requires consent before the first send, but
includes a **B2B exemption** and **implied-consent** bases — you can typically email a Canadian business
contact if the message is relevant to their role and their address was **published/provided in a
business context without a "no unsolicited email" notice**. Honor unsubscribes within **10 business
days**. Penalties up to **CA$10M** per violation
([Cold-email compliance overviews, CASL summary, 2025–26 — outreachbloom.com](https://outreachbloom.com/cold-email-compliance)).

**EU — GDPR (+ ePrivacy).** No blanket ban on B2B cold email, but you need a **lawful basis** — usually
**legitimate interest**, which you must actually document. In practice: run a **Legitimate Interest
Assessment (LIA)**, email **business/role addresses** (not personal), keep it **relevant to the person's
job**, **disclose where you got their data**, and provide an **easy opt-out**. Fines reach **€20M or 4%
of global turnover**
([Instantly, GDPR/CAN-SPAM B2B compliance, 2025 — instantly.ai](https://instantly.ai/blog/b2b-email-list-compliance-gdpr-canspam/)).

**UK — UK GDPR + PECR.** UK GDPR governs the personal data; **PECR** governs electronic marketing.
PECR's soft-opt-in/consent rules are **most restrictive for individual subscribers** (consumers, sole
traders, some partnerships) and **more permissive for corporate-body recipients** — but UK GDPR's
lawful-basis requirement still applies to the personal data either way. **Practical stance: treat UK
B2B like EU — legitimate interest + easy opt-out.**

> ⚠️ **Source-quality note:** some vendor guides flatly claim "the UK makes no B2B/B2C distinction under
> PECR." That's an **over-simplification** — PECR *does* distinguish corporate-body from individual
> subscribers for the marketing rules, while UK GDPR applies to the personal data regardless. The safe
> operating posture (consent or documented legitimate interest + frictionless opt-out) is the same
> either way, which is why the simplification is *close enough to act on but not technically accurate*.
> Verify against the ICO and counsel for anything high-stakes.

**Singapore — Spam Control Act (SCA) + PDPA.** Two laws apply. The **PDPA** carries a
**business-contact-information exemption**: personal data used *solely* to contact someone in their
**business capacity about business matters** sits outside most PDPA consent obligations — so B2B cold
email to a **corporate/work address** for a work-relevant reason generally needs **no prior consent**
([PDPC — Personal Data Protection Act / advisory guidelines](https://www.pdpc.gov.sg/)). Caveats: the
exemption **does not cover personal addresses** (a Gmail, even if used for work), doesn't cover
messages unrelated to the person's role, and you must still obtain addresses lawfully; the **Do-Not-Call
registry is phone/SMS/fax, not email**. The **Spam Control Act** governs **bulk** commercial email —
defined as **> 100 messages of similar content in 24h** — and then requires an **unsubscribe facility
valid ≥ 30 days**, honored **within 10 business days** (treat ~**5 business days** as the safe bar),
at no more than normal cost; **truthful subject + header** info; the sender's **valid contact details**;
and the advertisement to be **labelled** — historically an **`<ADV>`** tag at the start of the subject
line ([IMDA — unsolicited communications / best practices](https://www.imda.gov.sg/infocomm-regulation-and-guides/unsolicited-communications/best-practices-for-organisations)).
**Practical stance:** for **personalised, low-volume** B2B outreach to **work addresses**, PDPA's
business-contact exemption is your basis and you stay under the SCA's bulk threshold — so no `<ADV>`
label, but **always** carry a working opt-out + real identity/contact. For **true bulk** (> 100
similar/24h), the SCA's unsubscribe + labelling + header rules apply.

**Universal compliance defaults (do these everywhere):** truthful identity and headers; a real,
working, one-step opt-out honored fast; a physical postal address on any bulk/marketing send; suppress
opt-outs permanently; never buy/scrape lists that carry spam traps; keep records of your lawful basis.

---

## 8. Measurement (stop trusting opens)

**True-north metrics, in order:** **positive reply rate → meetings booked → reply rate.** Everything
else is a proxy. Positive replies and booked meetings are what the pipeline is actually made of; total
reply rate is a useful health check; opens are noise.

**Why open rates are now unreliable — and why it distorts more than you think.** Apple's **Mail Privacy
Protection (MPP)**, on by default in Apple Mail since iOS 15 (2021), **pre-fetches message content —
including the tracking pixel — whether or not the human ever opens the email.** Apple Mail is the single
largest email client (**~49% of opens in Jan 2025**), so a large share of your recorded "opens" are
machines, not people. Reported opens are inflated **~15–35%** depending on how Apple-heavy your list is
([EmailToolTester, Apple MPP open rates, 2025 — emailtooltester.com](https://www.emailtooltester.com/en/blog/apple-mpp-open-rate/);
[Litmus/industry client-share data, 2025](https://www.emailtooltester.com/en/blog/apple-mpp-open-rate/)).

Second-order damage, which is easy to miss:
- **Open-based A/B tests are broken.** If you pick subject-line winners by open rate, you're partly
  measuring which variant hit more Apple inboxes. Test subjects on **reply rate** (or click rate)
  instead — you'll need bigger samples and more patience, but the conclusion will be real.
- **"Opened-but-didn't-reply" triggers are broken.** Sequences that branch on "opened" fire on MPP
  pre-fetches — you'll send "I saw you opened this…" follow-ups to people who never looked. **Trigger on
  clicks or replies, not opens.**
- **Open-rate deliverability inference is broken.** A high open rate no longer proves inbox placement.
  Use **reply rate as the placement proxy** (a reply proves a human saw it in the primary inbox), plus
  seed-list/inbox-placement tests.

**Realistic B2B cold-email benchmarks (ranges, not false precision).** Reply rates have declined over
the years and vary enormously by list quality, targeting, and vertical. Directional 2025–26 bands
([Belkins, B2B response rates, 2026 — belkins.io](https://belkins.io/blog/cold-email-response-rates);
[Apollo, good reply-rate benchmark, 2026 — apollo.io](https://www.apollo.io/insights/what-is-a-benchmark-for-reply-rates-in-cold-outreach)):

| Metric | Poor | Realistic | Good | Elite |
|---|---|---|---|---|
| **Reply rate** | <3% (targeting/deliverability broken) | 3–5% | 5–8% | 10%+ (tight ICP, hyper-personalized, small list) |
| **Positive reply rate** | <0.5% | 0.5–1% | 1–2% | 5%+ |
| **Meetings booked** | — | ~1–3% of sends | — | — |

Two robust patterns behind the ranges: **smaller, tighter lists reply better** (≤50 recipients can
average ~5–6%; 1,000+ drops toward ~2%), and **relevance beats volume** — which is the whole argument
for §6's "relevant scale." Treat all specific percentages as **directional vendor data**: methodologies
differ, "reply" isn't defined identically, and the numbers drift year to year. Your own baseline over a
few hundred sends is the only benchmark that truly matters.

---

## 9. Tooling landscape (brief, neutral)

Cold-email **sequencing platforms** — Saleshandy, Instantly, Apollo, Smartlead, Lemlist, GMass, and
peers — occupy one category: they automate multi-step sends across many mailboxes with merge
variables/spintax, inbox rotation, warmup, reply detection, and analytics. They differ mainly in
emphasis, not kind: some bundle a prospect database (Apollo), some optimize for agency-scale
multi-inbox rotation and deliverability (Smartlead, Instantly), some lead on multichannel LinkedIn+email
sequencing (Lemlist), some are lightweight/inexpensive for small teams (Saleshandy, GMass — the latter
running natively inside Gmail)
([EmailToolTester, best email outreach tools, 2026 — emailtooltester.com](https://www.emailtooltester.com/en/blog/best-email-outreach-tools/)).
No endorsement is implied — the tool is a force-multiplier on the fundamentals in this doc, not a
substitute. A great platform sending unauthenticated, un-warmed, generic mail still lands in spam; the
deliverability, message, and cadence discipline above is what determines results regardless of vendor.

---

## 10. Templates (copy, adapt, ship)

Keep every one of these plain-text, short, one CTA, no time-ask until they engage. Fill the profile's
voice over the scaffolding.

### A. First-touch skeleton (signal → bridge → proof → ask)

```
Subject: [3–5 words, lowercase, names their signal]

Hi [First name],

[SIGNAL — the specific real thing they did/shipped/said, in one sentence.]
[BRIDGE — why it opens a gap in what they're building; hedge it ("likely", "my hunch —
 tell me if you've got this covered"), don't assert facts about their environment.]
[PROOF — the closest peer outcome, by company type not name, that closes exactly that gap.]
[ASK — one interest CTA: "want the [one-pager / short breakdown]?" Never a calendar slot.]

[Signature — name, from the profile]
```

### B. Subject-line bank (lowercase, specific)

- `[their launch/thing]`
- `question on [their specific initiative]`
- `[problem] at [company]`
- `[their signal] → [the gap]`
- `re: [something genuinely relevant to them]`

Avoid: `Quick question`, `Touching base`, anything Title Case, anything with `!`, emoji, or a promise.

### C. Follow-up bank (each adds something new — never "just bumping")

**Touch 2 (+2–3d, same thread) — deliver the asset**
```
[One line tying back to the signal.] Here's the [one-pager / teardown] I mentioned — it walks
through [the one capability] and how [company-type] got [the one outcome].
[Same interest ask, or "worth a look?"]
```

**Touch 3 (+5d, same thread) — new angle / peer proof**
```
One more thing on [their signal]: [a second-order consequence or edge case they'll recognize].
[Peer example: how a similar company hit this and what they did.]
[Soft ask.]
```

**Touch 4 (+7–10d, new thread, new subject) — fresh angle or persona switch, then park**
```
Subject: [new, different angle]

[Reframe from a new door — different implication, or write to the engineer/champion instead
 of the exec.] If [problem] isn't a priority right now, no worries — should I close the loop?
```

### D. CTA bank (interest, not time)

- "want the one-pager?"
- "worth me sending the breakdown?"
- "is [problem] even on your radar this quarter?"
- "should I send the [teardown/example]?"
- (Only after they reply/engage:) "want to grab 20 min next week — Tue or Thu?"

### E. Opt-out line (plain-text, compliant)

```
If this isn't relevant, just reply "stop" and I won't follow up.
[Company legal name, physical postal address]
```

---

## 11. What holds for every profile (the non-negotiables)

- **Never** send cold from the primary domain; **never** skip SPF/DKIM/DMARC alignment.
- **Never** exceed ~0.1% complaints knowingly; suppress opt-outs immediately.
- **Never** ask for a meeting/time in the cold first touch; **never** stack more than one CTA.
- **Never** "just bump" — every follow-up earns its place with a new reason to reply.
- **Never** trust open rates for decisions or A/B tests — measure replies, positive replies, meetings.
- **Never** buy/scrape unverified lists, fake identity/headers, or omit the opt-out + postal address.

Everything tenant-specific — the voice, the ICP personas, the signal library, the proof/case-study
matrix, the signature — lives in the **active profile** (`knowledge/voice.md`, `icp-personas.md`,
`hook-matrix.md`, `case-studies.md`), not here.

---

## Sources & verification log

Legend: **Verified** = confirmed against the primary/authoritative source (provider docs, RFC, FTC).
**Single-source** = one vendor/dataset, directionally credible but not independently corroborated.
**Contested** = sources materially disagree; treat with caution.

### Deliverability (verified against primary sources)

| Claim | Source(s) | Confidence |
|---|---|---|
| Google two-tier rules: all senders need SPF **or** DKIM, PTR, TLS, spam <0.3%, RFC 5322; bulk (5,000+/day) need SPF **and** DKIM, DMARC `p=none`+ aligned, one-click unsub | [Gmail Email sender guidelines (support.google.com/a/answer/81126)](https://support.google.com/a/answer/81126) + [FAQ (14229414)](https://support.google.com/a/answer/14229414) | **Verified** (primary) |
| Spam rate: keep <0.1%, never reach 0.3%; unsub honored within 48h | [Gmail sender guidelines FAQ](https://support.google.com/a/answer/14229414) | **Verified** (primary) |
| Google/Yahoo aligned requirements effective 1 Feb 2024; Yahoo mirrors Google | [Yahoo Sender Hub](https://senders.yahooinc.com/best-practices/) | **Verified** (primary) |
| Postmaster Tools v2 (binary compliance) replaced legacy dashboard Oct 2025; Nov 2025 enforcement → 5xx SMTP rejections | [Security Boulevard (Nov 2025)](https://securityboulevard.com/2025/11/google-and-yahoo-updated-email-authentication-requirements-for-2025/); [Red Sift](https://redsift.com/resources/blog/gmails-enforcement-ramps-up-what-bulk-senders-need-to-know) | **Single-source** (secondary reporting of Google changes; directionally consistent across vendors) |
| Microsoft/Outlook: 5,000+/day to outlook/hotmail/live → SPF+DKIM+DMARC (`p=none`+, aligned); effective 5 May 2025; non-compliant → Junk then reject `550 5.7.515`; no numeric complaint threshold; no RFC 8058 mandate | [Microsoft Tech Community (Apr 2025)](https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730); [Mailgun](https://www.mailgun.com/blog/deliverability/microsoft-sender-requirements/) | **Verified** (primary blog + corroborating secondary; MS page is JS-rendered so quoted via Mailgun) |
| RFC 8058 mechanics: `List-Unsubscribe` HTTPS URI + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; DKIM must cover both headers; POST unsubscribes, GET must not | [RFC 8058 (IETF)](https://datatracker.ietf.org/doc/html/rfc8058) | **Verified** (primary/RFC) |
| BIMI requires DMARC `quarantine`/`reject` (`p=none` insufficient) + VMC (or CMC at Gmail) | [BIMI Group FAQ](https://bimigroup.org/faqs-for-senders-esps/) | **Verified** (primary) |

### Deliverability (practitioner heuristics — directional)

| Claim | Source(s) | Confidence |
|---|---|---|
| Warmup 2–4 weeks; ~5–10/day ramping to ~30–50/inbox/day; scale by adding inboxes | [Ozigi warmup guide (2026)](https://blog.ozigi.app/blog/how-to-warm-up-sending-domain-2026) | **Single-source / practitioner consensus** — no provider publishes numbers; direction agreed, figures not authoritative |
| Dedicated domain not primary; subdomain vs separate-domain risk isolation; ~500/day subdomain rule of thumb | [Growleads (2026)](https://growleads.io/blog/subdomain-for-cold-email-protect-main-domain/) | **Single-source / practitioner** |
| Custom tracking domain isolates reputation vs shared `track.provider.com` | [Instantly (2025)](https://instantly.ai/blog/email-tracking-and-deliverability-why-tracking-pixels-can-hurt-your-inbox-placement/) | **Single-source** — mechanism is sound |
| Plain text out-delivers HTML for cold; "15–25% more replies," "652% higher HTML bounce" | [Hunter.io (2025)](https://hunter.io/blog/is-html-harming-your-cold-email-deliverability/) | **Direction: corroborated. Exact multiples: single-source vendor stats — flagged, do not quote as fact** |

### Message craft & sequencing

| Claim | Source(s) | Confidence |
|---|---|---|
| Subject lines: short (2–5 words / <~40 char), lowercase, specific; avoid spam words/CAPS/emoji | [Belkins subject-line study (2025)](https://belkins.io/blog/b2b-cold-email-subject-line-statistics) | **Single-source (large-N vendor)** — direction well supported |
| Subject-line "46% vs 35% open," "+29%," "+202%" personalization lifts | vendor blogs (multiple) | **Contested / stale** — open-rate-based, undermined by Apple MPP (§8); principle holds, numbers unreliable |
| Ideal length short: Lavender 25–50 words; Gong <100 words/3–4 sentences; some 50–125 | [Lavender](https://www.lavender.ai/blog/best-length-cold-email); [Gong](https://www.gong.io/blog/cold-email-stats) | **Multi-source but disagree on exact optimum** — direction (short) robust; single number not established |
| Time-ask in cold email cuts replies ~44%; interest CTAs win cold, time CTAs win at deal stage; 304,174-email study | [Gong CTA study](https://www.gong.io/blog/this-surprising-cold-email-cta-will-help-you-book-a-lot-more-meetings); [Growleads summary (2026)](https://growleads.io/blog/interest-based-ctas-vs-meeting-requests-study/) | **Single-source (Gong Labs, large-N)** — best-evidenced craft rule here; still one vendor |
| One CTA only; CTA at end (~22% more replies); questions > statements | [Gong cold email stats](https://www.gong.io/blog/cold-email-stats) | **Single-source** — "22%" is one dataset; principle widely echoed |
| **"40–65% of replies come from follow-ups"** | multiple vendors, no agreement | **CONTESTED — flagged in-text.** Some sources say the *opposite* (majority from touch 1) |
| Follow-ups lift reply rate (~9%→13% general, ~16%→27% experienced; +22% prospects); first follow-up ~40% higher than initial | [Woodpecker follow-up statistics](https://woodpecker.co/blog/follow-up-statistics/) | **Verified against Woodpecker's own data** (single vendor, but the primary of this claim; sample size not disclosed) |
| Cadence: first follow-up +2–3 days, widening; 3–4 touches; enterprise longer | [Woodpecker](https://woodpecker.co/blog/follow-up-statistics/); practitioner guides | **Single-source / consensus** |
| 4th+ follow-up correlates with rising spam (~1.6%)/unsub (~2%) | [Snov.io (2026)](https://snov.io/blog/cold-email-statistics/) | **Single-source — flagged; treat figures as directional** |
| Multichannel LinkedIn-first; "up to 287% more replies"; RAIN ~8 touchpoints; conditional logic | [SalesTarget (2025)](https://salestarget.ai/blogs/multichannel-outreach-linkedin-email-b2b-reply-playbook) | **Direction supported; "287%" single-source vendor stat — flagged** |
| Spintax breaks body fingerprint / AI-copy homogeneity is filter-detectable | [Instantly spintax (2025)](https://instantly.ai/blog/spintax/) | **Single-source** — plausible, not independently verified |

### Compliance (verified against primary law/regulator where possible)

| Claim | Source(s) | Confidence |
|---|---|---|
| CAN-SPAM: no consent needed; honor opt-out within 10 business days; opt-out functional ≥30 days; valid physical address; no deceptive headers; up to $53,088/email (2025) | [FTC CAN-SPAM Compliance Guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) | **Verified** (primary/FTC); penalty figure per 2025 inflation adjustment |
| CASL: consent-based, B2B exemption + implied consent; 10 business days; up to CA$10M | [Cold-email compliance overview (2025–26)](https://outreachbloom.com/cold-email-compliance) | **Single-source secondary** — CASL text not read directly; confirm with counsel |
| GDPR: legitimate-interest basis for B2B, LIA, business address, relevance, disclosure, opt-out; €20M/4% | [Instantly GDPR/CAN-SPAM (2025)](https://instantly.ai/blog/b2b-email-list-compliance-gdpr-canspam/) | **Single-source secondary** — direction standard; confirm with counsel |
| UK PECR distinguishes corporate vs individual subscribers (vendor claim of "no B2B/B2C distinction" is over-simplified); UK GDPR applies regardless | in-text reasoning + vendor guides | **Contested — flagged in-text.** Verify against ICO guidance |

### Measurement

| Claim | Source(s) | Confidence |
|---|---|---|
| Apple MPP pre-fetches pixels regardless of opens; Apple Mail ~49% of opens (Jan 2025); opens inflated ~15–35%; break A/B tests and open-triggers | [EmailToolTester (2025)](https://www.emailtooltester.com/en/blog/apple-mpp-open-rate/) | **Verified mechanism** (MPP behavior is Apple-documented); client-share % single-source but widely reported |
| Benchmarks: reply 3–5% realistic / 5–8% good / 10%+ elite; positive reply 0.5–2%; smaller lists reply better | [Belkins (2026)](https://belkins.io/blog/cold-email-response-rates); [Apollo (2026)](https://www.apollo.io/insights/what-is-a-benchmark-for-reply-rates-in-cold-outreach) | **Direction robust; exact numbers vary by methodology — presented as ranges, flagged** |

### Tooling

| Claim | Source(s) | Confidence |
|---|---|---|
| Sequencing-tool category framing (Saleshandy/Instantly/Apollo/Smartlead/Lemlist/GMass differ by emphasis) | [EmailToolTester (2026)](https://www.emailtooltester.com/en/blog/best-email-outreach-tools/) | **Single-source, neutral/category-level** — no endorsement; per-tool performance stats deliberately omitted as unverifiable |

### Internal prior art

- The **first-touch structure** (signal → bridge → proof → interest-ask), the **plain-text/≤1-link/no-time-ask** first-touch rules, the **one-CTA** rule, and the **cadence** (Touch 1 → +2–3d → +5d same-thread → +7–10d new thread, ~4 touches, each adding a new angle) are **distilled from this repo's internal outreach voice guide** (`profiles/<active>/knowledge/voice.md` — "First-touch email rules," "Calibration examples," "the gift ladder"). They are generic, reusable principles here; company-specific voice, banned words, and proof matrix stay in the profile. This internal guidance is **independently consistent** with the external sources above (notably Gong on time-asks and Woodpecker on follow-ups), which is why it's presented as durable rather than tenant-specific.

### Explicitly unverified / could not confirm

- **The specific "X% of replies come from follow-ups" figure** (40%, 55%, 65%) — could **not** be
  verified; sources contradict each other on the split. Only the *direction* (follow-ups add replies;
  first follow-up is strongest) is safe.
- **Exact warmup ramps, per-inbox caps, subdomain volume thresholds** — no mailbox provider publishes
  these; they are practitioner heuristics.
- **All plain-text-vs-HTML multiples, the 287% multichannel lift, the 22% end-CTA lift, and per-tool
  deliverability/reply percentages** — single-vendor marketing data, not independently reproduced.
- **CASL and GDPR/PECR specifics** were read via secondary compliance guides, not the primary statutes;
  the compliance section is general information and flags the need for counsel.
