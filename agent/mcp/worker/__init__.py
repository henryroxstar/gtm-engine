"""DeepSeek worker MCP — a thin stdio MCP server over the DeepSeek REST API.

Exposes two tools the brain calls for cheap, bulk generation:

  - ``summarize(texts, style)`` — bulk-summarise a batch of source snippets
    (content-radar uses this to summarise clustered stories).
  - ``draft(brief, format)``    — first-draft copy from a brief (content-studio /
    content-research use this; Claude always reviews the result).

Model discipline (locked): the worker talks to DeepSeek with a PINNED model id
(``deepseek-chat``). DeepSeek is a downstream worker reached as an MCP tool — it
is NEVER the Agent SDK brain model. Run it with::

    python -m agent.mcp.worker --transport stdio

The Agent SDK spawns exactly this command (see :mod:`agent.mcp_config`), gated on
``DEEPSEEK_API_KEY``. The server process starts even without a valid key; a
missing/invalid key surfaces as a tool-call error string, never a crash — so a
worker outage degrades to "Claude does it itself", it does not take the bot down.
"""
