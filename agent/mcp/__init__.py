"""Runtime MCP servers authored in-repo (as opposed to off-the-shelf npx servers).

Currently houses the DeepSeek ``worker`` MCP (:mod:`agent.mcp.worker`) — a thin
stdio server that exposes DeepSeek as a downstream generation tool. The brain
(Claude, via the Agent SDK) calls it for bulk first-draft / summary work and then
reviews the output. DeepSeek is NEVER the SDK brain model — see
:mod:`agent.mcp_config` for how this server is wired and gated on
``DEEPSEEK_API_KEY``.
"""
