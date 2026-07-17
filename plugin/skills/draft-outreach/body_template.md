
# Draft Outreach

Write first-touch and follow-up messages that sound like the colleague wrote them — signal-first, one capability, one ask — grounded in a specific "why now" signal and a matched case study from the active company's proof library. **Drafts only; the colleague reviews and sends manually.**

> Resolve the **active profile** (the agent provides it; everything loads from `profiles/<active>/`, never `plugin/`).
> Read the company brand from `PROFILE.md` (`brand_name`) and lead with the company's `default_product`
> (`PROFILE.md` → `products[]`). Use the real brand and product names throughout — never hardcode them.

## Load context first (in this order)

1. **PROFILE** — `profiles/<active>/PROFILE.md`. **Read first.** Pull `name`, `title`, `email_signature`, `brand_name`, `default_product`, `language`. Sign the colleague's name from `email_signature`. If language ≠ English, write in it. **If `voice_style` is set in the PROFILE, that is the primary voice spec for this colleague** — it overrides the calibration examples in `voice.md`.
2. **`voice.md`** — `profiles/<active>/knowledge/voice.md`. **Always read.** Provides: message structure (DM/email/follow-up), banned fluff words, persona adjustments, self-check checklist, and calibration examples. If PROFILE has no `voice_style`, `voice.md` is the full voice spec.
3. **`hook-matrix.md`** — `profiles/<active>/knowledge/hook-matrix.md`. The persona × signal opening ideas.
4. **`profiles/<active>/knowledge/case-studies.md`** — the case-study selection map (shape → proof) and reusable hooks. For product facts, `profiles/<active>/knowledge/product.md` and `profiles/<active>/knowledge/icp-personas.md`.

> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

## Inputs to gather

