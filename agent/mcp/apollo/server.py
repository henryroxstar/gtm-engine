"""The Apollo worker MCP server (FastMCP, stdio).

A thin wrapper over the Apollo.io REST API for the ``prospect`` skill: person
search + enrichment (email), company search (incl. buying-intent filters) +
enrichment, and job-posting hiring signals. See the package docstring
(:mod:`agent.mcp.apollo`) for the boundary + quota model, and the
phone-reveal-is-unsupported rationale.

Cost metering at the code boundary (NIST AU-12; budget integrity): the worker writes
its OWN cost record to ``content/<profile>/costs.jsonl`` after each metered call —
the component that spends the finite unit records the spend. The owning profile is
passed in at spawn via ``GTM_PROFILE``; if it is absent or the ledger write fails,
metering is skipped silently — it must never break a call.

Robustness contract: every tool returns a string. On any failure it returns an
``[apollo-error] …`` string rather than raising — so the brain gets a tool result it
can react to (fall back to Vibe / public web), and the SDK ↔ MCP connection never
breaks.
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# --- Apollo wiring -------------------------------------------------------------- #
# Every endpoint path, parameter placement, and response top-level key here is checked
# against Apollo's PUBLISHED OpenAPI spec — https://docs.apollo.io/openapi/apollo-rest-api.json
# (3.1.0, 71 paths, server `https://api.apollo.io/api/v1`), read 2026-07-27. Prefer that
# artifact over the rendered reference pages when the two disagree: three separate rounds of
# defects in this module came from interpreting docs prose, and the spec is the thing Apollo
# regenerates on every docs publish.
#
# Confirmed from the spec: POST /mixed_people/api_search, POST /people/match (response
# `{request_id, person}`), POST /people/bulk_match (the only one with a requestBody),
# POST /mixed_companies/search, GET /organizations/enrich (response `{organization}`),
# GET /organizations/{organization_id}/job_postings, POST /usage_stats/api_usage_stats.
#
# Auth is the ``X-Api-Key`` header. Base URL is overridable for tests/misconfiguration
# recovery — no secret in this module (the key is read at call time).
APOLLO_BASE_URL = os.getenv("APOLLO_BASE_URL", "https://api.apollo.io/api/v1").rstrip("/")
_API_KEY_ENV = "APOLLO_API_KEY"
_HTTP_TIMEOUT_S = 30.0

# The auth health-check. Apollo's CURRENT authentication reference gives the canonical curl as
# `--url 'https://api.apollo.io/api/v1/auth/health'` — i.e. it IS under the /api/v1 base, like
# every other endpoint here (re-verified 2026-07-27 against the live page). Older Apollo docs used
# the pre-migration `https://api.apollo.io/v1/auth/health`, which some third-party guides still
# repeat; that legacy form is kept below purely as a fallback.
#
# This matters more than it looks: the preflight is what decides "is Apollo connected this run?",
# so a 404 here reports a perfectly valid key as a dead connector and silently drops Apollo out of
# the waterfall. Because the two forms are one path segment apart and the docs have carried both,
# we try the documented one and fall back rather than betting the preflight on a single reading.
APOLLO_AUTH_HEALTH_URL = os.getenv("APOLLO_AUTH_HEALTH_URL", f"{APOLLO_BASE_URL}/auth/health")
_APOLLO_AUTH_HEALTH_LEGACY_URL = "https://api.apollo.io/v1/auth/health"

# …and the reason the preflight no longer LEADS with auth/health: that path does not exist
# in Apollo's published OpenAPI spec at all (71 paths, zero auth/* or health/*). It is real
# — the authentication guide's curl uses it — but it is undocumented-by-spec, which makes it
# the weakest thing to hang "is Apollo connected?" on. `/users/api_profile` IS in the spec,
# costs no credits, needs no master key, and is the same endpoint Apollo's own hosted MCP
# exposes as its profile/credit preflight tool. So: profile first, health as the fallback.
_USER_PROFILE_PATH = "/users/api_profile"

# Free (0-credit) team rate-limit/usage report. POST, and documented as requiring a MASTER api
# key — a non-master key gets 401/403, which `_usage` tolerates rather than failing the preflight.
_USAGE_STATS_PATH = "/usage_stats/api_usage_stats"

# Apollo's documented cap: "You can enrich up to 10 people per request."
_BULK_MAX = 10

# Apollo search pages support up to 100 results/page (vs RocketReach's 25) — but we
# keep the same "keep pages compact, let the brain paginate" philosophy.
# People/company search cap out at a 50,000-record display limit — "100 records per page,
# up to 500 pages" (OpenAPI spec endpoint descriptions). Paging past 500 returns nothing, so
# a deep-paging loop is wasted effort — and on company search, which bills per page, wasted
# CREDITS. Clamped rather than merely documented for that reason.
_SEARCH_MAX_PAGE_SIZE = 100
_SEARCH_MAX_PAGE = 500
_DEFAULT_PAGE_SIZE = 25

# Job postings bill **1 credit per page regardless of page size**, and Apollo allows far
# larger pages here than on search (documented "up to 10,000 results per page"). Clamping
# this to the search limit of 100 would make fetching a big employer's postings cost up to
# 100x more credits for identical data. The default stays small for context economy — the
# caller raises per_page deliberately when it wants breadth on one credit.
_JOB_POSTINGS_MAX_PAGE_SIZE = 10_000

# Per-credit USD rate for the cost ledger. Apollo credits are a monthly PLAN
# ALLOCATION, not a per-call dollar charge — same shape as RocketReach's flat
# subscription (see agent/mcp/rocketreach/server.py). Defaults to 0.0 so we never
# invent a dollar figure we can't stand behind; the real constraint is the credit
# COUNT against APOLLO_MONTHLY_CREDIT_CAP.
_USD_PER_CREDIT = float(os.getenv("APOLLO_USD_PER_CREDIT", "0") or 0)

# Monthly CREDIT allowance (the finite unit — see _meter). Because cost_usd is 0 by
# design, the dollar-based §R2 budget guard can never trip on Apollo spend. This is
# the count-based analogue: a hard stop once the month's logged `units.credits`
# would exceed the plan's allowance. Injected via Doppler, mirroring PROFILE.md
# §"Connector plans & entitlements" (settings.connectors[].monthly_allowance).
# Unset/unparseable ⇒ uncapped (backwards compatible).
_MONTHLY_CREDIT_CAP_ENV = "APOLLO_MONTHLY_CREDIT_CAP"


def _monthly_credit_cap() -> int | None:
    """The configured monthly credit allowance, or ``None`` when uncapped/misconfigured."""
    raw = (os.getenv(_MONTHLY_CREDIT_CAP_ENV) or "").strip()
    if not raw:
        return None
    try:
        cap = int(raw)
    except ValueError:
        return None
    return cap if cap > 0 else None


def _allowance_remaining() -> int | None:
    """Credits remaining under this month's allowance, or ``None`` when uncapped.

    Fail-OPEN on any ledger-read error (consistent with ``gtm_core.metering.check_budget``
    and ``agent.mcp.rocketreach``'s identical guard): a metering hiccup must not block
    a legitimate call, and the per-call caps below bound the blast radius. Returns a
    value that may be ``<= 0`` when the cap is already spent.
    """
    cap = _monthly_credit_cap()
    if cap is None:
        return None
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile:
        return None  # no profile ⇒ no ledger to sum ⇒ cannot enforce; stay uncapped
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        used = Ledgers(Config.from_env(), profile).month_unit_total("apollo", "credits")
    except Exception:  # noqa: BLE001 — fail open on any metering read error
        return None
    return int(cap - used)


def _cap_guard(needed: int) -> str | None:
    """Return an ``[apollo-cap] …`` refusal string if ``needed`` credits would
    exceed the remaining allowance, else ``None`` (proceed)."""
    remaining = _allowance_remaining()
    if remaining is None:
        return None
    if remaining <= 0:
        return (
            "[apollo-cap] monthly credit allowance exhausted. Person/company SEARCH "
            "remains free — do NOT retry enrichment/search-with-intent calls; report "
            "to the operator."
        )
    if needed > remaining:
        return (
            f"[apollo-cap] this call needs ~{needed} credits, exceeding the remaining "
            f"allowance ({remaining}); resubmit a smaller batch or wait for next month."
        )
    return None


mcp = FastMCP("apollo")


# Apollo does not always return a real address in the `email` field. When a person's email
# is not unlocked/revealed for your team, it returns the literal placeholder
# `email_not_unlocked@domain.com` (documented in Apollo's support material and reproduced
# widely by API users; it does NOT appear anywhere in the OpenAPI spec, which is exactly why
# it is easy to miss). Treating that string as a resolved address is the worst failure mode
# in this integration — every other bug here costs a MISSING contact that falls through to
# the web path, but this one manufactures a WRONG contact: a syntactically valid,
# undeliverable address that would pass the consolidator, land in ready-to-load.csv, and be
# enrollable in a sequence. Matched on the local part so any domain variant is caught.
_PLACEHOLDER_EMAIL_MARKERS = ("email_not_unlocked", "not_unlocked@", "email_not_found")


def _is_placeholder_email(email: str | None) -> bool:
    """True when Apollo returned a locked/placeholder token instead of a real address."""
    if not email:
        return False
    return any(marker in email.lower() for marker in _PLACEHOLDER_EMAIL_MARKERS)


def _real_email(person: dict) -> str | None:
    """The person's email, or ``None`` when absent or a locked placeholder."""
    email = person.get("email")
    if not isinstance(email, str) or not email.strip():
        return None
    return None if _is_placeholder_email(email) else email.strip()


def _headers(key: str) -> dict[str, str]:
    return {"X-Api-Key": key, "Content-Type": "application/json", "Accept": "application/json"}


def _query_params(payload: dict) -> list[tuple[str, str]]:
    """Encode a filter dict into Apollo's documented QUERY-parameter form.

    Apollo's published OpenAPI spec (``docs.apollo.io/openapi/apollo-rest-api.json``,
    read 2026-07-27) declares the search and match endpoints' filters as
    ``in: "query"`` with **suffixed names** — literally ``person_titles[]``,
    ``organization_num_employees_ranges[]``, ``revenue_range[min]`` — and declares no
    request body at all for ``/mixed_people/api_search``, ``/people/match`` and
    ``/mixed_companies/search`` (only ``/people/bulk_match`` has one).

    This worker previously sent those filters as a JSON body with bare keys
    (``person_titles``). Apollo's Rails backend does merge body params, which is why the
    body form is widely used and appears to work — but betting the filters on undocumented
    merge behaviour is how a search silently returns the *unfiltered universe*: free but
    useless for people search, and **1 credit for garbage** on company search. So we now
    emit the documented query form as well, and keep the body (identical values, so the
    two can never disagree whichever one Apollo reads).

    The suffix matters, not just the placement: Rails parses ``a[]=1&a[]=2`` into a list,
    but ``a=1&a=2`` into the single value ``2``. Sending an un-suffixed repeated key would
    silently narrow a multi-value filter to its last element.

    Accepts caller keys with or without the suffix, so the skill's documented vocabulary
    (``person_titles``) and Apollo's literal spelling (``person_titles[]``) both work.
    """
    out: list[tuple[str, str]] = []
    for key, value in (payload or {}).items():
        name = str(key)
        base = name[:-2] if name.endswith("[]") else name
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if item is not None:
                    out.append((f"{base}[]", str(item)))
        elif isinstance(value, dict):
            # Range-style filters: revenue_range[min] / [max].
            for sub, sub_value in value.items():
                if sub_value is not None:
                    out.append((f"{base}[{sub}]", str(sub_value)))
        elif isinstance(value, bool):
            out.append((base, "true" if value else "false"))
        elif value is not None:
            out.append((base, str(value)))
    return out


def _shape_person(person: dict) -> dict:
    """Reduce an Apollo person object to the fields the brain actually uses.

    Defensive by construction (``.get()`` throughout) — Apollo's field names here
    are best-effort per its commonly-documented schema; the live smoke test in the
    integration's verification phase is the source of truth if a field comes back
    empty that shouldn't.
    """
    org = person.get("organization") or {}
    phones = person.get("phone_numbers") or []
    clean_phones = [
        (p.get("sanitized_number") or p.get("raw_number")) for p in phones if isinstance(p, dict)
    ]
    # `/people/bulk_match` matches carry `organization_id` + `employment_history` but NO nested
    # `organization` object (unlike `/people/match`, which does). Without this fallback every
    # bulk-enriched contact came back with organization_name: null — i.e. a resolved contact the
    # prospect skill could not attribute to an account. Verified 2026-07-27 against the documented
    # bulk-people-enrichment response example.
    current_job = next(
        (
            j
            for j in (person.get("employment_history") or [])
            if isinstance(j, dict) and j.get("current")
        ),
        {},
    )
    return {
        "id": person.get("id"),
        "name": person.get("name")
        or " ".join(p for p in (person.get("first_name"), person.get("last_name")) if p),
        "title": person.get("title") or current_job.get("title"),
        # Never surface a locked placeholder as if it were a real address.
        "email": _real_email(person),
        "email_status": person.get("email_status"),
        # Apollo's own signal for whether this record is actually unlocked for the team.
        "revealed_for_current_team": person.get("revealed_for_current_team"),
        "linkedin_url": person.get("linkedin_url"),
        "organization_name": (
            org.get("name")
            or person.get("organization_name")
            or current_job.get("organization_name")
        ),
        "organization_domain": org.get("primary_domain") or org.get("website_url"),
        # Present only if a prior waterfall already resolved one — this worker never
        # requests phone reveal itself (see module docstring).
        "phones": [p for p in clean_phones if p],
    }


def _shape_person_search(person: dict) -> dict:
    """Reduce a *search* hit to what Apollo actually returns there.

    Deliberately a DIFFERENT shape from :func:`_shape_person` (which handles
    enrichment payloads). Apollo's People API Search response is privacy-obfuscated
    and much thinner than an enriched person: it carries ``first_name`` +
    ``last_name_obfuscated`` (e.g. ``"Do***e"`` — the real surname is NOT returned),
    ``title``, ``has_email``/``has_direct_phone`` availability booleans, and an
    ``organization`` with only ``name`` + ``has_*`` flags — **no** ``name``,
    ``email``, ``linkedin_url``, or ``primary_domain``. Verified 2026-07-27 against
    the documented response example.

    The practical consequence, which the prospect skill relies on: search gives you
    an ``id`` and enough to judge fit, and you resolve the actual person by passing
    that ``id`` to :func:`apollo_person_enrich`.
    """
    org = person.get("organization") or {}
    return {
        "id": person.get("id"),
        "first_name": person.get("first_name"),
        # Apollo masks the surname in search results; surfaced under its real key so
        # nothing downstream mistakes it for a usable last name.
        "last_name_obfuscated": person.get("last_name_obfuscated"),
        "title": person.get("title"),
        "organization_name": org.get("name") or person.get("organization_name"),
        # Availability flags, not the values themselves — useful for prioritizing
        # which finalists are worth spending an enrich credit on. NOTE the asymmetry
        # in Apollo's own payload: `has_email` is a real boolean, but
        # `has_direct_phone` is the STRING "Yes"/"No". Passed through verbatim rather
        # than coerced, so a downstream truthiness test can't be fooled by "No" — but
        # anything comparing it must compare to the string.
        "has_email": person.get("has_email"),
        "has_direct_phone": person.get("has_direct_phone"),
    }


def _shape_company_search(company: dict) -> dict:
    """Reduce a company *search* hit to what Apollo actually returns there.

    A DIFFERENT shape from :func:`_shape_company` (enrichment), for the same reason
    :func:`_shape_person_search` differs from :func:`_shape_person`: Apollo's
    Organization Search response carries only identity/social/firmographic-lite
    fields — ``id``, ``name``, ``primary_domain``, ``website_url``, ``linkedin_url``,
    ``founded_year``, ``phone``, ``alexa_ranking`` — and **no** ``industry``,
    ``estimated_num_employees``, or ``city``/``state``/``country``. Those appear only
    on the *enrichment* endpoints. Verified 2026-07-27 against the documented
    organization-search response example.

    Critically, search results DO carry the buying-intent fields — ``intent_strength``
    and ``show_intent`` — which is the entire reason this integration calls company
    search at all (the third intent feed, alongside Vibe/Bombora and
    RocketReach/Intentsify). Dropping them would make the feed structurally incapable
    of reporting intent.
    """
    return {
        "id": company.get("id"),
        "name": company.get("name"),
        "domain": company.get("primary_domain") or company.get("website_url"),
        "linkedin_url": company.get("linkedin_url"),
        "founded_year": company.get("founded_year"),
        "phone": company.get("sanitized_phone") or company.get("phone"),
        # The intent payload. `intent_strength` is null unless the team has tracked
        # buying-intent topics configured in Apollo's web UI (Settings → Buying
        # Intent) — an unconfigured account yields nulls, which means "feed absent",
        # NOT "account is cold". `show_intent` reports whether intent is exposed for
        # this org at all.
        "intent_strength": company.get("intent_strength"),
        "show_intent": company.get("show_intent"),
        "has_intent_signal_account": company.get("has_intent_signal_account"),
        # Deliberately absent here (enrichment-only, would always be null):
        # industry, employees, location.
    }


def _shape_company(company: dict) -> dict:
    """Reduce an Apollo organization *enrichment* object to the fields the prospect
    skill records. Enrichment (unlike search) does return ``industry``,
    ``estimated_num_employees`` and the address fields — see
    :func:`_shape_company_search`."""
    location = (
        ", ".join(
            p for p in (company.get("city"), company.get("state"), company.get("country")) if p
        )
        or None
    )
    return {
        "id": company.get("id"),
        "name": company.get("name"),
        "domain": company.get("primary_domain") or company.get("website_url"),
        "industry": company.get("industry"),
        "employees": company.get("estimated_num_employees"),
        "location": location,
        "linkedin_url": company.get("linkedin_url"),
        "founded_year": company.get("founded_year"),
    }


def _meter(credits: int, op: str) -> None:
    """Append a cost record for ``credits`` to the owning profile's ledger.

    Best-effort: any failure (no profile, ledger error) is swallowed so metering can
    never break a call or the MCP connection. Always records the CREDIT COUNT (the
    finite unit) even when the USD rate is 0 (plan allocation).
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile or credits <= 0:
        return
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "apollo",
                "op": op,
                "units": {"credits": credits},
                "cost_usd": round(credits * _USD_PER_CREDIT, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort; never break a call
        return


async def _get_url(url: str, params: dict, key: str) -> tuple[dict | None, str | None]:
    """GET an ABSOLUTE url. Returns ``(body, None)`` on success or ``(None, error)``."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(url, params=params, headers=_headers(key))
            resp.raise_for_status()
            return resp.json(), None
    except httpx.HTTPStatusError as exc:
        return None, f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return None, f"request failed: {type(exc).__name__}"
    except ValueError:
        return None, "non-JSON response"


async def _get(path: str, params: dict, key: str) -> tuple[dict | None, str | None]:
    """GET a path under ``APOLLO_BASE_URL`` (/api/v1). Note the auth health-check is
    NOT under that base — see :data:`APOLLO_AUTH_HEALTH_URL`."""
    return await _get_url(f"{APOLLO_BASE_URL}{path}", params, key)


async def _post(
    path: str, payload: dict, key: str, params: dict | None = None
) -> tuple[dict | None, str | None]:
    """POST helper. Returns ``(body, None)`` on success or ``(None, error)``.

    ``params`` appends a query string — Apollo documents the enrichment reveal flags
    as query parameters even though the identifying fields go in the body."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                f"{APOLLO_BASE_URL}{path}", json=payload, params=params, headers=_headers(key)
            )
            resp.raise_for_status()
            return resp.json(), None
    except httpx.HTTPStatusError as exc:
        return None, f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return None, f"request failed: {type(exc).__name__}"
    except ValueError:
        return None, "non-JSON response"


def _require_key() -> str | None:
    return os.environ.get(_API_KEY_ENV)


# --- apollo_usage (free — the connectivity + preflight tool) -------------------- #


async def _usage() -> str:
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot reach Apollo."
    # Primary check: the spec-documented, credit-free, non-master `/users/api_profile`.
    # Falls back to auth/health (documented base first, then the pre-migration path) only
    # on 404 — a 401/403 is a real auth answer about a real endpoint and must be reported,
    # not retried elsewhere, or a bad key would masquerade as a missing endpoint.
    body, err = await _get(_USER_PROFILE_PATH, {}, key)
    if err and err.startswith("HTTP 404"):
        for fallback in (APOLLO_AUTH_HEALTH_URL, _APOLLO_AUTH_HEALTH_LEGACY_URL):
            body, err = await _get_url(fallback, {}, key)
            if not err or not err.startswith("HTTP 404"):
                break
    if err:
        return f"[apollo-error] {err}"

    # Secondary, best-effort: the team's real per-endpoint rate limits. Free, but
    # master-key-only — a non-master key 401/403s here, which must NOT fail the
    # preflight (the key is demonstrably valid; it just lacks this one scope).
    rate_limits: dict | str
    stats_body, stats_err = await _post(_USAGE_STATS_PATH, {}, key)
    if stats_err:
        rate_limits = f"unavailable ({stats_err} — this endpoint needs a master API key)"
    else:
        rate_limits = (stats_body or {}).get("usage_stats") or stats_body or {}

    return json.dumps(
        {
            # `/users/api_profile` answers with account/team fields; auth/health answers
            # with `is_logged_in`. Reaching either at all is the connectivity signal.
            "is_logged_in": (body or {}).get(
                "is_logged_in", bool(body) if body is not None else None
            ),
            "monthly_credit_cap": _monthly_credit_cap(),
            "credits_remaining_this_month": _allowance_remaining(),
            "rate_limits": rate_limits,
            "note": (
                "Apollo's REST API exposes no CREDIT-balance endpoint, so "
                "credits_remaining_this_month is computed from this worker's own cost "
                "ledger against APOLLO_MONTHLY_CREDIT_CAP — a floor, not Apollo's own "
                "number; null means the cap is unset (uncapped). `rate_limits` IS live "
                "from Apollo (per-endpoint day/hour/minute limit + consumed + left_over) "
                "when the key is a master key, and reports why it's absent otherwise."
            ),
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def apollo_usage() -> str:
    """Confirm Apollo connectivity and report the ledger-computed remaining credit
    allowance. FREE — call this first, every run, before any other Apollo tool.

    Unlike RocketReach's ``account`` tool, Apollo's REST API does not publish a
    live credit-balance endpoint — the "remaining credits" figure here is this
    worker's own count against ``APOLLO_MONTHLY_CREDIT_CAP``, not Apollo's own
    number. Treat it as a floor, not a live truth.

    Returns a JSON string ``{is_logged_in, monthly_credit_cap,
    credits_remaining_this_month, note}``. On failure (missing key, network error)
    returns an ``[apollo-error] …`` string — treat Apollo as disconnected this run.
    """
    return await _usage()


# --- apollo_person_search (free) ------------------------------------------------- #


def _clamp_page_size(per_page: int, maximum: int = _SEARCH_MAX_PAGE_SIZE) -> int:
    return max(1, min(int(per_page or _DEFAULT_PAGE_SIZE), maximum))


def _clamp_page(page: int) -> int:
    """Clamp to Apollo's 500-page display ceiling. Past it the API returns nothing —
    which on company search would still cost a credit per call."""
    return max(1, min(int(page or 1), _SEARCH_MAX_PAGE))


@mcp.tool()
async def apollo_person_search(query: dict, page: int = 1, per_page: int = 25) -> str:
    """Search Apollo's 240M-contact database by facets — FREE (never spends a credit).

    ``query`` maps Apollo's People API Search filter names → value or list of
    values, forwarded as-is (unknown keys pass through — the API owns the
    taxonomy). Common filters: ``person_titles`` (list), ``person_seniorities``
    (list), ``person_locations`` (list), ``q_keywords``, ``organization_locations``
    (list), ``q_organization_domains_list`` (list), ``organization_num_employees_ranges``
    (list of "min,max" strings), ``contact_email_status`` (list).

    **Apollo obfuscates search results** — you get ``first_name`` plus a masked
    ``last_name_obfuscated`` (e.g. ``"Do***e"``), the title, the org name, and
    availability flags: ``has_email`` (a boolean) and ``has_direct_phone`` (the
    STRING ``"Yes"``/``"No"``, not a boolean — compare it as a string). There is
    **no** real surname, email, phone, LinkedIn URL, or company domain here. The
    intended flow is: search to judge fit and collect the Apollo ``id``, then pass
    that ``id`` to :func:`apollo_person_enrich` (the metered call) to resolve the
    actual person. Use ``has_email`` to avoid spending a credit on someone Apollo
    has no email for.

    Apollo documents this endpoint as requiring a **master** API key; a non-master
    key may get a 403 even though other tools here work.

    Returns a JSON string ``{count, total, page, per_page, people[]}``; each person
    is ``{id, first_name, last_name_obfuscated, title, organization_name,
    has_email, has_direct_phone}``. On failure returns an ``[apollo-error] …``
    string — fall back to RocketReach search or Vibe events.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot search."
    if not isinstance(query, dict) or not query:
        return "[apollo-error] `query` must contain at least one Apollo People API Search filter."
    payload = {**query, "page": _clamp_page(page), "per_page": _clamp_page_size(per_page)}
    body, err = await _post("/mixed_people/api_search", payload, key, params=_query_params(payload))
    if err:
        return f"[apollo-error] {err}"
    body = body or {}
    people = body.get("people") or body.get("contacts") or []
    shaped = [_shape_person_search(p) for p in people if isinstance(p, dict)]
    # People search puts `total_entries` at the TOP LEVEL — unlike company search,
    # which nests it under `pagination`. Read both, top level first (verified
    # 2026-07-27 against the documented response examples for each endpoint).
    pagination = body.get("pagination") or {}
    return json.dumps(
        {
            "count": len(shaped),
            "total": body.get("total_entries", pagination.get("total_entries")),
            "page": pagination.get("page", page),
            "per_page": pagination.get("per_page", per_page),
            "people": shaped,
        },
        ensure_ascii=False,
    )


# --- apollo_person_enrich / apollo_bulk_person_enrich (metered) ----------------- #


def _person_enrich_params(
    name: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    organization_name: str = "",
    domain: str = "",
    linkedin_url: str = "",
    id: str = "",  # noqa: A002
) -> dict:
    """Build a people/match (or bulk_match ``details[]``) entry from whatever
    identifiers are present. Blanks are dropped; ``reveal_phone_number`` is always
    present and always ``False``."""
    out = {
        k: v
        for k, v in {
            "id": id,  # Apollo's own person id — the strongest identifier, and what
            #             apollo_person_search returns for chaining into enrichment.
            "name": name,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "organization_name": organization_name,
            "domain": domain,
            "linkedin_url": linkedin_url,
        }.items()
        if v
    }
    # Hardcoded, never parameterized — see module docstring "Phone reveal is
    # deliberately unsupported". Never set true: it requires an inbound webhook_url
    # this deployment cannot receive, and costs 8 extra credits per match.
    out["reveal_phone_number"] = False
    return out


@mcp.tool()
async def apollo_person_enrich(
    name: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    organization_name: str = "",
    domain: str = "",
    linkedin_url: str = "",
    id: str = "",  # noqa: A002 — Apollo's own field name; keep the API's vocabulary
) -> str:
    """Resolve ONE person's verified email via Apollo — the enrichment backstop
    used after both RocketReach and Vibe ``enrich-prospects`` have missed.

    Provide the strongest identifiers you have. ``id`` (an Apollo person id, as
    returned by :func:`apollo_person_search`) is the most reliable and is the
    intended way to chain search → enrich; otherwise ``linkedin_url`` alone, or
    ``name`` (or ``first_name``+``last_name``) + ``organization_name``/``domain``.
    Spends 1 credit ONLY when Apollo returns a match with an email (Apollo's own
    billing rule — a miss costs nothing). **Never reveals a phone number** — see the
    module docstring; the phone-reveal path needs an inbound webhook this deployment
    doesn't expose.

    Returns a JSON string: ``{id, name, title, email, email_status, linkedin_url,
    organization_name, organization_domain, phones}`` (``phones`` is always empty
    from this tool). On any failure, or when Apollo returns no email, returns an
    ``[apollo-error] …`` string — mark the contact **unverified** from public web;
    this is the last paid source in the waterfall.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot resolve contacts."
    params = _person_enrich_params(
        name, first_name, last_name, email, organization_name, domain, linkedin_url, id
    )
    if len(params) <= 1:  # only reveal_phone_number present ⇒ no real identifier given
        return (
            "[apollo-error] no identifying fields (need an Apollo id, linkedin_url, "
            "name/first+last name with organization_name or domain, or email)."
        )
    cap_err = _cap_guard(1)
    if cap_err:
        return cap_err
    body, err = await _post(
        "/people/match",
        params,
        key,
        # The spec declares every identifier as a query param and no request body here.
        # The reveal flags ride along in the same encoding, so they cannot be dropped.
        params=_query_params(
            {**params, "reveal_personal_emails": False, "reveal_phone_number": False}
        ),
    )
    if err:
        return f"[apollo-error] {err}"
    person = (body or {}).get("person") or body or {}
    shaped = _shape_person(person)
    if not shaped.get("email"):
        if _is_placeholder_email(person.get("email")):
            # Distinguish "Apollo has no email" from "Apollo has one but it isn't unlocked
            # for this team" — same outcome for the waterfall, but very different fix.
            return (
                "[apollo-error] Apollo returned a locked placeholder instead of an email "
                "(the record is not unlocked for this team). Treat as unresolved and fall "
                "through to the web path — do NOT use the placeholder address."
            )
        return "[apollo-error] no email match found for this person"
    _meter(1, "person_enrich")
    return json.dumps(shaped, ensure_ascii=False)


@mcp.tool()
async def apollo_bulk_person_enrich(people: list[dict]) -> str:
    """Resolve verified emails for up to 10 FINALISTS in one call (Apollo's
    documented batch cap).

    Each item is a dict with the same fields as :func:`apollo_person_enrich`
    (``id`` — an Apollo person id from :func:`apollo_person_search`, the strongest
    identifier — or ``linkedin_url``, or ``name``/``first_name``+``last_name`` with
    ``organization_name``/``domain``, or ``email``). Spend this only on scored finalists both
    RocketReach and Vibe already missed — never on candidates. 1 credit per
    person Apollo actually matches with an email (misses are free). Phone reveal
    is never requested — see the module docstring.

    **Rate limit — much tighter than the other endpoints:** Apollo allows only
    **100 bulk_match calls/hour and 20/minute** (vs 600/hour for person search and
    single match). Batch finalists into one call rather than looping singles.

    Returns a JSON string ``{resolved, count, credits_consumed, requested,
    missing_records, query_mapping_reliable, results[]}``. Each result is the same
    shape as :func:`apollo_person_enrich`, or ``{error, query}`` for a miss.
    ``credits_consumed`` is **Apollo's own figure** (it can exceed ``resolved``: a
    record can consume a credit for demographics while returning no email). When
    ``query_mapping_reliable`` is false, Apollo returned a different number of
    matches than queries, so a miss's ``query`` is null rather than guessed. If the
    key is unset, over the batch cap, or over the remaining allowance, returns an
    ``[apollo-error]``/``[apollo-cap] …`` string.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot resolve contacts."
    if not isinstance(people, list) or not people:
        return "[apollo-error] `people` must be a non-empty list of identifier dicts."
    if len(people) > _BULK_MAX:
        return (
            f"[apollo-error] {len(people)} queries exceeds Apollo's {_BULK_MAX}-person batch cap."
        )
    cap_err = _cap_guard(len(people))
    if cap_err:
        return cap_err

    details = []
    for p in people:
        p = p if isinstance(p, dict) else {}
        details.append(
            _person_enrich_params(
                p.get("name", ""),
                p.get("first_name", ""),
                p.get("last_name", ""),
                p.get("email", ""),
                p.get("organization_name", ""),
                p.get("domain", ""),
                p.get("linkedin_url", ""),
                p.get("id", ""),
            )
        )
    body, err = await _post(
        "/people/bulk_match",
        {"details": details, "reveal_phone_number": False},
        key,
        # Apollo's canonical curl passes the reveal flags as QUERY params, not body
        # fields. Sending both makes the never-reveal-a-phone guarantee explicit on
        # whichever one the API actually reads, rather than relying on its default.
        params={"reveal_personal_emails": "false", "reveal_phone_number": "false"},
    )
    if err:
        return f"[apollo-error] {err}"
    body = body or {}
    matches = body.get("matches") or []
    # Apollo returns `matches` positionally aligned with `details` ONLY when it echoes
    # an entry per request. When it drops unmatched records the lengths diverge and
    # index-mapping a match back to its query would mislabel contacts — so only claim
    # the mapping when the lengths agree.
    aligned = len(matches) == len(details)
    results = []
    resolved = 0
    for i, m in enumerate(matches):
        shaped = _shape_person(m if isinstance(m, dict) else {})
        if shaped.get("email"):
            resolved += 1
            results.append(shaped)
        else:
            results.append(
                {"error": "no email match found", "query": details[i] if aligned else None}
            )
    # Apollo reports exactly what it charged. Prefer it over our own count: a match
    # can consume a credit for demographics while returning no email, so counting
    # emails UNDER-meters real spend and lets the monthly cap drift silently.
    reported = body.get("credits_consumed")
    charged = reported if isinstance(reported, int) and reported >= 0 else resolved
    _meter(charged, "bulk_person_enrich")
    return json.dumps(
        {
            "resolved": resolved,
            "count": len(results),
            "credits_consumed": charged,
            # Apollo's own reconciliation counts — surfaced so the brain can tell
            # "Apollo dropped a record" apart from "Apollo matched but had no email".
            "requested": body.get("total_requested_enrichments"),
            "missing_records": body.get("missing_records"),
            "query_mapping_reliable": aligned,
            "results": results,
        },
        ensure_ascii=False,
    )


