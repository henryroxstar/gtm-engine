-- V037: Developer MCP Prepaid Wallets & Transactions
-- Tracks prepaid credit balances and top-up transaction audits for developer MCP usage.

CREATE TABLE IF NOT EXISTS workspace_wallets (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    balance_usd  NUMERIC(10,6) NOT NULL DEFAULT 0.000000,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id            UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    amount_usd              NUMERIC(10,6) NOT NULL,
    provider                TEXT NOT NULL DEFAULT 'revenuecat_stripe',
    external_transaction_id TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS wallet_transactions_provider_ext_id_idx
    ON wallet_transactions (provider, external_transaction_id);

CREATE INDEX IF NOT EXISTS wallet_transactions_workspace_id_idx
    ON wallet_transactions (workspace_id);

-- Enable and force Row-Level Security for multi-tenant isolation
ALTER TABLE workspace_wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_wallets FORCE ROW LEVEL SECURITY;

CREATE POLICY workspace_wallets_isolation ON workspace_wallets
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE wallet_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE wallet_transactions FORCE ROW LEVEL SECURITY;

CREATE POLICY wallet_transactions_isolation ON wallet_transactions
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- Grant access to the API runtime role
GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_wallets TO gtm_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON wallet_transactions TO gtm_api;
