"""A5 step 3 — protocol 2: durable seq, opt-in `?since=` resume, cross-worker fan-out.

Protocol 1's suite (test_stream_protocol1.py) stays exactly as it was and must keep
passing: everything here is ADDITIVE, and a client that never sends `?since=` gets the
protocol-1 response. What is new is where `seq` comes from — the durable
``run_events.id`` assigned by the publish relay — and what a client can do with it.

Convention as elsewhere in tests/backend: no pytest-asyncio; each body runs under
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

from backend.services.runs import events as runs_events  # noqa: E402
from backend.services.runs import state as runs_state  # noqa: E402
from backend.services.runs import stream as runs_stream  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    SCOPE_MODULES,
    fake_broker,
    patch_everywhere,
    validate_frame,
)

WS_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


class EventsDb:
    """`runs` + the append-only `run_events` log, with the exact SQL the stream and the
    relay issue. Ids come from a counter, exactly as BIGSERIAL does."""

    def __init__(self, status: str = "running") -> None:
        self.status = status
        self.events: list[dict] = []
        self._seq = 0

    def append(self, event: str, data: dict) -> int:
        self._seq += 1
        self.events.append({"id": self._seq, "event": event, "data": data})
        return self._seq

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO run_events" in sql:
            return self.append(args[2], json.loads(args[3]))
        if "max(id)" in sql:
            return self.events[-1]["id"] if self.events else 0
        return None

    async def fetchrow(self, sql: str, *args):
        if "FROM runs" in sql:
            return {
                "id": RUN_ID,
                "status": self.status,
                "output": None,
                "error": None,
                "pending_gate": None,
                "pending_content": None,
            }
        return None

    async def fetch(self, sql: str, *args):
        if "FROM run_events" in sql:
            since, limit = args[2], args[3]
            return [e for e in self.events if e["id"] > since][:limit]
        return []

    async def execute(self, sql: str, *args):
        return None


def _scope_for(db):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield db

    return _scope


async def _never_disconnected() -> bool:
    return False


def _parse(chunks: list[str]) -> list[tuple[int | None, str, dict]]:
    """Reassemble ``(seq, event, data)`` triples, keeping the id line's absence visible."""
    frames: list[tuple[int | None, str, dict]] = []
    for chunk in chunks:
        seq: int | None = None
        event: str | None = None
        for line in chunk.split("\n"):
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event is not None:
                frames.append((seq, event, json.loads(line[6:])))
    return frames


async def _drain(gen, count: int) -> list[str]:
    return [await anext(gen) for _ in range(count)]


def _cleanup() -> None:
    runs_state._run_subscribers.pop(RUN_ID, None)
    runs_state._workspace_stream_count.pop(WS_ID, None)


# ── the relay: durable ids ────────────────────────────────────────────────────


def test_the_relay_persists_every_event_and_stamps_it_with_its_durable_id():
    """The seq a client resumes from IS the run_events row id — assigned by the INSERT,
    not by the connection. Without the relay running (any unit test, any process that
    never ran the lifespan) publishing still delivers locally, just with no id: that is
    protocol-1 behaviour, kept as the documented degraded mode."""
    db = EventsDb()

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)):
            q = runs_events._subscribe(RUN_ID)
            runs_events.start_relay(MagicMock())
            try:
                runs_events.publish_run_event(WS_ID, RUN_ID, "status", {"run_id": RUN_ID})
                runs_events.publish_run_event(WS_ID, RUN_ID, "done", {"run_id": RUN_ID})
                first = await asyncio.wait_for(q.get(), timeout=5)
                second = await asyncio.wait_for(q.get(), timeout=5)
            finally:
                await runs_events.stop_relay()
                runs_events._unsubscribe(RUN_ID, q)
        return first, second

    first, second = asyncio.run(_go())
    assert [e["event"] for e in db.events] == ["status", "done"]
    assert (first.seq, second.seq) == (1, 2), "the durable id is what reaches the stream"
    assert (first[0], second[0]) == ("status", "done")


def test_publishing_without_a_relay_is_protocol_1_behaviour():
    """T20 — the local transport was always complete on its own; the relay adds
    durability. A process with no relay must still deliver, with no seq."""
    _cleanup()
    q = runs_events._subscribe(RUN_ID)
    runs_events.publish_run_event(WS_ID, RUN_ID, "status", {"run_id": RUN_ID})
    frame = q.get_nowait()
    assert frame == ("status", {"run_id": RUN_ID})
    assert frame.seq is None
    runs_events._unsubscribe(RUN_ID, q)


# ── the stream ────────────────────────────────────────────────────────────────


def test_the_snapshot_advertises_protocol_2_and_the_watermark_it_was_taken_at():
    """The snapshot's id: is the resume point — everything after it is exactly what the
    snapshot does not already contain, because both were read in one transaction."""
    db = EventsDb()
    db.append("status", {"run_id": RUN_ID, "status": "running"})
    db.append("node", {"run_id": RUN_ID, "node_id": "plan", "state": "completed"})
    _cleanup()

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)):
            gen = runs_stream.run_event_stream(
                MagicMock(), WS_ID, RUN_ID, is_disconnected=_never_disconnected
            )
            chunks = await _drain(gen, 2)
            await gen.aclose()
        return chunks

    frames = _parse(asyncio.run(_go()))
    seq, event, data = frames[0]
    assert event == "snapshot"
    assert data["protocol"] == 2
    assert seq == 2, "the snapshot carries the watermark, not a per-connection zero"
    assert not validate_frame(event, data, seq)


