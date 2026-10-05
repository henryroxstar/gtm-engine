from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from gtm_core.account_folder import AmbiguousFolder
from gtm_core.account_folder import resolve as resolve_account_folder
from gtm_core.knowledge_staging import live_path, stage, staged_path
from gtm_core.merge_hygiene import clean_company
from gtm_core.paths import _safe_segment, resolve_content_root, resolve_profiles_root

from .contracts import BusinessEvent, event_dedup_hash
from .providers import ZeroCostATSSweep, build_ats_query, get_provider

logger = logging.getLogger(__name__)

_TOKEN_WHITELIST = re.compile(r"^[a-zA-Z0-9_\-\.\s]{1,50}$")


def _log_invalid(signals_dir: Path, data: dict[str, Any]) -> None:
    try:
        with (signals_dir / "invalid_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
    except OSError:
        pass


def read_recent_events(
    content_root: Path,
    profile: str,
    today: datetime.date | None = None,
    days: int = 30,
) -> list[BusinessEvent]:
    prof = _safe_segment(profile, "profile")
    today = today or datetime.date.today()
    signals_dir = content_root / prof / "signals"
    if not signals_dir.exists():
        return []

    prior_date = today - datetime.timedelta(days=days)
    cur = prior_date.replace(day=1)
    end = today.replace(day=1)
    months: list[str] = []
    while cur <= end:
        months.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    events: list[BusinessEvent] = []
    cutoff_iso = prior_date.isoformat()
    for ym in months:
        path = signals_dir / f"events-{ym}.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("event_date", "") >= cutoff_iso:
                        events.append(BusinessEvent(**data))
                except Exception as err:
                    _log_invalid(signals_dir, {"error": str(err), "raw": line_str})
                    continue
    return events


def detect_clusters(events: list[BusinessEvent]) -> dict[str, dict[str, Any]]:
    counts: dict[str, list[BusinessEvent]] = {}
    for ev in events:
        key = clean_company(ev.company_name).strip().lower()
        if not key:
            continue
        counts.setdefault(key, []).append(ev)

    clusters: dict[str, dict[str, Any]] = {}
    for _key, ev_list in counts.items():
        # Deduplicate events in this group by event_dedup_hash
        seen_hashes: set[str] = set()
        distinct_events: list[BusinessEvent] = []
        for ev in ev_list:
            h = event_dedup_hash(ev)
            if h not in seen_hashes:
                seen_hashes.add(h)
                distinct_events.append(ev)

        if len(distinct_events) >= 2:
            first = distinct_events[0]
            combined_frameworks = list(
                dict.fromkeys(fw for e in distinct_events for fw in e.meta.get("frameworks", []))
            )

            latest_date = max(e.event_date for e in distinct_events)
            cluster_domain = next(
                (e.company_domain for e in distinct_events if e.company_domain),
                first.company_domain,
            )
            cluster_event = BusinessEvent(
                company_name=first.company_name,
                company_domain=cluster_domain,
                event_type="cluster_expansion",
                event_date=latest_date,
                headline=f"AI Hiring Expansion ({len(distinct_events)} roles)",
                snippet=f"Multiple AI roles opened: {', '.join(e.headline for e in distinct_events[:3])}",
                source_url=first.source_url,
                provider="cluster_stacking",
                meta={
                    "cluster_size": len(distinct_events),
                    "is_cluster": True,
                    "frameworks": combined_frameworks,
                    "roles": [e.headline for e in distinct_events],
                },
            )
            cluster_data = {
                "cluster_size": len(distinct_events),
                "cluster_event": cluster_event,
                "events": distinct_events,
            }
            primary_key = cluster_domain or clean_company(first.company_name).strip().lower()
            clusters[primary_key] = cluster_data
            clean_name_key = clean_company(first.company_name).strip().lower()
            if clean_name_key and clean_name_key != primary_key:
                clusters[clean_name_key] = cluster_data
    return clusters


def render_hook_preview(event: BusinessEvent) -> str:
    frameworks_str = (
        f" expanding its {', '.join(event.meta['frameworks'])} agent pipelines"
        if event.meta.get("frameworks")
        else " scaling autonomous workflows"
    )
    pain_str = f" and tackling {event.meta['pain_cue']}" if event.meta.get("pain_cue") else ""
    hook = (
        f"Saw {event.company_name} is{frameworks_str}{pain_str}—are you seeing delegation security "
        f"become a hurdle as you move out of sandbox pilots?"
    )

    return (
        f"Target: {event.company_name} ({event.source_url})\n"
        f"Role:   {event.headline}\n"
        f'Hook:   "{hook}"\n'
        f"[NOTE: Hook preview only. Full 5-slot message (claims, proof points, CTA, word limits) "
        f"is drafted downstream via draft-outreach during prospect Step 8 using active angles.toml]"
    )


def _stage_token_safely(
    content_root: Path,
    profiles_root: Path,
    profile: str,
    token: str,
) -> Path:
    prof = _safe_segment(profile, "profile")
    clean_token = token.strip()
    if not _TOKEN_WHITELIST.match(clean_token):
        raise ValueError(f"Invalid token format for staging: {token!r}")

    staged_file = staged_path(content_root, prof, "web-sweep.toml")
    live_file = live_path(profiles_root, prof, "web-sweep.toml")

    existing_vocab: list[str] = []
    header_comment = ""
    queries_block = '[queries]\nhiring = \'("AI" OR "Agent")\'\n'

    source_file = (
        staged_file if staged_file.is_file() else (live_file if live_file.is_file() else None)
    )
    if source_file is not None:
        raw_text = source_file.read_text(encoding="utf-8")
        try:
            parsed = tomllib.loads(raw_text)
            existing_vocab = [
                str(x).strip() for x in parsed.get("ai_vocabulary", []) if str(x).strip()
            ]
            if "ai_vocabulary" in raw_text:
                header_comment = raw_text[: raw_text.find("ai_vocabulary")]
            if "[queries]" in raw_text:
                queries_block = raw_text[raw_text.find("[queries]") :]
        except Exception:
            existing_vocab = []

    if clean_token in existing_vocab:
        existing_vocab.remove(clean_token)
    existing_vocab.insert(0, clean_token)

    vocab_lines = (
        "ai_vocabulary = [\n"
        + "".join(f"  {json.dumps(item)},\n" for item in existing_vocab)
        + "]\n\n"
    )
    new_toml = header_comment + vocab_lines + queries_block
    return stage(content_root, prof, "web-sweep.toml", new_toml)


def _load_sweep_tokens(
    content_root: Path, profiles_root: Path, profile: str, max_queries: int = 10
) -> list[str]:
    """Load active search tokens from staged or live web-sweep.toml, bounded by max_queries."""
    cand = staged_path(content_root, profile, "web-sweep.toml")
    if not cand.is_file():
        cand = live_path(profiles_root, profile, "web-sweep.toml")
    tokens: list[str] = []
    if cand.is_file():
        try:
            parsed = tomllib.loads(cand.read_text(encoding="utf-8"))
            tokens = [str(t).strip() for t in parsed.get("ai_vocabulary", []) if str(t).strip()]
        except Exception:
            tokens = []
    return (tokens or ["LangGraph", "CrewAI", "AI", "Agent"])[:max_queries]


def _load_existing_hashes(path: Path) -> set[str]:
    hashes: set[str] = set()
    if not path.exists():
        return hashes
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                try:
                    hashes.add(event_dedup_hash(BusinessEvent(**json.loads(line_str))))
                except Exception:  # nosec B112 — ignore malformed or unparseable event records
                    continue
    return hashes


def execute_sweep(
    profile: str,
    max_queries: int = 10,
    max_events: int = 50,
    hits: list[dict[str, Any]] | None = None,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    kind: str = "hiring",
) -> int:
    if os.environ.get("GTM_SIGNAL_SWEEP_ENABLED", "true").lower() in ("false", "0", "no"):
        logger.info("[SWEEP] Disabled by GTM_SIGNAL_SWEEP_ENABLED kill switch.")
        return 0

    prof = _safe_segment(profile, "profile")
    c_root = content_root or resolve_content_root()
    p_root = profiles_root or resolve_profiles_root()
    signals_dir = c_root / prof / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    accounts_dir = c_root / prof / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today()
    ledger_path = signals_dir / f"events-{today.strftime('%Y-%m')}.jsonl"
    unresolved_path = signals_dir / "unresolved_events.jsonl"

    provider = ZeroCostATSSweep() if hits is not None else get_provider(kind)
    logger.info(f"[SWEEP] Active signal provider: {provider.__class__.__name__}")
    tokens = _load_sweep_tokens(c_root, p_root, prof, max_queries=max_queries)

    events: list[BusinessEvent] = []
    if hits is not None:
        token_to_use = tokens[0] if tokens else kind
        clean_hits = []
        for h in hits:
            if not isinstance(h, dict):
                _log_invalid(
                    signals_dir,
                    {
                        "error": "malformed_hit",
                        "raw": str(h),
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    },
                )
                continue
            if h.get("http_status") in (404, 410, "404", "410"):
                _log_invalid(
                    signals_dir,
                    {
                        "error": f"expired_job_{h['http_status']}",
                        "url": h.get("url"),
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                    },
                )
                continue
            clean_hits.append(h)
        events = provider.fetch_events(
            token=token_to_use, limit=max_events, hits=clean_hits, profile=prof
        )
    else:
        for token in tokens[:max_queries]:
            if len(events) >= max_events or getattr(provider, "fallback_reason", None):
                break
            batch = provider.fetch_events(token=token, limit=max_events - len(events), profile=prof)
            events.extend(batch)

    fallback = getattr(provider, "fallback_reason", None)
    if fallback:
        note = " No hits provided (--hits-file); 0 events returned." if hits is None else ""
        logger.warning(
            f"[SWEEP] Signal provider fell back: Zero-Cost ATS Sweep (Fallback: {fallback}).{note}"
        )

    if not events:
        logger.warning("[SWEEP] Sweep produced 0 events. Check search provider or input terms.")

    existing_ledger_hashes = _load_existing_hashes(ledger_path)
    existing_unresolved_hashes = _load_existing_hashes(unresolved_path)

    for ev in events[:max_events]:
        try:
            _, rung = resolve_account_folder(
                company=ev.company_name,
                profile=prof,
                domain=ev.company_domain or "",
                content_root=c_root,
            )
            is_known = rung != "new"
        except (AmbiguousFolder, ValueError):
            is_known = False

        ev_hash = event_dedup_hash(ev)
        target_path, hashes = (
            (ledger_path, existing_ledger_hashes)
            if is_known
            else (unresolved_path, existing_unresolved_hashes)
        )
        if ev_hash not in hashes:
            hashes.add(ev_hash)
            with target_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev.__dict__) + "\n")

    return 0


