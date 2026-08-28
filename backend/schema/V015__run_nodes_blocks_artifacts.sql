-- V015: Run-event persistence + artifact registry (Track A A2 + A11)
--
-- The settled A2/A5 shared row contract (Track A PRD §A2, 2026-08-09), first half:
--   run_nodes  — per-node state, written at the runner's persistence points; the
--                protocol-1 snapshot's nodes[] is a read of this table, so a client
--                that (re)connects mid-run rebuilds the DAG without replay.
--   run_blocks — the upsert-by-id-collapsed content-block tree; snapshot content[]
--                is ORDER BY ord (first-insert order = the wire's arrival order).
--   (run_gates lands with A5 step 1; a run_events replay log with A5 step 3.)
--
-- run_artifacts (A11): one row per file deliverable a run registered. POINTER semantics — the row
-- records (path, sha, size) at registration; the file is a living document in the account tree.
-- rel_path is relative to the workspace content root and is NEVER accepted from a client (download
-- is by opaque id + server-side containment check). These rows point at the system's
-- highest-sensitivity PII (dossiers, prospect lists) — tenant isolation here follows the same RLS
-- discipline as every tenant table.

CREATE TABLE IF NOT EXISTS run_nodes (
    run_id       UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    node_id      TEXT        NOT NULL,
    -- Stored in the WIRE vocabulary (run-event.schema.json node.state) so snapshot
    -- reads emit valid frames without translation; a gate-paused node stores
    -- 'running' (the awaiting_approval event carries the gate detail).
    state        TEXT        NOT NULL
                             CHECK (state IN ('queued', 'running', 'completed',
                                              'failed', 'skipped')),
    error        TEXT,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    PRIMARY KEY (run_id, node_id)
);

CREATE TABLE IF NOT EXISTS run_blocks (
    run_id       UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    block_id     TEXT        NOT NULL,
    ord          INTEGER     NOT NULL,          -- first-insert order; snapshot content[] sorts on it
    node_id      TEXT,
    block        JSONB       NOT NULL,          -- one contentBlock (run-event.schema.json $defs)
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, block_id)
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    rel_path     TEXT        NOT NULL,          -- relative to the workspace content root; resolution key
    name         TEXT        NOT NULL,          -- display + attachment filename
    size_bytes   BIGINT      NOT NULL CHECK (size_bytes >= 0),
    media_type   TEXT        NOT NULL,          -- server-assigned from the extension allowlist
    sha256       TEXT        NOT NULL,          -- content hash at registration (provenance)
    node_id      TEXT,                          -- producing graph node, when attributable
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, rel_path)
);

CREATE INDEX IF NOT EXISTS run_nodes_ws_idx      ON run_nodes(workspace_id, run_id);
CREATE INDEX IF NOT EXISTS run_blocks_ws_idx     ON run_blocks(workspace_id, run_id, ord);
CREATE INDEX IF NOT EXISTS run_artifacts_ws_idx  ON run_artifacts(workspace_id, run_id, created_at);

-- RLS + FORCE + isolation policy — same pattern as every tenant table (V004/V009/V013).
ALTER TABLE run_nodes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_nodes     FORCE  ROW LEVEL SECURITY;
ALTER TABLE run_blocks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_blocks    FORCE  ROW LEVEL SECURITY;
ALTER TABLE run_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_artifacts FORCE  ROW LEVEL SECURITY;

CREATE POLICY run_nodes_isolation ON run_nodes
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE POLICY run_blocks_isolation ON run_blocks
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE POLICY run_artifacts_isolation ON run_artifacts
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON run_nodes     TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON run_blocks    TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON run_artifacts TO gtm_api;
