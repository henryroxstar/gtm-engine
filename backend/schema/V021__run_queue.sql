-- V021: durable run queue (Track A A5 step 2).
--
-- V017 laid the queue's foundations — the 'queued' status, runs_queued_idx, the
-- run_events log — and nothing has used them: dispatch is still a fire-and-forget
-- asyncio.create_task in the process that accepted POST /v1/runs. That process is
-- therefore a single point of failure for every run in flight, and the backend
-- cannot run more than one uvicorn worker. This migration adds the claim surface.
--
--   runs.payload — the real job record. Pack-mode runs currently encode their job
--     as the string `[pack] <pack>/<variant> inputs=<json>` in runs.prompt and parse
--     it back with a regex; that round trip LOSES dry_run, language, and
--     agent_budget_usd, so a reconciled run silently becomes a non-dry-run with no
--     agent cap. Today that is an edge case on the restart path — with a queue every
--     pack run is reconstructed from the row, which would promote the defect from
--     rare to universal. payload retires the regex; prompt keeps its audit-line role.
--
--   runs.claimed_by / claimed_at / heartbeat_at / attempts — lease bookkeeping.
--     heartbeat_at is LIVENESS, never elapsed time: a run may legitimately sit in
--     awaiting_approval for the full 24h gate timeout, so any "non-terminal for a
--     while" sweep would double-dispatch every gated run. attempts is incremented by
--     the claim itself, so a job that crashes its worker is bounded rather than
--     walking the queue as a crash loop.
--
--   claim_next_run() — FOR UPDATE SKIP LOCKED, one row, exactly once across workers.

-- ── lease + payload columns ──────────────────────────────────────────────────
-- Additive; no CHECK change is needed (V017 already legalised 'queued').

ALTER TABLE runs ADD COLUMN IF NOT EXISTS payload      JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS claimed_by   TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS claimed_at   TIMESTAMPTZ;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS attempts     INT NOT NULL DEFAULT 0;

-- The lease half of the claim predicate, mirroring V017's runs_queued_idx.
CREATE INDEX IF NOT EXISTS runs_lease_idx
    ON runs(heartbeat_at)
    WHERE status IN ('running', 'awaiting_approval');

-- ── the claim ────────────────────────────────────────────────────────────────
-- A queue claim is inherently CROSS-TENANT, and the runtime role gtm_api has no
-- BYPASSRLS and no workspace context — under FORCE ROW LEVEL SECURITY (V009) a
-- plain `SELECT ... FROM runs` here returns ZERO rows. That is not hypothetical:
-- it is exactly the bug V018 was written to fix, where startup reconciliation
-- resumed nothing on the deployed role. So this follows the same audited definer
-- pattern as awaiting_approval_runs() (V018) and release_stale_reservations()
-- (V013): SECURITY DEFINER, owned by gtm_bootstrap, pinned search_path, EXECUTE
-- granted only to gtm_api, returning only the columns the worker loop needs. The
-- caller re-enters workspace_scope(pool, workspace_id) for every claimed row, so a
-- cross-tenant row never leaves this function without re-entering a scoped
-- transaction.
--
-- prev_status is returned because the caller MUST distinguish a first dispatch
-- (prev_status = 'queued') from a lease reclaim: a reclaimed pack run resumes from
-- its durable manifest, while a reclaimed PROMPT run is unresumable (its SDK
-- session died with the worker) and is failed explicitly instead.
CREATE OR REPLACE FUNCTION claim_next_run(p_worker_id TEXT, p_lease_s INT)
RETURNS TABLE (id UUID, workspace_id UUID, profile_name TEXT, prompt TEXT,
               payload JSONB, agent_id UUID, prev_status TEXT, attempts INT)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH cand AS (
        SELECT r.id AS id, r.status AS prev_status
          FROM runs r
         WHERE r.status = 'queued'
            -- Liveness, never elapsed time. `heartbeat_at IS NOT NULL` also means a
            -- never-claimed row can only be picked up through the 'queued' branch,
            -- so a 'running' row written by a pre-V021 code path is never mistaken
            -- for an expired lease.
            OR (r.status IN ('running', 'awaiting_approval')
                AND r.heartbeat_at IS NOT NULL
                AND r.heartbeat_at < now() - make_interval(secs => p_lease_s))
         ORDER BY r.created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    )
    UPDATE runs r
       SET claimed_by   = p_worker_id,
           claimed_at   = now(),
           heartbeat_at = now(),
           attempts     = r.attempts + 1,
           -- Only 'queued' is promoted. A reclaimed gated run KEEPS
           -- awaiting_approval: it resumes into its gate loop and re-claims the
           -- durable run_gates decision, and promoting it to 'running' would lie to
           -- every client polling it.
           status       = CASE WHEN r.status = 'queued' THEN 'running' ELSE r.status END
      FROM cand
     WHERE r.id = cand.id
    RETURNING r.id, r.workspace_id, r.profile_name, r.prompt,
              r.payload, r.agent_id, cand.prev_status, r.attempts;
$$;

ALTER FUNCTION claim_next_run(TEXT, INT) OWNER TO gtm_bootstrap;

-- ── the lease heartbeat ──────────────────────────────────────────────────────
-- The other half of the claim: a worker proves it is alive by refreshing the leases
-- it holds. Cross-tenant for the same reason the claim is (a worker's runs span
-- whatever tenants it happened to pick up), hence the same definer pattern — and
-- scoped to p_worker_id, so a worker can only ever extend its OWN leases, never
-- keep another worker's dead run out of reach of the reclaimer.
CREATE OR REPLACE FUNCTION touch_worker_runs(p_worker_id TEXT)
RETURNS INT
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH beat AS (
        UPDATE runs
           SET heartbeat_at = now()
         WHERE claimed_by = p_worker_id
           AND status IN ('running', 'awaiting_approval')
        RETURNING 1
    )
    SELECT count(*)::int FROM beat;
$$;

ALTER FUNCTION touch_worker_runs(TEXT) OWNER TO gtm_bootstrap;

-- V018 granted gtm_bootstrap SELECT on runs for its definer body; both functions
-- above also write, so it needs UPDATE.
GRANT UPDATE ON runs TO gtm_bootstrap;

REVOKE ALL     ON FUNCTION claim_next_run(TEXT, INT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION claim_next_run(TEXT, INT) TO gtm_api;
REVOKE ALL     ON FUNCTION touch_worker_runs(TEXT)   FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION touch_worker_runs(TEXT)   TO gtm_api;
