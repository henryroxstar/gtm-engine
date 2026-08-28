"""Connectivity preflight for prospecting runs — fail LOUD, before any spend.

WHY THIS EXISTS
---------------
The 2026-08-11 bulk run spent ~$77 discovering and scoring 500 accounts and then
delivered **zero contacts**, because RocketReach was not loaded into the session and
Apollo was paywalled. Both facts were knowable in the first ten seconds of the run for
zero cost. The skill's Step 1 said to "note which sources are live" — a note is not a
gate, so the run proceeded and the operator learned about the gap at the end.

This module turns that note into a gate. The brain probes each connector with its
cheapest liveness call, hands the observed results here as JSON, and this module decides
which pipeline CAPABILITIES survive — then exits non-zero if a capability the requested
run actually needs is missing.

WHY THE BRAIN PASSES RESULTS IN (rather than this module probing)
-----------------------------------------------------------------
MCP tools live in the agent session, not in this process. A Python module cannot see
whether an MCP server is loaded. So the split is: the brain observes (it is the only
thing that can), this module adjudicates (so the rule is committed, testable, and
identical across runs) — and the verdict lands in the run manifest as evidence.

USAGE
-----
    uv run python -m gtm_core.preflight --profile <slug> \\
        --observed '{"vibe":"ok","rocketreach":"ok","apollo":"api_inaccessible"}' \\
        --need contacts,discovery

Statuses per connector: ``ok`` | ``absent`` (no tools in session) | ``api_inaccessible``
(authorized but the plan blocks data) | ``error``.
"""

from __future__ import annotations

import argparse
import json
import sys

# Which connector(s) can satisfy each capability, in waterfall order.
# Mirrors the prospect skill's documented fallbacks — keep in sync with
# plugin/skills/prospect/references/discovery-and-budget.md.
CAPABILITIES: dict[str, dict] = {
    "discovery": {
        "providers": ["vibe", "web"],
        "why": "find net-new ICP accounts",
        "degraded_note": "web-only discovery: ~30 rows/pass, no firmographics, no topic-intent",
    },
    "intent": {
        "providers": ["vibe", "rocketreach"],
        "why": "heat axis (topic-intent scoring)",
        "degraded_note": "no heat axis — every account scores heat 0 and Tier-A becomes arbitrary",
    },
    "double_intent": {
        "providers": ["vibe+rocketreach"],
        "why": "+1 convergence bonus when two feeds agree",
        "degraded_note": "single feed only — the +1 double-intent bonus is unreachable",
    },
    "contacts": {
        "providers": ["rocketreach", "vibe", "apollo", "web"],
        "why": "resolve a named buyer's verified email/phone",
        "degraded_note": "NO CONTACTS — output is accounts only and cannot be sequenced",
    },
    "sequencing": {
        "providers": ["saleshandy"],
        "why": "stage a paused email sequence",
        "degraded_note": "cannot stage a sequence; drafts stay on disk",
    },
}

OK = "ok"


def adjudicate(observed: dict[str, str], needed: list[str]) -> dict:
    """Return a verdict: per-capability availability + whether the run should proceed."""
    caps, blocking, degraded = {}, [], []
    for name, spec in CAPABILITIES.items():
        winner = None
        for provider in spec["providers"]:
            if "+" in provider:  # convergence capability: every part must be ok
                if all(observed.get(p) == OK for p in provider.split("+")):
                    winner = provider
                    break
            elif provider == "web":  # always available, always the floor
                winner = "web"
                break
            elif observed.get(provider) == OK:
                winner = provider
                break
        primary = spec["providers"][0]
        # "degraded" means ANY fall down the waterfall, not just total absence. A run that
        # silently drops from RocketReach to Vibe for contacts produces materially worse
        # data (no phone, lower match rate) and the operator must be told before spending.
        fell_back = winner is not None and winner != primary
        caps[name] = {
            "available": winner is not None,
            "via": winner,
            "primary": primary,
            "degraded": winner in (None, "web") or fell_back,
            "note": (
                spec["degraded_note"]
                if winner in (None, "web")
                else (
                    f"primary '{primary}' unavailable — running on fallback '{winner}'"
                    if fell_back
                    else ""
                )
            ),
        }
        if name in needed and caps[name]["degraded"]:
            # Hard stop only when the capability is gone or down to the free web floor.
            # A paid-fallback is a warning the operator must acknowledge, not a block.
            if not caps[name]["available"] or winner == "web":
                blocking.append(name)
            else:
                degraded.append(name)
    return {
        "observed": observed,
        "capabilities": caps,
        "needed": needed,
        "blocking": blocking,
        "degraded": degraded,
        "proceed": not blocking,
    }


def render(verdict: dict) -> str:
    lines = ["", "CONNECTIVITY PREFLIGHT", "=" * 62]
    for name, status in verdict["observed"].items():
        mark = "OK  " if status == OK else "FAIL"
        lines.append(f"  [{mark}] {name:14s} {status}")
    lines.append("-" * 62)
    for name, cap in verdict["capabilities"].items():
        flag = "OK  " if not cap["degraded"] else ("GONE" if not cap["available"] else "WEAK")
        want = " <- REQUESTED" if name in verdict["needed"] else ""
        lines.append(f"  [{flag}] {name:14s} via {str(cap['via']):16s}{want}")
        if cap["degraded"] and cap["note"]:
            lines.append(f"         {cap['note']}")
    lines.append("=" * 62)
    if verdict["blocking"]:
        lines += [
            "",
            "STOP — this run cannot deliver what was asked for:",
            *[f"  * {c}: {verdict['capabilities'][c]['note']}" for c in verdict["blocking"]],
            "",
            "Fix the connector, or re-scope the run and say so explicitly, BEFORE spending.",
            "Discovery spend is NOT recoverable by connecting the tool afterwards.",
        ]
    elif verdict.get("degraded"):
        lines += [
            "",
            "PROCEED WITH CAUTION — requested capabilities are on a FALLBACK provider:",
            *[f"  * {c}: {verdict['capabilities'][c]['note']}" for c in verdict["degraded"]],
            "",
            "Say this in the run header and in the final report. Do not let the operator",
            "discover it after the spend.",
        ]
    else:
        lines.append("All requested capabilities available. Safe to spend.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.preflight")
    ap.add_argument("--profile", required=True)
    ap.add_argument(
        "--observed", required=True, help='JSON, e.g. {"vibe":"ok","rocketreach":"absent"}'
    )
    ap.add_argument(
        "--need", default="discovery", help="comma-separated capabilities this run needs"
    )
    ap.add_argument("--warn-only", action="store_true", help="report but always exit 0")
    args = ap.parse_args(argv)

    needed = [c.strip() for c in args.need.split(",") if c.strip()]
    unknown = [c for c in needed if c not in CAPABILITIES]
    if unknown:
        ap.error(f"unknown capability {unknown}; valid: {sorted(CAPABILITIES)}")

    verdict = adjudicate(json.loads(args.observed), needed)
    verdict["profile"] = args.profile
    print(render(verdict), file=sys.stderr)
    print(json.dumps(verdict, indent=2))
    return 0 if (verdict["proceed"] or args.warn_only) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
