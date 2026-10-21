-- V024: a gate wait's lease reclaims don't burn MAX_ATTEMPTS (RL-17).
--
-- claim_next_run() (V021) does `attempts = r.attempts + 1` unconditionally on every
-- claim — including a LEASE RECLAIM of a run sitting in `awaiting_approval`. That
-- status is a run legitimately parked at a human gate for up to 24h (V017/V021's own
-- "liveness, never elapsed time" comment): its worker can restart, redeploy, or simply
-- roll its heartbeat past `p_lease_s` several times over that window with ZERO actual
-- execution failures — the run is just waiting on a person. Because dispatch_claimed
-- (backend/services/runs/queue.py) fails a run outright once attempts > MAX_ATTEMPTS
-- (3), a gated run that loses its worker's lease three times during one long gate wait
-- gets failed with "run failed too many times — not retried" despite nothing about the
-- run itself ever failing.
--
--   attempts — now bumped ONLY when the reclaimed row is NOT awaiting_approval. A
--     reclaim of a genuinely stuck run (queued→running promotion, or a stranded
--     'running' lease from a crashed worker) still increments exactly as before, so the
--     crash-loop protection MAX_ATTEMPTS exists for is untouched. A reclaim of an
--     awaiting_approval row costs nothing, because waiting on a human is not a failure.
--
-- Everything else in the function — the `cand` CTE, the status-promotion CASE, the
-- RETURNING list, SECURITY DEFINER / search_path / ownership / grants — is byte-identical
-- to V021. This migration is a pure CREATE OR REPLACE of one column expression.
--
-- Additive and idempotent (V021's own pattern): safe to re-run. CREATE OR REPLACE keeps
-- the function's OID, so the existing `GRANT EXECUTE ... TO gtm_api` from V021 is
-- preserved automatically — restated below anyway, matching V021's own belt-and-braces
-- style, in case a future migration ever needs to REVOKE and re-GRANT explicitly.

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
           -- RL-17: a reclaim of a run legitimately parked at a gate (awaiting_approval)
           -- costs it nothing — only a reclaim of an actually-executing run (queued→running,
           -- or a stranded 'running' lease) counts against MAX_ATTEMPTS.
           attempts     = CASE WHEN r.status = 'awaiting_approval' THEN r.attempts ELSE r.attempts + 1 END,
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

REVOKE ALL     ON FUNCTION claim_next_run(TEXT, INT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION claim_next_run(TEXT, INT) TO gtm_api;
