-- V026: Service principals — machine callers as REST "service principals"
--
-- Why this migration exists
-- --------------------------
-- Machine callers (fleet executors) are becoming first-class API clients: a
-- workspace api_keys row (V002) bound to an agents row (V016), so a run made
-- with that key carries an attributable, budget-capped identity instead of
-- riding on the workspace's ambient trust. Today an api_keys row and an
-- agents row are two unrelated tenant-scoped tables with no link between
-- them, and resolve_api_key() (V012) — the pre-tenant bootstrap lookup that
-- turns a presented key into a workspace — has no way to say WHICH agent (if
-- any) that key speaks for.
--
-- This migration adds exactly the plumbing for that binding — the FK that
-- stops a key in workspace A being bound to an agent in workspace B, the
-- per-agent read/dispatch policy columns, run attribution columns, and the
-- resolver column that carries the bound agent through to the caller
-- (mcp_server/auth.py) — and nothing else. Enrollment/dispatch UX is a later
-- phase.
--
-- Idempotent: guarded ADD COLUMN IF NOT EXISTS / named-constraint existence
-- checks / CREATE INDEX IF NOT EXISTS throughout; safe to re-run.

-- ── 1. agents: composite identity + per-agent policy ─────────────────────────
-- (id, workspace_id) UNIQUE is not redundant with the existing PRIMARY KEY(id):
-- it is what lets api_keys reference "this agent, in this workspace" as ONE
-- FK target, so the database — not application code — refuses a key bound to
-- an agent that belongs to a different tenant.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'agents_id_workspace_uq'
    ) THEN
        ALTER TABLE agents
            ADD CONSTRAINT agents_id_workspace_uq UNIQUE (id, workspace_id);
    END IF;
END
$$;

-- read_scope governs what a run dispatched under this agent may read back
-- through the API: 'own' (only its own runs) or 'workspace' (everything the
-- workspace can see). Defaulting to 'own' is the least-privilege choice for
-- every agent created before this column existed.
ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS read_scope TEXT NOT NULL DEFAULT 'own'
        CHECK (read_scope IN ('own', 'workspace'));

-- daily_dispatch_cap bounds how many runs this agent may start per day; NULL
-- means "no agent-specific cap" (the workspace/entitlement caps still apply).
ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS daily_dispatch_cap INTEGER
        CHECK (daily_dispatch_cap IS NULL OR daily_dispatch_cap >= 0);

-- ── 2. api_keys: bind a key to an agent, scoped to the same workspace ────────
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS agent_id UUID;

-- Composite FK against agents_id_workspace_uq: a key's agent_id must belong
-- to the SAME workspace_id the key itself belongs to. This is the control
-- that stops a workspace-A key being wired to a workspace-B agent — enforced
-- by Postgres, not by application code remembering to check both fields.
-- Default NO ACTION (no ON DELETE clause): agents are archived, never hard-
-- deleted, in the ordinary run path. The one hard delete is the workspace
-- cascade — both agents.workspace_id and api_keys.workspace_id carry their
-- own ON DELETE CASCADE straight to workspaces, so a workspace delete clears
-- both tables within the same statement and NO ACTION (checked at
-- statement-end, not per-row) never sees an orphaned reference. Proven in
-- tests/backend/test_service_principals_live.py::test_workspace_delete_cascades_with_bound_key.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'api_keys_agent_workspace_fk'
    ) THEN
        ALTER TABLE api_keys
            ADD CONSTRAINT api_keys_agent_workspace_fk
            FOREIGN KEY (agent_id, workspace_id)
            REFERENCES agents (id, workspace_id);
    END IF;
END
$$;

