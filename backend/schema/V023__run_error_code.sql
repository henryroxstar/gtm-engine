-- V023: runs.error_code — a machine-readable reason beside the failure prose (M-07).
--
-- A refusal at POST /v1/runs already carries error.code, but a run that fails AFTER
-- admission recorded only runs.error, a human sentence. A client had to string-match that
-- sentence to tell an exhausted budget (an upgrade prompt) from an expired gate, a restart,
-- missing setup, or a genuine failure — and any rewording of the prose broke it silently.
--
--   runs.error_code — one value from the closed set in backend/services/runs/persistence.py
--     (RunErrorCode), written by _fail_run together with runs.error. Nullable: every row
--     that is not 'failed', and every failed row written before this migration, has none.
--     No CHECK constraint on purpose: the set grows additively, and a CHECK would turn a
--     new code into a migration that must land before the code that writes it. _fail_run
--     refuses a value outside the set before it writes, and a contract test pins every
--     call site to it.
--
-- Additive and idempotent (V021's pattern). No grant or policy change: the column is on a
-- table gtm_api already reads and writes under FORCE RLS (V009), and claim_next_run /
-- touch_worker_runs (V021) name their columns explicitly, so neither needs to change.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS error_code TEXT;
