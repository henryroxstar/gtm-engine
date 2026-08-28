<!-- GENERATED — DO NOT EDIT. X tweet-pattern catalog.
     Source of truth: gtm_core/tweet_patterns.toml.
     Regenerate: `python -m gtm_core.tweet_patterns generate`.
     CI (tests/skills/test_tweet_patterns.py) fails if this drifts. -->

# X tweet-pattern catalog

**Owner of:** the closed vocabulary of X post STRUCTURES (`pattern_id` values) and their B2B transpositions.

**Not the owner of:** the opening line (`docs/hook-craft.md`), post-level emotional engineering (`docs/virality-engineering.md`), sentence-level style (`docs/prose-craft.md`), or platform mechanics (`docs/x-optimization.md`). This doc picks the *shape*; those govern everything inside it.

Pattern **names** are taxonomy drawn from two third-party ebooks ("101 Ways to Write a Tweet" and "Even More Ways to Write a Tweet", The Art of Purpose, 2021; read in full 2026-08-23). Every template, transposition rule, and example below is original. No book text is reproduced. `source` cites the book index only.

## How to choose

Same discipline as `docs/hook-craft.md`'s Workflow: propose **3 candidates across distinct patterns**, self-check each against its guardrail (if `conditional`) and the concrete-anchor rule, then present all 3 as an operator choice. Never reach for a pattern silently, and never reuse the same one back to back for one hook. Check `content/<active>/history.jsonl` first.

## Not in this catalog

Deliberately absent: engagement-bait shapes (follow-trains, giveaway threads, outrage or shame framed at a person or group), anything reaching for a named competitor as the enemy, personal-life posts (spouse, co-workers, family), and any pattern whose only mechanism is speaking in bare absolutes rather than a sourced specific. See `docs/hook-craft.md`'s brand-safety section and each profile's `voice-bans.txt` for the standing rule these patterns would otherwise need to repeat.

## Summary

| id | name | formats | archetype | triggers | fit |
|---|---|---|---|---|---|
| `accomplishment-value-thread` | Accomplishment + value thread | thread | `receipts-first` | T2, T6 | core |
| `actionable-steps` | Actionable steps | single, thread | `named-number` | T6 | core |
| `analogy` | Analogy | single | `deep-cut-insider` | T5 | core |
| `bad-advice` | The bad-advice correction | single | `status-quo-fault-line` | T1 | core |
| `before-after` | Before / after | single, thread | `before-after` | T6, T2 | core |
| `biggest-mistake` | The biggest mistake I made | single, thread | `the-concession` | T1, T4 | core |
| `bs-excuse` | The BS excuse | single | `status-quo-fault-line` | T1 | core |
| `clickbait-redirect` | Clickbait redirect | thread | `frame-phrase` | T5 | core |
| `common-misconception` | Common misconception | single, thread | `status-quo-fault-line` | T5 | core |
| `comparison` | Comparison | single | `named-number` | T5 | core |
| `contrast` | Contrast | single | `before-after` | T5 | core |
| `define-the-word` | Define the word | single | `deep-cut-insider` | T2 | core |
| `harsh-truth` | Harsh truth | single | `status-quo-fault-line` | T1, T4 | core |
| `i-have-a-rule` | I have a rule | single | `frame-phrase` | T2 | core |
| `interesting-fact` | Interesting fact | single | `deep-cut-insider` | T5 | core |
| `juxtaposition` | Juxtaposition | single | `before-after` | T3 | core |
| `less-vs-more` | Less vs. more | single | `before-after` | T4 | core |
| `list` | List | single | `list-structure` | T2 | core |
| `list-thread` | List thread | thread | `list-structure` | T2 | core |
| `milestone-contrast` | Milestone with contrast | single | `receipts-first` | T6 | core |
| `nobody-wants-to-admit-it` | Nobody wants to admit it | single | `status-quo-fault-line` | T1 | core |
| `one-liner-value-followup` | One-liner with value follow-up | single | `receipts-first` | T2 | core |
| `origin-story` | Origin story | thread | `story-lesson` | T1 | core |
| `overrated` | Overrated | single | `status-quo-fault-line` | T3 | core |
| `percent-tweet` | The % tweet | single | `named-number` | T5 | core |
| `post-receipts` | Post receipts | single, thread | `receipts-first` | T2 | core |
| `read-that-again` | Read that again | single | `wordplay` | T1 | core |
| `reality-check` | Reality check | single | `the-concession` | T4 | core |
| `simplified-blueprint` | Simplified blueprint | single, thread | `named-number` | T6 | core |
| `solve-a-problem` | Solve a problem | single, thread | `the-stakes` | T4 | core |
| `steal-this` | Steal this template | single, thread | `receipts-first` | T2, T6 | core |
| `story-thread` | Story thread | thread | `story-lesson` | T1 | core |
| `talk-about-why` | Talk about why | single, thread | `the-concession` | T1 | core |
| `the-most-important` | The most important | single | `named-number` | T2 | core |
| `uncomfortable-fact` | Uncomfortable fact | single | `status-quo-fault-line` | T1 | core |
| `underrated` | Underrated | single | `deep-cut-insider` | T3 | core |
| `unpopular-fact` | Unpopular fact | single | `status-quo-fault-line` | T1 | core |
| `authority` | The authority tweet | single | `the-stakes` | T2 | conditional |
| `by-stage` | By stage | single | `named-number` | T2 | conditional |
| `challenge` | Give a challenge | single | `frame-phrase` | T6 | conditional |
| `i-dont-know-who-needs-to-hear-this` | I don't know who needs to hear this | single | `frame-phrase` | T1 | conditional |
| `past-vs-present` | Past vs. present | single | `before-after` | T5 | conditional |
| `polarizing-platitude` | Polarizing platitude | single | `status-quo-fault-line` | T3 | conditional |
| `question-post` | Question post | single | `question-post` | T2 | conditional |
| `question-with-limit` | Question with a limit | single | `question-post` | T2 | conditional |
| `quote` | Use a quote | single | `deep-cut-insider` | T2 | conditional |
| `repeat-after-me` | Repeat after me | single | `frame-phrase` | T6 | conditional |
| `six-months` | Six months | single | `named-number` | T6 | conditional |
| `sports-analogy` | The sports analogy | single | `deep-cut-insider` | T5 | conditional |
| `tell-no-one` | Tell no one | single | `frame-phrase` | T2 | conditional |
| `things-that-do-dont` | Things that do, things that don't | single, thread | `before-after` | T3 | conditional |
| `unpopular-opinion` | Unpopular opinion | single | `status-quo-fault-line` | T1 | conditional |
| `write-to-someone` | Write to someone | single | `the-concession` | T1 | conditional |

