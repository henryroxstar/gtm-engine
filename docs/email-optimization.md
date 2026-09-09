# Cold Email & Sequence Optimization Playbook

**For: GTM**
**Scope: how to write cold/outbound email and multi-step sequences that get replies — message craft, cadence, personalization, and measurement. Sending infrastructure ([`email-deliverability.md`](email-deliverability.md)) and compliance ([`email-compliance.md`](email-compliance.md)) are separate documents. Company-agnostic; tune voice, ICP, and signature to the active profile (`PROFILE.md`, `knowledge/voice.md`, `icp-personas.md`).**

> **Voice & targeting live in the profile, not here.** This doc is the *method* — how to land in the
> inbox and earn a reply. *Who* you write to, *what* signal you open on, and the *register* you write
> in come from the active profile's `voice.md` + `icp-personas.md`. Every principle below is written to
> layer under whatever voice the profile specifies.

This is a prescriptive playbook, not a survey. Performance numbers (reply rates, "best length,"
follow-up shares) come from vendor datasets that disagree with each other; where the data is soft or
contested, this doc says so and marks it. Templates are in the back half.

> ### This document is the writing. The other two are the machinery.
>
> Cold email has three separable problems, and only one of them is a writing problem. Know which one
> you are solving before you go looking for the answer here.
>
> | Problem | Owned by | Document |
> |---|---|---|
> | **Does the mail arrive?** authentication, domains, warmup, complaint rate | your **sequencer** and whoever runs your DNS — set up once per domain, then left alone | [`email-deliverability.md`](email-deliverability.md) |
> | **Does anyone reply?** subject, first touch, cadence, follow-ups, personalisation, measurement | **you**, every single send — the part that cannot be bought or configured | **this file** |
> | **May you email them at all?** postal address, opt-out, markets, suppression | **you and your counsel**, settled before a word is written | [`email-compliance.md`](email-compliance.md) |
>
> If you are here to write an email, you are in the right place and you can ignore the other two
> today. Someone set them up already, or the send would not go out.
>
> **Where the evidence lives.** This file is the prescriptive floor — the rules. The external
> research those rules rest on (graded by source quality), plus the US vs. Singapore CxO register
> analysis that no other doc owns, is in
> [`cold-email-craft-evidence.md`](cold-email-craft-evidence.md).

---

## 0. The 10-second version (do these and you've captured most of the upside)

