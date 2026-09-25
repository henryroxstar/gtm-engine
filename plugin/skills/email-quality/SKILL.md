---
name: email-quality
description: >-
  Evaluate and score outbound email drafts against voice, spam triggers, length constraints,
  and relevance rubrics. Trigger when the user says "audit email quality", "score outbound
  drafts", "check cold email copy", "review outreach before sending", or as QA gate in
  prospecting.
metadata:
  version: "0.3.0"
  phase: "1"
  capability_tier: pipeline
---
# Email quality — the loop that makes the next batch better than this one

This skill exists because the measurement stopped short of changing anything. The harness was
built, the statistics were written and unit-tested, the labeling sheet rendered — and after an
eval finished, **nothing happened**. No gate changed, no rule retired, no prospect row was
corrected. An operator could correctly identify five accounts that should never have been
contacted, and the next generated CSV contained all five again.

Four modes, one loop:

| mode | question it answers | writes |
|---|---|---|
| `judge` | which of these 400 emails would I not send? | a verdict per row |
| `sheet` | what does the operator actually think? | a blind sheet + a sealed holdout |
| `apply` | what changes in the next run because of that? | prospect state + the suppression ledger |
| `report` | is the judge worth its cost, and which rules are? | a validation score + a rule verdict |

**Two boundaries hold in every mode, and neither is negotiable.**

The judge **ranks; it never blocks.** It writes a `verdict` *data column*. The deterministic
`account_integrity --require-verdict send` is what refuses a row at enrollment. This is the same
division of labour as the publish gate — a model produces the bytes, deterministic code decides
whether they move — and it is the reason a language model's opinion can be wrong without being
dangerous.

**Nothing here touches the send path.** Sequences stay PAUSED. Activation stays the operator's, in
the sequencer's own UI. No mode of this skill calls a resume, activate, or send tool, and none exists
to call.

---

## Step 0 — Interpreter preflight. Run this before any mode, and STOP if it fails.

```bash
uv run python -m gtm_core.paths || python -m gtm_core.paths
```

One of the two prints the resolved content root and exits 0. Free, reads nothing metered, writes
nothing — its only job is to prove a real Python interpreter exists **and** that `gtm_core` imports.

**Whichever form worked is the prefix for every `gtm_core` command below.** The working prefix is
discovered, never assumed, because the two runtimes disagree: the operator's laptop carries a
`modern-python` shim that refuses a bare `python`, and the `gtm-agent` container has no `uv` on
PATH at all. A skill that hardcodes either form fails in one of them.

If **both** fail — or either prints a Windows App-Execution-Alias message (`Python was not found;
run without arguments to install from the Microsoft Store`) — **stop and tell the operator to fix
their interpreter first.** Do not proceed. Do not hand-assemble the numbers a mode would have
computed.

**Read the two boundaries above again, because a dead interpreter inverts both.** "The judge ranks;
it never blocks" is only true while `account_integrity --require-verdict send` can run. It is a
Python CLI. With no interpreter the deterministic half of that division of labour is simply absent,
and a language model's opinion becomes the only verdict in the system — which is the precise
arrangement both boundaries exist to prevent. The danger is not that a mode fails loudly; it is that
it fails quietly and the loop still appears to close.

| Mode | CLI | What goes dark |
|---|---|---|
| `judge` | `gtm_core.prospects integrity` / `lanes`, `gtm_core.adjudication sample` / `check-complete` / `repair-queue` / `disposal` | the deterministic gate the verdict column feeds; the repair cap is enforced by the CLI, not by the model, so with it gone nothing bounds re-composition |
| `sheet` | `gtm_core.build_eval_sheet`, `gtm_core.eval_calibration seal-holdout` / `verify-holdout` | **the holdout is never sealed.** Every later score is then computed on rows the judge helped shape — the circularity the seal exists to break |
| `apply` | `gtm_core.eval_writeback plan` / `apply`, `gtm_core.prospects suppression` | the labels change nothing. This is the exact failure this skill was built to end: an operator identifies five accounts that should never have been contacted, and the next CSV contains all five again |
| `report` | `gtm_core.eval_calibration score` / `rules` / `reconcile`, `gtm_core.rule_baseline` | no validation score and no rule verdict — every rule keeps its status by default, including the ones firing on nothing |

A hand-computed rate is not a substitute for any of these. Recompute deterministically or report that
the mode could not run — never re-derive a number by reading rows and counting.

**Fix on the operator's machine** (Windows is where this bites — `winget install Python…` alone often
does not fix it, because the WindowsApps alias still shadows the real interpreter on PATH):