## Core patterns

### Accomplishment + value thread (`accomplishment-value-thread`)

- **Formats:** thread
- **Archetype:** `receipts-first`
- **Triggers:** T2, T6
- **Source:** aop-101 #34

**Template**
```
1/ <the specific milestone>
2..N/ <the mechanism behind it, taught, not bragged>
last/ <CTA>
```

**Transposition.** confidence from specificity, not absolutes: the milestone must be a real, checkable number

**Example.** 1/ We cut our vendor-review turnaround from 6 weeks to 9 days.
2/ The change wasn't more reviewers. It was reusing last year's exceptions as the first pass, not the last.
3/ Full breakdown in the next few tweets.

### Actionable steps (`actionable-steps`)

- **Formats:** single, thread
- **Archetype:** `named-number`
- **Triggers:** T6
- **Source:** aop-101 #18

**Template**
```
<Topic>, in <N> steps:

1. <step>
2. <step>
3. <step>
```

**Transposition.** confidence from specificity, not absolutes: each step must be an action the reader can start today, not a category

**Example.** Cutting your vendor review time in three steps:

1. Pull last year's exceptions, not this year's checklist.
2. Score against those exceptions first.
3. Only then run the generic questionnaire.

### Analogy (`analogy`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T5
- **Source:** aop-101 #64

**Template**
```
<Unfamiliar concept> is like <a familiar one>. <The one point of the comparison that actually maps>.
```

**Transposition.** confidence from specificity, not absolutes: the analogy earns its place only if it explains a mechanism, not just a mood

**Example.** An agent with unscoped write access is like a new hire with the master key on day one. The real cost shows up later, when nobody can tell whether it was ever misused.

### The bad-advice correction (`bad-advice`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-101 #89

**Template**
```
Common advice: <the popular but flawed guidance>. Better: <the specific correction, with the reason it holds up>.
```

**Transposition.** confidence from specificity, not absolutes: name why the common advice fails in a specific, real case, not in general

**Example.** Common advice: grant broad access early, tighten later. Better: scope narrow from day one. 'Tighten later' is a task nobody schedules until an incident schedules it for you.

