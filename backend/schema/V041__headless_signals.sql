-- V041: Postgres-backed headless signal queue (PRD 2026-09-20)
--
-- Decouples ingestion skills (market-harvest, events-tracker) from reasoning/LLM execution.
-- Implements FOR UPDATE SKIP LOCKED claim semantics with bounded attempts (max_attempts = 3)
-- and PII claim-check pointers (payload_ref, never inline prospect PII).

CREATE TABLE IF NOT EXISTS headless_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_name    TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    payload_ref     TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempts        INT NOT NULL DEFAULT 0,
    max_attempts    INT NOT NULL DEFAULT 3,
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS headless_signals_status_idx
    ON headless_signals(created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS headless_signals_lease_idx
    ON headless_signals(claimed_at)
    WHERE status = 'processing';

-- The claim function: FOR UPDATE SKIP LOCKED
CREATE OR REPLACE FUNCTION claim_next_signal(p_worker_id TEXT, p_lease_s INT DEFAULT 90)
RETURNS TABLE (
    id UUID,
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
    RETURNING s.id, s.profile_name, s.signal_type, s.payload_ref,
              s.idempotency_key, s.attempts, s.status;
$$;

ALTER FUNCTION claim_next_signal(TEXT, INT) OWNER TO gtm_bootstrap;

GRANT SELECT, INSERT, UPDATE, DELETE ON headless_signals TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON headless_signals TO gtm_bootstrap;

REVOKE ALL ON FUNCTION claim_next_signal(TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_next_signal(TEXT, INT) TO gtm_api;
