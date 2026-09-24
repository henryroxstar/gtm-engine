#!/usr/bin/env python3
"""The outreach copy gate's command line. Implementation: ``tests/linter/outreach/``.

uv run python tests/linter/outreach_linter.py pack   <pack.md> [...]
uv run python tests/linter/outreach_linter.py render <spec.md> --csv <rows.csv> [...]
uv run python tests/linter/outreach_linter.py --list-rules   # every id it can raise
uv run python tests/linter/outreach_linter.py --selftest     # both positive controls
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from outreach.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
