-- V016: External-IdP federation identities + agent entities (Track A A3 + A4)
--
--   external_identities — the (issuer, subject) → user map for POST /v1/auth/exchange.
--     Identity spine, NOT tenant-scoped (peer of `users`): rows exist before any
--     workspace context, so no workspace_id / no RLS — exactly like `users`. The
--     identity key is the PAIR (issuer, subject) — never subject alone — so subjects
--     can never collide across issuers (A3 normative rule 4). Append-only from the
--     runtime's point of view: gtm_api gets SELECT + INSERT only.
--
--   agents — named bundle of (profile, optional pack subset, optional budget,
--     optional language) per agent.schema.json. Tenant-scoped: V013 RLS template
--     (ENABLE + FORCE + workspace policy + gtm_api grants). Narrowing-only is
--     enforced in the backend at write AND re-derived at use — the row is never
--     trusted (see backend/routers/agents.py).
--
--   runs / cost_records gain a NULLABLE agent_id for attribution (null on all
--     pre-existing rows — backward compatible by construction). ON DELETE SET NULL:
--     agents are archived, never hard-deleted, except via the workspace cascade —
--     where SET NULL keeps the cascade order-independent (runs/cost rows die from
--     their own workspace FK, not through the agent).

-- ── Federation: password-less users ──────────────────────────────────────────
-- A federated user has no password. Native login fails closed on a NULL hash
-- (backend/routers/auth.py guards it before bcrypt).

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- ── external_identities ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS external_identities (
    issuer     TEXT        NOT NULL,
    subject    TEXT        NOT NULL,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (issuer, subject)
);

CREATE INDEX IF NOT EXISTS external_identities_user_idx
    ON external_identities(user_id);

-- Identity spine: SELECT + INSERT only (append-only map; deletion rides the
-- users FK cascade). Revoke the default-privileges UPDATE/DELETE (V009 §4).
GRANT SELECT, INSERT ON external_identities TO gtm_api;
REVOKE UPDATE, DELETE ON external_identities FROM gtm_api;

-- ── agents ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agents (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name               TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    profile_name       TEXT        NOT NULL,
    -- NULL = every pack the bound profile activates (subset enforced app-side at
    -- write; effective set re-derived as the intersection at run creation).
    packs              TEXT[],
    language           TEXT        CHECK (language IS NULL OR char_length(language) <= 35),
    monthly_budget_usd NUMERIC(10,4) CHECK (monthly_budget_usd IS NULL OR monthly_budget_usd >= 0),
    status             TEXT        NOT NULL DEFAULT 'active'
                                     CHECK (status IN ('active', 'paused', 'archived')),
    is_default         BOOLEAN     NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exactly one default agent per workspace (V001 profiles precedent); the lazy
-- bootstrap INSERTs ON CONFLICT (workspace_id) WHERE is_default DO NOTHING.
CREATE UNIQUE INDEX IF NOT EXISTS agents_one_default_per_workspace
    ON agents(workspace_id)
    WHERE is_default;

-- Name uniqueness among NON-archived agents only (an archived agent frees its name).
CREATE UNIQUE INDEX IF NOT EXISTS agents_name_unique_live
    ON agents(workspace_id, name)
    WHERE status <> 'archived';

CREATE INDEX IF NOT EXISTS agents_workspace_idx ON agents(workspace_id);

CREATE TRIGGER agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- RLS (V013 template): workspace isolation, forced even for the table owner.
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents FORCE ROW LEVEL SECURITY;

CREATE POLICY agents_isolation ON agents
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

GRANT SELECT, INSERT, UPDATE ON agents TO gtm_api;
-- No DELETE: agents are archived via UPDATE, never hard-deleted by the runtime
-- (workspace deletion cascades as the owner/migration role).
REVOKE DELETE ON agents FROM gtm_api;

-- ── Attribution columns ──────────────────────────────────────────────────────

ALTER TABLE runs         ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id) ON DELETE SET NULL;
ALTER TABLE cost_records ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES agents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS runs_agent_idx ON runs(workspace_id, agent_id);

-- Per-agent month spend (the A4 min(cap, budget) check reads this shape; the
-- month expression matches cost_records_workspace_month_idx — pinned to UTC so
-- the expression is IMMUTABLE).
CREATE INDEX IF NOT EXISTS cost_records_agent_month_idx
    ON cost_records(agent_id, (date_trunc('month', recorded_at AT TIME ZONE 'UTC')))
    WHERE agent_id IS NOT NULL;
