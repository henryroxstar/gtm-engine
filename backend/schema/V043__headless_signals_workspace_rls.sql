-- V043: Bring headless_signals into the workspace RLS spine.
--
-- V041 (2026-09-20) created headless_signals keyed on `profile_name TEXT` — the
-- single-tenant VPS concept — with no ENABLE/FORCE ROW LEVEL SECURITY and no
-- workspace column, making it the only tenant-carrying table outside the spine.
-- backend/services/signals/queue.py read and wrote it through a bare
-- `pool.acquire()`, so nothing scoped a row to a tenant, and `claim_next_signal()`
-- claimed the oldest pending row across EVERY profile.
--
-- It was never caught because tests/backend/test_rls_live.py checks
-- relforcerowsecurity against a HARDCODED table list (a new table is invisible to
-- it) and lives in the dbtest tier, which self-skips wherever GTM_TEST_PG_ADMIN_DSN
-- is unset. tests/contracts/test_every_tenant_table_has_rls.py now derives the list
-- from this directory instead, so the next unarmed table fails without a database.
--
-- Safe on an empty table and LOUD on a populated one: the table is unwired
-- (`enqueue_signal` has no caller outside its own package), so there should be no
-- rows — but a NULL workspace_id under RLS would be a row no tenant can ever see
-- again, so this refuses to proceed rather than stranding one.
--
-- `profile_name` is KEPT alongside `workspace_id`, exactly as `runs` carries both:
-- the workspace is the tenant boundary, the profile names which persona inside it.

-- ── 1. the workspace column ──────────────────────────────────────────────────

ALTER TABLE headless_signals ADD COLUMN IF NOT EXISTS workspace_id UUID;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM headless_signals WHERE workspace_id IS NULL) THEN
        RAISE EXCEPTION
            'headless_signals holds % row(s) with no workspace_id. This table was '
            'unwired, so rows here are unexpected: map them to a workspace by hand, '
            'or delete them, then re-run. Leaving them NULL under RLS would make '
            'them permanently invisible.',
            (SELECT count(*) FROM headless_signals WHERE workspace_id IS NULL);
    END IF;
END $$;

ALTER TABLE headless_signals ALTER COLUMN workspace_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'headless_signals_workspace_id_fkey'
    ) THEN
        ALTER TABLE headless_signals
            ADD CONSTRAINT headless_signals_workspace_id_fkey
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── 2. idempotency is PER WORKSPACE, not global ──────────────────────────────
-- V041 declared `idempotency_key TEXT NOT NULL UNIQUE`. Global uniqueness across
-- tenants is both wrong and a leak: under RLS the conflicting row belongs to
-- another workspace and is invisible, so tenant B enqueueing a key tenant A
-- already used hits a unique violation it cannot see or resolve — and the failure
-- itself reveals that some other tenant used that key. ON CONFLICT DO UPDATE
-- cannot reach the hidden row either, so the insert simply fails.

ALTER TABLE headless_signals DROP CONSTRAINT IF EXISTS headless_signals_idempotency_key_key;

CREATE UNIQUE INDEX IF NOT EXISTS headless_signals_ws_idem_idx
    ON headless_signals(workspace_id, idempotency_key);

-- A tenant reading its own queue; the claim's own indexes stay cross-tenant
-- (V041) because the claim deliberately spans workspaces.
CREATE INDEX IF NOT EXISTS headless_signals_ws_status_idx
    ON headless_signals(workspace_id, status);

-- ── 3. arm RLS ───────────────────────────────────────────────────────────────
-- ENABLE alone leaves the table OWNER exempt, which is what V009 exists to fix;
-- FORCE is what makes `SET LOCAL app.current_workspace_id` actually filter.

ALTER TABLE headless_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE headless_signals FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "tenant_isolation" ON headless_signals;
CREATE POLICY "tenant_isolation" ON headless_signals
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- ── 4. the claim stays cross-tenant, and now hands back the workspace ────────
-- A worker claims whatever tenant's signal is next, so the claim cannot run under
-- workspace_scope — under FORCE RLS `gtm_api` would see zero rows. This is the
-- same audited definer pattern as claim_next_run (V021), awaiting_approval_runs
-- (V018) and release_stale_reservations (V013): SECURITY DEFINER, owned by
-- gtm_bootstrap, pinned search_path, EXECUTE granted only to gtm_api, returning
-- only what the worker loop needs — now including workspace_id, so the caller can
-- re-enter workspace_scope(pool, workspace_id) for every subsequent statement. A
-- cross-tenant row never leaves this function without re-entering a scoped
-- transaction.

-- DROP before CREATE: this adds workspace_id to the RETURNS TABLE, and
-- CREATE OR REPLACE cannot change a function's return type ("Row type defined by
-- OUT parameters is different"). The DROP is safe because the grants below are
-- re-applied in the same migration, and nothing calls this function between the
-- two statements — the whole file runs in one migration transaction.
DROP FUNCTION IF EXISTS claim_next_signal(TEXT, INT);

CREATE FUNCTION claim_next_signal(p_worker_id TEXT, p_lease_s INT DEFAULT 90)
RETURNS TABLE (
    id UUID,
    workspace_id UUID,
    profile_name TEXT,
    signal_type TEXT,
    payload_ref TEXT,
    idempotency_key TEXT,
    attempts INT,
    status TEXT
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH cand AS (
        SELECT s.id AS id
          FROM headless_signals s
         WHERE s.status = 'pending'
            OR (s.status = 'processing'
                AND s.claimed_at IS NOT NULL
                AND s.claimed_at < now() - make_interval(secs => p_lease_s))
         ORDER BY s.created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    )
    UPDATE headless_signals s
       SET claimed_by   = p_worker_id,
           claimed_at   = now(),
           attempts     = s.attempts + 1,
           status       = 'processing',
           updated_at   = now()
      FROM cand
     WHERE s.id = cand.id
    RETURNING s.id, s.workspace_id, s.profile_name, s.signal_type, s.payload_ref,
              s.idempotency_key, s.attempts, s.status;
$$;

ALTER FUNCTION claim_next_signal(TEXT, INT) OWNER TO gtm_bootstrap;

REVOKE ALL ON FUNCTION claim_next_signal(TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_next_signal(TEXT, INT) TO gtm_api;

-- Verify after applying:
--   SELECT relname, relrowsecurity, relforcerowsecurity
--     FROM pg_class WHERE relname = 'headless_signals';
--   -- expect (headless_signals, t, t)
