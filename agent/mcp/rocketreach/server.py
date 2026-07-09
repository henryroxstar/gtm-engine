"""The RocketReach worker MCP server (FastMCP, stdio).

A thin wrapper over the RocketReach REST API for the ``prospect`` skill: person
lookup (resolve a named buyer into a verified email + direct phone — the metered
unit) plus credit-free person/company **search** (intent, news, job-posting, and
job-change signal facets — the skill's signal pre-flag pass and new-in-role check).
See the package docstring (:mod:`agent.mcp.rocketreach`) for the boundary + quota
model.

Cost metering at the code boundary (NIST AU-12; budget integrity): the worker writes
its OWN cost record to ``content/<profile>/costs.jsonl`` after each resolving call —
the component that spends the finite unit records the spend. The owning profile is
passed in at spawn via ``GTM_PROFILE``; if it is absent or the ledger write fails,
metering is skipped silently — it must never break a lookup.

Robustness contract: every tool returns a string. On any failure it returns a
``[rocketreach-error] …`` string rather than raising — so the brain gets a tool
result it can react to (fall back to Vibe / public web), and the SDK ↔ MCP
connection never breaks.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# --- RocketReach wiring ------------------------------------------------------- #
# Endpoints confirmed against the RocketReach API docs (docs.rocketreach.co):
#   GET  {base}/person/lookup        — synchronous single-person resolve
#   GET  {base}/person/checkStatus   — poll while a lookup is still "searching"
#   POST {base}/person/search        — people search (facets incl. signal filters)
#   POST {base}/searchCompany        — company search (facets incl. signal filters)
# Auth is the ``Api-Key`` header. Base URL is overridable for tests, but defaults to
# the documented v2 host — no secret in this module (the key is read at call time).
ROCKETREACH_BASE_URL = os.getenv(
    "ROCKETREACH_BASE_URL", "https://api.rocketreach.co/api/v2"
).rstrip("/")
_API_KEY_ENV = "ROCKETREACH_API_KEY"
_HTTP_TIMEOUT_S = 30.0

# Poll budget while a record is still resolving. RocketReach returns status
# "searching"/"progress" with an id; we poll checkStatus until it completes or we
# give up (then the brain falls back). Kept small — the caller is human-paced.
_MAX_POLLS = 5
_POLL_INTERVAL_S = 2.0

# Bulk is a server-side loop of the synchronous lookup (the native async bulk endpoint
# needs ≥10 profiles + a webhook receiver this deployment has no inbound path for).
# Cap the loop so a bad call can't fan out — finalist sets are small by design.
_BULK_MAX = 25

# Search page cap. Searches are credit-free, but the brain reads the results —
# keep pages compact and let it paginate with ``start`` when it truly needs more.
_SEARCH_MAX_PAGE_SIZE = 25

# Per-lookup USD rate for the cost ledger. RocketReach is a flat monthly subscription,
# so the marginal dollar per lookup is ~0 — the real constraint is the finite LOOKUP
# (a.k.a. "export") COUNT, which we always record. Override via env if a plan meters
# per call. Defaults to 0.0 so we never invent a dollar figure we can't stand behind.
_USD_PER_LOOKUP = float(os.getenv("ROCKETREACH_USD_PER_LOOKUP", "0") or 0)

mcp = FastMCP("rocketreach")


def _headers(key: str) -> dict[str, str]:
    return {"Api-Key": key, "Content-Type": "application/json", "Accept": "application/json"}


def _shape(person: dict) -> dict:
    """Reduce a RocketReach person object to the fields the brain actually uses.

    Defensive by construction — RocketReach's payload shape varies by plan and match
    quality, so every field is pulled with ``.get`` and tolerated absent.
    """
    emails = person.get("emails") or []
    # Each email is typically {email, smtp_valid, grade, type}; keep the useful bits.
    clean_emails = [
        {
            "email": e.get("email"),
            "type": e.get("type"),
            "grade": e.get("grade"),
            "smtp_valid": e.get("smtp_valid"),
        }
        for e in emails
        if isinstance(e, dict) and e.get("email")
    ]
    phones = person.get("phones") or []
    clean_phones = [(p.get("number") if isinstance(p, dict) else p) for p in phones]
    clean_phones = [p for p in clean_phones if p]
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "current_title": person.get("current_title"),
        "current_employer": person.get("current_employer"),
        "linkedin_url": person.get("linkedin_url"),
        "location": person.get("location"),
        "recommended_email": person.get("recommended_email") or person.get("current_work_email"),
        "emails": clean_emails,
        "phones": clean_phones,
        # Person-level intent if the plan returns it; absent on most records.
        "intent": person.get("intent") or person.get("buying_intent"),
        "status": person.get("status"),
    }


def _meter(resolved: int) -> None:
    """Append a cost record for ``resolved`` lookups to the owning profile's ledger.

    Best-effort: any failure (no profile, ledger error) is swallowed so metering can
    never break a lookup or the MCP connection. Always records the LOOKUP COUNT (the
    finite unit) even when the USD rate is 0 (flat subscription).
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile or resolved <= 0:
        return
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "rocketreach",
                "op": "person_lookup",
                "units": {"lookups": resolved},
                "cost_usd": round(resolved * _USD_PER_LOOKUP, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort; never break a lookup
        return


async def _lookup_one(client: httpx.AsyncClient, key: str, query: dict) -> dict:
    """Resolve one person. Returns a shaped dict, or a dict with an ``error`` key.

    Never raises: HTTP/parse failures map to ``{"error": "…", "query": …}`` so the
    caller can aggregate and the brain still gets a usable result.
    """
    # Only forward the identifying params RocketReach accepts; drop blanks.
    params = {
        k: str(v).strip()
        for k, v in {
            "name": query.get("name"),
            "current_employer": query.get("current_employer") or query.get("company"),
            "title": query.get("title"),
            "linkedin_url": query.get("linkedin_url"),
            "email": query.get("email"),
            "id": query.get("id"),
        }.items()
        if v and str(v).strip()
    }
    if not params:
        return {"error": "no identifying fields (need name+company, linkedin_url, email, or id)"}

    try:
        resp = await client.get(
            f"{ROCKETREACH_BASE_URL}/person/lookup", params=params, headers=_headers(key)
        )
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}", "query": params}
    except httpx.HTTPError as exc:
        return {"error": f"request failed: {type(exc).__name__}", "query": params}
    except ValueError:
        return {"error": "non-JSON response", "query": params}

    status = (body.get("status") or "").lower()
    pid = body.get("id")
    # Poll while the record is still resolving.
    polls = 0
    while status in ("searching", "progress", "waiting") and pid and polls < _MAX_POLLS:
        await asyncio.sleep(_POLL_INTERVAL_S)
        polls += 1
        try:
            cs = await client.get(
                f"{ROCKETREACH_BASE_URL}/person/checkStatus",
                params={"ids": pid},
                headers=_headers(key),
            )
            cs.raise_for_status()
            rows = cs.json()
        except (httpx.HTTPError, ValueError):
            break
        # checkStatus returns a list of person objects.
        match = next(
            (r for r in rows if isinstance(r, dict) and r.get("id") == pid),
            rows[0] if isinstance(rows, list) and rows else None,
        )
        if isinstance(match, dict):
            body = match
            status = (body.get("status") or "").lower()

    if status in ("searching", "progress", "waiting"):
        return {"error": "still resolving after poll budget", "query": params}
    return _shape(body)


