"""Entry point: python -m mcp_server

Starts the GTM MCP server using FastMCP's streamable-http transport.
All configuration via environment variables:

  DATABASE_URL      — asyncpg DSN (required)
  DEEPSEEK_API_KEY  — required for PIPELINE tools (draft_post, draft_outreach)
  MCP_PORT          — HTTP port (default: 8001)
  MCP_HOST          — bind host (default: 0.0.0.0)

API key for callers: Authorization: Bearer sk-<key>
(or set MCP_API_KEY env var for local dev / testing)
"""

from .server import mcp

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
