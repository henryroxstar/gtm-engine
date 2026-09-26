# Prospecting

<p align="center">
  <img src="docs/assets/prospecting-lifecycle.svg" width="820" alt="The prospecting loop: your profile, find and score companies, research why now, draft from your fact sheet, then an amber gate where you approve the list; staged paused in your sending tool; a second amber gate where you press start; replies sorted into interested, not now, away and stop; measured per batch; keep what works and retire what does not, which feeds the next batch" />
</p>

Tell it who you sell to. It finds the companies that fit, the right person at each one, and
something that happened there that makes now the time to write. It drafts the email and, once you
approve the list, stages it **paused** in your email sequencer. You read it. You press start.

*This page is for humans: what the agent is doing, and which calls are yours. The agent never
reads it — it follows the [`prospect` skill](plugin/skills/prospect/SKILL.md).*

---

## What you get

One email per person, built from labelled parts. This one is made up, and so is the company:

> **Subject:** Marlowe's new Rotterdam site
>
> Morgan — I saw Marlowe Systems opened a second warehouse in Rotterdam. ‹signal›
>
> Teams at that stage usually find stock counts drifting between sites before anyone notices. ‹pain›
> We keep counts in step across sites ‹claim›, and in most setups the wiring takes about a week. ‹hedge›
>
> Would it be helpful if I mapped your two sites against how a distributor your size cut its
> monthly recount time by a third? ‹proof + offer›

The first email never asks for a call. It offers something useful, anchored on real proof. If they
say yes, the next touch delivers it — a one-pager or a short recorded demo your profile can
actually produce.

Only the signal is researched. Everything else comes from a fact sheet you own, so research can
misjudge a company but cannot invent something about your product.

You also get one status report that says whose move it is. Illustrative numbers:

```text
As of 25 Sep 2026, 09:12.
Today: 6 of the 9 people on the list can go out.
Yours (3): decide on 3 contacts — review sheet: <link>
The machine's: finding a contact for 2 companies, researching 1 company.
In the sending tool: 0. Nothing sends until you start a sequence there.
```

## Getting set up