```bash
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

then, in a **new** shell from the repo root, `uv sync` and re-run the probe as `uv run python -m
gtm_core.paths`. `uv` provisions its own Python 3.11+, so the alias never gets a vote.
`scripts/bootstrap.ps1` does all of this in one step. Everywhere else: `bash scripts/bootstrap.sh`.

---

## Mode: `judge` — score every row, before enrollment

Runs **between the two operator gates: after the outreach copy review, before sequence
staging** — the prospecting pack's node order (`outreach → quality → sequence`). The
deterministic gates live in the `sequence` stage and run *after* this skill, because they
consume what it writes: `--require-verdict send` reads the judge columns, so a verdict that
arrived after staging could only ever inform a restage. Every row, not a sample — per-row
coverage is the whole point, and Haiku pricing is what makes it affordable (a full-list pass
is single-digit dollars).

**The questions come from the quality card, and the card is one tuple: `gtm_core/messaging/card.py`.**
Three surfaces ask whether a rendered outbound email is any good — this skill's judge, the
operator's blind labeling sheet, and the label files — and until the card existed they carried
three different lists in two spellings with nothing comparing them. Now each surface declares a
**subset** of `card.CARD`: the judge's is `card.JUDGE_QUESTIONS`, the generic lane's is
`card.SEAT_ONLY_QUESTIONS`, the sheet's is `card.SHEET_QUESTIONS`. A surface may ask fewer
questions than the card holds; it may never ask one the card does not hold, and a card question no
surface asks is an orphan, not a subset — both are tested.

**Read the card rather than this file for the question set**, and read `card.QUESTIONS` for what
each one asks. Do not quote a question count anywhere: the card is derived from the canonical
defect classes, so its size moves when a class is added, and a number typed here would be wrong on
that commit (§R14). Two absences are deliberate and worth knowing:

* **`claim_within_status` is NOT on the judge's subset.** The judge reads the rendered body and the
  recipient context, never the fact registry, so it could only guess at a claim's status — and the
  deterministic `claim-status` linter rule is authoritative there. A soft opinion sitting beside a
  hard gate is how a gate gets argued with.
* **`wrong_entity_type` is not either**: it is an account judgment with durable consequences, and
  the operator surface owns it.

Every record carries `card.fingerprint()` of the items it was actually scored against, so a verdict
produced before a card change is **not** pooled with one produced after: the fingerprint is derived
from the keys *and their wording*, so a reworded item starts a new population by itself rather than
waiting for someone to bump a version constant.

**Read [`references/judge-rubric.md`](references/judge-rubric.md) before reading any verdict** for
the framing the card does not carry: the deliberate asymmetry (the judge is asked to **find reasons
not to send**, because a plausible email wasting a real person's attention is the expensive failure
and a false alarm costs one re-read), why §R5 holds on a rendered body carrying scraped prospect
text, and the **revision counter** behind the 3-per-campaign cap below, maintained by hand — check
it before proposing a revision, and log any revision there or the cap stops counting anything. That
file is documentation; where it and `card.py` disagree, the module is what ships.

```bash
# 1. Score. Writes one adjudication record per row.
uv run python -m gtm_core.adjudication sample --csv <list.csv> -n 0 >/dev/null  # optional: see strata
```

The scoring itself is an **MCP tool call**, not a shell command — the judge is
`agent/mcp/judge`, a downstream worker, because a per-row model call from a Python module
would be a new egress path (§R6):

```
judge.score_emails(
  spec_path="content/<active>/prospects/sequences/<spec>.md",
  csv_path="content/<active>/prospects/sequences/ready-to-load.csv",
  out_path="content/<active>/prospects/evals/adjudication-<date>.jsonl",
  profile="<active>")
```

**You do not choose the transport; it chooses itself, and records which it used.**

| | `api` | `sdk` |
|---|---|---|
| when | `ANTHROPIC_API_KEY` is set | no key — uses the host's own auth (OAuth locally) |
| rows per prompt | 1 — independent | a few, to amortise subprocess cost |
| cost | ~$0.90 per 400 rows, metered to `costs.jsonl` | subscription, no API credit, no cost rows |

Production has a key (`deploy/docker-compose.yml` passes it), so the pipeline runs `api`. A local
session without one still works, on `sdk`.

**The two are not interchangeable, and the difference is recorded rather than assumed away.** An
SDK spawn carries the whole Claude Code system prompt, so it only makes sense batched — and a judge
that sees five emails at once anchors across them, which weakens the per-row independence the
confusion matrix and Cohen's κ assume. Every record therefore carries `backend` and `judge_batch`
(1 = scored alone). **Do not seal a holdout scored across both transports** without looking at
those fields; it is a confound, and the whole point of recording them is that you can see it.

If the SDK path scores nothing, the reply will say so: the usual cause is an expired or revoked
Claude Code OAuth token, fixed by re-authenticating or by setting an API key.

**Then check it actually scored everything.** This is not optional ceremony:

```bash
uv run python -m gtm_core.adjudication check-complete \
  --csv content/<active>/prospects/sequences/ready-to-load.csv \
  --records content/<active>/prospects/evals/adjudication-<date>.jsonl
