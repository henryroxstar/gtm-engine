"""Source-URL validation for :mod:`gtm_core.web_sweep` (PSK-026).

Split out of ``web_sweep.py`` to keep that module under the §R10 500-line ratchet — this is
a coherent, single-purpose block (the URL filter) with no dependency on the rest of the sweep.

Only :func:`is_valid_source_url` is public; it is re-exported from ``gtm_core.web_sweep`` so
existing callers and tests are unaffected.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Search-engine hostnames whose *results* pages must never become a source citation. Matched on
# any hostname label (so a TLD other than com, like google.dev, or a subdomain like search.yahoo.com both
# match) AND a search-results-shaped path/query — never on the bare hostname, so a non-search
# page on the same registrable domain (cloud.google.com/blog/..., blog.google/...) is accepted.
_ENGINE_LABELS = frozenset({"google", "bing", "duckduckgo", "yahoo", "baidu", "yandex"})
_SEARCH_QUERY_PARAMS = ("q=", "p=", "wd=", "text=")


def _looks_like_search_results_page(path: str, query: str) -> bool:
    if path.startswith("/search"):
        return True
    return any(part.startswith(_SEARCH_QUERY_PARAMS) for part in query.split("&"))


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def is_valid_source_url(url: str) -> bool:
    """Verify that the URL is https, has a real non-IP hostname, and is not a search-engine
    results page (a non-search page on the same host, e.g. a blog, is accepted)."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        hostname = parsed.hostname
    except ValueError:
        # Malformed authority (e.g. an unterminated IPv6 literal) — refuse, don't crash.
        return False
    if parsed.scheme.lower() != "https":
        return False
    if not hostname:
        return False
    hostname = hostname.lower()
    if "." not in hostname or hostname == "localhost":
        return False
    if _is_ip_literal(hostname):
        return False
    labels = hostname.split(".")
    if any(label in _ENGINE_LABELS for label in labels) and _looks_like_search_results_page(
        parsed.path or "", parsed.query or ""
    ):
        return False
    return True
