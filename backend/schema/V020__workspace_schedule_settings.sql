-- V020 — per-workspace scheduling opt-in (backend Gate-2 schedule parity).
--
-- Before this, the backend publish path (backend/publish_dispatch.py) never passed
-- `scheduled_at` to the shared dispatch helper at all — an approved ⟦SCHEDULE⟧ block
-- on a backend-run publish node was silently dropped, and the post (if any) went out
-- immediately instead of at the requested time. Scheduling on the VPS/cockpit path
-- already has its own opt-in (HERMES_SCHEDULE_ENABLED, closed by default — see
-- CLAUDE.md "Scheduling is publishing with a delay"); the backend needs the SAME
-- opt-in, but per workspace, since one process serves every tenant.
--
-- Mirrors V019's trust model: written ONLY via the service-authed route
-- (require_service_auth / BILLING_SYNC_SECRET), never a user JWT. Default is
-- schedule_enabled = false — enabling immediate publish (workspace_publish_settings.
-- enabled) is not consent for posts to fire days later unattended; a workspace must
-- opt in to scheduling separately, exactly like the VPS kill switch.

ALTER TABLE workspace_publish_settings
    ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS schedule_max_horizon_days INT NOT NULL DEFAULT 90;
