"""Test that ⟦FILE:⟧ markers are only emitted when running under the cockpit."""

from __future__ import annotations

import re
from pathlib import Path

GATED = re.compile(r"only when .*cockpit|if you are running under the Telegram cockpit", re.I)


def test_file_sentinels_are_gated():
    for p in Path("plugin/skills").glob("*/body_template.md"):
        t = p.read_text(encoding="utf-8")
        if "⟦FILE:" in t:
            assert GATED.search(t), f"{p} emits ⟦FILE:⟧ without a surface check"