### Before / after (`before-after`)

- **Formats:** single, thread
- **Archetype:** `before-after`
- **Triggers:** T6, T2
- **Source:** aop-101 #5

**Template**
```
Before: <state one>. After: <state two, earned>. What changed: <the one mechanism>.
```

**Transposition.** confidence from specificity, not absolutes: name the mechanism, not just the delta

**Example.** Before: our SDR read 40 accounts a day and missed the renewal signal every time. After: the same SDR reads 400 and catches it. What changed: one field, contract_end_date, wired into the daily digest.

### The biggest mistake I made (`biggest-mistake`)

- **Formats:** single, thread
- **Archetype:** `the-concession`
- **Triggers:** T1, T4
- **Source:** emh-generator #19

**Template**
```
The biggest mistake I made as <role>: <the specific decision, named>. What it cost: <the measurable consequence>.
```

**Transposition.** the mistake is your own and dated, never a class of people's; the cost is a number or a named consequence, never 'a lot of time'

**Example.** The biggest mistake I made as a solo founder: I sent 40 outbound emails a week and never once read the prospect's last funding round. Nine months, 1,400 sends, four replies.

### The BS excuse (`bs-excuse`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-101 #87

**Template**
```
"<the common excuse, quoted>." No. <The real, specific reason, stated plainly>.
```

**Transposition.** confidence from specificity, not absolutes: the correction needs a mechanism, not just contradiction

**Example.** "We'll add logging once we scale." No. The reason to log now is that you can't reconstruct what an agent did last month once the reason to check has already shown up.

### Clickbait redirect (`clickbait-redirect`)

- **Formats:** thread
- **Archetype:** `frame-phrase`
- **Triggers:** T5
- **Source:** aop-101 #20

**Template**
```
<Provocative setup line>. <Body that leans into the setup>. <Redirect: your actual, specific position>.
```

**Transposition.** confidence from specificity, not absolutes: the redirect must be a real position with a mechanism, not a punchline

**Example.** AI agents don't need more autonomy. They need a smaller blast radius. Give one agent write access to one system, log every call, and the autonomy question mostly stops mattering.

### Common misconception (`common-misconception`)

- **Formats:** single, thread
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T5
- **Source:** aop-more #110

**Template**
```
Common misconception: <the myth>. What actually happens: <the correction, with mechanism>.
```

**Transposition.** confidence from specificity, not absolutes: the correction must explain the mechanism, not just contradict the myth

**Example.** Common misconception: adding a second approver slows a workflow down. What actually happens at Meridian Ledger: the second approver catches the exception before it becomes a rollback, so the average cycle got faster.

### Comparison (`comparison`)

- **Formats:** single
- **Archetype:** `named-number`
- **Triggers:** T5
- **Source:** aop-101 #42

**Template**
```
<Thing A> and <thing B> look similar. The difference that matters: <the specific delta>.
```

**Transposition.** confidence from specificity, not absolutes: the difference must be mechanical, not a matter of taste

**Example.** A signed attestation and a live control look similar on a vendor's slide. The difference that matters: one gets re-checked next quarter, the other doesn't.

### Contrast (`contrast`)

- **Formats:** single
- **Archetype:** `before-after`
- **Triggers:** T5
- **Source:** aop-101 #43

**Template**
```
<Two nearly identical things>. One difference: <the detail that changes the outcome>.
```

**Transposition.** confidence from specificity, not absolutes: keep both sides genuinely close; the whole post rests on the one real gap

**Example.** Two audit trails. One logs who called the API. One logs who authorized it. Only the second one answers an auditor's actual question.

### Define the word (`define-the-word`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T2
- **Source:** aop-101 #16

**Template**
```
<Term> doesn't mean <the diluted popular usage>. It means <your sharper, working definition>.
```

**Transposition.** confidence from specificity, not absolutes: ground the redefinition in a concrete case, not a dictionary flex

**Example.** Observability doesn't mean a dashboard with more panels. It means you can answer a question about a request you didn't anticipate asking, without shipping new code.

### Harsh truth (`harsh-truth`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1, T4
- **Source:** aop-101 #11

**Template**
```
Harsh truth: <the uncomfortable, specific claim>.
```

**Transposition.** confidence from specificity, not absolutes: the claim carries a number or a named mechanism, never a bare assertion

**Example.** Harsh truth: most agent demos never show the audit log, because there isn't one to show.

