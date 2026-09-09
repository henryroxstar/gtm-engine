-- V006: MCP call log — usage tracking for the Phase E MCP server
--
-- Records every tool call that consumed inference tokens (PIPELINE tier).
-- CORE tool calls (radar_check, profile_context) are free and not logged here
-- since they consume no inference budget.
--
-- Used for:
--   - Monthly cost cap enforcement (meter.py pre-call guard)
--   - Usage dashboard in the mobile app / billing webhook
--   - Rate limiting foundation (residual D-02)

CREATE TABLE IF NOT EXISTS mcp_calls (
    id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       UUID          NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    api_key_id         UUID          NOT NULL REFERENCES api_keys(id)   ON DELETE CASCADE,
    tool_name          TEXT          NOT NULL,
    profile_name       TEXT          NOT NULL,
    model              TEXT          NOT NULL,
    prompt_tokens      INTEGER       NOT NULL DEFAULT 0,
    completion_tokens  INTEGER       NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10,6) NOT NULL DEFAULT 0,
    called_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mcp_calls_workspace_month_idx
    ON mcp_calls(workspace_id, called_at DESC);

CREATE INDEX IF NOT EXISTS mcp_calls_key_idx
    ON mcp_calls(api_key_id, called_at DESC);

-- RLS: workspace_id scoping (same pattern as all other tables)
ALTER TABLE mcp_calls ENABLE ROW LEVEL SECURITY;

CREATE POLICY mcp_calls_isolation ON mcp_calls
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
