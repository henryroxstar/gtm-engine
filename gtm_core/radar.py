"""content-radar core — deterministic dedupe + cluster + score + digest (spec §7.1).

This is the *numeric* half of content-radar: pure, stdlib-only, no LLM and no
SDK. The brain (the ``content-radar`` SKILL) shells out to this for reproducible
clustering + 0-100 scoring, then layers DeepSeek summaries (worker MCP) and Claude
ranking/narrative on top. Keeping scoring here — not in the prompt — is what makes
the Phase-1 golden-path test possible and keeps the ledgered ``score`` reproducible.

Pipeline:
  rows (discovery_items) → news_from_rows → dedupe(vs history) → cluster_and_score
  → StoryCluster[]  (+ render_digest for the human-facing brief)

VPS invocation:   python -m gtm_core.radar --rows rows.json --profile template
Local invocation: python "$CLAUDE_PLUGIN_ROOT/lib/gtm_core/radar.py" --rows rows.json --profile template
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

# --- scoring weights (tunable; documented so the rubric is auditable) -------- #
W_TRENDING = 0.55
W_FIT = 0.45
W_PILLAR = 0.60
W_RELEVANCE = 0.40
_PLATFORM_X_THRESHOLD = 70
_TITLE_DUP_JACCARD = 0.6

_RELEVANCE_TERMS = {"ai", "agent", "agents", "agentic", "llm", "mcp", "model", "autonomous"}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PILLARS_RE = re.compile(r"^content_pillars:\s*\[(.*?)\]\s*$", re.MULTILINE)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _item_text(item: dict) -> str:
    topics = " ".join(item.get("topics", []) or [])
    return f"{item.get('title', '')} {item.get('summary', '')} {topics}"


def news_from_rows(rows: list[dict]) -> list[dict]:
    """Normalise raw ``discovery_items`` rows into NewsItem-shaped dicts."""
    out: list[dict] = []
    for r in rows:
        title = r.get("title") or r.get("item_name") or r.get("name")
        rid = r.get("id") or r.get("discovery_id")
        if not title or not rid:
            continue
        out.append(
            {
                "id": str(rid),
                "title": str(title),
                "url": r.get("url") or r.get("source_url") or "",
                "source": r.get("source") or r.get("source_name") or "",
                "summary": r.get("summary") or "",
                "topics": list(r.get("topics") or []),
                "trending_score": _as_float(r.get("trending_score"), 0.0),
                "published_at": r.get("published_at") or "",
            }
        )
    return out


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def seen_ids_from_history(history_path: Path) -> set[str]:
    """Collect already-used NewsItem ids from a ``history.jsonl`` ledger."""
    seen: set[str] = set()
    if not history_path.is_file():
        return seen
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for sid in rec.get("source_items", []) or []:
            if isinstance(sid, str):
                seen.add(sid)
        for key in ("news_id", "item_id"):
            val = rec.get(key)
            if isinstance(val, str):
                seen.add(val)
    return seen


def dedupe(items: list[dict], seen_ids: set[str]) -> list[dict]:
    return [it for it in items if it["id"] not in seen_ids]


def _pillar_fit(item_tokens: set[str], pillars: list[str]) -> tuple[str, float]:
    pillar_token_sets = [_tokens(p) for p in pillars]
    shared_across_all = (
        set.intersection(*pillar_token_sets) if len(pillar_token_sets) > 1 else set()
    )

    best_pillar = pillars[0]
    best_fit = 0.0
    for pillar, terms in zip(pillars, pillar_token_sets, strict=False):
        discriminating = terms - shared_across_all or terms
        hits = len(discriminating & item_tokens)
        fit = hits / len(discriminating)
        if fit > best_fit:
            best_fit = fit
            best_pillar = pillar
    return best_pillar, best_fit


def _relevance(item_tokens: set[str]) -> float:
    hits = len(_RELEVANCE_TERMS & item_tokens)
    return min(1.0, hits / 2.0)


def _short_title(title: str, words: int = 8) -> str:
    parts = title.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def cluster_and_score(items: list[dict], pillars: list[str]) -> list[dict]:
    """Cluster items by story + best-fit pillar and score each cluster 0-100."""
    if not items or not pillars:
        return []

    max_trending = max((it.get("trending_score", 0.0) for it in items), default=0.0)

    scored: list[dict] = []
    for it in items:
        toks = _tokens(_item_text(it))
        pillar, fit = _pillar_fit(toks, pillars)
        rel = _relevance(toks)
        norm_trending = (it.get("trending_score", 0.0) / max_trending) if max_trending else 0.0
        pillar_blend = W_PILLAR * fit + W_RELEVANCE * rel
        raw = 100.0 * (W_TRENDING * norm_trending + W_FIT * pillar_blend)
        score = max(0, min(100, round(raw)))
        scored.append(
            {
                "item": it,
                "pillar": pillar,
                "score": score,
                "title_tokens": _tokens(it["title"]),
            }
        )

    clusters: list[dict] = []
    for rec in scored:
        merged = False
        for cl in clusters:
            if cl["pillar"] != rec["pillar"]:
                continue
            if _jaccard(cl["title_tokens"], rec["title_tokens"]) >= _TITLE_DUP_JACCARD:
                cl["members"].append(rec["item"])
                cl["score"] = max(cl["score"], rec["score"])
                cl["title_tokens"] |= rec["title_tokens"]
                merged = True
                break
        if not merged:
            clusters.append(
                {
                    "pillar": rec["pillar"],
                    "score": rec["score"],
                    "members": [rec["item"]],
                    "title_tokens": set(rec["title_tokens"]),
                }
            )

    clusters.sort(key=lambda c: (-c["score"], c["members"][0]["title"]))

    out: list[dict] = []
    for i, cl in enumerate(clusters, start=1):
        lead = cl["members"][0]
        pillar = cl["pillar"]
        score = cl["score"]
        platform_fit = ["linkedin", "x"] if score >= _PLATFORM_X_THRESHOLD else ["linkedin"]
        out.append(
            {
                "id": f"cluster-{i:03d}",
                "pillar": pillar,
                "score": score,
                "why_it_matters": (
                    f'{pillar} signal: "{_short_title(lead["title"])}" is trending '
                    f"(discovery score {int(lead.get('trending_score', 0))}). "
                    f"On-narrative for the {pillar} pillar."
                ),
                "angle_seeds": [
                    f'What "{_short_title(lead["title"], 6)}" means for builders',
                    f"The {pillar} angle others are missing",
                ],
                "platform_fit": platform_fit,
                "source_items": [m["id"] for m in cl["members"]],
            }
        )
    return out


def load_content_signals(path: Path) -> list[dict]:
    """Read the content-radar handoff file. Missing or unreadable file → empty list.

    Each item must carry at least ``signal_id``, ``pillar``, and ``angle``;
    ``verified`` content signals are the ones content-radar should surface.

    Lives here rather than in ``gtm_core.voc`` (which produces the handoff file, via
    ``market-intelligence``): this reader has no VOC-pipeline dependency of its own, and
    ``market-intelligence`` is a private/stubbed skill in the OSS carve while
    ``content-radar`` — the only consumer of this function — ships publicly. A module-level
    import of ``gtm_core.voc`` here previously broke `python -m gtm_core.radar` entirely
    wherever ``gtm_core.voc`` doesn't exist.
    """
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        if not all(
            isinstance(raw.get(k), str) and raw.get(k) for k in ("signal_id", "pillar", "angle")
        ):
            continue
        out.append(
            {
                "signal_id": str(raw["signal_id"]),
                "pillar": str(raw["pillar"]),
                "angle": str(raw["angle"]),
                "title": str(raw["title"]) if isinstance(raw.get("title"), str) else "",
                "url": str(raw["url"]) if isinstance(raw.get("url"), str) else "",
                "evidence_ids": [str(x) for x in raw.get("evidence_ids", []) if isinstance(x, str)],
                "verified": bool(raw.get("verified", False)),
                "decay_days": int(raw.get("decay_days", 30)),
            }
        )
    return out


def content_signal_clusters(path: Path, pillars: list[str], seen_ids: set[str]) -> list[dict]:
    """Convert verified market-intelligence content signals into high-priority clusters.

    Each verified signal becomes its own cluster with a pinned score of 100 so it
    surfaces ahead of news-derived clusters without altering their computed scores.
    Unverified signals and signals already logged in history are ignored.
    """
    signals = load_content_signals(path)
    clusters: list[dict] = []
    default_pillar = pillars[0] if pillars else ""
    for i, sig in enumerate(signals, start=1):
        if not sig.get("verified"):
            continue
        sid = sig.get("signal_id")
        if not sid or sid in seen_ids:
            continue
        pillar = sig.get("pillar") or default_pillar
        title = sig.get("title") or sid
        angle = sig.get("angle", "")
        clusters.append(
            {
                "id": f"content-signal-{i:03d}",
                "pillar": pillar,
                "score": 100,
                "why_it_matters": (f"{pillar} signal: {title}" + (f" — {angle}" if angle else "")),
                "angle_seeds": [angle] if angle else [f"The {pillar} angle to watch"],
                "platform_fit": ["linkedin", "x"],
                "source_items": [sid],
            }
        )
    return clusters


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def render_digest(
    clusters: list[dict],
    items: list[dict],
    date_str: str,
    profile: str,
    content_signals: list[dict] | None = None,
) -> str:
    """Render the human-facing radar digest markdown."""
    by_id = {it["id"]: it for it in items}
    cs_by_id = {cs["signal_id"]: cs for cs in (content_signals or [])}
    lines: list[str] = []
    lines.append(f"# Content Radar — {profile} — {date_str}")
    lines.append("")
    if not clusters:
        lines.append("_No fresh, on-narrative stories this run._")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"{len(clusters)} story cluster(s), ranked by score (0–100).")
    lines.append("")
    for cl in clusters:
        lines.append(f"## {cl['score']} · {cl['pillar']} — {cl['id']}")
        lines.append("")
        lines.append(cl.get("why_it_matters", ""))
        lines.append("")
        if cl.get("angle_seeds"):
            lines.append("**Angles:**")
            for seed in cl["angle_seeds"]:
                lines.append(f"- {seed}")
            lines.append("")
        lines.append("**Sources:**")
        for sid in cl["source_items"]:
            it = by_id.get(sid)
            if it:
                url = it.get("url") or ""
                title = it.get("title", sid)
                lines.append(f"- [{title}]({url})" if url else f"- {title}")
                continue
            cs = cs_by_id.get(sid)
            if cs:
                url = cs.get("url") or ""
                title = cs.get("title") or sid
                lines.append(f"- [{title}]({url})" if url else f"- {title}")
                continue
            lines.append(f"- {sid}")
        lines.append("")
    return "\n".join(lines)


def load_pillars(profiles_root: Path, profile: str) -> list[str]:
    """Read ``content_pillars: [...]`` from ``profiles/<profile>/PROFILE.md``."""
    path = profiles_root / profile / "PROFILE.md"
    if not path.is_file():
        return []
    m = _PILLARS_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        return []
    raw = m.group(1)
    return [p.strip().strip("'\"") for p in raw.split(",") if p.strip()]


def _load_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("rows", []))
    return list(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.radar",
        description="content-radar core: dedupe + cluster + score discovery_items rows.",
    )
    parser.add_argument(
        "--rows", type=Path, required=True, help="JSON file of discovery_items rows."
    )
    parser.add_argument(
        "--profile", default=None, help="Profile (default: ACTIVE_PROFILE / 'template')."
    )
    parser.add_argument(
        "--history", type=Path, default=None, help="history.jsonl to dedupe against."
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Output dir (default: content/<profile>/radar)."
    )
    parser.add_argument("--date", default=None, help="Digest date YYYY-MM-DD.")
    parser.add_argument(
        "--content-signals",
        type=Path,
        default=None,
        help="market-intelligence content-signals-<date>.json to merge as high-priority clusters.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    args = parser.parse_args(argv)

    from .paths import PathConfig

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    profile = args.profile or cfg.default_profile

    pillars = load_pillars(cfg.profiles_root, profile)
    if not pillars:
        print(f"[radar] no content_pillars in profiles/{profile}/PROFILE.md", file=sys.stderr)
        return 1

    items = news_from_rows(_load_rows(args.rows))
    history_path = args.history or (cfg.content_root / profile / "history.jsonl")
    seen = seen_ids_from_history(history_path)
    items = dedupe(items, seen)
    news_clusters = cluster_and_score(items, pillars)

    date_str = args.date or _derive_date(items) or "undated"
    cs_path = args.content_signals or _default_content_signals_path(
        cfg.content_root, profile, date_str
    )
    content_clusters = content_signal_clusters(cs_path, pillars, seen)
    clusters = content_clusters + news_clusters

    content_signals = load_content_signals(cs_path) if cs_path.is_file() else []
    digest = render_digest(clusters, items, date_str, profile, content_signals=content_signals)

    out_dir = args.out or (cfg.content_root / profile / "radar")
    out_dir.mkdir(parents=True, exist_ok=True)
    digest_path = out_dir / f"{date_str}-digest.md"
    clusters_path = out_dir / f"{date_str}-clusters.json"
    digest_path.write_text(digest, encoding="utf-8")
    clusters_path.write_text(json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {"digest": str(digest_path), "clusters_file": str(clusters_path), "clusters": clusters},
            ensure_ascii=False,
        )
    )
    return 0


def _default_content_signals_path(content_root: Path, profile: str, date_str: str) -> Path:
    """Default path for the market-intelligence content-signals handoff file."""
    try:
        as_of = date.fromisoformat(date_str)
    except ValueError:
        as_of = date.today()
    return (
        content_root
        / profile
        / "plans"
        / "market-intelligence"
        / f"content-signals-{as_of.isoformat()}.json"
    )


def _derive_date(items: list[dict]) -> str | None:
    dates = sorted(
        (it.get("published_at", "")[:10] for it in items if it.get("published_at")), reverse=True
    )
    return dates[0] if dates else None


if __name__ == "__main__":
    raise SystemExit(main())
