"""Re-export shim — implementation lives in gtm_core.locks.

Existing callers (agent.pipeline, agent.__main__, tests/) continue to import
from here without change.
"""

from gtm_core.locks import LockBusy, profile_lock  # noqa: F401
