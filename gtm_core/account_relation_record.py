"""Appends relation_override_used history events to history.jsonl.

Separated from account_integrity so preflight / validation modules do not import Ledgers.
"""

from __future__ import annotations

from typing import Any

from .ledgers import Ledgers
from .paths import PathConfig


def record_relation_overrides(
    profile: str, overrides: list[dict[str, Any]], repo_root: Any = None
) -> None:
    """Record used relation overrides to the tenant's history ledger."""
    if not overrides:
        return
    cfg = PathConfig.from_env(repo_root=repo_root)
    led = Ledgers(cfg, profile)
    for ov in overrides:
        event = {
            "event": "relation_override_used",
            **ov,
        }
        led.append_history(event)
