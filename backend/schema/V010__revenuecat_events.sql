-- V010: RevenueCat webhook idempotency + ordering (H7)
--
-- The /webhooks/revenuecat handler was idempotent only by luck: a redelivered or
-- out-of-order event re-applied and could flip a paying customer's entitlement
-- (e.g. a late CANCELLATION arriving after a RENEWAL). This table records every
-- processed RC event by its globally-unique event id. The handler INSERTs
-- ON CONFLICT DO NOTHING before applying:
--   • duplicate event id  → 0 rows inserted → skip (idempotent)
--   • a later event_ts already recorded for the workspace → skip (stale/out-of-order)
--
-- RLS-scoped + FORCE like every tenant table; ON DELETE CASCADE so an account
-- erasure (GDPR Art.17) removes these rows too. gtm_api is auto-granted via V009's
-- ALTER DEFAULT PRIVILEGES; the explicit GRANT below is belt-and-suspenders. DELETE
-- is granted (no UPDATE — events are an immutable append-only log) so a direct
-- retention sweep can prune old rows under RLS without a permission error; the
-- cascade erasure itself needs no child grant.

CREATE TABLE IF NOT EXISTS revenuecat_events (
    event_id     TEXT        PRIMARY KEY,
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event_type   TEXT        NOT NULL,
    event_ts     BIGINT,     -- RC event_timestamp_ms; NULL if absent (ordering skipped)
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS revenuecat_events_workspace_ts_idx
    ON revenuecat_events(workspace_id, event_ts DESC);

ALTER TABLE revenuecat_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE revenuecat_events FORCE  ROW LEVEL SECURITY;

CREATE POLICY revenuecat_events_isolation ON revenuecat_events
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, DELETE ON revenuecat_events TO gtm_api;
