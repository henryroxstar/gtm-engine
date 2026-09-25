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
(authorized but the plan blocks data) | ``rate_limited`` (answering, but refusing the call) |
``error``.

Pass ``--limits`` with the provider's own ``account`` response to catch the case none of those
statuses can express: a connector that is live, in credit, and locked out of the one action the
run needs.
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
        "actions": {"rocketreach": "company_search"},
        "why": "heat axis (topic-intent scoring)",
        "degraded_note": "no heat axis — every account scores heat 0 and Tier-A becomes arbitrary",
    },
    "double_intent": {
        "providers": ["vibe+rocketreach"],
        "actions": {"rocketreach": "company_search"},
        "why": "+1 convergence bonus when two feeds agree",
        "degraded_note": "single feed only — the +1 double-intent bonus is unreachable",
    },
    "contacts": {
        "providers": ["rocketreach", "vibe", "apollo", "web"],
        "actions": {"rocketreach": "person_lookup"},
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

#: A connector the brain observed as rate-limited wholesale — it answered, but refused the call.
RATE_LIMITED = "rate_limited"


def exhausted_actions(limits: dict | None) -> dict[tuple[str, str], str]:
    """``(provider, action) -> the window it is exhausted for``, from providers' own payloads.

    WHY THIS EXISTS. ``ping`` returns pong and ``account`` returns a healthy credit balance, so
    liveness and credits both look fine while the call is refused: on 2026-09-21 this module
    reported "intent available via vibe+rocketreach · Safe to spend" while RocketReach
    ``company_search`` sat at 1000/1000 for the day, 0 remaining — a ~12 hour lockout on the
    CREDIT-FREE half of the double-intent axis. Heat was structurally capped at 2 and the run
    could have concluded "no double-intent accounts" when it simply could not look.

    Accepts either the provider's whole ``account`` response or just its ``rate_limits`` array,
    because the operator pastes whichever they have. A ``null`` limit (the real payload carries
    one for ``bulk_job``/``one_month``) means "no ceiling" and is not exhaustion.
    """
    out: dict[tuple[str, str], str] = {}
    for provider, payload in (limits or {}).items():
        rows = payload.get("rate_limits", []) if isinstance(payload, dict) else payload
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            action, remaining = row.get("action"), row.get("remaining")
            # `remaining` is the only field that decides. A null limit has no ceiling to hit;
            # a 0 is exhaustion whatever the limit says.
            if not isinstance(action, str) or not isinstance(remaining, int):
                continue
            if remaining <= 0:
                out[(provider, action)] = str(row.get("duration") or "an unknown window")
    return out


def adjudicate(
    observed: dict[str, str],
    needed: list[str],
    no_fallback: bool = False,
    limits: dict | None = None,
) -> dict:
    """Return a verdict: per-capability availability + whether the run should proceed.

    ``limits`` carries providers' own ``rate_limits`` payloads. A connector that is live and in
    credit but at **0 remaining** on the action this capability actually calls is NOT available
    for it — reporting otherwise is how a run concludes "no double-intent accounts" when it was
    locked out of looking.
    """
    spent = exhausted_actions(limits)
    caps, blocking, degraded = {}, [], []
    for name, spec in CAPABILITIES.items():
        actions = spec.get("actions", {})

        def usable(provider: str, _actions=actions) -> bool:
            """Live, and not locked out of the action this capability needs from it."""
            if observed.get(provider) != OK:
                return False
            action = _actions.get(provider)
            return not (action and (provider, action) in spent)

        winner = None
        for provider in spec["providers"]:
            if "+" in provider:  # convergence capability: every part must be usable
                if all(usable(p) for p in provider.split("+")):
                    winner = provider
                    break
            elif provider == "web":  # always available, always the floor
                winner = "web"
                break
            elif usable(provider):
                winner = provider
                break

        # Locked-out providers are computed over the WHOLE waterfall, not just the part the
        # loop walked before it found a winner. A fallback covering for a rate-limited primary
        # is exactly the case the operator must be told about, and it is also the case where
        # the loop breaks first — so deriving this from the loop reported nothing (caught by
        # test_intent_falls_back_to_vibe_rather_than_claiming_rocketreach).
        locked = [
            p
            for p in dict.fromkeys(
                part for provider in spec["providers"] for part in provider.split("+")
            )
            if observed.get(p) == RATE_LIMITED or (p in actions and (p, actions[p]) in spent)
        ]
        primary = spec["providers"][0]
        # "degraded" means ANY fall down the waterfall, not just total absence. A run that
        # silently drops from RocketReach to Vibe for contacts produces materially worse
        # data (no phone, lower match rate) and the operator must be told before spending.
        fell_back = winner is not None and winner != primary
        # Say WHICH action is spent and for how long. "rocketreach unavailable" sends an
        # operator to reconnect a connector that is working fine.
        rate_note = "; ".join(
            f"{p} '{actions[p]}' rate-limited (0 remaining this {spent[(p, actions[p])]})"
            for p in locked
            if p in actions and (p, actions[p]) in spent
        )
        caps[name] = {
            "available": winner is not None,
            "via": winner,
            "primary": primary,
            "degraded": winner in (None, "web") or fell_back,
            "rate_limited": locked,
            "note": "; ".join(
                part
                for part in (
                    rate_note,
                    spec["degraded_note"]
                    if winner in (None, "web")
                    else (
                        f"primary '{primary}' unavailable — running on fallback '{winner}'"
                        if fell_back
                        else ""
                    ),
                )
                if part
            ),
        }
        if name in needed and caps[name]["degraded"]:
            # Hard stop only when the capability is gone or down to the free web floor.
            # A paid-fallback is a warning the operator must acknowledge, not a block.
            # If no_fallback is True, ANY degradation is a blocking error.
            if not caps[name]["available"] or winner == "web" or no_fallback:
                blocking.append(name)
            else:
                degraded.append(name)

    web_floor_offer = False
    if blocking:
        web_causes = [
            c
            for c in blocking
            if caps[c]["available"] and caps[c]["via"] == "web" and not no_fallback
        ]
        if len(web_causes) == len(blocking):
            web_floor_offer = True

    return {
        "observed": observed,
        "capabilities": caps,
        "needed": needed,
        "blocking": blocking,
        "degraded": degraded,
        "proceed": not blocking,
        "web_floor_offer": web_floor_offer,
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
    if verdict.get("web_floor_offer"):
        from gtm_core.refusal_copy import Refusal

        refusal = Refusal(
            what="I stopped before spending",
            why="the step you asked for needs verified contacts and no contact tool is connected",
            next_step="Connect Vibe or RocketReach in Settings → Connectors",
            alternative="say 'run it on web search' to get companies only",
            cost="Nothing was spent",
        )
        lines += [
            "",
            refusal.render(),
            "",
            "You can still run on free web search: companies only, no verified contacts, nothing to load into a sender.",
        ]
    elif verdict["blocking"]:
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
    ap.add_argument(
        "--limits",
        default=None,
        help='JSON of providers\' own rate_limits, e.g. {"rocketreach": <the account response>}. '
        "A capability whose action is at 0 remaining is reported unavailable, not available.",
    )
    ap.add_argument("--warn-only", action="store_true", help="report but always exit 0")
    ap.add_argument(
        "--no-fallback",
        action="store_true",
        help="exit 2 if any required capability falls back to a secondary provider or web",
    )
    args = ap.parse_args(argv)

    needed = [c.strip() for c in args.need.split(",") if c.strip()]
    unknown = [c for c in needed if c not in CAPABILITIES]
    if unknown:
        ap.error(f"unknown capability {unknown}; valid: {sorted(CAPABILITIES)}")

    verdict = adjudicate(
        json.loads(args.observed),
        needed,
        no_fallback=args.no_fallback,
        limits=json.loads(args.limits) if args.limits else None,
    )
    verdict["profile"] = args.profile
    print(render(verdict), file=sys.stderr)
    print(json.dumps(verdict, indent=2))
    return 0 if (verdict["proceed"] or args.warn_only) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