async def _lookup(query: dict) -> str:
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        return f"[rocketreach-error] {_API_KEY_ENV} is not set — worker cannot resolve contacts."
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        result = await _lookup_one(client, key, query)
    if "error" in result:
        return f"[rocketreach-error] {result['error']}"
    _meter(1 if result.get("emails") or result.get("phones") else 0)
    return json.dumps(result, ensure_ascii=False)


async def _bulk(people: list[dict]) -> str:
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        return f"[rocketreach-error] {_API_KEY_ENV} is not set — worker cannot resolve contacts."
    if not isinstance(people, list) or not people:
        return "[rocketreach-error] `people` must be a non-empty list of {name, current_employer|linkedin_url}."
    if len(people) > _BULK_MAX:
        return f"[rocketreach-error] {len(people)} queries exceeds the {_BULK_MAX} finalist cap for one call."

    results: list[dict] = []
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        for q in people:
            results.append(await _lookup_one(client, key, q if isinstance(q, dict) else {}))
    # Meter only the records that actually returned contact info (a resolved lookup).
    resolved = sum(
        1 for r in results if not r.get("error") and (r.get("emails") or r.get("phones"))
    )
    _meter(resolved)
    return json.dumps(
        {"resolved": resolved, "count": len(results), "results": results}, ensure_ascii=False
    )


