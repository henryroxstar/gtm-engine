"""The brain: Claude Code via the claude-agent-sdk, with per-chat profile binding.

# deploy-trigger: Model routing P1+P2 — 2026-06-21 r7

Two classes:

  - :class:`AgentSession` — one persistent multi-turn SDK session for a single
    bound profile. Wraps :class:`claude_agent_sdk.ClaudeSDKClient`.
  - :class:`SessionStore` — maps each Telegram ``chat_id`` to its own
    :class:`AgentSession`, resolving the active profile per chat. There is NO
    global mutable ACTIVE_PROFILE — profile is per ``chat_id`` here.

Model discipline: ``build_agent_options`` resolves the ``brain_plan`` role via
:func:`gtm_core.models.resolve_model` — the registry is the single source of truth
for provider, model id, and capability flags. ``HERMES_MODEL`` env var overrides
the model id inside the resolver (break-glass; no redeploy). Request params
``effort`` and ``thinking`` are only included when the resolved spec declares
``supports_effort`` / ``supports_adaptive_thinking`` — this prevents the 400 a
Haiku/DeepSeek swap would otherwise trigger (P4).

The SDK is imported lazily inside the methods that need it so this module can be
imported (for config/profile/ledger work, and for unit tests of the store
plumbing) without ``claude-agent-sdk`` installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from gtm_core.context_metrics import TurnTally, read_context_summary
from gtm_core.ledgers import Ledgers
from gtm_core.metering import brain_cost_usd, resolve_rates
from gtm_core.models import resolve_model

from . import mcp_config, permissions, profiles
from .subagent_limit import SubagentCounter, resolve_max

if TYPE_CHECKING:  # type hints only
    from .config import Config

logger = logging.getLogger(__name__)

#: Upper bound on the one-off ``get_context_usage`` read (W4 measurement). A measurement must never
#: hold a run open: past this the figures are recorded as unavailable and the run ends normally.
_CONTEXT_SNAPSHOT_TIMEOUT_S = 5.0


# Brain cost rates (USD per 1K tokens) resolve through the shared metering contract:
# env override (BRAIN_INPUT_USD_PER_1K / BRAIN_OUTPUT_USD_PER_1K) → the registry rate
# pinned for the resolved brain model → list-pricing default. Sourcing the rate from the
# *resolved* spec means a brain_plan→brain_cheap (Sonnet→Haiku) fallback bills Haiku
# rates, not Sonnet's. Cache multipliers (1.25x write / 0.10x read) live in
# gtm_core.metering.


#: How long a stage waits for the MCP servers it cannot run without to finish starting. The CLI gives
#: each server its own startup timeout; this only has to outlast that on a loaded host.
MCP_READY_TIMEOUT_S = 90.0
#: The CLI's own per-server startup timeout (``MCP_TIMEOUT``, ms) for a tenant run; its default is 30 s.
TENANT_MCP_STARTUP_TIMEOUT_MS = 75_000


class RequiredMcpServersUnavailable(RuntimeError):
    """A server the stage cannot run without did not connect. Raised before the first turn, so nothing
    was spent. ``servers`` maps each such server to the status the CLI last reported for it.

    The class name carries none of the tokens ``gtm_core.models.is_retryable_error`` treats as
    transient, so the model fallback chain re-raises it at once instead of retrying on another model."""

    def __init__(self, servers: dict[str, str]):
        self.servers = servers
        detail = ", ".join(f"{name} ({status})" for name, status in sorted(servers.items()))
        super().__init__(f"required MCP server(s) did not start: {detail}")


async def _await_mcp_servers(client, required, timeout_s: float = MCP_READY_TIMEOUT_S) -> None:
    """Hold the first turn until every ``required`` server has left ``pending``; raise unless connected.

    The CLI does not wait for its MCP servers before the first model turn: on a CPU-starved host the turn
    starts with them still ``pending`` and their tools missing, and the model reports the tool absent
    (client issue #337, run 82e84540). Reproduced in the backend image at 0.25 CPU: the turn began at
    ~33 s with every server pending; waiting here put all four RocketReach tools in that first turn."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        status = await client.get_mcp_status()
        seen = {s.get("name"): s.get("status") for s in (status or {}).get("mcpServers", [])}
        states = {name: seen.get(name, "absent") for name in required}
        if not any(state == "pending" for state in states.values()) or loop.time() >= deadline:
            break
        await asyncio.sleep(0.5)
    if missing := {name: state for name, state in states.items() if state != "connected"}:
        raise RequiredMcpServersUnavailable(missing)


async def stream_brain_messages(
    options, prompt: str, *, require_mcp: tuple[str, ...] = ()
) -> AsyncGenerator[object, None]:
    """Drive one one-shot brain turn over a persistent ``ClaudeSDKClient`` and yield each message.

    Uses persistent client to keep SDK<->CLI control stream open for permission round-trips.
    Cleans up any ephemeral tenant launch plan on exit. ``require_mcp`` names servers the turn cannot
    run without: the prompt is not sent until they connect, and
    :class:`RequiredMcpServersUnavailable` is raised (before any spend) if one does not.
    """
    from claude_agent_sdk import ClaudeSDKClient

    try:
        async with ClaudeSDKClient(options=options) as client:
            if require_mcp:
                await _await_mcp_servers(client, require_mcp)
            await client.query(prompt)
            async for msg in client.receive_response():
                yield msg
    finally:
        if plan := getattr(options, "_tenant_plan", None):
            plan.discard()


#: A variable name that holds a secret. A tenant run's CLI is started from the backend's own environment,
#: which holds every house provider key and the platform's signing, vault and database secrets.
_SECRET_NAME = re.compile(
    r"KEY|KEK|SECRET|TOKEN|DSN|PASSWORD|PASSWD|CREDENTIAL|WEBHOOK", re.IGNORECASE
)
#: A URL that carries a password (``scheme://user:password@host``) is a secret whatever the variable is called:
#: the owner-role database URL lives under a name like ``POSTGRES_MIGRATION_URL``.
_URL_WITH_PASSWORD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
#: Secret-bearing names that do not look like one (a connection URL carries its password).
_ALSO_SECRET = frozenset({"DATABASE_URL", "REDIS_URL", "RATELIMIT_STORAGE_URI"})
#: What the CLI itself needs to talk to the model; never blanked.
_CLI_OWN_PREFIXES = ("ANTHROPIC_", "CLAUDE_")


def _tenant_env_scrub(environ) -> dict[str, str]:
    """``{name: ""}`` for every secret-shaped variable in ``environ`` a tenant run must not inherit.

    The SDK merges the run's ``env`` OVER ``os.environ`` for the CLI it starts, so an empty value is how a
    variable is removed from that process. By shape rather than by list — the name, or a password inside the
    value — so a secret added to the backend's environment tomorrow is blanked without anyone remembering to
    name it. The model credential stays (the CLI cannot run without it), which is the part the OS-level
    second layer closes.
    """
    return {
        name: ""
        for name, value in environ.items()
        if (
            _SECRET_NAME.search(name)
            or name in _ALSO_SECRET
            or (isinstance(value, str) and _URL_WITH_PASSWORD.search(value))
        )
        and not name.startswith(_CLI_OWN_PREFIXES)
    }


def _tenant_env(environ, cfg: Config) -> dict[str, str]:
    """What a tenant run's CLI environment adds to, or blanks from, the backend's own."""
    # A tenant run's CLI must not inherit the platform's secrets (see _tenant_env_scrub).
    env = _tenant_env_scrub(environ)
    # Name the workspace to what the run's tools start: gtm_core.preflight reads it to know it is in
    # workspace mode (client issue #337). Nothing on the backend path set it, so that mode never
    # turned on. The id is the tree's own name (data/workspaces/<id>/content), not a caller input.
    env["GTM_WORKSPACE_ID"] = Path(cfg.content_root).parent.name
    # The CLI gives each MCP server 30 s to start, then marks it failed and runs the turn without its
    # tools — on the shared tenant worker that is how RocketReach went missing (#337). Give a loaded
    # host longer; MCP_READY_TIMEOUT_S outlasts this, so a server that still fails is reported, not lost.
    if "MCP_TIMEOUT" not in environ:
        env["MCP_TIMEOUT"] = str(TENANT_MCP_STARTUP_TIMEOUT_MS)
    return env


def _keep_secrets_off_the_command_line(options_kw: dict, launch) -> None:
    """The SDK puts the MCP configuration (every provider key the run holds) and the system prompt on the CLI's
    command line, which every user in the container can read. Through the launcher they travel as private files."""
    servers = options_kw.get("mcp_servers")
    if servers:
        try:
            options_kw["mcp_servers"] = launch.private_file(
                "mcp.json", json.dumps({"mcpServers": servers})
            )
        except TypeError as exc:  # an in-process server object cannot be written to a file
            raise ValueError("a tenant run's MCP servers must all be plain configuration") from exc
    prompt = options_kw.get("system_prompt")
    if isinstance(prompt, dict) and "append" in prompt:
        text = prompt["append"]
        options_kw["system_prompt"] = {k: v for k, v in prompt.items() if k != "append"}
        extra = dict(options_kw.get("extra_args") or {})
        extra["append-system-prompt-file"] = launch.private_file("system-prompt.txt", text)
        options_kw["extra_args"] = extra


def _resume_kw(cfg: Config, resume: str | None) -> dict:
    """Cockpit conversation resume (F1): the CLI re-reads its own transcript for this session id.

    Never on the backend path — ``persist_sessions=False`` writes no transcript, so there is nothing to
    resume (and resuming a stale id would only fail the run).
    """
    return {"resume": resume} if resume and getattr(cfg, "persist_sessions", True) else {}


def build_agent_options(  # noqa: C901
    cfg: Config,
    profile: str,
    *,
    can_use_tool=None,
    role: str = "brain_plan",
    allowed_skills: frozenset[str] | None = None,
    language: str | None = None,
    capture_gate=None,
    run_id: str | None = None,
    resume: str | None = None,
    subagent_counter=None,
):
    """Construct the ``ClaudeAgentOptions`` shared by sessions and one-shot runs.

    Centralised so the persistent :class:`AgentSession` and the headless
    ``python -m agent`` path build IDENTICAL options — same plugin, same MCP
    servers, same system prompt, same permission posture.

    Permission posture (least privilege; see :mod:`agent.permissions`). Phase 0
    used ``bypassPermissions`` (every tool auto-approved, including
    ``Bash``/``Write`` on the VPS) because
    the SDK runs HEADLESS — a ``default``-mode prompt with no TTY would hang. We
    now keep ``default`` mode but supply a ``can_use_tool`` callback that returns
    a decision immediately (so nothing hangs) while enforcing least privilege:

      - ``can_use_tool`` — the run path's policy callback. Defaults to the
        fail-closed *headless* callback (allow known-safe, otherwise deny), so
        even a one-shot built without an explicit callback is not wide open.
      - ``disallowed_tools`` — a declarative deny floor for dangerous
        shell/process-exec vectors (ASI05), enforced even before the callback.

    ``setting_sources=["project"]`` loads the repo's ``CLAUDE.md`` (architecture
    invariants, injected into the conversation) and ``.claude/settings.json``
    (the declarative deny/allow rules) in addition to the plugin's settings.

    ``role`` selects the model via :func:`gtm_core.models.resolve_model` — the
    registry is the single source of truth. ``effort`` and ``thinking`` are only
    included when the resolved spec declares support, preventing 400s on Haiku or
    DeepSeek (P4 stage-routing will pass ``brain_radar`` here).
    """
    # Imported here (not at module top) so importing agent.session does not
    # require claude-agent-sdk to be installed.
    from claude_agent_sdk import ClaudeAgentOptions

    # Fail closed: a caller that forgot to pass a policy still gets least privilege,
    # never the old blanket bypass. When a pack-reachability scope is supplied, the
    # default callback carries it too. Denials land in the profile's denials.jsonl
    # (P0-1) so a blocked call on any default-built path is a queryable record.
    if capture_gate is not None and can_use_tool is not None:
        # The gate lives in the default callback. A caller-supplied one would drop it silently,
        # and a capture run with no allowlist is exactly what the gate exists to prevent.
        raise ValueError("capture_gate cannot be combined with a caller-supplied can_use_tool")
    if can_use_tool is None:
        if not getattr(
            cfg, "persist_sessions", True
        ):  # a tenant config: the default has no confinement
            raise ValueError(
                "a tenant run needs an explicit can_use_tool carrying its workspace confine"
            )
        from .denial_log import make_denial_sink

        can_use_tool = permissions.make_headless_can_use_tool(
            on_deny=make_denial_sink(cfg, profile, "headless-default"),
            allowed_skills=allowed_skills,
            capture_gate=capture_gate,
        )

    spec = resolve_model(role)

    # Build core options, then conditionally add capability-gated params.
    options_kw: dict = {
        "cwd": str(cfg.repo_root),
        # claude_code preset with exclude_dynamic_sections=True makes the ~50K-token
        # plugin payload cache-eligible across sessions (cross-session cache hits).
        # Dynamic sections (cwd, git state, memory) reinjected into first user message.
        "system_prompt": {
            "type": "preset",
            "preset": "claude_code",
            "append": profiles.system_prompt_for(profile, cfg),
            "exclude_dynamic_sections": True,
        },
        # Load the de-branded plugin from the git checkout (local plugin).
        "plugins": [{"type": "local", "path": str(cfg.plugin_path)}],
        # Infra MCP servers, each gated on its env (news / google / worker).
        "mcp_servers": mcp_config.build_mcp_servers(cfg, profile, run_id=run_id),
        # Honour the project's settings: CLAUDE.md + .claude/settings.json + plugin settings.
        "setting_sources": ["project"],
        # Least privilege: deny the dangerous-shell floor, route the rest through the policy.
        "permission_mode": "default",
        "disallowed_tools": list(permissions.DANGEROUS_TOOL_DENY_RULES),
        "can_use_tool": can_use_tool,
        # Model resolved from registry; HERMES_MODEL break-glass handled inside resolve_model.
        "model": spec.model,
        # Hard SDK-level budget stop — enforces the PROFILE per_run_cap_usd so the
        # cap is honoured even if the model would otherwise continue.
        "max_budget_usd": cfg.per_run_cap_usd,
    }
    # Gate capability-dependent params on the registry flags — prevents 400s when
    # the role resolves to a model that does not support these fields (e.g. Haiku,
    # DeepSeek). brain_plan (Sonnet) includes both; cheap/radar roles omit them.
    if spec.supports_adaptive_thinking:
        options_kw["thinking"] = {"type": "adaptive"}
    if spec.supports_effort:
        options_kw["effort"] = "medium"

    # Pack reachability, primary lever (A1): unlisted skills are hidden from the
    # model's skill listing and rejected by the Skill tool at the SDK layer. The
    # can_use_tool branch above is the defence-in-depth twin — the SDK option is a
    # context filter, not a sandbox (skill FILES remain readable), which is why both
    # levers exist. None ⇒ no scoping (VPS/cockpit paths unchanged).
    if allowed_skills is not None:
        options_kw["skills"] = sorted(allowed_skills)

    # Cross-provider brain routing (P4). When a role resolves to a non-Anthropic
    # provider (e.g. brain_radar → DeepSeek, which speaks the Anthropic Messages
    # format natively), point THIS subprocess's SDK at that provider's base_url +
    # key via options.env. The SDK merges this over os.environ for the spawned CLI
    # ONLY (subprocess_cli.py) — the parent process and the separately-spawned MCP
    # workers keep the real ANTHROPIC_* env untouched. Boundary: only the
    # mechanical, no-PII stages (radar/research) ever pass a DeepSeek role here;
    # plan/studio/publish stay on Claude by construction (see CLAUDE.md "Model
    # discipline").
    # Scope the spawned CLI's skills to THIS run's content + profiles roots. The SDK
    # merges options.env over os.environ for the CLI subprocess ONLY (not the parent
    # process or the separately-spawned MCP workers), so a workspace-scoped Config
    # (the backend, P3) confines skill file I/O to that workspace's tree with no
    # shared-os.environ race between concurrent runs. On the VPS these equal the
    # ambient values, so behaviour is preserved.
    env_overrides: dict[str, str] = {
        "GTM_CONTENT_ROOT": str(cfg.content_root),
        "GTM_PROFILES_ROOT": str(cfg.profiles_root),
        "GTM_RUNTIME": "headless",
        # The Firecrawl capture hook files a page under THIS run's profile (R0.1); with no
        # desktop marker on a headless host it would otherwise capture nothing.
        "GTM_ACTIVE_PROFILE": profile,
    }
    # A7 per-request output language: skills read GTM_RUN_LANGUAGE. Pinned in the
    # subprocess env alongside the scoping vars (never the shared os.environ), so
    # concurrent runs in different languages cannot race. Absent ⇒ unset ⇒ the
    # profile's own language setting governs, exactly as before.
    if language:
        env_overrides["GTM_RUN_LANGUAGE"] = language
    if spec.provider != "anthropic":
        key = spec.api_key() or ""
        env_overrides.update(
            {
                "ANTHROPIC_BASE_URL": spec.base_url,
                "ANTHROPIC_API_KEY": key,
                "ANTHROPIC_AUTH_TOKEN": key,
            }
        )
    if not getattr(cfg, "persist_sessions", True):
        env_overrides.update(_tenant_env(os.environ, cfg))
    options_kw["env"] = env_overrides
    options_kw.update(_resume_kw(cfg, resume))
    launch = None
    if not getattr(cfg, "persist_sessions", True):
        options_kw["extra_args"] = {"no-session-persistence": None}
        options_kw["strict_mcp_config"] = True
        tree = Path(cfg.content_root).parent
        if os.environ.get("GTM_RUN_ISOLATION") == "container":
            from .container_runner import prepare_container_launch

            launch = prepare_container_launch(
                tree.name,
                tree.parent,
                run_dir=tree / ".run",
                env=env_overrides,
                real_cli=options_kw.get("cli_path"),
            )
            if launch is not None:
                options_kw["cli_path"] = str(launch.cli_path)
                _keep_secrets_off_the_command_line(options_kw, launch)
        else:
            from .tenant_identity import ensure_provisioned, plan_for

            ensure_provisioned(tree.name, tree.parent)
            launch = plan_for(tree.name, tree.parent, run_dir=tree / ".run")
            if launch is not None:
                options_kw["cli_path"] = launch.cli_path
                env_overrides.update(launch.env)
                _keep_secrets_off_the_command_line(options_kw, launch)

    # W5: every run carries a counted limit on helper launches. A hook, not the permission callback: the
    # launch never reaches ``can_use_tool`` (see agent/subagent_limit.py). On by default so a new caller
    # cannot forget it; a caller that owns a window (a pack node, a warm session) passes its counter.
    from claude_agent_sdk import HookMatcher

    from .denial_log import make_denial_sink
    from .subagent_limit import HOOK_MATCHER, denial_sink, make_subagent_hook

    counter = subagent_counter or SubagentCounter(resolve_max(cfg, profile))
    hooks_kw: dict = {
        "PreToolUse": [
            HookMatcher(
                matcher=HOOK_MATCHER,
                hooks=[
                    make_subagent_hook(
                        counter,
                        on_deny=denial_sink(make_denial_sink(cfg, profile, "subagent-limit")),
                    )
                ],
            )
        ]
    }
    if capture_gate is not None:
        from .capture_hook import SCRAPE_MATCHER, make_capture_hook

        hooks_kw["PostToolUse"] = [
            HookMatcher(matcher=SCRAPE_MATCHER, hooks=[make_capture_hook(cfg, profile)])
        ]
    options_kw["hooks"] = hooks_kw

    try:
        opts = ClaudeAgentOptions(**options_kw)
        opts._subagent_counter = counter
        if launch is not None:
            opts._tenant_plan = launch
        return opts
    except BaseException:
        if launch is not None:
            launch.discard()
        raise


class AgentSession:
    """A persistent, multi-turn Claude Code session bound to one profile.

    Lifecycle: ``__init__`` (cheap, no I/O) → ``await connect()`` →
    ``async for text in run(prompt)`` (repeatable) → ``await close()``.
    """

    def __init__(
        self,
        cfg: Config,
        profile: str,
        can_use_tool=None,
        usage_sink=None,
        *,
        allowed_skills: frozenset[str] | None = None,
        resume: str | None = None,
        on_session_id=None,
        measure_context: bool = False,
    ) -> None:
        self._cfg = cfg
        self._profile = profile
        self._can_use_tool = can_use_tool
        # W4 measurement (backend only): tally per-turn usage for every run, and read the CLI's own
        # context breakdown once per session. Off for the cockpit, whose path stays byte-identical.
        self._measure_context = measure_context
        self.run_tally = TurnTally()
        self.context_summary: dict | None = None  # set after the first run; reused by warm runs
        self._context_tried = False
        # W5: this session's helper window. The hook built in connect() holds this same object; run() opens
        # a fresh window each time it starts (one backend run / one cockpit turn).
        self._subagents = SubagentCounter(resolve_max(cfg, profile))
        self._usage_sink = usage_sink
        self._allowed_skills = allowed_skills
        self._client = None
        self._connected = False
        self._tenant_plan = None
        # F1: the SDK session id to resume on connect, and a callback told the id after each turn.
        self._resume = resume
        self._on_session_id = on_session_id
        self.session_id: str | None = None
        # F3 /stop: is a turn running (including while the CLI is still connecting), and did the operator
        # ask to stop it before it started?
        self._running = False
        self._stop_requested = False

    @property
    def resumed_from(self) -> str | None:
        """The session id this session was asked to resume (None for a fresh conversation)."""
        return self._resume

    @property
    def profile(self) -> str:
        """The profile this session is bound to."""
        return self._profile

    async def connect(self) -> None:
        """Start the underlying SDK client (idempotent)."""
        if self._connected:
            return
        from claude_agent_sdk import ClaudeSDKClient

        options = build_agent_options(
            self._cfg,
            self._profile,
            can_use_tool=self._can_use_tool,
            allowed_skills=self._allowed_skills,
            resume=self._resume,
            subagent_counter=self._subagents,
        )
        self._tenant_plan = getattr(options, "_tenant_plan", None)
        try:
            self._client = ClaudeSDKClient(options=options)
            await self._client.connect()
            self._connected = True
        except BaseException:
            if self._tenant_plan:
                self._tenant_plan.discard()
                self._tenant_plan = None
            raise

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """Send ``prompt`` and yield assistant text blocks as they stream in.

        Yields only the text of ``TextBlock``s inside ``AssistantMessage``s —
        tool-call blocks, system messages, and the final result message are
        consumed but not yielded (the cockpit only wants the prose).

        Auto-connects if the session has not been connected yet.
        """
        self._running = True
        self._stop_requested = False
        try:
            if not self._connected:
                await self.connect()
            if (
                self._stop_requested
            ):  # /stop arrived while the CLI was still starting: never send the prompt
                self._stop_requested = False
                return

            # Imported lazily so this module imports without the SDK present.
            from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

            assert self._client is not None  # connect() guarantees this
            self.run_tally = TurnTally()
            self._subagents.reset(limit=resolve_max(self._cfg, self._profile))
            await self._client.query(prompt)
            async for msg in self._client.receive_response():
                if isinstance(msg, ResultMessage):
                    self._note_session_id(getattr(msg, "session_id", None))
                if isinstance(msg, AssistantMessage):
                    if msg.usage:
                        self._log_usage(msg.usage)
                        self.run_tally.note(
                            msg.usage, parent_tool_use_id=getattr(msg, "parent_tool_use_id", None)
                        )
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            yield block.text
            if note := self._subagents.note:
                yield f"\n\n{note}"
            if self._measure_context and not self._context_tried:
                # Once per session, after the first run (its MCP servers are connected by then). Bounded
                # and non-raising; a failure is recorded as unavailable and not retried.
                self._context_tried = True
                self.context_summary = await read_context_summary(
                    self._client, _CONTEXT_SNAPSHOT_TIMEOUT_S
                )
        finally:
            self._running = False

    def _note_session_id(self, session_id: str | None) -> None:
        """Remember the SDK session id and tell the owner (best-effort: never breaks the stream)."""
        if not session_id:
            return
        self.session_id = session_id
        if self._on_session_id is not None:
            try:
                self._on_session_id(session_id)
            except Exception:  # noqa: BLE001 — persistence of the id must never break a turn
                logger.debug("on_session_id callback failed", exc_info=True)

    async def interrupt(self) -> bool:
        """Interrupt the in-flight turn (F3 ``/stop``). False when nothing is running.

        A turn counts as running from the moment ``run`` is entered, including the seconds a cold session
        spends connecting — then there is nothing to interrupt yet, so the request is remembered and the
        prompt is never sent. An idle but connected session reports ``False``: nothing was stopped.
        """
        if not self._running:
            return False
        if self._client is None or not self._connected:
            self._stop_requested = True
            return True
        await self._client.interrupt()
        return True

    def _log_usage(self, usage: dict) -> None:
        """Append one token-usage + cost record to content/<profile>/costs.jsonl.

        Logs cache_creation and cache_read counts alongside regular input/output so
        cache effectiveness is visible over time, AND computes a cache-aware
        ``cost_usd`` so the brain — the largest single cost — is finally counted by
        the monthly cap (the worker/vision tools already self-meter;
        this closes the gap for the orchestrator). Best-effort: any error is silently
        ignored so a logging failure never breaks the response stream.
        """
        import time

        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        # Rates follow the resolved brain model (registry → env override → default).
        spec = resolve_model("brain_plan")
        r_in, r_out = resolve_rates(
            spec, env_input="BRAIN_INPUT_USD_PER_1K", env_output="BRAIN_OUTPUT_USD_PER_1K"
        )
        cost_usd = brain_cost_usd(usage, input_usd_per_1k=r_in, output_usd_per_1k=r_out)

        # Backend path: route the same cache-aware record to the injected sink
        # (→ Postgres cost_records, workspace-scoped) instead of the container disk.
        # The cost formula stays here (one home); the sink owns runtime + scope.
        if self._usage_sink is not None:
            from gtm_core.metering import CostRecord

            rec = CostRecord(
                runtime="vps",  # sink may replace() this with its own runtime/scope
                source="brain",
                cost_usd=cost_usd,
                model_or_sku=spec.model,
                profile=self._profile,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_input_tokens=cache_creation,
                cache_read_input_tokens=cache_read,
            )
            try:
                self._usage_sink(rec)
            except Exception:  # noqa: BLE001 — metering must never break the stream
                pass  # nosec B110 — intentional best-effort swallow
            return

        # VPS / cockpit default: append the cache-aware record to costs.jsonl via Ledgers, so
        # the row is hash-chained like every other ledger write (gtm_core.ledger_verify).
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "profile": self._profile,
            "tool": "brain",
            "model": spec.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "cost_usd": cost_usd,
        }
        try:
            Ledgers(self._cfg, self._profile).append_cost(entry)
        except (OSError, ValueError):  # ValueError: Ledgers refuses an unsafe profile segment
            pass

    async def close(self) -> None:
        """Disconnect the SDK client and release resources (idempotent)."""
        try:
            if self._client is not None and self._connected:
                await self._client.disconnect()
        finally:
            self._connected = False
            self._client = None
            if self._tenant_plan is not None:
                self._tenant_plan.discard()
                self._tenant_plan = None