The message needs four things — take them from a prospect run's Tier-A pack if available, otherwise ask briefly:
- **Company** + segment (Enterprise/Startup) and market.
- **Persona** being written to (Head of AI Platform, CISO, CEO/Founder, CPO, CTO, …).
- **The 🔥 "why now" signal** — the specific, dated, real thing (launch, job post, filing, incident) + source if known. If none is supplied, run the 6-source web sweep (see the prospect skill's `discovery-and-budget.md`) to find one — do **not** invent a signal.
- **Channel(s)** — LinkedIn DM, email, or both (default both + a follow-up).
- **The gift bundle** — the give-first asset the first touch offers and touch 2 delivers. Default: a **teaser one-pager + a short recorded demo**, both aligned to the exact gap the email names (alternatives: demo mockup screenshot, custom audit, demo-page link). If none exists yet, define it in one line — the first-touch CTA gates it, so it must be nameable and quick to produce.

## Compose

1. **Pick the hook** from the matrix at the persona × signal intersection. Rewrite it to name the *specific* signal (company, date, the actual event) — never paste the cell verbatim. One capability, one outcome.
2. **Map the case study** by shape first, then industry (`case-studies.md`). Use it as the one proof sentence — name the **company type + the specific outcome** ("a regulated-FI compliance team cut examiner verification 90 percent"), never the case-study company name (an unknown logo confuses more than it credits, and invites verification an Early-Access deployment may not survive; linter rule `named-case-study`). Exception: voice.md's **formal register** (SG regulated seats) may name the logo when permission exists. The persona docs flag the case proofs as *thin* — rotate the proof slot across four types per buyer: (a) regulatory why-now, (b) build-vs-buy math, (c) anonymized case outcome, (d) a concrete verified capability.
3. **Ground every mechanism claim in the product layer** (`profiles/<active>/products/<flagship>/`). Route the angle per seat using `knowledge/product.md`'s pain→persona table and five solution themes — never fire one theme (audit evidence) at every seat. Hard capability rules: claims stay inside the VERIFIED layer (`references/reference-architectures.md` **signing-scope note**: a standard single-gateway surface does NOT sign every request — per-action signed proof only for cross-org/VP-enabled shapes; Design-target features are never implied as live; audit immutability is an open item — say "independently verifiable evidence", not "tamper-proof"). No economic consequence ("costs what one did", "makes the review a formality") unless the account dossier — not our own teaser — evidences it; urgency comes from an account-tailored **discovery question** (`product.md` §Discovery), per the crosswalk honesty rule: the product produces *evidence that supports the customer's posture*, it does not discharge their obligations.
4. **Write each channel** to the voice.md structure:
   - **LinkedIn DM** ≤ 280 characters: signal hook → one bridge → one ask.
   - **Email** ≤ 100 words (aim 50–75): subject 1–4 words, lowercase, naming the signal (reads like an internal note) → greeting (`Hi <first name>,`) → opening (name the signal) → credit (one clause: their move is right) → gap (the ***likely*** gap in their vocabulary — hedged ("likely", "my hunch — tell me if you've got this covered"), structural (a property of how agents work, not a process miss), and naming why their current stack can't close it — **no product pitch, no feature list**; the bundle carries the product after they reply) → proof (the case study, one sentence — first cut if over length) → **one offer-CTA gating the gift bundle** ("want the one-pager + a short demo recording?") → signature from PROFILE. First touch is plain text: no images, screenshots, attachments, or calendar links; ≤1 untracked link; never a time-ask.
   - **Follow-ups (the gift ladder, up to 4 touches):** touch 2 (+2–3 days, same thread — or immediately on reply) delivers the bundle (teaser + demo-recording link) and names the product, one capability, one outcome; touch 3 (+5 days, same thread) adds a different-angle insight on the same signal; touch 4 (+7–10 days, **new thread** + new subject) tries a different angle or persona, then park. Every touch adds something new — never "just checking in", never repeat the original. Once they reply, flip the CTA to a **specific** time proposal.
5. **Apply the persona adjustment** (voice.md): CEO → deal risk; CTO → build-vs-buy; CISO → audit/regulatory exposure (name the regulator; time to a trigger — they respond to insight, not demos); Head of AI Platform → architecture fit; platform/security engineer → lead with the technical gap and give the artifact early (the reachable buyer when execs go quiet).

## Hard gates for 1:1 packs (rules_version 2026-07-16)

When drafting **per-person 1:1 emails** (Tier-A manual packs, sequencer step-1 bodies), the
self-check below is necessary but not sufficient — the 2026-07-16 Tier-A pack passed it while
being 48% shared boilerplate with unhedged claims dossiers contradicted. These gates are
**mandatory and mechanical**:

1. **Dossier depth, not CSV depth.** Source each email from the account folder
   (`content/<active>/accounts/<slug>/`) — never from a one-line `why_now` alone. Every email
   embeds **≥2 specific dossier facts** (dated events, named products/stack, the buyer's own
   words, customer names), at least one *after* the opener.
2. **Credibility diff before claiming a gap.** List what the dossier says they **already built**
   (auth, audit, governance, registration). The gap must *credit the named control first*, then
   name what is structurally missing — an unhedged claim their stack contradicts is
   worse than not sending. Hedge is mandatory ("my hunch / my read, tell me if you've got this
   covered"). Match the mechanism to their **real topology** (delegation chains vs parallel
   fleet vs agent-to-MCP) — claim delegation only where the dossier shows it.
3. **Per-seat lead pain.** CEO/founder → the stalled enterprise deal; CTO/platform → build-vs-own
   cost + no-rewrite drop-in; CISO/risk → attribution + the **named regulator/exam** (NCUA/FFIEC,
   EU AI Act Art. 12, HIPAA — only when the dossier or spec cites it); CFO → review-cost-of-growth;
   GC/compliance → examiner-provable trail; CPO → roadmap/feature trust; CoS → route-to-exec
   framing; CRO → deal-velocity. Raw IAM plumbing is a CISO/CTO register — never lead a CFO/CMO/GC
   with it.
4. **Proof matched by shape then vertical** (`case-studies.md` selection map) — healthcare gets the
   healthcare proof, payments gets payments; phrase it as the seat's gain.
5. **Gift CTA names the artifact** staged for that account (teaser one-pager, recorded demo,
   caller-context one-pager). Never "the teardown"; never a time-ask.
6. **Same-company divergence.** Two recipients at one account get different signals, angles, and
   subjects, sent 1–2 days apart — colleagues forward emails to each other.
7. **Prior-touch check.** Grep the outreach log + account folder for prior contact; a previously
   touched account gets the recorded follow-up angle, not a duplicate first touch.
8. **Stamp and lint.** The pack header carries `Rules-Version: <current>` and must pass the
   deterministic linter with **zero errors** before it is presented as sendable:

   ```bash
   uv run python3 tests/linter/outreach_pack_linter.py <pack.md> \
     --ban-file profiles/<active>/knowledge/voice-bans.txt \
     --case-study-file profiles/<active>/knowledge/outreach-case-studies.txt \
     --stem-file profiles/<active>/knowledge/outreach-banned-stems.txt
   ```

   The banned-stem and case-study lists are profile-supplied (each file is one entry per
   line; a missing file just disables that check) — the linter ships with neither baked in.

   The linter's `RULES_VERSION` is the source of truth; a pack stamped with an older version
   fails closed (stale → regenerate, never send). Its banned-stem list pins this pack's failure
   modes — recurrence of any stem means the draft fell back into the mail-merge skeleton.

## Self-check before presenting (every draft must pass)

- Email greets the person by first name (`Hi <name>,`) — then sentence one is the signal, never throat-clearing.
- Opens on their signal, not a compliment or generic market line — with one clause of credit before the gap.
- Gap is hedged as *likely* ("likely" / "my hunch — tell me if you've got this covered") — never asserted as fact about an environment you can't see — and is structural: names why their current stack can't close it, not a task they forgot.
- Exactly **one** ask; in a first touch it is offer-shaped (gates the gift bundle — default teaser one-pager + short recorded demo), never a time-ask or calendar link.
- DM ≤ 280 chars; email ≤ 100 words. Show the counts.
- Subject is 1–4 words, lowercase, names the signal.
- First-touch email is plain text: no images, screenshots, or attachments; ≤1 untracked link.
- No product pitch or feature list in the first touch — the bundle carries the product.
- No banned fluff words (excited/thrilled/reach out/touch base/synergy/leverage-verb/circle back/…) and no AI tells — passes `docs/prose-craft.md` (no em dash, no "not X, it's Y") — and no category labels ("AI", "platform") as pitch language — name the problem.
- One technical term max in a first touch.
- Case study named with a concrete outcome (cut first if over length).
- Signed with the PROFILE signature.

## Output

Present the drafts inline. If the user wants them saved (or this was called for a Tier-A account during a prospect run), write `prospects-YYYYMMDD-outreach-[company-slug].md` to the account folder `content/<active>/accounts/<company-slug>/`, using the Tier-A pack template in `${CLAUDE_PLUGIN_ROOT}/skills/prospect/references/output-templates.md`. Note any send-timing risks.

If the draft was saved, refresh the cross-run outreach log so it stays current outside a full prospect run too:
```bash
python -m gtm_core.outreach_log build --profile <active>
```

## Guardrails

- **Never auto-send.** These are drafts for human review.
- **Never invent a signal, quote, or metric.** If the real signal is thin, say so and offer to dig for a stronger one rather than fabricate.
- **Voice first:** if a draft can't both fit the voice and stay honest, fix the message, don't bend the voice.
