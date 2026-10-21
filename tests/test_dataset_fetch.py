"""§R6 boundary tests for gtm_core.dataset_fetch.

The value of this module is entirely in what it REFUSES. These tests are the
regression guard on the allowlist: if someone widens ALLOWED_HOSTS to a wildcard,
drops the https check, or lets redirects escape, one of these fails.

No network is touched — the allowlist decision is pure and tested directly.
"""

from __future__ import annotations

import pytest

from gtm_core.dataset_fetch import (
    ALLOWED_HOSTS,
    EgressRefused,
    _AllowlistRedirectHandler,
    _check_url,
    _safe_name,
)


class TestAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://share.explorium.ai/AbC123",
            "https://app.vibeprospecting.ai/lists?dataset_id=ds-1",
            "https://mcp-datasets-prod.s3.amazonaws.com/exports/x.csv?sig=1",
            "https://api.rocketreach.co/v2/export/1",
        ],
    )
    def test_allowed_provider_hosts_pass(self, url):
        assert _check_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/steal.csv",
            "https://share.explorium.ai.evil.example/x.csv",  # suffix-confusion
            "https://notshare.explorium.ai/x.csv",
            "https://raw.githubusercontent.com/o/r/main/x.csv",
        ],
    )
    def test_off_allowlist_hosts_refused(self, url):
        with pytest.raises(EgressRefused):
            _check_url(url)

    def test_wildcard_s3_is_not_allowed(self):
        """The Explorium bucket is pinned exactly; sibling buckets must not pass."""
        with pytest.raises(EgressRefused):
            _check_url("https://some-other-bucket.s3.amazonaws.com/x.csv")

    @pytest.mark.parametrize(
        "url",
        [
            "http://share.explorium.ai/x.csv",  # plain http, allowlisted host
            "file:///etc/passwd",
            "ftp://share.explorium.ai/x.csv",
        ],
    )
    def test_non_https_refused(self, url):
        """https-only: an allowlisted host over http is still refused, never upgraded."""
        with pytest.raises(EgressRefused):
            _check_url(url)

    def test_allowlist_is_a_closed_constant(self):
        """Guard against a wildcard/empty allowlist sneaking in."""
        assert ALLOWED_HOSTS, "allowlist must never be empty (that would be fail-open)"
        assert all("*" not in host for host in ALLOWED_HOSTS)
        assert all(host == host.lower() for host in ALLOWED_HOSTS)


class TestRedirectGuard:
    """A provider-side open redirect must not become arbitrary egress."""

    def test_redirect_off_allowlist_raises(self):
        handler = _AllowlistRedirectHandler()
        with pytest.raises(EgressRefused):
            handler.redirect_request(
                req=None,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://evil.example/x.csv",
            )


class TestSafeName:
    @pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "a\\b", "", ".", ".."])
    def test_traversal_rejected(self, name):
        with pytest.raises(ValueError):
            _safe_name(name)

    def test_plain_name_ok(self):
        assert _safe_name("us_ent_intent_a") == "us_ent_intent_a"


def test_name_ending_in_csv_does_not_double_suffix(tmp_path, monkeypatch):
    """A caller passing "foo.csv" gets foo.csv on disk, not foo.csv.csv."""
    import gtm_core.dataset_fetch as df

    monkeypatch.setattr(df, "imports_dir", lambda profile: tmp_path)

    class _Resp:
        headers = {"Content-Length": "6"}

        def read(self, n=-1):
            return b"a,b\n1,2" if n == -1 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        df.urllib.request,
        "build_opener",
        lambda *a: type("O", (), {"open": staticmethod(lambda req, timeout=None: _Resp())})(),
    )

    for given, expected in (
        ("evts.csv", "evts.csv"),
        ("evts", "evts.csv"),
        ("EVTS.CSV", "EVTS.csv"),
    ):
        dest = df.fetch("https://share.explorium.ai/abc", "acme", given)
        assert dest.name == expected, f"{given!r} -> {dest.name!r}, want {expected!r}"
        assert not dest.name.endswith(".csv.csv")
