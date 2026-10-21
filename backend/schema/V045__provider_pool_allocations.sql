-- V045: Per-workspace shares of a pooled provider key (MM4, decision D2-A).
--
-- When a tenant spends the PLATFORM's key for a pooled data provider (RocketReach, Apollo), it gets a
-- static monthly SHARE of a pool the operator sizes, and the worker refuses once the share is spent
-- (agent/mcp/*: an unset quota is a share of zero, never "uncapped"). This migration is the allocation:
--
--   provider_pools          one row per provider: the pool's size in provider units per month.
--                           Operator config, not tenant data — written by the owner role only (an
--                           UPDATE here is how the pool is resized), and NOT readable by gtm_api.
--   provider_pool_shares    one row per (workspace, provider): the share that workspace holds.
--                           Tenant-scoped under FORCE RLS; a tenant may READ its own share and can
--                           never write one.
--   allocate_provider_share the ONLY way a share comes into being. SECURITY DEFINER (the audited V013
--                           pattern), because a tenant under workspace_scope cannot see the other
--                           tenants' shares it must be summed against. It takes a row lock on the
--                           pool, so two allocations racing for the last of the pool serialise and the
--                           second sees the first: sum(shares) <= pool holds by construction.
--
-- Unused share is wasted rather than lent (D2-A) — dynamic pooling needs provider usage in Postgres
-- and lets a run overshoot by its own size. A pool lowered below what is already allocated claws
-- nothing back: holders keep their shares and new allocations are refused until the sum fits.

CREATE TABLE IF NOT EXISTS provider_pools (
    provider      TEXT        PRIMARY KEY,
    monthly_units NUMERIC     NOT NULL CHECK (monthly_units >= 0),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provider_pool_shares (
    workspace_id UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider     TEXT        NOT NULL REFERENCES provider_pools(provider) ON DELETE CASCADE,
    units        NUMERIC     NOT NULL CHECK (units > 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, provider)
);

ALTER TABLE provider_pool_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_pool_shares FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS provider_pool_shares_isolation ON provider_pool_shares;
CREATE POLICY provider_pool_shares_isolation ON provider_pool_shares
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- A tenant reads its own share (to hand it to its workers); it never writes one, and the pool's size
-- is not its business — gtm_api gets no grant on provider_pools at all.
-- gtm_api is named in the REVOKE on purpose: earlier migrations leave default privileges that hand it
-- the full DML set on every new table, and a tenant that could INSERT its own share row would be
-- allocating itself an unbounded quota.
REVOKE ALL ON provider_pools        FROM PUBLIC, gtm_api;
REVOKE ALL ON provider_pool_shares  FROM PUBLIC, gtm_api;
GRANT SELECT ON provider_pool_shares TO gtm_api;

CREATE OR REPLACE FUNCTION allocate_provider_share(
    p_workspace UUID,
    p_provider  TEXT,
    p_units     NUMERIC
) RETURNS NUMERIC
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_pool      NUMERIC;
    v_existing  NUMERIC;
    v_allocated NUMERIC;
BEGIN
    -- A definer function must not be an oracle for another tenant: only the workspace the caller is
    -- scoped to may be allocated. current_workspace_id() fails closed (V043), so an unscoped caller
    -- matches nothing.
    IF p_workspace IS NULL OR p_workspace IS DISTINCT FROM current_workspace_id() THEN
        RAISE EXCEPTION 'allocate_provider_share: workspace is not the caller''s scope'
            USING ERRCODE = '42501';
    END IF;
    IF p_units IS NULL OR p_units < 0 THEN
        RAISE EXCEPTION 'allocate_provider_share: share size must be a non-negative number'
            USING ERRCODE = '22023';
    END IF;

    -- Serialise every allocation for this provider on the pool row. A provider with no pool row has a
    -- pool of nothing: absence refuses.
    SELECT monthly_units INTO v_pool FROM provider_pools WHERE provider = p_provider FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 0;
    END IF;

    SELECT units INTO v_existing
      FROM provider_pool_shares
     WHERE workspace_id = p_workspace AND provider = p_provider;
    IF FOUND THEN
        RETURN v_existing;  -- idempotent: the share it already holds, never a new one
    END IF;

    IF p_units = 0 THEN
        RETURN 0;  -- nothing to remember
    END IF;

    SELECT COALESCE(SUM(units), 0) INTO v_allocated
      FROM provider_pool_shares WHERE provider = p_provider;
    IF v_allocated + p_units > v_pool THEN
        RETURN 0;  -- would breach the pool: refused, and nothing is left behind
    END IF;

    INSERT INTO provider_pool_shares(workspace_id, provider, units)
         VALUES (p_workspace, p_provider, p_units);
    RETURN p_units;
END
$$;

ALTER FUNCTION allocate_provider_share(UUID, TEXT, NUMERIC) OWNER TO gtm_bootstrap;

-- gtm_bootstrap (BYPASSRLS) needs the base-table privileges the body uses. SELECT ... FOR UPDATE on
-- the pool row is the lock that serialises allocations, and Postgres requires UPDATE for it; the
-- function never actually modifies the pool, and gtm_bootstrap is not a tenant-facing role.
GRANT SELECT, UPDATE         ON provider_pools       TO gtm_bootstrap;
GRANT SELECT, INSERT         ON provider_pool_shares TO gtm_bootstrap;

REVOKE ALL     ON FUNCTION allocate_provider_share(UUID, TEXT, NUMERIC) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION allocate_provider_share(UUID, TEXT, NUMERIC) TO gtm_api;
