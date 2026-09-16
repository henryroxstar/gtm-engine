"""Dev-only scripted run executor (``GTM_FAKE_RUNS``): the whole run lifecycle with no Agent
SDK behind it.

A client developer without a model key needs to drive ``POST /v1/runs`` → stream → gate →
artifact against a local stack. Everything around the executor stays real — the queue claim,
the §R2 pre-check, the run/node transitions, the durable ``run_gates`` row, push-on-gate,
event persistence + SSE, and artifact registration (through the pack path's own tree-diff
observer). Only the WORK is scripted.

Four properties hold by construction:

* **Unreachable outside development.** :func:`fake_runs_enabled` requires ``ENV=development``
  as well as the flag, and ``backend.main.check_fake_runs`` refuses to boot when the flag is
  set anywhere else — a misconfigured staging box fails loudly instead of serving scripted
  runs, and a path that skipped the boot guard still reaches the real executor, never this.
* **No spend, no egress.** Nothing here calls a model, a provider, or the network, and
  nothing writes ``cost_records``. The budget check still runs — and can still refuse — so a
  client sees the cap behave as it will in production.
* **No publish, ever.** This module never CALLS the publish or enrollment dispatch,
  whatever external effect a gated node declares — an approval writes one markdown artifact
  and completes. (It does import ``pack_executor``, which imports both, so "never imports"
  would be false.) Pinned by ``tests/backend/test_fake_runs.py``: an AST check that nothing
  here or in ``fake_script.py`` (what a run plays) imports either dispatch, the publisher,
  the stage executor, the SDK session, or an HTTP client, plus spies on both dispatches and
  every ``agent.publish`` publisher that fail a run reaching them.
* **A fake run stays fake.** The queue stamps ``"fake": true`` into the run's durable
  ``payload`` at dispatch. A stamped run met again with the flag off — a reclaim or a
  restart mid-gate — is FAILED (:data:`FAKE_RUN_OFF_ERROR`), never handed to a real
  executor: that would spend, and could apply an approval recorded against the fake draft to
  a real one.

A failed run is reachable on demand: ``GTM_FAKE_RUN_FAIL_NODE=<node_id>`` fails that node
when a run reaches it — before any gate there, as a real stage failure never pauses — and ends
the run ``failed`` through the pack path's own observer and ``_fail_run``. A run cancelled
before it gets there stays ``rejected``. A value naming no node the run executes (including
one an approval completes inline) is ignored with one WARNING: the knob is stack-wide, node
ids differ per variant, and failing every run that lacks the node would break every other
flow.

The flag is branched on at the two sites that build an executor coroutine:
``queue.dispatch_claimed`` (every claim, both modes, lease reclaims) and
``reconcile.reconcile_gates`` (restart mid-gate). A structural test pins that no third site
can skip it.

Differences from a real run (intentional; client docs say so too):

* **Prompt mode.** A real prompt run emits only ``status`` / ``awaiting_approval`` / ``done``
  and holds a NON-durable gate with no ``node_id``. A fake prompt run scripts three nodes
  (``research`` → ``draft`` → ``deliver``), so it also emits ``node``, ``content`` and a
  ``file`` block, and holds a DURABLE ``plan`` ``run_gates`` row at ``draft`` carrying that
  node_id — the frames a client needs to build its UI, from the only mode seedable locally.
* **Pack mode.** Node ids are the variant's real ones, and a gate is held wherever a real
  run pauses, with the kind a real run reports there, through the real runner's own rules
  (:mod:`.gate_kinds`): ``plan`` at a node running ``content-plan``, ``email_enroll`` where
  the approval dispatches enrollment, ``review`` at any other declared gate; a variant
  declaring no gate holds none. As on a real run, a gated node re-emits ``node running`` as
  it pauses, and approving an ``email_enroll`` gate completes the node it dispatches
  (``sequence-enroll``) inline — ``node completed`` and a markdown block, before the run
  resumes — except that the block says nobody was enrolled where a real one carries the
  dispatch outcome. The one exception: a node declaring its own ``publish`` effect holds a
  ``publish`` gate (``linkedin-post``: ``plan`` and ``publish``), where a real run currently
  never pauses (RL-01). No gate ever dispatches.
* **Gate content.** An ``email_enroll`` gate holds a one-line ``import_prospects_to_sequence``
  enroll-draft with fictional copy (``steps``) and fictional people (``prospect_list``), and an
  edit there is checked as a real run checks it (``agent.gate_actions.parse_enroll_draft``):
  bytes that are not an enroll-draft fail the run. A real run reads only the draft its own
  ``sequence`` node wrote for that run id, and fails at the pause when it is missing or
  malformed; the fake has no draft on disk and always holds its valid stub, so it never fails
  there. Every other gate holds the text approved so far (the fake draft, or its edit) under a
  first line naming its node. A real ``plan`` gate holds a JSON plan draft and fails the run
  on an edit that is not a JSON item array — the fake accepts any edit there; a real
  ``review`` gate holds a JSON stub; a real ``publish`` gate with no plan draft holds a JSON
  stub and refuses an approval without ``edited_content`` — the fake approves the text shown.
* **Output.** The last node — or the approval that completes it inline — writes one markdown
  artifact holding the approved text, and the run's output is that text. A real pack run's
  output is its stage manifest as JSON, and its artifacts are whatever its skills wrote.
* **Every gate's bytes are its own.** That naming line (an enroll-draft's ``note``) keeps two
  gates of one run from ever holding identical ``pending_content``, so a re-sent decision
  bound to an earlier gate's ``content_sha`` 409s at a later one instead of resolving it
  (H9). It frames the text, it is not part of it: an edit that kept it has it removed before
  the text is carried on.
* **Resume.** A fake run resumed by ``reconcile_gates`` replays from its first node rather
  than a durable manifest, so an already-applied earlier gate is asked again.
* **Unresolvable pack.** A pack request whose variant cannot be resolved gets the prompt
  script instead of failing: a dev nicety must never fail a run.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gtm_core.deploy_env import is_development

from .budget import _reserve_or_deny
from .decisions import run_status
from .fake_script import (
    FAIL_NODE_ERROR,
    FAKE_DRAFT,
    FAKE_ENROLL_OUTCOME,
    _fail_node,
    _gate_frame,
    _gate_pending,
    _step_text,
    script_nodes,
)
from .gate_kinds import _GATE_PLAN_SENTINEL
from .lifecycle import (
    CANCELLED,
    TIMED_OUT,
    approved,
    complete_run,
    hold_gate,
    reject_run,
    resume_run,
    rewrite_pending_content,
    start_run,
)
from .pack_executor import _node_observer
from .persistence import _CAP_REACHED, _fail_run
from .state import _cancelled_runs

FLAG_ENV = "GTM_FAKE_RUNS"
#: Total scripted delay across a run's nodes (seconds). 0 in tests.
DELAY_ENV = "GTM_FAKE_RUN_DELAY_S"
DEFAULT_DELAY_S = 1.0
_TRUTHY = frozenset({"1", "true"})

#: The additive ``runs.payload`` key the queue stamps on a fake run's first dispatch.
STAMP_KEY = "fake"
#: The terminal error for a stamped run met again with the flag off.
FAKE_RUN_OFF_ERROR = "fake run cannot resume: GTM_FAKE_RUNS is off"


def flag_truthy(raw: str | None) -> bool:
    """The flag's one parse, shared with the boot guard so the two can never disagree."""
    return (raw or "").strip().lower() in _TRUTHY


