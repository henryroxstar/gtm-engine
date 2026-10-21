"""Vision worker MCP — a thin stdio MCP server over the Anthropic Messages API.

Exposes one tool the brain calls for cheap image→text extraction:

  - ``extract_text(image_path, instructions)`` — OCR/extract the text in an image
    file using a PINNED cheap model, returning the extracted text for the brain to
    reason over. The LinkedIn skills use this to read a screenshot of a reactions /
    comments list without spending the (expensive) brain model's vision tokens.

Model discipline (locked): the worker talks to Anthropic with a PINNED model id
(``claude-haiku-4-5``) — the cheapest vision-capable Claude model. It is a
downstream worker reached as an MCP tool for a narrow perception task; the brain
(Sonnet/Opus) still reasons over and reviews whatever text it returns. This is the
same pattern as the DeepSeek worker (``agent/mcp/worker``), and stays inside the
"brain is always a Claude model" rule — the worker is just a cheaper Claude model
doing OCR. Run it with::

    python -m agent.mcp.vision --transport stdio

The Agent SDK spawns exactly this command (see :mod:`agent.mcp_config`), gated on
``ANTHROPIC_API_KEY``. The server process starts even without a valid key; a
missing/invalid key surfaces as a tool-call error string, never a crash — so a
worker outage degrades to "the brain reads the image itself", it does not take the
bot down.

Reach (local-only, by construction): the tool reads an image **file on disk** and
sends it to Anthropic. It only helps when the image is a file the worker process
can open — a screenshot pasted directly into chat is already in the brain's
context and cannot be handed off. The Telegram cockpit does not yet forward images
to disk, so today this is a local-session capability.
"""
