"""Schema conformance for a shot list — ``schemas/shots.schema.json``, actually enforced.

The schema declared itself the ``video-script`` → ``video-render`` contract and nothing ran it:
not CI, not the skills, not this linter. On 2026-09-03 that was measured — 11 of the 14 committed
shot lists failed the schema they were nominally written to, and the one place anybody noticed
worked AROUND it, relocating three keys into a render record with a note that they "were never
schema-validated" (``content/<tenant>/video/2026-09-03-launch-film/render-9x16.json``).
A schema nobody runs is what let that drift happen, so the fix is two-sided: the schema was
corrected to permit the production keys real files legitimately carry (via the reserved
``production`` namespace, with ``additionalProperties`` still FALSE so a typo is still a typo),
and this module makes running it the default rather than an opt-in.

Deliberately SEPARATE from :func:`gtm_core.shots_lint.lint_shotlist`. That function is the CRAFT
lint — a set of judgement rules with their own severities, published by ``--rules`` and pinned
family-by-family by the tripwire corpus. Schema conformance is a different kind of claim (this
document is the right SHAPE, not this script is good), so the CLI reports it under its own
``schema_errors`` key and a reader can always tell which gate refused a file.

Stdlib only, like the rest of ``gtm_core`` — :mod:`gtm_core.minischema` is the in-repo validator.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..minischema import validate

#: The contract this module enforces. One copy, resolved from the package, never re-derived by a
#: caller — a second path to the schema is a second schema waiting to disagree.
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "shots.schema.json"


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """The parsed shot-list schema. Cached: a batch run validates many files against one schema."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_errors(doc: object) -> list[str]:
    """Return this document's schema violations, most-specific path first (``[]`` = conformant)."""
    return validate(doc, load_schema())
