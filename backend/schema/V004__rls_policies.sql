-- V004: Row Level Security policies
--
-- Every tenant-scoped table is locked down with RLS so that even a SQL injection
-- in the backend application layer cannot read another workspace's data.
--
-- Pattern: the backend sets a session-local variable before any query:
--     SET LOCAL app.current_workspace_id = '<uuid>';
-- RLS policies then enforce that every row's workspace_id matches.
--
-- The function current_workspace_id() is intentionally STABLE (not VOLATILE)
-- so Postgres can inline it into index scans.
--
-- Important: `app_user` is a low-privilege DB role used by the backend API.
-- The superuser / migration role bypasses RLS (standard Postgres behaviour).
-- The backend must NEVER connect as superuser in production.

-- ── Session-variable helper ───────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION current_workspace_id() RETURNS UUID AS $$
    SELECT current_setting('app.current_workspace_id', true)::UUID
$$ LANGUAGE sql STABLE;

-- ── Enable RLS ────────────────────────────────────────────────────────────────

ALTER TABLE workspaces            ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_members     ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles              ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys              ENABLE ROW LEVEL SECURITY;
ALTER TABLE encrypted_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_records          ENABLE ROW LEVEL SECURITY;

-- ── RLS policies ─────────────────────────────────────────────────────────────
-- USING clause = read/delete filter; WITH CHECK clause = insert/update filter.
-- Both must pass for any mutating operation.

-- workspaces: a session may only see/touch its own workspace
CREATE POLICY workspace_isolation ON workspaces
    USING      (id = current_workspace_id())
    WITH CHECK (id = current_workspace_id());

-- workspace_members: scoped by workspace
CREATE POLICY workspace_members_isolation ON workspace_members
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- subscriptions: scoped by workspace
CREATE POLICY subscriptions_isolation ON subscriptions
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- profiles: scoped by workspace
CREATE POLICY profiles_isolation ON profiles
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- api_keys: scoped by workspace
CREATE POLICY api_keys_isolation ON api_keys
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- encrypted_credentials: scoped by workspace (tightest — holds PII/secrets)
CREATE POLICY credentials_isolation ON encrypted_credentials
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- cost_records: scoped by workspace
CREATE POLICY cost_records_isolation ON cost_records
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- ── Verification query (run after migration to confirm RLS is active) ─────────
-- Expected: all tables in the result set show rowsecurity = true.
--
-- SELECT tablename, rowsecurity
-- FROM   pg_tables
-- WHERE  schemaname = 'public'
--   AND  tablename IN (
--          'workspaces','workspace_members','subscriptions',
--          'profiles','api_keys','encrypted_credentials','cost_records'
--        )
-- ORDER  BY tablename;