```

A batch that scored 380 of 400 rows prints exactly like one that scored 400 — the ranked list looks
normal, the verdict tally looks plausible, and twenty rows silently carry no opinion. Nothing else
in the pipeline counts records against rows. If this fails, **do not proceed**; re-run the judge for
the missing rows.

**Every record also carries a `grounding` column — read it before you read the scores.**

The judge runs a deterministic **groundedness** cascade (`gtm_core.groundedness`) on each row
*before* the model sees it, and records the result on the record. It costs nothing, calls no model,
and is the half of `[roles.judge]` that `gtm_core/models.toml` has always described. Values:

| `grounding` | means |
|---|---|
| `clean` | no cheap tier fired |
| `untraceable=97%,3x` | those figures appear **nowhere** in the profile's `case-studies.md`. A number absent from its source cannot be a citation of it — treat as fabricated until proven otherwise |
| `unhedged=2` | that many assertive claims about our own internals, stated without hedging |
| `research=N` | N blocking findings on the row's signal record (`gtm_core.signal_record`) |
| `tier1=no-corpus` | the profile has no `case-studies.md`, so the fabricated-number tier **did not run**. This is NOT "every number is fine" and NOT "every number is fabricated" — it is "not checked" |

It **ranks; it does not gate.** `account_integrity --require-verdict send` is still the only thing
that refuses a row. A high judge score on a row flagged `untraceable=` is the interesting case: the
model liked copy whose proof point has no source, which is exactly the defect a reading pass alone
kept missing.

**Then write the verdicts and let the deterministic gate use them:**

```bash
uv run python -m gtm_core.prospects adjudication write-verdicts \
  --csv <list.csv> --records <records.jsonl> --out <list-judged.csv> --profile <active>

uv run python -m gtm_core.prospects adjudication rank --records <records.jsonl> --top 30

uv run python -m gtm_core.prospects integrity --csv <list-judged.csv> --profile <active> \
  --require-verdict send --lane <the lane this list was routed into>
```

`write-verdicts` refuses to run on a partially-judged list rather than writing what it has — a
half-judged list written as a judged one is worse than an unjudged one, because it reports
confidence it does not have.

**It writes `judge_verdict`, never `verdict`.** `verdict` belongs to the researcher — the
`prospect` skill writes it once, from evidence. Until 2026-08-27 this command wrote into that
same column, so a judge `send` silently erased a research `re-angle` or `drop` and the enrollment
gate then admitted the row: "the judge ranks and never blocks" was true in three documents and
false of the artifact. Pass `--profile` so the run also stamps `judge_calibrated` from this
profile's NEWEST sealed holdout — `true` only when `eval_calibration score` recorded a PASS against
it (`<holdout>-score.json`, bound to the holdout's bytes); **without a recorded pass the judge's
`drop` is advisory and the gate keeps the row**, reporting it. That is the whole point — an unmeasured judge has not earned a veto.
Multiple touches on one row collapse to the **worst** verdict, since a defect found on touch 3 is
a fact about the row and a clean touch 4 does not retract it.

### Account for the rejections before reporting a result (`disposal_audit`)

**A batch's `send` count is not its outcome.** The rows the judge rejected have to land
somewhere nameable — a repair queue, an enrollment, or an explicit suppression-ledger
retirement — and a summary written before they do reads as "nothing was salvageable" when the
truth is "nobody filed them".

```bash
uv run python -m gtm_core.adjudication disposal --records <records.jsonl>
```

It reports two things prose alone could not enforce:

* **stranded re-angles** — `re-angle` is defined as *the account is right and the argument is
  not*; those rows go back to research, never to the bin.
* **misfiled drops** — a `drop` carrying a **targeting** defect (`fact_earns_its_place`,
  `right_person`) is a re-angle wearing a terminal word. The judge does not reliably honour its
  own vocabulary: on 2026-08-27 every rejection came back `drop` while each note described a
  mis-aimed argument. Per [`PENDING.md`](PENDING.md) these route to the `prospect` skill to
  **re-target**, never to the copy gate — *"without this the loop polishes emails while the
  lever sits upstream."* (`fact_earns_its_place` merged three prior fields on 2026-09-02;
  `TARGETING_DEFECTS` in `gtm_core/adjudication.py` still honours the pre-merge
  `fact_creates_problem` string on records the judge wrote before that date.)

**An uncalibrated verdict ranks; it does not decide.** `score_emails` stamps every record with
`calibrated` and returns an `UNCALIBRATED` banner when no PASSING score record exists for the
profile — never measured, or measured and failed (the 2026-09-01 state: a holdout existed, the
judge FAILED it, and until 2026-09-03 that read as calibrated).
Until `report` mode clears the bar, do not retire an account on a judge verdict and do not
report rejected rows as "failed" — a bare rejection usually means the copy is wrong, and reading
it as "bad account" will disqualify most of a healthy list.

### Regenerating a spec from its defect report (the high-leverage repair)

Copy is per-spec: one body serves every row on its list, so fixing the ARGUMENT once fixes
50–150 emails. This is the repair that matters, and it runs in a session that never sees the old
body — regeneration from feedback, not paraphrase.

```bash
# 1. the feedback: per-source defect classes with evidence, novel vs covered classes, and the
#    operator's own guidance from the hold sheet (notes + salvage chips outrank the judge)
uv run python tests/linter/outreach_linter.py --list-rules content/<active>/prospects/evals/rules.txt
uv run python -m gtm_core.prospects adjudication defect-report \
  --records content/<active>/prospects/evals/judge/<records>.jsonl \
  --known-file content/<active>/prospects/evals/rules.txt \
  --feedback content/<active>/prospects/evals/operator-feedback.jsonl \
  --out content/<active>/prospects/evals/defect-report-$(date -u +%F).md

