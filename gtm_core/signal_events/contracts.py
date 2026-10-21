from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from gtm_core.merge_hygiene import clean_company

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {"hiring", "partnership", "regulatory", "funding", "pilot", "cluster_expansion"}
)

_FRAMEWORK_WHITELIST = re.compile(r"^[a-zA-Z0-9_\-\.\s]{1,30}$")
_INJECTION_DIRECTIVES = re.compile(r"(?i)\b(system|ignore|disregard|drop\s+table)\b")
_PUNCTUATION_CLEAN = re.compile(r"[^a-zA-Z0-9_\-\s]")


@dataclass(frozen=True)
class BusinessEvent:
    """Canonical representation of an enterprise buying intent signal or milestone."""

    company_name: str
    company_domain: str | None
    event_type: str
    event_date: str
    headline: str
    snippet: str
    source_url: str
    provider: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(
                f"Invalid event_type '{self.event_type}'. Must be in {ALLOWED_EVENT_TYPES}"
            )
        if not str(self.company_name or "").strip():
            raise ValueError("company_name cannot be empty")

    @classmethod
    def from_job_post(
        cls,
        company_name: str,
        company_domain: str | None,
        job_title: str,
        matched_keywords: list[str],
        event_date: str,
        job_url: str,
        snippet: str,
        provider: str = "ats_sweep",
        date_confidence: str = "author",
        frameworks: list[str] | None = None,
        pain_cue: str | None = None,
        hiring_manager_cue: str | None = None,
        url_status: str | None = None,
    ) -> BusinessEvent:
        safe_frameworks = [
            f.strip() for f in (frameworks or []) if _FRAMEWORK_WHITELIST.match(f.strip())
        ]
        clean_title = _INJECTION_DIRECTIVES.sub("", str(job_title or "")).strip()
        clean_snippet = _INJECTION_DIRECTIVES.sub("", str(snippet or "")).strip()
        raw_pain = _INJECTION_DIRECTIVES.sub("", (pain_cue or "")[:80])
        safe_pain = _PUNCTUATION_CLEAN.sub("", raw_pain).strip()
        raw_manager = _INJECTION_DIRECTIVES.sub("", (hiring_manager_cue or "")[:60])
        safe_manager = _PUNCTUATION_CLEAN.sub("", raw_manager).strip()

        return cls(
            company_name=company_name,
            company_domain=company_domain,
            event_type="hiring",
            event_date=event_date,
            headline=f"Hiring {clean_title}",
            snippet=clean_snippet,
            source_url=job_url,
            provider=provider,
            meta={
                "job_title": clean_title,
                "keywords": matched_keywords,
                "date_confidence": date_confidence,
                "frameworks": safe_frameworks,
                "pain_cue": safe_pain,
                "hiring_manager_cue": safe_manager,
                "url_status": url_status or "verified_active",
            },
        )

    def to_quality_inputs(self) -> dict[str, Any]:
        frameworks_str = (
            f" using {', '.join(self.meta['frameworks'])}" if self.meta.get("frameworks") else ""
        )
        pain_str = f" facing {self.meta['pain_cue']}" if self.meta.get("pain_cue") else ""
        res: dict[str, Any] = {
            "why_now": f"{self.headline}{frameworks_str}{pain_str}: {self.snippet}",
            "signal_observed": self.event_date,
            "source_id_or_url": self.source_url,
            "signal_agent_kind": "ai"
            if any(k in self.snippet.lower() for k in ("agent", "llm", "autonomous", "langgraph"))
            else "none",
            "event_type": self.event_type,
            "is_cluster": self.event_type == "cluster_expansion"
            or bool(self.meta.get("is_cluster")),
        }
        if self.event_type == "cluster_expansion":
            res["cluster_size"] = self.meta.get("cluster_size", 2)
            res["signal_agent_kind"] = "ai"
        return res


def event_dedup_hash(event: BusinessEvent) -> str:
    """Compute a deterministic 16-character SHA-256 deduplication hash for an event.

    Includes entity slug, event type, role identity (headline or job_title), and date
    to prevent collapsing multiple distinct openings posted on the same date while still
    equating company variations (e.g. Acme Health vs Acme Health Inc.).
    """
    entity_slug = clean_company(event.company_name).strip().lower()
    raw_role = event.meta.get("job_title") or event.headline or ""
    role_key = re.sub(r"\s+", " ", raw_role).strip().lower()
    key = f"{entity_slug}:{event.event_type}:{role_key}:{event.event_date}".encode()
    return hashlib.sha256(key).hexdigest()[:16]
