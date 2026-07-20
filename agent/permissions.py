"""Least-privilege tool policy for the brain (replaces ``bypassPermissions``).

Phase 0 ran the SDK with ``permission_mode="bypassPermissions"`` — every tool call
auto-approved, including ``Bash``/``Write`` on the VPS. Measured against the repo's own
frameworks that is a least-privilege hole: OWASP **ASI02** (Tool Misuse), **ASI05**
(Unexpected Code Execution), NIST **AC-6** (Least Privilege), AIRQ **D-02/D-03**
(Execution Isolation / Action Controls). See ``docs/SECURITY-SELF-ASSESSMENT.md``.

This module is the **authoritative, code-level policy**. It is intentionally pure (no
``claude-agent-sdk`` import in the classifier) so it imports anywhere and is unit-tested
directly. The SDK-typed callbacks at the bottom are thin wrappers built lazily by the two
run paths:

  - **Headless / cron** (no human reachable): unknown ⇒ **deny** (fail closed, never hang).
  - **Telegram cockpit** (human reachable): unknown ⇒ deny **and notify the operator**
    (inline approve-and-resume is a deferred, larger item — see the self-assessment).

The classifier returns one of three decisions:

  - ``"allow"``    — a known-safe tool (read/search/todo, writes inside the workspace, any
                     MCP tool — MCP egress is already constrained + pinned), or a Bash command
                     not on the dangerous-program deny list.
  - ``"deny"``     — a dangerous shell/process-exec vector (the ASI05 floor). Mirrored as
                     declarative ``deny`` rules in ``.claude/settings.json`` and as
                     ``disallowed_tools`` so the floor holds even if a callback is misconfigured.
  - ``"escalate"`` — nested shells (bash/sh/zsh) only; the path decides (cron denies, cockpit
                     denies + notifies).

Design note: ``Bash`` uses a **denylist** model. Anything not in ``_DANGEROUS_PROGRAMS`` and not
a nested shell is allowed — blocking unrecognised programs only breaks legitimate skill scripts
without adding meaningful security, since the real exfiltration/destruction vectors (rm, curl,
wget, sudo, ssh, etc.) are all explicitly denied. ``python -c '...'`` is the one exception: it
is denied because it is arbitrary code execution wearing a safe program name.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Awaitable, Callable

Decision = str  # one of: "allow" | "deny" | "escalate"

# --------------------------------------------------------------------------- #
# Built-in (non-Bash) tools
# --------------------------------------------------------------------------- #

#: Built-in tools that are always safe for the content pipeline. Writes/edits are
#: constrained to the working tree by the SDK; the tenant boundary (content/<active>/ only)
#: is enforced by the system prompt + CLAUDE.md, not by withholding the Write tool.
_ALWAYS_ALLOW: frozenset[str] = frozenset(
    {
        "Read",
        "Glob",
        "Grep",
        "LS",
        "NotebookRead",
        "TodoWrite",
        "TodoRead",
        "Task",
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "WebSearch",
        "WebFetch",  # read-only-ish; tightening web egress is a noted residual
        "ExitPlanMode",
    }
)

#: Any Model Context Protocol tool. Egress through MCP is the sanctioned path (pinned servers,
#: least-privilege DB role, account-pinned publish webhook), so MCP tools are allowed as a class.
_MCP_PREFIX = "mcp__"

# --------------------------------------------------------------------------- #
# Bash classification
# --------------------------------------------------------------------------- #

#: Programs that are denied outright wherever they appear in a Bash command (ASI05 floor).
#: Kept in sync with the declarative deny rules in .claude/settings.json.
_DANGEROUS_PROGRAMS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "curl",
        "wget",
        "npx",
        "npm",
        "pip",
        "pip3",
        "sudo",
        "ssh",
        "scp",
        "sftp",
        "nc",
        "ncat",
        "netcat",
        "telnet",
        "ftp",
        "dd",
        "chmod",
        "chown",
        "mkfifo",
        "eval",
        "exec",
        "kill",
        "pkill",
        "killall",
    }
)

#: Shell operators we split a command on, so chaining (``a && rm -rf``, ``a; curl …``,
#: pipes, background) cannot smuggle a dangerous program past a safe-looking head.
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||;|\||&|\n")


def _segment_program(segment: str) -> str | None:
    """Return the program name of one command segment, skipping ``FOO=bar`` env prefixes.

    Returns ``None`` for an empty/unparseable segment (treated as benign by the caller).
    """
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        return ""  # unbalanced quotes etc. — unparseable ⇒ caller escalates
    for tok in tokens:
        # Skip leading environment assignments (`KEY=value cmd ...`).
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            continue
        # Strip a path prefix: /usr/bin/curl -> curl.
        return tok.rsplit("/", 1)[-1]
    return None


def _classify_bash(command: str) -> Decision:
    """Classify a Bash command by inspecting every chained segment's program.

    Precedence across segments: any ``deny`` ⇒ deny; else any ``escalate`` ⇒ escalate;
    else ``allow``. Empty commands are escalated (nothing to vet ⇒ fail closed).
    """
    if not command or not command.strip():
        return "escalate"

    # Join shell line-continuations (a trailing ``\`` before a newline) into one logical line
    # BEFORE splitting. The brain routinely emits multi-line commands with ``\`` continuations
    # (every skill that documents a wrapped command does this). Without this, splitting on ``\n``
    # produced segments ending in a dangling backslash, which ``shlex.split`` cannot parse →
    # ``_segment_program`` returned ``""`` → the whole command wrongly **escalated** (and was
    # denied). That was the real, systemic block: it hit any skill with a multi-line command,
    # including the account-dossier visuals/build steps. Single-line commands were unaffected,
    # which is exactly why earlier single-line tests passed while the live multi-line calls failed.
    command = re.sub(r"\\\r?\n", " ", command)

    worst: Decision = "allow"
    for raw_segment in _SHELL_SPLIT_RE.split(command):
        segment = raw_segment.strip()
        if not segment:
            continue
        program = _segment_program(segment)
        if program is None:
            continue  # nothing executable in this segment
        if program == "":
            return "escalate"  # unparseable ⇒ fail closed immediately

        if program in _DANGEROUS_PROGRAMS:
            return "deny"
        if program in ("python", "python3"):
            # Block raw arbitrary code exec forms:
            #   python -c '...'   — explicit inline flag
            #   python - <<'HD'   — stdin heredoc (semantically identical to -c; the `-` arg means
            #                       "read from stdin", which is whatever the shell pipes in)
            # Script paths, module calls (-m), and all other invocation forms are allowed.
            args = segment.split(None, 1)[1].strip() if " " in segment else ""
            seg_decision = "deny" if re.match(r"^(-c\s|-\s|-$)", args) else "allow"
        elif program == "uv":
            # Block `uv run python -c '...'` and `uv run python -`; everything else (uv run
            # script.py, uv add, uv sync, etc.) is allowed — the dangerous-program check on
            # individual segments already catches `uv run curl` etc.
            try:
                tokens = shlex.split(segment)
            except ValueError:
                tokens = []
            rest = [t for t in tokens[1:] if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t)]
            if (
                len(rest) >= 2
                and rest[0] == "run"
                and rest[1] in ("python", "python3")
                and re.match(r"^(-c\s|-\s|-$)", " ".join(rest[2:]))
            ):
                seg_decision = "deny"
            else:
                seg_decision = "allow"
        elif program in ("bash", "sh", "zsh"):
            seg_decision = "escalate"  # nested shell can run anything ⇒ never auto-allow
        else:
            # Denylist model: anything not in _DANGEROUS_PROGRAMS and not a nested shell
            # is allowed. The dangerous-program deny floor above already blocks the real
            # exfiltration/destruction vectors; blocking unrecognised programs only breaks
            # legitimate skill scripts without adding meaningful security.
            seg_decision = "allow"

        if seg_decision == "deny":
            return "deny"
        if seg_decision == "escalate":
            worst = "escalate"
    return worst


# --------------------------------------------------------------------------- #
# Top-level classifier
# --------------------------------------------------------------------------- #


def classify_tool(tool_name: str, tool_input: dict | None) -> Decision:
    """Return the policy decision for one tool call. Pure; safe to unit-test directly."""
    if not tool_name:
        return "escalate"
    if tool_name.startswith(_MCP_PREFIX):
        return "allow"
    if tool_name in _ALWAYS_ALLOW:
        return "allow"
    if tool_name == "Bash":
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command", ""))
        return _classify_bash(command)
    # Unrecognised tool ⇒ fail closed; the run path decides deny vs deny+notify.
    return "escalate"


def deny_message(tool_name: str, decision: Decision) -> str:
    """A short, secret-free, *actionable* reason string for a denied/escalated tool call.

    Clarity matters for budget: a vague denial makes the brain blindly retry; a specific one lets
    it switch tactics on the next turn instead of looping. So the deny path names the usual culprits
    and points at the approved alternative (an MCP tool), and the escalate path says what to do.
    """
    if decision == "deny":
        return (
            f"DENIED (permanent): '{tool_name}' uses a program on the hard-deny floor "
            "(npm, npx, curl, wget, pip, rm, sudo, ssh, …) or is `python -c '…'` (raw code exec). "
            "This will NEVER be allowed and rephrasing won't change it — do not retry. If a skill "
            "told you to run npm/npx/node to render a deck or carousel, use the MCP renderer instead "
            "(mcp__deck__export_deck), not a shell build. Otherwise tell the operator this step is blocked "
            "and continue with what you can do."
        )
    return (
        f"BLOCKED: '{tool_name}' is not permitted as written — do NOT retry the same thing. "
        "For web pages use the Firecrawl MCP tool or WebFetch (never a shell fetch); for editing "
        "files use Edit/Write; for a nested shell (bash -c/sh -c) run the command directly instead. "
        "If nothing fits, the capability is unavailable — say so to the operator and move on."
    )


#: Declarative ``disallowed_tools`` mirror of the dangerous-program floor, passed to the SDK so
#: the deny holds even before the callback runs (and even if a future edit drops the callback).
DANGEROUS_TOOL_DENY_RULES: tuple[str, ...] = tuple(
    f"Bash({prog}:*)" for prog in sorted(_DANGEROUS_PROGRAMS)
)


# --------------------------------------------------------------------------- #
# Loop guard (anti-retry)
# --------------------------------------------------------------------------- #

#: After this many denials of the *same* tool call within one session, the deny message switches
#: from the normal explanation to a hard "stop, this is final" instruction. A denial is
#: model-driven: the SDK hands the deny back to the brain, which — following a skill that told it
#: to run the command — retries. Without this guard a single blocked call looped 85+ times and
#: burned budget under the per-run cap. The guard makes the brain stop instead of spinning.
STRIKE_LIMIT = 3

#: The per-call strike guard above is not enough on its own: when a denial comes back, the brain
#: often *rephrases* the command (tweaks a path, adds/drops ``cd``, reorders args). Each variation
#: is a NEW key with a fresh per-call budget, so the loop kept going ~12-15× and burned real money
#: before the per-run dollar cap finally stopped it. This GLOBAL cap counts *every* denial in the
#: session regardless of which command: once the brain has been denied this many times total, every
#: further denial returns the terminal "the run is over, stop now" message — so a rephrasing loop is
#: bounded to a handful of turns instead of grinding to the dollar cap.
GLOBAL_STRIKE_LIMIT = 6


def _attempt_key(tool_name: str, tool_input: dict | None) -> tuple[str, str]:
    """A stable key for one tool *call* (name + normalised input) so identical retries collide
    but two genuinely different commands (e.g. two distinct ``Bash`` lines) do not."""
    try:
        payload = json.dumps(tool_input or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_input)
    return (tool_name, payload)


def hard_stop_message(tool_name: str, count: int) -> str:
    """A firm, final-denial message after repeated attempts at the *same* blocked call."""
    return (
        f"⛔ STOP — you have tried '{tool_name}' {count}× this session and it is denied every "
        "time by the least-privilege policy. Do NOT attempt it again: retrying only burns budget "
        "and the result will not change. This capability is not available to you. Either achieve "
        "the goal with an approved tool/MCP, or tell the operator this step is blocked and move "
        "on. Treat this denial as final."
    )


def global_hard_stop_message(total: int) -> str:
    """Terminal message once the brain has hit the GLOBAL denial cap across all commands.

    This fires when the brain is in a *rephrasing* loop (different blocked commands, not one
    repeated). It is the strongest steer the policy can give: end the turn now. The per-run dollar
    cap (``max_budget_usd``) is the hard backstop; this exists to stop well before that.
    """
    return (
        f"⛔ STOP — END THIS TURN NOW. You have been denied {total} tool calls this session by the "
        "least-privilege policy and you keep rephrasing blocked commands. Rephrasing will NOT help — "
        "the policy decides by program (e.g. npm/npx/curl/rm are always denied; `python -c` is "
        "denied), not by wording. Do NOT call any more tools. Stop here, write a short plain-language "
        "message to the operator explaining exactly which step is blocked and what you'd need (an MCP "
        "tool such as mcp__deck__export_deck for deck rendering, an allow-list change, or them running it), "
        "and end your turn. Continuing only burns the operator's budget."
    )


# --------------------------------------------------------------------------- #
# SDK-typed callbacks (built lazily by each run path)
# --------------------------------------------------------------------------- #

# can_use_tool signature: async (tool_name, tool_input, context) -> PermissionResult
CanUseTool = Callable[..., Awaitable[object]]
# notifier for the cockpit path: async (tool_name, tool_input, final) -> None
# ``final`` is True on the strike-limit notice (the brain has been told to stop), False on first.
BlockedToolNotifier = Callable[..., Awaitable[None]]


def make_headless_can_use_tool(
    on_deny: Callable[[str, dict, Decision], None] | None = None,
) -> CanUseTool:
    """Callback for the unattended/cron path: allow known-safe, otherwise **deny** (fail closed).

    There is no human to ask, so ``escalate`` collapses to ``deny`` — but it returns a decision
    immediately, so the run never hangs (the old reason ``bypassPermissions`` existed). ``on_deny``
    is an optional sink for logging which calls were blocked. A per-callback strike counter swaps
    the deny text for a hard "stop" message after ``STRIKE_LIMIT`` retries of the same call, so a
    denied tool cannot loop and burn budget.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    attempts: dict[tuple[str, str], int] = {}
    totals = {"denials": 0}  # session-wide denial count (all commands) — global loop guard

    async def _cb(tool_name: str, tool_input: dict, context: object) -> object:
        decision = classify_tool(tool_name, tool_input)
        if decision == "allow":
            return PermissionResultAllow()
        key = _attempt_key(tool_name, tool_input)
        count = attempts[key] = attempts.get(key, 0) + 1
        totals["denials"] += 1
        if on_deny is not None:
            on_deny(tool_name, tool_input or {}, decision)
        # Global cap first: a rephrasing loop (many distinct blocked commands) is bounded here,
        # since the per-key counter alone resets on every new wording.
        if totals["denials"] >= GLOBAL_STRIKE_LIMIT:
            message = global_hard_stop_message(totals["denials"])
        elif count >= STRIKE_LIMIT:
            message = hard_stop_message(tool_name, count)
        else:
            message = deny_message(tool_name, decision)
        return PermissionResultDeny(message=message)

    return _cb


