<!--
For SECURITY fixes, coordinate privately first — see SECURITY.md.
Sign your commits off (DCO): git commit -s
-->

## What & why

Describe the change and the motivation. Link any related issue (`Fixes #123`).

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Docs
- [ ] Other:

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pytest -q` passes
- [ ] `bash tests/lint/debrand_check.sh` passes (no hardcoded tenant strings)
- [ ] `gitleaks detect --no-banner` is clean (no secrets)
- [ ] Tests added/updated for behavior changes
- [ ] Commits are signed off (`git commit -s`)

## Security invariants

- [ ] This PR does **not** weaken any invariant in `CONTRIBUTING.md` (human-gated publish,
      MCP-only egress, no secrets in repo, no `bypassPermissions`, tenant separation).
- [ ] If it **does** change a boundary, I updated `CLAUDE.md` + `docs/SECURITY-SELF-ASSESSMENT.md`
      in this same PR and said so above.
