"""Single source of truth for monthly tool budget and spend status (R-12)."""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from gtm_core.paths import resolve_content_root, resolve_profiles_root


@dataclass(frozen=True)
class BudgetStatus:
    cap_usd: float
    spent_usd: float
    resets_on: date


def _get_cap(
    profile: str,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
) -> float:
    p_root = profiles_root if profiles_root is not None else resolve_profiles_root()
    profile_md = p_root / profile / "PROFILE.md"
    if profile_md.is_file():
        text = profile_md.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if " #" in val:
                    val = val[: val.index(" #")].strip()
                val = val.strip('"').strip("'")
                if key == "monthly_tool_budget_usd":
                    try:
                        return float(val)
                    except ValueError as exc:
                        raise ValueError(
                            f"Non-numeric monthly_tool_budget_usd on line {lineno} of {profile_md}: {val!r}"
                        ) from exc

    # Legacy fallback: settings.json
    c_root = content_root if content_root is not None else resolve_content_root()
    settings_file = c_root / profile / "settings.json"
    if settings_file.is_file():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            if "monthly_budget_usd" in data:
                warnings.warn(
                    "settings.json monthly_budget_usd is deprecated; set monthly_tool_budget_usd in PROFILE.md instead",
                    DeprecationWarning,
                    stacklevel=2,
                )
                return float(data["monthly_budget_usd"])
        except (OSError, ValueError, TypeError):
            pass

    return 50.0


def _get_spent(profile: str, today: date, content_root: Path | None = None) -> float:
    c_root = content_root if content_root is not None else resolve_content_root()
    costs_file = c_root / profile / "costs.jsonl"
    if not costs_file.is_file():
        return 0.0

    total = 0.0
    for line in costs_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            ts_str = rec.get("ts")
            if ts_str:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt.year == today.year and dt.month == today.month:
                    total += float(rec.get("cost_usd", 0.0))
        except (ValueError, TypeError, AttributeError):
            continue
    return total


def status(
    profile: str,
    today: date | None = None,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
) -> BudgetStatus:
    today_dt = today if today is not None else date.today()
    cap = _get_cap(profile, profiles_root=profiles_root, content_root=content_root)
    spent = _get_spent(profile, today_dt, content_root=content_root)

    if today_dt.month == 12:
        resets_on = date(today_dt.year + 1, 1, 1)
    else:
        resets_on = date(today_dt.year, today_dt.month + 1, 1)

    return BudgetStatus(cap_usd=cap, spent_usd=spent, resets_on=resets_on)


def render(s: BudgetStatus) -> str:
    """Render the single canonical budget status sentence."""
    if s.resets_on.month == 1:
        prev_month = 12
        prev_year = s.resets_on.year - 1
    else:
        prev_month = s.resets_on.month - 1
        prev_year = s.resets_on.year
    month_name = date(prev_year, prev_month, 1).strftime("%B")
    reset_str = f"{s.resets_on.day} {s.resets_on.strftime('%b')}"
    return f"${s.spent_usd:.2f} of your ${s.cap_usd:.2f} for {month_name}. Resets {reset_str}."


def main() -> None:
    parser = argparse.ArgumentParser(description="Report monthly budget status.")
    parser.add_argument("--profile", required=True, help="Profile slug")
    args = parser.parse_args()

    s = status(args.profile)
    print(render(s))


if __name__ == "__main__":
    main()
