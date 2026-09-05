from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# parents[2], not [1]: this module sits one level deeper than the pre-split
# gtm_core/brief_lint.py it came from, so the same walk-up would stop at gtm_core/.
ROOT = Path(__file__).resolve().parents[2]

ERROR = "error"
WARN = "warn"

READER = "reader"  # the .html companion — a product/sales/strategy person reads this
RECORD = "record"  # the .md brief and per-source notes — the internal record


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    line: int
    excerpt: str
    fix: str
