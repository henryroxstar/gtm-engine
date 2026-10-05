from __future__ import annotations

import datetime
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from gtm_core.merge_hygiene import clean_company

from .contracts import BusinessEvent

ALLOWED_THEIRSTACK_HOSTS = ("api.theirstack.com",)


class EgressRefused(RuntimeError):
    """Raised when an egress request violates the host allowlist or attempts a redirect."""

    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse HTTP redirects to prevent leaking API keys or redirecting off the allowlist."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EgressRefused(
            f"TheirStack host attempted a redirect to {newurl!r} — refused, not followed"
        )


logger = logging.getLogger(__name__)

_AGENCY_REGEX = re.compile(
    r"(?i)\b(staffing|recruiting|talent\s+solutions|placement\s+agency|on\s+behalf\s+of\s+our\s+client)\b"
)
_SNIPPET_AGENCY_REGEX = re.compile(
    r"(?i)\b(staffing\s+agency|recruiting\s+firm|placement\s+agency|placement\s+firm|on\s+behalf\s+of\s+our\s+client|on\s+behalf\s+of\s+a\s+client)\b"
)
_CONFIDENTIAL_REGEX = re.compile(r"(?i)\b(stealth|confidential|undisclosed)\b")
_TITLE_COMPANY_REGEX = re.compile(r"(?i)\bat\s+(.+)$")
_SEPARATOR_REGEX = re.compile(r"\s+[|–—]\s+|\s+-\s+|[|–—]")

_FRAMEWORK_CANONICAL: dict[str, str] = {
    "langgraph": "LangGraph",
    "bedrock": "Bedrock",
    "crewai": "CrewAI",
    "semantic kernel": "Semantic Kernel",
    "vllm": "vLLM",
    "llamaindex": "LlamaIndex",
    "autogen": "AutoGen",
}


class PreLLMRegexFilter:
    @staticmethod
    def is_agency(text: str | None) -> bool:
        return bool(_AGENCY_REGEX.search(text or ""))

    @staticmethod
    def is_agency_snippet(text: str | None) -> bool:
        return bool(_SNIPPET_AGENCY_REGEX.search(text or ""))


def extract_company_from_title(title: str | None) -> str | None:
    if not title:
        return None
    title_prefix = _SEPARATOR_REGEX.split(title, maxsplit=1)[0].strip()
    matches = list(re.finditer(r"(?i)\bat\s+", title_prefix))
    if not matches:
        return None
    raw_company = title_prefix[matches[-1].end() :].strip()
    company = clean_company(raw_company)
    return None if not company or _CONFIDENTIAL_REGEX.search(company) else company


def check_url_head_status(status_code: int) -> tuple[str, str]:
    if status_code in (404, 410):
        return "drop", f"expired_job_{status_code}"
    if status_code == 403:
        return "keep", "unverified_bot_guard"
    return "keep", "verified_active"


def build_ats_query(token: str) -> str:
    return f'(site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com) "{token}"'


def _extract_signals(title: str, snippet: str, matched_token: str) -> tuple[list[str], str]:
    text = f"{title} {snippet}".lower()
    frameworks = [
        canon
        for fw, canon in _FRAMEWORK_CANONICAL.items()
        if re.search(rf"\b{re.escape(fw)}\b", text)
    ]
    canon_token = _FRAMEWORK_CANONICAL.get(matched_token.lower(), matched_token.strip())
    if re.search(rf"\b{re.escape(matched_token.lower())}\b", text):
        if not any(f.lower() == canon_token.lower() for f in frameworks):
            frameworks.append(canon_token)
    pain = next(
        (
            p
            for p in ("tool-calling", "delegation", "eval latency", "authorization", "evals")
            if re.search(rf"\b{re.escape(p)}\b", text)
        ),
        "",
    )
    return frameworks, pain


