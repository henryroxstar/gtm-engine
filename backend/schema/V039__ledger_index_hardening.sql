-- V039__ledger_index_hardening.sql
-- Goal: Resolve sequential scans on highly queried ledger tables.

CREATE INDEX IF NOT EXISTS idx_cost_reservations_run_id ON cost_reservations(run_id);
CREATE INDEX IF NOT EXISTS idx_unified_metering_log_api_key_id ON unified_metering_log(api_key_id);
