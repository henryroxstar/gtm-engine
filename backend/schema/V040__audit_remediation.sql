-- V040__audit_remediation.sql
-- Goal: Ledger indexing, foreign keys, and claim_next_run double-billing prevention.

-- 1. Unique partial index to prevent duplicate reservations
CREATE UNIQUE INDEX IF NOT EXISTS idx_cost_res_run_id_open ON cost_reservations(run_id) WHERE state = 'open';

-- 2. Foreign keys enforcing ON DELETE SET NULL to prevent orphaned records
ALTER TABLE cost_reservations ADD CONSTRAINT fk_cost_reservations_runs FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL;
ALTER TABLE unified_metering_log ADD CONSTRAINT fk_unified_metering_log_runs FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL;

-- 3. Backfill credits_deposited on legacy wallet_transactions
UPDATE wallet_transactions
   SET credits_deposited = amount_usd * 1000
 WHERE credits_deposited IS NULL AND amount_usd > 0;

-- 4. Replace claim_next_run to purge orphaned billing records from crashed attempts
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
            OR (r.status IN ('running', 'awaiting_approval')
                AND r.heartbeat_at IS NOT NULL
                AND r.heartbeat_at < now() - make_interval(secs => p_lease_s))
         ORDER BY r.created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    ),
    purge_legacy_cost AS (
        DELETE FROM cost_records WHERE run_id IN (SELECT id::text FROM cand)
    ),
    purge_uml AS (
        DELETE FROM unified_metering_log WHERE run_id IN (SELECT id FROM cand)
    )
    UPDATE runs r
       SET claimed_by   = p_worker_id,
           claimed_at   = now(),
           heartbeat_at = now(),
           attempts     = CASE WHEN r.status = 'awaiting_approval' THEN r.attempts ELSE r.attempts + 1 END,
           status       = CASE WHEN r.status = 'queued' THEN 'running' ELSE r.status END
      FROM cand
     WHERE r.id = cand.id
    RETURNING r.id, r.workspace_id, r.profile_name, r.prompt,
              r.payload, r.agent_id, cand.prev_status, r.attempts;
$$;

ALTER FUNCTION claim_next_run(TEXT, INT) OWNER TO gtm_bootstrap;

REVOKE ALL     ON FUNCTION claim_next_run(TEXT, INT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION claim_next_run(TEXT, INT) TO gtm_api;
GRANT  SELECT, DELETE ON cost_records TO gtm_bootstrap;
