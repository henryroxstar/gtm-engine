-- V030: track billing-synced plan cost cap as single authoritative writer.
--
-- Why this migration exists
-- -------------------------
-- subscriptions.monthly_cost_cap_usd was previously written by both the billing
-- sync route (PUT /v1/entitlement/{workspace_id}) and the user-facing cost cap route
-- (PATCH /v1/workspace/cost-cap) with no ceiling comparison (M-04, FL18).
-- This column stores the plan entitlement cap set authoritatively by the billing service;
-- user PATCH requests are constrained downward to stay within this plan ceiling.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS plan_cost_cap_usd NUMERIC(10,4) NOT NULL DEFAULT 0;

UPDATE subscriptions SET plan_cost_cap_usd = monthly_cost_cap_usd
 WHERE plan_cost_cap_usd = 0 AND monthly_cost_cap_usd > 0;