def fake_runs_enabled() -> bool:
    """True only with the flag set AND ``ENV=development`` (see the module docstring)."""
    return flag_truthy(os.getenv(FLAG_ENV)) and is_development()


def stamped_fake(payload: Any) -> bool:
    """True when a run's durable payload says it was dispatched as a fake run."""
    return isinstance(payload, dict) and payload.get(STAMP_KEY) is True


def _total_delay_s(explicit: float | None) -> float:
    if explicit is not None:
        return max(0.0, explicit)
    try:
        return max(0.0, float(os.getenv(DELAY_ENV, DEFAULT_DELAY_S)))
    except ValueError:
        return DEFAULT_DELAY_S


@dataclass(frozen=True)
class _Script:
    """What every scripted step needs: where to write, whom to tell, how long to take."""

    pool: Any
    workspace_id: str
    run_id: str
    on_node: Any
    step_s: float
    profile_root: Path
    last_node: str
    fail_node: str | None = None


async def _execute_fake_run(
    pool,
    repo_root,
    workspace_id: str,
    run_id: str,
    profile_name: str,
    *,
    pack: str | None = None,
    variant: str | None = None,
    delay_s: float | None = None,
) -> None:
    """Background task: the scripted twin of ``_execute_run`` / ``_execute_pack_run``.

    running → each node in order, holding a durable ``run_gates`` gate (push sent) at every
    gated node → the last node (or the approval completing it) writes one markdown artifact →
    ``ok`` with the approved text.
    A reject ends ``rejected``; an undecided gate fails with ``gate timeout`` exactly as a
    real run does, because ``hold_gate`` is the same code.
    """
    try:
        if not await _admit(pool, workspace_id, run_id):
            return
        from gtm_core.paths import _safe_segment, workspace_content_root

        _safe_segment(profile_name, "profile")  # B1 defense-in-depth, as the pack path does
        nodes, gates, successors = script_nodes(
            repo_root, workspace_id, profile_name, pack, variant
        )
        profile_root = workspace_content_root(workspace_id, repo_root) / profile_name
        script = _Script(
            pool=pool,
            workspace_id=workspace_id,
            run_id=run_id,
            on_node=_node_observer(pool, workspace_id, run_id, profile_name, profile_root),
            step_s=_total_delay_s(delay_s) / len(nodes),
            profile_root=profile_root,
            last_node=nodes[-1],
            fail_node=_fail_node(tuple(n for n in nodes if n not in successors.values())),
        )
        await _play(script, nodes, gates, successors)
    except Exception as exc:  # noqa: BLE001
        try:
            await _fail_run(pool, workspace_id, run_id, str(exc), error_code="internal_error")
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow


