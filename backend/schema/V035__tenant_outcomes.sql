-- V035: Tenant Outcomes Migration
-- Moves closed-loop outcomes tracking (previously outcomes.jsonl) to PostgreSQL.

CREATE TABLE tenant_outcomes (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel         TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    ref             TEXT,
    account_slug    TEXT,
    tags            TEXT[] DEFAULT '{}',
    value           NUMERIC NOT NULL DEFAULT 1,
    meta            JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX tenant_outcomes_channel_outcome_idx ON tenant_outcomes(workspace_id, channel, outcome);
CREATE INDEX tenant_outcomes_ref_idx ON tenant_outcomes(workspace_id, ref);
CREATE INDEX tenant_outcomes_account_idx ON tenant_outcomes(workspace_id, account_slug);
CREATE INDEX tenant_outcomes_ts_idx ON tenant_outcomes(workspace_id, ts);

-- Enable and Force RLS
ALTER TABLE tenant_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_outcomes FORCE ROW LEVEL SECURITY;

-- Tenant Isolation Policy
CREATE POLICY "tenant_isolation" ON tenant_outcomes
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

