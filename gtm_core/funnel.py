"""Funnel sizing for the prospect skill — turn a DELIVERY target into a DISCOVERY target.

The gap this closes (2026-08-12): an operator asking for "500 accounts, end to end" means
500 *sequence-ready contacts*. The skill read it as 500 *discovered accounts*, ran the whole
funnel, and delivered 7 — because every stage has a yield and nothing multiplied them out
beforehand. The narrowing was invisible until the end.

Two jobs:

* :func:`size` — given a delivery target and the profile's measured yields, return the
  discovery target, the per-stage expected counts, and the metered units required. Fail
  closed when the available net-new pool or the remaining budget cannot support it.
* :func:`check_stage` — a per-stage tripwire. Compare a stage's *actual* yield to the
  modelled one; a stage running materially cold stops the run instead of quietly
  shrinking the delivery.

Yields live in ``profiles/<profile>/knowledge/funnel-yields.toml`` so they are tenant
facts, not code, and :func:`record_actuals` writes measured values back after a run so the
model self-corrects instead of drifting.

Stdlib only. No I/O beyond reading/writing the profile's yields file.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ordered funnel. Each stage consumes the prior stage's output.
STAGES: tuple[str, ...] = (
    "scored",  # discovered rows that survive ingest + off-ICP screening
    "qualified",  # survive the profile's gate at the requested tier
    "seat_found",  # a real buyer seat exists at the account
    "contact_usable",  # seat resolves to a deliverable, grade-gated email
    "why_now",  # carries a usable why-now of the requested KIND
)

# Fallback yields, measured on a live 2026-08-11/12 run. A profile's own
# funnel-yields.toml overrides these; these exist so a first run is not blocked.
DEFAULT_YIELDS: dict[str, float] = {
    "scored": 1.00,
    "qualified": 1.00,  # tier="any" — see tier_yield()
    "seat_found": 0.82,
    "contact_usable": 0.68,
    "why_now": 1.00,  # structural mode — see WHY_NOW_YIELDS
}

# The dominant lever: demanding a dated public news event costs ~3x the discovery of a
# structural clause. 08-11 measured 8/25 verified, and only 2/286 signals still fresh later.
WHY_NOW_YIELDS: dict[str, float] = {
    "structural": 1.00,
    "hybrid": 0.60,
    "news": 0.32,
}

# Fraction of PUBLISHED accounts that clear the requested bar.
#
# Tier A and Tier B are the same rubric at different thresholds (icp-personas.md §Gates:
# startup publish >=6 / Tier-A >=7; enterprise publish >=5 / Tier-A >=6). They are NOT
# different treatment classes — copy policy, CTA style and compliance are identical, and
# no tier ever carries a meeting ask (docs/email-optimization.md: interest CTA only).
# The only treatment difference is depth: Tier-A earns a hand-written 1:1 pack, Tier-B
# goes in the merge sequence. So "a+b" is the normal cut for a volume send.
#
# Measured on the 2026-08-11 run: 84 Tier-A of 500 published = 16%.
TIER_YIELDS: dict[str, float] = {"a+b": 1.00, "a": 0.16}

MIN_STAGE_RATIO = 0.70  # a stage below 70% of modelled trips the wire


class FunnelInfeasible(RuntimeError):
    """The requested delivery target cannot be met from the available pool or budget."""


@dataclass
class Plan:
    target_delivered: int
    tier: str
    why_now_mode: str
    yields: dict[str, float]
    discovery_needed: int
    stage_counts: dict[str, int]
    lookups_needed: int
    pool_available: int
    backlog_ready: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_delivered": self.target_delivered,
            "tier": self.tier,
            "why_now_mode": self.why_now_mode,
            "yields": self.yields,
            "discovery_needed": self.discovery_needed,
            "stage_counts": self.stage_counts,
            "lookups_needed": self.lookups_needed,
            "pool_available": self.pool_available,
            "backlog_ready": self.backlog_ready,
            "warnings": self.warnings,
        }


def load_yields(profile_root: Path | None) -> dict[str, float]:
    """Profile-measured yields, falling back to the module defaults per key."""
    merged = dict(DEFAULT_YIELDS)
    if profile_root is None:
        return merged
    path = Path(profile_root) / "knowledge" / "funnel-yields.toml"
    if not path.is_file():
        return merged
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in STAGES:
        val = data.get("yields", {}).get(key)
        if isinstance(val, (int, float)) and 0 < float(val) <= 1:
            merged[key] = float(val)
    return merged


def size(
    target_delivered: int,
    *,
    tier: str = "a+b",
    why_now_mode: str = "structural",
    profile_root: Path | None = None,
    pool_available: int = 0,
    backlog_ready: int = 0,
    lookup_credits_remaining: int | None = None,
) -> Plan:
    """Size the top of the funnel for a DELIVERY target.

    ``pool_available`` is net-new discoverable accounts (after exclusions);
    ``backlog_ready`` is already-qualified accounts that only need a contact — always the
    cheapest source, so it is subtracted from the discovery requirement first.

    Raises :class:`FunnelInfeasible` when the pool or the metered budget cannot cover it.
    """
    if target_delivered <= 0:
        raise ValueError("target_delivered must be positive")
    if why_now_mode not in WHY_NOW_YIELDS:
        raise ValueError(f"why_now_mode must be one of {sorted(WHY_NOW_YIELDS)}")
    if tier not in TIER_YIELDS:
        raise ValueError(f"tier must be one of {sorted(TIER_YIELDS)}")

    y = load_yields(profile_root)
    y = dict(y)
    y["qualified"] = TIER_YIELDS[tier]
    y["why_now"] = WHY_NOW_YIELDS[why_now_mode]

    warnings: list[str] = []

    # Backlog first: an already-qualified account needs only seat+contact+why_now.
    backlog_rate = y["seat_found"] * y["contact_usable"] * y["why_now"]
    # Round the backlog draw UP: flooring here left a 1-row remainder that forced a
    # pointless discovery pass even when the backlog could cover the whole target.
    need_from_backlog = -(-target_delivered // backlog_rate) if backlog_rate else 0
    from_backlog = min(backlog_ready, int(need_from_backlog)) if backlog_rate else 0
    delivered_from_backlog = min(target_delivered, int(from_backlog * backlog_rate + 0.5))
    remaining = max(0, target_delivered - delivered_from_backlog)
    if from_backlog:
        warnings.append(
            f"{from_backlog} backlog accounts cover ~{delivered_from_backlog} of the target "
            f"at {backlog_rate:.0%} — cheaper than discovery; work these before discovering"
        )

    overall = 1.0
    for stage in STAGES:
        overall *= y[stage]
    if overall <= 0:
        raise FunnelInfeasible("modelled yield is zero; refusing to size a run")

    discovery_needed = 0 if remaining == 0 else int(-(-remaining // overall))

    counts: dict[str, int] = {}
    n = float(discovery_needed)
    for stage in STAGES:
        n *= y[stage]
        counts[stage] = int(n)

    # One metered lookup per account that has a seat, across both sources.
    lookups_needed = int(discovery_needed * y["scored"] * y["qualified"] * y["seat_found"]) + int(
        from_backlog * y["seat_found"]
    )

    if discovery_needed > pool_available:
        raise FunnelInfeasible(
            f"need to discover ~{discovery_needed:,} accounts to deliver {target_delivered:,} "
            f"at tier={tier}/{why_now_mode} (overall yield {overall:.1%}), but only "
            f"{pool_available:,} net-new accounts are available. Lower the target, relax the "
            f"tier, switch why_now_mode to 'structural', or widen discovery."
        )
    if lookup_credits_remaining is not None and lookups_needed > lookup_credits_remaining:
        raise FunnelInfeasible(
            f"need ~{lookups_needed:,} contact lookups but only {lookup_credits_remaining:,} "
            f"credits remain"
        )

    return Plan(
        target_delivered=target_delivered,
        tier=tier,
        why_now_mode=why_now_mode,
        yields=y,
        discovery_needed=discovery_needed,
        stage_counts=counts,
        lookups_needed=lookups_needed,
        pool_available=pool_available,
        backlog_ready=backlog_ready,
        warnings=warnings,
    )


def check_stage(stage: str, actual_in: int, actual_out: int, plan: Plan) -> dict[str, Any]:
    """Per-stage tripwire. A stage running materially cold must stop the run.

    Returns a verdict dict; ``ok=False`` means STOP and report, rather than carry a
    silently smaller set forward and surprise the operator at the end.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    modelled = plan.yields[stage]
    actual = (actual_out / actual_in) if actual_in else 0.0
    ratio = (actual / modelled) if modelled else 0.0
    ok = ratio >= MIN_STAGE_RATIO
    projected = plan.target_delivered
    if modelled:
        projected = int(plan.target_delivered * min(1.0, ratio))
    return {
        "stage": stage,
        "modelled": round(modelled, 4),
        "actual": round(actual, 4),
        "ratio": round(ratio, 3),
        "ok": ok,
        "projected_delivery": projected,
        "message": (
            f"{stage}: {actual:.0%} actual vs {modelled:.0%} modelled"
            + (
                ""
                if ok
                else f" — STOP. At this rate the run delivers ~{projected} of "
                f"{plan.target_delivered}. Re-size or widen before continuing."
            )
        ),
    }