# --- Search (credit-free) ------------------------------------------------------ #
# Searches never consume lookups/exports (the finite unit — see _meter), so nothing
# in this section is metered: the prospect skill's signal pre-flag pass and
# new-in-role check must stay free to run on every candidate. Facet values are
# lists of strings; signal facets take "Value::window" strings
# (e.g. "Funding::three_months").


def _normalize_query(query: dict) -> dict:
    """Coerce a facet dict to RocketReach's search shape: list-of-string values.

    Scalars are wrapped, blanks and Nones dropped. Facet NAMES pass through
    untouched — the API owns the taxonomy, so a new facet works without a worker
    change.
    """
    clean: dict[str, list[str]] = {}
    for facet, value in (query or {}).items():
        values = value if isinstance(value, list) else [value]
        vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
        if vals:
            clean[str(facet).strip()] = vals
    return clean


def _shape_search_person(person: dict) -> dict:
    """Reduce a search profile to identity fields. Deliberately NO contact fields —
    search returns none; resolving a finalist's contact is the metered lookup."""
    location = person.get("location")
    if not location:
        location = ", ".join(p for p in (person.get("city"), person.get("region")) if p) or None
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "current_title": person.get("current_title"),
        "current_employer": person.get("current_employer"),
        "location": location,
        "linkedin_url": person.get("linkedin_url"),
    }


def _shape_search_company(company: dict) -> dict:
    """Reduce a company search hit to the fields the prospect skill records."""
    location = (
        ", ".join(
            p for p in (company.get("city"), company.get("state"), company.get("country")) if p
        )
        or None
    )
    return {
        "id": company.get("id"),
        "name": company.get("name"),
        "domain": company.get("domain"),
        "industry": company.get("industry") or company.get("primary_industry"),
        "employees": company.get("employees") or company.get("num_employees"),
        "location": location,
        "linkedin_url": company.get("linkedin_url"),
    }