def test_since_replays_exactly_the_missed_events_and_emits_no_snapshot():
    """T16 — a resume hands back the transition trail. A snapshot alongside it would be
    actively wrong: it describes state NEWER than the replayed events, so applying them
    after it would regress node states and re-open an already-passed gate."""
    db = EventsDb()
    db.append("status", {"run_id": RUN_ID, "status": "running"})
    db.append("node", {"run_id": RUN_ID, "node_id": "plan", "state": "running"})
    db.append("node", {"run_id": RUN_ID, "node_id": "plan", "state": "completed"})
    _cleanup()

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)):
            gen = runs_stream.run_event_stream(
                MagicMock(), WS_ID, RUN_ID, is_disconnected=_never_disconnected, since=1
            )
            chunks = await _drain(gen, 3)
            await gen.aclose()
        return chunks

    frames = _parse(asyncio.run(_go()))
    assert [e for _, e, _ in frames] == ["node", "node"], "no snapshot on a resume"
    assert [s for s, _, _ in frames] == [2, 3]
    assert [d["state"] for _, _, d in frames] == ["running", "completed"]


def test_a_client_too_far_behind_gets_a_fresh_snapshot_instead_of_a_replay():
    """T17 — past REPLAY_MAX a rebuild is cheaper than a catch-up, and replaying an
    unbounded backlog into a bounded queue would just trip the overflow close anyway."""
    db = EventsDb()
    for i in range(runs_stream.REPLAY_MAX + 5):
        db.append("status", {"run_id": RUN_ID, "status": "running", "i": i})
    _cleanup()

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)):
            gen = runs_stream.run_event_stream(
                MagicMock(), WS_ID, RUN_ID, is_disconnected=_never_disconnected, since=0
            )
            chunks = await _drain(gen, 2)
            await gen.aclose()
        return chunks

    frames = _parse(asyncio.run(_go()))
    assert frames[0][1] == "snapshot"


def test_a_live_frame_already_covered_by_the_snapshot_is_dropped():
    """T18 — the subscribe-then-read overlap window. A frame whose durable id is at or
    below the watermark is already IN the snapshot; emitting it again would hand the
    client a duplicate and (for a node) an out-of-date state."""
    db = EventsDb()
    db.append("node", {"run_id": RUN_ID, "node_id": "plan", "state": "completed"})
    _cleanup()

    async def _go():
        with patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)):
            gen = runs_stream.run_event_stream(
                MagicMock(), WS_ID, RUN_ID, is_disconnected=_never_disconnected
            )
            chunks = await _drain(gen, 2)  # retry preamble + snapshot (watermark = 1)
            # Arrives late but is already folded in (seq 1) — must be dropped…
            runs_events._publish_event(
                RUN_ID, "node", {"run_id": RUN_ID, "node_id": "plan", "state": "running"}, seq=1
            )
            # …while a genuinely newer one is delivered.
            runs_events._publish_event(RUN_ID, "done", {"run_id": RUN_ID, "status": "ok"}, seq=2)
            chunks.append(await anext(gen))
            await gen.aclose()
        return chunks

    frames = _parse(asyncio.run(_go()))
    assert [e for _, e, _ in frames] == ["snapshot", "done"]
    assert frames[-1][0] == 2


def test_a_ping_carries_no_id_line():
    """T19 — a heartbeat is not a run event. Advancing the client's resume point past one
    would make the next ?since= skip real events."""
    db = EventsDb()
    _cleanup()

    async def _go():
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)),
            _patched_heartbeat(0.01),
        ):
            gen = runs_stream.run_event_stream(
                MagicMock(), WS_ID, RUN_ID, is_disconnected=_never_disconnected
            )
            chunks = await _drain(gen, 3)  # preamble, snapshot, ping
            await gen.aclose()
        return chunks

    frames = _parse(asyncio.run(_go()))
    seq, event, _data = frames[-1]
    assert event == "ping"
    assert seq is None, "a ping must never advance the client's watermark"


def _patched_heartbeat(seconds: float):
    from unittest.mock import patch

    return patch.object(runs_stream, "_STREAM_HEARTBEAT_S", seconds)


# ── cross-worker fan-out ──────────────────────────────────────────────────────


def test_another_workers_event_reaches_this_workers_subscriber_with_its_id():
    """T15 — two workers, one run: a client streaming from the worker that is NOT running
    the run still sees every frame, carrying the ORIGINATING worker's durable id, so both
    clients resume from the same numbers."""
    _cleanup()
    q = runs_events._subscribe(RUN_ID)
    runs_events.on_broker_message(
        f"gtm:run:{RUN_ID}",
        {
            "worker": "worker-a",
            "seq": 41,
            "event": "node",
            "data": {"run_id": RUN_ID, "node_id": "plan", "state": "completed"},
        },
    )
    frame = q.get_nowait()
    assert frame[0] == "node"
    assert frame.seq == 41
    runs_events._unsubscribe(RUN_ID, q)


def test_a_relayed_event_is_published_to_the_run_channel_with_its_origin():
    """The publisher stamps its own WORKER_ID so the listener can drop its own messages
    coming back around — without it every frame would be delivered twice locally."""
    from backend.broker import WORKER_ID

    db = EventsDb()

    async def _go():
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _scope_for(db)),
            fake_broker() as broker,
        ):
            runs_events.start_relay(MagicMock())
            try:
                runs_events.publish_run_event(WS_ID, RUN_ID, "status", {"run_id": RUN_ID})
                for _ in range(50):
                    if broker.published:
                        break
                    await asyncio.sleep(0.01)
            finally:
                await runs_events.stop_relay()
            return broker.published

    published = asyncio.run(_go())
    assert len(published) == 1
    channel, payload = published[0]
    assert channel == f"gtm:run:{RUN_ID}"
    assert payload["worker"] == WORKER_ID
    assert (payload["event"], payload["seq"]) == ("status", 1)
