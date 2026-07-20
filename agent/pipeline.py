"""Deterministic stage runner for the content pipeline (the workflow, not an engine).

The pipeline ``radar → plan → research → studio → publish`` is a *well-defined sequence*.
Per Anthropic's guidance, a well-defined sequence should be a **workflow** with deterministic
code paths and the model as a *step within* each stage — not a model-driven loop that keeps the
in-flight state in conversation context. This module is that thin code spine:

  - It owns the **stage order** and advances the run through it.
  - It reads/writes the EXISTING ``RunManifest`` (``schemas/run-manifest.schema.json``) via
    :class:`agent.ledgers.Ledgers`, finally *consuming* the per-stage status the schema was built
    to carry, so a run that died mid-pipeline **resumes from the first non-complete stage**
    instead of restarting from scratch.
  - It serialises a profile's run under the cross-process :func:`agent.locks.profile_lock`, so the
    cron ``hermes-brain`` and the Telegram ``hermes-cockpit`` cannot race manifest/plan writes on
    the shared ``content/`` volume.

The *work* of each stage is delegated to an injectable ``executor`` coroutine. That keeps the
orchestration logic (sequence, resume, manifest transitions, locking) pure and unit-testable with
a fake executor, while the real executor drives the LLM/skill for that stage. Human gates (plan,
publish) are modelled by the executor returning :data:`AWAITING_APPROVAL`: the runner persists that
state and **stops**, to be resumed by a later call once the cockpit records the operator's approval.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .graph import Graph, linear_graph
from .ledgers import Ledgers
from .locks import profile_lock

# Canonical stage order (Phase-1 vertical slice). The runner is authoritative on order;
# the manifest is the status record.
STAGES: tuple[str, ...] = ("radar", "plan", "research", "studio", "publish")

#: The linear pipeline expressed as a graph — same order, same semantics as STAGES.
DEFAULT_GRAPH: Graph = linear_graph(STAGES)

# Stage outcome statuses (a subset of the run-manifest stage-status enum).
OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"
RUNNING = "running"
PENDING = "pending"
AWAITING_APPROVAL = "awaiting_approval"  # runner-internal; persisted as the stage's status

#: Statuses that count as "this stage is finished, move on" when computing the resume point.
_COMPLETE = frozenset({OK, SKIPPED})


def _utc_now_iso() -> str:
    """UTC ISO-8601 with trailing ``Z`` (matches the run-manifest ``date-time`` format)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class StageOutcome:
    """What an executor returns for one stage."""

    status: str  # OK | FAILED | AWAITING_APPROVAL | SKIPPED
    error: str | None = None
    outputs: tuple[str, ...] = ()


# executor(stage_name, manifest) -> StageOutcome
StageExecutor = Callable[[str, dict], Awaitable[StageOutcome]]


def new_manifest(run_id: str, trigger: str, profile: str, graph: Graph = DEFAULT_GRAPH) -> dict:
    """Build a fresh manifest with every stage ``pending`` (conforms to the run-manifest schema).

    Stamps each stage entry's ``depends_on`` from the graph — additive to the schema, so a
    manifest is self-describing about its shape; readers that predate this field ignore it.
    """
    stages = []
    for node in graph.nodes:
        entry: dict = {"name": node.id, "status": PENDING}
        if node.depends_on:
            entry["depends_on"] = list(node.depends_on)
        stages.append(entry)
    return {
        "run_id": run_id,
        "trigger": trigger,
        "profile": profile,
        "stages": stages,
    }


def resume_index(manifest: dict, stages: Sequence[str] = STAGES) -> int:
    """Index of the first stage that is NOT complete (``ok``/``skipped``) → where to resume.

    Pure. Returns ``len(stages)`` when every stage is complete (nothing left to do). A stage
    that is missing from the manifest, or is ``pending``/``running``/``failed``/awaiting, is the
    resume point — so a crash during ``running`` re-runs that stage, and a ``failed`` stage is
    retried from itself rather than from the top.
    """
    status_by_name = {
        s.get("name"): s.get("status") for s in manifest.get("stages", []) if isinstance(s, dict)
    }
    for i, name in enumerate(stages):
        if status_by_name.get(name) not in _COMPLETE:
            return i
    return len(stages)


def runnable_frontier(graph: Graph, manifest: dict) -> list[str]:
    """Node ids in ``graph`` whose dependencies are all complete and are themselves not.

    Pure, graph-native generalization of :func:`resume_index`. For any :func:`linear_graph`
    (a straight chain), this returns exactly the single next-to-run stage — the same stage
    ``resume_index`` would point to — or ``[]`` when every stage is complete; a branching
    graph (e.g. a diamond) can return more than one runnable id in the same frontier, all in
    declaration order.
    """
    status_by_name = {
        s.get("name"): s.get("status") for s in manifest.get("stages", []) if isinstance(s, dict)
    }
    frontier = []
    for node in graph.nodes:
        if status_by_name.get(node.id) in _COMPLETE:
            continue
        if all(status_by_name.get(dep) in _COMPLETE for dep in node.depends_on):
            frontier.append(node.id)
    return frontier