def execute_probe(
    token: str,
    profile: str,
    limit: int = 10,
    non_interactive: bool = False,
    auto_yes: bool = False,
    hits: list[dict[str, str]] | None = None,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    kind: str = "jobs",
) -> tuple[int, str]:
    if os.environ.get("GTM_SIGNAL_PROBES_ENABLED", "true").lower() in ("false", "0", "no"):
        return 0, "[PROBE] Disabled by GTM_SIGNAL_PROBES_ENABLED kill switch."

    prof = _safe_segment(profile, "profile")
    c_root = content_root or resolve_content_root()
    p_root = profiles_root or resolve_profiles_root()
    signals_dir = c_root / prof / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    provider_kind = "hiring" if kind in ("jobs", "hiring") else kind
    provider = get_provider(provider_kind)
    events = provider.fetch_events(token=token, limit=limit, hits=hits, profile=prof)

    yield_count = len(events)
    precision = (yield_count / float(limit)) if limit > 0 else 0.0

    clean_token_display = (token[:20] + "...") if len(token) > 23 else token
    fallback = getattr(provider, "fallback_reason", None)
    if provider.__class__.__name__ == "TheirStackAccelerator" and not fallback:
        provider_name = "TheirStack Accelerator"
    elif fallback:
        provider_name = f"Zero-Cost ATS Sweep (Fallback: {fallback})"
    else:
        provider_name = "Zero-Cost ATS Sweep"
    hdr = f'[PROBE] Token: "{clean_token_display}" ({kind}) | Provider: {provider_name}'
    header_lines = (
        [hdr]
        if len(hdr) <= 80
        else [
            f'[PROBE] Token: "{clean_token_display}" ({kind})',
            f"[PROBE] Provider: {provider_name}"[:80],
        ]
    )

    output_lines = [
        *header_lines,
        "-" * 80,
        f"{'Company':<18} {'Status':<14} {'Snippet':<46}",
        "-" * 80,
    ]
    for ev in events[:5]:
        snippet_trunc = (ev.snippet[:43] + "...") if len(ev.snippet) > 46 else ev.snippet
        output_lines.append(f"{ev.company_name[:17]:<18} {'Target Buyer':<14} {snippet_trunc:<46}")
    output_lines.append("-" * 80)
    is_ats = provider.__class__.__name__ == "ZeroCostATSSweep"
    if hits is None and (is_ats or getattr(provider, "fallback_reason", None)):
        ats_query = build_ats_query(token)
        output_lines.append(f"[INFO] No hits provided. Run query:\n  {ats_query}")
    output_lines.append(
        f"Yield: {yield_count}/{limit} | Precision: {int(precision * 100)}% -> "
        f"{'PASS (>= 70%)' if precision >= 0.70 else 'FAIL (< 70%)'}"
    )

    if events:
        output_lines.append("")
        output_lines.append(render_hook_preview(events[0]))

    payload = {
        "token": token,
        "precision": precision,
        "yield": yield_count,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    with (signals_dir / "probe_results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

    if precision >= 0.70 and auto_yes:
        staged_p = _stage_token_safely(c_root, p_root, prof, token)
        output_lines.append(f"✓ Staged '{token}' to {staged_p}")

    full_output = "\n".join(output_lines)
    return 0, full_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Signal Events CLI (Probe & Sweep)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="Evaluate a candidate keyword on a single 10-item batch")
    p_probe.add_argument("--kind", choices=["jobs", "events"], default="jobs")
    p_probe.add_argument("--token", required=True)
    p_probe.add_argument("--profile", required=True)
    p_probe.add_argument("--limit", type=int, default=10)
    p_probe.add_argument("--non-interactive", action="store_true")
    p_probe.add_argument("--yes", action="store_true")
    p_probe.add_argument(
        "--hits-file",
        default=None,
        help="Path to JSON file containing search hits (or '-' for stdin)",
    )

    p_sweep = sub.add_parser("sweep", help="Execute production signal sweep")
    p_sweep.add_argument("--profile", required=True)
    p_sweep.add_argument("--kind", default="hiring")
    p_sweep.add_argument("--max-queries", type=int, default=10)
    p_sweep.add_argument("--max-events", type=int, default=50)
    p_sweep.add_argument(
        "--hits-file",
        default=None,
        help="Path to JSON file containing search hits (or '-' for stdin)",
    )

    args = parser.parse_args(argv)
    hits = None
    if getattr(args, "hits_file", None):
        hits = (
            json.load(sys.stdin)
            if args.hits_file == "-"
            else json.loads(Path(args.hits_file).read_text(encoding="utf-8"))
        )

    try:
        if args.command == "probe":
            code, out = execute_probe(
                token=args.token,
                profile=args.profile,
                limit=args.limit,
                non_interactive=args.non_interactive,
                auto_yes=args.yes,
                hits=hits,
                kind=args.kind,
            )
            print(out)
            return code
        elif args.command == "sweep":
            return execute_sweep(
                profile=args.profile,
                max_queries=args.max_queries,
                max_events=args.max_events,
                hits=hits,
                kind=args.kind,
            )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
