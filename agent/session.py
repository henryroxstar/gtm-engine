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
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from gtm_core.metering import brain_cost_usd, resolve_rates
from gtm_core.models import resolve_model

from . import mcp_config, permissions, profiles

if TYPE_CHECKING:  # type hints only
    from .config import Config

logger = logging.getLogger(__name__)


# Brain cost rates (USD per 1K tokens) resolve through the shared metering contract:
# env override (BRAIN_INPUT_USD_PER_1K / BRAIN_OUTPUT_USD_PER_1K) → the registry rate
# pinned for the resolved brain model → list-pricing default. Sourcing the rate from the
# *resolved* spec means a brain_plan→brain_cheap (Sonnet→Haiku) fallback bills Haiku
# rates, not Sonnet's. Cache multipliers (1.25x write / 0.10x read) live in
# gtm_core.metering. See docs/prds/2026-06-19-cost-usage-tracking.md §5.2.


async def stream_brain_messages(options, prompt: str) -> AsyncGenerator[object, None]:
    """Drive one one-shot brain turn over a persistent ``ClaudeSDKClient`` and yield each message.

    Use this for EVERY headless / one-shot run that sets ``can_use_tool`` — the pipeline stages
    and the ``agent`` CLI one-shot.

    Do **not** reach for the module-level ``claude_agent_sdk.query()`` here. Driven with a single
    user message (a one-yield async generator), ``query()`` closes the SDK↔CLI control stream the
    moment that generator is exhausted — so the CLI's permission round-trip for every gated tool
    (``Write`` / ``Bash`` / any MCP tool) comes back ``Tool permission request failed: Error: Stream
    closed`` while read-only tools (which need no ``can_use_tool`` callback) keep working. That is the
    asymmetry that silently broke the headless pipeline's plan-draft write and news-MCP calls. The
    persistent ``ClaudeSDKClient`` keeps the control stream open for the whole turn, so the callback
    is reached and permissions resolve. ``AgentSession`` already used this client pattern; this is the
    shared one-shot equivalent. Proven 2026-06-28 — see PENDING.md.
    """
    from claude_agent_sdk import ClaudeSDKClient

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            yield msg


