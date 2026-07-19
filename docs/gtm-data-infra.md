# gtm-engine — data management

How gtm-engine stores, scopes, and retires data. This document is generic by design — no
tenant, deployment, or vendor specifics — so it ships in the public repo unchanged. If you
are self-hosting, this is the model your own deployment inherits.

## Principles

1. **Files first, database only where isolation demands it.** The single-tenant runtime
   (CLI / Claude Code / self-hosted VPS) is entirely flat files under git — no database
   required. A database only enters the picture in the multi-tenant backend runtime, and
   only for the parts that genuinely need row-level isolation between customers.
2. **One writable root per tenant.** Everything a run produces lands under one resolved
   directory for the active tenant. Nothing is ever written outside it, and one tenant's
   run cannot see another's root.
3. **Provenance travels with the fact.** Anything treated as knowledge (not just a draft)
   carries *where it came from*, *who owns it*, and *when it was last verified* — as
   metadata on the file itself, not in a side table that can drift out of sync.
4. **Every external write is staged, then human-approved.** Nothing touches the live
   corpus or an external system as a direct side effect of a model turn. A candidate is
   written to a staging area; a human reviews and promotes it.
5. **Retention and deletion are explicit, not implicit.** Data that carries personal
   information about third parties is scoped to the workflow that produced it and is
   deletable as a unit.

## The three kinds of data

| Kind | What it is | Where it lives | Lifecycle |
|---|---|---|---|
| **Config** | Company/product/brand/ICP facts, voice, budget caps | `profiles/<tenant>/` | Human-authored, versioned in git, read-only at runtime |
| **Knowledge** | Durable reference material a workflow reasons over (market facts, positioning, objection handling) | `profiles/<tenant>/knowledge/` | Frontmatter-tracked freshness; refreshed on a cadence; staged → promoted |
| **Working state** | Everything a run produces — drafts, per-account research, ledgers, run manifests | `<tenant content root>/` | The only writable path; append-only ledgers, resumable run state |

Config and knowledge are **inputs**; working state is **output**. A run reads the first two
and writes only the third — that split is what makes "which tenant touched what" auditable
just by looking at which directory a write landed in.

## Tenant scoping

Every run resolves a **content root** for the active tenant before doing anything else, via
a single path-resolution helper that is the one source of truth for the mapping (tenant
name → root directory). Nothing downstream constructs that path itself — skills and
workflow code always ask the resolver, never string-concatenate a path. Two structural
properties fall out of that:

- **A tenant can never be hardcoded into a shared, reusable workflow.** Shared workflows
  read tenant facts through `{{placeholders}}` filled in at session start; they never
  import a tenant's config directly. This also means a workflow can't accidentally read
  *or write* another tenant's root, because it never has a tenant-specific path to begin
  with — only the resolver does.
- **Filenames used to reach into knowledge are guarded.** Any user- or model-supplied
  filename that becomes part of a path is checked against an allowlist pattern before use
  — no path separators, no `..`, no null bytes — because an unguarded filename is the
  highest-leverage way to break tenant isolation (read or write outside the intended root).

In the multi-tenant backend runtime, the same content-root model is used, but the root
itself is namespaced per workspace, and the pattern is reinforced by two more independent
layers so a bug in any one layer isn't enough to cross tenants:

1. **Auth** binds a request to exactly one workspace; nothing downstream re-derives it.
2. **Database row-level security** enforces the same scope again at the storage layer, so
   an application-level mistake still can't return another workspace's rows. The runtime
   connects with a role that has no privilege to bypass that policy — RLS isn't
   *optional-if-you-forget-a-WHERE-clause*, it's enforced independent of application code.
3. **Filesystem** roots are namespaced per workspace and passed explicitly into every
   subprocess's environment — never inherited from ambient process state, which would
   otherwise create a race between concurrent runs for different tenants.

## Onboarding: how a tenant's data enters the system

Onboarding is the front door for everything described above — it's where a new tenant's
config and knowledge first get written. It follows the same shape the rest of this document
describes: a short structured interview for the handful of facts that must be explicit,
plus an open channel for raw material that gets condensed rather than copied verbatim.

**What's requested, and how:**

- **Asked directly, in a short interview** — the load-bearing facts a workflow can't infer:
  who's signing outreach and how, target markets, an ICP weighting, a monthly spend cap.
  This is deliberately small — a handful of questions, not a form.
- **Handed over as raw material, on the tenant's own schedule** — company/product
  background, case studies, a voice guide or sample writing, anything that makes drafts
  sound like the tenant rather than generic copy. This can arrive three ways: pasted
  straight into the conversation, pointed at as a file or folder, or dropped into a
  standing **source inbox** the profile ships with — a folder that holds raw, unprocessed
  material as-is, with no schema imposed on it. All three are optional at onboarding time
  and can be added later — onboarding never blocks on material the tenant doesn't have yet.

