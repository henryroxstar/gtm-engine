-- V012: MCP runtime least-privilege — the pre-tenant api_keys bootstrap lookup
--
-- Why this migration exists
-- -------------------------
-- V009 armed FORCE RLS + moved the BACKEND runtime to the non-owner gtm_api role.
-- The MCP server (mcp_server/) was not moved: it still connects as the table OWNER,
-- which bypasses RLS, so its tenant isolation rests on ONE wall (the app's own
-- WHERE workspace_id = ...), not the two the backend has.
--
-- Moving the MCP runtime to gtm_api is blocked by one query that precedes any tenant
-- context: mcp_server/auth.py looks up api_keys BY key_hash to *discover* the
-- workspace from a presented key. There is no app.current_workspace_id yet, so under
-- FORCE RLS as gtm_api that SELECT returns zero rows → every MCP call fails auth.
--
-- The fix mirrors the login bootstrap exactly (workspace_for_user(), V009 §5): a
-- tightly-scoped SECURITY DEFINER function owned by the privileged gtm_bootstrap
-- role, EXECUTE-granted only to gtm_api, with a pinned search_path. After it resolves
-- the workspace, every post-auth MCP query (mcp_calls insert, budget read) runs
-- RLS-subject inside workspace_scope().
--
-- Idempotent: CREATE OR REPLACE + guarded grants; safe to re-run.

-- ── gtm_bootstrap needs table privilege on api_keys ──────────────────────────
-- The definer function runs AS gtm_bootstrap (BYPASSRLS). BYPASSRLS exempts it
-- from row-level security, but it still needs the base table privileges: SELECT
-- for the hash match, UPDATE for the folded-in last_used_at bump. V009 granted
-- api_keys only to gtm_api; gtm_bootstrap gets exactly these two, nothing more.
GRANT SELECT, UPDATE ON api_keys TO gtm_bootstrap;

-- ── resolve_api_key() — the pre-context key lookup ───────────────────────────
-- Does the ENTIRE current validate_api_key() query (hash match + revoked/expiry
-- filter) plus the last_used_at bump, returning only the three fields the caller
-- needs. Enumeration-safe: it resolves ONLY a key whose SHA-256 hash the caller
-- already presents (possession of the raw sk-... key), never lists or wildcards,
-- and exposes nothing beyond that key's own scope. Pinned search_path = public
-- blocks the classic definer search-path hijack.
CREATE OR REPLACE FUNCTION resolve_api_key(p_key_hash TEXT)
RETURNS TABLE (key_id UUID, workspace_id UUID, entitlement TEXT)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH hit AS (
        SELECT id, workspace_id, entitlement
        FROM   api_keys
        WHERE  key_hash = p_key_hash
          AND  revoked_at IS NULL
          AND  (expires_at IS NULL OR expires_at > now())
    ), touch AS (
        UPDATE api_keys SET last_used_at = now()
        WHERE id IN (SELECT id FROM hit) RETURNING id
    )
    SELECT id, workspace_id, entitlement FROM hit
$$;

ALTER FUNCTION resolve_api_key(TEXT) OWNER TO gtm_bootstrap;

-- Only the runtime role may call it; never PUBLIC.
REVOKE ALL     ON FUNCTION resolve_api_key(TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION resolve_api_key(TEXT) TO gtm_api;

-- ── Verification (run after migration) ───────────────────────────────────────
-- Expect proname=resolve_api_key, prosecdef=true, owner=gtm_bootstrap:
--   SELECT proname, prosecdef, pg_get_userbyid(proowner) AS owner
--   FROM pg_proc WHERE proname = 'resolve_api_key';
-- Expect gtm_api has EXECUTE, PUBLIC does not:
--   SELECT has_function_privilege('gtm_api', 'resolve_api_key(text)', 'EXECUTE');
