-- V031: Tenant Accounts Migration
-- Moves prospect accounts (previously latest.json) to PostgreSQL for multi-node backend access.

CREATE TABLE tenant_accounts (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id      TEXT NOT NULL,           -- e.g., a-<hex10>
    domain          TEXT,
    company_name    TEXT NOT NULL,
    slug            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','contact-resolved','contact-defective','disqualified',
                           'replied','meeting','engaged','in-conversation',
                           'customer','partner','do-not-contact','closed-lost')),
    tier            TEXT,
    score           NUMERIC,
    priority        TEXT,
    owner           TEXT,
    segment         TEXT,
    why_now         TEXT,
    signals         JSONB DEFAULT '{}'::jsonb,
    notes           TEXT,
    source_run      TEXT,
    last_touched    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (workspace_id, account_id)
);

CREATE INDEX tenant_accounts_slug_idx ON tenant_accounts(workspace_id, slug);
CREATE INDEX tenant_accounts_domain_idx ON tenant_accounts(workspace_id, domain);
CREATE INDEX tenant_accounts_status_idx ON tenant_accounts(workspace_id, status);

-- Enable and Force RLS
ALTER TABLE tenant_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_accounts FORCE ROW LEVEL SECURITY;

-- Tenant Isolation Policy
CREATE POLICY "tenant_isolation" ON tenant_accounts
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