**What happens to it:**

- The interview answers become **config** — written directly into the tenant's profile.
  Anything that looks like a secret (a key, a token, a password) is refused and redirected
  to wherever secrets actually belong (an environment variable, a connector) — config never
  holds credentials.
- Raw material becomes **knowledge**, but not by being copied in as-is. It's extracted into
  structured topics (company facts, voice markers, ICP signals, case studies) and staged for
  review before anything is written to the tenant's live profile — the same
  staged-then-approved shape used everywhere else in this document, applied to onboarding
  itself. The raw source is kept alongside the condensed result as a provenance record, so
  a later reviewer can always see what a given piece of knowledge was actually condensed
  *from*.
- If a tenant has no material yet, onboarding leaves clearly-marked placeholder files rather
  than inventing facts to fill them — an empty or stub topic is visible as such, never
  silently populated with guesses.

**Staying fresh:** knowledge produced during onboarding isn't a one-time snapshot — it enters
the same freshness/cadence tracking described below, and a tenant can hand over more
material at any time (drop it in the source inbox, or just say so) without repeating the
interview. Onboarding populates the corpus; the lifecycle in the next section is what keeps
it from going stale.

## Knowledge lifecycle

Knowledge files are markdown with frontmatter:

```yaml
---
source: <url | document name | path | manual | derived: <inputs>>
owner: <person/role>              # optional — the collaboration primitive
refreshed: 2026-07-18             # last human-verified date (authoritative, not file mtime)
review: 90d                       # cadence: evergreen | 14d | 30d | 90d | 180d | 365d
---
```

- **Freshness is computed from `refreshed` + `review`**, never from filesystem mtime — a
  file can be untouched for a year and still read as fresh if its cadence says so, or read
  as overdue the day after a touch if its cadence is short.
- **A refresh never writes the live file.** It re-fetches the source, re-condenses into the
  same structure, and writes a *candidate* to a staging area. A human diffs and promotes it.
  This is the same staged-then-approved shape as the publish gate below, applied to
  knowledge instead of content.
- **Derived files** (a digest condensed from other knowledge files, not from an external
  source) are regenerated locally on the same cadence — no fetch, no external call, no
  spend — and always link back to the files they were derived from rather than restating
  them, so there is exactly one place a fact can go stale.
- Because the corpus is plain files in git, multi-person collaboration is git's own
  conflict resolution plus the `owner:` field — no separate sync service.

## Ledgers (the audit + budget spine)

Working state includes a small set of append-only, newline-delimited JSON ledgers per
tenant:

