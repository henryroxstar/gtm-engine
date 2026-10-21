"""The onboarding extraction call reads untrusted text, so its model gets no tools.

On 2026-09-16 a one-shot query built with the SDK's default options ran Bash and Read inside
the staging API container and printed a planted file from ``data/workspaces``, the directory
that holds every tenant's tree. ``agent.onboard.extract`` built exactly those options, for a
prompt that carries a crawled page or pasted text (§R5). These tests pin what the call is
given now, down to the CLI command line the SDK builds from it, and that no other code drives
the SDK directly.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib
import json
from pathlib import Path

import pytest

from agent import permissions

# The package re-exports the function ``extract``, which shadows the submodule on attribute access.
extract_mod = importlib.import_module("agent.onboard.extract")

REPO = Path(__file__).resolve().parents[2]

# Schema-valid on purpose: extract()/extract_product() validate against
# schemas/profile-draft.schema.json (issue #267), so a skeleton draft here would fail these
# tests on the fixture's shape rather than on the sandbox options they exist to pin.
_DRAFT = {
    "source": {"type": "text", "value": "We rebuild deck winches."},
    "confidence": "low",
    "company": {
        "name": "Quillfield Winch Works",
        "slug": "quillfield-winch-works",
        "brand_name": "Quillfield",
        "description": "We rebuild deck winches for coastal fleets.",
        "markets": ["Coastal"],
        "social_handle": "",
    },
    "voice": {
        "tone": "Workshop-plain.",
        "principles": ["Name the part.", "Give the tolerance.", "No adjectives."],
        "ban_list": [],
        "examples": [],
    },
    "icp": {
        "personas": [
            {
                "title": "Fleet Engineer",
                "pain_points": ["Winch downtime"],
                "goals": ["Fewer hauls lost"],
            }
        ],
        "verticals": [],
        "company_size": "",
    },
    "competitors": [],
    "pillars": ["Deck machinery", "Preventive maintenance"],
    "products": [
        {
            "slug": "capstan",
            "name": "Capstan",
            "description": "A rebuilt hydraulic capstan winch.",
            "capabilities": [],
            "use_cases": ["Rebuild a seized capstan"],
            "references": [],
        }
    ],
    "brand": {"palette": ["#000000"]},
    "gaps": [],
}


@pytest.fixture
def captured(monkeypatch):
    """Record the options of every brain turn, and whether its cwd existed and was empty."""
    calls: list[dict] = []

    async def _stream(options, prompt):
        from claude_agent_sdk import AssistantMessage, TextBlock

        cwd = Path(options.cwd)
        calls.append({"options": options, "cwd_empty": cwd.is_dir() and not any(cwd.iterdir())})
        reply = (
            json.dumps(_DRAFT) if "ProfileDraft" in prompt else json.dumps(_DRAFT["products"][0])
        )
        yield AssistantMessage(content=[TextBlock(text=reply)], model="test")

    monkeypatch.setattr("agent.session.stream_brain_messages", _stream)
    return calls


def _assert_locked_down(call: dict, cfg) -> None:
    options = call["options"]
    assert options.tools == []
    assert not options.mcp_servers
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert options.permission_mode == "default"
    assert options.max_turns == 1
    assert options.extra_args == {"no-session-persistence": None}
    assert set(permissions.DANGEROUS_TOOL_DENY_RULES) <= set(options.disallowed_tools)
    assert call["cwd_empty"]
    cwd = Path(options.cwd).resolve()
    for root in (cfg.repo_root, cfg.content_root, cfg.profiles_root):
        assert not cwd.is_relative_to(Path(root).resolve())
    assert not cwd.exists(), "the extraction's cwd must be removed after the call"


def _decision(callback, tool_name: str, tool_input: dict):
    return asyncio.run(callback(tool_name, tool_input, None))


def test_extract_gets_no_tools_and_an_empty_cwd(cfg, captured):
    asyncio.run(extract_mod.extract("Quillfield Winch Works\nWe build capstans.", cfg))

    assert len(captured) == 1
    _assert_locked_down(captured[0], cfg)


def test_product_re_extract_gets_the_same_options(cfg, captured):
    asyncio.run(extract_mod.extract_product("capstan", "It now logs hauls.", _DRAFT, cfg))

    assert len(captured) == 1
    _assert_locked_down(captured[0], cfg)


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("Read", {"file_path": "data/workspaces/other/notes.md"}),
        ("Bash", {"command": "ls"}),
        ("Glob", {"pattern": "**/*"}),
        ("WebFetch", {"url": "https://example.com"}),
        ("mcp__any__tool", {}),
    ],
)
def test_the_callback_denies_every_tool(cfg, tool_name, tool_input):
    from claude_agent_sdk import PermissionResultDeny

    callback = extract_mod._extraction_options(cfg, "/nonexistent").can_use_tool

    decision = _decision(callback, tool_name, tool_input)

    assert isinstance(decision, PermissionResultDeny)
    assert decision.interrupt is True


def test_the_run_path_policy_is_not_a_substitute():
    """Why a dedicated deny-all callback: the run path's policy allows file tools. (It does not
    confine their paths either — SECURITY-SELF-ASSESSMENT §6 item 20 — so this deliberately
    asserts only a benign read, not what a confinement fix will change.)"""
    assert permissions.classify_tool("Read", {"file_path": "README.md"}) == "allow"


def test_the_cli_is_started_with_no_tools_no_settings_and_no_transcript(cfg):
    """The options object is not what runs; the command line built from it is. A later SDK that
    dropped an empty list as falsy would restore the default toolset with every other test here
    still green."""
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    options = dataclasses.replace(
        extract_mod._extraction_options(cfg, "/nonexistent"), cli_path="/nonexistent/claude"
    )
    cmd = SubprocessCLITransport(prompt="", options=options)._build_command()

    def value_of(flag: str) -> str:
        return cmd[cmd.index(flag) + 1]

    assert value_of("--tools") == ""
    assert "--setting-sources=" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    assert "--mcp-config" not in cmd
    assert value_of("--max-turns") == "1"
    assert value_of("--permission-mode") == "default"
    assert "Bash(curl:*)" in value_of("--disallowedTools").split(",")


# ── nothing else drives the SDK directly ─────────────────────────────────────

#: The SDK names that start a model session. ``query`` and ``ClaudeSDKClient`` build default
#: options themselves when handed none, so they are as dangerous as a bare options object.
_SESSION_NAMES = frozenset({"ClaudeAgentOptions", "ClaudeSDKClient", "query"})
#: Where they may appear: the run path's builder and one-shot driver, and the tool-less
#: extraction options above.
_SDK_DRIVERS = {"agent/session.py", "agent/onboard/extract.py"}
_SCANNED = ("agent", "backend", "gtm_core", "cockpit", "mcp_server", "plugin", "scripts")


def _sdk_session_uses(source: str) -> set[str]:
    """Session-starting SDK names this source imports or reaches through the module, by their
    ORIGINAL name, so an ``as`` alias cannot hide one."""
    found: set[str] = set()
    module_aliases: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("claude_agent_sdk"):
            found |= {a.name for a in node.names if a.name in _SESSION_NAMES}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "claude_agent_sdk" or alias.name.startswith("claude_agent_sdk."):
                    module_aliases.add((alias.asname or alias.name).split(".")[0])
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _SESSION_NAMES
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
        ):
            found.add(node.attr)
    return found


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from claude_agent_sdk import ClaudeAgentOptions\nClaudeAgentOptions()\n",
            {"ClaudeAgentOptions"},
        ),
        (
            "from claude_agent_sdk import ClaudeAgentOptions as Opts\nOpts()\n",
            {"ClaudeAgentOptions"},
        ),
        ("from claude_agent_sdk import query as ask\n", {"query"}),
        ("from claude_agent_sdk.client import ClaudeSDKClient\n", {"ClaudeSDKClient"}),
        ("import claude_agent_sdk\nclaude_agent_sdk.query(prompt='x')\n", {"query"}),
        ("import claude_agent_sdk as sdk\nsdk.ClaudeSDKClient()\n", {"ClaudeSDKClient"}),
        ("from claude_agent_sdk import AssistantMessage, TextBlock\n", set()),
        ("import other as claude_agent_sdk_like\nclaude_agent_sdk_like.query()\n", set()),
        ("query = None\nClaudeAgentOptions = dict\nClaudeAgentOptions()\n", set()),
    ],
)
def test_the_scan_discriminates(source, expected):
    assert _sdk_session_uses(source) == expected


def test_only_the_known_drivers_start_an_sdk_session():
    offenders = {}
    for top in _SCANNED:
        for path in sorted((REPO / top).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel in _SDK_DRIVERS:
                continue
            uses = _sdk_session_uses(path.read_text(encoding="utf-8"))
            if uses:
                offenders[rel] = sorted(uses)
    assert offenders == {}


def test_the_known_drivers_are_still_found():
    """Positive control on the real tree: an allowlisted path that stopped matching would mean
    the scan went blind, not that the code got safer."""
    for rel in _SDK_DRIVERS:
        assert _sdk_session_uses((REPO / rel).read_text(encoding="utf-8")), rel
