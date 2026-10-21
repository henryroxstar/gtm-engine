-- V047: provisioning_jobs (W1, PRD 2026-10-19a) — one repeatable call that sets up a working agent.
--
-- A job is the durable record of ONE setup attempt: which step it has reached, what it created, and who may
-- act on it. It is the whole reason a repeat of the same request is safe:
--   * (workspace_id, idempotency_key) is unique, so a repeat finds its own job and never makes a second;
--     request_hash binds the key to the request that first used it (a different request is refused).
--   * (workspace_id, source_key) is unique among ACTIVE jobs, so two different keys for the same site or
--     profile cannot start two paid onboardings; the second is handed the first's job.
--   * lease_expires_at is the mutual exclusion: one caller advances a job at a time; a caller that died is
--     taken over once its lease lapses, from the step recorded here.
--   * key_id is set in the SAME transaction that mints the agent's API key, so "key minted" and "job knows
--     it" are one fact. The raw key is never stored: only api_keys.key_hash exists, and it is not copied here.
--
-- Nothing here holds a secret. Integration key VALUES go to the existing encrypted store only; this row
-- carries provider NAMES (integrations) and, for a failure, the provider name and a reason.
--
-- UPDATE is granted, DELETE is not: an abandoned or finished job stays as the audit trail of what was set up.

CREATE TABLE IF NOT EXISTS provisioning_jobs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    idempotency_key  TEXT        NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 128),
    request_hash     TEXT        NOT NULL,
    source_key       TEXT        NOT NULL,
    agent_name       TEXT        NOT NULL,
    state            TEXT        NOT NULL CHECK (state IN (
                         'started', 'onboarding_running', 'awaiting_profile_confirmation',
                         'agent_created', 'ready', 'ready_with_blockers', 'failed', 'abandoned')),
    step             TEXT        NOT NULL DEFAULT 'precheck' CHECK (step IN (
                         'precheck', 'integrations', 'profile', 'agent', 'readiness', 'key', 'done')),
    onboard_job_id   UUID,
    profile_slug     TEXT,
    agent_id         UUID        REFERENCES agents(id) ON DELETE SET NULL,
    key_id           UUID        REFERENCES api_keys(id) ON DELETE SET NULL,
    integrations     TEXT[]      NOT NULL DEFAULT '{}',
    blockers         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    errors           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    lease_expires_at TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS provisioning_jobs_idempotency
    ON provisioning_jobs (workspace_id, idempotency_key);

-- One ACTIVE job per source (a profile name or a normalised URL). Finished, failed and abandoned jobs do not
-- hold the slot, so a new key can start over once the old attempt is over.
CREATE UNIQUE INDEX IF NOT EXISTS provisioning_jobs_one_active_per_source
    ON provisioning_jobs (workspace_id, source_key)
    WHERE state IN ('started', 'onboarding_running', 'awaiting_profile_confirmation', 'agent_created');

CREATE INDEX IF NOT EXISTS provisioning_jobs_workspace_idx ON provisioning_jobs (workspace_id, created_at);

ALTER TABLE provisioning_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE provisioning_jobs FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS provisioning_jobs_isolation ON provisioning_jobs;
CREATE POLICY provisioning_jobs_isolation ON provisioning_jobs
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- gtm_api is named in the REVOKE on purpose (earlier migrations leave default privileges that grant it the
-- full DML set on every new table). It may read, create and update a job; it may never delete one.
REVOKE ALL ON provisioning_jobs FROM PUBLIC, gtm_api;
GRANT SELECT, INSERT, UPDATE ON provisioning_jobs TO gtm_api;
