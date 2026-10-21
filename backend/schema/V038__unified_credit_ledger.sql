-- V038: Unified Credit Billing & Atomic Ledger (PRD 2026-09-19)
--
-- Unifies subscription plans, MCP developer top-ups, and tool execution into a
-- single Universal Credit Wallet and Unified Metering Ledger ($1.00 USD = 1,000 Credits).
-- Eliminates the 2x cap vulnerability and TOCTOU concurrency leaks via atomic cost reservations.

-- 1. Universal Credit Wallet: workspace_wallets
CREATE TABLE IF NOT EXISTS workspace_wallets (
    workspace_id    UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    balance_credits NUMERIC(14,4) NOT NULL DEFAULT 0.0000,
    balance_usd     NUMERIC(10,6) NOT NULL DEFAULT 0.000000,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ensure balance_credits exists if workspace_wallets was already created in V037
ALTER TABLE workspace_wallets ADD COLUMN IF NOT EXISTS balance_credits NUMERIC(14,4) NOT NULL DEFAULT 0.0000;

-- Backfill credits from existing balance_usd if any rows exist
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workspace_wallets' AND column_name = 'balance_usd'
    ) THEN
        UPDATE workspace_wallets
           SET balance_credits = balance_usd * 1000
         WHERE balance_credits = 0.0000 AND balance_usd > 0;
    END IF;
END $$;

-- 2. Atomic Pre-Flight Locks: cost_reservations
CREATE TABLE IF NOT EXISTS cost_reservations (
    id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      UUID          NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id            UUID,                                 -- backend run; NULL for one-off/MCP calls
    estimated_credits NUMERIC(14,4) NOT NULL DEFAULT 0.0000 CHECK (estimated_credits >= 0),
    estimated_usd     NUMERIC(10,6) DEFAULT 0.0 CHECK (estimated_usd >= 0),
    state             TEXT          NOT NULL DEFAULT 'open'  -- open | settled | released
                                    CHECK (state IN ('open', 'settled', 'released')),
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    closed_at         TIMESTAMPTZ
);

ALTER TABLE cost_reservations ADD COLUMN IF NOT EXISTS estimated_credits NUMERIC(14,4) DEFAULT 0.0000;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'cost_reservations' AND column_name = 'estimated_usd'
    ) THEN
        ALTER TABLE cost_reservations ALTER COLUMN estimated_usd DROP NOT NULL;
        UPDATE cost_reservations
           SET estimated_credits = estimated_usd * 1000
         WHERE (estimated_credits IS NULL OR estimated_credits = 0.0000) AND estimated_usd > 0;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS cost_reservations_workspace_open_credits_idx
    ON cost_reservations(workspace_id) WHERE state = 'open';

-- 3. Unified Metering Ledger: unified_metering_log
-- Replaces fragmented cost_records (backend) and mcp_calls (MCP) with one ledger.
CREATE TABLE IF NOT EXISTS unified_metering_log (
    id                 UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       UUID          NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    runtime            TEXT          NOT NULL, -- 'mcp' | 'backend' | 'vps'
    tool_name          TEXT          NOT NULL, -- 'heygen', 'claude-sonnet', 'deepseek', etc.
    cost_credits       NUMERIC(14,4) NOT NULL CHECK (cost_credits >= 0),
    profile_name       TEXT,
    model              TEXT,
    prompt_tokens      INTEGER       NOT NULL DEFAULT 0,
    completion_tokens  INTEGER       NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10,6) NOT NULL DEFAULT 0,
    run_id             UUID,
    api_key_id         UUID          REFERENCES api_keys(id) ON DELETE SET NULL,
    recorded_at        TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS unified_metering_log_workspace_month_idx
    ON unified_metering_log(workspace_id, (date_trunc('month', recorded_at AT TIME ZONE 'UTC')));

CREATE INDEX IF NOT EXISTS unified_metering_log_workspace_recorded_idx
    ON unified_metering_log(workspace_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS unified_metering_log_run_id_idx
    ON unified_metering_log(run_id) WHERE run_id IS NOT NULL;

-- 4. Top-up auditing: wallet_transactions
ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS credits_deposited NUMERIC(14,4);
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'wallet_transactions' AND column_name = 'amount_usd'
    ) THEN
        ALTER TABLE wallet_transactions ALTER COLUMN amount_usd DROP NOT NULL;
    END IF;
END $$;

-- 5. Row-Level Security (RLS) & Tenant Isolation
ALTER TABLE workspace_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_wallets FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_wallets_isolation ON workspace_wallets;
CREATE POLICY workspace_wallets_isolation ON workspace_wallets
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE cost_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_reservations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cost_reservations_isolation ON cost_reservations;
CREATE POLICY cost_reservations_isolation ON cost_reservations
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE unified_metering_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE unified_metering_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS unified_metering_log_isolation ON unified_metering_log;
CREATE POLICY unified_metering_log_isolation ON unified_metering_log
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- Runtime grants to gtm_api and gtm_bootstrap
GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_wallets TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON cost_reservations TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON unified_metering_log TO gtm_api;

GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_wallets TO gtm_bootstrap;
GRANT SELECT, UPDATE ON cost_reservations TO gtm_bootstrap;
GRANT SELECT, INSERT, UPDATE, DELETE ON unified_metering_log TO gtm_bootstrap;
