-- V017: Durable gates + run queue states + append-only run events (Track A A5)
--
--   run_gates — persists BOTH the wait AND the decision (the settled A2/A5 row
--     contract). Today a restart mid-gate kills the run (in-memory asyncio.Event)
--     and a decision posted while the runner is down is lost outright. With this
--     row, startup reconciliation replays a decided gate and re-opens an undecided
--     one. PK (run_id, gate, node_id) — a run may gate more than once per node
--     only under a different gate kind, which is exactly the uniqueness we want.
--
--   runs.status CHECK += 'queued' | 'canceled' — the queue state (A5 step 2:
--     FOR UPDATE SKIP LOCKED replaces fire-and-forget task spawn) and the
--     explicit cancel terminal. Additive: every existing value stays legal, so
--     pre-V017 rows and the current code paths are unaffected.
--
--   run_events — append-only event log with a global bigserial; per-run order is
--     id order. This is the per-run `seq` that upgrades A2's snapshot-resync
--     reconnect to true replay (advertised by a later protocol bump — the table
--     lands first so events accumulate before any client depends on them).
--     V011's append-only grant pattern: SELECT + INSERT + DELETE (retention),
--     never UPDATE.

-- ── run_gates ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS run_gates (
    run_id         UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    gate           TEXT        NOT NULL CHECK (gate IN ('plan', 'publish')),
    node_id        TEXT        NOT NULL DEFAULT '',
    workspace_id   UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- sha256 of the EXACT pending bytes the operator was shown; a decision is
    -- bound to it (H9), so a stale decision can never resolve a later gate.
    content_sha    TEXT,
    state          TEXT        NOT NULL DEFAULT 'open'
                                 CHECK (state IN ('open', 'approved', 'edited', 'rejected')),
    edited_content TEXT,
    opened_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at     TIMESTAMPTZ,
    -- Set when a decided row has been consumed by the runner, so reconciliation
    -- (and a racing in-process waiter) can never apply the same decision twice.
    applied_at     TIMESTAMPTZ,
    PRIMARY KEY (run_id, gate, node_id)
);

CREATE INDEX IF NOT EXISTS run_gates_ws_idx ON run_gates(workspace_id);
-- The reconciliation sweep's read: decided-but-unapplied rows, newest first.
CREATE INDEX IF NOT EXISTS run_gates_pending_apply_idx
    ON run_gates(decided_at)
    WHERE state <> 'open' AND applied_at IS NULL;

ALTER TABLE run_gates ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_gates FORCE ROW LEVEL SECURITY;

CREATE POLICY run_gates_isolation ON run_gates
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON run_gates TO gtm_api;

-- ── runs: queue + cancel states ──────────────────────────────────────────────
-- Additive widening of the existing CHECK (V005). Drop-and-recreate is the only
-- way to widen a CHECK; both statements are idempotent-safe on re-run.

ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check
    CHECK (status IN (
      'pending', 'queued', 'running', 'awaiting_approval',
      'ok', 'failed', 'rejected', 'canceled'
    ));

-- Claim query for the A5 step-2 worker loop (FOR UPDATE SKIP LOCKED).
CREATE INDEX IF NOT EXISTS runs_queued_idx
    ON runs(created_at)
    WHERE status = 'queued';

-- ── run_events (append-only) ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS run_events (
    id           BIGSERIAL   PRIMARY KEY,
    run_id       UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event        TEXT        NOT NULL,
    data         JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Replay read: everything after a client's last seen id, in id order.
CREATE INDEX IF NOT EXISTS run_events_run_id_idx ON run_events(run_id, id);
CREATE INDEX IF NOT EXISTS run_events_ws_idx ON run_events(workspace_id);

ALTER TABLE run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_events FORCE ROW LEVEL SECURITY;

CREATE POLICY run_events_isolation ON run_events
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- Append-only (V011 pattern): no UPDATE — an emitted event is immutable.
-- DELETE is granted for retention pruning only.
GRANT SELECT, INSERT, DELETE ON run_events TO gtm_api;
REVOKE UPDATE ON run_events FROM gtm_api;
GRANT USAGE, SELECT ON SEQUENCE run_events_id_seq TO gtm_api;
