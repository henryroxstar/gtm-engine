"""Confine a tenant run's file tools and read-only shell to its own workspace (MM9, first layer).

On the multi-tenant backend every workspace's tree lives in the SAME container under
``data/workspaces/<id>/``, and a run's working directory is the shared repo checkout. The permission
policy used to ask only "is this program allowed?", never "where?" — so one tenant's run (or an
instruction smuggled into untrusted research text, §R5) could read or overwrite another's files.

A :class:`Confinement` says where a run may READ (its own content and profile trees, plus the repo's
read-only directories skills legitimately consult) and WRITE (its own content tree only). The policy
decides by where a path RESOLVES — symlinks followed, ``..`` collapsed, ``~`` expanded — never by how it
is spelled. :func:`refusal` is pure; ``None`` confinement (the single-tenant VPS, the cockpit) changes
nothing, which ``tests/agent/test_permissions_golden.py`` pins against a recorded decision set.

**What this is not.** The shell is a denylist: a run that WRITES a script into its own tree and runs
``python script.py`` is not stopped by reading its command line, because the script can open any path the
container user can. Path parsing cannot confine that. This layer closes the built-in file tools and the
shell's plain read/redirect/copy forms; an OS-level second layer (a per-run operating-system user, so the
kernel — not a path parser — refuses the open) is what would close the rest, and until it exists
SECURITY-SELF-ASSESSMENT §6 item 20 says "partially confined", not closed.

``WebFetch`` is not path-confined; the exfiltration leg is closed by never letting another workspace's
bytes into the run's context in the first place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gtm_core.confine import within_any_root

__all__ = [
    "Confinement",
    "check_read",
    "check_write",
    "denial_record",
    "refusal",
    "resolve",
    "OUTSIDE",
    "UNRESOLVABLE",
]

OUTSIDE = "path_outside_workspace"
UNRESOLVABLE = "path_unresolvable"

#: The repo directories a skill legitimately reads. Strict first: anything else in the checkout is the
#: platform's own code and every other tenant's tree, and is refused until a staging run proves a need.
REPO_READ_DIRS = ("plugin", "packs", "schemas")

_READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_PATH_KEYS = ("file_path", "path", "notebook_path")
_GLOB_CHARS = "*?[{"

#: What a tenant may never WRITE even inside its own content tree: the ledgers that audit and bound it
#: (a forged or erased ``denials.jsonl`` hides its own refusals; ``costs.jsonl`` is the spend its caps
#: read) and the reserved ``_system`` profile that holds the onboarding spend the platform meters.
LEDGER = "ledger_write_refused"
# dispatch-intents.jsonl (F4): a tenant that could edit it could forge "never sent" over a real send and
# re-enable a duplicate, or forge "sent" over a failure and silence a legitimate one.
_LEDGER_NAMES = frozenset(
    {"denials.jsonl", "history.jsonl", "costs.jsonl", "dispatch-intents.jsonl"}
)
NOT_FOR_TENANTS = "tool_not_available_to_tenants"
_TENANT_BLOCKED_TOOLS = frozenset({"Monitor"})  # runs a shell command; not parsed, so not offered


@dataclass(frozen=True)
class Confinement:
    """Where one workspace's run may read and write, and the directory relative paths resolve from."""

    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    cwd: Path
    workspace_id: str | None = None

    @classmethod
    def for_workspace(cls, workspace_id: str, repo_root: Path) -> Confinement:
        """The roots for ``workspace_id``: its own content and profiles trees, the repo's read-only
        directories, and write access to its content tree only."""
        from gtm_core.paths import workspace_content_root, workspace_profiles_root

        content = workspace_content_root(workspace_id, repo_root).resolve()
        profiles = workspace_profiles_root(workspace_id, repo_root).resolve()
        repo = Path(repo_root).resolve()
        repo_dirs = tuple((repo / d).resolve() for d in REPO_READ_DIRS)
        return cls(
            read_roots=(content, profiles, *repo_dirs),
            write_roots=(content,),
            cwd=repo,
            workspace_id=workspace_id,
        )


def resolve(token: str, cwd: Path) -> Path | None:
    """Where ``token`` lands, symlinks followed, or ``None`` when it cannot be known statically.

    A token the shell would still expand (``$VAR``, a command substitution) has no static answer. A
    glob resolves its fixed PREFIX (everything before the first wildcard) — the wildcard cannot leave
    that directory unless a ``..`` follows it, which is refused outright.
    """
    if not token or "\x00" in token or "$" in token or "`" in token:
        return None
    wild = [token.index(c) for c in _GLOB_CHARS if c in token]
    if wild:
        cut = min(wild)
        if ".." in token[cut:].split("/"):
            return None
        token = token[:cut]
    try:
        p = Path(os.path.expanduser(token))
        if not p.is_absolute():
            p = cwd / p
        return p.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _check(token: str, roots: tuple[Path, ...], cwd: Path) -> tuple[str, str] | None:
    target = resolve(token, cwd)
    if target is None:
        return (UNRESOLVABLE, token)
    return None if within_any_root(target, roots) else (OUTSIDE, token)