### I have a rule (`i-have-a-rule`)

- **Formats:** single
- **Archetype:** `frame-phrase`
- **Triggers:** T2
- **Source:** aop-101 #3

**Template**
```
I have a rule: <the specific, actionable rule>.
```

**Transposition.** confidence from specificity, not absolutes: the rule must be something you can point to actually following

**Example.** I have a rule: no agent gets write access to a system before someone can name, in one sentence, what it's allowed to touch.

### Interesting fact (`interesting-fact`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T5
- **Source:** aop-101 #92

**Template**
```
<Specific, sourced fact from the domain>. <One line on why it matters>.
```

**Transposition.** confidence from specificity, not absolutes: cite where the fact came from

**Example.** Most SOC 2 exceptions we've reviewed trace back to a control that was true when written and false by the time of the audit. The gap is staleness, not dishonesty.

### Juxtaposition (`juxtaposition`)

- **Formats:** single
- **Archetype:** `before-after`
- **Triggers:** T3
- **Source:** aop-101 #93

**Template**
```
<Scenario one>. <Scenario two, placed right after it with no comment>.
```

**Transposition.** confidence from specificity, not absolutes: let the two scenarios do the work; add at most one closing line, no editorializing

**Example.** A vendor with a five-page security policy nobody has read. A vendor with a one-page policy the whole team can recite. Guess which one passes the next incident review faster.

### Less vs. more (`less-vs-more`)

- **Formats:** single
- **Archetype:** `before-after`
- **Triggers:** T4
- **Source:** aop-101 #41

**Template**
```
Do less: <the thing people over-invest in>. Do more: <the thing that actually moves the number>.
```

**Transposition.** confidence from specificity, not absolutes: both sides need a reason, not just a directive

**Example.** Do less: writing a new policy for every incident. Do more: testing the policies you already have against the incident you just had.

### List (`list`)

- **Formats:** single
- **Archetype:** `list-structure`
- **Triggers:** T2
- **Source:** aop-101 #25

**Template**
```
<Headline>:

- <item>
- <item>
- <item>
```

**Transposition.** confidence from specificity, not absolutes: every item must be independently true and checkable, not padding to hit a round number

**Example.** What actually slows down a SOC 2 renewal:

- Evidence collected by hand, not exported
- No owner assigned per control
- The auditor's first question has no existing answer

### List thread (`list-thread`)

- **Formats:** thread
- **Archetype:** `list-structure`
- **Triggers:** T2
- **Source:** aop-101 #30

**Template**
```
1/ <headline promising N items>
2..N/ <one item per tweet, each independently useful>
last/ <recap + CTA>
```

**Transposition.** confidence from specificity, not absolutes: each numbered tweet must stand alone if quoted out of context

**Example.** 1/ 7 questions we now ask before enabling any agent write access
2/ Which system, specifically, can it write to?
3/ What's the smallest scope that still does the job?
...

### Milestone with contrast (`milestone-contrast`)

- **Formats:** single
- **Archetype:** `receipts-first`
- **Triggers:** T6
- **Source:** aop-101 #70

**Template**
```
<Time ago>: <the starting state>. <Now>: <the milestone>. <One line on what it took>.
```

**Transposition.** confidence from specificity, not absolutes: the contrast is the number, not the adjectives around it

**Example.** A year ago: every agent action was logged to a text file nobody read. Now: a queryable, signed trail an auditor can run their own report against. The unglamorous part took longer than the exciting part.

### Nobody wants to admit it (`nobody-wants-to-admit-it`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-more #103

**Template**
```
Nobody wants to admit it: <the specific, evidenced claim>.
```

**Transposition.** confidence from specificity, not absolutes: the claim needs a real example behind it, not a vibe

**Example.** Nobody wants to admit it: the runbook everyone points to during an incident was last tested during onboarding, a year ago.

### One-liner with value follow-up (`one-liner-value-followup`)

- **Formats:** single
- **Archetype:** `receipts-first`
- **Triggers:** T2
- **Source:** aop-101 #29

**Template**
```
<Short, standalone claim>. (Follow up once it gets traction: <the expanded, evidenced version>.)
```

**Transposition.** confidence from specificity, not absolutes: the follow-up must add a mechanism or example, not just restate the claim louder

**Example.** A control nobody tests is a control that doesn't exist. Follow-up once this lands: here's the quarterly test schedule we run for every control we cite in a customer's audit.

