---
theme: ../../.engine/deck-theme
title: Tripwire clean deck
canvasWidth: 980
---

<h2>ONE VERIFIED IDENTITY<br/>PER AGENT</h2>

<StackDiagram :layers="['Agent identity layer', 'Policy enforcement layer', 'Evidence trail layer']" />

<!--
Cover note: open on the three layers, name the one the buyer already owns, and stop talking.
-->

---
layout: default
tag: "Where the work is"
---

<h2>WHERE THE AGENT WORK IS TODAY</h2>

<GlassCard pad="md"><p>Most teams run a handful of pilots and a single production agent that already calls another entity.</p></GlassCard>

<AskBox question="Of the agent work you have shipped, how many are in production versus pilot?" />

<!--
Deliver this as a count, not a judgement. If they say zero in production, move straight to slide four.
-->

---
layout: default
tag: "Who owns the credential"
---

<h2>WHO OWNS THE CREDENTIAL</h2>

<FlowSequence :steps="['Agent requests access', 'Gateway checks the credential', 'Entity records the decision']" />

<p>Today the answer is usually a shared service account that nobody has rotated since the pilot.</p>

<AskBox question="Which team owns the credential an agent presents when it calls another entity?" />

<!--
Let the silence sit after the question. The name they give is the sponsor for the next meeting.
-->

---
layout: default
tag: "What review asks for"
---

<h2>WHAT REVIEW ASKS FOR</h2>

<ScopeMap :zones="['Inside one entity', 'Across the entity boundary']" />

<p>Security review descopes a crossing it cannot evidence, and the agent ships smaller than planned.</p>

<AskBox question="What evidence does your security review ask for before an agent crosses an entity boundary?" />

<!--
Do not promise a shorter review. Ask what the review asked for last time and write it down.
-->

---
layout: statement
tag: "The Ask"
---

<h2>THE ASK</h2>

<p>A thirty-minute working session with the team that owns the credential, so we can map the one crossing that matters most.</p>

<!--
Propose the session and stop. The ask slide proposes; it never interrogates. Offer two dates.
-->
