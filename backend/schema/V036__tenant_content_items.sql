-- V036: Tenant Content Items Migration
-- Unifies content plans, staged drafts, and assets into PostgreSQL.

CREATE TABLE tenant_content_items (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    item_id         TEXT NOT NULL,
    plan_id         TEXT,
    pillar          TEXT,
    story_id        TEXT,
    platform        TEXT NOT NULL,
    format          TEXT,
    slot            TEXT,
    status          TEXT NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'draft', 'review', 'approved', 'published', 'archived', 'cancelled')),
    hook_id         TEXT,
    hook            TEXT,
    body            TEXT,
    brief           JSONB DEFAULT '{}'::jsonb,
    asset_refs      TEXT[] DEFAULT '{}',
    meta            JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (workspace_id, item_id)
);

CREATE INDEX tenant_content_items_plan_idx ON tenant_content_items(workspace_id, plan_id);
CREATE INDEX tenant_content_items_status_idx ON tenant_content_items(workspace_id, status);
CREATE INDEX tenant_content_items_platform_idx ON tenant_content_items(workspace_id, platform);
CREATE INDEX tenant_content_items_hook_idx ON tenant_content_items(workspace_id, hook_id);

-- Enable and Force RLS
ALTER TABLE tenant_content_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_content_items FORCE ROW LEVEL SECURITY;

-- Tenant Isolation Policy
CREATE POLICY "tenant_isolation" ON tenant_content_items
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

