# Linter tripwire corpus — PRD 2026-09-01 §6.2 V3, Phase 3A entry criterion

Five gtm_core linters are next in the complexity-budget burn-down: `deck_lint`, `brief_lint`,
`video_lint`, `shots_lint`, `content_quality`. Every one is a **tenant-facing gate** — it decides
whether generated content ships. A weakened linter after a split does not fail loudly; it silently
**approves content it used to block**. That is the one failure mode this corpus exists to catch,
and it is why the PRD requires it to land *before* any of the five is touched, captured against the
linter as it stands today.

## Two layers, per linter

1. **Semantic rule inventory** (`deck_lint`, `video_lint`, `brief_lint`, `shots_lint`) — each
   linter's findings are `(tier, rule, severity)` triples. A `CORPUS` of fixtures drives every rule
   family at least once; `test_every_rule_family_is_tripped_by_the_corpus` asserts **set equality**
   against a literal `INVENTORY`, so a rule that stops firing is named, and a new rule must join
   the corpus to pass. `content_quality` has no such literal — see its test file's docstring for
   why, and what it pins instead (CLI-only; a future semantic inventory is real work still open).
2. **CLI golden transcripts** (all five) — `python -m gtm_core.<linter> --help` and a handful of
   committed invocations (`support.CASES`), asserted byte-exact against `help/*.txt` / `cli/*.txt`.
   This is what catches an exit-code change, a reordered rule, or reworded output that the
   semantic layer's structural comparison wouldn't.

## Regenerating goldens

```bash
uv run python tests/tripwire/support.py --write
```

**Only after a deliberate CLI change** — a reworded message, a new flag, a genuinely new rule.
Never to turn a red run green after a split: a golden that drifts during what should be pure
motion *is* the drift this corpus exists to catch (PRD §6.1 row 7). If `--write` is the fix, the
split introduced a behavior change and the PR needs to say so, not silently re-baseline.

## Fixtures are fictional

Every corpus fixture (deck content, briefs, shot lists, quotes, personas) is invented — no real
company, person, or contact, per `CLAUDE.md`'s third-party-PII rule. Where a real finding shape
matters (e.g. a specific banned phrase), the fixture keeps the *shape* of what tripped a rule and
invents the specifics.

## When a split lands

The corpus must still pass byte-exact, unmodified, against the split linter. If it doesn't:

- A semantic-layer failure (a rule stopped firing, or fires with a different severity) is the
  regression this whole exercise exists to block — fix the split, not the test.
- A CLI-golden failure that is a **deliberate** consequence of the split (e.g. `prog` changed
  because the module became a package) gets `--write`'d, and the PR body says so explicitly.