def build_agent_options(cfg: Config, profile: str, *, can_use_tool=None, role: str = "brain_plan"):
    """Construct the ``ClaudeAgentOptions`` shared by sessions and one-shot runs.

    Centralised so the persistent :class:`AgentSession` and the headless
    ``python -m agent`` path build IDENTICAL options — same plugin, same MCP
    servers, same system prompt, same permission posture.

    Permission posture (least privilege; see :mod:`agent.permissions` and
    ``docs/SECURITY-SELF-ASSESSMENT.md``). Phase 0 used ``bypassPermissions``
    (every tool auto-approved, including ``Bash``/``Write`` on the VPS) because
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
    # never the old blanket bypass.
    if can_use_tool is None:
        can_use_tool = permissions.make_headless_can_use_tool()

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
        "mcp_servers": mcp_config.build_mcp_servers(cfg, profile),
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

    # Cross-provider brain routing (P4). When a role resolves to a non-Anthropic
    # provider (e.g. brain_radar → DeepSeek, which speaks the Anthropic Messages
    # format natively), point THIS subprocess's SDK at that provider's base_url +
    # key via options.env. The SDK merges this over os.environ for the spawned CLI
    # ONLY (subprocess_cli.py) — the parent process and the separately-spawned MCP
    # workers keep the real ANTHROPIC_* env untouched. Boundary: only the
    # mechanical, no-PII stages (radar/research) ever pass a DeepSeek role here;
    # plan/studio/publish stay on Claude by construction (see CLAUDE.md "Model
    # discipline" + docs/SECURITY-SELF-ASSESSMENT.md D-01).
    # Scope the spawned CLI's skills to THIS run's content + profiles roots. The SDK
    # merges options.env over os.environ for the CLI subprocess ONLY (not the parent
    # process or the separately-spawned MCP workers), so a workspace-scoped Config
    # (the backend, P3) confines skill file I/O to that workspace's tree with no
    # shared-os.environ race between concurrent runs. On the VPS these equal the
    # ambient values, so behaviour is preserved.
    env_overrides: dict[str, str] = {
        "GTM_CONTENT_ROOT": str(cfg.content_root),
        "GTM_PROFILES_ROOT": str(cfg.profiles_root),
    }
    if spec.provider != "anthropic":
        key = spec.api_key() or ""
        env_overrides.update(
            {
                "ANTHROPIC_BASE_URL": spec.base_url,
                "ANTHROPIC_API_KEY": key,
                "ANTHROPIC_AUTH_TOKEN": key,
            }
        )
    options_kw["env"] = env_overrides

    return ClaudeAgentOptions(**options_kw)


class AgentSession:
    """A persistent, multi-turn Claude Code session bound to one profile.

    Lifecycle: ``__init__`` (cheap, no I/O) → ``await connect()`` →
    ``async for text in run(prompt)`` (repeatable) → ``await close()``.
    """

    def __init__(self, cfg: Config, profile: str, can_use_tool=None, usage_sink=None) -> None:
        self._cfg = cfg
        self._profile = profile
        # Per-session permission policy callback (None → build_agent_options uses the
        # fail-closed headless default). The cockpit passes a chat-bound callback that
        # can notify the operator; the one-shot CLI passes the headless callback.
        self._can_use_tool = can_use_tool
        # Optional brain-cost sink: a Callable[[CostRecord], None]. When None (VPS /
        # cockpit default), _log_usage writes the cache-aware record to costs.jsonl as
        # before. When supplied (Backend), the same record is routed to the sink
        # (→ Postgres cost_records, workspace-scoped) instead of the container disk.
        self._usage_sink = usage_sink
        self._client = None  # lazily created in connect()
        self._connected = False

    @property
    def profile(self) -> str:
        """The profile this session is bound to."""
        return self._profile

    async def connect(self) -> None:
        """Start the underlying SDK client (idempotent).

        Builds the options, instantiates :class:`ClaudeSDKClient`, and opens the
        connection. Safe to call twice — a second call is a no-op.
        """
        if self._connected:
            return
        from claude_agent_sdk import ClaudeSDKClient

        options = build_agent_options(self._cfg, self._profile, can_use_tool=self._can_use_tool)
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()
        self._connected = True

    async def run(self, prompt: str) -> AsyncGenerator[str, None]:
        """Send ``prompt`` and yield assistant text blocks as they stream in.

        Yields only the text of ``TextBlock``s inside ``AssistantMessage``s —
        tool-call blocks, system messages, and the final result message are
        consumed but not yielded (the cockpit only wants the prose).

        Auto-connects if the session has not been connected yet.
        """
        if not self._connected:
            await self.connect()

        # Imported lazily so this module imports without the SDK present.
        from claude_agent_sdk import AssistantMessage, TextBlock

        assert self._client is not None  # connect() guarantees this
        await self._client.query(prompt)
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.usage:
                    self._log_usage(msg.usage)
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        yield block.text

    def _log_usage(self, usage: dict) -> None:
        """Append one token-usage + cost record to content/<profile>/costs.jsonl.

        Logs cache_creation and cache_read counts alongside regular input/output so
        cache effectiveness is visible over time, AND computes a cache-aware
        ``cost_usd`` so the brain — the largest single cost — is finally counted by
        the monthly cap (the worker/vision tools already self-meter;
        this closes the gap for the orchestrator). Best-effort: any error is silently
        ignored so a logging failure never breaks the response stream.
        """
        import json
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
                pass
            return

        # VPS / cockpit default: append the cache-aware record to costs.jsonl (unchanged).
        ledger = self._cfg.content_root / self._profile / "costs.jsonl"
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
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    async def close(self) -> None:
        """Disconnect the SDK client and release resources (idempotent)."""
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            finally:
                self._connected = False
                self._client = None


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

    def __init__(self, cfg: Config, can_use_tool_factory=None) -> None:
        self._cfg = cfg
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

    def _new_session(self, profile: str, chat_id: int) -> AgentSession:
        """Create an :class:`AgentSession`, binding the per-chat permission callback if any."""
        cb = self._can_use_tool_factory(chat_id) if self._can_use_tool_factory else None
        return AgentSession(self._cfg, profile, can_use_tool=cb)

    def active_profile(self, chat_id: int) -> str:
        """Return the profile bound to ``chat_id`` (default: ``cfg.default_profile``)."""
        return self._profiles.get(chat_id, self._cfg.default_profile)

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
            previous = self._profiles.get(chat_id, self._cfg.default_profile)
            self._profiles[chat_id] = name
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
        async for text in sess.run(prompt):
            yield text

    async def reset(self, chat_id: int) -> None:
        """Close and drop ``chat_id``'s session (keeps the bound profile).

        The next :meth:`session`/:meth:`run` call rebuilds a fresh session on the
        same profile — useful for clearing conversation state without re-binding.
        """
        async with self._lock:
            sess = self._sessions.pop(chat_id, None)
        if sess is not None:
            await sess.close()
