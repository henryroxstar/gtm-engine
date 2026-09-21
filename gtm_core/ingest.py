"""URL ingestion via the Firecrawl REST API.

Kept in gtm_core/ (not agent/) so that the httpx dependency is outside the semgrep
no-raw-egress-in-brain boundary (§R6 constrains agent/ and plugin/ from making raw HTTP
calls; gtm_core/ is the Python tool layer where network I/O is allowed).

The firecrawl_api_key is always read from Config (env via Doppler) — never hardcoded,
echoed, or logged (§R6). The cost cap is checked BEFORE the paid crawl call (§R2).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class IngestConfig(Protocol):
    """Structural config shape this module needs — avoids importing agent.config."""

    content_root: Path
    firecrawl_api_key: str | None
    onboarding_cap_usd: float | None


class UrlIngestUnavailableError(RuntimeError):
    """URL ingestion is not configured on this deployment (no Firecrawl key)."""


class UrlIngestFailedError(ValueError):
    """The crawl provider answered with a body that is not the JSON it documents, or reported
    the crawl itself as failed."""


class UrlIngestInvalidUrlError(ValueError):
    """The crawl provider rejected the requested URL as invalid (e.g. HTTP 400/422)."""


class UrlIngestTimeoutError(RuntimeError):
    """The crawl did not finish within :data:`CRAWL_DEADLINE_S`."""


#: Wall-clock budget for one crawl, polls included. Onboarding runs as a background job
#: (issue #259), so this bounds provider latency, not an HTTP request.
CRAWL_DEADLINE_S = 150.0
CRAWL_POLL_INTERVAL_S = 2.0


class OnboardingCapReachedError(RuntimeError):
    """This month's onboarding spend has reached ``onboarding_cap_usd`` (§R2)."""


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


def _json_body(response) -> dict:
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise UrlIngestFailedError(f"Firecrawl returned a non-JSON response: {exc}") from exc


def _poll_crawl_pages(crawl_id: str, headers: dict) -> list[str]:
    """Poll Firecrawl until the crawl completes, returning all markdown pages."""
    deadline = time.monotonic() + CRAWL_DEADLINE_S
    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                result = client.get(
                    f"https://api.firecrawl.dev/v1/crawl/{crawl_id}",
                    headers=headers,
                )
                result.raise_for_status()
                result_data = _json_body(result)
            except httpx.HTTPStatusError as exc:
                raise UrlIngestFailedError(
                    f"Firecrawl poll failed with status {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                raise UrlIngestFailedError(f"Firecrawl poll connection failed: {exc}") from exc

            crawl_status = result_data.get("status")
            if crawl_status == "completed":
                pages: list[str] = []
                for item in result_data.get("data", []):
                    content = item.get("markdown") or item.get("content") or ""
                    if content.strip():
                        pages.append(content)
                return pages
            if crawl_status in ("failed", "cancelled"):
                raise UrlIngestFailedError(f"Firecrawl crawl ended as {crawl_status!r}")
            if time.monotonic() + CRAWL_POLL_INTERVAL_S >= deadline:
                raise UrlIngestTimeoutError(
                    f"Firecrawl crawl not completed within {CRAWL_DEADLINE_S:.0f}s"
                )
            time.sleep(CRAWL_POLL_INTERVAL_S)


def _ingest_url(url: str, cfg: IngestConfig) -> str:
    """Crawl a URL via the Firecrawl REST API and return the combined markdown.

    Raises RuntimeError when FIRECRAWL_API_KEY is not configured (fail-closed),
    or when onboarding_cap_usd has been reached (§R2 cost cap, checked BEFORE the
    paid crawl).
    """
    if not cfg.firecrawl_api_key:
        raise UrlIngestUnavailableError(
            "URL ingestion requires FIRECRAWL_API_KEY — set it in Doppler or .env"
        )

    # §R2 cost cap check BEFORE the paid crawl call.
    if cfg.onboarding_cap_usd is not None:
        spent = _onboarding_month_spend(cfg)
        if spent >= cfg.onboarding_cap_usd:
            raise OnboardingCapReachedError(
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

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post("https://api.firecrawl.dev/v1/crawl", headers=headers, json=payload)
            resp.raise_for_status()
            data = _json_body(resp)
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            detail = exc.response.text.strip() or f"status {exc.response.status_code}"
            raise UrlIngestInvalidUrlError(f"Firecrawl rejected URL {url!r}: {detail}") from exc
        raise UrlIngestFailedError(
            f"Firecrawl crawl failed with status {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise UrlIngestFailedError(f"Firecrawl connection failed: {exc}") from exc

    crawl_id = data.get("id")
    if crawl_id:
        pages = _poll_crawl_pages(crawl_id, headers)
    else:
        pages = []
        content = data.get("markdown") or data.get("content") or ""
        if content.strip():
            pages.append(content)

    return "\n\n---\n\n".join(pages)