def make_cockpit_can_use_tool(notify: BlockedToolNotifier) -> CanUseTool:
    """Callback for the Telegram path: allow known-safe, deny dangerous, **notify on escalate**.

    An unrecognised (escalated) tool is denied AND surfaced to the operator, so a blocked call is
    visible rather than silent. A per-callback strike counter does two things: (1) after
    ``STRIKE_LIMIT`` retries of the same call the deny text becomes a hard "stop" instruction so
    the brain stops looping; (2) the operator is notified only on the **first** escalation and
    **once** at the strike limit — never on every retry (that was the 85-message spam). Interactive
    approve-and-resume is a deliberately deferred item — see docs/SECURITY-SELF-ASSESSMENT.md.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    attempts: dict[tuple[str, str], int] = {}
    totals = {"denials": 0, "global_notified": 0}  # session-wide denial count + notify-once flag

    async def _cb(tool_name: str, tool_input: dict, context: object) -> object:
        decision = classify_tool(tool_name, tool_input)
        if decision == "allow":
            return PermissionResultAllow()
        key = _attempt_key(tool_name, tool_input)
        count = attempts[key] = attempts.get(key, 0) + 1
        totals["denials"] += 1
        hit_global = totals["denials"] >= GLOBAL_STRIKE_LIMIT
        # Notify the operator: on the first escalation of a command, once when it crosses the
        # per-command strike limit, and exactly once when the session crosses the GLOBAL cap — the
        # rephrasing-loop signal, which fires regardless of decision type (under the denylist the
        # real loops are ``deny`` on npm/npx/`python -c`, not ``escalate``). After the global notice
        # we go fully silent (the brain has been told to end the turn). The dangerous-floor ``deny``
        # path stays silent for one-offs (it never spammed); the global cap is the loop safety net.
        if totals["global_notified"]:
            notify_now = False
        else:
            notify_now = decision == "escalate" and count in (1, STRIKE_LIMIT)
            if hit_global:
                totals["global_notified"] = 1
                notify_now = True
        if notify_now:
            try:
                await notify(tool_name, tool_input or {}, hit_global or count >= STRIKE_LIMIT)
            except Exception:  # noqa: BLE001 — a failed notice must not change the (deny) decision
                pass
        # Global cap first: a rephrasing loop (many distinct blocked commands) is bounded here,
        # since the per-key counter alone resets on every new wording.
        if hit_global:
            message = global_hard_stop_message(totals["denials"])
        elif count >= STRIKE_LIMIT:
            message = hard_stop_message(tool_name, count)
        else:
            message = deny_message(tool_name, decision)
        return PermissionResultDeny(message=message)

    return _cb
