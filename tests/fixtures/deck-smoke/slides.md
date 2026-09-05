---
title: Deck sidecar smoke test
info: |
  Fixture deck. Its only job is to prove the deck-renderer sidecar can render the nine
  components that were MISSING from it between 2026-08-14 and 2026-08-29, and that a
  deliberately varied deck clears gtm_core.deck_consistency.

  Do not add tenant content here. This file is carved into the public OSS cut, so every
  company, number and person in it is fictional (docs/RULES.md §R9).

  Run: bash scripts/deck_sidecar_smoke.sh
author: fixture
aspectRatio: 16/9
canvasWidth: 980
colorSchema: dark
transition: slide-left
---

<!--
  Slide 1 — cover. Staples only.
  Presenter note kept short: this deck is never presented, only rendered.
-->

---
layout: cover
tempo: hold
page: website
align: left
---

<Eyebrow>Fixture · sidecar smoke</Eyebrow>

<HeroTitle
  :lines="['Nine components', 'that production', 'could not render.']"
  :gradient-line="1"
/>

<Banner kind="punch" icon="warning">
  Between 2026-08-14 and 2026-08-29 the sidecar shipped 26 components while the theme had 35.
</Banner>

<!-- If this slide renders, the theme resolved and the baked fonts loaded. -->

---
layout: statement
tempo: brisk
page: website
---

<Eyebrow>The question this deck exists to answer</Eyebrow>

<AskBox
  kicker="A question for the renderer"
  question="Can you draw every component the composer is now allowed to reach?"
/>

<!-- AskBox is the component with 6 uses in a shipped deck the sidecar could not render. -->

---
layout: two-pane
tempo: brisk
page: fabric
---

<Eyebrow>Claim and consequence</Eyebrow>

<BreakTests
  heading="What a stale mirror costs"
  :tests="[
    { k: 'WHO', claim: 'The renderer ran an older theme than the workspace.', implication: 'Every export used components that no longer matched the source.' },
    { k: 'WHAT', claim: 'Nine components were absent from the render path.', implication: 'A deck using any of them lost that content silently.' },
    { k: 'PROOF', claim: 'A print-mode fix appeared 14 times upstream and zero times downstream.', implication: 'The fix never ran where it was needed.' },
  ]"
/>

---
layout: default
page: fabric
---

<Eyebrow>Where the coverage stopped</Eyebrow>

<ScopeMap
  span-label="What the catalog promised"
  covered-label="What the renderer shipped"
  :entities="[
    { name: 'Layouts', sub: 'seven', covered: true },
    { name: 'Styles', sub: 'tokens + animations', covered: true },
    { name: 'Components', sub: '35 in the theme', covered: false },
    { name: 'Export fix', sub: 'print-mode CSS + JS', covered: false },
  ]"
  :gaps="[
    { k: 'GAP', note: 'Nine components existed upstream and nowhere else.' },
    { k: 'GAP', note: 'Both halves of the print-mode fix were missing.' },
  ]"
/>

---
layout: default
page: forge
---

<Eyebrow>The request that could not cross</Eyebrow>

<EntityCrossing
  state="broken"
  caption="A copy boundary nobody was watching"
  band-label="BEFORE"
  band-text="The composer could name a component the renderer had never received."
  :zones="[
    { name: 'Design workspace', sub: 'no CI', nodes: [{ k: 'A', label: 'theme source' }, { k: 'B', label: 'live preview' }] },
    { name: 'Render sidecar', sub: 'runs production', nodes: [{ k: 'C', label: 'baked theme' }, { k: 'D', label: 'export' }] },
  ]"
  :checks="[
    { k: '1', note: 'A component is added upstream.', so: 'The workspace preview shows it immediately.' },
    { k: '2', note: 'Nothing copies it downstream.', so: 'The sidecar never learns the component exists.' },
    { k: '3', note: 'A deck uses it anyway.', so: 'The slide renders empty and no gate objects.' },
  ]"
/>

---
layout: chapter
tempo: hold
page: forge
tag: Section
chapterNo: "01"
---

# Crossing the boundary

<!-- A chapter divider also exercises MeshAurora, PulseHalo, BrandMark and BrandTag,
     which the layout mounts itself — four more components with zero corpus usage. -->

---
layout: default
page: forge
---

<Eyebrow>The same crossing, resolved</Eyebrow>

<FlowSequence
  state="resolved"
  boundary="OWNERSHIP BOUNDARY"
  outcome="One tree is authoritative; the other is regenerated from it."
  :nodes="[
    { k: 'A', label: 'theme home' },
    { k: 'B', label: 'push script' },
    { k: 'C', label: 'workspace copy' },
  ]"
  :checks="[
    { k: '1', note: 'The home lives beside the gates.', so: 'CI sees every change to it.' },
    { k: '2', note: 'The copy is generated, never edited.', so: 'A hand edit is overwritten, not merged.' },
    { k: '3', note: 'A stale copy surfaces in preview.', so: 'The failure is visible in seconds.' },
  ]"
/>

---
layout: default
page: radix
---

<Eyebrow>What the review asks</Eyebrow>

<GateFunnel
  in-label="Theme changes"
  in-sub="any edit to a component or token"
  out-label="Shipped to the renderer"
  out-sub="baked into the image"
  gate-label="DEPLOY"
  blockers-label="WHAT HAS TO BE TRUE"
  legend="✓ satisfied today · ✕ still open"
  :blockers="[
    { k: '1', note: 'The change is committed in a gated repo.', state: 'clear' },
    { k: '2', note: 'The path triggers a deploy.', state: 'clear' },
    { k: '3', note: 'The image is rebuilt.', state: 'open' },
    { k: '4', note: 'A smoke export proves it.', state: 'open' },
  ]"
