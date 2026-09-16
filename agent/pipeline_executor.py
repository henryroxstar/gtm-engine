"""pipeline_executor — maps stage names to skill prompts and drives each via the SDK.

Each stage drives one ``ClaudeSDKClient`` turn with the appropriate skill prompt. The executor returns
a :class:`~agent.pipeline.StageOutcome` — including ``AWAITING_APPROVAL`` when the brain emits the
plan gate sentinel **or** the stage's node is pack-declared ``gate=true`` — so
:class:`~agent.pipeline.PipelineRunner` can stop cleanly and wait for an operator action from the
cockpit (or, for a pack run, the backend's ``POST /gate`` / the VPS CLI's gate-decision verb).

This is intentionally thin: prompts are strings, detection is string-search plus a declared-gate
lookup, and failure is any unhandled exception. The real gate logic (resume, cockpit routing) lives
in ``cockpit/bot.py``; this file just drives the LLM steps.
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
# PII on Claude by construction — see CLAUDE.md "Model discipline". Unknown stages
# default to the Claude brain.
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


def _emit_stage_cost(usage_sink, spec, profile: str, stage_name: str, usage: dict) -> None:
    """Route one SDK usage report to the injected cost sink as a CostRecord.

    Rates follow the spec the stage ACTUALLY ran on (a brain_radar stage bills its
    own model's rates, not brain_plan's). Best-effort: metering must never fail a
    stage. The sink owns runtime/workspace/run scoping (backend replaces those);
    ``stage`` carries the node id so per-node cost rollups are possible.
    """
    try:
        from gtm_core.metering import CostRecord, brain_cost_usd, resolve_rates

        r_in, r_out = resolve_rates(
            spec, env_input="BRAIN_INPUT_USD_PER_1K", env_output="BRAIN_OUTPUT_USD_PER_1K"
        )
        rec = CostRecord(
            runtime="vps",  # sink may replace() with its own runtime/scope
            source="brain",
            cost_usd=brain_cost_usd(usage, input_usd_per_1k=r_in, output_usd_per_1k=r_out),
            model_or_sku=spec.model,
            profile=profile,
            stage=stage_name,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        )
        usage_sink(rec)
    except Exception:  # noqa: BLE001 — metering must never break a stage
        pass  # nosec B110 — intentional best-effort swallow


async def execute_stage(
    cfg: Config,
    profile: str,
    stage_name: str,
    manifest: dict,
    prompts: dict[str, str] | None = None,
    stage_roles: dict[str, str] | None = None,
    usage_sink=None,
    allowed_skills: frozenset[str] | None = None,
    external_effects: dict[str, str | None] | None = None,
    gates: dict[str, bool] | None = None,
    run_id_stages: frozenset[str] | None = None,
    language: str | None = None,
) -> StageOutcome:
    """Run one pipeline stage by querying the brain with the stage's skill prompt.

    ``prompts``/``stage_roles`` default to the hardcoded news-pipeline
    ``STAGE_PROMPTS``/``_STAGE_ROLES`` — unchanged behavior for the existing cron path. A
    pack-driven executor (``agent.packs.make_executor_from_pack``) passes a pack graph's own
    per-node prompt + ``model_role`` here instead, generalizing the two hardcoded dicts into pack
    data without changing this function's behavior for callers that omit them.

    Returns ``StageOutcome(AWAITING_APPROVAL)`` if the plan gate sentinel appears in the output OR
    ``gates`` declares this stage ``gate=true`` (A11: a pack node's ``gate=true`` is a structural
    pause, not merely a hint the skill may or may not honour with a sentinel — see
    ``agent.packs.make_executor_from_pack``, which is the only caller that ever passes ``gates``).
    ``gates=None`` (every caller over ``DEFAULT_GRAPH``, i.e. the non-pack news/journey cron path)
    preserves the historical marker-only behavior exactly — that path carries no pack `gate`
    declarations to enforce. Returns ``StageOutcome(SKIPPED)`` for a node with a declared
    ``external_effect`` (the publish/email-enrollment dispatch stages), and ``StageOutcome(OK)`` on
    a clean, non-gated run. Any exception becomes ``StageOutcome(FAILED)``.
    """
    # A node with a declared external effect is a DISPATCH point, not brain work:
    # short-circuit it to SKIPPED so the brain never performs the effect itself —
    # the operator-approved dispatch happens in Python, outside this executor
    # (cockpit/gates.py on the VPS, backend/publish_dispatch.py on the backend).
    # Recognised by DECLARATION when the caller supplies pack metadata (A10);
    # callers that don't (the VPS news/journey path over DEFAULT_GRAPH) keep the
    # historical name check, so that trajectory stays byte-identical.
    # Short-circuit BEFORE importing the SDK so this path is genuinely
    # SDK-independent (it never selects a model and works even if
    # claude_agent_sdk is absent/broken); it also keeps ``--help`` fast.
    is_dispatch_node = (
        bool(external_effects.get(stage_name))
        if external_effects is not None
        else stage_name == "publish"
    )
    if is_dispatch_node:
        return StageOutcome(status=SKIPPED, outputs=("publish-manual",))

    from claude_agent_sdk import (  # local import; SDK is only needed for model stages
        AssistantMessage,
        TextBlock,
    )

    _prompts = prompts if prompts is not None else STAGE_PROMPTS
    prompt = _prompts.get(stage_name)
    if prompt is None:
        return StageOutcome(status=FAILED, error=f"unknown stage: {stage_name!r}")
    node_declared_gate = gates is not None and bool(gates.get(stage_name))
    if stage_name in (run_id_stages or ()) and manifest.get("run_id"):
        # An enrollment gate's draft is named after its run so the gate reads this run's draft
        # and never another's (client issue #245) — which the model can only do if it is told.
        prompt += (
            f"\n\nPack run id: {manifest['run_id']}. Wherever a skill names a file after "
            "<run-id>, use exactly this value."
        )

    _roles = stage_roles if stage_roles is not None else _STAGE_ROLES
    role = _roles.get(stage_name, "brain_plan")

    # Denials go to stderr (journal) AND the profile's denials.jsonl (P0-1) so a
    # blocked pipeline step is a queryable record with the stage context intact.
    from .denial_log import make_denial_sink

    _ledger_sink = make_denial_sink(cfg, profile, f"pipeline:{stage_name}")

    def _deny_and_ledger(tool_name: str, tool_input: dict, decision: str) -> None:
        _log_deny(tool_name, tool_input, decision)
        _ledger_sink(tool_name, tool_input, decision)

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
            can_use_tool=permissions.make_headless_can_use_tool(
                on_deny=_deny_and_ledger, allowed_skills=allowed_skills
            ),
            role=spec.role,
            allowed_skills=allowed_skills,
            language=language,
        )
        chunks: list[str] = []
        print(f"[stage:{stage_name}] running on {spec.provider}/{spec.model} …", flush=True)
        async for msg in stream_brain_messages(options, prompt):
            if isinstance(msg, AssistantMessage):
                if getattr(msg, "usage", None) and usage_sink is not None:
                    _emit_stage_cost(usage_sink, spec, profile, stage_name, msg.usage)
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

    if _GATE_PLAN_SENTINEL in full_output or node_declared_gate:
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
        # A11: a node declared gate=true pauses REGARDLESS of what the skill did or didn't emit —
        # the sentinel check above is a second, independent way to reach the same pause, kept for
        # the marker-only callers (gates=None). Neither condition alone is trusted over the other;
        # either is sufficient.
        return StageOutcome(status=AWAITING_APPROVAL, outputs=(stage_name,), text=full_output)

    return StageOutcome(status=OK, outputs=(stage_name,), text=full_output)


def make_executor(cfg: Config, profile: str, source: str = "news"):
    """Return a bound :data:`~agent.pipeline.StageExecutor` for ``(cfg, profile, source)``.

    source="news" uses STAGE_PROMPTS (existing news pipeline — unchanged).
    source="journey" uses JOURNEY_STAGE_PROMPTS.
    """
    prompts = JOURNEY_STAGE_PROMPTS if source == "journey" else STAGE_PROMPTS

    async def _executor(stage_name: str, manifest: dict) -> StageOutcome:
        return await execute_stage(cfg, profile, stage_name, manifest, prompts=prompts)

    return _executor
