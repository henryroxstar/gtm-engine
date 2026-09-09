# Creative brief — the template

> **Company-neutral.** The shape of the hand-authored document beside a run's `brief.json`, at
> `content/<active>/video/<script-slug>/creative-brief.md`.

## Two pages. That is the whole template.

**Page one is the brief.** What we are making, for whom, and why — the questions an agency brief has
asked for forty years, answered in one screen.

**Page two is the beat sheet.** The strip of frames and one row per beat. What a director scans on
the day.

If it does not fit on those two pages, it is not brief material. That is not a style preference: an
earlier version of this template had eight sections, and the document it produced was four documents
stapled together — a brief, a production bible, a craft manual and an engineering changelog. Three of
those are not for the person reading a brief, and all three already existed elsewhere.

## The rule that keeps it short

**Never restate a fact that lives somewhere else.** Everything derived — captions, timings, gate
results, claim counts, sampling budgets — lives in the script and the shot list, and all of it
changes. A brief that copies them disagrees with the film within the hour. Point at the owner.

| the urge | where it actually goes |
|---|---|
| how each beat gets made; locations, units, wardrobe, coverage | the **call sheet** |
| what an earlier cut did and why it changed | the run's **`RESUME.md`** |
| a durable craft finding about the engine or the grammar | **`performance-lexicon.md`**, or the beat card in `video-storyboard` |
| a gate result, a score, a claim count, a word budget | the **script** |
| a validated decision and its `source` | **`brief.json`**, through the CLI |
| the method checking itself — ingredient tables, structure names | **`brief.json`** decision reasons |

---

## Page one — the brief

Eight blocks, a few lines each. Anything longer is a symptom.

**The film.** One sentence. What happens, in the order it happens.

**Who it is for.** One person, specific enough that the wrong reader is lost in three seconds.

**What they believe now.** Their line, in their words — the thing they would say if asked.

**What we want them to feel.** Not think. The shift, in one sentence.

**The one thing.** The single-minded proposition. If it needs two sentences it is two propositions
and the film will carry neither.

**The story, in three lines.** Setup, turn, payoff. Prose, not a table.

**Tone.** Three words, then the one rule that decides a close call.

**Never.** The short list of things that would break it. Five items at most; the rest belongs in the
shot list's `negative`.

---

## Page two — the beat sheet

A strip of numbered frames, then one row per beat:

| # | time | what happens | on screen | the one note |
|---|---|---|---|---|

**One beat, one shot, one row.** The *one note* column is the single direction that decides whether
the beat works — not a description of it. If a beat needs three notes, it is under-designed, and if
it needs none, say so.

Renderable fields — framing, lens, light, expression, camera, sound — live in the shot list. Do not
copy them here.

---

## What is deliberately absent

**No spine diagram.** The method's own reference owns it. A brief that teaches the method to its
reader has the wrong reader.

**No ingredient checklists.** Whether the payoff satisfies the five ingredients is a question
answered at `brief.json` decision 7, before spend. A brief is not where the method audits itself.

**No craft rules.** *"A negative cannot subtract what the positive builds"* is true of every film
this system makes, so it belongs where every film can see it — not in one film's brief.

**No open-questions list.** That is `RESUME.md`. A brief describes a decided film; what is still
undecided is a state of the project, not a property of the piece.

## The check

There is no linter for prose, which is why the restatement rule matters and why each run should carry
a twenty-line consistency check cross-referencing this document against the shot list. It catches the
class of drift every real gate is blind to.
