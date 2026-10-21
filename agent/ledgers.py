"""Re-export shim — implementation lives in gtm_core.ledgers.

Existing callers (agent.pipeline, agent.mcp.worker.server, cockpit.bot,
tests/) continue to import from here without change.
"""

from gtm_core.ledgers import (  # noqa: F401
    Ledgers,
    _current_year_month,
    _utc_now_iso,
)
