-- V003: Per-workspace encrypted credential vault (plan fix #9)
--
-- Stores social/publish tokens (LinkedIn OAuth, Twitter, etc.) per workspace.
-- Doppler holds ONLY platform secrets (webhooks, DB DSN, platform API keys).
-- Per-tenant social credentials live here, encrypted at rest.
--
-- Encryption scheme: envelope encryption (AES-256-GCM)
--   plaintext  = JSON blob of the provider's credential fields
--   DEK        = per-workspace data-encryption key (never stored in the DB)
--   ciphertext = AES-256-GCM(DEK, plaintext)         → encrypted_data
--   wrapped_dek = KMS.encrypt(platform_kek, DEK)     → wrapped_dek
--   The platform KEK lives in Doppler/KMS (Phase D wires the KMS client).
--   DEK rotation: generate new DEK, re-encrypt ciphertext, update wrapped_dek
--   and key_version in a single transaction; old ciphertext is never readable
--   after rotation without the old KEK version.
--
-- The backend vault accessor (Phase D) is the ONLY code that decrypts.
-- gtm_core never holds decrypted credentials.

CREATE TABLE IF NOT EXISTS encrypted_credentials (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider        TEXT        NOT NULL,     -- e.g. 'linkedin', 'twitter', 'instagram'
    account_ref     TEXT        NOT NULL,     -- human label, NOT the secret (e.g. 'acme-corp')
    encrypted_data  BYTEA       NOT NULL,     -- AES-256-GCM ciphertext of credentials JSON
    wrapped_dek     BYTEA       NOT NULL,     -- KMS-encrypted DEK
    iv              BYTEA       NOT NULL,     -- GCM initialisation vector (12 bytes)
    tag             BYTEA       NOT NULL,     -- GCM authentication tag (16 bytes)
    key_version     TEXT        NOT NULL,     -- identifies the KEK version (for rotation)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at      TIMESTAMPTZ,
    UNIQUE (workspace_id, provider, account_ref)
);

CREATE INDEX IF NOT EXISTS credentials_workspace_idx
    ON encrypted_credentials(workspace_id);

-- ── Cost ledger (workspace-scoped, mirrors content/<profile>/costs.jsonl) ────
-- Mirrors gtm_core.ledgers.Ledgers cost tracking in the DB for the backend API.
-- The VPS/plugin still use the JSONL ledger on disk; the backend persists both.
-- cost_usd is the canonical billing unit consumed by the monthly cap check
-- (Capabilities.probe() → gtm_core.ledgers.Ledgers.is_over_monthly_cap).

CREATE TABLE IF NOT EXISTS cost_records (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    profile_name    TEXT        NOT NULL,
    run_id          TEXT,
    stage           TEXT,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        NUMERIC(10,6) NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexed expression must be IMMUTABLE: date_trunc over a timestamptz is only
-- STABLE (depends on session TZ), so pin to UTC — date_trunc(text, timestamp) is
-- immutable. This also matches the budget query's predicate so the planner can use
-- it (gtm_core/metering.py: date_trunc('month', t.recorded_at AT TIME ZONE 'UTC')).
CREATE INDEX IF NOT EXISTS cost_records_workspace_month_idx
    ON cost_records(workspace_id, (date_trunc('month', recorded_at AT TIME ZONE 'UTC')));