async def _search(
    path: str, items_key: str, shaper, query: dict, start: int, page_size: int
) -> str:
    """Shared search runner. Returns a JSON string or ``[rocketreach-error] …``.

    Credit-free by contract — deliberately no ``_meter`` call here.
    """
    key = os.environ.get(_API_KEY_ENV)
    if not key:
        return f"[rocketreach-error] {_API_KEY_ENV} is not set — worker cannot search."
    q = _normalize_query(query if isinstance(query, dict) else {})
    if not q:
        return (
            "[rocketreach-error] `query` must contain at least one search facet "
            "(e.g. current_title, current_employer, company_intent, news_signal)."
        )
    payload = {
        "query": q,
        "start": max(1, int(start or 1)),
        "page_size": max(1, min(int(page_size or 10), _SEARCH_MAX_PAGE_SIZE)),
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                f"{ROCKETREACH_BASE_URL}{path}", json=payload, headers=_headers(key)
            )
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[rocketreach-error] HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return f"[rocketreach-error] request failed: {type(exc).__name__}"
    except ValueError:
        return "[rocketreach-error] non-JSON response"

    # Tolerate both shapes seen in the docs: keyed ({"profiles": […]}) and bare list.
    items = body.get(items_key) if isinstance(body, dict) else body
    items = items or []
    shaped = [shaper(i) for i in items if isinstance(i, dict)]
    pagination = body.get("pagination") if isinstance(body, dict) else None
    pagination = pagination or {}
    return json.dumps(
        {
            "count": len(shaped),
            "total": pagination.get("total"),
            "next_start": pagination.get("next"),
            items_key: shaped,
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def rocketreach_lookup(
    name: str = "",
    current_employer: str = "",
    linkedin_url: str = "",
    title: str = "",
    email: str = "",
) -> str:
    """Resolve ONE person's verified email + direct phone via RocketReach.

    Provide the strongest identifiers you have — ``linkedin_url`` alone is the most
    reliable; otherwise ``name`` + ``current_employer``. Spends one metered lookup
    ("export") when a contact is found; records it to the profile cost ledger.

    Args:
        name: Full name of the buyer.
        current_employer: Their company (pairs with ``name``).
        linkedin_url: Their LinkedIn profile URL (best single identifier).
        title: Optional job title to disambiguate.
        email: A known email to enrich from, if you have one.

    Returns a JSON string: ``{id, name, current_title, current_employer,
    linkedin_url, recommended_email, emails[], phones[], intent, status}``. On any
    failure returns a ``[rocketreach-error] …`` string — fall back to Vibe
    ``enrich-prospects`` or public web (mark the email unverified).
    """
    return await _lookup(
        {
            "name": name,
            "current_employer": current_employer,
            "linkedin_url": linkedin_url,
            "title": title,
            "email": email,
        }
    )


@mcp.tool()
async def rocketreach_bulk_lookup(people: list[dict]) -> str:
    """Resolve verified contacts for a small list of FINALISTS in one call.

    Each item is a query dict, e.g. ``{"name": "...", "current_employer": "..."}`` or
    ``{"linkedin_url": "..."}``. Spend an export only on scored finalists — never on
    candidates. Capped at 25 per call; a resolving lookup is metered per resolved
    person to the profile cost ledger.

    Returns a JSON string ``{resolved, count, results[]}`` where each result is the
    same shape as :func:`rocketreach_lookup` (or ``{error, query}`` for a miss). If
    the key is unset, returns a ``[rocketreach-error] …`` string.
    """
    return await _bulk(people)


@mcp.tool()
async def rocketreach_person_search(query: dict, start: int = 1, page_size: int = 10) -> str:
    """Search people by facets — credit-free (never spends a lookup/export).

    ``query`` maps facet → value or list of values. Plain facets: ``name``,
    ``current_title``, ``current_employer``, ``management_levels``, ``department``,
    ``location``, ``company_domain``, ``company_size``, ``keyword``. Signal facets
    (values are ``"Value::window"`` strings): ``job_change_signal`` ("Company
    Change" or "Promotion"; windows one_week|one_month|three_months — the
    new-in-role check), ``company_news_signal`` (e.g. "Executive
    Hire::three_months", "Funding::three_months", "Vulnerability::six_months"),
    ``company_job_posting_signal`` (e.g. "Engineering Roles::three_months"), and
    ``company_intent`` (tracked Intentsify topic names). Unknown facets pass
    through — the API owns the taxonomy.

    Returns a JSON string ``{count, total, next_start, profiles[]}``; each profile
    is ``{id, name, current_title, current_employer, location, linkedin_url}`` —
    NO contact info (resolving a finalist's email/phone is
    :func:`rocketreach_lookup`, which spends the metered unit). On failure returns
    a ``[rocketreach-error] …`` string — fall back to Vibe events + the web sweep.
    """
    return await _search(
        "/person/search", "profiles", _shape_search_person, query, start, page_size
    )


@mcp.tool()
async def rocketreach_company_search(query: dict, start: int = 1, page_size: int = 10) -> str:
    """Search companies by facets — credit-free (never spends a lookup/export).

    ``query`` maps facet → value or list of values. Plain facets: ``name``,
    ``domain``, ``industry``, ``employees`` (operators: "1000+", "<5000",
    "100-500"), ``revenue``, ``location``, ``techstack``, ``keyword``. Signal
    facets (values are ``"Value::window"`` strings; windows
    one_week|one_month|three_months|six_months|one_year): ``news_signal`` (e.g.
    "Executive Hire::three_months", "Funding::three_months",
    "Vulnerability::six_months", "Launches Product::three_months"),
    ``job_posting_signal`` (e.g. "Engineering Roles::three_months", "Machine
    Learning Roles::three_months"), and ``intent`` (tracked Intentsify topic
    names). Unknown facets pass through — the API owns the taxonomy.

    This powers the prospect skill's signal pre-flag pass: run per market with the
    segment size floor, cross hits against the candidate pool, and let the web
    sweep confirm + date what it pre-flags. Returns a JSON string ``{count, total,
    next_start, companies[]}``; each company is ``{id, name, domain, industry,
    employees, location, linkedin_url}``. On failure returns a
    ``[rocketreach-error] …`` string.
    """
    return await _search(
        "/searchCompany", "companies", _shape_search_company, query, start, page_size
    )
