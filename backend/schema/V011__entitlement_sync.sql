-- V011: Billing boundary — the billing service owns billing; gtm-engine receives entitlement syncs.
--
-- RevenueCat billing moved OUT of gtm-engine to the billing service's own backend (see
-- the billing-boundary design doc). The engine no longer integrates
-- with any billing vendor: the billing service PUTs the resolved entitlement + spend cap to
-- /v1/entitlement/{workspace_id} (service-authed), and the cost guard enforces it.
--
-- This migration:
--   1. Replaces the RC-specific webhook idempotency table (revenuecat_events) with a
--      provider-agnostic entitlement_sync_events table (same dedup + ordering guards).
--   2. Makes the pre-sync cap FAIL-SAFE: a workspace gets NO paid budget until the billing service
--      confirms a plan. Previously a fresh workspace defaulted to entitlement='free'
--      but monthly_cost_cap_usd=50.00 — a free tenant could spend $50 of paid calls.
--      With the RC webhook (which corrected the cap on entitlement change) removed,
--      nothing would ever fix that, so the default drops to 0 and existing free rows
--      are reset (free tier = no paid spend is an enforcement invariant, not a price).

-- 1. Provider-agnostic entitlement-sync idempotency + ordering.
--    sync_id = opaque dedup key from the billing service (its own id or the upstream RC event id).
--    version = monotonic; a sync whose version is older than one already recorded for
--    the workspace is ignored (a redelivered/reordered sync can't flip a live plan).
CREATE TABLE IF NOT EXISTS entitlement_sync_events (
    sync_id      TEXT        PRIMARY KEY,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    entitlement  TEXT        NOT NULL,
    version      BIGINT,     -- NULL if absent (ordering guard skipped)
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entitlement_sync_events_workspace_version_idx
    ON entitlement_sync_events(workspace_id, version DESC);

ALTER TABLE entitlement_sync_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlement_sync_events FORCE  ROW LEVEL SECURITY;

CREATE POLICY entitlement_sync_events_isolation ON entitlement_sync_events
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- DELETE granted (no UPDATE — events are an immutable append-only log) so a retention
-- sweep / GDPR erasure can prune under RLS; the ON DELETE CASCADE handles account erasure.
GRANT SELECT, INSERT, DELETE ON entitlement_sync_events TO gtm_api;

-- 2. RC billing is owned by the billing service now — drop the RC-specific idempotency table.
--    Never carried prod rows (Phase G go-live never completed), so this is a clean drop.
DROP TABLE IF EXISTS revenuecat_events;

-- 3. Fail-safe pre-sync cap: no paid budget until the billing service syncs a plan.
ALTER TABLE subscriptions ALTER COLUMN monthly_cost_cap_usd SET DEFAULT 0;
UPDATE subscriptions SET monthly_cost_cap_usd = 0
 WHERE entitlement = 'free' AND monthly_cost_cap_usd <> 0;
