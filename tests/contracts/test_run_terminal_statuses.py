"""FL12/RL-04+ST-15 — ``_TERMINAL_STATUSES`` must track the `done` event's status enum
and the V017 CHECK constraint exactly, or a cancel's terminal write silently stops being
terminal to one of the three places that care (the in-process gate waiter's poll,
`GET /runs/{id}`'s 409 guard, and the wire contract).

``canceled`` becoming a real, distinct terminal status (rather than an alias for
``rejected``) is the whole point of this task: widening ``_TERMINAL_STATUSES`` without
widening the schema/DB CHECK in lockstep is exactly the drift this guards against.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.services.runs.state import _TERMINAL_STATUSES  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def test_terminal_statuses_is_exactly_ok_failed_rejected_canceled():
    assert _TERMINAL_STATUSES == frozenset({"ok", "failed", "rejected", "canceled"})


def test_terminal_statuses_matches_the_done_events_status_enum():
    """schemas/run-event.schema.json's `done` event is the wire contract for what a
    terminal run looks like to a client — its status enum must be exactly the Python
    terminal set, in either direction: a status the code treats as terminal that the
    schema doesn't advertise (or vice versa) is a client-visible contract break."""
    schema = json.loads((REPO / "schemas" / "run-event.schema.json").read_text(encoding="utf-8"))
    done_branch = next(
        branch for branch in schema["oneOf"] if branch["properties"]["event"]["const"] == "done"
    )
    enum = done_branch["properties"]["data"]["properties"]["status"]["enum"]
    assert set(enum) == set(_TERMINAL_STATUSES)


def test_v017_check_constraint_declares_canceled():
    """The DB CHECK is the enforcement layer under both of the above — a status the code
    or the schema calls terminal that Postgres refuses to store would fail every cancel
    at write time. A plain string-containment check is deliberate: parsing SQL is out of
    scope here, and the CHECK clause is small and stable enough that a substring match on
    the quoted literal is not a fragile assertion."""
    sql = (REPO / "backend" / "schema" / "V017__run_gates_events.sql").read_text(encoding="utf-8")
    assert "'canceled'" in sql