# --- apollo_company_search (metered — 1 credit/page, incl. buying-intent filters) #


@mcp.tool()
async def apollo_company_search(query: dict, page: int = 1, per_page: int = 25) -> str:
    """Search companies by facets, including Apollo's buying-intent filters — the
    third intent feed alongside Vibe/Bombora and RocketReach/Intentsify.

    ``query`` maps Apollo's Organization Search filter names → value or list of
    values, forwarded as-is (unknown keys pass through). Confirmed filters:
    ``organization_num_employees_ranges`` (list of "min,max"),
    ``organization_locations`` (list), ``q_organization_keyword_tags`` (list),
    ``q_organization_name``. **Buying-intent filter names are NOT publicly
    documented by Apollo** — this worker does not invent or validate them; pass
    whatever the operator has confirmed against a live account (Settings → Buying
    Intent → tracked topics must be configured first, or the filter is ignored
    and this returns the whole universe — the same footgun as RocketReach's
    `intent` facet before its tracked topics were set).

    **Metered: 1 credit per page/call, regardless of how many results come
    back** (Apollo bills this endpoint per call, not per match — unlike person
    enrichment). Gate this behind the Step 2 budget check; it is not free like
    ``apollo_person_search``.

    **Search returns thinner company records than enrichment does.** Each company is
    ``{id, name, domain, linkedin_url, founded_year, phone, intent_strength,
    show_intent, has_intent_signal_account}``. Apollo does **not** return
    ``industry``, employee count, or location on the search endpoint — those come
    only from :func:`apollo_company_enrich`. Don't call search expecting
    firmographics.

    ``intent_strength`` is the buying-intent payload and is ``null`` unless tracked
    topics are configured in Apollo's web UI — nulls mean **the feed is absent**, not
    that the accounts are cold.

    Returns a JSON string ``{count, total, page, per_page, companies[]}``. On failure
    returns an ``[apollo-error]``/``[apollo-cap] …`` string. Note this endpoint 403s
    on a free Apollo plan (paid-only) and is capped at 600 calls/hour.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot search."
    if not isinstance(query, dict) or not query:
        return "[apollo-error] `query` must contain at least one Organization Search filter."
    cap_err = _cap_guard(1)
    if cap_err:
        return cap_err
    payload = {**query, "page": _clamp_page(page), "per_page": _clamp_page_size(per_page)}
    body, err = await _post("/mixed_companies/search", payload, key, params=_query_params(payload))
    if err:
        return f"[apollo-error] {err}"
    _meter(1, "company_search")  # billed per call per Apollo's docs, hit or miss
    companies = (body or {}).get("organizations") or (body or {}).get("accounts") or []
    shaped = [_shape_company_search(c) for c in companies if isinstance(c, dict)]
    pagination = (body or {}).get("pagination") or {}
    return json.dumps(
        {
            "count": len(shaped),
            "total": pagination.get("total_entries"),
            "page": pagination.get("page", page),
            "per_page": pagination.get("per_page", per_page),
            "companies": shaped,
        },
        ensure_ascii=False,
    )


# --- apollo_company_enrich (metered — 1 credit/successful call) ----------------- #


@mcp.tool()
async def apollo_company_enrich(
    domain: str = "", linkedin_url: str = "", name: str = "", website: str = ""
) -> str:
    """Enrich ONE company's firmographics via Apollo (industry, revenue, headcount,
    funding). Provide at least one of ``domain`` (preferred, no ``www.``/``@``),
    ``linkedin_url``, ``name``, or ``website`` — more improves match accuracy.

    Metered: 1 credit per successful call (Apollo's org-enrichment endpoint bills
    per call, same as company search — not conditional on match quality the way
    person enrichment is).

    Returns a JSON string ``{id, name, domain, industry, employees, location,
    linkedin_url, founded_year}``. On failure, or no match, returns an
    ``[apollo-error]``/``[apollo-cap] …`` string — fall back to Vibe
    ``enrich-business`` or public web.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot enrich companies."
    params = {"domain": domain, "linkedin_url": linkedin_url, "name": name, "website": website}
    params = {k: v for k, v in params.items() if v}
    if not params:
        return "[apollo-error] provide at least one of domain, linkedin_url, name, or website."
    cap_err = _cap_guard(1)
    if cap_err:
        return cap_err
    body, err = await _get("/organizations/enrich", params, key)
    if err:
        return f"[apollo-error] {err}"
    org = (body or {}).get("organization") or body or {}
    if not org.get("id") and not org.get("name"):
        return "[apollo-error] no company match found"
    _meter(1, "company_enrich")
    return json.dumps(_shape_company(org), ensure_ascii=False)


