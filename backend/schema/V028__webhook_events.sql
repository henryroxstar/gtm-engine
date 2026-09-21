-- V028: webhook_events — deduplication and replay protection for external webhooks.
--
-- Tracks incoming webhook events by workspace, provider, and event_id.
-- Duplicate deliveries within the 72-hour window are acknowledged without
-- triggering duplicate pack runs.

CREATE TABLE IF NOT EXISTS webhook_events (
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider      TEXT        NOT NULL,
    event_id      TEXT        NOT NULL,
    run_id        UUID        REFERENCES runs(id) ON DELETE SET NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, provider, event_id)
);

CREATE INDEX IF NOT EXISTS webhook_events_workspace_idx
    ON webhook_events(workspace_id);

CREATE INDEX IF NOT EXISTS webhook_events_received_at_idx
    ON webhook_events(received_at);

ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE  ROW LEVEL SECURITY;

CREATE POLICY webhook_events_isolation ON webhook_events
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON webhook_events TO gtm_api;
