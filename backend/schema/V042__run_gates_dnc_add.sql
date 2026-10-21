-- V042: widen run_gates.gate to admit the SC9 do-not-contact kind (`dnc_add`), the third
-- member of the engine's closed `external_effect` set alongside `publish` and
-- `email_enroll`.
--
-- WHY A MIGRATION IS PART OF THIS CHANGE. The effect vocabulary is hardcoded in FOUR
-- independent places: `gtm_core/packs/loader.py:_ALLOWED_EXTERNAL_EFFECTS`,
-- `backend/services/runs/gate_kinds.py`, `schemas/run-event.schema.json`, and this CHECK.
-- Widening three of four is not a partial rollout — it is a silent approve-and-skip: the
-- operator approves a gate, and the INSERT that records the decision fails on the fourth.
-- `tests/contracts/test_closed_sets_agree.py` now derives all four from the loader so the
-- next widening cannot repeat it.
--
-- Additive: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT is the only way to widen a CHECK,
-- and both statements are idempotent-safe on re-run (V017/V022's pattern). Every existing
-- row stays legal. No column is added, nothing is backfilled, and there is deliberately no
-- `dnc_remove` value — the direction is one-way, here as everywhere in this vertical.

ALTER TABLE run_gates DROP CONSTRAINT IF EXISTS run_gates_gate_check;
ALTER TABLE run_gates ADD CONSTRAINT run_gates_gate_check
    CHECK (gate IN ('plan', 'publish', 'email_enroll', 'dnc_add', 'review'));
