-- V013: Atomic cost reservation — close the budget-cap TOCTOU (follow-up (f), Phase 1)
--
-- Why this migration exists
-- -------------------------
-- The backend budget gate reads (cap, month_to_date_spent) then spends — a
-- check-then-act race. Two concurrent runs can read the SAME pre-spend total, both
-- see spent < cap, and both proceed → the workspace overshoots its cap. Bounded today
-- (per-workspace concurrency cap of 3 + a 2x-cap hard ceiling), not unbounded, but not
-- exact. This table makes cap enforcement EXACT under concurrency: a reservation is
-- inserted inside the same SELECT ... FOR UPDATE critical section as the cap read, so
-- concurrent reserves for one workspace serialize and their sum can never exceed cap.
-- See docs/prds/2026-07-06-atomic-cost-reservation.md (Phase 1).
--
-- Ships behind a default-OFF feature flag (COST_RESERVATION_ENABLED); with the flag off
-- the runtime keeps calling acheck_budget and this table stays empty (zero open
-- reservations ⇒ identical numbers to today).

CREATE TABLE IF NOT EXISTS cost_reservations (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID          NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id        UUID,                                 -- backend run; NULL for one-off calls
    estimated_usd NUMERIC(10,6) NOT NULL CHECK (estimated_usd >= 0),
    state         TEXT          NOT NULL DEFAULT 'open'  -- open | settled | released
                                CHECK (state IN ('open', 'settled', 'released')),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    closed_at     TIMESTAMPTZ
);

-- Hot path is "sum the OPEN reservations for this workspace" — a partial index on the
-- open rows keyed by workspace serves both the reserve read and the crash sweep.
CREATE INDEX IF NOT EXISTS cost_reservations_open_idx
    ON cost_reservations(workspace_id) WHERE state = 'open';

-- RLS + FORCE + isolation policy — same pattern as every tenant table (V004/V009/V011).
ALTER TABLE cost_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_reservations FORCE  ROW LEVEL SECURITY;

CREATE POLICY cost_reservations_isolation ON cost_reservations
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON cost_reservations TO gtm_api;

-- ── Crash sweep — release 'open' reservations older than a TTL, across all tenants ──
-- The done-callback settles a run's reservations on any terminal state, but a hard
-- process kill can skip it, leaving an 'open' row that permanently consumes headroom.
-- A periodic sweep (extends the backend lifespan _evict loop) is the backstop. It must
-- be cross-tenant, but the runtime role gtm_api has NO BYPASSRLS and runs with no
-- workspace context (a global sweep) → under FORCE RLS a plain UPDATE sees zero rows.
-- So the sweep is a tightly-scoped SECURITY DEFINER function owned by gtm_bootstrap
-- (BYPASSRLS), EXECUTE-granted only to gtm_api, pinned search_path — same audited
-- definer pattern as resolve_api_key / workspace_for_user. It only flips open→released
-- past a TTL and returns the count; it exposes no tenant data.
CREATE OR REPLACE FUNCTION release_stale_reservations(p_ttl_seconds INTEGER)
RETURNS INTEGER
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    WITH upd AS (
        UPDATE cost_reservations
           SET state = 'released', closed_at = now()
         WHERE state = 'open'
           AND created_at < now() - make_interval(secs => p_ttl_seconds)
        RETURNING 1
    )
    SELECT count(*)::int FROM upd
$$;

ALTER FUNCTION release_stale_reservations(INTEGER) OWNER TO gtm_bootstrap;

-- gtm_bootstrap (BYPASSRLS) needs the base-table privileges the definer body uses:
-- UPDATE for the SET, and SELECT because the WHERE reads state/created_at.
GRANT SELECT, UPDATE ON cost_reservations TO gtm_bootstrap;

REVOKE ALL     ON FUNCTION release_stale_reservations(INTEGER) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION release_stale_reservations(INTEGER) TO gtm_api;
