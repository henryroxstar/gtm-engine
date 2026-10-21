-- V014: invalidate JWTs issued before a password change.
--
-- Why this migration exists
-- -------------------------
-- The /auth/refresh endpoint mints a fresh access+refresh pair from any signature-valid
-- refresh token, with no per-use state check. A stolen refresh token therefore survives a
-- password reset for its full TTL (default 30 days) — changing the password does NOT lock the
-- attacker out (Strix pentest VULN-0003). This column records when a user last changed their
-- password; the refresh handler rejects any token whose `iat` predates it (backend/auth.py
-- token_predates_password_change + backend/routers/auth.py refresh).
--
-- NULL = the user has never changed their password since registration, so all their
-- outstanding tokens stay valid — no forced re-login on deploy. The column is populated on
-- the next password change (backend/routers/account.py). Plain nullable ALTER: transaction-safe
-- (no -- gtm:no-transaction opt-out), and `gtm_api` already holds UPDATE on `users`, so no new
-- GRANT is needed. `users` is not an RLS table.

ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMPTZ;
