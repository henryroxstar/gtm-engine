-- V032: Tenant Contacts Migration
-- Moves contact pool rows (previously master-list.csv, ready-to-load.csv) to PostgreSQL.

CREATE TABLE tenant_contacts (
    id                      UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id            UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id              TEXT,
    pool_row_id             TEXT NOT NULL,
    email                   TEXT,
    first_name              TEXT,
    last_name               TEXT,
    title                   TEXT,
    company                 TEXT,
    company_domain          TEXT,
    city                    TEXT,
    country                 TEXT,
    segment                 TEXT,
    tier                    TEXT,
    score                   NUMERIC,
    conf                    NUMERIC,
    conf_tier               TEXT,
    email_status            TEXT,
    why_now                 TEXT,
    case_study              TEXT,
    src                     TEXT,
    heat                    TEXT,
    top_intent_score        NUMERIC,
    intent_topics           TEXT,
    cohort                  TEXT,
    qualification_path      TEXT,
    signal_source_url       TEXT,
    signal_observed         TEXT,
    signal_evidence         TEXT,
    signal_subject          TEXT,
    signal_agent_kind       TEXT,
    category_relation       TEXT,
    verdict                 TEXT,
    verdict_reason          TEXT,
    signal_column           TEXT,
    suppression             TEXT,
    suppression_date        TEXT,
    judge_verdict           TEXT,
    judge_verdict_reason    TEXT,
    judge_calibrated        TEXT,
    judge_defect_class      TEXT,
    lane                    TEXT,
    lane_reason             TEXT,
    raw_fields              JSONB DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now(),
    UNIQUE (workspace_id, pool_row_id)
);

CREATE UNIQUE INDEX tenant_contacts_email_idx ON tenant_contacts(workspace_id, lower(email)) 
    WHERE email IS NOT NULL AND email != '';
CREATE INDEX tenant_contacts_account_id_idx ON tenant_contacts(workspace_id, account_id);
CREATE INDEX tenant_contacts_lane_idx ON tenant_contacts(workspace_id, lane);
CREATE INDEX tenant_contacts_verdict_idx ON tenant_contacts(workspace_id, verdict);
CREATE INDEX tenant_contacts_company_domain_idx ON tenant_contacts(workspace_id, company_domain);

-- Enable and Force RLS
ALTER TABLE tenant_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_contacts FORCE ROW LEVEL SECURITY;

-- Tenant Isolation Policy
CREATE POLICY "tenant_isolation" ON tenant_contacts
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

