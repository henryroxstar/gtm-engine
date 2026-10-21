"""The outreach log's READ side imports no network, no writer, and names no sending tool.

The five modules that assemble the log hold no state and change nothing: they read files the
system already keeps. This pins that by reading their source (§R6, and the PRD's
"unrepresentability"): no network or subprocess import, no import of anything that writes a
ledger, a list or the figures, no file-writing call, and no provider tool name. The one writer
(the view module that replaces the log's own three files) is deliberately NOT in this set.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "gtm_core"
READ_SIDE = (
    "outreach_strict.py",
    "outreach_people.py",
    "outreach_model.py",
    "outreach_findings.py",
    "outreach_touches.py",
)
FORBIDDEN_ROOTS = {
    "requests", "httpx", "urllib", "urllib3", "socket", "http", "aiohttp", "subprocess",
    "smtplib", "agent", "backend",
}  # fmt: skip
FORBIDDEN_LOCAL = {
    "ledgers", "fsio", "sequencer_snapshot", "prospects_lock", "sequencer_gone", "dnc_dispatch",
    "email_dispatch", "outreach_view",
}  # fmt: skip
WRITE_CALLS = {
    "write_text",
    "write_bytes",
    "unlink",
    "rename",
    "replace",
    "mkdir",
    "rmdir",
    "touch",
}
PROVIDER_TOOLS = (
    "add_dnc_items",
    "add_leads_to_sequence",
    "import_prospects",
    "update_sequence_status",
)


def _tree(name: str) -> ast.AST:
    return ast.parse((CORE / name).read_text(encoding="utf-8"))


def test_no_network_subprocess_or_writer_imports():
    for name in READ_SIDE:
        for node in ast.walk(_tree(name)):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
                assert not set(mods) & FORBIDDEN_ROOTS, (name, mods)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if node.level == 0:
                    assert root not in FORBIDDEN_ROOTS, (name, node.module)
                else:
                    assert (node.module or "").split(".")[0] not in FORBIDDEN_LOCAL, (
                        name,
                        node.module,
                    )
                    assert not {a.name for a in node.names} & FORBIDDEN_LOCAL, (name, node.names)


def test_no_file_writing_calls_and_no_write_mode_open():
    for name in READ_SIDE:
        for node in ast.walk(_tree(name)):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    assert fn.attr not in WRITE_CALLS, (name, fn.attr)
                is_open = (isinstance(fn, ast.Name) and fn.id == "open") or (
                    isinstance(fn, ast.Attribute) and fn.attr == "open"
                )
                if is_open:
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    modes += [
                        k.value.value
                        for k in node.keywords
                        if k.arg == "mode" and isinstance(k.value, ast.Constant)
                    ]
                    assert not any(set("wax+") & set(str(m)) for m in modes), (name, modes)


def test_no_provider_tool_name_appears():
    for name in READ_SIDE:
        text = (CORE / name).read_text(encoding="utf-8")
        for tool in PROVIDER_TOOLS:
            assert tool not in text, (name, tool)