class SessionStore:
    """Per-chat session registry. Each ``chat_id`` binds its own profile.

    There is no global mutable profile: ``active_profile(chat_id)`` falls back to
    ``cfg.default_profile`` until a chat explicitly switches via
    :meth:`switch_profile`. Switching closes and drops the chat's old session so
    the next :meth:`session` call rebuilds it against the new profile.

    A per-store :class:`asyncio.Lock` serialises session creation/teardown so
    concurrent updates for the same chat cannot race two clients into existence.
    """

    #: Reply-delivery modes a chat can select (see :meth:`set_reply_mode`).
    #: ``both`` = text bubble + voice clip (default), ``text`` = text only,
    #: ``voice`` = voice clip only (gates always force text — see the cockpit).
    REPLY_MODES = ("both", "text", "voice")

    def __init__(
        self,
        cfg: Config,
        can_use_tool_factory=None,
        *,
        state=None,
        settings=None,
        notice_sink=None,
    ) -> None:
        self._cfg = cfg
        # F1: durable state (None = exactly the old in-memory behaviour), the COCKPIT_* settings, and an
        # async ``(chat_id, text)`` sink the cockpit uses to tell the operator about a silent reset.
        self._state = state
        if settings is None:
            from .cockpit_state import CockpitSettings

            settings = CockpitSettings.from_env()
        self._settings = settings
        self._notice_sink = notice_sink
        self._notices: dict[int, list[str]] = {}
        # chat_id -> bound profile name
        self._profiles: dict[int, str] = {}
        # chat_id -> reply-delivery mode (default "both"). Persisted to disk so a
        # cockpit redeploy doesn't silently reset everyone back to "both".
        self._reply_modes: dict[int, str] = {}
        self._load_reply_modes()
        # chat_id -> live AgentSession (created on demand)
        self._sessions: dict[int, AgentSession] = {}
        # Optional factory: chat_id -> can_use_tool callback. The cockpit supplies one so a
        # blocked tool can be surfaced to that specific operator; without it, sessions fall
        # back to the fail-closed headless policy in build_agent_options.
        self._can_use_tool_factory = can_use_tool_factory
        self._lock = asyncio.Lock()

    def _new_session(
        self, profile: str, chat_id: int, *, allow_resume: bool = True
    ) -> AgentSession:
        """Create an :class:`AgentSession`, binding the per-chat permission callback if any.

        With durable state, a session recorded for THIS ``(chat, profile)`` is resumed (F1) unless it has
        been idle too long or run too many turns — in which case the conversation starts fresh and the
        operator is told. The key includes the profile, so a ``/profile`` switch can never resume another
        profile's conversation.
        """
        cb = self._can_use_tool_factory(chat_id) if self._can_use_tool_factory else None
        resume = None
        on_id = None
        if self._state is not None:
            state = self._state
            on_id = lambda sid, c=chat_id, p=profile: state.record_session(c, p, sid)  # noqa: E731
            if allow_resume and self._settings.session_resume:
                decision = state.resume_decision(
                    chat_id,
                    profile,
                    self._settings.session_max_idle_days,
                    self._settings.session_max_turns,
                )
                resume = decision.session_id
                if decision.notice:
                    state.drop_session(chat_id, profile)
                    self._notices.setdefault(chat_id, []).append(decision.notice)
        return AgentSession(self._cfg, profile, can_use_tool=cb, resume=resume, on_session_id=on_id)

    def active_profile(self, chat_id: int) -> str:
        """Return the profile bound to ``chat_id`` (default: ``cfg.default_profile``)."""
        bound = self._profiles.get(chat_id)
        if bound is None and self._state is not None:
            bound = self._state.binding(chat_id)
        return bound or self._cfg.default_profile

    async def _flush_notices(self, chat_id: int) -> None:
        """Deliver any queued silent-reset notices for ``chat_id`` (best-effort, never blocks a turn)."""
        queued = self._notices.pop(chat_id, [])
        if not queued or self._notice_sink is None:
            return
        for text in queued:
            try:
                await self._notice_sink(chat_id, text)
            except Exception:  # noqa: BLE001 — a notice that cannot be delivered must not cost the turn
                logger.debug("could not deliver notice to chat_id=%s", chat_id, exc_info=True)

    def reply_mode(self, chat_id: int) -> str:
        """Return ``chat_id``'s reply-delivery mode (default ``"both"``)."""
        return self._reply_modes.get(chat_id, "both")

    def set_reply_mode(self, chat_id: int, mode: str) -> None:
        """Bind ``chat_id`` to reply-delivery ``mode`` and persist the change.

        Raises:
            ValueError: if ``mode`` is not one of :attr:`REPLY_MODES`.
        """
        if mode not in self.REPLY_MODES:
            raise ValueError(f"unknown reply mode {mode!r}; expected one of {self.REPLY_MODES}")
        self._reply_modes[chat_id] = mode
        self._save_reply_modes()

    def _reply_modes_path(self) -> Path:
        """Durable store for per-chat reply modes (survives cockpit redeploys).

        Lives in the cross-profile ``_system`` area on the persistent content
        volume, beside the costs ledger — reply mode is a per-chat cockpit
        setting, not tenant content, so it is profile-independent and must not
        sit under any one profile's ``content/<active>/`` tree.
        """
        return self._cfg.content_root / "_system" / "cockpit-prefs.json"

    def _load_reply_modes(self) -> None:
        """Populate ``self._reply_modes`` from disk; best-effort, never raises.

        A missing, corrupt, or hand-edited file degrades to the in-memory default
        rather than crashing cockpit startup: unknown modes and non-integer chat
        ids are skipped individually.
        """
        try:
            raw = json.loads(self._reply_modes_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return
        modes = raw.get("reply_modes") if isinstance(raw, dict) else None
        if not isinstance(modes, dict):
            return
        for key, mode in modes.items():
            if mode not in self.REPLY_MODES:
                continue
            try:
                self._reply_modes[int(key)] = mode
            except (TypeError, ValueError):
                continue

    def _save_reply_modes(self) -> None:
        """Persist ``self._reply_modes`` atomically; best-effort, never raises.

        A failed write must never break the chat flow — the in-memory value still
        applies for the rest of the process; only cross-restart persistence is
        lost. The temp-file + ``replace`` keeps a concurrent reader from ever
        seeing a half-written file.
        """
        path = self._reply_modes_path()
        payload = {"reply_modes": {str(cid): m for cid, m in self._reply_modes.items()}}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.debug("Could not persist reply modes to %s: %s", path, exc)

    async def switch_profile(self, chat_id: int, name: str) -> None:
        """Bind ``chat_id`` to profile ``name``, tearing down any old session.

        Validates ``name`` via :func:`profiles.profile_dir` (raises ``ValueError``
        on an unknown profile). If the profile actually changed, the existing
        session for the chat is closed and dropped so the next :meth:`session`
        call rebuilds against the new profile's system prompt + MCP config.
        """
        # Validate first — raises ValueError if the profile does not exist.
        profiles.profile_dir(self._cfg.profiles_root, name)

        async with self._lock:
            previous = self.active_profile(chat_id)
            self._profiles[chat_id] = name
            if self._state is not None:
                self._state.set_binding(chat_id, name)
            if previous != name:
                old = self._sessions.pop(chat_id, None)
                if old is not None:
                    await old.close()

    def session(self, chat_id: int) -> AgentSession:
        """Return the chat's session, creating it if absent or if the profile changed.

        NOTE: this is intentionally synchronous (it does not ``connect()``) so it
        can be called from non-async code; the session connects lazily on first
        :meth:`AgentSession.run`. If the bound profile no longer matches the cached
        session's profile, a fresh (disconnected) session is created — the stale
        one is replaced but not closed here (use :meth:`switch_profile` for clean
        teardown; this branch only guards against drift).
        """
        wanted = self.active_profile(chat_id)
        existing = self._sessions.get(chat_id)
        if existing is None or existing.profile != wanted:
            existing = self._new_session(wanted, chat_id)
            self._sessions[chat_id] = existing
        return existing

    async def run(self, chat_id: int, prompt: str) -> AsyncGenerator[str, None]:
        """Run ``prompt`` on ``chat_id``'s session, yielding streamed assistant text.

        The get-or-create runs under the store lock so two concurrent updates for
        the same chat cannot race two ``ClaudeSDKClient``s into existence (PTB
        dispatches each update in its own task). The lock is released before the
        (long) run so it never serialises unrelated chats.
        """
        async with self._lock:
            wanted = self.active_profile(chat_id)
            sess = self._sessions.get(chat_id)
            if sess is None or sess.profile != wanted:
                sess = self._new_session(wanted, chat_id)
                self._sessions[chat_id] = sess
        await self._flush_notices(chat_id)
        started = False
        try:
            async for text in sess.run(prompt):
                started = True
                yield text
        except Exception as exc:
            if started or not self._is_failed_resume(sess, exc):
                raise
            # The CLI has no transcript for the id we asked it to resume (e.g. the volume was lost).
            # Forget the dead id, start a fresh conversation, tell the operator, and replay the prompt.
            logger.warning(
                "resume failed for chat_id=%s profile=%s — starting fresh", chat_id, wanted
            )
            if self._state is not None:
                self._state.drop_session(chat_id, wanted)
            try:
                await sess.close()
            except Exception:  # noqa: BLE001
                logger.debug("closing the failed-resume session raised", exc_info=True)
            async with self._lock:
                sess = self._new_session(wanted, chat_id, allow_resume=False)
                self._sessions[chat_id] = sess
            from .cockpit_state import NOTICE_FAILED_RESUME

            self._notices.setdefault(chat_id, []).append(NOTICE_FAILED_RESUME)
            await self._flush_notices(chat_id)
            async for text in sess.run(prompt):
                yield text

    @staticmethod
    def _is_failed_resume(sess: AgentSession, exc: BaseException) -> bool:
        """True only for the CLI's own "no transcript for that id" error on a session we asked to resume.

        Anything else (network, auth, a crashed CLI) must NOT make the store forget a good session id.
        """
        return sess.resumed_from is not None and "No conversation found" in str(exc)

    async def interrupt(self, chat_id: int) -> bool:
        """Interrupt the turn running on ``chat_id``'s live session (``/stop``). False if nothing is running.

        Never raises: a session that cannot be interrupted is reported as "nothing to stop".
        """
        sess = self._sessions.get(chat_id)
        if sess is None:
            return False
        try:
            return await sess.interrupt()
        except Exception:  # noqa: BLE001 — a failed interrupt must never become an error for the operator
            logger.warning("interrupt failed for chat_id=%s", chat_id, exc_info=True)
            return False

    async def reset(self, chat_id: int) -> None:
        """Close and drop ``chat_id``'s session (keeps the bound profile).

        The next :meth:`session`/:meth:`run` call rebuilds a fresh session on the
        same profile — useful for clearing conversation state without re-binding.
        """
        async with self._lock:
            sess = self._sessions.pop(chat_id, None)
            if self._state is not None:
                self._state.drop_session(chat_id, self.active_profile(chat_id))
        if sess is not None:
            await sess.close()
