-- V046: dispatch intents (F4) + a cross-process cancel request (F8).
-- Always-on hardening PRD (2026-10-19).
--
-- dispatch_intents — "a send that cannot prove it is not a duplicate must not guess".
--   Every external send (an enrollment into a sequencer, a publish) writes an INTENT row before the
--   outbound call and OUTCOME rows after it. A crash, timeout or 5xx leaves an intent with no proven
--   outcome, and that BLOCKS a repeat until a human resolves it (POST /v1/dispatch-intents/{batch}/resolve).
--
--   APPEND-ONLY by grant: gtm_api may SELECT and INSERT and nothing else, so a row is never edited or
--   removed — release is a NEW row (failed / not_sent / removed), and a recipient's state is the LATEST row
--   for (workspace, sequence, recipient). The same fold the VPS file ledger (gtm_core/dispatch_intents.py)
--   computes; both stores are held to the same scenarios.
--
--   Atomicity: a claim takes pg_advisory_xact_lock(hashtext(workspace || sequence)) so two dispatchers racing
--   for overlapping batches serialise, and the partial UNIQUE index on claim rows is the backstop — it holds
--   even if the advisory lock were ever skipped. `generation` makes a re-claim after a release legal: it is
--   (number of release rows for that recipient) + 1, so a plain UNIQUE (…, generation) lets exactly one
--   claimant win each generation without ever needing an UPDATE.
--
-- runs.cancel_requested_at — F8. The backend's cancel flag was per-process memory, so a run executing in the
--   separate worker saw it only at its next DB status check. A column the worker reads on its heartbeat tick
--   (a workspace-scoped read of the runs it holds) makes cancel work across processes; the broker wake is
--   latency only ("Redis is latency, never correctness"). NULL means "no request" — the one permissive
--   default, justified: no request is not a request.

CREATE TABLE IF NOT EXISTS dispatch_intents (
    id            BIGSERIAL   PRIMARY KEY,
    workspace_id  UUID        NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    batch_id      TEXT        NOT NULL,
    -- the sequence id, or 'publish:<platform>' for a publish; '*' for the workspace-wide hold
    sequence_id   TEXT        NOT NULL,
    recipient_key TEXT        NOT NULL,
    kind          TEXT        NOT NULL CHECK (kind IN (
                      'claim', 'dispatched', 'failed', 'unknown', 'sent', 'not_sent', 'removed',
                      'hold', 'hold_cleared')),
    generation    INTEGER     NOT NULL DEFAULT 0 CHECK (generation >= 0),
    tool          TEXT,
    label         TEXT,
    detail        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- exactly one winner per (recipient, generation): the DB-level backstop behind the advisory lock
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_intents_one_claim
    ON dispatch_intents (workspace_id, sequence_id, recipient_key, generation)
    WHERE kind = 'claim';
CREATE INDEX IF NOT EXISTS dispatch_intents_recipient_idx
    ON dispatch_intents (workspace_id, sequence_id, recipient_key, id);
CREATE INDEX IF NOT EXISTS dispatch_intents_batch_idx
    ON dispatch_intents (workspace_id, batch_id);

ALTER TABLE dispatch_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE dispatch_intents FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS dispatch_intents_isolation ON dispatch_intents;
CREATE POLICY dispatch_intents_isolation ON dispatch_intents
    USING      (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- gtm_api is named in the REVOKE on purpose: earlier migrations leave default privileges that hand it the
-- full DML set on every new table. Append-only means INSERT and SELECT, and nothing that can rewrite history.
REVOKE ALL ON dispatch_intents FROM PUBLIC, gtm_api;
GRANT SELECT, INSERT ON dispatch_intents TO gtm_api;
GRANT USAGE, SELECT ON SEQUENCE dispatch_intents_id_seq TO gtm_api;

ALTER TABLE runs ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ;
