"""ST-09 — run_nodes.error reaches the wire: GET /v1/runs/{id}.nodes[] and the SSE
snapshot's nodes[] each carry `error` when a node failed with one. Both read paths
build the entry with the one `_wire_node`; a live pack run's `node` event carrying it is
covered separately in
test_run_lifecycle_matrix.py::test_node_failure_mid_run_fails_run_and_frees_slot.
"""

from __future__ import annotations

import os

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.services.runs import queries as runs_queries  # noqa: E402
from backend.services.runs import stream as runs_stream  # noqa: E402

_wire_node = runs_queries._wire_node


def test_a_failed_node_carries_its_error():
    assert _wire_node({"node_id": "plan", "state": "failed", "error": "boom"}) == {
        "id": "plan",
        "state": "failed",
        "error": "boom",
    }


def test_error_is_absent_unless_the_node_failed_with_one():
    """The schema says `error` is present only when state is 'failed', matching the
    `node` event. A stale error on a node that is no longer failed is not surfaced."""
    assert _wire_node({"node_id": "plan", "state": "completed", "error": "old"}) == {
        "id": "plan",
        "state": "completed",
    }
    assert _wire_node({"node_id": "plan", "state": "failed", "error": None}) == {
        "id": "plan",
        "state": "failed",
    }
    assert _wire_node({"node_id": "radar", "state": "completed"}) == {
        "id": "radar",
        "state": "completed",
    }


def test_the_snapshot_and_the_poll_share_one_node_shape():
    """A client that reconnects must see exactly the shape a client that polls sees, so
    there is one implementation, not two copies that can drift."""
    assert runs_stream._wire_node is runs_queries._wire_node