0. **Before any of this: check who is on the list.** Targeting sits upstream of every lever below,
   and no amount of copy quality survives a wrong seat. [§12](#12-list-quality--the-lever-upstream-of-every-other-lever)
   owns it, it is gated by `gtm_core.list_fit`, and it runs *before* research is commissioned.
1. **Get the machinery right once, then stop thinking about it.** Separate sending domain (never your
   primary), SPF + DKIM + DMARC aligned, 2–4 weeks of warmup, per-inbox volume capped, complaints
   under 0.1%. All of it is one-time setup and all of it lives in
   [`email-deliverability.md`](email-deliverability.md). Nothing below matters if this is wrong —
   and nothing below is affected by it once it's right.
2. **Plain text. One link at most. No images, no tracking pixel if you can avoid it.** Cold mail should
   read like a person typed it, not a newsletter. Design-heavy HTML reads as bulk and filters harder.
3. **Win the subject line in ~3–5 words, lowercase, specific to them.** No spammy words, no ALL-CAPS,
   no `!!!`. It should look like an internal note, not a campaign.
4. **First touch: short (aim ~50–90 words), one idea, one CTA — and do NOT ask for time.** A hard
   meeting ask in the cold email depresses replies; ask for *interest*, not a calendar slot.
5. **Follow up. 3–4 touches, spaced out.** A large share of replies come from follow-ups, and the
   first follow-up is usually the best-performing single message. Add a new angle each time — never
   "just bumping this."
6. **Stop measuring opens — measure replies, positive replies, and meetings.** Apple Mail Privacy
   Protection made open rates noise; roughly half of "opens" are machine pre-fetches. Reply is the
   first trustworthy signal.
7. **Comply by default.** Real sender identity, a working opt-out, a physical postal address,
   opt-outs suppressed the same day. Most of that is sequencer configuration, and none of it is
   settled here — [`email-compliance.md`](email-compliance.md) owns it. Read it before you send.

---

## 1. Writing for the filter (as well as the reader)

Deliverability is upstream of every tactic in this doc — the best-written sequence in the world
scores zero replies from the spam folder. But almost all of it is **infrastructure**, and it lives in
its own document now: authentication, provider requirements, domains, warmup and complaint rate are
in [`email-deliverability.md`](email-deliverability.md). Set that up once per sending domain and
forget it.

What stays here is the part you decide **while writing** — the formatting and link choices that make
a well-authenticated email land in spam anyway.

### 1.1 Plain text vs HTML, and link/image/attachment discipline

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

### 1.2 Common spam-filter triggers (avoid)

- **Spammy words/phrases:** "free," "guarantee," "act now," "limited time," "risk-free," "make
  $$$," "100% free." Write the way you'd message a colleague.
- **Formatting tells:** ALL-CAPS, `!!!`, `$$$`, lots of bold/colored text, giant fonts.
- **Link/image tells:** multiple links, shortened links, image-only emails, mismatched display-vs-actual
  URLs.
- **List tells:** high bounce rate (verify every address before sending — dead addresses spike your
  bounce rate and torch reputation), spam-trap hits from scraped/purchased lists.
- **Guessed addresses.** Never send to an address you inferred from a pattern
  (`first.last@company.com`) instead of resolving. It bounces, bouncing burns the domain you just
  spent a month warming, and mass-generated addresses are exactly what spam traps are built to
  catch. This pipeline blocks pattern-guessed rows outright — see
  [`email-compliance.md`](email-compliance.md).
- **Volume tells:** sudden volume spikes, identical body text across thousands of sends (see spintax,
  §6), sending outside business hours in a machine-gun pattern.

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

## 7. Compliance — not this document

Compliance has one home, and it is not here: **[`email-compliance.md`](email-compliance.md)**. Read
it before your first send. It covers what every commercial send has to carry, the automated
pre-load check, the per-domain test send, how markets are gated, opt-out handling, and the
operator's pre-activation checklist — plus links to the regulators themselves.

Two reasons it lives there and not in this playbook. First, most of it is not writing: the postal
address and the opt-out are switches inside your **sequencer**, set once, invisible in any draft you
proofread. Second, whether you may email a given person at all is a question for you and your
counsel, answered before a word is written — not a craft decision you make at the keyboard.

What that leaves for this document: don't fake identity or headers, don't disguise an ad as personal
mail, keep the opt-out line in the copy (§10 template E), and never send to a guessed address
(§1.2). The rest is not a writing problem.

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

### E. Opt-out line (plain text — carry this on every send)

```
If this isn't relevant, just reply "stop" and I won't follow up.
[Company legal name, physical postal address]
```

---

## 11. What holds for every profile (the non-negotiables)

- **Never** ask for a meeting/time in the cold first touch; **never** stack more than one CTA.
- **Never** "just bump" — every follow-up earns its place with a new reason to reply.
- **Never** trust open rates for decisions or A/B tests — measure replies, positive replies, meetings.
- **Never** buy/scrape unverified lists, fake identity/headers, or omit the opt-out + postal address.

The infrastructure non-negotiables — dedicated sending domain, SPF/DKIM/DMARC alignment, warmup,
complaint rate under 0.1% — are in [`email-deliverability.md`](email-deliverability.md); they hold
just as hard, they just are not decided at the keyboard.

Everything tenant-specific — the voice, the ICP personas, the signal library, the proof/case-study
matrix, the signature — lives in the **active profile** (`knowledge/voice.md`, `icp-personas.md`,
`hook-matrix.md`, `case-studies.md`), not here.

---

## 12. List quality — the lever upstream of every other lever

Everything above assumes the mail is going to someone who could act on it. That assumption failed
silently on a live 286-row list on 2026-08-11, which is why this section exists and why it is
**gated in code** rather than left as advice: `python -m gtm_core.list_fit --csv <list>`.

**Ordering, from the 2026 benchmark data:** signal-triggered cold email replies at roughly **5–18%**
where generic role-and-company-size targeting gets **1–3%**, and the gap between average and elite
senders is an infrastructure-and-targeting problem before it is a copy problem. Teams that invest in
verified data and enrichment outperform those that don't by 2–3×. So run these three checks *before*
commissioning research — research is the most expensive input in the pipeline.

### 12.1 Role fit — can this seat act at all?

Classify every title as **in / unclear / out** against who could own, block or champion the
purchase. On the failed list, **53%** were plausible seats; the rest were HR directors, a pharmacy
director, a patient liaison, a director of hospitality operations. Two rules:

- **Unclear is not a pass.** It means nobody has read it yet.
- **Flag wrong seats; never auto-delete them.** A regex must not unilaterally disqualify a real
  person. Write them to a review file and let a human confirm.

### 12.2 Verify what your tier column actually encodes

A/B/C tiers are usually assigned on seniority and firmographics, which is *not* the same as role
relevance — and nothing warns you when they diverge. On the failed list they ran **backwards**:

| Tier | in-ICP |
|---|---|
| A | 26% |
| B | 35% |
| C | 54% |
| *untiered* | **100%** |

Had "full dossiers for Tier A" been executed, the most expensive research would have gone to the
least qualified people. **Never let a tier column allocate research spend until you have checked
that it predicts fit.**

### 12.3 Attribute hit rate to the sourcing run

Group rows by the run that produced them and compare in-ICP rates. On the failed list one bulk
vendor pull contributed **202 of 286 rows at a 35% hit rate**, while a targeted enrichment run
contributed 69 at **100%**. That single ratio decides where the next enrichment budget goes, and it
costs nothing — no sends required.

### 12.4 Grade the signal: structural beats dated

A "why now" is not one thing. Grade it (`gtm_core.list_fit.signal_grade`):

| Grade | Usable as an opener? | Note |
|---|---|---|
| **fresh** | yes | dated event inside the freshness window |
| **structural** | yes | durable fact about what their systems do — **does not decay** |
| **stale** | **no** | dated event past the window |
| **intent-only** | **no** | a Bombora topic + score |
| **absent** | no | nothing usable |

Three things this catches that §6 does not:

- **A stale signal is worse than no signal.** "Saw the news out of X" about a 13-month-old funding
  round tells the reader you are working from an old list. On the failed list, of 78 rows carrying
  research, only **2** referenced the current year.
- **An intent score is not a fact.** `"machine learning & artificial intelligence (intent score 93)"`
  ranks who to call first. It cannot be merged into a sentence. 18 rows carried nothing else.
- **Prefer structural hooks for anything that takes weeks to send.** A list that drains slowly will
  outlive its news. A structural claim is immune, so it should be the default rather than the
  fallback. Critically: **do not let a stale signal silently demote a row to a generic template** —
  that is how a personalised campaign quietly becomes an untargeted one, with no error raised.

### 12.5 Exclusions must outlive a rebuild

Pool CSVs are build outputs. A `suppression` column written onto one is a cache that the next
consolidate discards — on 2026-08-11 that returned 55 excluded people to the sendable pool within
the hour. Keep exclusions in a ledger (`gtm_core.suppression`) and `verify` after every rebuild.
Reserve the provider-side DNC list for genuine opt-outs: it is global, permanent, and typically has
no removal API, so using it for "already contacted" forfeits that person forever.

### 12.6 Replies asking to opt out are not auto-suppressed

Sequencers suppress on the unsubscribe *link*. A prospect who **replies** "unsubscribe" is usually
logged with negative sentiment and left un-suppressed. This one is a legal obligation
([`email-compliance.md`](email-compliance.md) §5), not hygiene — verified live in this account
2026-08-11 (`jordan.avery@brackenhealth.example` replied "Unsubscribe"; Saleshandy tagged it
Negative sentiment, `Unsubscribed` stayed `No`) and again 2026-08-17: he sat un-suppressed for six
days because nothing was watching, not because the rubric was wrong.

**Detection is automated on a timer** (`gtm_core.optout_watch` + `agent/optout_sweep.py`,
`systemd/gtm-optout-watch.timer` — every 4h) — it does **not** auto-write the provider's Do Not
Contact list itself. DNC has no removal API, so a false-positive auto-add would be permanent and
unrecoverable; the sweep escalates to the operator's Telegram (`agent.gate_notify.push_optout_alert`)
with the quoted reply, and a human taps add-to-DNC. This mirrors every other irreversible action in
this repo (publish, schedule) — detection speed was the actual gap, not the confirm step. For a
profile without the timer wired (or before its `agent/mcp/saleshandy` inbox endpoints are confirmed
live — they carry a `# VERIFY:` marker), fall back to sweeping replies by hand every send window.

---

## Sources & verification log

Legend: **Verified** = confirmed against the primary/authoritative source (provider docs, RFC).
**Single-source** = one vendor/dataset, directionally credible but not independently corroborated.
**Contested** = sources materially disagree; treat with caution.

### Deliverability claims that are writing decisions

| Claim | Source(s) | Confidence |
|---|---|---|
| Plain text out-delivers HTML for cold; "15–25% more replies," "652% higher HTML bounce" | [Hunter.io (2025)](https://hunter.io/blog/is-html-harming-your-cold-email-deliverability/) | **Direction: corroborated. Exact multiples: single-source vendor stats — flagged, do not quote as fact** |

Provider requirements, authentication, warmup and reputation claims moved with their section to
[`email-deliverability.md`](email-deliverability.md), which carries its own source log.

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

### Compliance

No claims to log. This playbook no longer covers compliance at all — it moved to
[`email-compliance.md`](email-compliance.md), which links the regulators directly rather than
summarising them. The market-by-market summary that used to sit here rested partly on secondary
vendor guides rather than the statutes, so it was deleted rather than re-sourced.

### Measurement

| Claim | Source(s) | Confidence |
|---|---|---|
| Apple MPP pre-fetches pixels regardless of opens; Apple Mail ~49% of opens (Jan 2025); opens inflated ~15–35%; break A/B tests and open-triggers | [EmailToolTester (2025)](https://www.emailtooltester.com/en/blog/apple-mpp-open-rate/) | **Verified mechanism** (MPP behavior is Apple-documented); client-share % single-source but widely reported |
| Benchmarks: reply 3–5% realistic / 5–8% good / 10%+ elite; positive reply 0.5–2%; smaller lists reply better | [Belkins (2026)](https://belkins.io/blog/cold-email-response-rates); [Apollo (2026)](https://www.apollo.io/insights/what-is-a-benchmark-for-reply-rates-in-cold-outreach) | **Direction robust; exact numbers vary by methodology — presented as ranges, flagged** |

### Tooling

| Claim | Source(s) | Confidence |
|---|---|---|
| Sequencing-tool category framing (Saleshandy/Instantly/Apollo/Smartlead/Lemlist/GMass differ by emphasis) | [EmailToolTester (2026)](https://www.emailtooltester.com/en/blog/best-email-outreach-tools/) | **Single-source, neutral/category-level** — no endorsement; per-tool performance stats deliberately omitted as unverifiable |

### List quality (§12) — targeting before copy

- Reply-rate benchmarks by targeting method, and the "infrastructure and targeting before copy"
  ordering: [B2B Cold Email Statistics 2026 — martal.ca](https://martal.ca/b2b-cold-email-statistics-lb/);
  [What's a Good Cold Email Reply Rate in 2026 — apollo.io](https://www.apollo.io/insights/whats-the-expected-reply-rate-for-a-well-run-outbound-cold-email-campaign);
  [Cold Email in 2026: Domains, Deliverability, Replies — unifygtm.com](https://www.unifygtm.com/explore/cold-email-2026-domain-setup-deliverability-sequences).
  The 5–18% vs 1–3% split and the 2–3× verified-data effect are practitioner benchmarks, directional
  rather than provider-published — treat the *ordering* as the durable claim, not the exact figures.
- Sequencer-side practice (sender rotation, per-account 30–50/day, pre-launch setup score, multiple
  step variants so identical bodies don't ship at volume):
  [Best Practices to Improve Your Email Deliverability — docs.saleshandy.com](https://docs.saleshandy.com/en/articles/7220812-best-practices-to-improve-your-email-deliverability).
  Note our own mailboxes run at 10/day, deliberately below the vendor's 30–50 guidance, because the
  domains are new — see [`email-deliverability.md`](email-deliverability.md) §8.
- §12.1–12.6 defect evidence is first-party: the 2026-08-11 list audit, recorded as
  `list_quality_audit_and_wave1_defined` in `content/<active>/history.jsonl` and gated by
  `gtm_core/list_fit.py` + `gtm_core/suppression.py` (tests: `tests/test_list_fit.py`,
  `tests/test_suppression.py`).

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
- **All per-market legal specifics** — these were previously summarised from secondary compliance
  guides rather than the primary statutes, and have been removed rather than restated. §7 now
  describes only how this pipeline is configured and links the regulators directly.