def check_read(token: str, c: Confinement, cwd: Path) -> tuple[str, str] | None:
    """A place a run may READ: inside its own trees or a read-only repo directory."""
    return _check(token, c.read_roots, cwd)


def check_write(token: str, c: Confinement, cwd: Path) -> tuple[str, str] | None:
    """A place a run may WRITE: inside its own content tree, and never one of its audit/spend ledgers."""
    if bad := _check(token, c.write_roots, cwd):
        return bad
    target = resolve(token, cwd)
    if target is not None and (target.name in _LEDGER_NAMES or "_system" in target.parts):
        return (LEDGER, token)
    return None


def refusal(
    tool_name: str, tool_input: dict | None, confine: Confinement
) -> tuple[str, str] | None:
    """``(reason, offending path)`` when this call leaves the workspace, else ``None``. Pure."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in _READ_TOOLS:
        return _read_tool(tool_name, inp, confine)
    if tool_name in _WRITE_TOOLS:
        targets = [str(inp[k]) for k in _PATH_KEYS if isinstance(inp.get(k), str) and inp[k]]
        if not targets:
            return (UNRESOLVABLE, "")
        for t in targets:
            if bad := check_write(t, confine, confine.cwd):
                return bad
        return None
    if tool_name == "Bash":
        from .shell_paths import bash_refusal

        return bash_refusal(str(inp.get("command", "")), confine)
    if tool_name in _TENANT_BLOCKED_TOOLS or _foreign_mcp_server(tool_name):
        return (NOT_FOR_TENANTS, tool_name)
    return None


def _foreign_mcp_server(tool_name: str) -> bool:
    """True for an MCP tool whose server is not one the platform itself starts for a run.

    MM6, widened: a tenant may call only the servers ``agent/mcp_config.py`` hands it, named in the one
    registry that classifies them (``gtm_core.provider_registry.known_servers``). Anything else — Vibe
    Prospecting (one hosted identity for every caller, so a tenant's lookup cannot be told from another's),
    a copy the plugin ships, a server a project's ``.mcp.json`` declares, a connector added by hand — is
    refused. ``--strict-mcp-config`` (agent/session.py) keeps most of these from starting at all; this is
    the same refusal at the call, and it is an ALLOWlist: a server nobody classified is not callable,
    where the base policy would class-allow it.
    """
    if not tool_name.startswith("mcp__"):
        return False
    from gtm_core.provider_registry import known_servers

    server = tool_name[len("mcp__") :].rpartition("__")[0]
    return server not in known_servers()


def denial_record(tool_name, tool_input, confine, allowed_skills=None):
    """A ``ConfineDenial`` when ``confine`` — not the base policy — is what refused this call, else None.

    The record the permission callback hands its ``on_deny`` sink so ``denials.jsonl`` says WHY: the
    reason code, the path, and the workspace. Read only from that type (like ``GateDenial``), so nothing
    the brain wrote can reach the ledger as if the policy had said it.
    """
    if confine is None:
        return None
    from .denial_log import ConfineDenial
    from .permissions import classify_tool

    if classify_tool(tool_name, tool_input, allowed_skills=allowed_skills) == "deny":
        return None
    found = refusal(tool_name, tool_input, confine)
    if found is None:
        return None
    return ConfineDenial(reason=found[0], path=found[1], workspace_id=confine.workspace_id)


def _read_tool(tool: str, inp: dict, c: Confinement) -> tuple[str, str] | None:
    targets = [str(inp[k]) for k in _PATH_KEYS if isinstance(inp.get(k), str) and inp[k]]
    if not targets:
        if tool in ("Read", "NotebookRead"):
            return None  # nothing to read: the tool itself will refuse
        targets = ["."]  # Grep/Glob/LS with no path search the run's cwd: the whole shared checkout
    for t in targets:
        if bad := check_read(t, c, c.cwd):
            return bad
    # A pattern is searched relative to the path, so it must not climb out of it or name its own root.
    for key in ("pattern", "glob") if tool in ("Glob", "Grep") else ():
        pat = inp.get(key)
        if tool == "Grep" and key == "pattern":
            continue  # a regex, not a path
        if isinstance(pat, str) and pat:
            if ".." in pat.split("/"):
                return (OUTSIDE, pat)
            if pat.startswith(("/", "~")) and (bad := check_read(pat, c, c.cwd)):
                return bad
    return None
