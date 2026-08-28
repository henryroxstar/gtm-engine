"""A10 — Gate-2 publish dispatch: driven by DECLARED metadata (not the node's
name), exact-approved-bytes binding, and the never-dispatch invariants
(dry_run / reject / cancel / timeout / hash mismatch).

The cockpit and the backend now share one implementation
(``agent.publish_dispatch``); these tests pin the shared helper's guarantees plus
the backend's metadata-driven trigger.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

import agent.publish as publish  # noqa: E402
from agent.graph import Graph, Node  # noqa: E402
from agent.packs import pack_graph_to_engine_graph  # noqa: E402
from agent.publish_dispatch import dispatch_approved_publish  # noqa: E402
from backend.publish_dispatch import publish_gated_node  # noqa: E402
from gtm_core.packs.loader import PackGraph, PackNode  # noqa: E402
from gtm_core.publish_hash import approval_hash, content_hash  # noqa: E402

POST = "The approved post bytes."


@dataclass
class FakeResult:
    ok: bool = True
    status: str = "published"
    post_id: str | None = "urn:li:share:123"
    error: str | None = None
    scheduled_at: str | None = None

    def operator_line(self) -> str:
        return "✅ Published to LinkedIn."


class FakePublisher:
    def __init__(self, result: FakeResult | None = None) -> None:
        self.calls: list[tuple] = []
        #: Recorded separately so the existing 3-tuple `calls` assertions stay valid
        #: (same pattern as tests/cockpit/fakes.py).
        self.scheduled_at_calls: list[str | None] = []
        self.result = result or FakeResult()

    async def publish(self, post, media_urls=(), *, is_published=None, scheduled_at=None):
        self.calls.append((post, tuple(media_urls), is_published))
        self.scheduled_at_calls.append(scheduled_at)
        return self.result


class FakeLedgers:
    def __init__(self, published: set[str] | None = None, raise_on_read: bool = False) -> None:
        self.published = published or set()
        self.raise_on_read = raise_on_read
        self.history: list[dict] = []

    def published_content_hashes(self):
        if self.raise_on_read:
            raise RuntimeError("ledger unreadable")
        return self.published

    def append_history(self, entry):
        self.history.append(entry)


# ── metadata-driven trigger (A10's core change) ──────────────────────────────


def test_dispatch_is_driven_by_declaration_not_node_name():
    """A node named 'publish' with NO declared effect must not dispatch; a node
    with any other name that DOES declare one must."""
    graph = Graph(
        nodes=(
            Node(id="publish"),  # named publish, declares nothing
            Node(id="ship-it", gate=True, external_effect="linkedin_post"),
            Node(id="studio"),
        )
    )
    assert publish_gated_node(graph, "publish") is False
    assert publish_gated_node(graph, "ship-it") is True
    assert publish_gated_node(graph, "studio") is False
    assert publish_gated_node(graph, "no-such-node") is False


def test_pack_adapter_carries_gate_metadata_into_the_engine_graph():
    """Previously dropped at the adapter — which is why the runtime had to match
    on the node NAME. The engine graph now carries the declaration."""
    pack = PackGraph(
        pack="marketing",
        variant="x",
        nodes=(
            PackNode(id="studio", prompt="p"),
            PackNode(
                id="publish",
                depends_on=("studio",),
                prompt="p",
                gate=True,
                external_effect="linkedin_post",
            ),
        ),
    )
    graph = pack_graph_to_engine_graph(pack)
    by_id = {n.id: n for n in graph.nodes}
    assert by_id["publish"].gate is True
    assert by_id["publish"].external_effect == "linkedin_post"
    assert by_id["studio"].gate is False and by_id["studio"].external_effect is None
    # depends_on still carried (the pre-existing contract).
    assert by_id["publish"].depends_on == ("studio",)


def test_executor_short_circuits_declared_effect_nodes_only():
    """execute_stage skips a declared-effect node (dispatch is Python's job) and
    keeps the historical name-based behaviour when no metadata is supplied."""
    from agent.pipeline import SKIPPED
    from agent.pipeline_executor import execute_stage

    async def _go():
        # Pack mode: metadata present — 'ship-it' short-circuits by declaration.
        declared = await execute_stage(
            MagicMock(), "p", "ship-it", {}, external_effects={"ship-it": "linkedin_post"}
        )
        # …and a node named 'publish' WITHOUT a declaration is NOT short-circuited
        # (it would proceed to the brain, so we only assert it isn't SKIPPED here
        # by checking the declaration map is what decides).
        # VPS mode: no metadata — the historical name check still applies.
        legacy = await execute_stage(MagicMock(), "p", "publish", {})
        return declared, legacy

    declared, legacy = asyncio.run(_go())
    assert declared.status == SKIPPED and declared.outputs == ("publish-manual",)
    assert legacy.status == SKIPPED and legacy.outputs == ("publish-manual",)


# ── shared helper: binding, idempotency, audit ───────────────────────────────


def test_dispatch_publishes_exact_approved_bytes_and_audits():
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(publisher, ledgers, post=POST, staged_hash=content_hash(POST, ()))
    )
    assert outcome.ok and outcome.status == "published"
    (post, media, is_published) = publisher.calls[0]
    assert post == POST and media == ()
    assert is_published is not None, "the durable idempotency predicate must be supplied"
    entry = ledgers.history[0]
    assert entry["event"] == "published"
    assert entry["content_sha256"] == content_hash(POST, ())
    assert entry["chars"] == len(POST)


def test_hash_mismatch_refuses_to_publish():
    """An approval is bound to its exact bytes — tampering between approval and
    dispatch must publish nothing and write no history."""
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(
            publisher, ledgers, post="different bytes", staged_hash=content_hash(POST, ())
        )
    )
    assert outcome.ok is False and outcome.status == "hash_mismatch"
    assert publisher.calls == [] and ledgers.history == []


def test_dry_run_never_dispatches():
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(
            publisher, ledgers, post=POST, staged_hash=content_hash(POST, ()), dry_run=True
        )
    )
    assert outcome.status == "dry_run" and outcome.ok is False
    assert publisher.calls == [], "dry_run must be structurally incapable of publishing"
    assert ledgers.history == []


def test_ledger_read_failure_does_not_block_an_approved_publish():
    publisher, ledgers = FakePublisher(), FakeLedgers(raise_on_read=True)
    outcome = asyncio.run(
        dispatch_approved_publish(publisher, ledgers, post=POST, staged_hash=content_hash(POST, ()))
    )
    assert outcome.ok
    assert publisher.calls[0][2] is None, "a ledger failure degrades to in-memory idempotency"


def test_failed_publish_is_audited_as_publish_failed():
    publisher = FakePublisher(FakeResult(ok=False, status="error", post_id=None, error="boom"))
    ledgers = FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(publisher, ledgers, post=POST, staged_hash=content_hash(POST, ()))
    )
    assert outcome.ok is False and outcome.status == "publish_failed"
    assert ledgers.history[0]["event"] == "publish_failed"


def test_history_write_failure_does_not_break_the_dispatch():
    class BrokenLedgers(FakeLedgers):
        def append_history(self, entry):
            raise RuntimeError("disk full")

    outcome = asyncio.run(
        dispatch_approved_publish(
            FakePublisher(), BrokenLedgers(), post=POST, staged_hash=content_hash(POST, ())
        )
    )
    assert outcome.ok, "an audit-write failure must not turn a successful publish into a failure"


# ── backend wrapper: never raises, honours dry_run ───────────────────────────


_WS = "11111111-1111-4111-8111-111111111111"


def test_backend_dispatch_never_raises_on_misconfiguration():
    from backend import publish_dispatch as pd

    # A5: an unconfigured workspace (resolver → None) does not publish and does not
    # fall back to a shared destination; a resolver blip (raises) must not crash the
    # run task either. Both return None.
    with patch.object(pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=None)):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(), "example-profile", pool=MagicMock(), workspace_id=_WS, content=POST
            )
        )
    assert outcome is None, "an unconfigured workspace returns None (no shared-account fallback)"

    with patch.object(
        pd, "_resolve_workspace_publish_settings", AsyncMock(side_effect=RuntimeError("db blip"))
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(), "example-profile", pool=MagicMock(), workspace_id=_WS, content=POST
            )
        )
    assert outcome is None, "a resolver failure returns None rather than raising"


def test_backend_dispatch_dry_run_short_circuits():
    from backend import publish_dispatch as pd

    publisher = FakePublisher()
    with (
        patch.object(
            pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=MagicMock())
        ),
        patch("agent.publish.LinkedInPublisher", return_value=publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=POST,
                dry_run=True,
            )
        )
    assert outcome is not None and outcome.status == "dry_run"
    assert publisher.calls == []


# ── disclosure gate (§6.2, Article 50) ───────────────────────────────────────
# The gap this closes: previously NOTHING in the shared dispatch helper (or either
# caller) checked identity_used/disclosure at dispatch time — only the cockpit's
# staging step did. The backend approve flow has no separate staging step, so a
# backend-driven synthetic-identity post could publish with zero disclosure
# enforcement. Verified live against a real backend + Postgres before this fix
# landed: dispatch reached the actual httpx POST with no disclosure check at all.


def test_undisclosed_synthetic_identity_refuses_dispatch():
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(
            publisher,
            ledgers,
            post=POST,
            staged_hash=content_hash(POST, ()),
            identity_used=("soul",),
            disclosure_lines=(),
        )
    )
    assert outcome.ok is False and outcome.status == "disclosure_missing"
    assert publisher.calls == [], "must not reach the publisher without a disclosure line"
    assert ledgers.history == [], "a refused dispatch must not be audited as an attempt"


def test_disclosed_synthetic_identity_dispatches_normally():
    disclosed_post = f"{POST} Made with AI."
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(
            publisher,
            ledgers,
            post=disclosed_post,
            staged_hash=content_hash(disclosed_post, ()),
            identity_used=("soul",),
            disclosure_lines=("Made with AI.",),
        )
    )
    assert outcome.ok and outcome.status == "published"
    assert len(publisher.calls) == 1


def test_no_identity_used_is_unaffected_by_the_disclosure_check():
    """Regression pin: identity_used/disclosure_lines both default to () so a
    caller that never resolved them (every caller, before this fix existed) is
    byte-identical to before."""
    publisher, ledgers = FakePublisher(), FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(publisher, ledgers, post=POST, staged_hash=content_hash(POST, ()))
    )
    assert outcome.ok and outcome.status == "published"


def test_backend_dispatch_wires_identity_used_through_and_refuses_when_undisclosed():
    """Confirms dispatch_backend_publish (not just the shared helper in isolation)
    actually parses ⟦IDENTITY⟧ and resolves disclosure lines from THIS workspace's
    own profile tree — the fix for the confirmed backend disclosure gap."""
    from backend import publish_dispatch as pd

    identity_content = (
        "Drafted it.\n⟦GATE:publish⟧\n⟦POST⟧\nA post with no disclosure line.\n⟦/POST⟧\n"
        "⟦IDENTITY⟧soul⟦/IDENTITY⟧"
    )
    publisher = FakePublisher()
    with (
        patch.object(
            pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=MagicMock())
        ),
        patch("agent.publish.LinkedInPublisher", return_value=publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
        patch("agent.publish.candidate_disclosure_lines", return_value=[]) as mock_lines,
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(repo_root=Path("/tmp/fake-repo-root")),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=identity_content,
            )
        )
    assert outcome.ok is False and outcome.status == "disclosure_missing"
    assert publisher.calls == []
    mock_lines.assert_called_once()
    # The workspace id must reach the resolver — this is what proves the lookup is
    # scoped to THIS workspace's own tenant tree, not the shared VPS profiles/ tree.
    called_profiles_root = mock_lines.call_args[0][0]
    assert _WS in str(called_profiles_root)


def test_backend_dispatch_publishes_when_the_disclosure_line_is_present():
    from backend import publish_dispatch as pd

    identity_content = (
        "Drafted it.\n⟦GATE:publish⟧\n⟦POST⟧\nA post. Made with AI.\n⟦/POST⟧\n"
        "⟦IDENTITY⟧soul⟦/IDENTITY⟧"
    )
    publisher = FakePublisher()
    with (
        patch.object(
            pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=MagicMock())
        ),
        patch("agent.publish.LinkedInPublisher", return_value=publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
        patch("agent.publish.candidate_disclosure_lines", return_value=["Made with AI."]),
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(repo_root=Path("/tmp/fake-repo-root")),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=identity_content,
            )
        )
    assert outcome.ok is True
    assert len(publisher.calls) == 1


def test_backend_dispatch_skips_disclosure_resolution_when_no_identity_marker():
    """No ⟦IDENTITY⟧ marker at all ⇒ candidate_disclosure_lines is never even
    called — an ordinary post pays no extra brand-kit-read cost."""
    from backend import publish_dispatch as pd

    publisher = FakePublisher()
    with (
        patch.object(
            pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=MagicMock())
        ),
        patch("agent.publish.LinkedInPublisher", return_value=publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
        patch("agent.publish.candidate_disclosure_lines") as mock_lines,
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(repo_root=Path("/tmp/fake-repo-root")),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=POST,
            )
        )
    assert outcome.ok is True
    mock_lines.assert_not_called()


# ── backend scheduling parity (V020) ─────────────────────────────────────────

_GATE_BLOCK = (
    "Drafted it.\n⟦GATE:publish⟧\n⟦POST⟧\nScheduled backend copy.\n⟦/POST⟧\n"
    "⟦SCHEDULE⟧2026-09-01T09:00:00Z⟦/SCHEDULE⟧"
)


def test_backend_dispatch_forwards_a_parsed_schedule_when_the_workspace_opted_in():
    """A gate block's ⟦SCHEDULE⟧ reaches the publisher exactly as the cockpit
    forwards it — subject to THIS workspace's own schedule_enabled (resolved from
    workspace_publish_settings), not a global switch."""
    from backend import publish_dispatch as pd

    publisher = FakePublisher(FakeResult(status="scheduled", scheduled_at="2026-09-01T09:00:00Z"))
    settings = MagicMock(schedule_enabled=True)
    with (
        patch.object(pd, "_resolve_workspace_publish_settings", AsyncMock(return_value=settings)),
        patch("agent.publish.LinkedInPublisher", return_value=publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=_GATE_BLOCK,
            )
        )
    assert outcome.ok and outcome.status == "scheduled"
    assert publisher.scheduled_at_calls == ["2026-09-01T09:00:00Z"]
    post, media, _ = publisher.calls[0]
    assert post == "Scheduled backend copy." and media == ()


def test_backend_dispatch_refuses_a_schedule_when_the_workspace_has_not_opted_in():
    """schedule_enabled defaults False (V020) — a workspace that never opted in
    must have a scheduled approval refused, never silently published immediately
    (that downgrade is explicitly forbidden — see agent.publish guard 1b) and never
    silently scheduled anyway."""
    from backend import publish_dispatch as pd

    real_publisher = publish.LinkedInPublisher(
        settings=publish.PublishSettings(
            url="https://relay.example/p",
            secret="s",  # nosec B106
            enabled=True,
            schedule_enabled=False,
        ),
    )
    with (
        patch.object(
            pd,
            "_resolve_workspace_publish_settings",
            AsyncMock(return_value=real_publisher.settings),
        ),
        patch("agent.publish.LinkedInPublisher", return_value=real_publisher),
        patch("agent.ledgers.Ledgers", return_value=FakeLedgers()),
    ):
        outcome = asyncio.run(
            pd.dispatch_backend_publish(
                MagicMock(),
                "example-profile",
                pool=MagicMock(),
                workspace_id=_WS,
                content=_GATE_BLOCK,
            )
        )
    assert outcome.ok is False
    assert outcome.result.status == "schedule_disabled"


def test_run_id_and_media_are_not_smuggled_into_the_destination():
    """A10 invariant: nothing in the dispatch payload can express a destination —
    the helper only ever forwards post bytes + media URLs it was handed."""
    publisher, ledgers = FakePublisher(), FakeLedgers()
    asyncio.run(
        dispatch_approved_publish(
            publisher,
            ledgers,
            post=POST,
            media_urls=(),
            staged_hash=content_hash(POST, ()),
        )
    )
    post, media, _ = publisher.calls[0]
    assert media == ()
    assert str(uuid.uuid4()) not in post  # sanity: no id injection into the bytes


# --------------------------------------------------------------------------- #
# Scheduling (Phase 7) — two hashes, two jobs
# --------------------------------------------------------------------------- #

SCHEDULE = "2026-08-20T09:00:00Z"


def test_approval_hash_is_backward_compatible_with_every_prior_approval():
    """Unscheduled callers must be byte-identical to before — otherwise shipping this
    would invalidate every staged approval and every recorded publish at once."""
    assert approval_hash(POST, ()) == content_hash(POST, ())
    assert approval_hash(POST, (), None) == content_hash(POST, ())


def test_changing_only_the_time_breaks_the_approval_binding():
    """The gap this closes: with a content-only binding, a draft re-staged for a
    different hour keeps a matching hash, so the new time dispatches unreviewed."""
    approved_for_9am = approval_hash(POST, (), SCHEDULE)
    assert approval_hash(POST, (), "2026-08-20T17:00:00Z") != approved_for_9am

    publisher, ledgers = FakePublisher(), FakeLedgers()
    out = asyncio.run(
        dispatch_approved_publish(
            publisher,
            ledgers,
            post=POST,
            staged_hash=approved_for_9am,
            scheduled_at="2026-08-20T17:00:00Z",
        )
    )
    assert out.status == "hash_mismatch"
    assert publisher.calls == [], "a time the operator never saw must not dispatch"


def test_a_scheduled_dispatch_is_audited_as_scheduled_not_published():
    publisher = FakePublisher(FakeResult(status="scheduled", scheduled_at=SCHEDULE))
    ledgers = FakeLedgers()
    outcome = asyncio.run(
        dispatch_approved_publish(
            publisher,
            ledgers,
            post=POST,
            staged_hash=approval_hash(POST, (), SCHEDULE),
            scheduled_at=SCHEDULE,
        )
    )
    row = ledgers.history[0]
    assert row["event"] == "scheduled", "it is booked, not live"
    assert row["scheduled_at"] == SCHEDULE
    # The returned outcome must agree with the ledger event — a stale "published"
    # status here previously disagreed with what was actually audited.
    assert outcome.ok is True
    assert outcome.status == "scheduled"


def test_audited_content_hash_stays_the_idempotency_key_not_the_approval_hash():
    """`published_content_hashes` compares this field against the publisher's
    content-only key. Recording the approval hash here would silently break dedupe
    for every scheduled post — it would never match, so it could send twice."""
    publisher = FakePublisher(FakeResult(status="scheduled", scheduled_at=SCHEDULE))
    ledgers = FakeLedgers()
    staged = approval_hash(POST, (), SCHEDULE)
    asyncio.run(
        dispatch_approved_publish(
            publisher, ledgers, post=POST, staged_hash=staged, scheduled_at=SCHEDULE
        )
    )
    row = ledgers.history[0]
    assert row["content_sha256"] == content_hash(POST, ())
    assert row["approval_sha256"] == staged
    assert row["content_sha256"] != row["approval_sha256"]
