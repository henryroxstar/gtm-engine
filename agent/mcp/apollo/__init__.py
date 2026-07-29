"""Apollo worker MCP — a thin stdio MCP server over the Apollo.io REST API.

Contact-resolution backstop + company intent for the ``prospect`` skill: the last
*paid* source in the enrichment waterfall (RocketReach → Vibe → Apollo → web), plus
buying-intent-filterable company search and job-posting hiring signals. It is
additive to, not a replacement for, RocketReach (contact resolution) and Vibe
Prospecting (discovery) — see
``plugin/skills/prospect/references/discovery-and-budget.md``.

Exposes seven tools the brain calls:

  - ``apollo_usage()`` — free connectivity check. Two calls: ``/v1/auth/health``
    (**note: NOT under /api/v1** — the only endpoint here that isn't) plus a
    best-effort ``usage_stats/api_usage_stats`` for live per-endpoint rate limits.
    Apollo exposes **no credit-balance endpoint** (unlike RocketReach's free
    ``account`` tool), so remaining *credits* is a floor computed from this
    worker's own ledger; remaining *rate limit* is live but master-key-only.
  - ``apollo_person_search(query, page, per_page)`` — free people search
    (``mixed_people/api_search``; documented as requiring a **master** API key).
    Results are privacy-obfuscated: ``first_name`` + a masked
    ``last_name_obfuscated``, title, org name, and ``has_email``/
    ``has_direct_phone`` flags — never an email, phone, LinkedIn URL, or domain.
    The chaining path is search → take the Apollo ``id`` → ``apollo_person_enrich``.
  - ``apollo_person_enrich(...)`` — resolve ONE person's verified email
    (``people/match``). Metered: 1 credit only when a match with an email is
    found. **Phone reveal is not exposed** — see the module-level note below.
  - ``apollo_bulk_person_enrich(people)`` — resolve up to 10 people in one call
    (``people/bulk_match``). Metered per resolved person.
  - ``apollo_company_search(query, page, per_page)`` — company search
    (``mixed_companies/search``), including Apollo's buying-intent filters.
    Metered: 1 credit per page, regardless of hit count (Apollo bills this
    endpoint per call, not per match).
  - ``apollo_company_enrich(...)`` — enrich one company
    (``organizations/enrich``). Metered: 1 credit per successful call.
  - ``apollo_job_postings(organization_id, page, per_page)`` — hiring-signal feed
    (``organizations/{id}/job_postings``). Metered: 1 credit per page.

Boundary (§R6 — all external I/O via MCP): the brain passes query parameters in and
gets structured data back; it never sees ``APOLLO_API_KEY`` and never makes the HTTP
call. The key is read from the process env (Doppler-injected at spawn; see
:func:`agent.mcp_config.build_mcp_servers`), never on the command line, never echoed.

Quota model: Apollo credits are a **monthly plan allocation**, not per-row dollars
(same shape as RocketReach's flat subscription — see ``_USD_PER_CREDIT``, which
defaults to 0). The worker writes its OWN cost record to
``content/<profile>/costs.jsonl`` after each metered call, and hard-stops BEFORE any
HTTP once ``APOLLO_MONTHLY_CREDIT_CAP`` (mirroring PROFILE.md §"Connector plans &
entitlements" → ``apollo.monthly_allowance``) is exhausted.

**Phone reveal is deliberately unsupported, not merely defaulted off.** Apollo's
``reveal_phone_number=true`` (a) costs 8 additional credits per match and (b)
resolves ASYNCHRONOUSLY via a caller-supplied ``webhook_url`` — Apollo POSTs the
result to it once ready. This headless deployment has no inbound path for that (the
same reason ``agent/mcp/rocketreach`` loops synchronous lookups instead of
RocketReach's native async bulk endpoint). The enrich tools therefore never accept
or forward a phone-reveal parameter; ``reveal_phone_number`` is hardcoded ``false``
in every request this worker sends.

Robustness contract: every tool returns a string. On any failure (no key, HTTP
error, malformed body, no match) it returns an ``[apollo-error] …`` string rather
than raising — so a worker outage degrades to "the brain falls back to Vibe / public
web (unverified)", it never breaks the run or the SDK ↔ MCP connection.

.. warning::
   **This module has never been run against the live Apollo API.** Every endpoint
   path, request shape, and response field name here is derived from Apollo's
   published docs; the unit tests pin that *documented* contract, not an observed
   one. Doc-reading has already proven fallible for this exact integration — **nine**
   real defects have been found and fixed this way on 2026-07-27. Four in the first
   pass (``auth/health`` under the wrong base, ``total_entries`` nesting, the
   obfuscated person-search shape, a missing ``id`` identifier) and five in a second
   pass against Apollo's OpenAPI spec and documented response examples: the
   *company*-search shape (search returns none of ``industry`` /
   ``estimated_num_employees`` / location — those are enrichment-only), the dropped
   ``intent_strength``/``show_intent`` fields (which are the entire point of calling
   company search here), bulk metering that counted returned emails instead of
   Apollo's own ``credits_consumed`` (under-metering real spend), positional
   query-mapping that mislabels contacts when Apollo drops records, and a missing
   ``employment_history`` fallback that left every bulk-enriched contact with a null
   ``organization_name``.

   A **third pass** (current authentication page + the People API Search parameter list)
   found three more — one of them a **correction to a pass-1 "fix"**: the auth
   health-check is ``https://api.apollo.io/api/v1/auth/health``, under the normal base,
   not the pre-migration ``/v1/auth/health`` that pass 1 hardcoded on a stale reading;
   both are now tried, documented-first. Also: Apollo's ``email_status`` vocabulary is
   exactly ``verified`` / ``unverified`` / ``likely to engage`` / ``unavailable``, and
   the consolidator had been matching *invented* labels ("apollo likely"), so three of
   those four silently reached the hold queue; and job postings bill per PAGE with a far
   larger page allowance than search, so clamping them to the search limit cost up to
   100x the credits for identical data.

   A **fourth pass** finally read the right artifact — Apollo's published OpenAPI spec at
   ``https://docs.apollo.io/openapi/apollo-rest-api.json`` — instead of the rendered docs,
   and found four more, two of them serious: **(9)** the spec declares every filter and
   identifier on ``/mixed_people/api_search``, ``/people/match`` and
   ``/mixed_companies/search`` as ``in: "query"`` with **no request body at all**, spelled
   with suffixes (``person_titles[]``, ``revenue_range[min]``) — this worker was sending
   bare-keyed JSON bodies, so on a strict reading none of its filters applied and a search
   would return the *unfiltered universe* (free but useless for people, **1 credit for
   garbage** on companies); **(10)** Apollo returns the literal placeholder
   ``email_not_unlocked@domain.com`` for records not unlocked for your team, and this
   worker treated any truthy ``email`` as resolved — the only defect found in four passes
   that manufactures a **wrong** contact instead of a missing one, and one that appears
   nowhere in the spec; **(11)** ``/auth/health`` is not in the spec at all (zero ``auth/*``
   or ``health/*`` paths), so the preflight now leads with the spec-documented, credit-free,
   non-master ``/users/api_profile`` and keeps both health paths as fallbacks; **(12)**
   search caps at 500 pages / 50,000 records, so unclamped deep paging burns credits on
   company search for guaranteed-empty results.

   Three patterns worth carrying forward. **(1) Apollo's per-endpoint shapes and limits
   differ more than its docs' prose suggests** — risk concentrates wherever one shaper or
   one constant is reused across two endpoints. **(2) A "verified" fix is only as good as
   the page it came from** — Apollo's docs still carry pre-migration paths. **(3) Prefer the
   machine-readable spec to rendered docs**: three rounds of defects came from reading
   prose, and the fourth round's biggest finds were a single ``json.load`` away the whole
   time. Note the spec is necessary but not sufficient — ``email_not_unlocked`` is real and
   absent from it, so behavioural quirks still need support/community corroboration.

   The API-key path is gated behind a paid Apollo plan, which this deployment has
   deliberately not bought, so this is expected to stay unverified.
   **If you set ``APOLLO_API_KEY``, treat the first run as a smoke test:** confirm
   each tool returns real data before trusting the enrichment waterfall, and set
   ``APOLLO_MONTHLY_CREDIT_CAP`` at the same time. Tracked in ``PENDING.md``
   ("Apollo — live verification gap").

Run it with::

    python -m agent.mcp.apollo --transport stdio
"""
