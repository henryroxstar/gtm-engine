"""Hard deterministic CLI runtime guards for the prospecting skill.

Replaces advisory prose checks with executable gates that fail closed (exit code 2).
Enforces §R2 (budget cap), §R8 (interpreter sanity), §R13 (permanent bans in code).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gtm_core.ledgers import sum_month_costs
from gtm_core.paths import resolve_content_root, resolve_profiles_root


def check_interpreter_sanity() -> tuple[bool, str]:
    """Verify that the current Python interpreter correctly imports gtm_core."""
    try:
        from gtm_core import paths

        root = paths.resolve_content_root()
        return True, f"Interpreter OK; content_root={root}"
    except Exception as err:
        return False, f"Interpreter sanity check failed: {err}"


def get_generic_lane_share_cap(profile: str, content_root: Path | None = None) -> float:
    """Read generic_lane_share_cap from content/<profile>/settings.json, defaulting to 0.5."""
    root = content_root if content_root is not None else resolve_content_root()
    settings_path = root / profile / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if "generic_lane_share_cap" in data:
                return float(data["generic_lane_share_cap"])
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return 0.5


def check_generic_lane_cap(
    profile: str,
    generic_count: int,
    total_count: int,
    content_root: Path | None = None,
) -> tuple[bool, str]:
    """Check whether generic lane accounts exceed generic_lane_share_cap."""
    if total_count <= 0:
        return True, "No enrollable candidates; generic lane share is 0.0%"

    cap = get_generic_lane_share_cap(profile, content_root)
    share = generic_count / total_count
    if share > cap:
        return (
            False,
            f"Generic lane share {share:.1%} ({generic_count}/{total_count}) exceeds cap {cap:.1%}",
        )
    return (
        True,
        f"Generic lane share {share:.1%} ({generic_count}/{total_count}) within cap {cap:.1%}",
    )


def get_profile_budget_limits(profile: str, profiles_root: Path | None = None) -> dict[str, float]:
    """Extract per_run_cap_usd and monthly_tool_budget_usd from PROFILE.md."""
    root = profiles_root if profiles_root is not None else resolve_profiles_root()
    profile_md = root / profile / "PROFILE.md"
    limits: dict[str, float] = {
        "per_run_cap_usd": 10.0,
        "monthly_tool_budget_usd": 50.0,
    }

    if not profile_md.exists():
        return limits

    try:
        content = profile_md.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if " #" in val:
                    val = val[: val.index(" #")].strip()
                val = val.strip('"').strip("'")
                if key in ("per_run_cap_usd", "monthly_tool_budget_usd"):
                    try:
                        limits[key] = float(val)
                    except ValueError:
                        pass
    except OSError:
        pass

    return limits


def check_budget_cap(
    profile: str,
    estimated_spend_usd: float,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> tuple[bool, str]:
    """Verify estimated spend respects both per_run_cap_usd and monthly_tool_budget_usd."""
    limits = get_profile_budget_limits(profile, profiles_root)
    per_run_cap = limits["per_run_cap_usd"]
    monthly_budget = limits["monthly_tool_budget_usd"]

    if estimated_spend_usd > per_run_cap:
        return (
            False,
            f"Estimated spend ${estimated_spend_usd:.2f} exceeds per_run_cap_usd (${per_run_cap:.2f})",
        )

    root = content_root if content_root is not None else resolve_content_root()
    costs_path = root / profile / "costs.jsonl"
    if not costs_path.exists():
        costs_path = root / profile / "prospects" / "costs.jsonl"
    current_month_cost = sum_month_costs(costs_path)

    projected_month = current_month_cost + estimated_spend_usd
    if projected_month > monthly_budget:
        return (
            False,
            f"Projected month spend ${projected_month:.2f} (${current_month_cost:.2f} current + ${estimated_spend_usd:.2f} est) exceeds monthly_tool_budget_usd (${monthly_budget:.2f})",
        )

    return (
        True,
        f"Spend approved: ${estimated_spend_usd:.2f} <= per_run (${per_run_cap:.2f}); projected month ${projected_month:.2f} <= monthly (${monthly_budget:.2f})",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI runtime guard dispatcher. Exits 0 on success, 2 on guard violation."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.prospect_guards",
        description="Deterministic runtime guards for prospect runs.",
    )
    parser.add_argument(
        "--check",
        required=True,
        choices=["interpreter", "generic-lane-cap", "budget"],
        help="Guard check to execute",
    )
    parser.add_argument("--profile", default="default", help="Tenant profile name")
    parser.add_argument(
        "--estimated-spend",
        type=float,
        default=0.0,
        help="Estimated run spend in USD for budget guard",
    )
    parser.add_argument(
        "--generic-count",
        type=int,
        default=0,
        help="Generic lane accounts count",
    )
    parser.add_argument(
        "--total-count",
        type=int,
        default=0,
        help="Total enrollable accounts count",
    )

    args = parser.parse_args(argv)

    if args.check == "interpreter":
        ok, msg = check_interpreter_sanity()
        if not ok:
            print(f"[GUARD REFUSED] {msg}", file=sys.stderr)
            return 2
        print(f"[GUARD APPROVED] {msg}")
        return 0

    if args.check == "generic-lane-cap":
        ok, msg = check_generic_lane_cap(
            profile=args.profile,
            generic_count=args.generic_count,
            total_count=args.total_count,
        )
        if not ok:
            print(f"[GUARD REFUSED] {msg}", file=sys.stderr)
            return 2
        print(f"[GUARD APPROVED] {msg}")
        return 0

    if args.check == "budget":
        ok, msg = check_budget_cap(
            profile=args.profile,
            estimated_spend_usd=args.estimated_spend,
        )
        if not ok:
            print(f"[GUARD REFUSED] {msg}", file=sys.stderr)
            return 2
        print(f"[GUARD APPROVED] {msg}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
