# Direct-Response Patterns & Lead-Magnet Bridges

Cross-profile, **de-branded** craft reference for direct-response (DR) assets whose primary goal is
**conversion** (`ContentItem.goal == "conversion"`): moving the audience off-platform to an owned
resource, waitlist, prompt pack, calculator, or DM automation. Loaded by `content-studio` (written
copy) and `video-script` (production video shotlists) at draft time.

**This is not `hook-craft.md`.** `docs/hook-craft.md` governs **strictly the first line or cover
frame** (0–2 seconds). This document governs the **entire structural progression** from opening
symptom through root-cause diagnosis, artifact demonstration, and the conversion bridge (CTA).

---

## 1. The Core Rules of B2B Direct Response

Direct-response copy on professional networks (LinkedIn, X) and short-form video (Reels, Shorts)
operates under tighter constraints than consumer influencer marketing. High-hype templates
damage credibility and trigger automated linter warnings.

Every pattern in this catalog enforces four hard constraints:

1. **Concrete Anchors First (Zero Abstraction):** The opening line must name an empirical
   symptom, a measured audit volume, or a shipped artifact. No category generalities.
2. **Linter-Safe Architecture (No Antithetical Parallelism):**
   Consumer templates rely on theatrical contrast (*"You're not broken, you just..."* or *"Don't
   do X, instead do Y"*). This triggers `tests/linter/content_linter.py:106` (`_ANTITHESIS_RES`).
   State the positive truth directly: *"Calendar architecture dictates sprint velocity, not
   willpower."*
3. **No Rhetorical Question Staging:**
   `docs/hook-craft.md` and `docs/prose-craft.md` ban question openers (*"What do X all have in
   common?"*). State the collective pattern or finding directly as an authoritative thesis.
4. **No Empty Intensifiers:**
   Ban phrases like *"I can't believe how well this works"*, *"mind-blowing"*, or *"random
   automation"*. Replace adverbs with concrete deltas (e.g., *"jumped from 1.8% to 6.2% across 800
   accounts"*).

---

## 2. The Dual-Action Platform Bridge (The CTA Rule)

A conversion asset must tell the reader or viewer exactly what action to take. On consumer
platforms, ManyChat comment automations (*"Comment [WORD] below"*) are standard. On enterprise
networks, pure comment-bait causes algorithmic throttling and friction for senior buyers (e.g.,
CISOs, VPs) who do not want to comment publicly.

Enforce platform-specific bridges:

* **LinkedIn Bridge (Dual-Action):**
  > *Comment "[WORD]" below (or DM me "[WORD]" if you prefer privacy) and I'll send over the raw [resource].*
* **X Bridge (Reply / DM):**
  > *Drop "[WORD]" below or send a DM and I'll share the repo link.*
* **Short-Form Video (Reels/Shorts/TikTok):**
  Spoken line: *"Drop '[WORD]' in the comments and I'll send you the breakdown."*
  Visual overlay: Burned-in lower-third text: `Comment "[WORD]"` (via `caption_text_override`).

---

## 3. The 5 B2B Direct-Response Patterns

### Pattern 01: `dr-symptom-root-cause`
**Concept:** Shift focus from personal frustration to an architectural or process defect, then
bridge to the remediation template.  
**Emotional Trigger Stack:** Productive Discomfort (T4) + Aspiration (T6).  
**Input Requirements:** A felt daily friction point from `audience-psychology.md` + a downloadable
remediation artifact.

* **Structural Beats:**
  1. *Felt Symptom:* The empirical friction the practitioner lives daily (hours wasted, alerts
     firing, PRs blocked).
  2. *Reframed Root Cause:* The architectural or structural design choice actually causing it.
  3. *Remediation Asset:* The concrete template, checklist, or config that fixes it.
  4. *Dual Bridge:* Keyword trigger.

* **Written Copy Template (LinkedIn / X):**
  ```text
  Ten hours of sprint meetings, zero code shipped, release slipping. The issue is not developer
  discipline. It is calendar architecture. Our 3-tier meeting firewall template reclaimed 14
  engineering hours a week. Drop "FIREWALL" below (or DM me) and I'll send over the raw sheet.
  ```

* **Video Shotlist Spec (30–45s Reel):**
  * `Shot 1` (0–3s, Presenter): Camera slow push-in; deliver the felt symptom.
  * `Shot 2` (3–10s, B-roll/VO): Visual metaphor or code/timeline cut illustrating the defect.
  * `Shot 3` (10–22s, Screen): Screen recording showing the actual template or config in use.
  * `Shot 4` (22–32s, Presenter): Keyword lower-third overlay (`Comment "[WORD]"`).

---

### Pattern 02: `dr-earned-authority`
**Concept:** Leverage verified reps or audit volume to share a counterintuitive discovery, bridging
to the documentation.  
**Emotional Trigger Stack:** Deep-cut Insider (T2) + Curiosity Gap (T5).  
**Input Requirements:** Real audit volume or test count from the evidence pack + an executive brief
or checklist.

* **Structural Beats:**
  1. *Earned Authority:* Concrete volume of reps, audits, or deployments completed.
  2. *Counterintuitive Finding:* The non-obvious pattern that contradicts common industry advice.
  3. *Distilled Guide:* The brief, memo, or checklist recording the prerequisites.
  4. *Dual Bridge:* Keyword trigger.

* **Written Copy Template (LinkedIn / X):**
  ```text
  We audited 340 enterprise agent gateways last quarter. Teams achieving high compliance were
  not running larger models: they restricted agent authorization to screen-level mocks. I
  documented the 3 prerequisites in an 8-page brief. Comment "GATEWAY" to grab the PDF.
  ```

* **Video Shotlist Spec (35–45s Reel):**
  * `Shot 1` (0–4s, Presenter): State the audited volume directly (receipts-first).
  * `Shot 2` (4–15s, Screen/B-roll): Visual graph or chart of the pass vs fail distribution.
  * `Shot 3` (15–27s, B-roll/VO): Explain the counterintuitive finding over system telemetry.
  * `Shot 4` (27–37s, Presenter): Display the brief cover; deliver spoken keyword invite.

---

### Pattern 03: `dr-gap-roadblock`
**Concept:** Highlight the gap between an engineering target state and current reality, identify
the single constraint, and provide the tool to remove it.  
**Emotional Trigger Stack:** Productive Discomfort (T4) + Aspiration (T6).  
**Input Requirements:** Target benchmark vs current bottleneck + an open-source repo, script, or
generator.

* **Structural Beats:**
  1. *Target State:* The high-performance outcome the engineering team wants.
  2. *Current Bottleneck:* The manual delay or flakiness stalling progress.
  3. *Single Roadblock:* Isolate the core technical constraint.
  4. *Enabler Asset:* The script or tool that removes it.
  5. *Dual Bridge:* Keyword trigger.

* **Written Copy Template (LinkedIn / X):**
  ```text
  Autonomous deployments in production. Most platform teams remain stalled on manual code reviews
  for 48 hours. The single blocker: non-deterministic tests. We built a deterministic mock
  generator for pytest that stabilizes evaluations. Drop "mock" for the repo.
  ```

* **Video Shotlist Spec (30–40s Reel):**
  * `Shot 1` (0–3s, Screen): Terminal or UI execution showing the automated ideal state.
  * `Shot 2` (3–11s, Presenter): Contrast with the current 48-hour manual bottleneck.
  * `Shot 3` (11–22s, Screen): Code diff or screen recording showing the solver in action.
  * `Shot 4` (22–32s, Presenter): Keyword overlay with clear next step.

---

### Pattern 04: `dr-empirical-test`
**Concept:** Share an empirical experiment, report the measured before/after delta without hype, and
share the workflow diagram.  
**Emotional Trigger Stack:** Receipts-first (T2) + Productive Discomfort (T4).  
**Input Requirements:** Concrete before/after test numbers + architecture flowchart or routing map.

* **Structural Beats:**
  1. *Experiment Setup:* The specific variable tested in production.
  2. *Measured Delta:* Exact before/after numbers with zero subjective adverbs.
  3. *Workflow Artifact:* The flowchart, routing rule set, or configuration map.
  4. *Dual Bridge:* Keyword trigger.

* **Written Copy Template (LinkedIn / X):**
  ```text
  We tested an alternative enrichment waterfall against the standard baseline. Account response
  rates changed from 1.8% to 6.2% across 800 accounts. The entire routing logic is mapped in this
  diagram. Comment "WATERFALL" to inspect the flowchart.
  ```

* **Video Shotlist Spec (30–40s Reel):**
  * `Shot 1` (0–3s, Presenter): Name the experiment and sample size directly.
  * `Shot 2` (3–12s, Screen): Metric graph or dashboard showing the verified lift.
  * `Shot 3` (12–24s, Screen): Camera slow zoom into the architectural flowchart.
  * `Shot 4` (24–32s, Presenter): Spoken CTA + keyword lower-third.

---

### Pattern 05: `dr-industry-benchmark`
**Concept:** Highlight an architectural design pattern shared by tier-1 engineering orgs, explain
the mechanism, and provide the teardown guide.  
**Emotional Trigger Stack:** Status-quo Reframe (T3) + Deep-cut Insider (T2).  
**Input Requirements:** 2–3 benchmark companies + a technical implementation brief or teardown doc.

* **Structural Beats:**
  1. *Tier-1 Roster:* 2–3 admired industry engineering teams utilizing a specific design pattern.
  2. *Architectural Mechanism:* The technical reason why this design choice is standard.
  3. *Implementation Guide:* The teardown brief for growing teams.
  4. *Dual Bridge:* Keyword trigger.

* **Written Copy Template (LinkedIn / X):**
  ```text
  Top engineering teams enforce egress filtering at the Linux kernel layer rather than the
  application gateway. Kernel-level filtering eliminates bypasses from compromised dependencies
  before user code executes. We broke down the topologies in a brief. Comment "kernel".
  ```

* **Video Shotlist Spec (35–45s Reel):**
  * `Shot 1` (0–4s, Presenter): State the companies and their shared architectural choice.
  * `Shot 2` (4–16s, Screen): Architecture diagram comparing standard vs tier-1 topology.
  * `Shot 3` (16–28s, B-roll/VO): Threat model animation explaining the failure mode this prevents.
  * `Shot 4` (28–38s, Presenter): Spoken invite + lower-third keyword super.
