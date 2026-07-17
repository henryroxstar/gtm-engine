-- V009: Arm the tenant boundary — FORCE RLS + a non-owner runtime role
--
-- Why this migration exists
-- -------------------------
-- V004–V008 ENABLE row-level security on every tenant table, but never FORCE it,
-- and the API historically connected as POSTGRES_USER (gtm_app) — a Postgres
-- SUPERUSER that also OWNS the tables. Superusers and table owners bypass
-- non-forced RLS, so `SET LOCAL app.current_workspace_id` filtered nothing and
-- the "second reinforcement" the docs describe was inert. V004's own header
-- (lines 13–15) already anticipated this: "the backend must NEVER connect as
-- superuser in production." This migration realizes that intent.
--
-- Two independent changes are BOTH required — either alone is a no-op:
--   1. FORCE ROW LEVEL SECURITY  → the table owner is no longer exempt.
--   2. A LOGIN NOSUPERUSER NOBYPASSRLS role (gtm_api) that owns nothing → RLS +
--      FORCE fully apply to it. backend/main.py connects the RUNTIME pool as
--      gtm_api; the short-lived MIGRATION pool keeps connecting as gtm_app (owner)
--      for DDL, then closes. (Superusers bypass even FORCE, so the role swap is
--      what actually arms RLS.)
--
-- Auth bootstrap (the subtle part)
-- --------------------------------
-- register()/login() must touch `workspaces` BEFORE a workspace context exists
-- (there is no app.current_workspace_id at signup). Under FORCE RLS + gtm_api
-- those reads/writes would silently return zero rows / fail WITH CHECK, breaking
-- signup and login. Two SECURITY DEFINER helpers owned by a dedicated BYPASSRLS
-- role (gtm_bootstrap) provide the exact, minimal bypass:
--   • bootstrap_workspace_for_new_user() — the V001 signup trigger, re-declared
--     as SECURITY DEFINER so its workspace/member/subscription INSERTs succeed.
--   • workspace_for_user(uuid) — resolves a user's workspace at login/register
--     without exposing the `workspaces` table to the runtime role.
-- gtm_bootstrap gets ONLY the table grants those two functions need — nothing
-- more. The runtime role never has BYPASSRLS.
--
-- Idempotent + re-runnable: role creation is guarded on pg_roles; FORCE and GRANT
-- are naturally idempotent; functions use CREATE OR REPLACE. gtm_api is created
-- here WITHOUT a password (a secret) — backend/main.py sets it post-migrate from
-- Doppler (POSTGRES_GTMAPI_PASSWORD); the test harness sets its own.

-- ── 1. FORCE RLS on every tenant-scoped table ────────────────────────────────
-- `users` is intentionally excluded: it is the shared identity table (email,
-- password_hash, display_name), has no workspace_id, and carries no tenant data.
ALTER TABLE workspaces            FORCE ROW LEVEL SECURITY;
ALTER TABLE workspace_members     FORCE ROW LEVEL SECURITY;
ALTER TABLE subscriptions         FORCE ROW LEVEL SECURITY;
ALTER TABLE profiles              FORCE ROW LEVEL SECURITY;
ALTER TABLE api_keys              FORCE ROW LEVEL SECURITY;
ALTER TABLE encrypted_credentials FORCE ROW LEVEL SECURITY;
ALTER TABLE cost_records          FORCE ROW LEVEL SECURITY;
ALTER TABLE runs                  FORCE ROW LEVEL SECURITY;
ALTER TABLE mcp_calls             FORCE ROW LEVEL SECURITY;
ALTER TABLE push_tokens           FORCE ROW LEVEL SECURITY;

