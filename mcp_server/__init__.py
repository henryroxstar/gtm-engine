"""GTM MCP server — Phase E paid developer surface.

Exposes read/draft/research tools only (no publish/PRODUCTION at launch).
Auth: API key → workspace + entitlement (V002 api_keys table).
Metering: every inference call logged to mcp_calls (V006).
"""