### Origin story (`origin-story`)

- **Formats:** thread
- **Archetype:** `story-lesson`
- **Triggers:** T1
- **Source:** aop-101 #31

**Template**
```
1/ <where this started, and why>
2..N/ <what it took, honestly>
last/ <where it stands now>
```

**Transposition.** confidence from specificity, not absolutes: name the actual cost (time, a failed attempt), don't skip to the win

**Example.** 1/ We started logging agent actions because a customer asked a question we couldn't answer: which agent touched this row?
2/ The first version was a print statement someone forgot to remove.
3/ It's now a signed, queryable audit trail on every write.

### Overrated (`overrated`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T3
- **Source:** aop-101 #35

**Template**
```
<Thing> is overrated. <The specific reason, with a mechanism>.
```

**Transposition.** confidence from specificity, not absolutes: name what the thing fails to do, not that it's fashionable

**Example.** A longer vendor questionnaire is overrated. Past a point, length correlates with less careful answers, not more coverage.

### The % tweet (`percent-tweet`)

- **Formats:** single
- **Archetype:** `named-number`
- **Triggers:** T5
- **Source:** aop-101 #96

**Template**
```
<N>% of <population> <specific, sourced claim>.
```

**Transposition.** confidence from specificity, not absolutes: the number must be real and sourced (or explicitly marked as an internal estimate), never invented for effect

**Example.** In our own review of 40 vendor risk assessments this year, 31 had no re-test date on file.

### Post receipts (`post-receipts`)

- **Formats:** single, thread
- **Archetype:** `receipts-first`
- **Triggers:** T2
- **Source:** aop-101 #88

**Template**
```
<The specific, checkable number>. <One line on how it was earned, not just stated>.
```

**Transposition.** confidence from specificity, not absolutes: a receipt is a number with a source, not an adjective

**Example.** 9 days, down from 6 weeks. That's our vendor-review turnaround after we started reusing last year's exceptions as the first pass instead of the last.

### Read that again (`read-that-again`)

- **Formats:** single
- **Archetype:** `wordplay`
- **Triggers:** T1
- **Source:** aop-more #108

**Template**
```
<A short, dense sentence, once>. Read that again.
```

**Transposition.** confidence from specificity, not absolutes: the sentence must reward a second read with a real point, not just wordplay

**Example.** A control you haven't tested is a hope with a compliance stamp on it. Read that again.

### Reality check (`reality-check`)

- **Formats:** single
- **Archetype:** `the-concession`
- **Triggers:** T4
- **Source:** aop-101 #24

**Template**
```
Reality check: <the thing that took longer / cost more / went less smoothly than the highlight reel suggests>.
```

**Transposition.** confidence from specificity, not absolutes: name the actual cost, not a vague 'it was hard'

**Example.** Reality check: our first attempt at agent audit logging shipped without timestamps. It took a customer's support ticket to notice.

### Simplified blueprint (`simplified-blueprint`)

- **Formats:** single, thread
- **Archetype:** `named-number`
- **Triggers:** T6
- **Source:** aop-101 #19

**Template**
```
The path to <outcome>, stripped to the essentials:

1. <step>
2. <step>
```

**Transposition.** confidence from specificity, not absolutes: simplify the steps, never the honesty about what each one actually takes

**Example.** The path to a working incident postmortem, stripped down:

1. Write the timeline before the explanation.
2. Name the decision that would have prevented it.
3. Ship one change, not ten.

### Solve a problem (`solve-a-problem`)

- **Formats:** single, thread
- **Archetype:** `the-stakes`
- **Triggers:** T4
- **Source:** aop-101 #91

**Template**
```
<The problem, named plainly>. <Why the usual fix doesn't hold>. <What actually resolves it>.
```

**Transposition.** confidence from specificity, not absolutes: 'the usual fix' must be a real, common approach, not a straw man

**Example.** Vendor questionnaires go stale within a quarter. Re-sending the same PDF doesn't catch that. What holds up: tying the questionnaire's re-send date to the vendor's own change log, not to a calendar reminder.

### Steal this template (`steal-this`)

- **Formats:** single, thread
- **Archetype:** `receipts-first`
- **Triggers:** T2, T6
- **Source:** emh-generator #27

**Template**
```
Steal this <artifact>: <what it does, in one line>. <The one constraint that makes it work>.
```