# --- apollo_job_postings (metered — 1 credit/page) ------------------------------- #


@mcp.tool()
async def apollo_job_postings(organization_id: str, page: int = 1, per_page: int = 25) -> str:
    """List a company's open job postings — the hiring-signal trigger, alongside
    RocketReach's ``job_posting_signal`` facet.

    Requires ``organization_id`` (the Apollo org ID — from :func:`apollo_company_search`
    or :func:`apollo_company_enrich`'s ``id`` field, not a domain). Metered: 1
    credit per page/call, regardless of how many postings come back.

    **Because billing is per PAGE, raise ``per_page`` rather than paginating.** Apollo
    allows far bigger pages here than on search (documented up to 10,000 per page), so
    two pages of 25 cost twice what one page of 50 costs for the same postings. The
    default stays small to protect context; widen it deliberately when you need breadth.

    Returns a JSON string ``{count, page, per_page, job_postings[]}`` with
    whatever fields Apollo returns per posting (title, posted_at, url — pass
    through as-is, this tool does not reshape postings the way person/company
    results are shaped, since the schema here is less stable). On failure returns
    an ``[apollo-error]``/``[apollo-cap] …`` string.
    """
    key = _require_key()
    if not key:
        return f"[apollo-error] {_API_KEY_ENV} is not set — worker cannot fetch job postings."
    if not organization_id or not str(organization_id).strip():
        return "[apollo-error] `organization_id` is required (the Apollo org ID, not a domain)."
    cap_err = _cap_guard(1)
    if cap_err:
        return cap_err
    body, err = await _get(
        f"/organizations/{str(organization_id).strip()}/job_postings",
        {
            "page": max(1, int(page or 1)),
            "per_page": _clamp_page_size(per_page, _JOB_POSTINGS_MAX_PAGE_SIZE),
        },
        key,
    )
    if err:
        return f"[apollo-error] {err}"
    _meter(1, "job_postings")
    postings = (
        (body or {}).get("organization_job_postings") or (body or {}).get("job_postings") or []
    )
    pagination = (body or {}).get("pagination") or {}
    return json.dumps(
        {
            "count": len(postings),
            "page": pagination.get("page", page),
            "per_page": pagination.get("per_page", per_page),
            "job_postings": postings,
        },
        ensure_ascii=False,
    )
