-- V005: Pipeline runs table
-- Stores every run triggered via the backend API. The background task in
-- backend/routers/runs.py writes to this table; mobile clients poll it.
--
-- status lifecycle:
--   pending → running → awaiting_approval (at each gate) → ok
--                                      ↓                 ↓
--                                  rejected            failed

CREATE TABLE IF NOT EXISTS runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    profile_name     TEXT        NOT NULL,
    prompt           TEXT        NOT NULL,
    dry_run          BOOLEAN     NOT NULL DEFAULT false,
    status           TEXT        NOT NULL DEFAULT 'pending'
                                   CHECK (status IN (
                                     'pending', 'running', 'awaiting_approval',
                                     'ok', 'failed', 'rejected'
                                   )),
    -- Output accumulates as the pipeline stages complete
    output           TEXT,
    error            TEXT,
    -- Gate pause state
    pending_gate     TEXT,
    pending_content  TEXT,
    -- Timestamps
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS runs_workspace_created_idx
    ON runs(workspace_id, created_at DESC);

-- RLS: workspace_id scoping (same pattern as V004 — policy co-located with table)
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY runs_isolation ON runs
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
