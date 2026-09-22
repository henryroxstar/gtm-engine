# Provider adapter — Saleshandy

Maps the skill's logical staging flow to Saleshandy's MCP tools. **Read this before staging.**

## Tool namespace

Saleshandy's tools are named `<server>__<tool>`. The `<server>` prefix differs by runtime:
- **Interactive (claude.ai connector):** `mcp__<connector-id>__create_sequence` (an opaque id).
- **Headless (VPS agent, wired in `agent/mcp_config.py`):** `mcp__saleshandy__create_sequence`.

Use whichever Saleshandy server is connected in the current session — the bare function names below
are identical across both. If no Saleshandy server is connected, fall back to the skill's manual
path.

## The one rule

**Never call `update_sequence_status` (or any resume / activate / start).** That tool is the send
trigger — flipping a sequence to `resume` is what makes Saleshandy start emailing prospects. It
belongs to the operator, in Saleshandy's UI. A freshly built sequence is already **inert** (it
cannot send until it is *both* resumed *and* has an active sending account), so staging is safe by
construction: just build, enroll, and stop.

## Capability map

| Logical step | Saleshandy tool | Key params | Notes |
|---|---|---|---|
| Find sending account | `list_email_accounts` | `search`, `status` | `status:1` = Active. Grab the `id` (hashed) + check `healthScore`. |
| Create the sequence | `create_sequence` | `title` (req); optional `emailAccountId`, `scheduleId` | Returns `sequenceId`. Can attach the sender + schedule atomically here. Starts **paused/inert**. |
| Create a schedule | `create_schedule` | `name`, `timezone` (IANA), `timeSlots` (exactly 7 days, 0=Sun) | Use `slots:[]` for inactive days. Returns `scheduleId`. |
| Add one step per touch | `add_sequence_step` | `sequenceId`, `step_type` (friendly name e.g. `Email` — the wrapper maps it to Saleshandy's integer channel code), `absoluteDays` (1–999 = cadence offset), `variants:[{payload}]` | One step per touch, in day order. Email variant `payload = {subject, content}` (`content` = HTML body — **not** `body`; optional `preheader`). **≤ 1 Email step per day.** |
| Add an A/B variant | `add_step_variant` | `sequenceId`, `stepId`, `type:"Email"`, `payload` | Max **26 variants** per step. Variant channel must match the step. |
| Attach sending account | `add_email_accounts_to_sequence` | `sequenceId`, `emailAccountIds:[…]` | Account must be Active (else error 40201). Max 50/call. |
| Enroll leads | `add_leads_to_sequence` | `leadIds:[…]`, `sequenceId`, `stepId` | **Confirm sequence + step + list with the operator first** (the tool mandates it). Only leads with a **revealed/verified email** enroll; unverified/phone-only are silently skipped. `newTags`/`tagIds` optional. |
| Read config | `get_sequence_settings` | `sequenceId`, optional `code` | CC/BCC, tracking, unsubscribe link/text + one-click header, text-only, schedule, priority. |
| Set config | `update_sequence_settings` | `sequenceId`, `settings:[{code,value}]` | Config only — **no send**. See recipes below. |
| Read stats (later, read-only) | `get_sequence_stats` / `list_sequences` | `sequenceId` | Safe to call any time — no send. Use for the read-back offer. |

**Settings codes** (all `value`s are strings): 1 unsubscribe-link (HTML w/ `{{link}}`), 2 unsubscribe-text, 3 mark-as-finished, 4 track-link-clicks, 5 track-email-opens, 6 email-risky-prospects, **7 bcc** (JSON array string), **8 cc** (JSON array string), 9 text-only-email, 10 show-text-only-option, 11 esp-matching, 12 first-step-text-only, **13 unsubscribe-via-email-header** (one-click List-Unsubscribe).

**CC / BCC / unsubscribe recipes** (via `update_sequence_settings`):
- **CC** a copy to yourself/a colleague → `[{"code":8,"value":"[\"you@work.com\"]"}]` — **visible to the recipient**.
- **BCC** a CRM logging address (e.g. HubSpot) → `[{"code":7,"value":"[\"12345@bcc.hubspot.com\"]"}]` — hidden; **only logs if the CRM recognises the sending mailbox**.
- **One-click unsubscribe header** (required by Google/Yahoo bulk-sender rules; expected by this repo on every sequence) → `[{"code":13,"value":"1"}]`.
- CC/BCC values are **per-operator PII** — pass them at runtime (or from env); **never hardcode into committed config.**

## Bulk CSV/array import — and the step-scoped upsert trap that silently no-ops

`add_leads_to_sequence` above takes `leadIds` for prospects that already exist as Saleshandy
leads. Loading straight from a `ready-to-load*.csv`-style export instead goes through one of
two array/CSV-shaped import tools, and picking the wrong one for a *refresh* looks identical
to success while doing nothing:

| Tool | Scope | Behavior |
|---|---|---|
| `import_prospects_to_sequence_step` | one sequence + one step | Creates new prospects in that step. With `conflictAction: "upsert"`, an email **already enrolled in a different step of the same sequence** is rejected — the field update is **not applied** — even though the call itself reports success. |
| `import_prospects_with_field_name` | account-wide, sequence-agnostic | Upserts by email regardless of current sequence/step enrollment. The correct tool for refreshing a custom field (e.g. a `Why Now` merge field) on prospects that may already be staged. |

**Found 2026-08-12:** refreshing a `Why Now` custom field on 439 already-enrolled prospects
via `import_prospects_to_sequence_step` (`conflictAction: "upsert"`) returned `isCompleted:
true` for every batch — but every row's `failedProspectsURL` listed "Prospect already present
in another step of same sequence" as the failure reason. The field had **not** actually
updated for any of them, despite the call looking clean. `isCompleted: true` is proof the
*import job finished*, not proof any row's data changed — the two are silently different
things on this tool.

**Rule:** use `import_prospects_to_sequence_step` only for **first-time enrollment** into a
specific step. Use `import_prospects_with_field_name` for **updating a field on prospects that
may already be enrolled anywhere in the sequence** — it was the tool that actually applied the
same 439-row refresh cleanly once swapped in. After **either** tool, call
`check_prospect_import_status(requestId)` and fetch `failedProspectsURL` (a plain URL) before
reporting the batch as landed — a batch with zero entries there is the only real confirmation.

## Compliance preflight — where the address and the opt-out actually live

Neither lives in the copy, so neither shows up in the sequence spec. Both are checkable before
enrollment; check them, don't assume them.

| Requirement | Where it lives | How to check (read-only) | Pass condition |
|---|---|---|---|
| **Physical postal address** | The **sending mailbox's signature**, appended to every email — *not* a sequence setting | `list_email_accounts` → `payload.emails[].settings[]` → the entry with `code: "signature"` | non-empty **and** contains the legal entity + street address |
| **One-click unsubscribe** (RFC 8058 `List-Unsubscribe`) | Sequence setting **code 13** | `get_sequence_settings(sequenceId)` → code 13 | `"1"` (provider default is `"0"` — off) |
| **Visible opt-out line** | Sequence settings **code 1** (link HTML w/ `{{link}}`) and **code 2** (plain text) | same call → codes 1, 2 | at least one non-empty |

**Operator UI paths** (for the confirm step, and for anything you must not set yourself):
- Signature / postal address → `my.saleshandy.com` → **Settings → Email Accounts** → the mailbox →
  **Signature**. Per mailbox — a new domain's mailboxes start blank.
- Unsubscribe → open the sequence → **Settings** tab → **Unsubscribe** (link/text) and the
  **unsubscribe-via-email-header** toggle (code 13).

Turning code 13 on via `update_sequence_settings` is config, not a send — do it and report it. Setting
a mailbox signature is the operator's (it is their legal identity block).

**Config ≠ delivered.** Once per sending domain, the operator sends one live test to an address they
control and confirms the received mail carries the signature block and a `List-Unsubscribe` header.

Non-email channels (`LinkedInMessage`, `CallFollowUp`, `Custom`, `WhatsappMessage`, …) are supported
by `add_sequence_step` as **manual task steps** with a `taskNote`; use them only if the operator asks
for a multichannel arc. They create tasks, not automated sends.

## Quirks & error codes

- **40103** — "No active email attached" when resuming. Irrelevant to staging (we never resume), but
  it confirms a sequence can't send without an attached active account.
- **40201** — sending account not Active. Reconnect/warm it in Saleshandy first.
- **Enrollment is the PII step.** `add_leads_to_sequence` sends prospect identity + email to
  Saleshandy's servers. This is the consequential action in staging — confirm before it.
- **A step-scoped import upsert on an already-enrolled prospect silently no-ops** — see
  *Bulk CSV/array import* above. `isCompleted: true` is not proof a refresh applied; check
  `failedProspectsURL` every time.
- **`leadIds` are Saleshandy Lead Finder ids.** If prospects came from elsewhere (a prospect run
  CSV, the people ledger), they must first exist as Saleshandy leads/prospects (import via the
  provider's prospect-import path) before they can be enrolled. Flag this to the operator rather than
  guessing ids.
- **Schedule timezone** is validated against the IANA database up front — use e.g.
  `America/New_York`, `Asia/Singapore`, `Asia/Hong_Kong` (match the profile's `target_cities`).

## Capability contract — what the PRODUCT does, and what this workspace switched on

Two different facts, and conflating them is what let three real opt-outs sit unmirrored for six
weeks. A **capability** is a property of Saleshandy's product: the same for every tenant, changing
only when the vendor ships. A **configuration** is a toggle any human can flip in the vendor's UI
at any moment. Writing a configuration into a file records a boolean that was true once.

So capabilities live in [`gtm_core/sequencers.toml`](../../../../../gtm_core/sequencers.toml) —
cited, dated, and refusing to load without a `source` URL and a `verified_on` date — and
configuration is read live at every preflight. **This adapter deliberately does not restate the
values**; the registry is their one home, and a table here would be a second copy to drift.

```bash
uv run python -m gtm_core.sequencers saleshandy              # every capability, with its verdict
uv run python -m gtm_core.sequencers saleshandy stop_on_reply --json
```

The capabilities the preflight governs, and what each one costs if it is wrong:

| Capability | Why the preflight asserts it |
|---|---|
| `stop_on_reply` | With it off, follow-ups keep going after someone replies. The vendor is explicit: "Disabling this option will let the system send follow-ups to prospects even after they reply." |
| `ooo_auto_pause` | **Off is not neutral.** With it off, an out-of-office reply is marked *Replied* — which, with `stop_on_reply` on, finishes the prospect and ends the sequence. Silent lead loss with no local trace. |
| `reply_categories` | Advisory. The unified inbox's own label on a reply, used as a SECOND witness that may only make routing more conservative. |
| `reply_text_unsubscribe` | Advisory, and currently `unknown` — vendor docs say a "Stop"/"Remove" reply opts out; a dated first-party observation in this account says it did not. Until a live send settles it, our own matcher carries the load. |

Assert them as part of the compliance preflight — one command, one exit status, no second gate:

```bash
uv run python -m gtm_core.email_compliance preflight --profile <active> --provider saleshandy \
    --settings-json <(echo '{}') --sequence-id <sequence-id>
```

`--attest <capability>` is accepted **only** for a capability whose registry row records that the
setting cannot be read back, and only for that run — it renders as `ATTESTED`, never `PASS`,
because an operator confirming something is not the same evidence as reading it. Of the four words
the table prints — `PASS`, `ATTESTED`, `advisory`, `BLOCKS` — only the last one blocks.

The registry records six more capabilities that the preflight does **not** gate on. They are
recorded because a future session will otherwise "discover" one and read its absence as an
oversight rather than a decision:

| Capability | Status here |
|---|---|
| `dnc_read` | Used. `list_dnc_lists` → `get_dnc_items` back the suppression mirror and the daily `gtm-dnc-sync` reconcile. |
| `dnc_write_add` | Used, behind a gate, **add only**. A detected opt-out is drafted for a human and written by `agent/dnc_dispatch.py` after approval; `add_dnc_items` is denied to the brain on every connector, and every removal verb is denied in every context. |
| `webhooks` | Not used. A receiver is approved as a separate follow-on change; polling stays as the fallback either way. |
| `reply_send` | **The provider has it; this system must not.** `agent/reply.py` stays gated and inert by default, and no send tool exists on this wrapper. |
| `prospect_pause` | **The provider has it; deliberately out of scope.** Pausing a prospect is a second way to stop contact, and one gated write is enough to review. |
| `behaviour_branching` | Not used. Subsequences branch on behaviour (opened/clicked/replied), not on what a reply says — evaluated, not adopted. |
