"""Credits → USD. The conversion that makes a prepaid-pool spend reach the monthly cap.

WHY THIS FILE EXISTS
--------------------
Every paid surface in this system meters in the unit its provider bills in, and the monthly cap
(:meth:`gtm_core.ledgers.Ledgers.month_cost_total`, §R2) is denominated in **USD**. For the token
surfaces the conversion is already config: :mod:`gtm_core.models` pins ``input_usd_per_1k`` /
``output_usd_per_1k`` per role, so a call's dollars are derivable at the call site. For the
**credit** surfaces there was no such rate anywhere in the repo — ``grep credit_rate`` /
``usd_per_credit`` returned nothing — and a Higgsfield render therefore had no honest ``cost_usd``
to write. So nothing was written, and the cap read ``$0.00``.

That is not an abstract gap. It has now happened twice on the same lane:

* **August 2026** — ~580 credits of video spend recorded as $0 (:mod:`gtm_core.render_manifest`'s
  ``cost_source`` docstring; recovered after the fact by ``ledger_cli reconcile-renders``).
* **2026-09-03, one tenant** — a daily launch film spent 405 credits on nine ``seedance_2_0``
  renders plus predictor jobs and images (measured balance delta 1306 → 899.46), while
  ``month-total --cap 100`` reported ``{"total_usd": 0.0, "over_cap": false}`` throughout.

Both spends were real money. Neither was representable, because the repo could not answer "what
is a credit worth". This module answers exactly that, and nothing else.

WHERE THE RATE LIVES, AND WHY IT IS SPLIT IN TWO
------------------------------------------------
The published **rate card** (which plan buys how many credits for how much) is a provider fact:
global, non-secret, identical for every tenant, and dated — so it is a constant here, in the same
shape :mod:`gtm_core.video_preflight` already uses for the Reap and HeyGen rate cards, with a
``_VERIFIED_ON`` date on the claim rather than on the file.

**Which plan a tenant is on** is a tenant fact — it differs per profile and changes when the
operator changes it — so it lives in that profile's ``content/<profile>/settings.json``:

    "higgsfield_plan":    "ultra",     # one of HIGGSFIELD_PLANS
    "higgsfield_billing": "monthly"    # "monthly" or "annual" — a 1.84x difference on Ultra

Neither half has a default. A profile that has not declared its plan gets a
:class:`CreditRateError`, never a guessed rate: the failure this module exists to fix is a
confident ``$0.00``, and a confidently *wrong* dollar figure is the same failure wearing a number.
Callers that must degrade (``ledger_cli reconcile-renders``) report the error text alongside a
``None``, so "we do not know" stays distinguishable from "it was free".

CLI (read-only)::

    uv run python -m gtm_core.credit_rates --profile <tenant>
    uv run python -m gtm_core.credit_rates --profile <tenant> --credits 405
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from gtm_core.paths import resolve_content_root

#: The provider this card prices. One provider today; the resolver keys on it so a second credit
#: surface (ElevenLabs characters, Reap media credits) lands as a sibling card, not a rewrite.
PROVIDER = "higgsfield"

#: When the plan card below was last read off the live pricing page. It dates the CLAIM, not the
#: file — see gtm_core/render_engines.toml's `verified_on` for the same convention and the same
#: reason. Source: the 2026-06-19 Higgsfield integration note, §3.3 ("confirmed live
#: 2026-06-19", consumer MCP-OAuth pool — the pool the render lane actually draws against, NOT
#: the separately-funded developer-portal REST balance, whose rates that note records as
#: unconfirmed to this day).
HIGGSFIELD_PLANS_VERIFIED_ON = "2026-06-19"


class CreditRateError(ValueError):
    """No honest credits→USD rate could be resolved. The message names what is missing."""


@dataclass(frozen=True)
class Plan:
    """One row of the published consumer plan card.

    ``usd_per_month_annual`` is the headline price when billed annually; ``usd_per_month_monthly``
    is the month-to-month price. They are both "per month" and they are NOT interchangeable — on
    Ultra they differ by 1.84x, which is the difference between a cap that fires and one that does
    not. ``None`` means the source quoted an approximation (e.g. Starter's "~$24/mo") and this
    module will refuse to derive an exact rate from it rather than pass the tilde on silently.
    """

    credits_per_month: int
    usd_per_month_annual: float | None
    usd_per_month_monthly: float | None


#: Higgsfield consumer plans (the MCP-OAuth / web-app pool). Verified live 2026-06-19; recorded in
#: the 2026-06-19 Higgsfield integration note, §3.3.
#:
#: A credit BALANCE is deliberately absent — that changes on every job, the live read is free, and
#: a stale balance stated as fact is the drift class this repo has stamped out elsewhere
#: (tests/media/test_video_preflight_cost.py::test_no_credit_balance_is_baked_into_the_module).
#: Rates and plan allotments are constants; balances are not.
HIGGSFIELD_PLANS: dict[str, Plan] = {
    "starter": Plan(credits_per_month=270, usd_per_month_annual=19.0, usd_per_month_monthly=None),
    "plus": Plan(credits_per_month=1200, usd_per_month_annual=35.0, usd_per_month_monthly=59.0),
    "ultra": Plan(credits_per_month=3000, usd_per_month_annual=70.0, usd_per_month_monthly=129.0),
}

BILLING_MODES = ("annual", "monthly")

_SETTINGS_PLAN_KEY = "higgsfield_plan"
_SETTINGS_BILLING_KEY = "higgsfield_billing"


def plan_usd_per_credit(plan_name: str, billing: str) -> float:
    """USD per credit for a named plan on a named billing cadence. Pure.

    ``price_per_month / credits_per_month`` — the plan allotment is what those dollars buy, so
    that quotient is what one credit costs. Raises :class:`CreditRateError` for an unknown plan,
    an unknown cadence, or a cadence whose published price is an approximation.
    """
    plan = HIGGSFIELD_PLANS.get(plan_name)
    if plan is None:
        raise CreditRateError(
            f"unknown {PROVIDER} plan {plan_name!r} — expected one of {sorted(HIGGSFIELD_PLANS)}"
        )
    if billing not in BILLING_MODES:
        raise CreditRateError(
            f"unknown billing cadence {billing!r} — expected one of {list(BILLING_MODES)}. "
            "The two are not interchangeable: on Ultra they differ by 1.84x."
        )
    price = plan.usd_per_month_annual if billing == "annual" else plan.usd_per_month_monthly
    if price is None:
        raise CreditRateError(
            f"the published {plan_name!r} price for {billing!r} billing is an approximation in "
            "the source pricing note, not an exact "
            "figure. Read the exact price off the live billing page and record it here before "
            "metering against it — an approximate rate presented as a dollar amount is the same "
            "failure as no rate at all."
        )
    return price / plan.credits_per_month


def _settings_path(profile: str, *, repo_root: Path | None = None) -> Path:
    return resolve_content_root(repo_root) / profile / "settings.json"


def _read_settings(profile: str, *, repo_root: Path | None = None) -> dict:
    path = _settings_path(profile, repo_root=repo_root)
    if not path.is_file():
        raise CreditRateError(f"no settings.json for profile {profile!r} (looked at {path})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CreditRateError(f"settings.json for {profile!r} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise CreditRateError(f"settings.json for {profile!r} is not a JSON object")
    return data


def usd_per_credit(profile: str, *, repo_root: Path | None = None) -> float:
    """The profile's credits→USD rate, from its declared plan. Fail-closed.

    Raises :class:`CreditRateError` when the profile has not declared both
    ``higgsfield_plan`` and ``higgsfield_billing``. There is no default and there must not be
    one: a guessed rate would put a wrong number in ``costs.jsonl`` and against the cap, which
    is strictly worse than the ``$0.00`` this module was written to replace, because a wrong
    number looks metered.
    """
    settings = _read_settings(profile, repo_root=repo_root)
    plan_name = str(settings.get(_SETTINGS_PLAN_KEY, "") or "").strip().lower()
    billing = str(settings.get(_SETTINGS_BILLING_KEY, "") or "").strip().lower()
    missing = [
        key
        for key, value in ((_SETTINGS_PLAN_KEY, plan_name), (_SETTINGS_BILLING_KEY, billing))
        if not value
    ]
    if missing:
        raise CreditRateError(
            f"profile {profile!r} declares no {PROVIDER} rate: "
            f"{_settings_path(profile, repo_root=repo_root)} is missing {missing}. "
            f"Set {_SETTINGS_PLAN_KEY} (one of {sorted(HIGGSFIELD_PLANS)}) and "
            f"{_SETTINGS_BILLING_KEY} (one of {list(BILLING_MODES)}). Until then a credit spend "
            "on this profile cannot be converted to USD and must NOT be recorded as $0 — an "
            "unknown cost and a free one are different facts."
        )
    return plan_usd_per_credit(plan_name, billing)


def credits_to_usd(credits: float, profile: str, *, repo_root: Path | None = None) -> float:
    """Convert a credit spend to USD for ``profile``, rounded to the ledger's 6dp.

    Six decimal places because that is what :meth:`gtm_core.metering.CostRecord.to_jsonl_dict`
    writes; rounding here keeps a manifest's arithmetic and its ledger row byte-identical.
    """
    return round(float(credits) * usd_per_credit(profile, repo_root=repo_root), 6)


def rate_report(profile: str, *, repo_root: Path | None = None) -> dict:
    """Everything a caller needs to show its working, or to explain why it cannot.

    Never raises. On failure ``usd_per_credit`` is ``None`` and ``error`` carries the message —
    the shape a degrading caller needs so "unknown" never collapses into ``0.0``.
    """
    out: dict = {
        "profile": profile,
        "provider": PROVIDER,
        "verified_on": HIGGSFIELD_PLANS_VERIFIED_ON,
        "plan": None,
        "billing": None,
        "usd_per_credit": None,
        "error": None,
    }
    try:
        settings = _read_settings(profile, repo_root=repo_root)
        out["plan"] = str(settings.get(_SETTINGS_PLAN_KEY, "") or "").strip().lower() or None
        out["billing"] = str(settings.get(_SETTINGS_BILLING_KEY, "") or "").strip().lower() or None
        out["usd_per_credit"] = usd_per_credit(profile, repo_root=repo_root)
    except CreditRateError as exc:
        out["error"] = str(exc)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.credit_rates",
        description="Resolve a profile's credits→USD rate (read-only; no provider call).",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--credits",
        type=float,
        default=None,
        help="Also convert this many credits to USD.",
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    report = rate_report(args.profile, repo_root=args.repo_root)
    if args.credits is not None and report["usd_per_credit"] is not None:
        report["credits"] = args.credits
        report["cost_usd"] = round(args.credits * report["usd_per_credit"], 6)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["error"]:
        print(f"credit-rates: {report['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