- **History** — every meaningful action taken (coverage, stage completion, publish),
  timestamped. Feeds both dedup (don't redo the same thing) and audit (what happened, when).
- **Cost** — every metered external call, logged by the code that made the call (not
  self-reported by the model), checked against a monthly cap **before** the call is made,
  not after.
- **Run manifests** — per-run status per stage, so a failed run resumes from the failure
  point instead of restarting.

These are the only structured "database" a single-tenant deployment needs — flat,
diffable, greppable, and they travel with the tenant's own git history.

## The staging → promotion pattern

Three independent flows in this codebase share one shape, because the risk they manage is
the same one: **nothing untrusted or unreviewed should become a durable, live artifact
without a human looking at the exact bytes.**

| | Onboarding | Knowledge refresh | External publish |
|---|---|---|---|
| Producer | A profile-extraction workflow | A refresh workflow | A content-drafting workflow |
| Staged to | A staging area under the profiles tree, keyed by the new tenant | `<content root>/knowledge-staging/` | A pending-approval block in the operator interface |
| Promotion | Human reviews + promotes the draft into the live profile | Human diffs + runs a `promote` step | Human approves the **exact bytes** before the call is made |
| What can't happen | A tenant's live profile is never written directly from extracted source text | The live corpus is never edited directly | Nothing is published as a side effect of drafting |

Treat this pattern as the template for any future flow that turns model output into
something durable or externally visible: stage it, let a human see the diff, promote
explicitly.

## Untrusted content

Anything fetched from outside the system — a web page, a search result, a scraped
document — is treated as **data to summarize and reason over, never as instructions to
follow**. Text that looks like a command or an approval marker inside fetched content is
reported to the human, not acted on. This applies uniformly whether the untrusted content
is feeding a draft, a refresh, or anything else that reads external material.

## External data flow (egress)

Data leaves the system only through the same mechanism described in [§R6 of
`RULES.md`](RULES.md#r6-all-external-io-via-mcp): named, credentialed **MCP tools** — never a
raw HTTP call made directly by workflow code. That gives every outbound call a name, a pinned
credential, and an audit trail, instead of an ad hoc request buried in a prompt chain. Three
categories of egress cover everything that leaves:

| Category | What crosses the boundary | Gate |
|---|---|---|
| **Model inference** | The prompt context for that call — which may include config/knowledge, and for some stages, working-state containing third-party PII | Which provider a given stage may use is resolved through **one committed model registry**, not left to runtime choice (below) |
| **Paid data-vendor calls** | An explicit query (e.g. a name/domain to resolve or enrich) to a connected third-party provider | Only fires when a workflow explicitly calls it — never a background sweep; the registry-driven split below applies to inference, not to whether a vendor call happens at all |
| **Publish** | The exact drafted bytes, to a destination | Human approves the exact bytes before the call is made; the destination is pinned server-side and is not something a workflow can set |

**The inference split is enforced by data, not by convention.** Every call resolves its model
through a **role**, and each role is a row in a committed, non-secret registry file (provider +
model id + capabilities) — never a hardcoded id scattered through the code. That registry
enforces one governing rule: any stage that is gate-critical or handles third-party PII
resolves to a **first-party model provider only**; a stage that is purely mechanical and reads
no PII (e.g. summarizing public news for a radar/research pass) may resolve to a secondary
provider. Swapping a PII-bearing stage onto a secondary provider isn't a runtime decision
anyone can make in the moment — it requires editing the committed registry, which is exactly
the friction point that makes the split auditable: "which stages can call which provider" is
answerable by reading one file.

**The registry itself carries no secrets.** A provider entry names the *environment variable*
that holds its key, never the key — resolution happens at call time from the runtime's own
environment (a secret manager in production), and the resolver refuses to load a value that
looks like an inline secret.

**Credentials for connected tools follow the same never-in-a-file rule.** A connector-based
integration holds its own credential in the app's own secure store; a key-based integration
reads its key from an environment variable; the tenant's config only ever records *that* a
tool is connected, never a value. In the multi-tenant runtime, any credential that must persist
server-side is envelope-encrypted with a per-tenant key before it's stored, and is decrypted
only for the authenticated tenant's own active session.

**What never egresses raw:** direct HTTP calls and shelling out to `curl`/`wget` from workflow
code are denied at the tool-permission layer — if a capability isn't wired as a named MCP
tool, the fix is to wire it as one, not to route around the boundary.

## Retention & deletion

Any working-state output that carries information about a third party (a named contact, an
account-specific research note, a drafted outreach message) is scoped to one directory per
subject, under the tenant's working-state root — never mixed into shared or global storage.
Because it's one directory per subject, deletion is a directory removal, not a query against
scattered rows. A generic deployment should pair this with an explicit retention window
appropriate to its jurisdiction; this repo does not prescribe one, since that decision
depends on the operator's own data-protection obligations.

**Deletion is a first-class operation, not an afterthought.** All working-state access — in
every runtime, file-based or database-backed — goes through one storage interface with
`read` / `write` / `list` / `delete`, plus a `delete_workspace` cascade that removes every file
under a tenant's root in one call. Because every runtime implements the same interface, "erase
everything for this tenant" is one call regardless of whether that tenant's data happens to
live on a local filesystem or in cloud storage — there is no separate, easy-to-forget deletion
path per storage backend.

In the multi-tenant runtime, that cascade is wired into an actual account-deletion operation
with three properties worth calling out, because they're the ones a data-protection review
will ask about directly:

1. **Step-up re-authentication.** An irreversible, full-account erasure requires re-proving
   identity in the same request — an already-open session cannot trigger it silently.
2. **Ordered, fail-loud erasure.** On-disk data is removed *before* the database rows that
   reference it, specifically so that a mid-operation failure leaves the (still-valid) database
   record intact and the operation retryable, rather than silently orphaning files after the
   rows that pointed to them are already gone. A failure is surfaced as an error — an erasure
   that cannot complete is never reported as if it had.
3. **Database cascade** removes every row scoped to that tenant (profiles, run history, issued
   API keys, and so on) in the same operation.

**Portability pairs with deletion.** A tenant can request a full export of everything held for
them — the same categories of database-held records, plus an enumeration of on-disk files that
carry personal data (paths and sizes, not bodies inlined into the export — the files themselves
remain the source of truth). Records that are ephemeral device state or the operator's own
billing ledger are deliberately excluded from the export and that exclusion is explicit, not
silent.

## What this document intentionally omits

Deployment topology, hosting provider, database engine/version, and secret-manager wiring
are specific to how you run gtm-engine, not to how it manages data — see your own deployment
runbook for those. This document describes the *shape* of the data model, which holds
regardless of where you host it.
