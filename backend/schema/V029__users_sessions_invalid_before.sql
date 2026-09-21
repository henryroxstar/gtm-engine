-- V029: invalidate JWTs issued before session logout.
--
-- Why this migration exists
-- -------------------------
-- POST /v1/auth/logout stamps when a user revokes their sessions (AU-03, FL16).
-- The /auth/refresh endpoint checks this column to reject any refresh token
-- whose `iat` predates the logout event (access tokens expire naturally per TTL).
--
-- NULL = the user has never logged out, so existing tokens remain valid.
-- users is not an RLS-scoped table; runtime role gtm_api already holds UPDATE.

ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before TIMESTAMPTZ;
