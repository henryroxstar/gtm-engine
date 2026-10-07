from __future__ import annotations

import datetime
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from gtm_core.merge_hygiene import clean_company
from gtm_core.signal_events.contracts import BusinessEvent, event_dedup_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KineticChainRule:
    name: str
    events: tuple[str, ...]
    max_duration_days: int
    description: str = ""


@dataclass(frozen=True)
class KineticChainMatch:
    chain_name: str
    company_name: str
    company_domain: str | None
    matched_events: tuple[BusinessEvent, ...]
    compound_why_now: str
    heat_boost: int = 3


def load_kinetic_chains_config(config_path: Path | None) -> dict[str, KineticChainRule]:
    """Load kinetic chains configuration from TOML with safe degradation.

    If file is absent or empty, returns an empty dict. Malformed blocks are
    logged and skipped to prevent halting execution. Zero network dependencies.
    """
    if config_path is None or not config_path.is_file():
        return {}

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        if not raw_text.strip():
            return {}
        data = tomllib.loads(raw_text)
    except Exception as exc:
        logger.warning("Failed to parse kinetic chains config %s: %s", config_path, exc)
        return {}

    chains_raw = data.get("chains")
    if not isinstance(chains_raw, dict):
        return {}

    rules: dict[str, KineticChainRule] = {}
    for name, block in chains_raw.items():
        if not isinstance(block, dict):
            logger.warning("Skipping invalid chain block %r: not a dictionary", name)
            continue
        events = block.get("events")
        if not isinstance(events, list):
            logger.warning(
                "Skipping chain %r: 'events' must be a list of at least 2 event types", name
            )
            continue
        clean_events = tuple(str(e).strip() for e in events if str(e).strip())
        if len(clean_events) < 2:
            logger.warning(
                "Skipping chain %r: 'events' must contain at least 2 non-empty event types", name
            )
            continue
        try:
            max_days = int(block.get("max_duration_days", 90))
            if max_days <= 0:
                raise ValueError("max_duration_days must be positive")
        except (ValueError, TypeError):
            logger.warning("Skipping chain %r: invalid max_duration_days", name)
            continue

        rules[str(name)] = KineticChainRule(
            name=str(name),
            events=clean_events,
            max_duration_days=max_days,
            description=str(block.get("description", "")),
        )

    return rules


def _format_event_label(ev: BusinessEvent) -> str:
    date_part = f"({ev.event_date})"
    if ev.event_type == "cluster_expansion":
        return f"AI Hiring Expansion {date_part}"
    if ev.event_type == "regulatory":
        return f"Regulatory Action {date_part}"
    if ev.event_type == "hiring":
        role = ev.meta.get("job_title") or ev.headline
        return f"Hired {role} {date_part}"
    return f"{ev.event_type.capitalize()} {date_part}"


def format_compound_why_now(
    chain_name: str,
    matched_events: list[BusinessEvent] | tuple[BusinessEvent, ...],
) -> str:
    labels = " + ".join(_format_event_label(e) for e in matched_events)
    return f"Kinetic Trigger ({chain_name}): {labels}"


def _parse_event_date(date_val: str | None) -> datetime.date | None:
    if not date_val:
        return None
    try:
        return datetime.date.fromisoformat(str(date_val)[:10])
    except (ValueError, TypeError):
        return None


def _match_chain_sequence(
    target_events: tuple[str, ...],
    account_events: list[BusinessEvent],
    max_duration_days: int,
) -> tuple[BusinessEvent, ...] | None:
    """Find a sequence of events matching target_events types in chronological order.

    Ensures date(E_1) <= date(E_2) <= ... <= date(E_n) and
    (date(E_n) - date(E_1)).days <= max_duration_days.
    Safely ignores events with invalid dates.
    """
    valid_events: list[tuple[BusinessEvent, datetime.date]] = []
    for e in account_events:
        d = _parse_event_date(e.event_date)
        if d is not None:
            valid_events.append((e, d))

    sorted_events = sorted(valid_events, key=lambda pair: pair[1])

    def _backtrack(
        event_idx: int, matched: list[tuple[BusinessEvent, datetime.date]]
    ) -> list[BusinessEvent] | None:
        if len(matched) == len(target_events):
            first_d = matched[0][1]
            last_d = matched[-1][1]
            if (last_d - first_d).days <= max_duration_days:
                return [p[0] for p in matched]
            return None

        wanted_type = target_events[len(matched)]
        for i in range(event_idx, len(sorted_events)):
            cand_ev, cand_d = sorted_events[i]
            cand_type = cand_ev.event_type
            type_match = (
                (cand_type == wanted_type)
                or (wanted_type == "hiring_cluster" and cand_type == "cluster_expansion")
                or (wanted_type == "cluster_expansion" and cand_type == "hiring_cluster")
            )
            if type_match:
                if matched:
                    # Enforce chronological ordering T_prev <= T_curr (same-day permitted)
                    if cand_d < matched[-1][1]:
                        continue
                res = _backtrack(i + 1, matched + [(cand_ev, cand_d)])
                if res is not None:
                    return res
        return None

    res = _backtrack(0, [])
    return tuple(res) if res is not None else None