def record_actuals(profile_root: Path, measured: dict[str, float]) -> Path:
    """Write measured yields back so the model self-corrects run over run."""
    path = Path(profile_root) / "knowledge" / "funnel-yields.toml"
    lines = [
        "# Measured funnel yields for this profile. Written by gtm_core.funnel.record_actuals",
        "# after a run; hand-edit only to seed a new profile. Used by the prospect skill to",
        "# turn a DELIVERY target into a DISCOVERY target (Step 2 funnel sizing).",
        "",
        "[yields]",
    ]
    for stage in STAGES:
        if stage in measured:
            lines.append(f"{stage} = {float(measured[stage]):.4f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m gtm_core.funnel")
    ap.add_argument("--target", type=int, required=True, help="DELIVERED contacts wanted")
    ap.add_argument("--tier", default="a+b", choices=sorted(TIER_YIELDS))
    ap.add_argument("--why-now-mode", default="structural", choices=sorted(WHY_NOW_YIELDS))
    ap.add_argument("--profile-root", type=Path, default=None)
    ap.add_argument("--pool-available", type=int, default=0)
    ap.add_argument("--backlog-ready", type=int, default=0)
    ap.add_argument("--lookup-credits", type=int, default=None)
    args = ap.parse_args(argv)

    try:
        plan = size(
            args.target,
            tier=args.tier,
            why_now_mode=args.why_now_mode,
            profile_root=args.profile_root,
            pool_available=args.pool_available,
            backlog_ready=args.backlog_ready,
            lookup_credits_remaining=args.lookup_credits,
        )
    except FunnelInfeasible as exc:
        print("FUNNEL INFEASIBLE\n  " + str(exc))
        return 2

    print("FUNNEL PLAN")
    print("=" * 62)
    print(
        f"  deliver            {plan.target_delivered:,} contacts (tier={plan.tier}, {plan.why_now_mode})"
    )
    print(f"  discover           {plan.discovery_needed:,} accounts")
    print(f"  contact lookups    ~{plan.lookups_needed:,}")
    print("-" * 62)
    for stage in STAGES:
        print(f"  {stage:16s} x{plan.yields[stage]:.2f}  -> {plan.stage_counts[stage]:,}")
    print("=" * 62)
    for w in plan.warnings:
        print("  [NOTE] " + w)
    print(json.dumps(plan.to_dict(), indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
