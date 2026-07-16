"""URL ingestion via the Firecrawl REST API.

Kept in gtm_core/ (not agent/) so that the httpx dependency is outside the semgrep
no-raw-egress-in-brain boundary (§R6 constrains agent/ and plugin/ from making raw HTTP
calls; gtm_core/ is the Python tool layer where network I/O is allowed).

The firecrawl_api_key is always read from Config (env via Doppler) — never hardcoded,
echoed, or logged (§R6). The cost cap is checked BEFORE the paid crawl call (§R2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class IngestConfig(Protocol):
    """Structural config shape this module needs — avoids importing agent.config."""

    content_root: Path
    firecrawl_api_key: str | None
    onboarding_cap_usd: float | None


def _onboarding_month_spend(cfg: IngestConfig) -> float:
    """Return the current month's onboarding spend from content/_system/costs.jsonl.

    Counts only records whose event_type starts with "onboard." so normal pipeline
    costs don't count against the onboarding cap. Returns 0.0 if the file is absent
    or unreadable (fail-open on read, but the cap check itself is fail-closed on spend).
    """
    from gtm_core.ledgers import _current_year_month

    window = _current_year_month()
    costs_path = cfg.content_root / "_system" / "costs.jsonl"
    if not costs_path.exists():
        return 0.0
    total = 0.0
    try:
        with costs_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = str(rec.get("ts", ""))
                if ts.startswith(window) and str(rec.get("event_type", "")).startswith("onboard."):
                    total += float(rec.get("cost_usd", 0) or 0)
    except OSError:
        return 0.0
    return total


def _ingest_url(url: str, cfg: IngestConfig) -> str:
    """Crawl a URL via the Firecrawl REST API and return the combined markdown.

    Raises RuntimeError when FIRECRAWL_API_KEY is not configured (fail-closed),
    or when onboarding_cap_usd has been reached (§R2 cost cap, checked BEFORE the
    paid crawl).
    """
    import httpx

    if not cfg.firecrawl_api_key:
        raise RuntimeError("URL ingestion requires FIRECRAWL_API_KEY — set it in Doppler or .env")

    # §R2 cost cap check BEFORE the paid crawl call.
    if cfg.onboarding_cap_usd is not None:
        spent = _onboarding_month_spend(cfg)
        if spent >= cfg.onboarding_cap_usd:
            raise RuntimeError(
                f"Onboarding cost cap exceeded: ${spent:.4f} >= ${cfg.onboarding_cap_usd:.4f} "
                f"(GTM_ONBOARDING_CAP_USD). Check content/_system/costs.jsonl."
            )

    headers = {
        "Authorization": f"Bearer {cfg.firecrawl_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "limit": 8,
        "scrapeOptions": {"formats": ["markdown"]},
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post("https://api.firecrawl.dev/v1/crawl", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    pages: list[str] = []
    crawl_id = data.get("id")
    if crawl_id:
        import time

        with httpx.Client(timeout=30.0) as client:
            for _ in range(30):
                result = client.get(
                    f"https://api.firecrawl.dev/v1/crawl/{crawl_id}",
                    headers=headers,
                )
                result.raise_for_status()
                result_data = result.json()
                if result_data.get("status") == "completed":
                    for item in result_data.get("data", []):
                        content = item.get("markdown") or item.get("content") or ""
                        if content.strip():
                            pages.append(content)
                    break
                time.sleep(2)
    else:
        content = data.get("markdown") or data.get("content") or ""
        if content.strip():
            pages.append(content)

    return "\n\n---\n\n".join(pages)