def detect_kinetic_chains(
    events: list[BusinessEvent],
    config: dict[str, KineticChainRule] | None = None,
) -> list[KineticChainMatch]:
    """Evaluate sequences of structural events against configured kinetic chain rules.

    Normalizes company aliases and domains with a two-pass resolution map (PRD §2 Step 2).
    Deduplicates events per account to prevent duplicate ledger artifacts.
    Returns all satisfied chain matches.
    """
    if not events or not config:
        return []

    # Pass 1: Build alias-to-canonical-domain map across all events
    alias_to_domain: dict[str, str] = {}
    for ev in events:
        domain = (ev.company_domain or "").strip().lower()
        alias = clean_company(ev.company_name).strip().lower()
        if domain and alias:
            alias_to_domain[alias] = domain

    # Pass 2: Group events by canonical identity
    grouped: dict[str, list[BusinessEvent]] = {}
    for ev in events:
        domain = (ev.company_domain or "").strip().lower()
        alias = clean_company(ev.company_name).strip().lower()
        key = domain or alias_to_domain.get(alias) or alias
        if not key:
            continue
        grouped.setdefault(key, []).append(ev)

    matches: list[KineticChainMatch] = []
    for _id_key, acct_events in grouped.items():
        # Deduplicate events in this group by event_dedup_hash
        seen_hashes: set[str] = set()
        deduped_events: list[BusinessEvent] = []
        for ev in acct_events:
            h = event_dedup_hash(ev)
            if h not in seen_hashes:
                seen_hashes.add(h)
                deduped_events.append(ev)

        if not deduped_events:
            continue

        sample_ev = deduped_events[0]
        # Resolve best known domain
        domain = next((e.company_domain for e in deduped_events if e.company_domain), None)
        if not domain:
            domain = alias_to_domain.get(clean_company(sample_ev.company_name).strip().lower())
        comp_name = sample_ev.company_name

        for rule in config.values():
            matched_seq = _match_chain_sequence(rule.events, deduped_events, rule.max_duration_days)
            if matched_seq is not None:
                matches.append(
                    KineticChainMatch(
                        chain_name=rule.name,
                        company_name=comp_name,
                        company_domain=domain,
                        matched_events=matched_seq,
                        compound_why_now=format_compound_why_now(rule.name, matched_seq),
                        heat_boost=3,
                    )
                )

    return matches


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


def detect_chains_for_profile(
    content_root: Path,
    profiles_root: Path,
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
    days: int = 90,
) -> list[KineticChainMatch]:
    """High-level runner: resolves kinetic-chains.toml, reads recent events, synthesizes clusters, and evaluates."""
    from gtm_core.paths import resolve_knowledge_file
    from gtm_core.signal_events.cli import detect_clusters, read_recent_events

    if overlay is not None:
        from gtm_core.experiments import admit

        admit(profile=profile, slug=overlay, profiles_root=profiles_root)

    config_path = resolve_knowledge_file(
        profiles_root=profiles_root,
        profile=profile,
        filename="kinetic-chains.toml",
        product=product,
        overlay=overlay,
    )
    rules = load_kinetic_chains_config(config_path)
    if not rules:
        return []

    events = read_recent_events(content_root=content_root, profile=profile, days=days)
    if not events:
        return []

    # Synthesize cluster_expansion events so sequential chains requiring clusters can match
    clusters = detect_clusters(events)
    seen_cluster_hashes: set[str] = set()
    cluster_events: list[BusinessEvent] = []
    for c_data in clusters.values():
        c_ev = c_data.get("cluster_event")
        if isinstance(c_ev, BusinessEvent):
            h = event_dedup_hash(c_ev)
            if h not in seen_cluster_hashes:
                seen_cluster_hashes.add(h)
                cluster_events.append(c_ev)

    all_events = list(events) + cluster_events
    return detect_kinetic_chains(all_events, config=rules)


def run_detect_chains_cli(
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
    days: int = 90,
) -> int:
    """CLI handler for detect-chains command. Formats findings for operator cockpit."""
    from gtm_core.paths import resolve_content_root, resolve_profiles_root

    content_root = resolve_content_root()
    profiles_root = resolve_profiles_root()
    matches = detect_chains_for_profile(
        content_root=content_root,
        profiles_root=profiles_root,
        profile=profile,
        product=product,
        overlay=overlay,
        days=days,
    )
    if not matches:
        print(f"No kinetic chains detected for profile {profile!r} in past {days} days.")
        return 0

    print(f"Detected {len(matches)} kinetic chain(s) for profile {profile!r}:")
    for m in matches:
        domain_str = f" ({m.company_domain})" if m.company_domain else ""
        print(f"  • {m.company_name}{domain_str}")
        print(f"    {m.compound_why_now}")
        print(f"    Heat Boost: +{m.heat_boost} -> Tier A promotion")
    return 0