def terminal_status(manifest: dict) -> str:
    """Reduce a returned run manifest to one terminal status string.

    ``PipelineRunner.run`` returns the *manifest*, not a status, and it stops early on the first
    ``FAILED`` or ``AWAITING_APPROVAL`` stage. So the run's outcome is: ``failed`` if any stage
    failed (surface it — a cron run must exit non-zero), else ``awaiting_approval`` if any stage
    is gated (push Gate 1), else ``ok``. Callers must not compare the manifest dict to a status
    string directly — that silently never matches.
    """
    statuses = {s.get("status") for s in manifest.get("stages", []) if isinstance(s, dict)}
    if FAILED in statuses:
        return FAILED
    if AWAITING_APPROVAL in statuses:
        return AWAITING_APPROVAL
    return OK


@dataclass
class PipelineRunner:
    """Runs a profile's content pipeline deterministically, with durable resume + locking."""

    cfg: object  # agent.config.Config (typed loosely to keep this import-light)
    profile: str
    graph: Graph = DEFAULT_GRAPH
    ledgers: Ledgers = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.ledgers is None:
            self.ledgers = Ledgers(self.cfg, self.profile)

    # -- manifest helpers ---------------------------------------------------- #

    def _stage_entry(self, manifest: dict, name: str) -> dict:
        for s in manifest["stages"]:
            if s.get("name") == name:
                return s
        entry = {"name": name, "status": PENDING}
        manifest["stages"].append(entry)
        return entry

    def _persist(self, manifest: dict) -> None:
        """Write the manifest to ``runs/<run_id>.json`` (caller holds the profile lock)."""
        self.ledgers.write_run_manifest(manifest)

    # -- the run loop -------------------------------------------------------- #

    async def run(
        self,
        run_id: str,
        trigger: str,
        executor: StageExecutor,
        *,
        manifest: dict | None = None,
    ) -> dict:
        """Run (or resume) the pipeline for ``run_id``, returning the final manifest.

        Resumes via :func:`runnable_frontier`, recomputed after every batch so a node that
        unblocks new dependents (e.g. a diamond join) is picked up without a second call.
        A frontier with more than one runnable node (a fan-out) is dispatched **concurrently**
        via ``asyncio.gather`` — every node in the batch is marked ``RUNNING`` and persisted
        *before* any of them is dispatched, and the whole batch always runs to completion
        (never cancelled mid-flight — an in-flight paid call finishes rather than being
        abandoned). The run stops — persisting state — once the batch completes, if ANY node
        in it ``FAILED`` (surface, don't barrel on) or returned ``AWAITING_APPROVAL`` (a human
        gate); a frontier of exactly one node behaves identically to the pre-E-4 sequential
        runner (this is what the differential/equivalence tests pin). The whole run is
        serialised per profile via :func:`agent.locks.profile_lock`, held for its entire
        duration — concurrent nodes in a batch run under that same single lock, never a
        second one.
        """
        content_root = self.cfg.content_root
        with profile_lock(content_root, self.profile):
            if manifest is None:
                manifest = self._load_or_create(run_id, trigger)

            from .budget import vps_budget_ok

            while True:
                frontier = runnable_frontier(self.graph, manifest)
                if not frontier:
                    return manifest

                # §R2 budget guard, checked before EVERY dispatch batch (not just once at
                # run start) — a per-node/per-batch guard, since a concurrent fan-out or a
                # long resumed run can cross the cap mid-flight. Fail-open on a ledger read
                # error (the SDK per-run cap backstops). Marks the first pending node in this
                # batch FAILED so terminal_status() == FAILED.
                if not vps_budget_ok(self.cfg, self.profile):
                    entry = self._stage_entry(manifest, frontier[0])
                    entry["status"] = FAILED
                    entry["ended"] = _utc_now_iso()
                    entry["error"] = "monthly cost cap reached — run aborted before any paid call"
                    self._persist(manifest)
                    return manifest

                batch: list[tuple[str, dict]] = []
                for name in frontier:
                    entry = self._stage_entry(manifest, name)
                    entry["status"] = RUNNING
                    entry["started"] = _utc_now_iso()
                    entry.pop("error", None)
                    batch.append((name, entry))
                self._persist(manifest)

                await asyncio.gather(
                    *(self._run_node(name, entry, manifest, executor) for name, entry in batch)
                )

                if any(entry["status"] in (FAILED, AWAITING_APPROVAL) for _, entry in batch):
                    return manifest

    async def _run_node(
        self, name: str, entry: dict, manifest: dict, executor: StageExecutor
    ) -> None:
        """Run one node's executor and persist its outcome into ``entry`` (mutated in place).

        Never raises — a stage crash becomes a ``FAILED`` entry, not a runner crash, exactly
        as the pre-E-4 single-node path behaved.
        """
        try:
            outcome = await executor(name, manifest)
        except Exception as exc:  # noqa: BLE001 — a stage crash is a failed stage, not a runner crash
            entry["status"] = FAILED
            entry["ended"] = _utc_now_iso()
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self._persist(manifest)
            return

        entry["status"] = outcome.status
        entry["ended"] = _utc_now_iso()
        if outcome.error:
            entry["error"] = outcome.error
        if outcome.outputs:
            entry["outputs"] = list(outcome.outputs)
        self._persist(manifest)

    def _load_or_create(self, run_id: str, trigger: str) -> dict:
        """Load an existing manifest for ``run_id``, or create a fresh one."""
        path = self.cfg.content_root / self.profile / "runs" / f"{run_id}.json"
        if path.is_file():
            import json

            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("stages"):
                    return data
            except (json.JSONDecodeError, OSError):
                pass  # unreadable/corrupt → start fresh
        return new_manifest(run_id, trigger, self.profile, self.graph)