-- ── 3. runs: attribute a run to the principal that started it ───────────────
-- principal_kind/principal_id are deliberately loose (TEXT, not a FK) — a
-- 'user' principal_id is a users.id, a 'service' principal_id is an
-- api_keys.id, and the set of principal kinds is closed by the CHECK so a
-- future 'delegated_agent' kind (agent-calls-agent) cannot be stored until a
-- migration explicitly widens this constraint — the same closed-set pattern
-- pack loading uses for external_effect (see CLAUDE.md "Workflow graph").
ALTER TABLE runs ADD COLUMN IF NOT EXISTS principal_kind TEXT
    CHECK (principal_kind IS NULL OR principal_kind IN ('user', 'service'));
ALTER TABLE runs ADD COLUMN IF NOT EXISTS principal_id TEXT;

-- runs_agent_idx (V016) is (workspace_id, agent_id) only — it has no
-- created_at, so it cannot serve a per-agent-per-day dispatch count without a
-- full residual scan of every row for that agent. This index adds created_at
-- as the third key so "how many runs has agent X started today" is an
-- index-only range scan.
CREATE INDEX IF NOT EXISTS runs_agent_daily_idx
    ON runs (workspace_id, agent_id, created_at);

-- ── 4. resolve_api_key(): carry the bound agent through the resolver ────────
-- RETURNS TABLE changes shape (a 4th column), so CREATE OR REPLACE is not
-- legal here (Postgres refuses to change a function's return type in place);
-- DROP + CREATE is required, which also means every grant/ownership
-- statement V012 applied must be re-applied verbatim below — a fresh
-- CREATE FUNCTION is owned by whichever role runs this migration (the
-- owner/migration role), not gtm_bootstrap, and starts with no grants.
DROP FUNCTION IF EXISTS resolve_api_key(TEXT);

CREATE FUNCTION resolve_api_key(p_key_hash TEXT)
RETURNS TABLE (key_id UUID, workspace_id UUID, entitlement TEXT, agent_id UUID)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH hit AS (
        SELECT id, workspace_id, entitlement, agent_id
        FROM   api_keys
        WHERE  key_hash = p_key_hash
          AND  revoked_at IS NULL
          AND  (expires_at IS NULL OR expires_at > now())
    ), touch AS (
        UPDATE api_keys SET last_used_at = now()
        WHERE id IN (SELECT id FROM hit) RETURNING id
    )
    SELECT id, workspace_id, entitlement, agent_id FROM hit
$$;

ALTER FUNCTION resolve_api_key(TEXT) OWNER TO gtm_bootstrap;

-- Only the runtime role may call it; never PUBLIC.
REVOKE ALL     ON FUNCTION resolve_api_key(TEXT) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION resolve_api_key(TEXT) TO gtm_api;

-- ── 5. Grants ─────────────────────────────────────────────────────────────────
-- No new GRANTs needed: V009 grants SELECT/INSERT/UPDATE/DELETE on agents,
-- api_keys and runs to gtm_api at the TABLE level, and a table-level grant
-- covers every column on that table, including the ones added here. The
-- gtm_bootstrap role's existing GRANT SELECT, UPDATE ON api_keys (V012)
-- likewise already covers the new agent_id column — nothing further to grant,
-- and nothing here widens either role's privileges (no BYPASSRLS, no new
-- table/column grant).

-- ── Verification (run after migration) ───────────────────────────────────────
-- Expect the 4-column shape, still owned by gtm_bootstrap, still EXECUTE-only
-- for gtm_api:
--   SELECT proname, prosecdef, pg_get_userbyid(proowner) AS owner
--   FROM pg_proc WHERE proname = 'resolve_api_key';
--   SELECT has_function_privilege('gtm_api', 'resolve_api_key(text)', 'EXECUTE');
-- Expect a cross-workspace bind to fail:
--   INSERT INTO api_keys(workspace_id, key_hash, prefix, agent_id)
--   VALUES ('<workspace-b>', 'x', 'x', '<agent-in-workspace-a>');
--   -- ERROR: insert or update on table "api_keys" violates foreign key
--   -- constraint "api_keys_agent_workspace_fk"
