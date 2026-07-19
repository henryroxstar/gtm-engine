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
- **One-click unsubscribe header** (CAN-SPAM + Google/Yahoo bulk-sender + SG Spam Control Act) → `[{"code":13,"value":"1"}]`.
- CC/BCC values are **per-operator PII** — pass them at runtime (or from env); **never hardcode into committed config.**

Non-email channels (`LinkedInMessage`, `CallFollowUp`, `Custom`, `WhatsappMessage`, …) are supported
by `add_sequence_step` as **manual task steps** with a `taskNote`; use them only if the operator asks
for a multichannel arc. They create tasks, not automated sends.

## Quirks & error codes

- **40103** — "No active email attached" when resuming. Irrelevant to staging (we never resume), but
  it confirms a sequence can't send without an attached active account.
- **40201** — sending account not Active. Reconnect/warm it in Saleshandy first.
- **Enrollment is the PII step.** `add_leads_to_sequence` sends prospect identity + email to
  Saleshandy's servers. This is the consequential action in staging — confirm before it.
- **`leadIds` are Saleshandy Lead Finder ids.** If prospects came from elsewhere (a prospect run
  CSV, the people ledger), they must first exist as Saleshandy leads/prospects (import via the
  provider's prospect-import path) before they can be enrolled. Flag this to the operator rather than
  guessing ids.
- **Schedule timezone** is validated against the IANA database up front — use e.g.
  `America/New_York`, `Asia/Singapore`, `Asia/Hong_Kong` (match the profile's `target_cities`).
