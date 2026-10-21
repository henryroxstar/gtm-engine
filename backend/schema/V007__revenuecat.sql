-- V007: RevenueCat billing layer
--
-- Adds revenuecat_subscriber_id alongside the existing stripe_subscription_id.
-- stripe_subscription_id is kept for any direct web-Stripe checkout flows;
-- they are independent purchase paths and may coexist on the same workspace.
--
-- The /webhooks/revenuecat handler sets revenuecat_subscriber_id on
-- INITIAL_PURCHASE / RENEWAL events (== RC app_user_id == workspace_id).
-- The subscriptions.entitlement column continues to be the single source of
-- truth read by the Phase B resolver — no resolver changes needed.

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS revenuecat_subscriber_id TEXT;

COMMENT ON COLUMN subscriptions.revenuecat_subscriber_id IS
    'RevenueCat subscriber ID (app_user_id set to workspace_id at SDK init in the '
    'Flutter app). Written by the /webhooks/revenuecat handler on INITIAL_PURCHASE, '
    'RENEWAL, and related events. Null for workspaces that subscribe via web Stripe only.';

COMMENT ON COLUMN subscriptions.stripe_subscription_id IS
    'Direct Stripe subscription ID for web-checkout flows. Kept alongside '
    'revenuecat_subscriber_id — they are different purchase paths for the same '
    'workspace and may both be populated (e.g. a workspace that started on web then '
    'switched to in-app). The entitlement column is authoritative regardless of source.';
