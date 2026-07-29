"""pipeline_executor — maps stage names to skill prompts and drives each via the SDK.

Each stage drives one ``ClaudeSDKClient`` turn with the appropriate skill prompt. The executor returns
a :class:`~agent.pipeline.StageOutcome` — including ``AWAITING_APPROVAL`` when the brain emits the
plan gate sentinel — so :class:`~agent.pipeline.PipelineRunner` can stop cleanly and wait for an
operator action from the cockpit.

This is intentionally thin: prompts are strings, detection is string-search, and failure is any
unhandled exception. The real gate logic (resume, cockpit routing) lives in ``cockpit/bot.py``;
this file just drives the LLM steps.
"""

from __future__ import annotations

import sys

from gtm_core.models import breaker_for, call_with_fallback, resolve_model

from . import permissions
from .config import Config
from .pipeline import AWAITING_APPROVAL, FAILED, OK, SKIPPED, StageOutcome
from .session import build_agent_options, stream_brain_messages

# The gate sentinel the content-plan skill emits when it wants human approval.
_GATE_PLAN_SENTINEL = "⟦GATE:plan⟧"

# Stage → model role (P4 hybrid brain). Mechanical, no-PII upstream stages run on the
# cheap worker brain (brain_radar → DeepSeek); judgment + gate-critical + PII-bearing
# stages stay on Claude (brain_plan). This split keeps the publish gate and customer
# PII on Claude by construction — see CLAUDE.md "Model discipline" and
# docs/SECURITY-SELF-ASSESSMENT.md D-01. Unknown stages default to the Claude brain.
_STAGE_ROLES: dict[str, str] = {
    "radar": "brain_radar",
    "research": "brain_radar",
    "plan": "brain_plan",
    "studio": "brain_plan",
    "publish": "brain_plan",
}

# Per-stage prompts. Kept as module-level constants so bot.py can reference them if needed.
STAGE_PROMPTS: dict[str, str] = {
    "radar": "Run the content-radar skill for the active profile.",
    "plan": (
        "Run the content-plan skill. Propose this week's content plan for the active profile "
        "and present it for approval."
    ),
    "research": (
        "Run the content-research skill for all planned items in the approved plan for the "
        "active profile."
    ),
    "studio": (
        "Run the content-studio skill for all researched items in the approved plan for the "
        "active profile. For each item, produce and lint the asset for its platform (LinkedIn, X, "
        "or Instagram) and locale. Copy/brief only — do NOT generate any paid visuals in this "
        "automated stage. Report any item that fails to lint rather than skipping it."
    ),
    "publish": "Phase 1 publish is manual — log this stage as skipped.",
}

# Journey-pipeline stage prompts — substituted when source="journey"
JOURNEY_STAGE_PROMPTS: dict[str, str] = {
    "radar": (
        "Run the builder-radar skill for the active profile. "
        "Read the repo's git history and docs to surface build moments as story clusters. "
        "Use the watermark from content/<active>/journey/state.json for incremental runs."
    ),
    "plan": (
        "Run the content-plan skill. The radar clusters are in "
        "content/<active>/journey/radar/ (not the usual radar/ path). "
        "Propose this week's journey content plan for the active profile "
        "and present it for Gate 1 approval."
    ),
    "research": (
        "Run the builder-evidence skill for all planned journey items in the approved plan. "
        "Gather primary-source git and PRD evidence for each item."
    ),
    "studio": (
        "Run the builder-studio skill for all researched journey items in the approved plan. "
        "For each item, produce the LinkedIn post, article, and podcast script. "
        "Run the content linter and safe-to-share lint before surfacing for review."
    ),
    "publish": (
        "Run the content-publish skill for any journey items ready for Gate 2. "
        "The LinkedIn asset.json is at content/<active>/journey/assets/<item-id>.asset.json."
    ),
}


def _log_deny(tool_name: str, tool_input: dict, decision: str) -> None:  # noqa: ARG001
    print(f"[pipeline_executor] permission policy {decision}: tool={tool_name}", file=sys.stderr)


