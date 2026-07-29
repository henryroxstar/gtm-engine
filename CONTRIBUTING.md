# Contributing

Thanks for your interest in contributing. This document covers the workflow,
tooling, and the few non-negotiable invariants this project enforces.

## Ground rules

- Be respectful — see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
- For **security issues**, do **not** open a public issue — follow
  [`SECURITY.md`](SECURITY.md).
- By contributing, you agree your contributions are licensed under the project's
  [LICENSE](LICENSE) (Apache-2.0). Sign your commits off (DCO): `git commit -s`.

## Development setup

Python ≥ 3.11. This project uses [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync                 # install deps into .venv
uv run pytest -q        # run the test suite
```

Always use `uv run …` — never a bare `python`/`pytest`.

## Before you open a PR — the CI gates

Your change must pass the same gates CI runs:

```bash
uv run ruff check .                       # lint
uv run ruff format --check .              # formatting
uv run pytest -q                          # tests
bash tests/lint/debrand_check.sh          # no hardcoded tenant strings in the engine
gitleaks detect --no-banner               # no secrets
```

For changes that touch packaging or the publishable surface, also run the
pre-publish de-brand gate:

```bash
bash tests/lint/debrand_check.sh --release
```

The enforced Python rules (§R1–§R8) are documented in [`docs/RULES.md`](docs/RULES.md);
each is CI-gated. Read them before changing the agent, publish, or permission paths.

## Invariants you must not break

These are load-bearing — see [`CLAUDE.md`](CLAUDE.md) and
[`docs/SECURITY-SELF-ASSESSMENT.md`](docs/SECURITY-SELF-ASSESSMENT.md):

1. **No secrets in the repo.** Config stores env-var *names*, never keys. The model
   registry resolver rejects secret-shaped values.
2. **All external I/O via MCP tools** — no raw outbound HTTP in the agent path.
3. **Publishing stays human-gated.** `autopublish: false` everywhere; no new outbound
   destinations representable in agent output.
4. **No `bypassPermissions`.** Keep `permission_mode="default"` + `can_use_tool`.
5. **Tenant facts live in `profiles/<slug>/`, never in `plugin/` or engine code.**
   The de-brand lint enforces this.

A PR that changes one of these boundaries must say so explicitly and update
`CLAUDE.md` + `docs/SECURITY-SELF-ASSESSMENT.md` in the same PR.

## Pull request process

1. Fork and branch from the default branch.
2. Keep PRs focused; write a clear description of *what* and *why*.
3. Add/adjust tests for behavior changes.
4. Ensure all gates above pass locally.
5. Link any related issue. A maintainer will review; address feedback by pushing
   follow-up commits (don't force-push during review unless asked).

## Commit style

- Conventional-commit prefixes are appreciated (`fix:`, `feat:`, `docs:`, `chore:`…).
- Sign off every commit: `git commit -s`.
