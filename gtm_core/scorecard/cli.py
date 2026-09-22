"""``python -m gtm_core.scorecard score|explain`` — offline, free, and read-only.

Human report to stderr, machine JSON to stdout, following :mod:`gtm_core.preflight`.
Exit codes follow the house split: **1 = the data is bad, 2 = I refuse to run.**

Global flags are declared on each SUBPARSER rather than on the top-level parser. That is not
style: ``tests/lint/test_skill_cli_contract.py`` extracts cited commands assuming every flag
consumes a value, so a boolean flag written before the subcommand swallows the subcommand and the
gate then charges the wrong parser's flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..paths import clean_env_var
from .loader import load, scorecard_path
from .model import ScoreCardError, Scored
from .score import score_rows

#: Default **ON**. Off falls back to the caller-supplied ``fit_score`` path, preserving the
#: behaviour that predates this engine.
#:
#: Every other ``GTM_*`` switch in this repo is default-off, because those enable an EXTERNAL
#: EFFECT and opt-in is the safe direction there. This one *disables a refusal*, so default-off
#: would be fail-open. The principle that actually carries across is preserved in :func:`enabled`:
#: an unrecognised value never moves the gate to the permissive side.
ENABLED_ENV = "GTM_SCORECARD_ENABLED"

#: The closed list of values that switch the engine OFF. Anything else leaves it on.
_DISABLING = frozenset({"0", "false", "no", "off"})


def enabled() -> bool:
    return (clean_env_var(ENABLED_ENV) or "").strip().lower() not in _DISABLING


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.scorecard",
        description="Score rows against a tenant's declared rubric, or refuse to.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("score", help="score an items file against the tenant's card")
    _common(run)
    run.add_argument("--items", required=True, help="path to a JSON array of row objects")

    explain = sub.add_parser("explain", help="print the resolved card and what it requires")
    _common(explain)

    args = ap.parse_args(argv)

    try:
        card = load(args.profile, product=args.product, overlay=args.overlay)
    except ScoreCardError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "explain":
        return _explain(card, args)
    return _score(card, args)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", required=True)
    p.add_argument("--product", default=None, help="resolve the card product-first")
    # Same split as `prospects_backlog`: this flag RESOLVES a card, it does not VALIDATE an
    # overlay. Admission is `gtm_core.experiments.admit` (closed allowlist, expiry, kill
    # switch) and the caller runs it BEFORE spending anything. `scorecard.toml` is on that
    # allowlist, so an overlay carrying one can be admitted — an overlay that was never
    # admitted has no business reaching this CLI in the first place.
    p.add_argument(
        "--overlay",
        default=None,
        help="resolve the card from an experiment overlay; admission is the caller's "
        "(python -m gtm_core.experiments), not this command's",
    )
    p.add_argument("--json", action="store_true", help="machine output only, no human report")


def _explain(card, args: argparse.Namespace) -> int:
    payload = {
        "profile": args.profile,
        "path": str(scorecard_path(args.profile, product=args.product, overlay=args.overlay)),
        "scorecard_version": card.version,
        "source": card.source,
        "ceiling": card.ceiling,
        "tiers": dict(card.tiers),
        "required_inputs": list(card.required_inputs),
        "axes": [
            {"name": a.name, "max": a.max, "reads": list(a.reads), "modulated_by": a.modulated_by}
            for a in card.axes
        ],
        "categories": dict(card.categories),
        "exclusions": dict(card.exclusions),
    }
    if not args.json:
        print(
            f"\n{card.source or '(no source declared)'} @ {card.version}\n"
            f"  ceiling {card.ceiling} · tiers "
            + " ".join(f"{k}>={v}" for k, v in sorted(card.tiers.items()))
            + "\n  requires, in order: "
            + " -> ".join(card.required_inputs),
            file=sys.stderr,
        )
    print(json.dumps(payload, indent=2))
    return 0


def _score(card, args: argparse.Namespace) -> int:
    if not enabled():
        print(f"refused: {ENABLED_ENV} is switched off", file=sys.stderr)
        return 2
    try:
        rows = json.loads(Path(args.items).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # An unreadable items file is named, never partially scored.
        print(f"ERROR: cannot read {args.items}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(rows, list):
        print(f"ERROR: {args.items} must hold a JSON array of row objects", file=sys.stderr)
        return 1

    try:
        batch = score_rows(card, rows)
    except ScoreCardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    tiers: dict[str, int] = {}
    categories: dict[str, int] = {}
    out = []
    for row, result in zip(rows, batch.results, strict=True):
        record = {
            "id": row.get("id") or row.get("company"),
            "rubric_source": card.source,
            "rubric_version": card.version,
        }
        if isinstance(result, Scored):
            tiers[result.tier] = tiers.get(result.tier, 0) + 1
            record |= {
                "score": result.score,
                "score_tier": result.tier,
                "score_derivation": list(result.derivation),
            }
        else:
            categories[result.category] = categories.get(result.category, 0) + 1
            record |= {
                "score_category": result.category,
                "score_missing_input": result.missing_input,
                # Every gap, not just the reported one: an operator who fixes the named input
                # and re-runs should not meet the next one with no warning.
                "score_missing_inputs": list(result.missing_inputs),
                "tier": "unscored",
            }
        out.append(record)

    summary = {
        "rubric_source": card.source,
        "rubric_version": card.version,
        "input_count": batch.input_count,
        "scored": len(batch.scored),
        "categorised": len(batch.categorised),
        "tiers": tiers,
        "categories": categories,
    }
    # The ledger row, built here and WRITTEN BY THE CALLER (`ledger_cli append-history`). This
    # module computes and never writes — it is named like a read and stays one, which
    # `tests/lint/destructive_reachability.py` asserts. `rubric_version` is the field that lets
    # `outcomes-sync` attribute a reply-rate change to a rubric change rather than to noise;
    # without it the loop this feature enables cannot be measured.
    event = {"event": "scorecard_run", "skill": "prospect", **summary}
    if not args.json:
        print(
            f"scored {summary['scored']} · categorised {summary['categorised']} · rubric "
            f"{card.source}@{card.version} · tiers "
            + "/".join(f"{k} {v}" for k, v in sorted(tiers.items())),
            file=sys.stderr,
        )
    print(
        json.dumps({"summary": summary, "event": event, "items": out}, indent=2, ensure_ascii=False)
    )
    return 0
