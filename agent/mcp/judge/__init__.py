"""Email judge MCP — a thin stdio MCP server over the Anthropic Messages API.

Exposes one tool the brain calls to score a staged sequence row by row:

  - ``score_emails(spec_path, csv_path, out_path, profile, ...)`` — render every row
    against the spec, ask a PINNED cheap model (``claude-haiku-4-5``, registry role
    ``judge``) whether the operator would send it, and write one
    :class:`gtm_core.adjudication.Adjudication` record per row as JSONL.

**Why an MCP server and not a function in ``gtm_core``.** The judge needs a per-row model
call over 400 PII-bearing rows. That cannot be the in-session brain (wrong tier, wrong
cost), and it cannot be a Python module in ``gtm_core`` making HTTP calls — that is a new
egress path, which §R6 forbids. ``agent/mcp/vision`` already solved this exact shape: a
FastMCP stdio server running a cheap Claude model as a downstream worker, reached as a
tool. This mirrors it. No new egress path, no new allowlist entry, and the registry role
``[roles.judge]`` — added 2026-08-21 and inert ever since — finally has a consumer.

**The judge ranks; it never blocks.** :mod:`gtm_core.adjudication`'s contract is explicit —
*"this is not a gate and must not become one"* — and nothing here changes that. This server
writes a ``verdict`` **data column**; the deterministic
``account_integrity --require-verdict send`` is what refuses a row at enrollment. Same
division of labour as the publish gate: a model produces the bytes, deterministic code
decides whether they move.

Model discipline (locked): the role resolves through :func:`gtm_core.models.resolve_model`,
and CLAUDE.md binds it to a Claude model because judged rows carry prospect PII — the
DeepSeek ``worker_draft`` path is prohibited here. Run it with::

    python -m agent.mcp.judge --transport stdio

The Agent SDK spawns exactly this command (see :mod:`agent.mcp_config`), gated on
``ANTHROPIC_API_KEY``. A missing/invalid key surfaces as a tool-call error string, never a
crash — a judge outage degrades to "the operator reads the list themselves", which is the
pre-2026-08-22 status quo, not an outage of the pipeline.

**Untrusted input (§R5).** Rendered bodies carry scraped ``why_now`` clauses and company
names — prospect-controlled text. The rubric prompt frames every rendered email as DATA to
be judged and never as instructions, and the parser accepts only a fixed verdict
vocabulary, so text inside a body cannot promote itself to a verdict.
"""
