# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report privately via one of:

- GitHub **Security Advisories** → *Report a vulnerability* (preferred), or
- email **register.henry@gmail.com** with subject `SECURITY: <short summary>`.

Please include: affected version/commit, a description, reproduction steps or a
proof-of-concept, and the impact you observed.

### What to expect

| Stage | Target |
|---|---|
| Acknowledgement of your report | within **3 business days** |
| Initial assessment + severity | within **7 business days** |
| Fix or mitigation plan | depends on severity; communicated in the assessment |
| Public disclosure | coordinated with you, **after** a fix is available |

We support responsible, coordinated disclosure and will credit reporters who
wish to be named.

## Scope

In scope: the engine code in this repository (`agent/`, `backend/`, `gtm_core/`,
`mcp_server/`, `cockpit/`, `plugin/`, `schemas/`).

Out of scope: third-party dependencies (report upstream), any operator-specific
deployment (VPS, secrets, tenant profiles) that is **not** part of this repo, and
findings that require a misconfigured deployment rather than a code defect.

## Security model (context for reporters)

This project is built least-privilege by design. A few invariants are load-bearing —
see [`CLAUDE.md`](CLAUDE.md), [`docs/RULES.md`](docs/RULES.md), and
[`docs/SECURITY-SELF-ASSESSMENT.md`](docs/SECURITY-SELF-ASSESSMENT.md):

- **Secrets never live in the repo.** Provider keys resolve at runtime from named
  environment variables; the model registry and config store env-var *names*, never keys.
- **All external I/O flows through MCP tools** — no raw outbound HTTP in the agent path.
- **Publishing is human-gated.** No content leaves the system without an explicit
  operator approval bound to the exact bytes; `autopublish: false` everywhere.
- **No `bypassPermissions`.** The agent runs with `permission_mode="default"` + a
  `can_use_tool` callback.

Reports that demonstrate a break in any of these invariants are especially valuable.