# 2. the cap lives in code: refuses the fourth regeneration of one spec lineage
uv run python -m gtm_core.prospects adjudication regen-count --spec <old spec>

# 3. ONE fresh one-shot session per spec (role brain_plan; never the DeepSeek worker — rows carry
#    PII). It receives ONLY: the report section for this spec, the fact registry (claims/proof/
#    angles + voice-rules), and the old spec's FRONT BLOCK — not the old body, not this transcript.
uv run python -m agent --profile <active> "Regenerate <old spec> as spec-<variant>-$(date -u +%F).md: \
  keep the front block's angle: unless the defect report says the premise or the claim is wrong, in \
  which case re-resolve it with gtm_core.messaging resolve and record the new id; argue against \
  these classes: <top classes>; never paraphrase a failing sentence; add \`regeneration: <n+1>\` and \
  \`regenerated_from: <old spec filename>\` to the front block."

# 4. gates, unchanged: linter (with --json so require-qa can find the record) → judge → compare
uv run python tests/linter/outreach_linter.py render <new spec> --csv <list.csv> --signoff "<name>" \
  --json content/<active>/prospects/evals/qa/<seq>-$(date -u +%F).json
#    (judge the new spec with score_emails, then read the verdict mix before/after — reported,
#     never gated; the reply rate by lane is the real check)

# 5. staging precondition — a QA record for the spec's CURRENT bytes, or it does not stage
uv run python -m gtm_core.prospects adjudication require-qa --spec <new spec> \
  --qa-dir content/<active>/prospects/evals/qa
