"""RocketReach worker MCP — a thin stdio MCP server over the RocketReach REST API.

Contact resolution + signal search for the ``prospect`` skill: turn a named buyer
into a verified email + direct phone (the metered unit), and search people/companies
by intent, news, job-posting, and job-change facets (credit-free). It is the
enrichment + trigger-signal counterpart to Vibe Prospecting (discovery + Bombora
topic-intent) — see ``plugin/skills/prospect/references/discovery-and-budget.md``.

Exposes four tools the brain calls:

  - ``rocketreach_lookup(name, current_employer, linkedin_url, title, email)`` —
    resolve ONE person (synchronous ``person/lookup``, polling ``person/checkStatus``
    while the record is still "searching"). Returns a structured JSON record.
  - ``rocketreach_bulk_lookup(people)`` — resolve a small list of FINALISTS by
    looping the synchronous lookup server-side and aggregating. (RocketReach's
    native ``person/bulkLookup`` is async — it requires ≥10 profiles and a webhook
    receiver, which this headless deployment has no inbound path for; sequential
    sync lookups give the brain the same one-call ergonomics for a ≤N finalist set
    without opening a webhook.)
  - ``rocketreach_person_search(query, start, page_size)`` — credit-free people
    search (``person/search``): plain facets plus ``job_change_signal`` (the
    new-in-role check), ``company_news_signal``, ``company_job_posting_signal``,
    ``company_intent``. Identity fields only — never contact info.
  - ``rocketreach_company_search(query, start, page_size)`` — credit-free company
    search (``searchCompany``): plain facets plus ``news_signal``,
    ``job_posting_signal``, ``intent`` — the prospect skill's signal pre-flag pass.

Boundary (§R6 — all external I/O via MCP): the brain passes query parameters in and
gets structured data back; it never sees ``ROCKETREACH_API_KEY`` and never makes the
HTTP call. The key is read from the process env (Doppler-injected at spawn; see
:func:`agent.mcp_config.build_mcp_servers`), never on the command line, never echoed.

Quota model: RocketReach person **lookups** are the metered/finite unit on the plan
(the UI calls them "exports"). The worker writes its OWN cost record to
``content/<profile>/costs.jsonl`` after each resolving call — units counted so the
PROFILE monthly cap stays honest — with a per-lookup USD rate that defaults to 0
(flat subscription; the marginal dollar is ~0, the constraint is the count).
**Searches are credit-free and therefore never metered** — the signal pre-flag pass
and new-in-role check must stay free to run on every candidate.

Robustness contract: every tool returns a string. On any failure (no key, HTTP
error, malformed body, no match) it returns a ``[rocketreach-error] …`` string
rather than raising — so a worker outage degrades to "the brain falls back to Vibe /
public web (unverified)", it never breaks the run or the SDK ↔ MCP connection.

Run it with::

    python -m agent.mcp.rocketreach --transport stdio
"""
