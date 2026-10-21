from __future__ import annotations

import re
from dataclasses import dataclass, field

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    slide: int
    excerpt: str
    fix: str


# ══════════════════════════════════════════════════════════════════════════════════════
# Parsing — a Slidev deck is headmatter, then slides separated by `---`, each optionally
# carrying its own frontmatter and ending with an HTML comment that is the presenter note.
# ══════════════════════════════════════════════════════════════════════════════════════


@dataclass
class Slide:
    index: int  # 1-based, as Slidev numbers them
    line: int  # 1-based line in the source file where the slide body starts
    frontmatter: dict[str, str] = field(default_factory=dict)
    body: str = ""
    notes: str = ""

    @property
    def layout(self) -> str:
        return self.frontmatter.get("layout", "default")

    @property
    def clicks(self) -> int:
        try:
            return int(self.frontmatter.get("clicks", "0"))
        except ValueError:
            return 0

    @property
    def tag(self) -> str:
        return self.frontmatter.get("tag", "").strip("\"'")

    @property
    def suppressed(self) -> set[str]:
        # `D\d+`, not `D\d` — with a two-digit tier in play (D10) a single-digit capture would
        # read `lint-ok D10` as `D1` and suppress the wrong rule while looking like it worked.
        return {m.upper() for m in re.findall(r"lint-ok\s+(D\d+)", self.body + self.notes)}
