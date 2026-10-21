-- V025: runs.client_request_id — an optional client-chosen idempotency key (RT-04).
--
-- POST /v1/runs had no idempotency mechanism: a fresh uuid4() per request, unconditional
-- INSERT. A mobile client that retries after a timeout (the realistic case on a 202 whose
-- response was lost) creates a DUPLICATE queued run that also consumes one of the 3
-- concurrent-run slots and later spends budget twice.
--
--   runs.client_request_id — a bare, client-supplied string (shape-validated at the
--     schema boundary, backend/schemas.py's RunRequest, 1-128 chars). Nullable: every
--     pre-RT-04 row, and every request that never supplies one, carries NULL forever.
--
--   runs_workspace_client_request_id_uq — a UNIQUE index on (workspace_id,
--     client_request_id), scoped by a PARTIAL "WHERE client_request_id IS NOT NULL"
--     predicate. That predicate is the whole point: Postgres treats NULL <> NULL, so an
--     ordinary (non-partial) unique index would already let unlimited NULLs coexist —
--     but the partial form is kept explicit here anyway so the intent ("this constrains
--     only rows that opted in") reads directly off the DDL, and so admission.py's
--     `INSERT ... ON CONFLICT (workspace_id, client_request_id) WHERE client_request_id
--     IS NOT NULL DO NOTHING` has a matching partial-index conflict target to name (an
--     ON CONFLICT clause over a partial unique index must repeat its WHERE predicate).
--
-- Additive and idempotent (IF NOT EXISTS on both statements, V021's pattern). No RLS/grant
-- change: the column and index live on a table gtm_api already reads and writes under
-- FORCE RLS (V009).

ALTER TABLE runs ADD COLUMN IF NOT EXISTS client_request_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS runs_workspace_client_request_id_uq
  ON runs (workspace_id, client_request_id) WHERE client_request_id IS NOT NULL;
