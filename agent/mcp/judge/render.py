"""The one place the judge reaches the linter's template renderer.

Isolated in its own module so :mod:`agent.mcp.judge.scoring` stays importable without the
``sys.path`` insert, and so there is exactly one copy of the insert rather than one per
caller.

Imported the same documented way :mod:`gtm_core.cells` imports ``seat_of`` and
:mod:`gtm_core.build_eval_sheet` imports ``render``/``parse_spec``. Two implementations of
"what does this row actually receive" would drift, and the drift would be invisible: the
judge would score copy nobody sends.

Since 2026-09-04 it also re-exports the 1:1 **pack** parsers, for the same reason and under
the same rule: a pack is a rendered email for one named person, and there must be exactly one
definition of what a pack says. They come through here rather than through a second
``sys.path`` insert of their own.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LINTER_DIR = Path(__file__).resolve().parents[3] / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from merge_render_linter import Touch, parse_spec, render  # noqa: E402
from outreach_pack_linter import (  # noqa: E402
    detect_format,
    parse_draft_outreach_pack,
    parse_prospect_pack,
)

__all__ = [
    "Touch",
    "detect_format",
    "parse_draft_outreach_pack",
    "parse_prospect_pack",
    "parse_spec",
    "render",
]
