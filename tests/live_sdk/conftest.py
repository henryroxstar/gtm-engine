"""tests/live_sdk — tests that need the REAL Claude Agent SDK and CLI (a few cents of model spend each).

Every test here is collected and then SKIPPED unless ``GTM_LIVE_SDK=1``, so the normal suite never spends
money or needs auth. Run on purpose, e.g.::

    GTM_LIVE_SDK=1 uv run pytest tests/live_sdk -q -o addopts=""

What lives here is exactly what a mocked SDK cannot prove (always-on hardening test plan §6): that a
resumed conversation really restores context across two OS processes, and what the CLI really does with
its transcript-retention setting.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("GTM_LIVE_SDK") == "1":
        return
    skip = pytest.mark.skip(
        reason="live SDK/CLI test: set GTM_LIVE_SDK=1 to run (spends model tokens)"
    )
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(skip)
