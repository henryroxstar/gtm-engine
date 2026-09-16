-- V022: widen run_gates.gate to admit the A11 email-enrollment kind, plus a generic
-- "review" kind for a gated node with neither a plan draft nor an enroll draft to show
-- (outreach, reply/triage, format-plan, capture, ...) — the A11 fix (agent/pipeline_executor.py)
-- makes every pack-declared gate=true node actually pause now, not only 'plan'/'publish'
-- shaped ones, so the backend's run_gates.gate label needs a value for each of them.
--
-- Additive: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT is the only way to widen a CHECK,
-- and both statements are idempotent-safe on re-run (V017's own pattern). Every existing
-- 'plan'/'publish' row stays legal.

ALTER TABLE run_gates DROP CONSTRAINT IF EXISTS run_gates_gate_check;
ALTER TABLE run_gates ADD CONSTRAINT run_gates_gate_check
    CHECK (gate IN ('plan', 'publish', 'email_enroll', 'review'));
