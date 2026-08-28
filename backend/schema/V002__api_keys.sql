-- V002: API keys — for MCP server (Phase E) and direct backend access
--
-- The raw key is shown exactly ONCE at creation time and is never stored.
-- Only the SHA-256 hash is persisted. The prefix (first 8 chars) is stored
-- for display in the dashboard ("sk-abc123...").
--
-- entitlement on the key can be at most the workspace's subscription entitlement
-- (enforced by the backend service layer, not a DB constraint — the DB constraint
-- would require a cross-table check which complicates Stripe webhook atomicity).
-- A key with entitlement='pro' under a pro_plus workspace works; a key cannot
-- exceed what the workspace is entitled to.

CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    key_hash      TEXT        NOT NULL UNIQUE,   -- sha256(raw_key), hex-encoded
    prefix        TEXT        NOT NULL,          -- first 8 chars of raw key (display only)
    label         TEXT,                          -- human-assigned name ("MCP prod key")
    entitlement   TEXT        NOT NULL DEFAULT 'pro'
                                CHECK (entitlement IN ('free', 'pro', 'pro_plus')),
    last_used_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS api_keys_workspace_idx ON api_keys(workspace_id);

-- Partial index for fast active-key lookup (the hot path on every MCP call).
-- The predicate must be IMMUTABLE, so it can only filter on revoked_at (now() is
-- not allowed in an index predicate). Expiry is filtered at query time — see
-- mcp_server/auth.py: WHERE key_hash=$1 AND revoked_at IS NULL
--   AND (expires_at IS NULL OR expires_at > now()). The index serves the key_hash
-- equality + non-revoked predicate; the expiry check is a cheap residual filter.
CREATE INDEX IF NOT EXISTS api_keys_active_idx
    ON api_keys(key_hash)
    WHERE revoked_at IS NULL;
