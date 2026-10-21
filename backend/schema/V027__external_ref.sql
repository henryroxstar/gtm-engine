-- V027: runs.external_ref — an optional external correlation id (G1).
--
-- Lets an outside control plane or caller correlate its own task ID with a
-- gtm-engine run without having to remember or map the server-generated run_id.
-- Opaque, max 128 chars, charset-restricted to [A-Za-z0-9._:-]+ at the API boundary.
-- Nullable: runs dispatched without external_ref carry NULL.
--
-- Index on (workspace_id, external_ref) supports fast lookup by external_ref
-- scoped to the workspace (GET /v1/runs?external_ref=...).

ALTER TABLE runs ADD COLUMN IF NOT EXISTS external_ref TEXT;

CREATE INDEX IF NOT EXISTS runs_workspace_external_ref_idx
    ON runs (workspace_id, external_ref);
