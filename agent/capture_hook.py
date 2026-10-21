"""The capture run's own PostToolUse hook: file each scraped page and meter it, from the raw result.

The editor hook (``.claude/hooks/firecrawl_capture.py``) does the same job for an interactive
session. It is registered in the project's ``.claude/settings.json``, so a headless run that loads
the project settings can have BOTH hooks fire on one scrape; the capture index and the cost ledger
each drop the second copy of the same page within two minutes (``test_capture_dedupe.py``). A run
cannot rely on the editor hook being there, though: on the 2026-09-30 live probe none ran, the brain
stored the page itself through a shell command, and no cost row was written. Here the **code**
stores what the tool returned and logs what it cost, so a transcribed copy can never stand in for
the page and the §R2 cap sees every capture.

Registered by :func:`agent.session.build_agent_options` only for a run carrying a capture gate.
Never raises: a hook that throws would fail the brain's turn, and a page the gate allowed is already
paid for.
"""

from __future__ import annotations

import sys

#: The headless brain reaches Firecrawl through the plugin-provided server, so the tool is
#: ``mcp__plugin_gtm-engine_firecrawl__firecrawl_scrape`` there and ``mcp__firecrawl__...`` in an
#: editor session (measured by the 2026-09-30 live probe); the server part is never relied on.
SCRAPE_MATCHER = r"mcp__.*firecrawl_scrape"
_SCRAPE_LEAF = "firecrawl_scrape"


def make_capture_hook(cfg, profile: str):
    """An async SDK hook callback that captures Firecrawl scrape results for ``profile``."""
    from gtm_core import capture_cost, signal_sources

    sources_dir = signal_sources.sources_dir_for(profile, cfg.content_root)

    async def _hook(input_data, tool_use_id, context) -> dict:  # noqa: ARG001 - SDK signature
        try:
            tool = input_data.get("tool_name") if isinstance(input_data, dict) else None
            if not isinstance(tool, str) or tool.rsplit("__", 1)[-1] != _SCRAPE_LEAF:
                return {}
            call = input_data.get("tool_input")
            url = call.get("url") if isinstance(call, dict) else None
            result = input_data.get("tool_response")
            text = signal_sources.page_text(result)
            if not isinstance(url, str) or not url or not text:
                return {}
            sha = signal_sources.store_capture(
                url=url, text=text, sources_dir=sources_dir, tool=tool
            )
            try:
                capture_cost.record_capture_cost(
                    profile=profile,
                    url=url,
                    sha256=sha,
                    credits=capture_cost.credits_used(result),
                    tool=tool,
                    content_root=cfg.content_root,
                )
            except Exception as exc:  # noqa: BLE001 - the capture is kept, the gap is reported
                print(f"capture-hook: cost row NOT written for {url!r} ({exc})", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - never fail the brain's turn from a hook
            print(f"capture-hook: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {}

    return _hook