def parse_ats_serp_item(
    hit: dict[str, Any],
    matched_token: str,
    event_date: str | None = None,
    today: datetime.date | None = None,
) -> BusinessEvent | None:
    if not isinstance(hit, dict):
        return None

    ref_today = today or datetime.date.today()
    date_val = str(event_date).strip() if event_date else ref_today.isoformat()
    try:
        ev_d = datetime.date.fromisoformat(date_val[:10])
        if ev_d < (ref_today - datetime.timedelta(days=45)):
            return None
    except (ValueError, TypeError):
        return None

    title = str(hit.get("title") or "")
    snippet = str(hit.get("snippet") or "")
    url = str(hit.get("url") or "")

    if PreLLMRegexFilter.is_agency(title) or PreLLMRegexFilter.is_agency(snippet):
        return None

    # Enforce non-blocking URL HEAD status check if status is provided in hit
    url_status = "verified_active"
    if "http_status" in hit and hit["http_status"] is not None:
        try:
            action, reason = check_url_head_status(int(hit["http_status"]))
            if action == "drop":
                return None
            url_status = reason
        except (ValueError, TypeError):
            pass

    company = extract_company_from_title(title)
    if not company:
        return None

    job_title = (
        re.split(r"(?i)\s+at\s+", title)[0].strip() if re.search(r"(?i)\s+at\s+", title) else title
    )
    frameworks, pain_cue = _extract_signals(title, snippet, matched_token)

    return BusinessEvent.from_job_post(
        company_name=company,
        company_domain=None,
        job_title=job_title,
        matched_keywords=[matched_token],
        event_date=date_val,
        job_url=url,
        snippet=snippet,
        provider="ats_sweep",
        date_confidence="observed",
        frameworks=frameworks,
        pain_cue=pain_cue,
        url_status=url_status,
    )


class SignalProvider(Protocol):
    def is_available(self) -> bool: ...

    def fetch_events(
        self,
        token: str,
        limit: int = 10,
        hits: list[dict[str, Any]] | None = None,
        profile: str | None = None,
    ) -> list[BusinessEvent]: ...


class ZeroCostATSSweep:
    def is_available(self) -> bool:
        return True

    def fetch_events(
        self,
        token: str,
        limit: int = 10,
        hits: list[dict[str, Any]] | None = None,
        profile: str | None = None,
    ) -> list[BusinessEvent]:
        if limit <= 0:
            return []
        results: list[BusinessEvent] = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            ev = parse_ats_serp_item(hit, matched_token=token)
            if ev:
                results.append(ev)
            if len(results) >= limit:
                break
        return results


class ZeroCostNewswireSweep:
    def is_available(self) -> bool:
        return True

    def fetch_events(
        self,
        token: str,
        limit: int = 10,
        hits: list[dict[str, Any]] | None = None,
        profile: str | None = None,
    ) -> list[BusinessEvent]:
        return []


def _resolve_target_profile(profile: str | None) -> str | None:
    target = profile or os.environ.get("GTM_ACTIVE_PROFILE") or os.environ.get("PROFILE")
    if not target:
        try:
            from gtm_core.active_profile import show

            target = show()
        except Exception as exc:
            logger.debug("Failed to read active profile: %s", exc)
    return target


