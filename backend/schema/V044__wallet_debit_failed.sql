-- V044: Durable "a debit failed" marker on the prepaid wallet (MM3).
--
-- A metered call that already ran but whose wallet debit could not be written used to be
-- indistinguishable from one that was billed: settle_credits caught every error and returned
-- False, and its callers ignored the value. The call's cost was then never deducted, and its
-- reservation stayed 'open' until the crash sweep released it.
--
-- This column is the record that it happened. It is written in its OWN transaction (a failed
-- statement aborts the one it ran in) and READ by every refusal gate — reserve_credits,
-- the MCP check_budget, acheck_budget and areserve_budget — so the NEXT call is refused
-- rather than quietly free. NULL = no unreconciled failed debit.
--
-- There is deliberately no automated clear. An operator reconciles the missing charge and then
-- clears the marker by hand:
--     UPDATE workspace_wallets SET debit_failed_at = NULL WHERE workspace_id = '<uuid>';
--
-- Additive: same table, same FORCE RLS policy, no new grant (gtm_api already holds
-- SELECT/INSERT/UPDATE on workspace_wallets from V037/V038).

ALTER TABLE workspace_wallets ADD COLUMN IF NOT EXISTS debit_failed_at TIMESTAMPTZ;