1. **Your company profile.** Say *"set me up"* and point it at your website. It drafts your
   profile, then you check the few files that decide who qualifies. ([§0](#0-before-your-first-run))
2. **A data enrichment tool** — RocketReach or Apollo — for verified emails.
   Without one it still finds, scores and drafts, but no address is verified, so nothing is
   sendable.
3. **An email sequencer** — Saleshandy, Apollo or GMass — where sequences are staged paused. This
   page calls it your *sending tool*.

Setup also records a monthly budget cap. Every paid call is checked against it first.

## How it is different

| | AI SDR platforms | Sequencer + enrichment, done by hand | **This** |
|---|---|---|---|
| **Context** | A scrape of your site | Re-typed into every template | A profile you onboard once: your ICP, your ideal customer profile — the kind of company you sell to best — plus personas, voice, competitors, case studies, and a fact sheet of claims and proof |
| **Craft** | Merge fields on a template | Your own writing time | One researched reason to write per account, a pain matched to the reader's role, and an offer anchored on your proof |
| **Checks** | The vendor's, unseen | Your proofreading | Every draft is checked before you see it: disputed figures and roadmap claims are refused, AI-sounding prose and your banned phrases are caught, and stale news cannot open an email |
| **Safety** | Often sends on its own | Safe, but slow | It cannot send. Sequences are staged paused and a person presses start |
| **Control** | Settings in someone else's app | Full, and all manual | Every judgement call is yours ([§5](#5-the-decisions-only-you-can-make)), and silence never sends |
| **Learning** | Opaque | A spreadsheet, if anyone keeps one | Angles are kept or retired on your replies, never on a lucky small sample |
| **Cost** | Per-seat or per-contact subscription | Several subscriptions and your hours | Your AI plan plus the data tools you choose, with a hard budget cap |
| **Channels** | Often email plus LinkedIn | Whatever you wire up | **Email only.** No LinkedIn, phone or SMS steps |

## Not live yet

Everything above is live. These are the gaps:

- **Opt-outs.** On a server install, a clear *"stop"* is added to your do-not-contact list
  automatically, but no automatic add has been seen live yet. Unclear ones wait for your approval.
  Until then, say *"add [person] to the do-not-contact ledger, permanently"* yourself.
- **Batch tripwires** run before every batch but can still be skipped. Stopping on bounces or spam
  complaints is planned.
- **The automated reader** only ranks drafts. Calibrating it against your own labels is planned.
- **Measurement per angle** is partial. Opt-outs per angle, cost per reply and a random holdout
  are planned.
- **Experiments** — trying a different ICP for one run — exist, but runs do not use them yet.

## Start here

You don't need to type a command. Say the sentence; the command is shown so you can see what ran.
No number on this page is copied from your data — ask, and you get today's.

| What you want | Just say | What that runs |
|---|---|---|
| **Set up (once)** | *"set me up"* | the `setup` skill |
| **Start a run** | *"run my prospecting"* — add a market, a segment, or a number to scope it | the `prospect` skill |
| **See where you are** | *"where do I stand right now?"* | `uv run python -m gtm_core.prospects status --profile <you>` |
| **Decide the parked rows** | *"show me the accounts waiting on a decision from me"* | builds the review sheet — see [§5](#5-the-decisions-only-you-can-make) |

Everything else you can ask is in [§10](#10-everything-you-can-ask).

**Before you start:** [§0 Before your first run](#0-before-your-first-run) ·
[§1 What it will never do](#1-what-it-will-never-do) · [§2 The words](#2-the-words) ·
[§3 Where everything is](#3-where-everything-is)

**Running it:** [§4 The week](#4-the-week) · [§5 Your decisions](#5-the-decisions-only-you-can-make) ·
[§6 After you press start](#6-after-you-press-start) · [§7 How it gets better](#7-how-it-gets-better)

**Reference:** [§8 Reading the numbers](#8-reading-the-numbers) ·
[§9 The quality bar](#9-the-quality-bar--for-whoever-reads-the-drafts) ·
[§10 Everything you can ask](#10-everything-you-can-ask) · [§11 FAQ](#11-frequently-asked-questions-faq)

---

## 0. Before your first run

Installing the engine is in the README's [Getting started](README.md#getting-started-chat-mode);
which data tool does what is in [Tools & keys](README.md#tools--keys).

**Fill in the files prospecting reads.** Onboarding scaffolds them as **templates**, and a run will
happily produce finished-looking output from a template. Three ship **inert or fictional** and fail
quietly: `premise-vocab.toml`, `competitors.toml` and a stub `icp-personas.md`. Check those by hand
before a first paid run.

<details>
<summary><b>Every file a run reads — and what goes wrong if it is still a template</b></summary>

| File in `profiles/<you>/knowledge/` | What prospecting uses it for | If it is still a template |
|---|---|---|
| `icp-personas.md` | **The gates and the scoring rubric.** The most important file — it decides who qualifies and what they score. | Nothing is really being scored. Every account looks equally good. |
| `competitors.toml` | What an account *is to you* — prospect, competitor, partner, regulator. | Ships with fictional companies, so a real competitor can be pitched. |
| `premise-vocab.toml` | The "can an argument rest on this fact?" check. | **Ships deliberately inert** — the check is off. |
| `case-studies.md` | The proof story matched to each account. | Drafts cite nothing, or the placeholder. |
| `claims.toml` | What an email may say your product does, each with a status — see §9. | Every example ships as roadmap, so nothing can be stated as fact. |
| `proof.toml` | The numbers and references an email may quote, by the reader's country. | The examples are shapes, not measurements. |
| `angles.toml` | Who you write to, what you argue, and how you open. | Ships empty — there is no angle for any row. |
| `role-vocabulary.toml` | What each job title struggles with, and the tone to take. | Optional; a built-in business-software vocabulary is used. |
| `hook-matrix.md` | The opening ideas as a grid, **generated from `angles.toml`** — edit that instead. | Openers get free-written, so a run sounds the same. |
| `voice.md` | Every line in a Tier-A pack is checked against it. | Copy reads like the model, not like you. |
| `market-scan-config.md` | Which buyer-intent topics count as heat. | Intent never fires, so nothing is timed. |

</details>

**Check you are ready.** Both checks are free. First, *"check my environment"* — which keys are set,
and what each unlocks:

```bash
uv run python -m gtm_core.check_env
```

Then *"check my prospecting connectors before we spend anything"*. It tests each data tool live and
**stops the run** rather than spending on one that cannot finish. Every run does this anyway.

---

## 1. What it will never do

- **Send.** A run produces drafts and lists. The sequencer step leaves sequences **PAUSED**, and
  the tool it talks to has no start button to give it. A person starts every sequence.
- **Reply for you.** It drafts replies for you to send.
- **Load contacts on a schedule.** Scheduled runs only find accounts, refresh signals and score.
- **Buy anything.** If credits run low it tells you and stops.

---

## 2. The words

### Four things act on your list. Only one of them is you.

- **The router** sorts each person into the email they could get. Sorting is not permission.
- **The checks** read the account behind each row and **pass** or **refuse** it, with a reason.
- **The judge** ranks drafts and removes nobody.
- **You** start the sequence, every time.

So a sorted count is always larger than a passed count: sorted people have not been checked yet.

### The status word — one word, six values, always derived

Every row, and every account in the ledger, has one **status** that answers "whose move is it?" It
is never stored, only worked out fresh each time you ask, so it cannot go stale.

| Status | Whose move |
|---|---|
| **Waiting on you** | yours — one decision |
| **Sorted — not yet checked** | the checks, then yours |
| **Being reworked** | the engine's — nothing for you to do |
| **In the sending tool** | already loaded — do not load again |
| **Closed — not contacting** | closed |
| **Still finding the right person** | the engine's — it looks first, then asks you if it cannot find one |

Ask *"where do I stand right now?"*, or run it yourself:

```bash
uv run python -m gtm_core.prospects status --profile <you>
```

The report's lines are shown in [What you get](#what-you-get). Two rules make it trustworthy. If the
list changed since the checks last ran, *Today* says *unknown* rather than showing an old answer.
And if a count ever lost someone, the report says *unknown* rather than a smaller number.

Companies outside your target markets, and those not yet researched enough to score, are kept off
the send list automatically. The second kind rejoins once researched.

<details>
<summary><b>"For the record" — the detail under the report</b></summary>

It counts two populations on purpose. They never add up to each other, because one company can be
several people.

| Part | Counts | Lines |
|---|---|---|
| Companies | accounts, by the furthest any of their contacts got | *Not a fit / excluded*, *Being researched*, *Finding a contact*, *Not yet sorted*, *Held*, *Sorted*, *All accounts* |
| People | people on your current list, by status | the first five statuses above |
| Passed the checks | the same people, counted by a later step | *not run yet* or *out of date* when the checks have not seen this list |
| Address work | accounts | *Needs an address*, *Checking the address* |

</details>

### Scored or categorised

- **Scored** — it had everything the rubric needs, so it has a number, a tier, and a sentence
  naming what each axis cost it.
- **Categorised** — the rubric **refused to guess** and names what is missing: *no ICP label*,
  *no buyer-intent reading*, *no research on file*. That is not low priority. It is *unknown*, and
  the category is the next action.

The exception is **"Blocked — outside target markets"**: a decision already made, which research
cannot change.

<details>
<summary><b>The rest of the vocabulary — and what each word is not</b></summary>

| Word | What it means | Not to be confused with |
|---|---|---|
| **Account** | A company. Kept permanently in the account ledger, rejected ones included, so it is never re-decided. | A contact — one account can be several people. |
| **Contact / row** | A person at that account. Lists are rows; the ledger is accounts. | An account. |
| **Tier (A, B, C … / unscored)** | A **score band** from your rubric. Tier-A earns a hand-built 1:1 pack. Tier-B and Tier-C go into the shared sequence, and C gets the general email like any other fit. | Permission. Tier is effort. "Only two Tier-A" is not a bad run. `unscored` is not a band; see Scored or categorised. |
| **Heat (0–3)** | *Timing*: points when a buyer-intent feed shows the account researching your category now. | Copy. Heat picks the week; it never appears in the email. |
| **Verdict** (`send` / `re-angle` / `drop`) | **The researcher's** conclusion about one row. `re-angle` = real account, wrong story. `drop` = competitor, dead, or wrong entity. Never overwritten by a machine. | Unusable. A `re-angle` row can still get a generic email. |
| **Judge verdict** | The automated reader's column. It **ranks and never removes** until calibrated. | The researcher's verdict. |
| **Lane** | Which *kind of email* a row can carry: `personalised`, `repair` (needs a re-write), `generic` (claims nothing about the recipient), `hold` (waiting on you), `excluded`. | A rejection. A lane is work queued. |
| **Needs verification** | An address we are not confident in. Never loaded blind. | A bad address. |
| **Suppressed** | On the durable do-not-contact ledger. Survives every rebuild. | A held row. |
| **Wave** | One batch of sends, measured as a unit. | A run. |
| **Overlay** | A named experiment that swaps your targeting files for **one run only**; its results stay labelled. | Your live settings. |

</details>

---

## 3. Where everything is

Ask *"where do my prospecting files live?"*, or run it yourself:

```bash
uv run python -m gtm_core.prospects paths --profile <you>
```

- **The account ledger is the record. The CSVs are views.** A hand edit to a pooled CSV is lost on
  the next build. Fix facts on the account.
- **Load only the two dated files** — one personalised, one generic. Everything else sits in a
  hidden pool folder.
- **Nothing is cleaned up automatically.** Archiving happens only when you ask, after a plan, and
  never while a campaign is unfinished.

---

## 4. The week

- **Monday:** say *"run my prospecting"*. It checks the data tools and the funnel first, and stops
  rather than spend on a run that cannot land your number. A number means **delivered contacts**,
  not companies found. Say so if you meant companies.
- **Mid-week:** answer the decisions in [§5](#5-the-decisions-only-you-can-make).
- **End of week:** the last batch's replies must be on file before the next is staged. Ask *"has
  the last wave been measured yet?"*

---

## 5. The decisions only you can make

If you do not answer, work stops and the rows stay held.

1. **Generic or hold.** Accounts that fit but have no usable signal: send the generic email, or
   hold for research? A cap limits how much of a run may be generic.
2. **Research spend.** The only variable-cost step, so it always asks.
3. **Credit top-ups.** It warns you. It never buys.
4. **Held rows.** Competitor-adjacent, partner, prior-contact, negative-reply and strategic
   accounts wait in a review sheet. Choose **suppress**, **generic** or **salvage**. A blank keeps
   the row held.
5. **Go-live.** You start the paused sequence.
6. **A weak criterion.** It flags ICP phrases that match nothing or everything and suggests edits.
   It never edits the file and never stops the run over it.

**Not yours: the data tool's download buttons.** When a panel offers **"Download N rows"** or
**"Export N rows"**, click neither. Download uses up the one-time link the engine needs. Export
spends credits outside your budget check. For your own copy, say *"give me these accounts as a
spreadsheet"*.

---

## 6. After you press start

![Every kind of reply has one destination. An email is sent; a reply check runs every few hours and fans out to six rows — interested, pricing question, not now, out of office, stop, meeting booked — each showing what the machine does and what is yours. Amber marks the three things only a person does: send the drafted reply, answer a pricing question, approve a do-not-contact copy. Dashed boxes are planned](docs/assets/prospecting-replies.svg)

Ask *"who replied?"* and it sorts each reply and drafts where a draft helps. On a server install a
scheduled check does this every few hours. **Anything that goes back to the prospect goes through
you.**

**● works today · ◐ partly · ○ planned.**

| When they… | It… | You… | |
|---|---|---|---|
| **say yes, or ask to talk** | drafts a reply, with your booking link if you have one | edit it and send it | ● |
| **ask about price** | alerts you and drafts nothing | answer it | ● |
| **say not now** | keeps the account off every new list | nothing | ● · a revisit date ○ |
| **are out of office** | leaves it to your sequencer's auto-pause | switch that setting on, once | ◐ |
| **say stop** | adds a clear "stop" to your do-not-contact lists; alerts you to unclear ones | approve the unclear ones | ◐ not yet seen live |
| **book a meeting** | logs it against the account | nothing | ◐ when you *"sync outcomes"* |

---

## 7. How it gets better

![The learning loop. A batch goes out; replies are counted once at least twenty are sent; the batch is measured before the next one can go; results are read per angle; each angle is kept, watched or retired, and you promote or retire it before the next batch. A tripwire off the measurement step: opt-outs over the ceiling refuse the next batch, and you pause the running sequence in your sending tool, because the system cannot. A side column sharpens the aim: an ICP check you act on, a staged knowledge refresh, and three planned pieces — one-run experiments, cost per reply and per meeting, and a random holdout](docs/assets/prospecting-learning-loop.svg)

**Nothing scales until it is measured.** A new batch waits for the last one's replies. A batch
needs at least 20 sent before its reply rate is read; below that, one reply is noise.

Say *"sync outcomes"* to pull results and flag what is doing clearly better or worse. Then each
angle is yours to move:

- **Keep** — real replies and a verified claim. Promote it to live.
- **Watch** — too little data. Nothing is retired for bad luck on a small sample.
- **Retire** — stop it any time; it stays on file as a record.

Telling a twice-as-good angle from luck takes a few hundred sends each, so early on most answers are
*watch*.

### Tripwires

| If… | Then… | |
|---|---|---|
| the last batch has not been measured | the next one is refused | ◐ |
| opt-outs pass 5% on a batch of 30 or more, or more than 3 on a smaller one | the next batch is refused unless you say *"I know — go ahead"* | ◐ |
| the opt-out count does not add up | the next batch is refused | ◐ |
| bounces or spam complaints climb | your sequencer's health numbers stop the next batch | ○ |

It cannot pause a running sequence, because it cannot start one either. When a tripwire fires, pause
it in your sequencer.

---

## 8. Reading the numbers

- **"How many can I send" is the *Today* line.** Not a CSV row count, not yesterday's figure.
- **A per-cent needs its denominator.** "Most rows fail" means nothing until you know which rows.
- **The score column qualifies; it does not rank spend.** An impossibly high score is probably on
  another scale.

---

## 9. The quality bar — for whoever reads the drafts

### What an email may say about you

**About them** — researched fresh each run: who the person is, whether the company fits, and the
*signal* that makes now the time. Research can be wrong; the second half of this section is how to
catch it.

**About you** — your fact sheet, the same for every account. Research never writes to it.

![An email card with five parts — signal, claim, pain, hedge, proof. The signal is fed by research about their company, done fresh this run. The other four are fed by your fact sheet — claims, role vocabulary, voice rules and proof — and a dashed divider marks that research never crosses into it. One angle picks the set; a figure you have marked disputed gets the draft refused](docs/assets/prospecting-email-anatomy.svg)

| Part | What it is | Where it comes from |
|---|---|---|
| **Signal** | why we are writing now | this account's research |
| **Claim** | what the product does | `claims.toml` |
| **Pain** | what this role usually struggles with | `role-vocabulary.toml` |
| **Hedge** | the honest qualifier | `voice-rules.toml` |
| **Proof** | the figure or reference the offer rests on | `proof.toml`, by the reader's country — some deliberately get none |

The writer picks one **angle** from `angles.toml`, and the angle names the rest. No fitting angle
means the row is refused, not free-written.

| Claim status | May an email state it as fact? |
|---|---|
| `verified` — you hold the evidence, and the entry names the file and line | yes |
| `conditional` — true only under a condition | only with the condition said |
| `design-target` — roadmap | never |

For figures, `measured` may be quoted, `illustrative` may not, and `disputed` gets any draft using
it refused. Retract a figure once and every draft leaning on it is refused from then on. A draft
with no angle is only partly checked, and says so with the warning `angle-missing`.

Angles move `draft` → `live` → `retired` by command, never by file edit. Promoting needs sent and
reply counts and a `verified` claim. After editing `claims.toml` or `proof.toml` by hand, ask
*"check my messaging registry"*.

### Whether the researched fact is good enough

**Can it ship?** The opening is a plain sentence: no numbers, no date stamp, no URL, and faithful to
the source. A funding round is never the opener; look for what the money *bought*.

**Can it carry an argument?** "They built an agent" says who to talk to, not what to argue.

**Reject a draft when:**
- It claims something plural when the evidence shows it once.
- The fact is about the parent, investor or acquirer, not the recipient.
- "Agent" in the source means people, such as insurance agents, but the email treats it as AI.
- A roadmap capability is described as shipped.

---

## 10. Everything you can ask

If a phrase does not land, say what you want and add *"— which command does that?"*.

| What you want | Just say | What that runs |
|---|---|---|
| **Where are we?** | *"where do I stand right now?"* | `uv run python -m gtm_core.prospects status --profile <you>` |
| **The full status page** | *"refresh the prospecting status page"* | `uv run python -m gtm_core.email_campaign_dashboard --profile <you> --scope open` |
| **How many can I send today?** | *"how many can I send today?"* | reads the *Today* line — never a hand count |
| **Refresh the checks** | *"run the prospecting checks again"* | `uv run python -m gtm_core.preflight_report --profile <you> --warn-only` |
| **Which file is which?** | *"where do my prospecting files live?"* | `uv run python -m gtm_core.prospects paths --profile <you>` |
| **Is this list worth working?** | *"check whether this list is worth working before I spend on it"* | `uv run python -m gtm_core.prospects list-fit` |
| **Are the data tools live?** | *"check my prospecting connectors before we spend anything"* | `uv run python -m gtm_core.prospects preflight` |
| **What have I spent?** | *"what have I spent this month?"* | `uv run python -m gtm_core.budget_status --profile <you>` |
| **Last run's summary** | *"show me the last run summary"* | `uv run python -m gtm_core.run_summary --profile <you>` |
| **Critique my ICP** | *"check my ICP definition"* | `uv run python -m gtm_core.prospects icp check --profile <you>` |
| **Suggest ICP changes** | *"suggest changes to my ICP"* | `uv run python -m gtm_core.prospects icp propose --profile <you>` |
| **Why is this row blocked?** | *"why is [company] blocked from the send list?"* | `uv run python -m gtm_core.prospects integrity` |
| **What is sitting unworked?** | *"what's in the backlog that nobody has touched?"* | `uv run python -m gtm_core.prospects backlog` |
| **Can we start the next batch?** | *"has the last wave been measured yet?"* | `uv run python -m gtm_core.prospects wave-gate check --profile <you>` |
| **Pull results** | *"sync outcomes"* | the `outcomes-sync` skill |
| **Check the fact sheet** | *"check my messaging registry"* | `uv run python -m gtm_core.messaging check --profile <you>` |
| **See the angles as a grid** | *"regenerate my hook matrix"* | `uv run python -m gtm_core.messaging matrix --profile <you>` |
| **Find unused angles** | *"which angles is nothing using?"* | `uv run python -m gtm_core.messaging unused --profile <you>` |
| **Promote an angle** | *"promote angle X — here are its numbers"* | `uv run python -m gtm_core.messaging angle promote --id X --evidence 'sent=<n> replies=<n>'` |
| **Retire an angle** | *"retire angle X"* | `uv run python -m gtm_core.messaging angle retire --id X` |
| **See staged knowledge** | *"show me what's staged"* | `uv run python -m gtm_core.knowledge_staging list --profile <you>` |
| **Never contact this person** | *"add [person] to the do-not-contact ledger, permanently"* | `uv run python -m gtm_core.prospects suppression` — the durable ledger, never a CSV edit |
| **The account list looks wrong** | *"the prospect ledger looks wrong — restore the last good snapshot"* | `uv run python -m gtm_core.prospects state restore --profile <you>` |
| **The run stopped mid-way** | *"resume my prospecting run"* | `uv run python -m gtm_core.run_state --profile <you> resume-from` |
| **Is a run active?** | *"check if prospecting is locked"* | `uv run python -m gtm_core.run_lock --profile <you> status` |
| **Stuck run lock** | *"break the prospecting run lock"* | `uv run python -m gtm_core.run_lock --profile <you> break --force` |
| **Anything else** | *"what prospecting commands are there?"* | `uv run python -m gtm_core.prospects` |

A refused tool call or check is **by design**. Fix the cause; do not route around it.

---

## 11. Frequently asked questions (FAQ)

**If a run stops mid-way, do I lose credits?** No. Say *"resume my prospecting run"*; it picks up
without paying again for accounts it already verified.

**Can two people run it at once?** No. One run lock per profile stops double-spending. If a crash
left a stale lock, say *"break the prospecting run lock"*.

**How old can the news in an email be?** A signal older than 210 days is stale, and the account goes
back for a fresh reason to write.

---

## 12. What this page does not own

| For | Read |
|---|---|
| The procedure itself, step by step | [`plugin/skills/prospect/SKILL.md`](plugin/skills/prospect/SKILL.md) |
| Scoring rubric, gates, tiers for **your** company | your profile's `knowledge/icp-personas.md` |
| Discovery filters, credits, the budget model | [`references/discovery-and-budget.md`](plugin/skills/prospect/references/discovery-and-budget.md) |
| The export column map | [`references/hubspot-csv-map.md`](plugin/skills/prospect/references/hubspot-csv-map.md) — generated from the code |
| Every skill in the system | [`docs/SKILLS.md`](docs/SKILLS.md) |
| Security and tenant rules | [`CLAUDE.md`](CLAUDE.md) |
