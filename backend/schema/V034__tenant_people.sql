-- V034: Tenant People & Engagements Migration
-- Moves CRM people records and engagement logs (previously people.json) to PostgreSQL.

CREATE TABLE tenant_people (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    person_id           TEXT NOT NULL,
    name                TEXT,
    headline            TEXT,
    company             TEXT,
    profile_url         TEXT,
    tags                TEXT[] DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'lead'
                        CHECK (status IN ('lead', 'engaged', 'replied', 'opportunity', 'account', 'disqualified')),
    linked_account      TEXT,
    first_seen          TIMESTAMPTZ,
    last_seen           TIMESTAMPTZ,
    engagement_count    INT NOT NULL DEFAULT 0,
    raw_fields          JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (workspace_id, person_id)
);

CREATE INDEX tenant_people_status_idx ON tenant_people(workspace_id, status);
CREATE INDEX tenant_people_account_idx ON tenant_people(workspace_id, linked_account);

-- Enable and Force RLS for tenant_people
ALTER TABLE tenant_people ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_people FORCE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON tenant_people
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE TABLE tenant_engagements (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    person_id       TEXT NOT NULL,
    type            TEXT NOT NULL,
    post_url        TEXT,
    date            TIMESTAMPTZ,
    notes           TEXT,
    meta            JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now(),
    FOREIGN KEY (workspace_id, person_id) REFERENCES tenant_people(workspace_id, person_id) ON DELETE CASCADE
);

CREATE INDEX tenant_engagements_person_idx ON tenant_engagements(workspace_id, person_id);
CREATE INDEX tenant_engagements_type_idx ON tenant_engagements(workspace_id, type);

-- Enable and Force RLS for tenant_engagements
ALTER TABLE tenant_engagements ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_engagements FORCE ROW LEVEL SECURITY;

CREATE POLICY "tenant_isolation" ON tenant_engagements
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

