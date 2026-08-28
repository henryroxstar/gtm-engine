"""Syften worker MCP — a thin **read-only** stdio MCP server over the Syften JSON API.

Community social-listening ingest for the ``community-signal-analysis`` skill: pull
recent matches (Reddit, Hacker News, forums, blogs, dev communities, …), read the
account's configured filters and monitoring settings, and read account quota. That is
the whole surface — this wrapper **reads**, it never writes.

CRITICAL SECURITY INVARIANT: the wrapper makes it **structurally impossible for the
brain to change Syften configuration.** The Syften API's ``filters/set`` REPLACES the
entire configured filter set (a destructive write), and it is deliberately NOT wrapped
here — there is no set/update/delete/replace tool of any kind. Filter *changes* are a
human action: the skill can only *recommend* corrected filters (evidence-cited, in a
report the operator pastes into the Syften dashboard). This is the social-listening
analogue of the publish gate — *reading is a capability, writing is not, and writing is
not even representable in this tool surface.*

Untrusted content (§R5 / OWASP ASI01 Goal-Hijack, ASI06 Context-Poisoning): every match
body is untrusted third-party text. To keep it out of the brain's context by default and
to compute signal-quality deterministically (so injected prose can never move a number),
``syften_get_matches`` writes the raw pull to a file under the profile's content tree and
returns a **compact aggregate summary** (counts, per-filter AI accept/reject tallies) plus
that path — not the match bodies. The skill reads specific raw items only when it needs to.

Exposes these tools the brain calls (READ ONLY):

  - ``syften_get_matches(timeframe, since, filter, limit)`` — pull recent matches within a
    time window, persist the raw array, and return an aggregate summary + the file path.
  - ``syften_get_filters()`` — the account's configured filter strings.
  - ``syften_get_settings()`` — monitoring settings (filters, communities, plan).
  - ``syften_account_info()`` — subscription plan, usage, and quota.

Boundary (§R6 — all external I/O via MCP): the brain passes parameters in and gets
structured data back; it never sees ``SYFTEN_API_KEY`` and never makes the HTTP call. The
key is read from the process env (Doppler-injected at spawn; see
:func:`agent.mcp_config.build_mcp_servers`), never on the command line, never echoed into a
tool result, a log, or an error message.

No cost metering: Syften is a flat subscription with no per-call finite unit, so — unlike the
RocketReach worker — this server writes NO cost ledger record. ``GTM_CONTENT_ROOT`` /
``GTM_PROFILE`` are used to place the raw-pull file; ``GTM_PROFILES_ROOT`` is accepted for
spawn parity.

Robustness contract: every tool returns a string. On any failure (no key, HTTP error,
malformed body) it returns a ``[syften-error] …`` string rather than raising — so a worker
outage degrades to "the skill falls back to a manual CSV drop", it never breaks the run or
the SDK ↔ MCP connection.

Run it with::

    python -m agent.mcp.syften --transport stdio
"""
