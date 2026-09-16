"""Competitor registry: a per-profile TOML config that market-harvest and
market-intelligence share.

The registry is the single place competitor names, tiers, watch URLs and Syften
filter mappings live. It is loaded by:

* ``market-harvest`` to drive ``vendor_watch`` + ``funding_and_ma`` and to emit a
  filter-budget report.
* ``market-intelligence`` §4b to iterate over tracked competitors — absence of
  movement renders as **"no movement in window"** instead of silence.
* ``market-scan`` §1B for its competitor watchlist.

**Security boundary (§R5):** fetched content may NEVER write this file. The update
loop is ``propose -> operator approves -> apply``. ``apply`` validates every row,
refuses non-HTTPS watch URLs, and refuses hosts that do not belong to one of the
operator-supplied ``domains``.

CLI::

    python -m gtm_core.voc.registry --profile P show [--product SLUG]
    python -m gtm_core.voc.registry --profile P apply --proposals PATH --accept NAME [NAME ...]
    python -m gtm_core.voc.registry --profile P budget [--product SLUG]

A row's optional ``product`` field scopes it to one of the profile's products
(``profiles/<profile>/products/<slug>/``); absent means the row applies to every
product. ``--product`` filters ``show``/``budget`` to that product's rows plus the
unscoped ones.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from ..paths import PathConfig, _safe_segment

SCHEMA_VERSION = 1

VALID_TIERS = frozenset({"direct", "nhi-native", "adjacent", "si-channel", "standards-body"})
VALID_STATUSES = frozenset({"active", "watch", "retired"})


def _today() -> str:
    return date.today().isoformat()


def load(*, profile: str, profiles_root: Path, product: str | None = None) -> dict:
    """Load and validate ``profiles/<profile>/knowledge/competitors.toml``.

    Returns ``{"schema": 1, "reviewed": "YYYY-MM-DD", "competitor": [...]}``.
    A missing file is treated as an empty registry so skills degrade gracefully.

    ``product``, when given, is validated as a safe path segment, must name a real
    ``profiles/<profile>/products/<product>/`` directory (a typo'd slug must not
    silently return "no rows" instead of an error), and filters the returned rows to
    those whose ``product`` is absent (applies to every product) or equals it.
    ``product=None`` skips that requested-product check and returns every row, but
    every row's OWN ``product`` (if it has one) is still checked against the same
    directory regardless of the ``product`` argument — an existing caller passing
    none now also raises on a row naming an unknown product.
    """
    if product is not None:
        product = _safe_segment(product, "product")
        product_dir = profiles_root / _safe_segment(profile, "profile") / "products" / product
        if not product_dir.is_dir():
            raise ValueError(f"unknown product '{product}': no such directory {product_dir}")
    path = profiles_root / _safe_segment(profile, "profile") / "knowledge" / "competitors.toml"
    if not path.is_file():
        return {"schema": SCHEMA_VERSION, "reviewed": _today(), "competitor": []}
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    validate(raw)
    _check_row_products(raw, profiles_root=profiles_root, profile=profile)
    if product is not None:
        raw = dict(raw)
        raw["competitor"] = [
            row for row in raw["competitor"] if row.get("product") in (None, product)
        ]
    return raw


def _check_row_products(data: dict, *, profiles_root: Path, profile: str) -> None:
    """Fail loudly if a row's ``product`` slug has no matching ``products/<slug>/``
    directory — a typo'd slug would otherwise silently hide a rival from the product
    it was meant to scope to. ``validate()`` already proved each ``product`` value is a
    safe segment by the time this runs."""
    base = profiles_root / _safe_segment(profile, "profile") / "products"
    for row in data.get("competitor", []):
        product = row.get("product")
        if product is None:
            continue
        if not (base / product).is_dir():
            raise ValueError(
                f"competitor '{row.get('name')}' has unknown product '{product}': "
                f"no such directory {base / product}"
            )


def validate(data: dict) -> None:
    """Validate registry schema. Raises ``ValueError`` with a clear message on failure."""
    if not isinstance(data, dict):
        raise ValueError("registry root must be a TOML table")
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"registry schema must be {SCHEMA_VERSION}")
    competitors = data.get("competitor")
    if not isinstance(competitors, list):
        raise ValueError("registry must contain a 'competitor' array-of-tables")
    seen: set[str] = set()
    for idx, row in enumerate(competitors):
        prefix = f"competitors[{idx}]"
        if not isinstance(row, dict):
            raise ValueError(f"{prefix} must be a table")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{prefix} 'name' is required and must be a non-empty string")
        if name in seen:
            raise ValueError(f"duplicate competitor name: {name}")
        seen.add(name)
        tier = row.get("tier")
        if tier not in VALID_TIERS:
            raise ValueError(
                f"{prefix} '{name}' has unknown tier '{tier}'; expected one of {sorted(VALID_TIERS)}"
            )
        status = row.get("status", "active")
        if status not in VALID_STATUSES:
            raise ValueError(
                f"{prefix} '{name}' has unknown status '{status}'; "
                f"expected one of {sorted(VALID_STATUSES)}"
            )
        product = row.get("product")
        if product is not None:
            if not isinstance(product, str) or not product.strip():
                raise ValueError(f"{prefix} '{name}' 'product' must be a non-empty string")
            _safe_segment(product, "product")
        for key in ("aliases", "watch_urls", "domains"):
            value = row.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{prefix} '{name}' '{key}' must be a list of strings")
        for url in row.get("watch_urls", []):
            parsed = urlparse(url)
            if parsed.scheme != "https":
                raise ValueError(f"{prefix} '{name}' watch_url must use https: {url}")
            if not parsed.hostname:
                raise ValueError(f"{prefix} '{name}' watch_url has no host: {url}")
        syften_filter = row.get("syften_filter", "")
        if not isinstance(syften_filter, str):
            raise ValueError(f"{prefix} '{name}' syften_filter must be a string")


def _host_matches_domain(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for d in domains:
        d = d.lower().rstrip(".")
        if host == d or host.endswith("." + d):
            return True
    return False


def propose(
    *,
    content_root: Path,
    profile: str,
    candidates: list[dict],
) -> Path:
    """Write candidate competitor additions to a data artifact under
    ``content/<profile>/market-signals/``.

    This function does NOT touch ``competitors.toml``.
    """
    prof = _safe_segment(profile, "profile")
    out_dir = content_root / prof / "market-signals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"registry-proposals-{_today()}.json"
    payload = {
        "proposed_at": _today(),
        "profile": prof,
        "candidates": candidates,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def apply(
    *,
    profiles_root: Path,
    profile: str,
    proposals_path: Path,
    accept: list[str],
) -> list[str]:
    """Append operator-approved competitor rows to the registry.

    ``accept`` is the list of candidate names the operator explicitly approved.
    Each accepted candidate is validated, its watch URLs are checked against the
    supplied ``domains``, and it is appended to ``competitors.toml`` with
    ``first_seen`` / ``last_reviewed`` stamped today.

    Returns the list of names actually appended (duplicates against the existing
    registry are skipped and reported).
    """
    prof = _safe_segment(profile, "profile")
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    candidates = {c["name"]: c for c in proposals.get("candidates", [])}

    registry_path = profiles_root / prof / "knowledge" / "competitors.toml"
    existing = load(profile=prof, profiles_root=profiles_root)
    existing_names = {c["name"] for c in existing.get("competitor", [])}

    appended: list[str] = []
    lines: list[str] = []
    today = _today()

    for name in accept:
        candidate = candidates.get(name)
        if candidate is None:
            raise ValueError(f"'{name}' not found in proposals {proposals_path}")
        validate({"schema": SCHEMA_VERSION, "competitor": [candidate]})
        _check_row_products({"competitor": [candidate]}, profiles_root=profiles_root, profile=prof)
        if name in existing_names:
            continue  # idempotent: already tracked

        domains = candidate.get("domains", [])
        if not domains:
            raise ValueError(f"'{name}' has no 'domains'; cannot validate watch URLs")
        for url in candidate.get("watch_urls", []):
            host = urlparse(url).hostname or ""
            if not _host_matches_domain(host, domains):
                raise ValueError(
                    f"'{name}' watch_url {url!r} host {host!r} is not under allowed domains {domains}"
                )

        row = {
            "name": candidate["name"],
            "tier": candidate["tier"],
            **({"product": candidate["product"]} if candidate.get("product") else {}),
            "aliases": candidate.get("aliases", []),
            "watch_urls": candidate.get("watch_urls", []),
            "domains": domains,
            "syften_filter": candidate.get("syften_filter", ""),
            "first_seen": today,
            "last_reviewed": today,
            "status": candidate.get("status", "active"),
            "note": candidate.get("note", ""),
        }
        lines.append("")
        lines.append("[[competitor]]")
        for key, value in row.items():
            if isinstance(value, list):
                items = ", ".join(json.dumps(v, ensure_ascii=False) for v in value)
                lines.append(f"{key} = [{items}]")
            elif isinstance(value, str):
                lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
            else:
                lines.append(f"{key} = {value}")
        appended.append(name)
        existing_names.add(name)

    if not appended:
        return appended

    if registry_path.is_file():
        text = registry_path.read_text(encoding="utf-8").rstrip("\n")
        # Update the global reviewed date while we have the file open.
        text = _update_reviewed_date(text, today)
    else:
        text = f'schema = {SCHEMA_VERSION}\nreviewed = "{today}"\n'

    registry_path.write_text(text + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return appended


def _label_appended(names: list[str], proposals_path: Path) -> list[str]:
    """Render each appended name with the product it was staged under, so the
    operator's approval surface (§R5: they approve by name) also shows the product a
    proposal rides in on, e.g. ``Clay [product-slug]`` / ``Apollo [all products]``."""
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    candidates = {c["name"]: c for c in proposals.get("candidates", [])}
    labels = []
    for name in names:
        product = candidates.get(name, {}).get("product")
        labels.append(f"{name} [{product}]" if product else f"{name} [all products]")
    return labels


def _update_reviewed_date(text: str, today: str) -> str:
    """Replace the top-level ``reviewed = "..."`` line, preserving comments."""
    out: list[str] = []
    replaced = False
    for line in text.splitlines():
        if not replaced and line.strip().startswith("reviewed ="):
            out.append(f'reviewed = "{today}"')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(1, f'reviewed = "{today}"')
    return "\n".join(out)


def filter_budget_report(*, registry: dict, syften_filters: dict) -> dict:
    """Report which registry competitors are covered by a Syften filter slot.

    ``syften_filters`` is the parsed ``knowledge/syften-filters.json`` object.
    A competitor is *covered* if its ``syften_filter`` value matches a ``$tag:``
    declared in one of the configured filter strings, or a category metadata value.
    Uncovered direct competitors are the highest-priority slots to fill.
    """
    filters = (syften_filters or {}).get("filters", {})
    known_keys = set(filters.keys())
    known_categories = {meta.get("category") for meta in filters.values() if isinstance(meta, dict)}
    known_tags: set[str] = set()
    tag_re = re.compile(r"\$tag:([^\s\"]+)")
    for key in known_keys:
        for m in tag_re.finditer(key):
            known_tags.add(m.group(1))

    covered: list[dict] = []
    uncovered: list[dict] = []
    for row in registry.get("competitor", []):
        sf = row.get("syften_filter", "")
        covered_by = None
        if sf:
            if sf in known_tags:
                covered_by = f"tag:{sf}"
            elif sf in known_categories:
                covered_by = f"category:{sf}"
        if covered_by:
            covered.append({"name": row["name"], "tier": row["tier"], "covered_by": covered_by})
        else:
            uncovered.append({"name": row["name"], "tier": row["tier"]})

    return {
        "total": len(registry.get("competitor", [])),
        "covered": covered,
        "uncovered": uncovered,
        "uncovered_direct": [u for u in uncovered if u["tier"] == "direct"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.registry",
        description="Manage the per-profile competitor registry.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    show_p = sub.add_parser("show", help="Print the validated registry as JSON.")
    show_p.add_argument(
        "--product", default=None, help="Filter to this product's rows plus unscoped ones."
    )
    budget = sub.add_parser("budget", help="Print the Syften filter-budget report.")
    budget.add_argument(
        "--product", default=None, help="Filter to this product's rows plus unscoped ones."
    )
    budget.add_argument("--syften-filters", type=Path, default=None)
    apply_p = sub.add_parser("apply", help="Approve proposed competitor additions.")
    apply_p.add_argument("--proposals", type=Path, required=True)
    apply_p.add_argument("--accept", nargs="+", required=True)

    args = parser.parse_args(argv)
    cfg = PathConfig.from_env(repo_root=args.repo_root)

    if args.cmd == "show":
        registry = load(profile=args.profile, profiles_root=cfg.profiles_root, product=args.product)
        print(json.dumps(registry, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "budget":
        registry = load(profile=args.profile, profiles_root=cfg.profiles_root, product=args.product)
        filters_path = args.syften_filters
        if not filters_path:
            filters_path = (
                cfg.profiles_root
                / _safe_segment(args.profile, "profile")
                / "knowledge"
                / "syften-filters.json"
            )
        syften = (
            json.loads(filters_path.read_text(encoding="utf-8")) if filters_path.is_file() else {}
        )
        print(
            json.dumps(
                filter_budget_report(registry=registry, syften_filters=syften),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "apply":
        appended = apply(
            profiles_root=cfg.profiles_root,
            profile=args.profile,
            proposals_path=args.proposals,
            accept=args.accept,
        )
        if appended:
            labels = _label_appended(appended, args.proposals)
            print(f"Appended {len(appended)} competitor(s): {', '.join(labels)}")
        else:
            print("No new competitors appended (all already tracked).")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