**Transposition.** hand over a real artifact you actually run, not a generic checklist; it must be complete enough to use without a reply or a DM

**Example.** Steal this account-research prompt: it reads a company's last four filings and returns the three lines a first call should open on. The constraint that makes it work: it must quote, never summarize.

### Story thread (`story-thread`)

- **Formats:** thread
- **Archetype:** `story-lesson`
- **Triggers:** T1
- **Source:** aop-101 #31

**Template**
```
1/ <the moment, concretely>
2..N/ <what happened, in order>
last/ <the lesson, stated once>
```

**Transposition.** confidence from specificity, not absolutes: the lesson must follow from the specific events told, not be bolted on

**Example.** 1/ Our first agent rollout wrote to the production database on day two.
2/ Nobody had scoped its permissions past 'read customer records.'
3/ We caught it in a diff review, not in production.
4/ Now every new agent gets a scoped service account before it gets a task.

### Talk about why (`talk-about-why`)

- **Formats:** single, thread
- **Archetype:** `the-concession`
- **Triggers:** T1
- **Source:** aop-101 #90

**Template**
```
Why we <do the specific thing>: <the honest, specific reason>.
```

**Transposition.** confidence from specificity, not absolutes: the reason must be the real one, including the unflattering part

**Example.** Why we log every agent write, not just the risky ones: we didn't know which ones were risky until we could see all of them side by side.

### The most important (`the-most-important`)

- **Formats:** single
- **Archetype:** `named-number`
- **Triggers:** T2
- **Source:** aop-more #111

**Template**
```
The most important <thing> in <domain>: <the specific claim>.
```

**Transposition.** confidence from specificity, not absolutes: defend the ranking with one concrete reason, not just the label 'most important'

**Example.** The most important line in an incident postmortem: the decision that would have prevented it, named once, not buried in the timeline.

### Uncomfortable fact (`uncomfortable-fact`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-more #49

**Template**
```
<Domain> fact nobody says out loud: <the specific, sourced claim>.
```

**Transposition.** confidence from specificity, not absolutes: cite the source of the fact in the same line or the next

**Example.** Compliance fact nobody says out loud: a signed audit report and a working control are not the same thing.

### Underrated (`underrated`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T3
- **Source:** aop-101 #36

**Template**
```
<Thing> is underrated. <The specific reason it works, that most people skip>.
```

**Transposition.** confidence from specificity, not absolutes: name the mechanism that makes it work, not just that you like it

**Example.** The pre-mortem is underrated. Ten minutes asking 'how would this fail' before a rollout catches more than the retro after it does.

### Unpopular fact (`unpopular-fact`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-101 #47

**Template**
```
<Stated as fact, no hedge>: <the claim>.
```

**Transposition.** confidence from specificity, not absolutes: state one fact, not a sweeping generalization, and be ready to source it in the replies

**Example.** Most vendor security questionnaires get filled out once and reused for three years without a re-check.

## Conditional patterns

### The authority tweet (`authority`)

- **Formats:** single
- **Archetype:** `the-stakes`
- **Triggers:** T2
- **Source:** aop-101 #67

**Template**
```
<A confident, specific claim>. <The proof, cited or shown>.
```

**Transposition.** confidence from specificity, not absolutes: the proof must be real and checkable, never implied or invented

**Example.** Audit trails don't slow agents down. Ours added under 5ms per call, measured across 2 million logged actions last quarter.

**Guardrail.** Every claim of authority needs cited, checkable proof in the same post or the very next one. An authority claim with no proof is the exact failure this catalog exists to prevent.

### By stage (`by-stage`)

- **Formats:** single
- **Archetype:** `named-number`
- **Triggers:** T2
- **Source:** aop-101 #15

**Template**
```
By <stage, e.g. Series A / $1M ARR>, you should have: <3-5 specific items>.
```

**Transposition.** transposed from the book's 'by age' framing to a company-stage axis, and from bare absolutes to sourced specifics: every item needs a reason, not just a checklist entry

**Example.** By the time you have your first enterprise customer, you should have: a named owner for every control in your security questionnaire, and a re-test date on each one.

**Guardrail.** Stage only (funding round, ARR, headcount), never a person's age or personal-life milestone. Each item needs one line on why it matters at that stage, not just a label.

### Give a challenge (`challenge`)

- **Formats:** single
- **Archetype:** `frame-phrase`
- **Triggers:** T6
- **Source:** aop-more #104