async def _admit(pool, workspace_id: str, run_id: str) -> bool:
    """The cancel guard, the §R2 pre-check, and ``running`` — the same three steps, in the
    same order, as both real executors.

    RL-13/ST-06: skips the reserve/start pair on a RESUME (a run already sitting at
    awaiting_approval — a boot reconcile or a lease reclaim), mirroring
    ``pack_executor._execute_pack_run``'s own admission check — see its comment for why."""
    if run_id in _cancelled_runs:
        _cancelled_runs.discard(run_id)
        return False
    if await run_status(pool, workspace_id, run_id) == "awaiting_approval":
        return True
    if not await _reserve_or_deny(pool, workspace_id, run_id):
        await _fail_run(pool, workspace_id, run_id, _CAP_REACHED, error_code="cost_cap_reached")
        return False
    await start_run(pool, workspace_id, run_id)
    return True


async def _play(
    script: _Script, nodes: tuple[str, ...], gates: dict[str, str], successors: dict[str, str]
) -> None:
    """Script every node in order. A gated node completes only once approved, then resumes
    the run (as the real pack runner does); the last node delivers the artifact."""
    text: str | None = FAKE_DRAFT
    inline = set(successors.values())
    for node_id in nodes:
        if node_id in inline:
            continue  # completed by the approval that dispatched it
        # RL-02: checked BETWEEN nodes, before starting the next one — cheap, since a
        # fake run is already node-granular. A cancel landing while THIS node is
        # in-flight (the sleep below) still lets it finish, mirroring the real pack
        # runner's own contract (an in-flight batch runs to completion); this stops the
        # NEXT node from ever starting.
        if script.run_id in _cancelled_runs:
            _cancelled_runs.discard(script.run_id)
            return
        await script.on_node(node_id, "running", {"status": "running"})
        await asyncio.sleep(script.step_s)
        if node_id == script.fail_node:
            await _fail_at(script, node_id)
            return
        if node_id not in gates:
            await _finish_node(script, node_id, _delivered(script, node_id, text), gated=False)
            continue
        text = await _pass_gate(script, node_id, gates[node_id], text, successors.get(node_id))
        if text is None:
            return
    if script.run_id in _cancelled_runs:
        _cancelled_runs.discard(script.run_id)
        return
    await complete_run(script.pool, script.workspace_id, script.run_id, text)


async def _pass_gate(
    script: _Script, node_id: str, kind: str, text: str, successor: str | None
) -> str | None:
    """Hold the gate at ``node_id``; once approved, complete it — and the successor its approval
    dispatches, inline, in ``_complete_approved_gate``'s order — then resume the run. Returns
    the approved text, or None when the run stopped at the gate."""
    approved_text = await _hold_fake_gate(script, node_id, kind, text)
    if approved_text is None:
        return None
    await _finish_node(script, node_id, _delivered(script, node_id, approved_text), gated=True)
    if successor is not None:
        delivered = _delivered(script, successor, approved_text)
        if delivered is not None:
            _write_artifact(script.profile_root, script.run_id, delivered)
        await script.on_node(successor, "ok", {"status": "ok", "text": FAKE_ENROLL_OUTCOME})
    await resume_run(script.pool, script.workspace_id, script.run_id)
    return approved_text


