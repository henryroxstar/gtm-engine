---
theme: ../../.engine/deck-theme
title: Tripwire dirty deck
canvasWidth: 980
---

<h2>A HEADLINE OVER NOTHING</h2>

<!--
Cover note long enough for D5: the slide itself is a headline and nothing else, which D10 flags.
-->

---
layout: cover
tag: "Banners"
---

<h2>TWO BANNERS AND A WALL</h2>

<Banner kind="punch">FIRST BANNER PUNCHES ABOVE ITS WEIGHT AND THEN KEEPS GOING UNTIL THE LINE WRAPS THREE TIMES ACROSS THE CANVAS BECAUSE NOBODY MEASURED IT BEFORE IT SHIPPED INTO THE ROOM WITH THE BUYER WATCHING</Banner>
<Banner v-click="1" kind="punch">SECOND BANNER CLIPS OFF THE CANVAS.</Banner>

<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>
<p>The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses. The estimate says this slide holds one paragraph about the client's architecture and the boundary it crosses.</p>

<!--
Note long enough for D5. Two banners, one over length, and a wall of prose that blows the fit budget.
-->

---
layout: two-pane
tag: "Suppressed"
---

<!-- lint-ok D1: the second banner is a deliberate reveal on this one slide -->

<h2>SUPPRESSION IS HONOURED</h2>

<Banner kind="punch">FIRST BANNER.</Banner>
<Banner v-click="1" kind="punch">SECOND BANNER, SUPPRESSED.</Banner>

<GlassCard pad="md"><p>The card carries twenty-odd words so that the slide clears the word floor and the visual check with nothing else to say.</p></GlassCard>

<!--
Note long enough for D5. This slide trips no D1 rule because the author suppressed it with a reason.
-->

---
layout: statement
tag: "The Ask"
---

<h2>THE ASK, INTERROGATED</h2>

<AskBox question="Who signs the purchase order for a new gateway this quarter?" />
<AskBox question="How many pilots did your platform group start this quarter?" />

<!--
Note long enough for D5. Two questions stacked on the ask slide, neither from the question bank.
-->

---
layout: bogus
tag: "Appendix"
---

<h2>APPENDIX WITH BROKEN RENDER</h2>

<Widget kind="mystery" />

<p class="foo-bar">A class with no rule, an unknown component, an unknown layout and an appendix tag, all on one slide for the render checks.</p>

<style>
.unused-rule { color: red; }
</style>

<!--
Short.
-->

---
layout: default
image: /generated/background.png
---

<h2>THE IMAGE DOES THE EXPLAINING</h2>

<p>A generated background restates the headline while the twenty-five words of prose here carry the whole argument.</p>

<!--
Note long enough for D5. A frontmatter image and no visual component is D7's image rule.
-->

---
layout: default
tag: "Over budget"
---

<h2>SIXTY WORDS ON A SLIDE</h2>

<p>Sixty-odd words is over the soft budget and still under the hard cap, so this slide draws the warning rather than the error, which is the point: one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty-one twenty-two twenty-three twenty-four twenty-five twenty-six twenty-seven twenty-eight.</p>

<!--
Note long enough for D5. Fifty-six to ninety words trips the soft word budget as a warning.
-->