**Template**
```
Challenge: <one specific, self-imposed task with a deadline>.
```

**Transposition.** confidence from specificity, not absolutes: the challenge must be concretely completable, not a vague resolution

**Example.** Challenge: find one system today where you genuinely don't know which agent last wrote to it. Fix the logging gap before you fix anything else this week.

**Guardrail.** Frame it as a challenge to yourself or the reader personally undertakes, never a dare aimed at the reader's competence or a veiled test of the audience.

### I don't know who needs to hear this (`i-dont-know-who-needs-to-hear-this`)

- **Formats:** single
- **Archetype:** `frame-phrase`
- **Triggers:** T1
- **Source:** aop-101 #9

**Template**
```
I don't know who needs to hear this, but <one specific piece of advice>.
```

**Transposition.** confidence from specificity, not absolutes: the advice must be concrete enough to act on today, not a mood

**Example.** I don't know who needs to hear this, but the agent you haven't scoped yet is the one that will surprise you first.

**Guardrail.** One piece of advice, grounded in something you've actually seen go wrong or right. Never a vague inspirational line with nothing underneath it.

### Past vs. present (`past-vs-present`)

- **Formats:** single
- **Archetype:** `before-after`
- **Triggers:** T5
- **Source:** aop-101 #38

**Template**
```
<N> years ago: <the old, specific state>. Today: <the new, specific state>.
```

**Transposition.** confidence from specificity, not absolutes: the comparison must be fair and checkable, not nostalgia dressed as insight

**Example.** Three years ago: 'we trust the agent because it passed the demo.' Today: we trust the agent because every action it takes is logged, scoped, and reviewable after the fact.

**Guardrail.** Both states must be genuinely comparable and specific. Never use this to make a competitor's older approach look worse than it was, only to describe your own or the field's evolution.

### Polarizing platitude (`polarizing-platitude`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T3
- **Source:** aop-101 #37

**Template**
```
<Adjective one> teams <do X>. <Adjective two> teams <do Y>. Guess which ships fewer incidents.
```

**Transposition.** confidence from specificity, not absolutes: replace the book's bare-absolute delivery with a claim you could actually back with an example

**Example.** Careful teams test their runbooks before an incident. Confident teams test them during one. Guess which postmortem is shorter.

