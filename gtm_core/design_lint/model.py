from __future__ import annotations

import re
from dataclasses import dataclass

ERROR = "error"
WARN = "warn"

#: A judgement call, and the one severity here that never blocks — not on exit code, and
#: not under ``--strict``. It carries three properties the other two do not: it cannot fail
#: a build, it cannot be suppressed from the document (there is nothing to suppress, so a
#: solution design never carries a linter comment recording that the linter is out of date),
#: and it prints in its own block addressed to a person. SD6-SD9 emit it because all four
#: reason about what a design *claims* rather than how it is shaped, and the author's
#: knowledge of what shipped is newer than this package's. See
#: :mod:`gtm_core.design_lint.rules_claims`.
ADVISORY = "advisory"


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    section: int
    excerpt: str
    fix: str


# ══════════════════════════════════════════════════════════════════════════════════════
# Parsing — a solution design is a markdown document whose headings carry its structure:
# an executive summary, a Tier 1 customer overview, and a Tier 2 technical appendix whose
# items are numbered A1…An. The IR is one Section per heading, in document order.
# ══════════════════════════════════════════════════════════════════════════════════════


@dataclass
class Section:
    index: int  # 1-based, document order
    line: int  # 1-based line in the source where the heading sits
    level: int  # 1–6 (the count of leading '#'); 0 for text before the first heading
    heading: str = ""
    body: str = ""

    @property
    def slug(self) -> str:
        """Heading normalised for matching: lowercased, punctuation dropped."""
        return re.sub(r"[^a-z0-9 ]+", " ", self.heading.lower()).strip()

    @property
    def appendix_id(self) -> str:
        """`A3` for a heading like `A3. Component inventory`, else ''."""
        match = re.match(r"^(A\d+)\b", self.heading.strip())
        return match.group(1) if match else ""

    @property
    def suppressed(self) -> set[str]:
        # `SD\d+`, not `SD\d` — with a two-digit tier in play (SD10…SD13) a single-digit
        # capture reads `lint-ok SD12` as `SD1` and suppresses the wrong rule while looking
        # like it worked. deck_lint/model.py carries the same note for the same reason.
        return {m.upper() for m in re.findall(r"lint-ok\s+(SD\d+)", self.body + self.heading)}
