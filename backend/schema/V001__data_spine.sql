-- V001: Data spine — users, workspaces, membership, subscriptions, profiles
-- Migration tool: Flyway / Alembic (wired in Phase D). Standard Postgres DDL;
-- compatible with Supabase and self-hosted Postgres equally.
--
-- Design invariants (from gtm_core/tenancy.py + program plan):
--   - workspace_id is on EVERY tenant-scoped row from day one (V2 teams need
--     no migration — just add members to workspace_members).
--   - V1 auto-creates one workspace per user at signup (see signup trigger below).
--   - entitlement is resolved from subscriptions.entitlement (never hard-coded
--     in the backend code — always a DB read so Stripe webhook updates take
--     effect immediately).
--   - ON DELETE CASCADE ensures workspace deletion propagates to all child rows
--     (GDPR/lifecycle obligation, plan fix #11).

-- ── Extensions ────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ── Users ────────────────────────────────────────────────────────────────────
-- Authentication identities. Phase D uses JWT (Supabase Auth or Auth0).
-- The id here mirrors the sub claim from the JWT.

CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        NOT NULL UNIQUE,
    display_name  TEXT,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ
);

-- ── Workspaces ────────────────────────────────────────────────────────────────
-- The primary tenant unit. V1: one workspace per user.
-- V2: a workspace can have multiple members (workspace_members).

CREATE TABLE IF NOT EXISTS workspaces (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT        NOT NULL UNIQUE
                                CHECK (slug ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'),
    display_name  TEXT        NOT NULL,
    owner_user_id UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS workspaces_owner_idx ON workspaces(owner_user_id);

-- ── Workspace members ─────────────────────────────────────────────────────────
-- V1: only the owner is a member (role = 'owner'). V2: invite teammates.
-- RLS on all downstream tables keys on workspace_id, not user_id, so the join
-- here is the only place user identity meets workspace identity.

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    role          TEXT        NOT NULL DEFAULT 'member'
                                CHECK (role IN ('owner', 'member', 'viewer')),
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_members_user_idx
    ON workspace_members(user_id);

-- ── Subscriptions ─────────────────────────────────────────────────────────────
-- One subscription per workspace. Phase G wires Stripe webhook → UPDATE here.
-- entitlement maps directly to gtm_core.capabilities.Entitlement values.
-- Phase B resolver reads this field (via the backend session layer).

CREATE TABLE IF NOT EXISTS subscriptions (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id          UUID        NOT NULL UNIQUE
                                        REFERENCES workspaces(id) ON DELETE CASCADE,
    entitlement           TEXT        NOT NULL DEFAULT 'free'
                                        CHECK (entitlement IN ('free', 'pro', 'pro_plus')),
    stripe_subscription_id TEXT,                      -- null for free tier
    status                TEXT        NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active', 'past_due', 'canceled', 'trialing')),
    current_period_start  TIMESTAMPTZ,
    current_period_end    TIMESTAMPTZ,
    monthly_cost_cap_usd  NUMERIC(10,4) NOT NULL DEFAULT 50.00,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Keep updated_at current on every Stripe webhook write
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER subscriptions_updated_at
    BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ── Profiles ──────────────────────────────────────────────────────────────────
-- A workspace can bind multiple profiles (e.g. acme + template for an agency).
-- profile_name maps to profiles/<profile_name>/ in the skills repo.
-- is_default: only one default per workspace (enforced by partial unique index).

CREATE TABLE IF NOT EXISTS profiles (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    profile_name  TEXT        NOT NULL
                                CHECK (profile_name ~ '^[a-z0-9][a-z0-9_-]*$'),
    is_default    BOOLEAN     NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, profile_name)
);

-- Only one default profile per workspace
CREATE UNIQUE INDEX IF NOT EXISTS profiles_one_default_per_workspace
    ON profiles(workspace_id)
    WHERE is_default = true;

-- ── V1 bootstrap trigger ─────────────────────────────────────────────────────
-- When a new user is created (Phase D signup), auto-create their workspace,
-- add them as owner member, and create a free subscription in one transaction.

CREATE OR REPLACE FUNCTION bootstrap_workspace_for_new_user()
RETURNS TRIGGER AS $$
DECLARE
    ws_id UUID;
    ws_slug TEXT;
BEGIN
    -- Derive a slug from the email local-part, sanitised to [a-z0-9-]
    ws_slug := regexp_replace(
                 lower(split_part(NEW.email, '@', 1)),
                 '[^a-z0-9]+', '-', 'g'
               );
    -- Ensure uniqueness by appending a 6-char suffix
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
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_bootstrap_workspace
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION bootstrap_workspace_for_new_user();
