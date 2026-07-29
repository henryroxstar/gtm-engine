"""Saleshandy worker MCP — a thin stdio MCP server over the Saleshandy REST API.

Sequence **staging** for the ``email-sequence`` skill: create a cold-email sequence,
add steps and A/B variants, define a sending schedule, attach sending mailboxes, and
enroll leads/prospects into a step — plus read back mailboxes, sequences, and
per-sequence stats, and (read-only) the unified-inbox reply threads that feed
``inbound-triage``.

Exposes these tools the brain calls (staging + read ONLY — never send/reply):

  Read:
  - ``list_email_accounts(...)`` — the workspace's sending mailboxes; also the auth
    smoke test (cheapest read → "is the key valid?").
  - ``list_sequences(...)`` — the workspace's sequences and their steps.
  - ``get_sequence_stats(sequence_id)`` — prospect + email stats for one sequence.
  - ``get_inbox_threads(...)`` — unified-inbox reply threads (read-only) for the
    ``inbound-triage`` skill.
  - ``get_thread(thread_id)`` — one reply thread's messages (read-only).

  Staging (build only — a built sequence is inert until a human resumes it):
  - ``create_sequence(title, email_account_ids, schedule_id)``
  - ``add_sequence_step(sequence_id, step_type, absolute_days, variants, …)``
  - ``add_step_variant(sequence_id, step_id, step_type, payload, …)``
  - ``create_schedule(name, timezone, time_slots, is_default)``
  - ``add_email_accounts_to_sequence(sequence_id, email_account_ids)``
  - ``add_leads_to_sequence(lead_ids, sequence_id, step_id, …)`` — enroll Lead Finder
    leads into a step.
  - ``import_prospects_to_sequence(prospect_list, step_id, …)`` — the prospect-import
    counterpart for raw email prospects.

CRITICAL SECURITY INVARIANT: this wrapper makes it **structurally impossible for the
brain to cause Saleshandy to send email** — the email analogue of the publish gate
(*build is a capability, send is not*). Sending is triggered by resuming/activating a
sequence; there is deliberately NO activate/resume/pause/status tool and NO
delete/revoke tool. The inbox tools are read-only — there is deliberately NO reply/send
tool (a reply is a ``⟦GATE:reply⟧`` artifact the operator approves). A sequence built
here is inert until a human resumes it in the Saleshandy UI. See
:mod:`agent.mcp.saleshandy.server` and
``docs/prds/2026-07-13-email-sequence.md``.

Boundary (§R6 — all external I/O via MCP): the brain passes parameters in and gets
structured data back; it never sees ``SALESHANDY_API_KEY`` and never makes the HTTP
call. The key is read from the process env (Doppler-injected at spawn), never on the
command line, never echoed.

No cost metering: Saleshandy is a flat subscription with no finite per-call unit, so —
unlike the RocketReach worker — this server writes NO cost ledger record.

Robustness contract: every tool returns a string. On any failure (no key, HTTP error,
malformed body) it returns a ``[saleshandy-error] …`` string rather than raising — so a
worker outage never breaks the run or the SDK ↔ MCP connection.

Run it with::

    python -m agent.mcp.saleshandy --transport stdio
"""
