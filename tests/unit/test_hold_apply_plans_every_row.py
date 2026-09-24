"""`plan_apply` plans EVERY row of a filled hold sheet: a row it leaves held, refuses, or
finds already recorded is stepped over, never the end of the plan.

The 2026-09-23 hunt turned the `continue` after each of those outcomes in
`gtm_core/lanes/decisions.py::plan_apply` into `break`; three of the four survived, because
every existing fixture put the skipped row LAST. A filled sheet is a person's afternoon —
rows they never got to (blank decision) sit beside rows they answered — so a plan that stops at
the first untouched row silently drops every decision below it, and `--apply` then records
nothing while reporting nothing wrong. Fictional fixtures only (§R9).
"""

from __future__ import annotations

from gtm_core.lanes import decisions as dec


def _entry(**kw) -> dec.DecisionEntry:
    base = {
        "email": "a@brightpath.example",
        "trigger": "prior-contact",
        "account_key": "d:brightpath.example",
        "decision": "",
        "detail": "this address was already emailed",
    }
    base.update(kw)
    return dec.DecisionEntry(**base)


def test_every_skipped_row_kind_is_stepped_over_not_stopped_at():
    prior = {
        ("prior-contact", "d:hollowmere.example"): {"decision": "generic", "stamp": "2026-08-27"}
    }
    entries = [
        # 1. left blank by the operator — held, and the plan goes on
        _entry(),
        # 2. salvage with a chip nobody defined — refused + held, and the plan goes on
        _entry(
            email="b@copperfield.example",
            account_key="d:copperfield.example",
            decision="salvage",
            salvage_kind="make-it-better",
        ),
        # 3. a decision the ledger already holds — idempotent, and the plan goes on
        _entry(
            email="c@hollowmere.example", account_key="d:hollowmere.example", decision="generic"
        ),
        # 4. a decision with no trigger to answer — refused + held, and the plan goes on
        _entry(
            email="d@quillstone.example",
            account_key="d:quillstone.example",
            trigger="",
            decision="suppress",
        ),
        # 5. the row every earlier `break` would have thrown away
        _entry(
            email="e@thornfield.example", account_key="d:thornfield.example", decision="suppress"
        ),
    ]
    plan = dec.plan_apply(entries, prior)

    assert [e.email for e in plan.held] == [
        "a@brightpath.example",
        "b@copperfield.example",
        "d@quillstone.example",
    ]
    assert [r.split(":", 1)[0] for r in plan.refused] == [
        "b@copperfield.example",
        "d@quillstone.example",
    ]
    assert "no trigger on the row" in plan.refused[1]
    assert [e.email for e in plan.already] == ["c@hollowmere.example"]
    assert [e.email for e in plan.suppress] == ["e@thornfield.example"]
    assert not plan.generic and not plan.salvage and not plan.conflicts
    # Every entry landed in exactly one bucket.
    assert len(plan.held) + len(plan.already) + len(plan.suppress) == len(entries)
