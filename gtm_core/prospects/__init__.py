"""One front door for the prospecting pipeline's deterministic commands.

The skills that drive this pipeline reference **33 CLI verbs across 24 modules**, and
the module a verb lives in is an implementation detail a reader has to memorise:
``prospects_consolidate`` but ``list_fit``, ``account_integrity`` but ``suppression``,
``eval_writeback`` but ``adjudication``. The 2026-08-27 audit found that surface was a
memorisation burden the model repeatedly failed — hand-rolling work a module already
did, or calling a neighbouring verb that looked right.

So: ``python -m gtm_core.prospects <verb>``, one namespace, verbs named for what they
do rather than for where they live. Every underlying module keeps its own entry point
untouched — this adds a door, it does not move any rooms, and no existing invocation
changes meaning.
"""

from __future__ import annotations

import importlib
import sys

__all__ = ["VERBS", "main"]

#: verb -> module implementing it. Ordered as a run uses them: build the list, gate it,
#: judge it, write back, measure.
VERBS: dict[str, str] = {
    # Build the list
    "consolidate": "gtm_core.prospects_consolidate",
    "import": "gtm_core.prospects_import",
    "state": "gtm_core.prospects_state",
    "backlog": "gtm_core.prospects_backlog",
    "dashboard": "gtm_core.email_campaign_dashboard",
    "paths": "gtm_core.prospect_paths",
    "status": "gtm_core.prospect_status_cli",
    # Gate it
    "icp": "gtm_core.icp_check",
    "preflight": "gtm_core.preflight",
    "funnel": "gtm_core.funnel",
    "list-fit": "gtm_core.list_fit",
    "integrity": "gtm_core.account_integrity",
    "lanes": "gtm_core.lanes",
    "compliance": "gtm_core.email_compliance",
    "suppression": "gtm_core.suppression",
    # Judge it, write back, measure
    "adjudication": "gtm_core.adjudication",
    "eval": "gtm_core.eval_calibration",
    "writeback": "gtm_core.eval_writeback",
    "wave-gate": "gtm_core.wave_gate",
    "schema-doc": "gtm_core.schema_doc",
}

#: One line per verb for the verb table. Kept here rather than imported from each module
#: so ``--help`` stays a zero-import operation.
_SUMMARY: dict[str, str] = {
    "consolidate": "fold new exports into the master list and rebuild ready-to-load.csv",
    "import": "ingest a bulk provider export into a run's hubspot CSV",
    "state": "merge/restore latest.json — the cumulative account ledger of record",
    "backlog": "report accounts sitting unworked",
    "dashboard": "rebuild the operator status page",
    "paths": "print the canonical locations of this profile's pipeline state",
    "status": "Where does the current list stand, in plain language.",
    "icp": "critique the ICP definition before it spends anything",
    "preflight": "check budget and connectors before a run spends anything",
    "funnel": "size the run against the delivery target",
    "list-fit": "is this list worth working? role fit, signal grade, source hit rate",
    "integrity": "dossier, domain, competitor and record gates before enrollment",
    "lanes": "route every pooled row to exactly one lane; the hold queue and its decisions",
    "compliance": "jurisdiction and sending-posture gate",
    "suppression": "the durable exclusion ledger: apply, verify, migrate",
    "adjudication": "the reading pass — sample, rank, write judge verdicts",
    "eval": "holdouts, scoring, and the rule-fleet report",
    "writeback": "turn human eval labels into changes to the next send list",
    "wave-gate": "record a wave's outcomes; refuse to stage the next one unmeasured",
    "schema-doc": "regenerate the export column map from the schema the code reads",
}


def _usage() -> str:
    width = max(len(v) for v in VERBS)
    lines = [
        "usage: python -m gtm_core.prospects <verb> [args...]",
        "",
        "One front door for the prospecting pipeline. Each verb forwards to the module",
        "that implements it, so `<verb> --help` is that module's own help.",
        "",
        "verbs:",
    ]
    lines += [f"  {verb:<{width}}  {_SUMMARY.get(verb, '')}" for verb in VERBS]
    lines += [
        "",
        "Every module keeps its own entry point; this adds a door, it does not move rooms.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    verb, rest = args[0], args[1:]
    module_name = VERBS.get(verb)
    if module_name is None:
        print(f"unknown verb {verb!r}\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2
    module = importlib.import_module(module_name)
    entry = _entry_point(module)
    if entry is None:  # pragma: no cover - guarded by a test over every verb
        print(f"{module_name} has no CLI entry point; cannot dispatch {verb!r}", file=sys.stderr)
        return 2
    return int(entry(rest) or 0)


def _entry_point(module):
    """The module's CLI callable.

    Half this family names it ``main`` and half names it ``_cli``; both take ``argv``
    and return an exit code. Accepting either is a two-line accommodation here, versus
    renaming six modules' entry points and every direct invocation of them — this
    dispatcher exists to reduce churn on callers, not to cause it.
    """
    for attr in ("main", "_cli"):
        entry = getattr(module, attr, None)
        if callable(entry):
            return entry
    return None


if __name__ == "__main__":
    sys.exit(main())
