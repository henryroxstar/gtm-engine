-- V018 — durable-gate reconciliation read (fixes A4).
--
-- Startup reconciliation (backend/main.py → routers.runs.reconcile_gates) must find
-- EVERY run left in 'awaiting_approval' by a restart, across all tenants, so it can
-- re-dispatch the stranded gate loop. But reconciliation runs on the runtime pool
-- (role gtm_api, NO BYPASSRLS) with no workspace context — it is a global sweep, like
-- release_stale_reservations (V013). Under FORCE ROW LEVEL SECURITY (V009) the runs
-- isolation policy is `workspace_id = current_workspace_id()`, and current_workspace_id()
-- is NULL with no context set, so a plain `SELECT ... FROM runs` returns ZERO rows.
-- Before this migration the reconcile therefore resumed nothing and gated runs stayed
-- stranded after every restart — i.e. the V017 durability guarantee ("a restart no
-- longer strands a gated run") did NOT hold on the deployed role.
--
-- Fix: the same audited definer pattern as release_stale_reservations / resolve_api_key
-- — a tightly-scoped SECURITY DEFINER function owned by gtm_bootstrap (BYPASSRLS, V009),
-- pinned search_path, EXECUTE granted only to gtm_api. It returns only the columns the
-- reconcile needs and only 'awaiting_approval' rows. The caller (reconcile_gates) still
-- acts on each row under workspace_scope(pool, workspace_id), so cross-tenant rows never
-- leave this function's result set without re-entering a properly scoped transaction.
CREATE OR REPLACE FUNCTION awaiting_approval_runs()
RETURNS TABLE (id UUID, workspace_id UUID, profile_name TEXT, prompt TEXT, agent_id UUID)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT id, workspace_id, profile_name, prompt, agent_id
      FROM runs
     WHERE status = 'awaiting_approval'
$$;

ALTER FUNCTION awaiting_approval_runs() OWNER TO gtm_bootstrap;

-- gtm_bootstrap (BYPASSRLS) needs base-table SELECT for the definer body to read runs.
GRANT SELECT ON runs TO gtm_bootstrap;

REVOKE ALL     ON FUNCTION awaiting_approval_runs() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION awaiting_approval_runs() TO gtm_api;
