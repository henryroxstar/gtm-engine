---
refreshed: 2026-09-01
review: evergreen
---
# Knowledge Pack — REFRESH guide (template)

> **This file did not exist before 2026-09-01**, so every tenant onboarded until then started with
> no refresh doctrine at all. Rewrite the tenant-specific parts as the pack grows; keep the two
> mechanisms below, which are engine behaviour rather than opinion.

## What's in the pack

Record, as a table, which condensed topic came from which raw source. That mapping is the thing
nobody can reconstruct later, and it is what makes a source-driven refresh possible at all.

| File | Condensed from | Drives |
|---|---|---|
| `company.md` | *(name the brief/deck)* | positioning, outreach framing |
| `product.md` | *(name the brief/deck)* | product claims, solution design |
| `icp-personas.md` | *(name the brief/deck)* | targeting, scoring, hook selection |
| `case-studies.md` | *(delivered engagements)* | proof, outreach |

## Two axes: the clock and the source

A topic goes stale two different ways, and the corpus tracks them separately.

**`review:` — the clock.** `evergreen | 14d | 30d | 90d | 180d | 365d`. A cadence is a *promise
that elapsed time makes a topic due*, so only set one the pipeline can actually clear: refresh can
re-fetch a topic that has a URL `source:` or its own "Sources & verification log" section. A topic
with neither on a short clock is **permanently overdue** — it can never be cleared and it drowns
out the topics that can. That is a mis-set cadence, not a stale file.

```bash
python -m gtm_core.knowledge_refresh due --profile <active> --json
```

**`triggers:` — the source.** Comma-separated event kinds: `sales-deck` · `product-release` ·
`regulatory-update` · `case-study` · `pricing` · `icp-revision` · `brand`. When a new deck, release
or regulation lands, ask the corpus what it touches instead of hand-picking files:

```bash
python -m gtm_core.knowledge_refresh impact --profile <active> --source-kind sales-deck
python -m gtm_core.knowledge_refresh impact --profile <active> --source "https://docs.…/product/"
```

The `--source` mode needs no setup — it inverts the `source:` field every topic already carries, so
one doc-site URL selects every topic condensed from it.

**`reflects:` — which vintage.** `refreshed:` dates the *human verification act*; `reflects:` names
the *material* (`master-deck-2026-08`, `product-v2.1`, `some-framework-v1.5`). Without it, a report
can say a topic is coupled to a change but never whether it is behind. Set it on every candidate
you stage.

**Why `triggers:` is not just another `review:` value.** An out-of-band trigger is not something a
periodic scan can observe, so it must not masquerade as a cadence — `check` rejects prose cadences
like `on next deck revision` for that reason. `triggers:` is never scanned for; it is matched at the
moment an operator brings a new source in.

**Evergreen still gets reviewed.** `evergreen` means *no clock*, not *never review*: those topics
are excluded from `due` and **included** in `impact`. The voice guide, brand notes and hook matrix
usually carry it, and they are exactly what a rebrand or a new deck invalidates.

## The write boundary

`profiles/<tenant>/` is **read-only at runtime**. The refresh skill stages candidates under
`content/<tenant>/knowledge-staging/`; an operator reviews with `knowledge_staging diff` and
promotes with `knowledge_staging promote`, which re-stamps `refreshed:`. Never edit the live corpus
from a pipeline skill.
