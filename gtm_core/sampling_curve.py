"""How many drafts to take of each shot, priced BEFORE any of them is dispatched.

`video-render` already samples: it renders K variants of shot 1, reads them, picks one, and
renders shots 2..N once each. C5 generalises that from a fixed shape into a **curve** the brief
decides — because which shots deserve more than one attempt is a creative judgement, and the
current shape encodes exactly one answer to it (the hook, and nothing else).

Two properties make this safe to widen, and both are the reason it is a module rather than a
paragraph in a body.

**The whole curve is priced before the first render.** A curve is an integer per shot, so its cost
is knowable exactly, and the guard runs over the SUM. Pricing shot by shot is how a batch gets
half-way through a month's budget and stops: the money is spent and there is no finished film.

**A selection is a decision, so it records who made it and why.** `select_record` refuses a
selection with no criterion. A pool whose winner was chosen by an unnamed process is a pool that
cannot be audited, and — practically — nobody can tell later whether the operator looked at it or
a scorer did.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SamplingCurveError",
    "DEFAULT_HOOK_POOL",
    "SamplingCurve",
    "Verdict",
    "price",
    "preflight",
    "select_record",
]


class SamplingCurveError(ValueError):
    """The curve, its price, or a selection made from it is not usable."""


#: What the pool was before C5: K on the hook, one everywhere else. Kept as the default so a run
#: with no brief renders EXACTLY what it rendered before — the widening is opt-in, per piece.
DEFAULT_HOOK_POOL = 3


@dataclass(frozen=True)
class SamplingCurve:
    """How many drafts each shot gets, and how the winner will be chosen.

    ``per_shot`` is keyed by 1-based shot number. A shot absent from it takes one draft; that is
    not a default hiding a decision, it is what "sample this shot once" means.
    """

    per_shot: dict[int, int]
    criterion: str

    def n_for(self, shot: int) -> int:
        return int(self.per_shot.get(shot, 1))

    def total(self, shot_count: int) -> int:
        return sum(self.n_for(i) for i in range(1, shot_count + 1))

    @classmethod
    def default(cls, *, hook_pool: int = DEFAULT_HOOK_POOL) -> SamplingCurve:
        """The pre-C5 shape: a pool on shot 1, one draft everywhere else."""
        return cls(per_shot={1: hook_pool}, criterion="operator_contact_sheet")

    @classmethod
    def from_brief(cls, brief: dict | None, *, hook_pool: int = DEFAULT_HOOK_POOL) -> SamplingCurve:
        """Read decision 8 out of a creator brief, or fall back to the default shape.

        A brief that declares no sampling curve gets the default rather than an error: the brief
        is a record of decisions, and "we did not decide this one" is a legitimate state that must
        keep rendering what it rendered before.
        """
        decision = ((brief or {}).get("decisions", {}) or {}).get("sampling_curve") or {}
        value = decision.get("value") if isinstance(decision, dict) else None
        if not isinstance(value, dict):
            return cls.default(hook_pool=hook_pool)

        criterion = str(value.get("criterion") or "").strip()
        if not criterion:
            raise SamplingCurveError(
                "the brief declares a sampling_curve with no `criterion`. A pool whose winner is "
                "chosen by an unnamed process cannot be audited — nobody can tell afterwards "
                "whether the operator looked at it or a scorer did."
            )

        per_shot: dict[int, int] = {}
        for row in value.get("per_shot") or []:
            if not isinstance(row, dict):
                raise SamplingCurveError(f"sampling_curve.per_shot entry is not an object: {row!r}")
            shot, n = row.get("shot"), row.get("n")
            if not isinstance(shot, int) or shot < 1:
                raise SamplingCurveError(f"sampling_curve names shot {shot!r}, which is not a shot")
            if not isinstance(n, int) or n < 1:
                raise SamplingCurveError(
                    f"sampling_curve asks for {n!r} drafts of shot {shot} — a shot is rendered at "
                    "least once, and 0 is a request to cut it from the script instead"
                )
            per_shot[shot] = n
        return cls(per_shot=per_shot, criterion=criterion)


def price(curve: SamplingCurve, *, shot_count: int, per_render_credits: float) -> float:
    """Total credits the WHOLE curve costs. Exact, because a curve is integers."""
    if shot_count < 1:
        raise SamplingCurveError(f"shot_count must be >= 1, got {shot_count}")
    if per_render_credits < 0:
        raise SamplingCurveError(f"per_render_credits must be >= 0, got {per_render_credits}")
    return curve.total(shot_count) * float(per_render_credits)


@dataclass(frozen=True)
class Verdict:
    """Whether the curve may be dispatched, and the arithmetic behind the answer.

    ``cost`` is in the CAP'S unit; ``cost_credits`` is the raw render count times the per-render
    credit price. They differ by ``credits_to_cap_unit``, and both are kept so a reader can see
    the conversion rather than trust it.
    """

    approved: bool
    renders: int
    cost: float
    month_total: float
    cap: float
    reason: str = ""
    cost_credits: float = 0.0


def preflight(
    curve: SamplingCurve,
    *,
    shot_count: int,
    per_render_credits: float,
    month_total: float,
    cap: float,
    credits_to_cap_unit: float = 1.0,
) -> Verdict:
    """Price the whole curve against the cap BEFORE anything is dispatched.

    The sum is what matters. Checking shot by shot passes the first few and refuses the last,
    which is the worst outcome available: the money is spent and there is no finished film. So a
    curve that would cross the cap is denied ENTIRELY and the caller dispatches nothing.

    ``month_total`` and ``cap`` must share a unit, and ``credits_to_cap_unit`` is what takes the
    curve's CREDIT price into it (USD per credit when the cap is the profile's USD cap). The
    first version compared credits straight against the cap and left the caller to notice —
    which, for a spend guard, is the one place a silent unit mix must not be possible. The CLI
    derives the multiplier from the profile's own rate table rather than accepting a typed one.
    """
    if credits_to_cap_unit <= 0:
        raise SamplingCurveError(f"credits_to_cap_unit must be > 0, got {credits_to_cap_unit}")
    renders = curve.total(shot_count)
    cost_credits = price(curve, shot_count=shot_count, per_render_credits=per_render_credits)
    cost = cost_credits * credits_to_cap_unit
    if month_total + cost > cap:
        return Verdict(
            approved=False,
            renders=renders,
            cost=cost,
            month_total=month_total,
            cap=cap,
            cost_credits=cost_credits,
            reason=(
                f"the whole curve is {renders} render(s) at {cost:.2f} against the cap "
                f"({cost_credits:.2f} credits), and "
                f"{month_total:.2f} of a {cap:.2f} cap is already spent. Denied as a WHOLE rather "
                "than shot by shot: a partial batch spends the money and leaves no finished film. "
                "Lower the curve, raise the cap, or wait for the month to roll."
            ),
        )
    return Verdict(
        approved=True,
        renders=renders,
        cost=cost,
        month_total=month_total,
        cap=cap,
        cost_credits=cost_credits,
    )


def select_record(
    *,
    criterion: str,
    chosen: int,
    requested: int,
    succeeded: int,
) -> dict:
    """The manifest fields recording which pool member won, and how it was picked.

    ``requested`` and ``succeeded`` are both kept. A pool of five where two renders failed is a
    choice made from three, and reporting it as "1 of 5" overstates how much was actually looked
    at — which is exactly the number a later reader would use to judge whether the pick was
    informed.
    """
    if not criterion.strip():
        raise SamplingCurveError(
            "a selection needs a criterion. Which member won is only meaningful alongside how it "
            "was chosen; without it the record cannot distinguish an operator's eye from a "
            "scorer's number."
        )
    if requested < 1:
        raise SamplingCurveError(f"requested must be >= 1, got {requested}")
    if not 0 <= succeeded <= requested:
        raise SamplingCurveError(
            f"succeeded={succeeded} is not within 0..{requested} — a pool cannot return more "
            "members than were asked for"
        )
    if succeeded == 0:
        raise SamplingCurveError(
            f"all {requested} render(s) in this pool failed, so there is nothing to select from. "
            "That is a reportable state, not a selection."
        )
    if not 1 <= chosen <= succeeded:
        raise SamplingCurveError(
            f"chosen={chosen} is out of range for {succeeded} successful member(s) — a rank the "
            "pool could not have produced is a bug in whatever wrote it"
        )
    return {
        "draft_pool_size": requested,
        "pool_succeeded": succeeded,
        "draft_rank": chosen,
        "selection_criterion": criterion.strip(),
        "k_of_n": f"{chosen} of {succeeded}"
        + (f" ({requested} requested)" if succeeded != requested else ""),
    }


def _month_cost_total(profile: str, *, repo_root=None) -> float:
    """This month's USD spend from the profile's own ledger — the same read `ledger_cli
    month-total` performs, reused rather than re-implemented, and never re-typed by a body."""
    from .ledgers import Ledgers
    from .paths import PathConfig

    return Ledgers(PathConfig.from_env(repo_root=repo_root), profile).month_cost_total()


def main(argv: list[str] | None = None) -> int:
    """`python -m gtm_core.sampling_curve preflight --shots N --per-render C --month-total M --cap X`."""
    import argparse
    import json
    from dataclasses import asdict
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.sampling_curve")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preflight", help="price the whole curve against the monthly cap")
    p.add_argument("--brief", type=Path, default=None)
    p.add_argument("--shots", required=True, type=int, help="number of shots in the list")
    p.add_argument("--per-render", required=True, type=float, help="CREDITS per render")
    p.add_argument(
        "--profile",
        default=None,
        help="convert credits to USD with this profile's own rate table and read the month's "
        "spend from its ledger — the two numbers a body must never re-type (§R14)",
    )
    p.add_argument(
        "--month-total",
        type=float,
        default=None,
        help="override the ledger read (tests, offline); same unit as --cap",
    )
    p.add_argument("--cap", required=True, type=float, help="the monthly cap; USD with --profile")
    p.add_argument("--hook-pool", type=int, default=DEFAULT_HOOK_POOL)
    p.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.profile is None and args.month_total is None:
        parser.error("--month-total is required without --profile (and --cap then shares its unit)")

    try:
        brief = json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else None
        curve = SamplingCurve.from_brief(brief, hook_pool=args.hook_pool)

        multiplier = 1.0
        month_total = args.month_total
        if args.profile is not None:
            # Both numbers come from the code that owns them. A body re-typing the ledger total
            # is a §R14 defect; a body guessing the credit rate is a unit mix inside a spend guard.
            from .credit_rates import usd_per_credit

            multiplier = usd_per_credit(args.profile, repo_root=args.repo_root)
            if month_total is None:
                month_total = _month_cost_total(args.profile, repo_root=args.repo_root)

        verdict = preflight(
            curve,
            shot_count=args.shots,
            per_render_credits=args.per_render,
            month_total=month_total,
            cap=args.cap,
            credits_to_cap_unit=multiplier,
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[sampling-curve] {exc}")
        return 1
    except SamplingCurveError as exc:
        print(f"[sampling-curve] {exc}")
        return 2

    print(
        json.dumps(
            {
                "curve": {str(k): v for k, v in sorted(curve.per_shot.items())},
                "criterion": curve.criterion,
                **asdict(verdict),
            },
            indent=2,
        )
    )
    return 0 if verdict.approved else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
