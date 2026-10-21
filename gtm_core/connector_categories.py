"""Connector categories — an unbound category refuses, and never falls through to plausible prose.

Five skills address a connector by CATEGORY rather than by name — ``~~CRM``,
``~~product_analytics``, ``~~user_feedback``. The convention comes from a plugin that resolves
those placeholders against the user's own connected tools. **This repo resolves none of them.**

That is not a gap this module closes; it is a gap it makes loud. A skill that says "query the
connected ``~~CRM``" and runs in an environment with no CRM does not stop — it produces a
confident deal-hygiene report over nothing, and the report looks exactly like one built from real
pipeline data. The failure is silent at every layer: no tool errors, no ledger row, no empty
output. So the category is checked first, and an unbound one **refuses by name**.

The shape follows the one this repo already learned: *a value that is absent must never be read as
permission* (``tests/unit/test_absence_is_never_permissive.py``). ``BOUND`` is a closed list of
GRANTING values, not an open test for a blocking one — an unrecognised category refuses exactly as
an unbound one does, because "we have never heard of this category" is not evidence that it is
wired.

Usage::

    uv run python -m gtm_core.connector_categories crm

Exit 0 when the category is bound to at least one connector this deployment ships; exit 1 with a
named message otherwise. Stdlib only, no I/O, tenant-agnostic.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

__all__ = ["BOUND", "CATEGORIES", "Preflight", "main", "preflight"]

#: The closed vocabulary of categories a skill may address. A category outside this set is a
#: typo or an invention; either way it cannot be bound, so it refuses.
CATEGORIES: frozenset[str] = frozenset({"crm", "product_analytics", "user_feedback"})

#: Category -> the connectors this deployment actually binds to it. **Every value is empty, and
#: that is the current, measured truth** (2026-09-22): `agent/mcp_config.py` wires fourteen
#: servers and not one of them is a CRM, a product-analytics tool or a feedback inbox. Binding a
#: category means adding its MCP server there AND naming it here — the second half is what makes
#: the skills stop refusing, so the two cannot drift into "wired but still refusing" or, far
#: worse, "declared but not wired".
BOUND: dict[str, tuple[str, ...]] = {
    "crm": (),
    "product_analytics": (),
    "user_feedback": (),
}

_WHAT_IT_IS = {
    "crm": "CRM (deal stages, owners, close dates)",
    "product_analytics": "product-analytics connector (event streams, funnels, retention)",
    "user_feedback": "user-feedback source (tickets, surveys, call transcripts)",
}


@dataclass(frozen=True)
class Preflight:
    """The verdict on one category. ``granted`` is the only value that admits."""

    category: str
    granted: bool
    connectors: tuple[str, ...]
    message: str


def preflight(category: object, bound: dict[str, tuple[str, ...]] | None = None) -> Preflight:
    """Whether `category` is bound to a connector here.

    `bound` is injectable so the granting branch is testable while every real category is
    unbound. A granting branch nothing exercises is indistinguishable from one that does not
    work, and this one has to work the day a CRM is wired.
    """
    table = BOUND if bound is None else bound
    name = category.strip().lower() if isinstance(category, str) else ""
    if not name or name not in CATEGORIES:
        return Preflight(
            category=name,
            granted=False,
            connectors=(),
            message=(
                f"refused: {category!r} is not a connector category this system recognises. "
                f"Known categories: {', '.join(sorted(CATEGORIES))}."
            ),
        )
    # `if c` is not enough: a half-written binding of `("   ",)` is a row somebody started
    # and did not finish, and whitespace is truthy. Absence, not permission.
    connectors = tuple(c.strip() for c in table.get(name, ()) if isinstance(c, str) and c.strip())
    if not connectors:
        return Preflight(
            category=name,
            granted=False,
            connectors=(),
            message=(
                f"refused: no connector is bound to `{name}` in this deployment, so there is no "
                f"{_WHAT_IT_IS[name]} to read. Say so and stop. Do NOT substitute a plausible "
                f"answer, a `content/` file that is not that system, or a recollection of an "
                f"earlier run — a report built from nothing reads exactly like one built from "
                f"data. To bind it: add the MCP server in `agent/mcp_config.py` and name it "
                f"under `{name}` in `gtm_core/connector_categories.py`."
            ),
        )
    return Preflight(
        category=name,
        granted=True,
        connectors=connectors,
        message=f"`{name}` is bound to: {', '.join(connectors)}.",
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(
            "usage: python -m gtm_core.connector_categories <category>\n"
            f"categories: {', '.join(sorted(CATEGORIES))}",
            file=sys.stderr,
        )
        return 2
    failed = False
    for category in args:
        verdict = preflight(category)
        print(("✓ " if verdict.granted else "✗ ") + verdict.message)
        failed = failed or not verdict.granted
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
