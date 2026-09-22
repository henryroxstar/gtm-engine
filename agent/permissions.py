"""Least-privilege tool policy for the brain (replaces ``bypassPermissions``).

Phase 0 ran the SDK with ``permission_mode="bypassPermissions"`` — every tool call
auto-approved, including ``Bash``/``Write`` on the VPS. Measured against the repo's own
frameworks that is a least-privilege hole: OWASP **ASI02** (Tool Misuse), **ASI05**
(Unexpected Code Execution), NIST **AC-6** (Least Privilege), AIRQ **D-02/D-03**
(Execution Isolation / Action Controls).

This module is the **authoritative, code-level policy**. It is intentionally pure (no
``claude-agent-sdk`` import in the classifier) so it imports anywhere and is unit-tested
directly. The SDK-typed callbacks at the bottom are thin wrappers built lazily by the two
run paths:

  - **Headless / cron** (no human reachable): unknown ⇒ **deny** (fail closed, never hang).
  - **Telegram cockpit** (human reachable): unknown ⇒ deny **and notify the operator**.
    When the operator arms approve-and-resume (``APPROVE_RESUME_ENABLED``, default off), an
    **escalated** call becomes an inline Approve/Deny ask instead: approve runs it (and is
    remembered for identical calls this session), decline or timeout denies — fail closed.
    Only the escalate class is askable; the dangerous-program floor and secret reads are
    never one approval away.

The classifier returns one of three decisions:

  - ``"allow"``    — a known-safe tool (read/search/todo, writes inside the workspace, an MCP tool
                     from an in-repo worker — that egress is already constrained + pinned), or a
                     Bash command not on the dangerous-program deny list. **Exception:** a hosted
                     third-party connector we do not author (today: Apollo) is allowlisted per tool
                     instead, since the vendor — not this repo — decides what its surface exposes.
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

import contextlib
import contextvars
import json
import posixpath
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
        # ── CLI coordination/inspection built-ins ────────────────────────────────────────────
        # These carry no capability of their own: they read output or move local bookkeeping.
        # They are listed because the catch-all for an unrecognised tool is ``escalate``, which the
        # cockpit renders as a deny plus a "🛡 Blocked a tool the brain tried to use" notice. ``Skill``
        # sat in this same blind spot and silently denied every skill run. The image installs the CLI
        # at a PINNED version (Dockerfile ``CLAUDE_CODE_VERSION``) precisely so a rebuild cannot add a
        # new built-in that lands here unreviewed — when bumping that pin, re-check this set.
        "BashOutput",  # reads stdout of a Bash call already classified by this policy
        "Monitor",
        "EnterPlanMode",
        "AskUserQuestion",
        "TaskCreate",  # Task* = local todo/subtask bookkeeping, no external effect
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "TaskStop",
        # Deliberately NOT allowed, and not an oversight:
        #   SlashCommand — expands to an arbitrary command, so it would bypass Bash classification.
        #   KillShell    — process termination; ``kill``/``pkill`` are on the dangerous-program
        #                  floor, so allowing the tool form would route straight around it.
    }
)

#: Any Model Context Protocol tool. Egress through MCP is the sanctioned path (pinned servers,
#: least-privilege DB role, account-pinned publish webhook), so MCP tools are allowed as a class.
_MCP_PREFIX = "mcp__"

# --------------------------------------------------------------------------- #
# Third-party hosted MCP connectors (AP-02) — the class-allow above has a premise
# --------------------------------------------------------------------------- #
# "MCP tools are allowed as a class" is sound *because* every server in ``build_mcp_servers`` is an
# in-repo stdio worker we author: the Saleshandy wrapper ships no send tool at all, so sending is
# **unrepresentable** rather than merely forbidden. That premise fails for a **hosted third-party
# connector** whose tool surface the vendor chooses. Apollo's official hosted MCP is the first such
# connector: alongside the person/company enrichment the ``prospect`` skill wants, it exposes
# ``apollo_emailer_messages_send_now``, ``apollo_emailer_campaigns_approve``, sequence/contact/
# account writes, and domain + email-account **purchase** tools. Under a blanket class-allow those
# are one goal-hijack away from a real send, a real CRM write, or a real charge — exactly the
# structural guarantee the publish gate and the Saleshandy wrapper exist to provide.
#
# So for connector families we do not author, the class-allow is replaced by an explicit
# **allowlist**, keyed on the tool *leaf* name (the part after the final ``__``). Keying on the leaf
# rather than the server is deliberate: a claude.ai-authorized connector's server segment is an
# opaque per-user UUID (``mcp__a84f5f15-…__apollo_people_match``), so a server-keyed rule — or a
# declarative ``disallowed_tools`` entry — cannot match it reliably. The leaf is stable across both
# surfaces, so one rule covers the in-repo worker and the hosted connector alike.
#
# Fail-closed by construction: an ``apollo_*`` tool that is not on this list is denied, so a tool
# Apollo *adds* later is denied by default rather than silently inheriting the class-allow.

#: Read/enrich-only Apollo tools the ``prospect`` skill actually needs. Left column is the in-repo
#: worker (``agent/mcp/apollo``); right column is Apollo's hosted OAuth connector, which names the
#: same capabilities differently. Everything absent here — send, approve, sequence/contact/account
#: writes, task writes, label writes, domain and email-account purchase — is denied.
_APOLLO_READ_TOOLS: frozenset[str] = frozenset(
    {
        # in-repo worker (agent/mcp/apollo/server.py)
        "apollo_usage",
        "apollo_person_search",
        "apollo_person_enrich",
        "apollo_bulk_person_enrich",
        "apollo_company_search",
        "apollo_company_enrich",
        "apollo_job_postings",
        # hosted OAuth connector (mcp.apollo.io) — same capabilities, vendor's names
        "apollo_users_api_profile",
        "apollo_usage_stats_credit_usage_stats",
        "apollo_mixed_people_api_search",
        "apollo_people_match",
        "apollo_people_bulk_match",
        "apollo_mixed_companies_search",
        "apollo_organizations_enrich",
        "apollo_organizations_bulk_enrich",
        "apollo_organizations_job_postings",
    }
)

#: Leaf-name prefix ⇒ the only leaves allowed under it. A tool whose leaf starts with a key here is
#: allowed **only** if it is in that key's set; the blanket MCP class-allow does not apply to it.
_MCP_CONNECTOR_ALLOWLISTS: dict[str, frozenset[str]] = {
    "apollo_": _APOLLO_READ_TOOLS,
}

# --------------------------------------------------------------------------- #
# External-effect leaf denial — publish/schedule/write on ANY connector (§R7)
# --------------------------------------------------------------------------- #
# The prefix-allowlist above solves Apollo because every Apollo leaf is namespaced (``apollo_*``),
# so one prefix key selects the family. **That mechanism cannot express a Buffer rule.** Buffer's
# leaves carry no vendor prefix at all — they are bare verbs (``create_post``, ``edit_post``,
# ``get_account``) — so there is nothing to key a family allowlist on, and the server segment is an
# opaque per-user UUID on the hosted/claude.ai surface (the same reason the Apollo rule is
# leaf-keyed). What is left is a denylist on the *verb*.
#
# Denying the bare verb across **every** connector is deliberate, not collateral damage:
# ``create_post`` on any social connector is a publish, and publishing is not a capability the brain
# holds. The brain emits the exact bytes inside ``⟦GATE:publish⟧`` and Python
# (``agent/publish.py``) makes the call after the operator approves — the destination is pinned
# server-side and is not representable in brain output. A connector that hands the brain a direct
# publish route would dissolve that guarantee, whatever the vendor named it.
#
# **Scheduling is publishing with a delay and does not get a weaker gate** — hence ``create_post``
# is denied even though Buffer "only" queues it.

#: Leaf names that reach outside the system — publish, schedule, link an account, or write to a
#: third-party surface. Checked before every other MCP rule, so a connector cannot re-open one of
#: these by naming it inside an otherwise-allowlisted family.
_EXTERNAL_EFFECT_LEAVES: frozenset[str] = frozenset(
    {
        # ── Buffer: post creation + scheduling, and the arbitrary-GraphQL escape hatches ──────
        "create_post",
        "edit_post",
        "delete_post",
        "create_idea",
        "create_post_template",
        "update_post_template",
        "delete_post_template",
        # ``execute_mutation`` is arbitrary writes. ``execute_query`` is denied too: GraphQL
        # separates query from mutation *by convention*, and whether this server rejects a mutation
        # string passed to the query tool is unverified — and unverifiable without performing a real
        # external write. The domain read tools (get_account / list_channels / list_posts /
        # get_aggregated_post_metrics) cover everything the outcomes loop needs, so the arbitrary
        # surface buys nothing and is the one place behaviour cannot be bounded. Fail closed.
        "execute_mutation",
        "execute_query",
        # ── Higgsfield: direct-to-platform publish + account linking ──────────────────────────
        "tiktok_publish",
        "tiktok_prepare_publish",
        "tiktok_connect",
        "tiktok_reconnect",
        # ── Higgsfield: website publish. Same invariant, found while wiring the two above —
        #    these push a live public artifact, so leaving them on the class-allow while denying
        #    ``create_post`` would be incoherent. No skill references any of them.
        "create_website",
        "deploy_website",
        "publish_website",
        "rename_website",
        # Submits an entry to a public contest — an outward-facing post under the account.
        "participate_in_contest",
        # ── Reap: direct-to-platform publish + scheduling + editing a LIVE post ──────────────
        # These three were live-connected and class-allowed via the line-238 fallthrough until
        # this fix — the same architectural hole this block already closes for Buffer and
        # Higgsfield, never applied to Reap. `update_publisher_post` mutates an already-published
        # post in place — the same class as Buffer's `edit_post` above, which is why it is denied
        # alongside the two obvious verbs rather than treated as a lesser "edit".
        # `list_integrations` / `get_publisher_post` / `list_publisher_posts` stay allowed: they
        # only read.
        "publish_clip",
        "schedule_clips",
        "update_publisher_post",
        # ── Saleshandy: lead/prospect enrollment — a PII egress into a real (if paused)
        # sequence (A11). Denied outright, exactly like the Reap publish verbs above: the
        # pack graph's `sequence` node is instructed never to call these, and the deny here
        # is the actual structural guarantee — an instruction the model could ignore or a
        # future skill regression cannot reach Saleshandy. See agent/email_dispatch.py.
        "add_leads_to_sequence",
        "import_prospects_to_sequence",
        # ── Saleshandy: the DNC write (SC9, 2026-09-21). A suppression list is a compliance
        # record, so a brain-initiated write to it is the same class of action as a publish.
        # Admitted only inside `dnc_context()` — see `_SALESHANDY_DNC_LEAVES`.
        "add_dnc_items",
        "create_dnc_list",
        # The REMOVAL verbs, denied with NO admitting context at all — they are absent from
        # all three context leaf-sets, so nothing can ever open them. The in-repo connector
        # exposes none of these, and "we did not build one" is not a guarantee: a hosted or
        # claude.ai Saleshandy connector that exposes `remove_dnc_items` would otherwise be
        # class-allowed, which is exactly how the three Reap publish verbs were reachable
        # until they were named above. Un-suppressing someone who opted out is the one
        # action in this repo with no undo and no legitimate automated caller.
        "remove_dnc_items",
        "delete_dnc_item",
        "delete_dnc_list",
        "update_dnc_list",
        "clear_dnc_list",
    }
)

#: Reap verbs that publish, schedule, or edit a live post. Denied everywhere EXCEPT inside
#: an approved publish dispatch (``agent/publish.py`` / ``agent/publish_dispatch.py``), so the
#: brain can never initiate them directly but Python can call them after operator approval.
_REAP_PUBLISH_LEAVES: frozenset[str] = frozenset(
    {
        "publish_clip",
        "schedule_clips",
        "update_publisher_post",
    }
)

#: Saleshandy verbs that enroll a lead/prospect (a PII egress). Denied everywhere EXCEPT
#: inside an approved enrollment dispatch (``agent/email_dispatch.py``), so the brain can
#: never initiate them directly but Python can call them after operator approval — the
#: same shape as ``_REAP_PUBLISH_LEAVES``/``publish_context()``, for a second effect kind.
_SALESHANDY_ENROLL_LEAVES: frozenset[str] = frozenset(
    {
        "add_leads_to_sequence",
        "import_prospects_to_sequence",
    }
)

#: Saleshandy verbs that WRITE to the provider's Do Not Contact list. Denied everywhere
#: EXCEPT inside an approved ``dnc_add`` dispatch (``agent/dnc_dispatch.py``) — the third
#: external effect, same shape as the two above.
#:
#: Note what is NOT here and never will be: a REMOVE verb. The direction is one-way by
#: construction, because nothing in this system may un-suppress a person who opted out.
#: `create_dnc_list` is included because a brain that can create a list can create an empty
#: one and add to that instead of the real one — a suppression write that suppresses nobody.
_SALESHANDY_DNC_LEAVES: frozenset[str] = frozenset(
    {
        "add_dnc_items",
        "create_dnc_list",
    }
)

#: Thread/async-safe flag: True only while ``agent/publish.py`` is dispatching an
#: operator-approved publish. Used by ``_classify_mcp`` to allow Reap publish verbs
#: in that narrow window and deny them everywhere else.
_publish_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_publish_context", default=False
)

#: Thread/async-safe flag: True only while ``agent/email_dispatch.py`` is dispatching an
#: operator-approved lead enrollment. Used by ``_classify_mcp`` to allow the Saleshandy
#: enrollment verbs in that narrow window and deny them everywhere else. Mirrors
#: ``_publish_context`` exactly, for the A11 email-enrollment gate.
_email_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_email_context", default=False
)


#: Thread/async-safe flag: True only while ``agent/dnc_dispatch.py`` is dispatching an
#: operator-approved DNC add. Mirrors ``_publish_context``/``_email_context`` exactly.
_dnc_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_dnc_context", default=False
)


def in_publish_context() -> bool:
    """True when running inside an approved publish dispatch."""
    return _publish_context.get()


@contextlib.contextmanager
def publish_context():
    """Set the publish-context flag for the duration of the block.

    Used by ``agent/publish_dispatch.dispatch_approved_publish`` to wrap the actual
    outbound publish call. ContextVars propagate across async/await, so MCP tool calls
    made by a publisher implementation inside this block see the flag.
    """
    token = _publish_context.set(True)
    try:
        yield
    finally:
        _publish_context.reset(token)


def in_email_context() -> bool:
    """True when running inside an approved enrollment dispatch."""
    return _email_context.get()


@contextlib.contextmanager
def email_context():
    """Set the email-context flag for the duration of the block.

    Used by ``agent/email_dispatch.dispatch_approved_enrollment`` to wrap the actual
    Saleshandy enrollment call. ContextVars propagate across async/await, so a call made
    inside this block sees the flag; the brain's own tool calls never run inside it.
    """
    token = _email_context.set(True)
    try:
        yield
    finally:
        _email_context.reset(token)


def in_dnc_context() -> bool:
    """True when running inside an approved DNC-add dispatch."""
    return _dnc_context.get()


@contextlib.contextmanager
def dnc_context():
    """Set the DNC-context flag for the duration of the block.

    Used by ``agent/dnc_dispatch.dispatch_approved_dnc_add`` to wrap the actual provider
    write. ContextVars propagate across async/await, so a call made inside this block sees
    the flag; the brain's own tool calls never run inside it.

    The three contexts are NOT interchangeable: a publish dispatch cannot open a DNC write,
    and a DNC dispatch cannot open an enrollment. Each effect gets its own window, or the
    gate that approved one set of bytes would authorise a different action entirely.
    """
    token = _dnc_context.set(True)
    try:
        yield
    finally:
        _dnc_context.reset(token)


# Note: `reframe` is a leaf on BOTH the Reap and Higgsfield MCP servers. Both class-allow today —
# no live bug — but this rule is leaf-keyed by design (a claude.ai connector's server segment is an
# opaque per-user UUID), so it cannot distinguish the two vendors. If Reap's `reframe` ever needs
# denying, Higgsfield's would die with it under this mechanism alone.


def _classify_mcp(tool_name: str) -> Decision:
    """Allow an MCP tool as a class, unless it publishes externally or belongs to an
    allowlisted connector family.

    Reap publish/schedule/update verbs are denied by default but allowed inside the
    narrow publish context set by ``agent/publish_dispatch.dispatch_approved_publish``
    — that is the only window where Python (not the brain) may call them after operator
    approval. Saleshandy enrollment verbs (A11) are denied by default the same way,
    allowed only inside the narrow email context set by
    ``agent/email_dispatch.dispatch_approved_enrollment``.

    ``tool_name`` is ``mcp__<server>__<leaf>``; ``<server>`` may itself contain ``__``, so the leaf
    is taken from the **last** separator. Precedence:

    1. An external-effect leaf is **denied** outright, on any connector (§R7 publish gate /
       A11 enrollment gate), unless it is a Reap publish verb and we are inside an approved
       publish context, or a Saleshandy enrollment verb and we are inside an approved email
       context.
    2. A leaf matching a family prefix must be on that family's allowlist — else denied (AP-02).
    3. Otherwise the class-allow applies: MCP is the sanctioned egress path.
    """
    from gtm_core.mcp_categories import CATEGORY_RULES, get_category_for_connector, is_write_tool

    server_and_leaf = tool_name[len(_MCP_PREFIX) :]
    if "__" in server_and_leaf:
        connector_name = server_and_leaf.rsplit("__", 1)[0]
        leaf = server_and_leaf.rsplit("__", 1)[-1]
    else:
        connector_name = ""
        leaf = server_and_leaf

    cat = get_category_for_connector(connector_name)
    if cat and cat in CATEGORY_RULES:
        rules = CATEGORY_RULES[cat]
        denied_patterns = rules["denied_patterns"]
        context_name = rules["context"]

        # A DNC write is a write to a COMPLIANCE RECORD. It is denied on every connector,
        # in every category, and is admitted only by its own context — deliberately NOT by
        # the category's, which for the sequencer category is `email_context`. An approved
        # ENROLLMENT must never also authorise a suppression write: the operator approved
        # one set of bytes for one effect, and the three effects are not interchangeable.
        #
        # Checked first inside this branch because the category path returns before the
        # leaf-level `_EXTERNAL_EFFECT_LEAVES` rule below ever runs — the ordering that let
        # `add_dnc_items` through on a categorised connector until SC9.
        if leaf in _SALESHANDY_DNC_LEAVES:
            return "allow" if in_dnc_context() else "deny"

        if denied_patterns:
            is_denied = any(leaf.startswith(pat) or leaf == pat for pat in denied_patterns)
            if is_denied:
                return (
                    "allow"
                    if (
                        (context_name == "email_context" and in_email_context())
                        or (context_name == "publish_context" and in_publish_context())
                        # No `dnc_context` clause: no CATEGORY_RULES entry declares that
                        # context, and a branch that cannot fire reads as coverage it does
                        # not provide. DNC writes are decided above, before this branch.
                    )
                    else "deny"
                )
            return "allow"

        # PRD §4.3 Fail-Closed Default: If a connector is bound to a category
        # but its tools are not yet mapped in the deny declarations, all write/mutation
        # tools from that connector default to denied. Read-only tools default to allowed.
        return "deny" if is_write_tool(leaf) else "allow"

    if leaf in _EXTERNAL_EFFECT_LEAVES:
        return (
            "allow"
            if (
                (leaf in _REAP_PUBLISH_LEAVES and in_publish_context())
                or (leaf in _SALESHANDY_ENROLL_LEAVES and in_email_context())
                or (leaf in _SALESHANDY_DNC_LEAVES and in_dnc_context())
            )
            else "deny"
        )
    for prefix, allowed in _MCP_CONNECTOR_ALLOWLISTS.items():
        if leaf.startswith(prefix):
            return "allow" if leaf in allowed else "deny"
    return "allow"


# --------------------------------------------------------------------------- #
# Secret-path read denial (AIRQ D-04 / OWASP ASI06; self-assessment §6 item 8)
# --------------------------------------------------------------------------- #
# The brain has no reason to read raw secret material: outbound credentials live with the code
# (Doppler → worker env), never in the model's context. Reading a `.env`/key file would only widen
# the blast radius of a goal-hijack (§1). We deny it at every read path — the `Read`/`Grep`/`Glob`
# built-ins AND the shell file-readers (`cat .env`, `xxd id_rsa`, `source .env`, `< .env`).

#: Committed, non-secret templates that must stay readable (they carry no real values).
_SECRET_PATH_EXCEPTIONS: frozenset[str] = frozenset(
    {".env.example", ".env.sample", ".env.template"}
)

#: Path shapes that name secret material. Matched against a posix-normalized path (so
#: ``foo/../.env`` and ``x/.env.example/../.env`` cannot slip past). The ``.env`` family is checked
#: against the exception set first, so committed ``.env.example`` templates stay readable.
_SECRET_PATH_RE = re.compile(
    r"""
    (^|/)\.env(\.[\w.-]+)?$      # .env, .env.local, .env.prod, …
    | \.pem$                      # TLS / private keys
    | \.key$
    | (^|/)id_(rsa|ed25519|dsa|ecdsa)[^/]*$   # SSH private keys
    | (^|/)\.doppler(/|$)         # Doppler local config
    | (^|/)secrets?(/|$)          # secrets/ or secret/ directories
    """,
    re.VERBOSE,
)

#: Shell programs that read file contents to stdout/stderr — path-checked for secret targets.
_FILE_READ_PROGRAMS: frozenset[str] = frozenset(
    {"cat", "head", "tail", "less", "more", "strings", "xxd", "od", "base64", "source", "."}
)


def _is_secret_path(path: str) -> bool:
    """True when ``path`` names secret material we refuse to read.

    Normalizes with posixpath first (collapses ``..``/``.`` and backslashes) so traversal tricks
    like ``foo/../.env`` resolve to the real target before matching. The committed ``.env.example``
    template family is exempted on the normalized basename.
    """
    if not path:
        return False
    norm = posixpath.normpath(path.strip().replace("\\", "/"))
    if posixpath.basename(norm) in _SECRET_PATH_EXCEPTIONS:
        return False
    return _SECRET_PATH_RE.search(norm) is not None


# --------------------------------------------------------------------------- #
# Bash classification
# --------------------------------------------------------------------------- #

#: Programs that are denied outright wherever they appear in a Bash command (ASI05 floor).
#:
#: This set — via ``DANGEROUS_TOOL_DENY_RULES`` → the SDK's ``disallowed_tools`` — is the
#: **complete** declarative floor. ``.claude/settings.json`` carries only a PARTIAL mirror (it
#: omits ``exec``, ``ftp``, ``kill``, ``killall``, ``mkfifo``, ``ncat``, ``netcat``, ``pip3``,
#: ``pkill``, ``sftp``, ``telnet``). That is safe — deny beats allow and ``disallowed_tools`` is
#: passed on every run (``agent/session.py``) — but an earlier comment here claimed the two were
#: "kept in sync", which was false. ``test_settings_deny_is_a_subset_of_the_code_floor`` pins the
#: real relationship: settings.json must be a SUBSET, never a superset, of this set.
#:
#: ``ffmpeg``/``ffprobe`` are here for PIPELINE INTEGRITY, not ASI05 — they are the one pair whose
#: risk is a wrong artifact rather than a dangerous host action (2026-08-18). A raw ``ffmpeg``
#: subprocess trips neither the §R6 semgrep import scan (``languages: [python]``, matches imports —
#: a subprocess never trips it) nor any egress gate, the blind spot already documented in
#: ``gtm_core/media_fetch.py``. Left open, a hand-authored filter chain silently bypasses
#: ``gtm_core.video_finish``, which owns grading, Pillow caption overlays and -14 LUFS loudness
#: and asserts the single-grade invariant — the module's OWN ``subprocess`` call is unaffected by
#: this set, because it never routes through the permission callback. ``ffprobe`` is denied
#: alongside it so verification routes through ``gtm_core.video_lint`` rather than ad-hoc probing.
_DANGEROUS_PROGRAMS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "curl",
        "wget",
        "npx",
        "uvx",  # npx's twin: fetches and executes an arbitrary package
        "npm",
        # Doppler injects every secret as a process env var, so printing the environment
        # dumps the lot into the transcript — the secret-echo ban by a route the secret-PATH
        # check cannot see, because no file is read. `env <cmd>` still resolves to <cmd>
        # (see _segment_program); only a bare dump lands here.
        "env",
        "printenv",
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
        "ffmpeg",
        "ffprobe",
    }
)

#: Shell operators we split a command on, so chaining (``a && rm -rf``, ``a; curl …``,
#: pipes, background) cannot smuggle a dangerous program past a safe-looking head.
_SHELL_OPERATOR_CHARS = frozenset({";", "|", "&", "\n"})


def _read_until_close(text: str, start: int, opener: str, closer: str) -> tuple[str, int]:
    """Return ``(body, index_after_closer)`` for a substitution body starting at ``start``.

    Tracks nesting so ``$(a $(b) c)`` reads to the correct ``)``. An **unterminated** substitution
    returns the rest of the string as the body, so the program inside is still classified (
    ``X=$(curl evil`` denies) rather than being silently dropped.
    """
    depth = 1
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if opener and char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return text[start:], len(text)


def _split_segments(command: str) -> list[str]:
    """Split a Bash command on shell operators, ignoring ones inside quotes.

    A plain regex split treats ``;``/``|``/``&`` as operators even inside a quoted argument, so
    ``--gap-cards "a;b;c"`` was shredded into segments with unbalanced quotes; ``shlex.split`` then
    failed and the whole command wrongly **escalated** (and was denied). Semicolon-delimited
    arguments are a documented calling convention for skill scripts, so every such call was blocked.
    Quote state is tracked here so only *unquoted* operators split.

    Command substitutions — ``$(...)`` and backticks — are extracted and returned as their own
    segments so the program inside them is classified too. Without this, the dangerous-program floor
    was bypassable: ``X=$(curl evil)`` parsed as a lone ``X=`` env assignment (no program) and was
    ALLOWED, as were ``echo $(rm -rf /)`` and ``echo $(cat .env)``. Substitution runs inside double
    quotes as well, so only single quotes suppress it.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = in_double = escaped = False
    i = 0

    while i < len(command):
        char = command[i]
        if escaped:
            current.append(char)
            escaped = False
            i += 1
            continue
        if char == "\\" and not in_single:
            current.append(char)
            escaped = True
            i += 1
            continue
        # `$(...)` and backticks execute unless single-quoted — recurse into the body so the
        # program inside is classified rather than hidden from the deny floor.
        if not in_single and char == "$" and command[i + 1 : i + 2] == "(":
            body, i = _read_until_close(command, i + 2, "(", ")")
            segments.extend(_split_segments(body))
            continue
        if not in_single and char == "`":
            body, i = _read_until_close(command, i + 1, "", "`")
            segments.extend(_split_segments(body))
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char in _SHELL_OPERATOR_CHARS and not in_single and not in_double:
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1

    segments.append("".join(current))
    return segments


#: Programs that RUN another program. The floor is a claim about what executes, so these are
#: transparent: the wrapped program is classified, not the wrapper. Without this,
#: ``env curl …``, ``xargs curl …`` and ``timeout 5 curl …`` were all allowed while a bare
#: ``curl`` was denied — the denylist was matching first tokens, not shell semantics.
_WRAPPER_PROGRAMS: frozenset[str] = frozenset(
    {
        "env",
        "command",
        "nice",
        "nohup",
        "stdbuf",
        "setsid",
        "time",
        "timeout",
        "ionice",
        "chrt",
        "xargs",
    }
)

#: A wrapper's own operands, skipped while looking for the wrapped program: flags
#: (``-n1``, ``-I{}``), a bare duration/niceness (``timeout 5``, ``nice -n 10``), and the
#: placeholder/terminator tokens ``find -exec`` uses.
_WRAPPER_OPERAND_RE = re.compile(r"^(-.*|\d+[smhd]?|\{\}|;|\\;)$")

_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def _segment_program(segment: str) -> str | None:
    """Return the program a command segment actually RUNS.

    Skips ``FOO=bar`` env prefixes, and sees through transparent wrappers
    (:data:`_WRAPPER_PROGRAMS`) and ``find -exec`` to the program they execute — a denylist
    that stops at the first token is not a floor, it is a spelling test.

    Returns ``None`` for an empty/unparseable segment (treated as benign by the caller).
    """
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        return ""  # unbalanced quotes etc. — unparseable ⇒ caller escalates

    i = 0
    saw_wrapper = False
    while i < len(tokens):
        tok = tokens[i]
        # Skip leading environment assignments (`KEY=value cmd ...`).
        if _ASSIGNMENT_RE.fullmatch(tok):
            i += 1
            continue
        # Strip a path prefix: /usr/bin/curl -> curl.
        prog = tok.rsplit("/", 1)[-1]

        if prog in _WRAPPER_PROGRAMS:
            saw_wrapper = True
            i += 1
            while i < len(tokens) and (
                _WRAPPER_OPERAND_RE.match(tokens[i]) or _ASSIGNMENT_RE.fullmatch(tokens[i])
            ):
                i += 1
            continue

        if prog in ("find", "gfind"):
            # `find … -exec <prog> …` runs <prog> once per hit. Classify that, not `find`.
            for j in range(i + 1, len(tokens) - 1):
                if tokens[j] in ("-exec", "-execdir", "-ok", "-okdir"):
                    return tokens[j + 1].rsplit("/", 1)[-1]
            return prog

        return prog

    # Nothing but wrappers and their operands. A bare `env` prints the whole environment —
    # and Doppler injects every secret as a process env var, so that is a secret dump by a
    # route the secret-PATH check cannot see (no file is read). Report it as `env` so the
    # denylist decides.
    return "env" if saw_wrapper else None


def _segment_tokens(segment: str) -> list[str]:
    """Best-effort shlex tokenization of a segment; ``[]`` on unbalanced quotes."""
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return []


def _segment_reads_secret(segment: str) -> bool:
    """True when a file-reading segment targets a secret path in any of its arguments.

    Skips the program token and flag tokens (``-n5``); checks every remaining operand with
    ``_is_secret_path`` so ``cat .env``, ``head -n5 server.pem``, ``source .env`` all trip.
    """
    tokens = _segment_tokens(segment)
    saw_program = False
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
            continue  # env-assignment prefix
        if not saw_program:
            saw_program = True  # first non-env token is the program name
            continue
        if tok.startswith("-"):
            continue  # a flag, not a path operand
        if _is_secret_path(tok):
            return True
    return False


def _segment_redirects_from_secret(segment: str) -> bool:
    """True when the segment reads a secret file via an input redirect (``cmd < .env``)."""
    for match in re.finditer(r"<\s*([^\s<>|&]+)", segment):
        if _is_secret_path(match.group(1)):
            return True
    return False


def _is_uv_run_arbitrary_python(segment: str) -> bool:
    """Return True if segment is `uv run ... python -c/-` arbitrary execution."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    rest = [t for t in tokens[1:] if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t)]
    if len(rest) < 2 or rest[0] != "run":
        return False
    idx = 1
    opt_takes_arg = frozenset(
        {
            "--with",
            "--extra",
            "--package",
            "--python",
            "-p",
            "--directory",
            "--project",
            "--env-file",
            "--group",
        }
    )
    while idx < len(rest) and rest[idx].startswith("-"):
        idx += 2 if rest[idx] in opt_takes_arg else 1
    if idx < len(rest) and (
        rest[idx] in ("python", "python3")
        or rest[idx].endswith("/python")
        or rest[idx].endswith("/python3")
    ):
        py_args = " ".join(rest[idx + 1 :])
        return bool(re.match(r"^(-c(\s|$|[\"'\w])|\s*-c|-\s|-$)", py_args))
    return False


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
    for raw_segment in _split_segments(command):
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
        if program in _FILE_READ_PROGRAMS and _segment_reads_secret(segment):
            # `cat .env`, `xxd id_rsa`, `source .env`, `base64 secrets/tok` … — reading raw secret
            # material is denied even though the program itself is benign (self-assessment §6.8).
            return "deny"
        if _segment_redirects_from_secret(segment):
            # `foo < .env` — an input redirect from a secret file, regardless of program.
            return "deny"
        if program in ("python", "python3"):
            # Block raw arbitrary code exec forms:
            #   python -c '...'   — explicit inline flag
            #   python - <<'HD'   — stdin heredoc (semantically identical to -c; the `-` arg means
            #                       "read from stdin", which is whatever the shell pipes in)
            # Script paths, module calls (-m), and all other invocation forms are allowed.
            args = segment.split(None, 1)[1].strip() if " " in segment else ""
            # `-c` binds its argument with or without whitespace (`python -c'x'`). This
            # branch required a space while the `uv run python` branch above did not, so the
            # cheaper spelling walked through the narrower check.
            seg_decision = "deny" if re.match(r"^(-c(\s|$|[\"\'\w])|-\s|-$)", args) else "allow"
        elif program == "uv":
            # Block `uv run python -c '...'` and `uv run python -`; everything else (uv run
            # script.py, uv add, uv sync, etc.) is allowed — the dangerous-program check on
            # individual segments already catches `uv run curl` etc.
            seg_decision = "deny" if _is_uv_run_arbitrary_python(segment) else "allow"
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


def classify_tool(
    tool_name: str,
    tool_input: dict | None,
    *,
    allowed_skills: frozenset[str] | None = None,
) -> Decision:
    """Return the policy decision for one tool call. Pure; safe to unit-test directly.

    ``allowed_skills`` is the caller's skill scope (SECURITY-SELF-ASSESSMENT
    residuals #11/#12): when set, a ``Skill`` invocation is allowed only for a
    skill in the set and DENIED otherwise — deactivating a pack in ``packs.toml``
    now blocks its skills at the tool layer, not just in the listing, and a skill
    priced above the workspace's entitlement is blocked the same way. ``None``
    preserves the pre-reachability behaviour exactly (``Skill`` escalates), so
    unscoped VPS/cockpit paths are byte-identical.
    """
    if not tool_name:
        return "escalate"
    if tool_name == "Skill":
        # Unscoped path (VPS cron + Telegram cockpit): every skill the plugin ships is reachable.
        # This MUST be an explicit allow. ``Skill`` is not in ``_ALWAYS_ALLOW``, so it used to fall
        # through to the catch-all ``escalate`` — which the cockpit turns into a deny plus a
        # "🛡 Blocked a tool the brain tried to use" notice. Since the CLI invokes every packaged
        # skill through this tool, that denied EVERY skill run (account-dossier, prospect,
        # solution-design, …) on the cockpit path; the brain's usual recovery was to Read the
        # SKILL.md and follow it by hand, which is why the failure surfaced as a blocked *Bash*
        # step rather than a blocked skill. Skills are in-repo, de-branded, and reviewed — they are
        # the product surface, not an escalation vector; the tools a skill then calls are still
        # classified individually by this same policy.
        if allowed_skills is None:
            return "allow"
        requested = ""
        if isinstance(tool_input, dict):
            requested = str(tool_input.get("skill") or tool_input.get("name") or "")
        # Plugin-scoped invocations arrive as "plugin:skill" — reachability is keyed
        # on the bare skill name (the packs' vocabulary).
        bare = requested.rsplit(":", 1)[-1]
        return "allow" if bare and bare in allowed_skills else "deny"
    if tool_name.startswith(_MCP_PREFIX):
        return _classify_mcp(tool_name)
    # Deny reads of secret material through the built-in read tools before the always-allow pass
    # (self-assessment §6.8). A `Read`/`Grep`/`Glob`/`NotebookRead` whose target is a secret path is
    # blocked; the exception set keeps committed `.env.example` templates readable.
    if tool_name in ("Read", "Grep", "Glob", "NotebookRead"):
        if isinstance(tool_input, dict):
            target = tool_input.get("file_path") or tool_input.get("path") or ""
            if isinstance(target, str) and _is_secret_path(target):
                return "deny"
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
    if decision == "deny" and tool_name.startswith(_MCP_PREFIX):
        leaf = tool_name[len(_MCP_PREFIX) :].rsplit("__", 1)[-1]
        if leaf in _EXTERNAL_EFFECT_LEAVES:
            return (
                f"DENIED (permanent): '{tool_name}' publishes, schedules, or links an account on an "
                "external platform. Publishing is not a capability you hold — and scheduling is "
                "publishing with a delay, so queuing a post is the same denial. This is by design, "
                "not a misconfiguration.\n\n"
                "The approved route: emit the EXACT post content inside a ⟦GATE:publish⟧ block and "
                "stop. The operator approves those exact bytes, then Python makes the call to a "
                "destination pinned server-side. You never name the destination and never make the "
                "call. Do not retry, do not look for another connector, and do not try to reach the "
                "platform through a query/mutation tool. Reading performance data is allowed — the "
                "read tools (account, channels, posts, aggregated metrics) are available."
            )
        return (
            f"DENIED (permanent): '{tool_name}' is a write/send/purchase tool on a third-party "
            "connector. This engine never sends, enrolls, writes to a CRM, or buys anything on "
            "your behalf — outreach is staged and a human flips it live in the vendor's own UI, "
            "and publishing goes through the ⟦GATE:publish⟧ operator approval. Only Apollo's "
            "read/enrich tools (person + company search, enrichment, job postings, usage) are "
            "available. Do not retry or look for another route — use the read tool you need, or "
            "tell the operator this step needs to be done by hand."
        )
    if decision == "deny" and tool_name == "Skill":
        return (
            "DENIED (permanent): that skill is not reachable in this run — it is outside this "
            "workspace's active packs, or above the entitlement its plan carries. Do not retry "
            "it or route around it via Read/Bash — finish the work with the skills that ARE "
            "available, or report the gap."
        )
    if decision == "deny":
        return (
            f"DENIED (permanent): '{tool_name}' uses a program on the hard-deny floor "
            "(npm, npx, curl, wget, pip, rm, sudo, ssh, …), is `python -c '…'` (raw code exec), or "
            "reads secret material (.env / *.pem / *.key / id_rsa* / secrets/ — the brain never "
            "needs raw credentials; they live with the code, not with you). This will NEVER be "
            "allowed and rephrasing won't change it — do not retry. If a skill told you to run "
            "npm/npx/node to render a deck or carousel, use the MCP renderer instead "
            "(mcp__deck__export_deck), not a shell build. If you were reaching for ffmpeg/ffprobe: "
            "every pixel and loudness operation belongs to `uv run python -m gtm_core.video_finish "
            "run` (grade, Pillow caption overlays, -14 LUFS) and verification to `uv run python -m "
            "gtm_core.video_lint` — never a hand-authored filter chain, drawtext, or a second grade "
            "pass. A 'make a video' request starts at the video-router skill, which picks the "
            "creator-pack lane (demo-clips for a screen recording); do not assemble one by hand. "
            "Otherwise tell the operator this step is blocked and continue with what you can do."
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
# operator ask for the cockpit approve-and-resume path (P1-6):
# async (tool_name, tool_input) -> True (approved) | False (declined) | None (timeout).
AskOperator = Callable[..., Awaitable[bool | None]]


def make_headless_can_use_tool(
    on_deny: Callable[[str, dict, Decision], None] | None = None,
    *,
    allowed_skills: frozenset[str] | None = None,
) -> CanUseTool:
    """Callback for the unattended/cron path: allow known-safe, otherwise **deny** (fail closed).

    There is no human to ask, so ``escalate`` collapses to ``deny`` — but it returns a decision
    immediately, so the run never hangs (the old reason ``bypassPermissions`` existed). ``on_deny``
    is an optional sink for logging which calls were blocked. A per-callback strike counter swaps
    the deny text for a hard "stop" message after ``STRIKE_LIMIT`` retries of the same call, so a
    denied tool cannot loop and burn budget. ``allowed_skills`` scopes the
    ``Skill`` tool to the caller's reachable set — active packs ∩ entitlement on backend pack
    runs, entitlement alone on backend prompt runs — see :func:`classify_tool`.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    attempts: dict[tuple[str, str], int] = {}
    totals = {"denials": 0}  # session-wide denial count (all commands) — global loop guard

    async def _cb(tool_name: str, tool_input: dict, context: object) -> object:
        decision = classify_tool(tool_name, tool_input, allowed_skills=allowed_skills)
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


def make_cockpit_can_use_tool(
    notify: BlockedToolNotifier,
    *,
    on_deny: Callable[[str, dict, Decision], None] | None = None,
    ask: AskOperator | None = None,
) -> CanUseTool:
    """Callback for the Telegram path: allow known-safe, deny dangerous, **notify on escalate**.

    An unrecognised (escalated) tool is denied AND surfaced to the operator, so a blocked call is
    visible rather than silent. A per-callback strike counter does two things: (1) after
    ``STRIKE_LIMIT`` retries of the same call the deny text becomes a hard "stop" instruction so
    the brain stops looping; (2) the operator is notified only on the **first** escalation and
    **once** at the strike limit — never on every retry (that was the 85-message spam).

    ``on_deny`` is the observability sink (→ ``denials.jsonl`` via ``agent.denial_log``); it fires
    on every denial and never changes the decision.

    ``ask`` (P1-6 approve-and-resume) restores the interactive "Ask" tier for the **escalate class
    only**: on the first attempt of a distinct escalated call the operator is asked inline —
    ``True`` allows it (remembered for identical calls this session), ``False``/``None``
    (decline/timeout) falls through to the normal deny path. The dangerous-program floor and
    secret reads are never askable, and asking stops once the global loop guard has tripped.
    ``ask=None`` (the default, and whenever ``APPROVE_RESUME_ENABLED`` is off) preserves the
    prior deny+notify behaviour exactly.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    attempts: dict[tuple[str, str], int] = {}
    totals = {"denials": 0, "global_notified": 0}  # session-wide denial count + notify-once flag
    approved_keys: set[tuple[str, str]] = set()  # operator-approved calls (this session)

    async def _cb(tool_name: str, tool_input: dict, context: object) -> object:
        decision = classify_tool(tool_name, tool_input)
        if decision == "allow":
            return PermissionResultAllow()
        key = _attempt_key(tool_name, tool_input)
        if key in approved_keys:
            # The operator already approved this exact call this session — a repeat
            # (e.g. a skill re-running an approved step) does not re-ask or re-count.
            return PermissionResultAllow()
        count = attempts[key] = attempts.get(key, 0) + 1
        # Approve-and-resume: only escalate is askable, only on the first attempt of a
        # distinct call, and never after the global loop guard has tripped.
        asked = False
        if (
            ask is not None
            and decision == "escalate"
            and count == 1
            and totals["denials"] < GLOBAL_STRIKE_LIMIT
        ):
            asked = True
            try:
                approved = await ask(tool_name, tool_input or {})
            except Exception:  # noqa: BLE001 — a broken ask path must fail closed, not crash
                approved = None
            if approved is True:
                approved_keys.add(key)
                attempts[key] = 0  # a granted ask is not a strike
                return PermissionResultAllow()
        totals["denials"] += 1
        if on_deny is not None:
            try:
                on_deny(tool_name, tool_input or {}, decision)
            except Exception:  # noqa: BLE001 — observability must never change the decision
                pass  # nosec B110 — intentional best-effort swallow
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
            # An ask WAS this call's operator-facing notice — don't double-message.
            notify_now = decision == "escalate" and count in (1, STRIKE_LIMIT) and not asked
            if hit_global:
                totals["global_notified"] = 1
                notify_now = True
        if notify_now:
            try:
                await notify(tool_name, tool_input or {}, hit_global or count >= STRIKE_LIMIT)
            except Exception:  # noqa: BLE001 — a failed notice must not change the (deny) decision
                pass  # nosec B110 — swallow-and-continue is intentional here, not a missed error
        # Global cap first: a rephrasing loop (many distinct blocked commands) is bounded here,
        # since the per-key counter alone resets on every new wording.
        if hit_global:
            message = global_hard_stop_message(totals["denials"])
        elif count >= STRIKE_LIMIT:
            message = hard_stop_message(tool_name, count)
        elif asked:
            message = (
                f"DENIED: the operator was asked to approve '{tool_name}' and did not "
                "(declined, or no response before the timeout). Do not retry it — continue "
                "with an approved tool, or report this step as blocked and move on."
            )
        else:
            message = deny_message(tool_name, decision)
        return PermissionResultDeny(message=message)

    return _cb