```

Register the regenerated spec in `cells.toml` with its `lane`. Rows the judge rejected on the OLD
spec are in the repair lane already (`lanes route`); they take the new spec's argument, or a
different fact, or a different person — never a per-row body rewrite at sequence scale.

**Every claim in touch 1 must be traceable to a phrase in the evidence.** On 2026-09-04 two
regenerated bodies failed `fact_earns_its_place` for the same reason: each asserted an
architecture the fact does not contain ("acts inside someone else's systems", against a press
release that only says a product was introduced). What passed was the argument built from the
fact's own words — "for enterprise networking", "draws on member data and returns outcomes". A
product announcement supports an argument about the product **category**; it does not support a
deployment you inferred. A partner integration supports an argument about **that** data flow; it
does not support "any third-party agent".

**A row-level lever is not applied until the pool carries it.** The three levers — a different
fact, a different spec, a different person — change the account record (`prospects_state merge`,
merge-only), but `lanes route` reads `sequences/ready-to-load.csv`, a build output of
`prospects_consolidate` that does not re-read `latest.json` on its own. On 2026-09-04 a
re-resolved contact was correct in `latest.json` and absent from the pool the router routed, so
the repaired row matched no judge record at all. After any lever: re-run
`python -m gtm_core.prospects_consolidate consolidate --profile <active>`, confirm the pool row
carries the change, **then** re-judge and route. Never patch `ready-to-load.csv` by hand — the next
sweep discards it (`email-sequence`, *Enrollment hygiene gate*).

### The repair pass (per row, optional, capped at three, and the cap is not yours)

A `re-angle` verdict means the person is right and the argument is not. The repair pass
re-composes those rows using the judge's own `evidence` phrase, then re-judges.

```bash
uv run python -m gtm_core.adjudication repair-queue --records <records.jsonl> --out <queue.jsonl>
```

**`repair-queue` decides when the loop ends, not you.** It refuses any row already at three
attempts, and — importantly — refuses any row whose attempt count was never recorded rather than
assuming zero, because assuming zero is how a row already at the cap silently restarts.

For each eligible row: re-compose the body against the evidence, then re-judge with
`repair_attempt` incremented and `repaired=true`.

Three facts about the counter and the record, each learned once:

* **The cap is per (row, spec).** A new spec file starts a row at attempt 1 — by design, since "a
  different argument" is one of the three levers — so a row can legitimately see more than three
  judged bodies across two specs. Say so in the new spec's §1 when it happens, so the count stays
  visible.
* **`score_emails` overwrites `out_path`.** Two attempts written to one path keep only the last, and
  the before/after mix this procedure tells you to report is gone. One file per attempt:
  `evals/judge/<spec-stem>-attempt<N>.jsonl`. On 2026-09-04 an attempt-3 record was lost this way.
* **Never re-judge an already-passing body to refresh its metadata.** `grounding` is stamped on the
  record at score time from the row *as it was then*; if the row is corrected afterwards the stored
  string is stale, but the tier is deterministic (`gtm_core.signal_record.check_record`) —
  recompute it, do not re-run the model. A re-run on identical bytes costs an attempt, and with
  self-κ ≈ 0.39 it can flip a `send` to `re-angle` on nothing: on 2026-09-04 that produced two
  contested rows and a lost record, for the price of a field that was never in doubt.

**A repaired body must go back through the deterministic gates before it can enroll.** Repair
happens *after* the copy gates ran, so a re-composed body can reintroduce a rule violation that
already has a rule — and ship. Re-run the merge-render gate on the repaired list:

```bash
uv run python tests/linter/outreach_linter.py render <spec> --csv <repaired.csv> --signoff "<name>" \
  --json content/<active>/prospects/evals/qa/<seq>-repair-$(date -u +%F).json
```

Two things are true about this loop at once, and the second is why it is safe to keep: it is
**shaping copy to satisfy a rubric**, which is proxy-metric optimisation and can drift away from
what actually earns replies. The control is measurement, not prevention — repaired rows are tagged
`repaired: true`, **excluded from the validation holdout** (labelling copy the judge shaped and then
validating the judge on it is circular), and labelled as their own stratum so "is judge-shaped copy
better or worse than human-shaped copy" stays a number instead of an assumption.

**Do not enable repair before the judge has passed `report`.** A judge that cannot match the
operator on 30 known cases has no business rewriting 400 unknown ones. That gate governs the
per-row `repair-queue` only; rows the judge rejects are not stranded meanwhile — see the lanes
paragraph in judge mode.

**Verdicts route; they never remove.** After `write-verdicts`, route the pool:

```bash
uv run python -m gtm_core.prospects lanes route --profile <active> \
  --csv content/<active>/prospects/sequences/ready-to-load.csv \
  --records content/<active>/prospects/evals/judge/<records>.jsonl [--dry-run]
```

It reads the judge JSONL (never the CSV's stale `judge_*` columns) and sends every pooled row to
exactly one lane — personalised / repair / generic / hold / excluded — writing one CSV per lane
plus `evals/hold-<date>.csv` and its review sheet `hold-<date>.html`. A `drop` whose normalised
`judge_defect_class` is account-scoped HOLDS for the operator; argument- or contact-scoped
rejections go to repair; whatever the argument cannot carry falls to the generic seat email
instead of being stranded. Records older than 14 days are refused (`--allow-stale-records` to
override knowingly). Decide the hold sheet by group, download, then
`lanes hold-apply --profile <active> --decisions <file>` (plan) and `--apply` (write the
ledgers); the next route honours every recorded decision. A decision is honoured only when its
recorded `detail` matches the row's, so re-applying the same decision with a corrected detail is
recorded as a superseding entry (the plan shows it as `detail updated N`), and only an identical
re-apply is skipped as `already recorded`. `lanes suggest-rules` proposes a
`lane-policy.toml` line once a reason has ten unanimous generic/salvage decisions — it never
proposes suppress and never writes the file.

**`--records` takes several files, and the join is by email with the WORST verdict winning.** The
router has no notion of attempt order or of which spec a record came from: pass a row's attempt-1
`re-angle` beside its attempt-3 `send` and the row stays in repair; pass the generic-lane judge
sweeps beside the personalised ones and every generic row the judge disliked is pulled into repair
(416 of 592 on 2026-09-04, from 135). So route on a **superseding** set: the base sweep, plus one
file holding exactly the final record per repaired row — built by selecting the last attempt per
email — and **no** other file that names those emails. Generic-lane sweeps feed `defect-report`
only; they never go to `lanes route`. A verdict that changed on an unchanged `body_hash` is
*contested* and belongs in generic, never personalised; the router enforces that only against the
last `lanes-state.jsonl`, so a contest you produced in a file it never read is yours to apply by
hand — not to resolve in favour of the verdict you preferred.

---

## Mode: `sheet` — the operator's blind labeling session

```bash
uv run python -m gtm_core.build_eval_sheet --profile <active> --campaign <campaign> \
  --seed <campaign>-<YYYY-MM-DD> --duplicate 5 --include-drafts
