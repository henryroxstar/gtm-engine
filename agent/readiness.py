"""agent.readiness — the pack-activation readiness check (PRD §5, the "second brain" gap).

Diffs a pack's ``PackInputs`` (settings + knowledge, §5's two-class inputs) against a
profile's two stores — ``PROFILE.md`` (settings, by key) and ``knowledge/`` (corpus, by
topic via ``resolve_knowledge_file``, product-first) — and returns a traffic-light
report so an operator activating a pack knows exactly what's missing and why, instead of
the pack silently running on empty/stale context.

Lives in ``agent/`` (not ``gtm_core/``, despite the PRD's illustrative path) because it
needs ``agent.profiles.read_profile_field`` for settings — and ``gtm_core`` must never
import ``agent`` (tests/contracts/test_layering.py enforces this). The knowledge half
(``resolve_knowledge_file``) is a legal ``agent -> gtm_core`` import either way.

Freshness prefers the authoritative per-file ``refreshed:`` frontmatter stamp (the human's
last-verified date, ``gtm_core.knowledge_meta`` — Phase 1 of the knowledge-lifecycle PRD),
falling back to filesystem mtime for files that predate the metadata. Semantic/embedding
retrieval remains deliberately deferred until the messy knowledge tail demands it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from gtm_core import knowledge_meta
from gtm_core.packs.loader import PackInputs
from gtm_core.paths import resolve_knowledge_file

from .profiles import profile_dir, read_profile_field

GREEN = "green"
YELLOW = "yellow"
RED = "red"

#: Freshness policies with a bounded window. "evergreen" (not listed here) never stales.
_FRESHNESS_WINDOW_DAYS = {"90d": 90}


def _knowledge_age_days(path: Path, now: float) -> float:
    """Age of a knowledge file in days — prefers the ``refreshed:`` frontmatter stamp (a human's
    last-verified date), falling back to filesystem mtime for files that predate the metadata."""
    stamp = knowledge_meta.refreshed_date(path)
    if stamp is not None:
        return (now - time.mktime(stamp.timetuple())) / 86400
    return (now - path.stat().st_mtime) / 86400


@dataclass(frozen=True)
class ReadinessItem:
    """One settings key or knowledge topic's readiness verdict."""

    kind: str  # "setting" | "knowledge"
    name: str  # settings key or knowledge topic
    status: str  # GREEN | YELLOW | RED
    required: bool
    detail: str = ""


@dataclass(frozen=True)
class ReadinessReport:
    items: tuple[ReadinessItem, ...]

    @property
    def blocked(self) -> bool:
        """True if any REQUIRED input is missing — pack activation must refuse to run."""
        return any(i.status == RED and i.required for i in self.items)

    @property
    def degraded(self) -> bool:
        """True if anything is stale or an optional input is missing — runs, but flagged."""
        return any(i.status in (YELLOW, RED) for i in self.items)


def check_readiness(
    profiles_root: Path,
    profile: str,
    inputs: PackInputs,
    *,
    product: str | None = None,
    now: float | None = None,
) -> ReadinessReport:
    """Diff ``inputs`` against ``profile``'s PROFILE.md + knowledge/ corpus.

    Raises ``ValueError`` (via :func:`agent.profiles.profile_dir`) if ``profile`` doesn't
    exist — activating a pack for an unknown profile is a caller error, not a readiness
    gap. ``now`` is injectable (epoch seconds) for deterministic freshness tests;
    defaults to :func:`time.time`.
    """
    now = time.time() if now is None else now
    pdir = profile_dir(profiles_root, profile)
    profile_md = pdir / "PROFILE.md"
    text = profile_md.read_text(encoding="utf-8") if profile_md.is_file() else ""

    items: list[ReadinessItem] = []

    for s in inputs.settings:
        value = read_profile_field(text, s.key) if text else None
        if value:
            items.append(ReadinessItem("setting", s.key, GREEN, s.required))
        else:
            items.append(
                ReadinessItem(
                    "setting",
                    s.key,
                    RED,
                    s.required,
                    detail=f"missing from PROFILE.md (source: {s.source})",
                )
            )

    for k in inputs.knowledge:
        path = resolve_knowledge_file(profiles_root, profile, f"{k.topic}.md", product=product)
        if not path.is_file():
            items.append(
                ReadinessItem(
                    "knowledge", k.topic, RED, k.required, detail="no corpus/index match found"
                )
            )
            continue

        window_days = _FRESHNESS_WINDOW_DAYS.get(k.freshness)
        if window_days is None:  # "evergreen" — presence is enough
            items.append(ReadinessItem("knowledge", k.topic, GREEN, k.required))
            continue

        age_days = _knowledge_age_days(path, now)
        if age_days > window_days:
            items.append(
                ReadinessItem(
                    "knowledge",
                    k.topic,
                    YELLOW,
                    k.required,
                    detail=f"stale — {age_days:.0f}d old, {k.freshness} re-verify policy",
                )
            )
        else:
            items.append(ReadinessItem("knowledge", k.topic, GREEN, k.required))

    return ReadinessReport(items=tuple(items))