class TheirStackAccelerator:
    def __init__(self) -> None:
        self.fallback_reason: str | None = None

    def is_available(self) -> bool:
        return bool(os.environ.get("THEIRSTACK_API_KEY"))

    @staticmethod
    def _build_payload(token: str, limit: int) -> dict[str, Any]:
        clean = token.strip()
        norm = clean.lower().replace("-", " ").replace("_", " ")
        capped_limit = min(max(limit, 1), 50)
        if norm in _FRAMEWORK_CANONICAL:
            return {
                "job_technology_slug_or": [norm.replace(" ", "-")],
                "posted_at_max_age_days": 45,
                "limit": capped_limit,
            }
        return {"job_keyword_slug_or": [clean], "posted_at_max_age_days": 45, "limit": capped_limit}

    @staticmethod
    def _record_cost(profile: str | None) -> None:
        target = _resolve_target_profile(profile)
        if not target:
            logger.warning("[WARN] TheirStack query executed without profile; spend unrecorded.")
            return
        try:
            from gtm_core.ledgers import Ledgers
            from gtm_core.paths import PathConfig

            Ledgers(PathConfig.from_env(), target).append_cost(
                {
                    "tool": "theirstack",
                    "op": "jobs_search",
                    "units": 1,
                    "unit_kind": "queries",
                    "cost_usd": 0.05,
                }
            )
        except Exception as cost_exc:
            logger.warning(f"[WARN] Failed to record TheirStack cost in ledger: {cost_exc}")

    def _check_budget(self, profile: str | None) -> bool:
        target = _resolve_target_profile(profile)
        if not target:
            return True
        try:
            from gtm_core.budget_status import _get_cap
            from gtm_core.ledgers import Ledgers
            from gtm_core.paths import PathConfig

            cap = _get_cap(target)
            if Ledgers(PathConfig.from_env(), target).over_monthly_cap(cap):
                self.fallback_reason = "Monthly Budget Cap Exceeded"
                logger.warning(
                    f"[WARN] Budget cap (${cap:.2f}) reached for '{target}'. Falling back."
                )
                return False
        except Exception as exc:
            logger.warning(f"[WARN] Failed to check budget cap: {exc}")
        return True

    def _execute_http(
        self,
        req: urllib.request.Request,
        token: str,
        limit: int,
        hits: list[dict[str, Any]] | None,
        profile: str | None,
    ) -> bytes | list[BusinessEvent] | None:
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(req, timeout=10.0) as response:
                response_data = response.read()
            self._record_cost(profile)
            return response_data
        except EgressRefused:
            raise
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):
                self.fallback_reason = (
                    "HTTP 402 (Credit Exhaustion)" if e.code == 402 else "HTTP 429 (Rate Limit)"
                )
                desc = (
                    "credit pool exhausted (HTTP 402)"
                    if e.code == 402
                    else "rate limit reached (HTTP 429)"
                )
                logger.warning(f"[WARN] TheirStack {desc}. Falling back to ZeroCostATSSweep.")
                return ZeroCostATSSweep().fetch_events(token, limit, hits, profile=profile)
            if e.code in (400, 422):
                logger.error(
                    f"[ERROR] TheirStack query rejected (HTTP {e.code}: {e.reason}). Verify search token '{token}' formatting."
                )
                return None
            logger.error(f"[ERROR] TheirStack upstream API error {e.code}: {e.reason}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.fallback_reason = "Network Timeout / Unreachable"
            logger.warning(
                f"[WARN] TheirStack unreachable ({e}). Falling back to ZeroCostATSSweep."
            )
            return ZeroCostATSSweep().fetch_events(token, limit, hits, profile=profile)
        except Exception as e:
            logger.error(f"[ERROR] TheirStack unexpected error: {e}")
            return None

    @staticmethod
    def _parse_job(job: dict[str, Any], clean_token: str) -> BusinessEvent | None:
        if not isinstance(job, dict):
            return None
        raw_title = job.get("job_title")
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        raw_snippet = job.get("snippet")
        snippet = raw_snippet.strip() if isinstance(raw_snippet, str) else ""
        raw_url = job.get("url")
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        raw_domain = job.get("company_domain")
        company_domain = (
            re.sub(r"^https?://|/.*$", "", raw_domain.strip())
            if isinstance(raw_domain, str) and raw_domain.strip()
            else None
        )

        if PreLLMRegexFilter.is_agency(title) or PreLLMRegexFilter.is_agency_snippet(snippet):
            return None

        raw_company = job.get("company_name")
        company = (
            clean_company(raw_company)
            if isinstance(raw_company, str) and raw_company.strip()
            else None
        )
        if company and (
            _CONFIDENTIAL_REGEX.search(company) or PreLLMRegexFilter.is_agency(company)
        ):
            return None
        if not company:
            extracted = extract_company_from_title(title)
            if extracted:
                company = clean_company(extracted)
        if not company:
            return None

        job_title = (
            re.split(r"(?i)\s+at\s+", title)[0].strip()
            if re.search(r"(?i)\s+at\s+", title)
            else title
        )
        frameworks, pain_cue = _extract_signals(title, snippet, clean_token)

        raw_posted_at = job.get("posted_at")
        date_confidence = "inferred"
        if isinstance(raw_posted_at, str) and len(raw_posted_at) >= 10:
            try:
                datetime.date.fromisoformat(raw_posted_at[:10])
                date_val = raw_posted_at[:10]
                date_confidence = "author"
            except (ValueError, TypeError):
                date_val = datetime.date.today().isoformat()
        else:
            date_val = datetime.date.today().isoformat()

        return BusinessEvent.from_job_post(
            company_name=company,
            company_domain=company_domain,
            job_title=job_title,
            matched_keywords=[clean_token],
            event_date=date_val,
            job_url=url,
            snippet=snippet,
            provider="theirstack",
            date_confidence=date_confidence,
            frameworks=frameworks,
            pain_cue=pain_cue,
            url_status="verified_active",
        )

    def _parse_response(
        self, response_bytes: bytes, clean_token: str, limit: int
    ) -> list[BusinessEvent]:
        try:
            data = json.loads(response_bytes.decode("utf-8"))
        except Exception:
            logger.error("[ERROR] TheirStack invalid JSON response")
            return []
        if not isinstance(data, dict):
            logger.error("[ERROR] TheirStack response is not a JSON object")
            return []

        jobs = data.get("data")
        if not isinstance(jobs, list):
            return []

        results: list[BusinessEvent] = []
        for job in jobs:
            try:
                ev = self._parse_job(job, clean_token)
                if ev:
                    results.append(ev)
                    if len(results) >= limit:
                        break
            except Exception as item_exc:
                logger.warning(f"[WARN] Skipping malformed job item: {item_exc}")
        return results

    def fetch_events(
        self,
        token: str,
        limit: int = 10,
        hits: list[dict[str, Any]] | None = None,
        profile: str | None = None,
    ) -> list[BusinessEvent]:
        api_key = os.environ.get("THEIRSTACK_API_KEY")
        if not api_key:
            raise RuntimeError("TheirStackAccelerator invoked without THEIRSTACK_API_KEY")
        if not token or not token.strip():
            return []
        if limit <= 0:
            return []

        if self.fallback_reason:
            return ZeroCostATSSweep().fetch_events(token, limit, hits, profile=profile)
        if not self._check_budget(profile):
            return ZeroCostATSSweep().fetch_events(token, limit, hits, profile=profile)

        url = "https://api.theirstack.com/v1/jobs/search"
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname not in ALLOWED_THEIRSTACK_HOSTS or parsed.scheme != "https":
            raise EgressRefused(
                f"Host {parsed.hostname!r} or scheme {parsed.scheme!r} not in ALLOWED_THEIRSTACK_HOSTS"
            )

        payload = self._build_payload(token, limit)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gtm-engine/1.0",
            },
            method="POST",
        )
        res = self._execute_http(req, token, limit, hits, profile)
        if isinstance(res, list):
            return res
        if not res:
            return []
        return self._parse_response(res, token.strip(), limit)


def get_provider(kind: str) -> SignalProvider:
    if kind == "hiring":
        ts = TheirStackAccelerator()
        if ts.is_available():
            return ts
        logger.warning("[WARN] TheirStack unconfigured. Falling back to ZeroCostATSSweep.")
        return ZeroCostATSSweep()
    return ZeroCostNewswireSweep()