**Guardrail.** Never speak in absolutes for their own sake (book entry #110's own advice, inverted here on purpose). Each side needs a real, defensible difference, not just opposite adjectives.

### Question post (`question-post`)

- **Formats:** single
- **Archetype:** `question-post`
- **Triggers:** T2
- **Source:** aop-101 #26

**Template**
```
<A genuine, specific question about the reader's own experience>?
```

**Transposition.** confidence from specificity, not absolutes: ask a specific question about a real situation, not a leading or rhetorical one

**Example.** What's the oldest control in your security questionnaire that hasn't been re-tested since it was written?

**Guardrail.** Body/standalone post only, never the opening line of a hook or tweet 1 of a thread: docs/hook-craft.md's first line is never a question. Ask to learn, don't imply an answer.

### Question with a limit (`question-with-limit`)

- **Formats:** single
- **Archetype:** `question-post`
- **Triggers:** T2
- **Source:** aop-101 #27

**Template**
```
<A specific question>, in <N> word(s) or less.
```

**Transposition.** confidence from specificity, not absolutes: the constraint should make the question easier to answer, not gimmicky

**Example.** The one control you'd re-test first if you had a single afternoon: in three words or less.

**Guardrail.** Body/standalone post only, never tweet 1 or a hook's opening line. The word limit exists to lower the reply barrier, not to turn the question into a game with no content.

### Use a quote (`quote`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T2
- **Source:** aop-101 #28

**Template**
```
"<accurate, attributed quote>" (<source>). <Your one-line extension, tied to your own point>.
```

**Transposition.** confidence from specificity, not absolutes: cite the real source, and add original commentary rather than letting the quote stand alone

**Example.** "You can't secure what you can't see" (a line that shows up in nearly every security conference talk). The part that's usually missing: seeing isn't enough without a record of who acted on what you saw.

**Guardrail.** The quote must be verbatim and attributed to its real source (prose-craft's quote exemption covers the quote itself, not a paraphrase dressed as one). Never invent or misattribute a quote.

### Repeat after me (`repeat-after-me`)

- **Formats:** single
- **Archetype:** `frame-phrase`
- **Triggers:** T6
- **Source:** aop-101 #4

**Template**
```
Repeat after me: <one short, positive, specific line>.
```

**Transposition.** confidence from specificity, not absolutes: the line must be actionable, not a generic affirmation

**Example.** Repeat after me: an audit trail you can't query is just a backup with extra steps.

**Guardrail.** Every line must be positive and concretely actionable, never a veiled dig at a competitor, a team, or a named practice. If it only works as a put-down, it isn't this pattern.

### Six months (`six-months`)

- **Formats:** single
- **Archetype:** `named-number`
- **Triggers:** T6
- **Source:** aop-101 #22

**Template**
```
In six months you can <a specific, realistic outcome>, if you start <the one concrete step> today.
```

**Transposition.** confidence from specificity, not absolutes: the timeframe and outcome must both be realistic for the domain, not hype-scaled

**Example.** In six months you can have a queryable audit trail across every agent in production, if you start by logging one field, the calling identity, today.

**Guardrail.** The timeframe must match the claim's real difficulty. A six-month promise for something that takes two years is the exact bare-absolute failure this catalog exists to avoid.

### The sports analogy (`sports-analogy`)

- **Formats:** single
- **Archetype:** `deep-cut-insider`
- **Triggers:** T5
- **Source:** aop-101 #65

**Template**
```
<A specific, well-known sports moment>. <The one point of the comparison that maps to your domain>.
```

**Transposition.** confidence from specificity, not absolutes: the mapping must be tight and explained, not decorative name-dropping

**Example.** A team that wins the championship still reviews the game tape. A team that ships an agent to production without reviewing its action log is skipping the tape.

**Guardrail.** Keep the analogy to the one point that actually maps. Cut it if the sports detail is more interesting than the point it's supposed to illustrate.

### Tell no one (`tell-no-one`)

- **Formats:** single
- **Archetype:** `frame-phrase`
- **Triggers:** T2
- **Source:** aop-101 #21

**Template**
```
<Do the specific thing>. Tell no one until <the milestone>.
```

**Transposition.** confidence from specificity, not absolutes: the 'secret' is discipline, never confidentiality about your own product or customers

**Example.** Build the audit trail before anyone asks for one. Tell no one until the first customer question it answers in five minutes instead of five days.

**Guardrail.** Never implies real confidentiality is being broken or kept about the product, a customer, or a security matter. This is a framing device for quiet effort, not a claim about withheld information.

### Things that do, things that don't (`things-that-do-dont`)

- **Formats:** single, thread
- **Archetype:** `before-after`
- **Triggers:** T3
- **Source:** aop-101 #40

**Template**
```
Things that reduce <outcome>:
- <item>

Things that don't:
- <item>
```

**Transposition.** confidence from specificity, not absolutes: both lists must be genuinely evidenced, not a setup-and-punchline where the second list is a strawman

**Example.** Things that reduce incident recurrence:
- A named owner per fix, not per ticket

Things that don't:
- A longer postmortem template

**Guardrail.** Both lists need real evidence behind every item. Never load the second list with a competitor's actual approach dressed up as an obvious failure.

### Unpopular opinion (`unpopular-opinion`)

- **Formats:** single
- **Archetype:** `status-quo-fault-line`
- **Triggers:** T1
- **Source:** aop-101 #45

**Template**
```
Unpopular opinion: <the specific, defensible contrarian claim>.
```

**Transposition.** confidence from specificity, not absolutes: the opinion must be genuinely arguable with evidence, not manufactured for reaction

**Example.** Unpopular opinion: a shorter vendor security questionnaire, reviewed properly, catches more real risk than a hundred-question form nobody reads twice.

**Guardrail.** The opinion must be one you'd defend with a real example if challenged in the replies. If it's contrarian only because it's phrased that way, it isn't this pattern.

### Write to someone (`write-to-someone`)

- **Formats:** single
- **Archetype:** `the-concession`
- **Triggers:** T1
- **Source:** aop-101 #71

**Template**
```
For the <specific role/persona> about to <the specific situation>: <one piece of advice>.
```

**Transposition.** confidence from specificity, not absolutes: name a real persona type, not a vague 'you'

**Example.** For the platform engineer about to ship their first production agent: log the calling identity before you log anything else. Everything downstream depends on that one field existing.

**Guardrail.** The addressee must be a real, specific role or situation (a named persona from the profile's ICP, not a generic 'to whoever needs this'). Vague framing defeats the pattern.
