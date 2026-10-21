-- V019 — per-workspace publish destination (fixes A5).
--
-- Before this, the backend Gate-2 dispatch built PublishSettings.from_env() — a single
-- process-global HERMES_PUBLISH_URL/SECRET. So ANY tenant who activated a publish pack and
-- approved Gate 2 would publish to the OPERATOR's one pinned account (mitigated only by the
-- publisher being disabled by default). The destination must be per-workspace and fail-closed:
-- a workspace with no row here does NOT publish (no fallback to a shared account).
--
-- Trust model (mirrors entitlement sync, V011):
--   - Written ONLY via the service-authed route (require_service_auth / BILLING_SYNC_SECRET),
--     NEVER a user JWT and never client-settable run inputs — a tenant able to set its own
--     publish URL would be an arbitrary-egress/exfil hole strictly worse than A5.
--   - The secret is NOT stored here. `secret_ref` names a PUBLISH_*-namespaced env var
--     (Doppler-injected), resolved at dispatch time — so the row never holds a credential
--     (keeps CLAUDE.md's "never echo secrets into files/rows" invariant).
--   - `url` is validated (https, allowlist) at write time by the route.
-- The row is still workspace-scoped under FORCE RLS so a tenant can only ever read/carry its
-- own destination; the service write sets app.current_workspace_id to the target workspace via
-- workspace_scope, so WITH CHECK ties the row to that workspace.

CREATE TABLE IF NOT EXISTS workspace_publish_settings (
    workspace_id  UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    enabled       BOOLEAN     NOT NULL DEFAULT false,
    url           TEXT,
    secret_ref    TEXT,        -- env var NAME (PUBLISH_*), never the secret value
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS + FORCE + isolation policy — same pattern as every tenant table (V004/V009/V013).
ALTER TABLE workspace_publish_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_publish_settings FORCE  ROW LEVEL SECURITY;

CREATE POLICY workspace_publish_settings_isolation ON workspace_publish_settings
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_publish_settings TO gtm_api;
