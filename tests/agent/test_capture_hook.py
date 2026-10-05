"""A capture run files each scraped page itself, from the raw tool result, and meters it.

The brain is never the one that stores a page: a transcribed copy could differ from what the
provider returned, and a headless host has no editor hook to do it (the 2026-09-30 live probe
found the page stored by the brain through a shell command, with no cost row).
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("claude_agent_sdk")

from agent import (
    capture_hook,  # noqa: E402
    session,  # noqa: E402
)
from agent import pipeline_executor as pe  # noqa: E402
from agent.packs import make_executor_from_pack  # noqa: E402
from gtm_core import capture_manifest as cm  # noqa: E402
from gtm_core.packs.loader import PackGraph, PackNode  # noqa: E402
from gtm_core.signal_sources import get_captures, url_norm  # noqa: E402

PROFILE = "acme"
URLS = ["https://registry.example.test/members", "https://lists.example.test/banks"]
RUN = "cap-1"


@pytest.fixture(autouse=True)
def _switch_open(monkeypatch):
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")


def _write_manifest(cfg):
    return cm.write_manifest(
        profile=PROFILE,
        run_id=RUN,
        content_root=cfg.content_root,
        urls=URLS,
        cap=2,
        max_age_ms=86_400_000,
    )


SCRAPE = "mcp__plugin_gtm-engine_firecrawl__firecrawl_scrape"
EDITOR_SCRAPE = "mcp__firecrawl__firecrawl_scrape"
PAGE = "# Members\n\n- Alpha Holdings\n- Beta Partners\n"


def _event(tool=SCRAPE, url=URLS[0], body=None):
    body = {"markdown": PAGE, "metadata": {"statusCode": 200}} if body is None else body
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"url": url},
        # The SDK hands a headless MCP result over as a list of content blocks.
        "tool_response": [{"type": "text", "text": json.dumps(body)}],
    }


def _run(hook, event):
    return asyncio.run(hook(event, "tu1", {}))


def _cost_rows(cfg):
    p = cfg.content_root / PROFILE / "costs.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_scraped_page_is_filed_under_the_runs_profile_and_metered(cfg):
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    assert _run(hook, _event()) == {}
    caps = get_captures(URLS[0], sources_dir=cfg.content_root / PROFILE / "sources")
    assert [c.text for c in caps] == [PAGE.strip()]
    rows = _cost_rows(cfg)
    assert (
        len(rows) == 1 and rows[0]["op"] == "capture" and rows[0]["url_norm"] == url_norm(URLS[0])
    )


def test_the_editor_form_of_the_tool_name_is_filed_too(cfg):
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    _run(hook, _event(tool=EDITOR_SCRAPE))
    assert len(_cost_rows(cfg)) == 1


def test_a_tool_that_is_not_the_scrape_is_ignored(cfg):
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    _run(hook, _event(tool="mcp__firecrawl__firecrawl_search"))
    _run(hook, _event(tool="Bash"))
    assert _cost_rows(cfg) == []
    assert not (cfg.content_root / PROFILE / "sources" / "index.jsonl").exists()


def test_an_error_page_is_not_filed(cfg):
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    _run(hook, _event(body={"markdown": "Forbidden", "metadata": {"statusCode": 403}}))
    assert _cost_rows(cfg) == []


def test_a_malformed_event_never_raises(cfg):
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    for bad in (
        {},
        {"tool_name": SCRAPE},
        {"tool_name": SCRAPE, "tool_input": "x"},
        _event(body=[]),
    ):
        assert _run(hook, bad) == {}


def test_a_failed_cost_row_keeps_the_capture(cfg, monkeypatch):
    def boom(**_kw):
        raise OSError("ledger locked")

    monkeypatch.setattr("gtm_core.capture_cost.record_capture_cost", boom)
    hook = capture_hook.make_capture_hook(cfg, PROFILE)
    _run(hook, _event())
    assert get_captures(URLS[0], sources_dir=cfg.content_root / PROFILE / "sources")


def test_options_for_a_gated_run_carry_the_hook_and_others_do_not(cfg):
    _write_manifest(cfg)
    gate = cm.CaptureGate(cm.load_manifest(cm.manifest_path_for(PROFILE, RUN, cfg.content_root)))
    gated = session.build_agent_options(cfg, PROFILE, capture_gate=gate)
    assert [m.matcher for m in gated.hooks["PostToolUse"]] == [capture_hook.SCRAPE_MATCHER]
    import re

    assert all(re.fullmatch(capture_hook.SCRAPE_MATCHER, t) for t in (SCRAPE, EDITOR_SCRAPE))
    plain = session.build_agent_options(cfg, PROFILE)
    assert not plain.hooks


def test_the_brain_is_told_the_manifest_run_id(cfg, monkeypatch):
    _write_manifest(cfg)
    prompts: list[str] = []

    async def _stream(options, prompt):
        prompts.append(prompt)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(pe, "build_agent_options", lambda *_a, **_k: object())
    monkeypatch.setattr(pe, "stream_brain_messages", _stream)
    graph = PackGraph(
        pack="p",
        variant="v",
        nodes=(PackNode(id="capture", prompt="step"),),
        internal=True,
        egress_scope="capture_manifest",
    )
    ex = make_executor_from_pack(cfg, PROFILE, graph, run_inputs={"manifest_run_id": RUN})
    asyncio.run(ex("capture", {"run_id": "r-other"}))
    assert f"Capture manifest run id: {RUN}" in prompts[0]
    # The gate lets the brain Read exactly one file, so the prompt names its absolute path.
    from gtm_core import capture_manifest

    assert str(capture_manifest.manifest_path_for(PROFILE, RUN, cfg.content_root)) in prompts[0]
