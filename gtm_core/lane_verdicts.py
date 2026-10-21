"""Which researcher ``verdict`` values may enrol in each lane. One rule, one home.

A leaf module with no imports, and that is the whole reason it exists as a module rather
than as a constant inside either of its two readers:

* :mod:`gtm_core.account_integrity` is the ENROLLMENT gate — it applies this rule when a
  list is loaded into a sequencer.
* :mod:`gtm_core.prospects_consolidate` is the CONSOLIDATION sweep — it applies the same
  rule when it writes the hand-send list, which is never loaded into a sequencer and so
  never passes the gate at all.

Neither can import the other (``account_integrity`` already imports
``prospects_consolidate``), and ``gtm_core.lanes`` cannot host it either, because that
package's ``__init__`` reaches ``account_integrity`` and would close the cycle.

Before 2026-09-08 only the gate held the rule, so the sweep had no way to ask "may this row
be sent at all" and wrote hand-send rows unfiltered. A rule that only one of two callers can
reach is a rule the other one will eventually re-invent, or skip.
"""

from __future__ import annotations

#: ``drop`` enrols NOWHERE — it is admissible in no lane, which is the property every
#: caller here depends on.
#:
#: The empty lane is the pre-lane default and ``signal`` spells it out: only ``send``.
#: ``generic`` admits ``re-angle`` and an EMPTY verdict — "the account is right and the
#: argument (or the research) is not" is exactly the row a seat-scoped generic email exists
#: for, and it is also the contract a "Hi team," hand-sent body meets, since neither makes
#: a per-recipient claim. ``repair`` admits the rows being re-aimed.
LANE_VERDICTS: dict[str, frozenset[str]] = {
    "": frozenset({"send"}),
    "signal": frozenset({"send"}),
    "personalised": frozenset({"send"}),
    "generic": frozenset({"send", "re-angle", ""}),
    "repair": frozenset({"send", "re-angle"}),
}
