"""CLI + shared functions: derive a commercial quote from a profile's pricing pack.

A price in a proposal is DERIVED, never typed (docs/RULES.md §R14). The
``commercial-proposal`` skill runs this instead of doing arithmetic in prose, so the
monthly total, the term total, the year-1 total and the net position after any value
flowing back to the counterparty all come from one implementation reading one file:

    profiles/<profile>/knowledge/commercial/pricing.toml

VPS invocation:
    python -m gtm_core.commercial keys  --profile <p>
    python -m gtm_core.commercial quote --profile <p> --price-key <dotted.key> \
        [--units N] [--term-months M] [--price X --price-reason "..."] \
        [--outbound Y --outbound-label "..."] [--json]

It fails loudly (exit 2) rather than emit a confident wrong number: an unknown key, a
key that is not a price (an appliance count or a resource limit is numeric but is not
something to multiply by months), an override with no stated reason, outbound value
with no label, or a non-positive unit count or term.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from .paths import _safe_segment, resolve_profiles_root

PRICING_RELPATH = ("knowledge", "commercial", "pricing.toml")

#: Leaf names that hold a monthly price. Anything else numeric (appliance counts,
#: environment days, resource limits, GB allowances, revenue-share ratios) is not a price.
_PRICE_LEAVES = frozenset({"price_month", "price_month_from", "floor_month"})
_PRICE_SECTIONS = ("addons.",)


class QuoteError(ValueError):
    """A quote that cannot be derived honestly."""


@dataclass(frozen=True)
class Quote:
    price_key: str
    basis: str  # "list" | "from" (a starting price for a custom tier — not a firm figure)
    list_price: Decimal
    unit_price: Decimal
    price_reason: str | None
    units: int
    term_months: int
    monthly_total: Decimal
    term_total: Decimal
    year1_total: Decimal
    outbound: Decimal
    outbound_label: str | None
    net_year1: Decimal
    currency: str
    tax_note: str
    source: str
    as_of: str
    open_questions: list[str] = field(default_factory=list)

    @property
    def overridden(self) -> bool:
        return self.unit_price != self.list_price


def pricing_path(profiles_root: Path, profile: str) -> Path:
    return profiles_root.joinpath(_safe_segment(profile, "profile"), *PRICING_RELPATH)


def load_pricing(path: Path) -> dict:
    if not path.is_file():
        raise QuoteError(f"no pricing pack at {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _numeric_leaves(data: dict, prefix: str = "") -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for name, value in data.items():
        key = f"{prefix}{name}"
        if isinstance(value, dict):
            out.update(_numeric_leaves(value, f"{key}."))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = Decimal(str(value))
    return out


def is_price_key(key: str) -> bool:
    return key.rsplit(".", 1)[-1] in _PRICE_LEAVES or key.startswith(_PRICE_SECTIONS)


def price_keys(data: dict) -> dict[str, Decimal]:
    """Every monthly price in the pack, as dotted key -> amount."""
    return {k: v for k, v in _numeric_leaves(data).items() if is_price_key(k)}


def _require_positive_int(value: int, label: str) -> None:
    if value < 1:
        raise QuoteError(f"{label} must be at least 1 (got {value})")


def quote(
    data: dict,
    price_key: str,
    *,
    units: int = 1,
    term_months: int = 12,
    price: Decimal | None = None,
    price_reason: str | None = None,
    outbound: Decimal = Decimal(0),
    outbound_label: str | None = None,
) -> Quote:
    leaves = _numeric_leaves(data)
    if price_key not in leaves:
        raise QuoteError(f"unknown price key {price_key!r} — run `keys` to list them")
    if not is_price_key(price_key):
        raise QuoteError(f"{price_key!r} is numeric but is not a monthly price")
    _require_positive_int(units, "units")
    _require_positive_int(term_months, "term_months")

    list_price = leaves[price_key]
    unit_price = list_price
    if price is not None and price != list_price:
        if price <= 0:
            raise QuoteError(f"override price must be positive (got {price})")
        if not (price_reason or "").strip():
            raise QuoteError(f"overriding list {list_price} needs --price-reason")
        unit_price = price
    if outbound < 0:
        raise QuoteError(f"outbound value cannot be negative (got {outbound})")
    if outbound > 0 and not (outbound_label or "").strip():
        raise QuoteError("outbound value needs --outbound-label saying what it is")

    monthly = unit_price * units
    year1 = monthly * min(term_months, 12)
    meta = data.get("meta", {})
    return Quote(
        price_key=price_key,
        basis="from" if price_key.endswith("_from") else "list",
        list_price=list_price,
        unit_price=unit_price,
        price_reason=price_reason if unit_price != list_price else None,
        units=units,
        term_months=term_months,
        monthly_total=monthly,
        term_total=monthly * term_months,
        year1_total=year1,
        outbound=outbound,
        outbound_label=outbound_label if outbound > 0 else None,
        net_year1=year1 - outbound,
        currency=str(meta.get("currency", "")),
        tax_note=str(meta.get("tax_note", "")),
        source=str(meta.get("source", "")),
        as_of=str(meta.get("as_of", "")),
        open_questions=sorted(data.get("open_questions", {})),
    )


def _money(q: Quote, amount: Decimal) -> str:
    return f"{q.currency} {amount:,}".strip()


def render_text(q: Quote) -> str:
    price_line = f"{_money(q, q.unit_price)} / month"
    if q.overridden:
        price_line += f"   (OVERRIDE of list {q.list_price}: {q.price_reason})"
    if q.basis == "from":
        price_line += "   (FROM price — a starting point for a custom tier, not a firm quote)"
    lines = [
        f"Quote — {q.price_key}",
        f"  source          {q.source} (as of {q.as_of})",
        f"  unit price      {price_line}",
        f"  units           {q.units}",
        f"  monthly total   {_money(q, q.monthly_total)}",
        f"  term            {q.term_months} months",
        f"  term total      {_money(q, q.term_total)}",
        f"  year-1 total    {_money(q, q.year1_total)}",
    ]
    if q.outbound > 0:
        lines.append(f"  outbound value  {_money(q, q.outbound)}   ({q.outbound_label})")
        lines.append(
            f"  net, year 1     {_money(q, q.net_year1)}   (all outbound value counted in year 1)"
        )
        if q.year1_total > 0:
            ratio = (q.outbound / q.year1_total).quantize(Decimal("0.1"))
            lines.append(f"  outbound ÷ year-1 revenue   {ratio}×")
    if q.tax_note:
        lines.append(f"  {q.tax_note}")
    if q.open_questions:
        lines.append(f"open questions in the pack: {', '.join(q.open_questions)}")
    return "\n".join(lines)


def _jsonable(q: Quote) -> dict:
    out = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(q).items()}
    out["overridden"] = q.overridden
    return out


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gtm_core.commercial")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("keys", "quote"):
        sp = sub.add_parser(name)
        sp.add_argument("--profile", required=True)
        sp.add_argument("--profiles-root", type=Path, default=None)
        sp.add_argument("--json", action="store_true")
    q = sub.choices["quote"]
    q.add_argument("--price-key", required=True)
    q.add_argument("--units", type=int, default=1)
    q.add_argument("--term-months", type=int, default=12)
    q.add_argument("--price", type=Decimal, default=None)
    q.add_argument("--price-reason", default=None)
    q.add_argument("--outbound", type=Decimal, default=Decimal(0))
    q.add_argument("--outbound-label", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.profiles_root or resolve_profiles_root()
        data = load_pricing(pricing_path(root, args.profile))
        if args.cmd == "keys":
            keys = {k: str(v) for k, v in price_keys(data).items()}
            print(
                json.dumps(keys, indent=2)
                if args.json
                else "\n".join(f"{k} = {v}" for k, v in keys.items())
            )
            return 0
        result = quote(
            data,
            args.price_key,
            units=args.units,
            term_months=args.term_months,
            price=args.price,
            price_reason=args.price_reason,
            outbound=args.outbound,
            outbound_label=args.outbound_label,
        )
    except (QuoteError, ValueError) as exc:
        print(f"commercial: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_jsonable(result), indent=2) if args.json else render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
