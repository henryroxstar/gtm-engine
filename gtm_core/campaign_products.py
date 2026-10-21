"""Which product each sequence was sent for — the lookup outcomes need before they can be split.

Outcome tags carry ``seq:`` and nothing about the product, and the cell id is deliberately left
alone (changing it would rewrite every historical id). So the product of a reply is derived when
it is read: sequence → its ``cells.toml`` campaign → that campaign's product. This module owns the
second arrow and the check that every sequence has an answer.

**Where the product is declared, in order.** ``plans/campaign-products.toml`` first:

    [campaigns]              # campaign slug  -> product (slug or display name)
    sept-icp1-5-2026-09 = "agent-gateway"

    [sequences]              # for a sequence registered with no campaign at all
    "seq-example::seat::spec-v1" = "agent-gateway"

then a campaign manifest's own ``product`` field (``plans/campaigns/<slug>.campaign.toml``). The map
exists so a product can be recorded without writing a manifest: a manifest is a card on the
campaign rollup page, and eight empty cards to carry one field each would be noise on a page a
founder reads.

**Never a default.** A sequence with no answer is ``unknown`` and reported, never the default
product: attributing a reply to the wrong product is the silent error this whole chain exists to
prevent. A product that names nothing in ``PROFILE.md`` refuses, same as a run would.

CLI (read-only; writes nothing)::

    python -m gtm_core.campaign_products check --profile P [--json]

Exit 0 when every registered sequence resolves to a product, 1 when any does not (each gap is
listed), 2 when the map itself is unreadable or names an unknown product.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import run_scope
from .cells import load_cell_map
from .paths import resolve_content_root, resolve_profiles_root

MAP_FILE = "campaign-products.toml"
UNKNOWN = "unknown"


@dataclass
class Attribution:
    by_sequence: dict[str, str] = field(default_factory=dict)  # sequence id -> product slug
    gaps: list[str] = field(default_factory=list)  # one plain line per unresolved sequence


def _plans_dir(profile: str, content_root: Path | None) -> Path:
    return (content_root or resolve_content_root()) / profile / "plans"


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path.name} is unreadable: {exc}") from exc


def _manifest_products(profile: str, content_root: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted((_plans_dir(profile, content_root) / "campaigns").glob("*.campaign.toml")):
        doc = _read_toml(path)
        if doc.get("slug") and doc.get("product"):
            out[str(doc["slug"])] = str(doc["product"])
    return out


def attribute(
    profile: str, *, content_root: Path | None = None, profiles_root: Path | None = None
) -> Attribution:
    """Every ``cells.toml`` sequence mapped to a product slug, or listed as a gap.

    Raises ``ValueError`` when the map is unreadable or names a product ``PROFILE.md`` does not
    declare: a map that cannot be trusted is a stop, not a partial answer.
    """
    root = profiles_root or resolve_profiles_root()
    facts = run_scope._load_facts(profile, root)
    doc = _read_toml(_plans_dir(profile, content_root) / MAP_FILE)
    declared_campaigns, by_seq_declared = doc.get("campaigns") or {}, doc.get("sequences") or {}
    if not isinstance(declared_campaigns, dict) or not isinstance(by_seq_declared, dict):
        raise ValueError(f"{MAP_FILE}: [campaigns] and [sequences] must be tables")
    by_campaign = {**_manifest_products(profile, content_root), **declared_campaigns}

    def slug_of(value: object, where: str) -> str:
        try:
            return run_scope._match(facts, str(value))
        except run_scope.ScopeError as exc:
            raise ValueError(f"{where}: {exc}") from exc

    out = Attribution()
    for entry in load_cell_map(profile, content_root):
        seq = str(entry.get("sequence_id") or "")
        campaign = str(entry.get("campaign") or "")
        earlier = out.by_sequence.get(seq)
        if seq in by_seq_declared:
            product = slug_of(by_seq_declared[seq], f"[sequences] {seq}")
        elif campaign and campaign in by_campaign:
            product = slug_of(by_campaign[campaign], f"campaign {campaign}")
        else:
            product = UNKNOWN
        if earlier is not None and earlier != product:
            # A sequence registered twice under different campaigns: last-row-wins would credit
            # replies to whichever row happened to come last (fresh audit, B9).
            raise ValueError(
                f"{seq}: registered under campaigns that map to {earlier} and {product}"
            )
        out.by_sequence[seq] = product
        if product == UNKNOWN and earlier is None:
            why = (
                f"campaign {campaign!r} has no product (add it under [campaigns])"
                if campaign
                else "registered with no campaign (add it under [sequences])"
            )
            out.gaps.append(f"{seq}: {why}")
    return out


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gtm_core.campaign_products", description=__doc__.split("\n")[0]
    )
    sub = ap.add_subparsers(dest="verb", required=True)
    chk = sub.add_parser("check", help="every sequence resolves to a product, or list the gaps")
    chk.add_argument("--profile", required=True)
    chk.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        att = attribute(args.profile)
    except ValueError as exc:
        print(f"[campaign_products] {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for product in att.by_sequence.values():
        counts[product] = counts.get(product, 0) + 1
    if args.json:
        print(json.dumps({"by_product": counts, "gaps": att.gaps}, indent=2))
    else:
        for product, n in sorted(counts.items()):
            print(f"{product}: {n} sequence(s)")
        for gap in att.gaps:
            print(f"  gap: {gap}")
    return 1 if att.gaps else 0


if __name__ == "__main__":
    sys.exit(_cli())
