-- V033: Tenant Suppression Migration
-- Moves durable local suppression lists (previously suppression.csv) to PostgreSQL.

CREATE TABLE tenant_suppression (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email           TEXT,
    name            TEXT,
    company_domain  TEXT,
    reason          TEXT NOT NULL,
    date            TEXT,
    note            TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX tenant_suppression_email_idx ON tenant_suppression(workspace_id, lower(email))
    WHERE email IS NOT NULL AND email != '';
CREATE INDEX tenant_suppression_person_idx ON tenant_suppression(workspace_id, lower(name), lower(company_domain))
    WHERE name IS NOT NULL AND company_domain IS NOT NULL;
CREATE INDEX tenant_suppression_reason_idx ON tenant_suppression(workspace_id, reason);

-- Enable and Force RLS
ALTER TABLE tenant_suppression ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_suppression FORCE ROW LEVEL SECURITY;

-- Tenant Isolation Policy
CREATE POLICY "tenant_isolation" ON tenant_suppression
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