```

**Round one is BLIND. Do not pass `--prefill`.** The judge's suggestions are available and using
them here would destroy the measurement: a labeler who sees a suggested answer keeps it, and
"keeping the suggestion" is indistinguishable from "agreeing with it". Validating the judge against
labels the judge anchored is self-agreement wearing a statistic's clothes. Prefill is unlocked only
*after* the judge clears `report`, and even then `seal_holdout` keeps prefilled rows out.

**This rule was already written here on 2026-08-21 and the round ran fully pre-filled anyway.**
Not by overriding it — by going around it. A bespoke `build.py` under `content/.../labeler-src/`
generated a nicer HTML sheet, and that script pre-filled all 30 rows, including marking every
injected row `send_it: N` so the plants announced themselves. `seal_holdout` then had nothing to
seal (`0 positive, 0 negative available`), so the round produced no holdout at all and only the 11
rows the operator edited carried evidence. **If a sheet did not come out of this CLI, the blind
rule is not in force — check which script built it before trusting any statistic downstream.**

**Always pass `--seed`, and change it every round.** Without one the sampler's stable hash draws
the same exemplars from each stratum forever, so round two re-reads round one's rows and the rest
of the list is never exercised. A seed of `<campaign>-<date>` gives a different draw each round
while keeping any single sheet reproducible — both properties are needed: reproducible so a sheet
can be rebuilt and two labelers compared, varying so coverage accumulates.

**Pass `--include-drafts`, and if the coverage block is still thin, go draft cells.** The sheet
builder reads `cells.toml`, which is the *staged sequence* join — so without this flag an eval can
only test copy already committed to a sequencer, which is backwards: the point of an eval is to
judge copy *before* it ships. `--include-drafts` adds every cell under
`content/<active>/prospects/evals/drafts/<cell-slug>/` (a `spec.md` + a `rows.csv`, the same two
files a staged sequence has, linted by the same gate). Drawn drafted cells print as `[DRAFT]` and
carry no outcomes — judge the writing, never read them as evidence about a live campaign.

**Do not register a drafted cell in `cells.toml` to make it samplable.** That file is the
outcome-attribution join; its own header warns that a wrong list-or-spec pairing silently credits
one seat's replies to another. A drafted cell has no replies to attribute, so registering it there
corrupts live learning data to make a sheet look wider.

**Drafting a cell is cheap and is the actual fix for thin coverage.** Ask which angles no spec has
claimed (`uv run python -m gtm_core.messaging unused --profile <active>`), filter the send-ready
rows to those that resolve to one of them, write those rows to `rows.csv`, compose one touch
against that angle, and lint to zero errors. A row `messaging resolve` refuses with
`premise-unsupported` is re-cut or dropped — never accommodated by softening the body. Coverage
went 2 cells → 9 this way on 2026-08-23, with no new research spend.

**Read the coverage block the command prints.** It reports how many hook-matrix cells the draw
spans, which axes collapsed, and warns below 4 distinct arguments. A sample can be perfectly
stratified across seat, signal and tier and still carry **one argument**, because copy is scoped
per list: seat varies who receives the email, not which argument they receive. The 2026-08-21
sheet was 30 rows over 4 seats and 2 segments — and **just 2 of the matrix's cells**. Findings
generalise to the cells actually drawn and no further.

The sheet withholds the injection flag and the linter verdict by construction. A labeler who can
see which rows are planted learns the plant, not the defect. It also **de-wraps** each body before
display: the spec's ~90-char authoring wrap does not survive staging (the sequencer stores one
unbroken line per paragraph), so showing it asks the labeler to judge something the recipient will
never see — which has already cost a label to a "formatting error" that existed only in the sheet.

Then seal:

```bash
uv run python -m gtm_core.eval_calibration validate --profile <active> --file <labels.jsonl>
uv run python -m gtm_core.eval_calibration seal-holdout --profile <active> \
  --file <labels.jsonl> --out-stem <stem>