async def execute_stage(
    cfg: Config,
    profile: str,
    stage_name: str,
    manifest: dict,  # noqa: ARG001 — available for future use (e.g. passing run_id in prompt)
    prompts: dict[str, str] | None = None,
    stage_roles: dict[str, str] | None = None,
) -> StageOutcome:
    """Run one pipeline stage by querying the brain with the stage's skill prompt.

    ``prompts``/``stage_roles`` default to the hardcoded news-pipeline
    ``STAGE_PROMPTS``/``_STAGE_ROLES`` — unchanged behavior for the existing cron path.
    A pack-driven executor (``agent.packs.make_executor_from_pack``) passes a pack
    graph's own per-node prompt + ``model_role`` here instead, generalizing the two
    hardcoded dicts into pack data (docs/prds/2026-07-06-engine-pack-tenant-three-layer.md §6)
    without changing this function's behavior for callers that omit them.

    Returns ``StageOutcome(AWAITING_APPROVAL)`` if the plan gate sentinel appears in the output,
    ``StageOutcome(SKIPPED)`` for the publish stage (manual in Phase 1), and ``StageOutcome(OK)``
    on a clean run. Any exception becomes ``StageOutcome(FAILED)``.
    """
    # Publish is manual in Phase 1 — short-circuit BEFORE importing the SDK so the
    # publish path is genuinely SDK-independent (it never selects a model and works
    # even if claude_agent_sdk is absent/broken). Keeping the import below this guard
    # also keeps ``--help`` fast.
    if stage_name == "publish":
        return StageOutcome(status=SKIPPED, outputs=("publish-manual",))

    from claude_agent_sdk import (  # local import; SDK is only needed for model stages
        AssistantMessage,
        TextBlock,
    )

    _prompts = prompts if prompts is not None else STAGE_PROMPTS
    prompt = _prompts.get(stage_name)
    if prompt is None:
        return StageOutcome(status=FAILED, error=f"unknown stage: {stage_name!r}")

    _roles = stage_roles if stage_roles is not None else _STAGE_ROLES
    role = _roles.get(stage_name, "brain_plan")

    async def _attempt(spec) -> str:
        """Run the stage once on ``spec``'s model; return the accumulated brain text.

        Used by :func:`gtm_core.models.call_with_fallback`, which retries on the next
        spec in the chain when this one raises a retryable error (deprecation 404,
        429, 5xx, timeout). ``spec.role`` may be the primary (brain_radar/brain_plan)
        or a fallback (brain_cheap) — build_agent_options resolves whichever, so a
        DeepSeek failure transparently falls through to Claude Haiku.
        """
        options = build_agent_options(
            cfg,
            profile,
            can_use_tool=permissions.make_headless_can_use_tool(on_deny=_log_deny),
            role=spec.role,
        )
        chunks: list[str] = []
        print(f"[stage:{stage_name}] running on {spec.provider}/{spec.model} …", flush=True)
        async for msg in stream_brain_messages(options, prompt):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
                        chunks.append(block.text)
        return "".join(chunks)

    try:
        full_output = await call_with_fallback(
            resolve_model(role), _attempt, breaker=breaker_for(role)
        )
    except Exception as exc:  # noqa: BLE001 — chain exhausted (or a non-retryable error) → failed stage
        return StageOutcome(status=FAILED, error=f"{type(exc).__name__}: {exc}")

    if _GATE_PLAN_SENTINEL in full_output:
        # Defence-in-depth: the plan brain occasionally emits the gate marker but skips the durable
        # draft write — hallucinating a "lock"/permission block when there is none (the dir is
        # writable, Write is allowed). A Gate-1 pause with no persisted draft is a silent dead end
        # (the cockpit approve path has nothing to promote), so treat it as a stage
        # failure the runner surfaces, not a clean gate. The content-plan skill Step 2 steers the
        # brain to the Write tool; this is the backstop. Applies to both the news and journey
        # plan stages (both reuse content-plan → content/<profile>/plans/.pending/).
        if stage_name == "plan":
            pending_dir = cfg.content_root / profile / "plans" / ".pending"
            if not any(pending_dir.glob("*.draft.json")):
                return StageOutcome(
                    status=FAILED,
                    error=(
                        "plan stage emitted Gate 1 but persisted no .pending/*.draft.json "
                        "(draft was not saved to disk)"
                    ),
                )
        return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,))

    return StageOutcome(status=OK, outputs=(stage_name,))


def make_executor(cfg: Config, profile: str, source: str = "news"):
    """Return a bound :data:`~agent.pipeline.StageExecutor` for ``(cfg, profile, source)``.

    source="news" uses STAGE_PROMPTS (existing news pipeline — unchanged).
    source="journey" uses JOURNEY_STAGE_PROMPTS.
    """
    prompts = JOURNEY_STAGE_PROMPTS if source == "journey" else STAGE_PROMPTS

    async def _executor(stage_name: str, manifest: dict) -> StageOutcome:
        return await execute_stage(cfg, profile, stage_name, manifest, prompts=prompts)

    return _executor
