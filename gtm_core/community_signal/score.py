"""Deterministic scoring of raw Syften match pulls → the quantitative signal-model fields.

The numbers are computed here, in code, from Syften's **server-assigned** structured fields
only — ``filter`` (which configured filter matched), ``analysis.accept`` (Syften's own AI
verdict), and ``item.backend`` (the platform). None of those are author-controlled: a match's
post *text/title/author* are never read for a metric. So injected free-text in a match body
cannot move a count — the §R5 "metrics in code, not model narration" guarantee, enforced by
``tests/community_signal/test_scoring.py``.

Only the *bucketing labels* (which entity / category a filter belongs to) come from an optional
caller-supplied ``mapping`` — the brain builds that from the Syften filter config ($brand/$tag)
using judgment. The counts stay deterministic regardless of the labels.

CLI::

    python -m gtm_core.community_signal.score --map map.json --out metrics.json raw1.json [raw2.json …]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_OTHER = "other"
_TOP_ENTITIES = 12


def _verdict(match: dict) -> str:
    analysis = match.get("analysis")
    accept = analysis.get("accept") if isinstance(analysis, dict) else None
    if accept is True:
        return "accepted"
    if accept is False:
        return "rejected"
    return "unscored"


def _relevant(match: dict) -> bool:
    """Delivered-as-relevant = not AI-rejected (Syften: 'accepted' includes matches where AI
    filtering did not run)."""
    return _verdict(match) != "rejected"


def _bucket(filter_string: str, mapping: dict[str, dict]) -> tuple[str, str]:
    """(entity, category) for a filter string. Falls back to (filter, 'other')."""
    entry = mapping.get(filter_string) if isinstance(mapping, dict) else None
    if isinstance(entry, dict):
        entity = str(entry.get("entity") or filter_string or "(unlabelled)")
        category = str(entry.get("category") or _OTHER)
        return entity, category
    return (filter_string or "(unlabelled)"), _OTHER


def _sorted_counts(counts: dict[str, float]) -> list[tuple[str, float]]:
    """Deterministic: descending count, then name ascending for stable ties."""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def score_pulls(pulls: list[list[dict]], mapping: dict[str, dict] | None = None) -> dict:
    """Aggregate one or more raw match pulls (ordered oldest→newest) into metric fields.

    Returns a partial signal model: ``kpis``, ``categories``, ``share_of_voice``,
    ``platforms``, ``momentum`` (only when >1 pull), plus a ``per_filter`` noise table and
    ``totals`` — the qualitative sections (bluf/signals/plays/filter_suggestions) are added
    by the caller.
    """
    mapping = mapping or {}
    merged: list[dict] = [m for pull in pulls for m in pull if isinstance(m, dict)]

    per_filter: dict[str, dict[str, int]] = {}
    entity_counts: dict[str, float] = {}
    entity_category: dict[str, str] = {}
    category_counts: dict[str, float] = {}
    backend_counts: dict[str, float] = {}
    accepted = rejected = unscored = 0

    for m in merged:
        flt = str(m.get("filter", "") or "(unlabelled)")
        verdict = _verdict(m)
        pf = per_filter.setdefault(flt, {"accepted": 0, "rejected": 0, "unscored": 0, "total": 0})
        pf[verdict] += 1
        pf["total"] += 1
        if verdict == "accepted":
            accepted += 1
        elif verdict == "rejected":
            rejected += 1
        else:
            unscored += 1
        if not _relevant(m):
            continue
        entity, category = _bucket(flt, mapping)
        entity_counts[entity] = entity_counts.get(entity, 0) + 1
        entity_category.setdefault(entity, category)
        category_counts[category] = category_counts.get(category, 0) + 1
        item = m.get("item") if isinstance(m.get("item"), dict) else {}
        backend = str(item.get("backend", "") or "unknown")
        backend_counts[backend] = backend_counts.get(backend, 0) + 1

    # Add a noise_pct to each per-filter row.
    for pf in per_filter.values():
        total = pf["total"] or 1
        pf["noise_pct"] = round(pf["rejected"] / total * 100, 1)

    # categories (preserve a stable, count-desc order so palette assignment is deterministic).
    categories = [
        {"key": key, "label": key.replace("-", " ").replace("_", " ").title(), "count": int(count)}
        for key, count in _sorted_counts(category_counts)
    ]

    # share of voice — top N entities, remainder folded into "Other".
    ranked = _sorted_counts(entity_counts)
    share_of_voice = [
        {"name": name, "category": entity_category.get(name, _OTHER), "value": int(count)}
        for name, count in ranked[:_TOP_ENTITIES]
    ]
    tail = sum(count for _, count in ranked[_TOP_ENTITIES:])
    if tail:
        share_of_voice.append({"name": "Other", "category": _OTHER, "value": int(tail)})

    platforms = [
        {"name": name, "value": int(count)} for name, count in _sorted_counts(backend_counts)
    ]

    momentum: list[dict] = []
    if len(pulls) > 1:
        top_entities = [name for name, _ in ranked[:6]]
        for name in top_entities:
            series = []
            for pull in pulls:
                c = 0
                for m in pull:
                    if not isinstance(m, dict) or not _relevant(m):
                        continue
                    ent, _cat = _bucket(str(m.get("filter", "") or ""), mapping)
                    if ent == name:
                        c += 1
                series.append(c)
            momentum.append({"name": name, "series": series})

    total_relevant = accepted + unscored
    overall_noise = round(rejected / (len(merged) or 1) * 100, 1)
    kpis = [
        {"val": len(merged), "label": "matches pulled", "foot": f"{len(pulls)} pull(s)"},
        {"val": total_relevant, "label": "delivered as relevant", "accent": True},
        {"val": f"{overall_noise}%", "label": "noise (AI-rejected)"},
        {"val": len(entity_counts), "label": "entities tracked"},
        {"val": len(category_counts), "label": "categories"},
    ]

    return {
        "kpis": kpis,
        "categories": categories,
        "share_of_voice": share_of_voice,
        "platforms": platforms,
        "momentum": momentum,
        "per_filter": per_filter,
        "totals": {
            "raw": len(merged),
            "relevant": total_relevant,
            "accepted": accepted,
            "rejected": rejected,
            "unscored": unscored,
            "noise_pct": overall_noise,
            "pulls": len(pulls),
        },
    }


def _load_pull(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of matches")
    return [m for m in data if isinstance(m, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.community_signal.score",
        description="Aggregate raw Syften match pulls into quantitative signal-model fields.",
    )
    parser.add_argument("pulls", nargs="+", help="Raw pull JSON files, oldest → newest.")
    parser.add_argument(
        "--map",
        help="Optional JSON mapping {filter_string: {entity, category}} for bucketing.",
    )
    parser.add_argument("--out", help="Write the metrics JSON here (default: stdout).")
    args = parser.parse_args(argv)

    mapping: dict[str, Any] = {}
    if args.map:
        mapping = json.loads(Path(args.map).read_text(encoding="utf-8"))
    pulls = [_load_pull(p) for p in args.pulls]
    metrics = score_pulls(pulls, mapping)
    out_text = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out_text, encoding="utf-8")
        print(args.out)
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
