-- V008: Push notification device tokens
--
-- Stores APNs / FCM device tokens per workspace + user.
-- Registered by the mobile client at login; deregistered on logout.
-- The /webhooks/revenuecat handler and the gate transition in runs.py
-- fan out to all tokens for the workspace when an action is needed.
--
-- RLS: follows the same workspace_id pattern as all other tenant tables.
-- current_workspace_id() helper and workspace_scope() are already in place.

CREATE TABLE IF NOT EXISTS push_tokens (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      UUID        NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    token        TEXT        NOT NULL,
    platform     TEXT        NOT NULL CHECK (platform IN ('apns', 'fcm')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, token)
);

CREATE INDEX IF NOT EXISTS push_tokens_workspace_idx ON push_tokens(workspace_id);

ALTER TABLE push_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY push_tokens_isolation ON push_tokens
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