```

**Verify the seal produced something real before trusting any number computed from it:**

```bash
uv run python -m gtm_core.eval_calibration verify-holdout --profile <active> \
  --holdout content/<active>/prospects/evals/<stem>-holdout.json
```

This refuses an **empty** holdout, a holdout containing **prefilled** rows, a holdout containing
**repaired** rows, and one where either class is empty. The empty check is the load-bearing one and
the least obvious: an empty holdout satisfies every other assertion vacuously, so a purity check
without it passes *hardest* exactly when the sealing step silently produced nothing.

---

## Mode: `apply` — the labels change the next run

```bash
uv run python -m gtm_core.eval_writeback plan --profile <active> \
  --labels <labels.jsonl> --internal <internal-<date>-<campaign>.jsonl>
```

`plan` is the default and writes nothing. **Read its output before applying**, specifically the
join line. `0 disqualified` and `the join matched nothing` print identically, and only the
`N labels joined to a row` count distinguishes them.

```bash
uv run python -m gtm_core.eval_writeback apply --profile <active> \
  --labels <labels.jsonl> --internal <internal-<date>-<campaign>.jsonl>
```

`apply` performs a **double write**, and both halves are load-bearing:

1. the **suppression ledger** — the durable half. Nothing in the send-list build filters on
   lifecycle `status`, so a `disqualified` row in `latest.json` would reappear in the very next
   generated CSV. The ledger is what `suppression verify` enforces against a rebuild.
2. **`latest.json` lifecycle status** — the visible half, for the dashboard and every human reader.

The suppression reason is `eval-disqualified`, which is deliberately **not** a provider-DNC reason.
A fit judgment is not a legal do-not-contact: the provider's Global DNC list is global, permanent,
and has no removal tool, so pushing "wrong buyer this quarter" to it forfeits the company forever.

**Three grains, and conflating them destroys good accounts.** `right_person: N` suppresses the
*address* and leaves the account alone — the company may be exactly right. `account_fit: N` is the
only label that retires the *account*. A bare `send_it: N` is **never** disqualifying: it usually
means the copy is wrong, which is the repair loop's job, and reading it as "bad account" would
disqualify most of a healthy list.

**`account_fit` exists because inferring it was tried and failed.** Until 2026-08-23 an account was
disqualified when three *other* sub-checks all read N. But all three are scoped to the FACT the
email opens on, so no combination of them can express a judgment about the company — and a weak
signal on a good account produces three honest Ns. A hospital system whose clause said it was
already centralising AI governance answered N to all three, and came one `apply` away from being
retired over a signal that merely undercut the pitch. The operator's ruling: *a bad email is not
evidence that the account is bad.* So it is now asked directly, and left blank it disqualifies
nothing — N is the only durable, expensive answer, and it must be authored, never derived.

Do not "improve" this by ORing the old trio back in as a fallback: that restores the exact false
positive. And do not key it on the row's recorded `category_relation` either — an adverse relation
is a blocking ERROR upstream, so a staged row structurally cannot carry one, and gating on it would
mean human review could never overturn a research error.

`apply` refuses above a 20% disqualification ceiling without `--force`. A writeback that broad is
far more likely a join defect than a genuinely terrible list, and the ceiling is what makes those
two distinguishable *before* the write rather than after.

**Then prove it survives a rebuild** — this is the step that closes R4:

```bash
uv run python -m gtm_core.prospects consolidate consolidate --profile <active>
uv run python -m gtm_core.prospects suppression verify \
  --ledger "$(uv run python -m gtm_core.prospects paths --profile <active> --name suppression.csv)" \
  --target <regenerated.csv>
```

The ledger path is asked for rather than spelled out. This line used to name
`prospects/suppression.csv` — one segment short of where every gate reads
(`prospects/sequences/.pool/`) — so a disqualification written by `apply` above landed in a
file nothing consulted, and this verify step checked an empty ledger and passed. Since
2026-08-27 the rebuild also re-derives the suppression columns from the ledger itself, so
this proves a property the build now maintains rather than one a human had to remember.

---

## Mode: `report` — is the judge worth its cost, and which rules are?

```bash
# The rule fleet, scored as a classifier on the SAME holdout — the comparison that makes
# "the judge catches defects" falsifiable.
uv run python -m gtm_core.rule_baseline --spec <spec> --csv <list.csv> \
  --signoff "<name>" --out <baseline.jsonl>

uv run python -m gtm_core.eval_calibration score --profile <active> \
  --holdout <stem>-holdout.json --predictions <records.jsonl> \
  --reversed-predictions <records-reversed.jsonl> --baseline-predictions <baseline.jsonl>