/>

---
layout: chapter
tempo: brisk
page: radix
tag: Section
chapterNo: "02"
---

# What changed

---
layout: default
page: radix
---

<Eyebrow>Before and after</Eyebrow>

<ProofContrast
  left-key="WAS"
  left-title="Two hand-copied trees"
  left-sub="one of them unguarded"
  left-caption="The authoritative copy was the one nothing could check."
  right-key="NOW"
  from-title="Design workspace"
  from-sub="generated"
  token-label="push"
  to-title="Gated repo"
  to-sub="the home"
  right-caption="The failure mode inverts: staleness lands where it is visible."
/>

---
layout: default
page: website
---

<Eyebrow>What carries, what restarts</Eyebrow>

<ReuseTrack
  :columns="['Tokens', 'Layouts', 'Components', 'Export fix']"
  ships-label="CARRIES"
  ships-items="Tokens · Layouts"
  partial-label="PARTIAL"
  partial-items="Components"
  partial-state="built"
  rebuild-label="RESTARTS"
  rebuild-items="Export fix"
  rebuild-note="from zero"
/>

---
layout: chapter
tempo: brisk
page: website
tag: Section
chapterNo: "03"
---

# What the rules require

---
layout: default
page: website
---

<Eyebrow>What the rules actually require</Eyebrow>

<ReqAnchors
  our-label="How this repo answers it"
  note="Fictional references. This fixture cites no real standard."
  :anchors="[
    { k: 'R1', demand: 'A generated artifact declares what generates it.', src: 'Fixture Handbook §1 (2026-01-01)' },
    { k: 'R2', demand: 'A path that ships must be able to trigger the deploy that ships it.', src: 'Fixture Handbook §2 (2026-01-01)' },
    { k: 'R3', demand: 'A gate that cannot read its input must fail, not pass.', src: 'Fixture Handbook §3 (2026-01-01)' },
  ]"
/>

---
layout: chapter
tempo: brisk
page: website
tag: Section
chapterNo: "04"
---

# What the export must survive

---
layout: default
tempo: burst
page: website
---

<Eyebrow>Three kinds of emphasis, one of them gradient-clipped</Eyebrow>

<CalloutRow :items="[
  { icon: 'magnifying-glass', phrase: 'READ\nTHE RULE.' },
  { icon: 'rocket-launch', phrase: 'RUN\nTHE GATE.', kind: 'accent' },
  { icon: 'seal-check', phrase: 'SHIP\nTHE PROOF.', kind: 'highlight' },
]" />

<FlowTrack :nodes="[
  { label: 'Authored', sub: 'gradient on screen' },
  { label: 'Exported', sub: 'guard flattens it' },
  { label: 'Vector', sub: 'glyphs stay outlines', success: true },
]" />

---
layout: default
page: website
---

<Eyebrow>The last gradient-clipped runs in the theme</Eyebrow>

<ImpactRow :items="[
  { value: 'FLAT', label: 'gradient text under html.deck-export' },
  { value: 'ZERO', label: 'Type 3 bitmap fonts this should produce' },
  { value: 'ONE', label: 'colour each flattened run falls back to' },
]" />

<SpeakerCard
  name="Fictional Presenter"
  org="Fixture Working Group"
  topic="Why a theme gate is cheaper than a debugging session"
  page="website"
>
  <template #number>04</template>
</SpeakerCard>

---
layout: statement
tempo: brisk
page: website
---

<Eyebrow>Export duality — author both states, not one flattened one</Eyebrow>

<RenderWhen context="main">
  <ImpactRow :items="[
    { value: 'ONE', label: 'EXPORT-DUALITY-MAIN-ONLY-MARKER' },
  ]" />
</RenderWhen>

<RenderWhen context="print">
  <BreakTests
    heading="EXPORT-DUALITY-PRINT-ONLY-MARKER"
    :tests="[
      { k: 'LIVE', claim: 'The room hears one number.', implication: 'A live audience does not read a table.' },
      { k: 'PRINT', claim: 'The PDF recipient gets the full breakdown.', implication: 'RenderWhen prints what the live branch only summarizes.' },
    ]"
  />
</RenderWhen>

<GateFunnel
  v-click="1"
  in-label="v-click steps on this slide"
  in-sub="present only under --with-clicks"
  out-label="rendered here"
  out-sub="1 of 3 states this slide can export as"
  gate-label="STEP"
  blockers-label="WHAT THIS SLIDE PROVES"
  :blockers="[
    { k: '1', note: 'RenderWhen renders only its matching context — never both.', state: 'clear' },
    { k: '2', note: 'A v-click element gets its own page only under --with-clicks.', state: 'clear' },
  ]"
/>

<Banner v-click="2" kind="source">
  Second click step — with_clicks=true turns this slide into three PDF pages instead of one.
</Banner>

---
layout: end
page: website
---

<Eyebrow>Fixture complete</Eyebrow>

<HeroTitle
  :lines="['Every layout.', 'Every guarded run.', 'Nothing invented.']"
  :gradient-line="2"
/>

<Banner kind="source">
  If every slide above drew, the sidecar is running the current theme.
</Banner>