async def _hold_fake_gate(script: _Script, node_id: str, kind: str, text: str) -> str | None:
    """Open a durable ``kind`` gate on ``text`` (framed by :func:`_gate_content`; an
    ``email_enroll`` gate holds :func:`_enroll_stub` instead) and apply the decision. Returns
    the approved text, or None when the run stopped here (cancelled, timed out, rejected, or
    refused at the post-approval re-gate)."""
    pool, workspace_id, run_id = script.pool, script.workspace_id, script.run_id
    pending, carried = _gate_pending(node_id, kind, text)
    # The pause, as the real runner's observer reports it: the node reads ``running`` again.
    await script.on_node(node_id, "awaiting_approval", {"status": "awaiting_approval"})
    decision = await hold_gate(
        pool,
        workspace_id,
        run_id,
        sentinel=_GATE_PLAN_SENTINEL,
        gate=kind,
        pending_content=pending,
        node_id=node_id,
        durable=True,
    )
    if decision in (CANCELLED, TIMED_OUT):
        return None
    if not approved(decision):
        await reject_run(pool, workspace_id, run_id)
        return None
    # Re-gate before continuing (H6), as both real executors do.
    if not await _reserve_or_deny(pool, workspace_id, run_id):
        await _fail_run(pool, workspace_id, run_id, _CAP_REACHED, error_code="cost_cap_reached")
        return None
    edited = decision.get("edited_content")
    if edited is None:
        return carried
    return await _apply_edit(script, node_id, kind, edited)


async def _apply_edit(script: _Script, node_id: str, kind: str, edited: str) -> str | None:
    """Record approve-with-edits and return the text carried on — or, when an ``email_enroll``
    edit is no longer an enroll-draft, fail the run as a real run's draft promotion does."""
    error = _enroll_edit_error(edited, script.run_id) if kind == "email_enroll" else None
    if error is not None:
        await _fail_run(
            script.pool, script.workspace_id, script.run_id, error, error_code="draft_invalid"
        )
        return None
    await rewrite_pending_content(script.pool, script.workspace_id, script.run_id, edited)
    return edited.removeprefix(_gate_frame(node_id))


def _enroll_edit_error(edited: str, run_id: str) -> str | None:
    """The real enroll-draft parser's refusal of ``edited``, or None when it parses."""
    from agent.gate_actions import EnrollDraftError, parse_enroll_draft

    try:
        parse_enroll_draft(edited, source=f"fake-run-{run_id[:8]}.enroll-draft.json")
    except EnrollDraftError as exc:
        return str(exc)
    return None


async def _fail_at(script: _Script, node_id: str) -> None:
    """Fail ``node_id`` and the run as a real node failure does: the observer writes the failed
    ``run_nodes`` row and ``node`` frame, then ``_fail_run`` the terminal row and ``done``."""
    if script.run_id in _cancelled_runs:
        # Cancelled on the way here: the run is already ``rejected`` with its ``done`` sent.
        _cancelled_runs.discard(script.run_id)
        return
    error = FAIL_NODE_ERROR.format(node_id=node_id)
    await script.on_node(node_id, "failed", {"status": "failed", "error": error})
    await _fail_run(
        script.pool, script.workspace_id, script.run_id, error, error_code="node_failed"
    )


def _delivered(script: _Script, node_id: str, text: str) -> str | None:
    """``text`` when ``node_id`` is the run's last node (the one that delivers), else None."""
    return text if node_id == script.last_node else None


async def _finish_node(script: _Script, node_id: str, delivered: str | None, *, gated: bool):
    """Complete one node. The delivering node writes the artifact INTO the profile tree
    first, where the observer's tree diff registers it as a ``run_artifacts`` row + ``file``
    block — the path every real run's deliverable takes."""
    entry = {"status": "ok"}
    if delivered is not None:
        _write_artifact(script.profile_root, script.run_id, delivered)
        entry["text"] = delivered
    elif not gated:
        entry["text"] = _step_text(node_id)
    await script.on_node(node_id, "ok", entry)


def _write_artifact(profile_root, run_id: str, text: str) -> None:
    path = profile_root / "fake-runs" / f"fake-run-{run_id[:8]}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Fake run {run_id}\n\nScripted by GTM_FAKE_RUNS. The approved text, verbatim:\n\n"
        f"{text}\n",
        encoding="utf-8",
    )