```

Four bars, all of which must pass: TPR ≥ 0.90, TNR ≥ 0.80, Cohen's κ ≥ 0.6, flip rate < 10%.

`--reversed-predictions` comes from a **second judging run with `reverse_rubric=true`**. Without it
the flip rate is reported `NOT MEASURED` and a passing verdict is downgraded to provisional — on
purpose. A flip-rate run against a rubric that was never actually reversed reports `0% flips` and
reads as the strongest result on the page while measuring nothing.

**If the rule fleet matches or beats the judge, the honest outcome is to drop the judge.** The
command says so when it happens. A judge that adds nothing a regex already contributed is a cost,
not a capability.

Then the rule lifecycle:

```bash
uv run python -m gtm_core.eval_calibration rules --profile <active> \
  --qa-dir content/<active>/prospects/evals/qa/ --labels <labels.jsonl> \
  --sequence-id <sequence-id> --not-human-visible "<unusable rules from build_eval_sheet>"
```

**Pass `--sequence-id`.** A QA directory holding two campaigns' records aggregates them into one
confident, wrong fire rate. **Pass `--not-human-visible`** with the unusable-rule list
`build_eval_sheet` returns: those rules' defects never render into a labeling sheet, so absence of a
human penalty says nothing about them, and reading it as "nobody cared" would retire working rules
on an artifact of the sheet.

A rule with no persisted QA record reports `no-control` — **not "fine", *unknown***. If everything
comes back `no-control`, the finding is that `--json` is missing from the merge-render gate, not
that the rules are unproven.

Deletions are PRs, never automatic. The mutation suite pins the catalogue count and requires one
negative-control test per rule, so retiring one is a deliberate, reviewed act.

Finally, outcomes:

```bash
uv run python -m gtm_core.eval_calibration reconcile --profile <active> --judged <records.jsonl>
```

This will report **"insufficient events"** and that report is the point, not a gap. With 24 sends
and 0 positive replies, every loop above optimises against *taste*, not outcomes. This verb is the
socket that makes taste falsifiable the moment replies exist; until then it correctly refuses to let
one reply flip a verdict.

---

## When to run a round

Ad hoc on a material change, not a calendar — labelling is human time, and the rule report needs
send-driven QA accumulation rather than date-driven. Triggers:

- a new angle promoted, retired, or added to `angles.toml`
- a card question added, removed or **reworded** — `card.fingerprint()` changes, so records before
  and after are two populations and pooling them moves a rate nobody changed the copy for
- copy re-cut, or new premise vocabulary
- a judge rubric revision (**capped at 3 per campaign** — past that the judge is permanently
  demoted to a ranker rather than tuned further)
- ≥ 2 new QA records since the last rule report
- replies crossing the 5-event floor, which is when `reconcile` starts saying something

The one thing that runs **every** time is QA-record persistence (`--json` on the merge-render
gate), because it is free and because nothing downstream works without it.

## What this loop does not do

It does not revise its own rubric. That is a human reading disagreements, capped at three
revisions. A rubric that rewrites itself against labels it influenced has no fixed point — the
system improves, and the standard it improves against stays human.

## Degraded mode (no paid connectors)

The judge picks its transport automatically and needs no configuration: with ANTHROPIC_API_KEY set it scores one row per request against the Anthropic API; without one it falls back to the Agent SDK on the host's own auth (an OAuth subscription in a local Claude Code session), batching a few rows per prompt to amortise subprocess cost. Both record `backend` and `judge_batch` on every row, so a holdout scored across the two is a visible confound rather than a silent one. If BOTH are unavailable — no key and an expired or revoked OAuth token — the judge returns an error payload and the reading pass reverts to what it was before this skill existed: sample the list with `gtm_core.adjudication sample`, read the rendered emails yourself, and record one adjudication JSONL line per email by hand. Every other mode — `sheet`, `apply`, `report` — is pure deterministic Python and works unchanged, because the human labels, not the judge, are the ground truth the whole program is built on.

## How to close this run (every surface)

Report, in this order and in the operator register (the `gtm-operator` output style): Lead with the outcome; what matters about it in their terms; the next decision as a choice they can answer; and what it cost, exactly as the ledger reported it, if anything metered ran.
File paths, commands, module names and raw output go in a final
<details><summary>Details</summary> … </details> block; the main reply must make sense
without it.

Markers: emit a ⟦…⟧ marker (⟦GATE:…⟧, ⟦POST⟧, ⟦FILE:…⟧) only when your system prompt carries
a `Surface:` line that says so. Otherwise show the same content as a quoted block headed
"This is exactly what would go out."

Active profile: the one in your system instructions, or, in the desktop app, the answer to
`uv run python -m gtm_core.active_profile show`.