-- ── 1b. Harden current_workspace_id() to fail closed CONSISTENTLY ────────────
-- V004 defined this as current_setting('app.current_workspace_id', true)::UUID.
-- On a pooled connection, once a transaction has used SET LOCAL, the GUC resets
-- to the empty string ''' (not unset), so ''::UUID would RAISE "invalid input
-- syntax for type uuid" on any tenant query that forgot to set a workspace
-- context — an intermittent 500 that depends on connection reuse. nullif(...,'')
-- maps both the unset (NULL) and reset ('') cases to NULL, so a missing context
-- yields NO ROWS on read and fails WITH CHECK on write — quietly, every time.
CREATE OR REPLACE FUNCTION current_workspace_id() RETURNS UUID AS $$
    SELECT nullif(current_setting('app.current_workspace_id', true), '')::UUID
$$ LANGUAGE sql STABLE;

-- ── 2. Non-owner runtime role (gtm_api) ──────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gtm_api') THEN
        CREATE ROLE gtm_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO gtm_api;

-- Tenant tables: the runtime role reads/writes rows, but RLS scopes every one.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    workspaces, workspace_members, subscriptions, profiles, api_keys,
    encrypted_credentials, cost_records, runs, mcp_calls, push_tokens
    TO gtm_api;

-- users: register reads (email dup-check) + inserts; login reads; password change
-- updates. delete_account() issues a top-level `DELETE FROM users`; the
-- workspaces.owner_user_id → users FK is ON DELETE CASCADE with users as the
-- PARENT, so the delete is DRIVEN from users and REQUIRES delete privilege here
-- (the cascade into child tables needs no extra grant). Without DELETE, account
-- erasure raises permission-denied AFTER the on-disk rmtree has already run — a
-- partial, non-atomic GDPR Art. 17 erasure.
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO gtm_api;

-- All UUID PKs today (no sequences), but grant defensively for future tables.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gtm_api;

-- RLS policies call current_workspace_id(); the runtime role must execute it.
GRANT EXECUTE ON FUNCTION current_workspace_id() TO gtm_api;

-- Future objects created by the migration (owner) role auto-grant to gtm_api, so
-- a new migration can never silently leave an endpoint with no privileges (the
-- "500s only in prod" GRANT-gap trap). No FOR ROLE clause → binds to the role
-- running this migration, whatever POSTGRES_USER is set to.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gtm_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO gtm_api;

-- ── 3. Bootstrap role (gtm_bootstrap) — narrow, BYPASSRLS, no login ───────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gtm_bootstrap') THEN
        CREATE ROLE gtm_bootstrap NOLOGIN NOSUPERUSER BYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO gtm_bootstrap;
-- Exactly what the two SECURITY DEFINER functions below touch, nothing more.
-- (SELECT on workspaces also satisfies the INSERT ... RETURNING id in the trigger.)
GRANT SELECT, INSERT ON workspaces        TO gtm_bootstrap;
GRANT INSERT          ON workspace_members TO gtm_bootstrap;
GRANT INSERT          ON subscriptions     TO gtm_bootstrap;

-- ── 4. Signup trigger, re-declared as SECURITY DEFINER ───────────────────────
-- Body is identical to V001's bootstrap_workspace_for_new_user(); the only
-- changes are `SECURITY DEFINER` + a pinned search_path (definer-function
-- hardening). Owned by gtm_bootstrap so its three INSERTs bypass FORCE RLS.
CREATE OR REPLACE FUNCTION bootstrap_workspace_for_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    ws_id UUID;
    ws_slug TEXT;
BEGIN
    ws_slug := regexp_replace(
                 lower(split_part(NEW.email, '@', 1)),
                 '[^a-z0-9]+', '-', 'g'
               );
    ws_slug := ws_slug || '-' || substr(gen_random_uuid()::text, 1, 6);

    INSERT INTO workspaces(slug, display_name, owner_user_id)
    VALUES (ws_slug, coalesce(NEW.display_name, split_part(NEW.email, '@', 1)), NEW.id)
    RETURNING id INTO ws_id;

    INSERT INTO workspace_members(workspace_id, user_id, role)
    VALUES (ws_id, NEW.id, 'owner');

    INSERT INTO subscriptions(workspace_id)
    VALUES (ws_id);

    RETURN NEW;
END;
$$;

ALTER FUNCTION bootstrap_workspace_for_new_user() OWNER TO gtm_bootstrap;

-- ── 5. workspace_for_user() — resolve a user's workspace pre-context ──────────
CREATE OR REPLACE FUNCTION workspace_for_user(p_user_id UUID)
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT id FROM workspaces WHERE owner_user_id = p_user_id
$$;

ALTER FUNCTION workspace_for_user(UUID) OWNER TO gtm_bootstrap;

-- Only the runtime role may call it; never PUBLIC.
REVOKE ALL     ON FUNCTION workspace_for_user(UUID) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION workspace_for_user(UUID) TO gtm_api;

-- ── Verification (run after migration) ───────────────────────────────────────
-- Expect relforcerowsecurity = true on all 10 tenant tables:
--   SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
--   WHERE relname IN ('workspaces','workspace_members','subscriptions','profiles',
--     'api_keys','encrypted_credentials','cost_records','runs','mcp_calls',
--     'push_tokens') ORDER BY relname;
-- Expect rolsuper=false, rolbypassrls=false for gtm_api:
--   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='gtm_api';
