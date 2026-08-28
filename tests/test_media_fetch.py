"""§R6 boundary tests for gtm_core.media_fetch.

Sibling of tests/test_dataset_fetch.py — same shape, opposite corner of the pipeline.
The value of this module is entirely in what it REFUSES, on both sides: which hosts it
will read from, and which paths it will write to. If someone widens ALLOWED_HOSTS to a
wildcard, drops the https check, lets redirects escape, or lets a destination climb out
of the content root, one of these fails.

These live here rather than in tests/media/ on purpose: that directory has an ffmpeg
collection prerequisite (tests/media/conftest.py) and none of this needs ffmpeg. The real
sibling is the other §R6 fetch module.

No network is touched — the allowlist and path decisions are pure and tested directly.
"""

from __future__ import annotations

import pytest

import gtm_core.media_fetch as mf
from gtm_core.media_fetch import (
    ALLOWED_HOSTS,
    MAX_BYTES,
    EgressRefused,
    _AllowlistRedirectHandler,
    _check_url,
    resolve_dest,
)


class TestAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://d8j0ntlcm91z4.cloudfront.net/user_X/hf_2026_abc.mp4",
            "https://d8j0ntlcm91z4.cloudfront.net/user_X/hf_2026_abc.mp3",
            "https://d2ol7oe51mr4n9.cloudfront.net/user_X/element_resize.jpg",
            "https://files2.heygen.ai/aws_pacific/avatar_tmp/user_X/render.mp4",
            # resource2.heygen.ai — pinned 2026-08-20 from live list_avatar_looks/
            # list_brand_kits responses (preview_image_url/preview_video_url/logo_url).
            # A DIFFERENT HeyGen surface from files2 above (previews, not finished
            # renders) — both are allowed, but each was pinned from its own live
            # response, never inferred from the other.
            "https://resource2.heygen.ai/best_frame_selection/candidates/abc.jpg",
            "https://resource2.heygen.ai/avatar/v3/xyz/half/2.2/preview_video_target.mp4",
        ],
    )
    def test_allowed_provider_hosts_pass(self, url):
        assert _check_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/steal.mp4",
            "https://d8j0ntlcm91z4.cloudfront.net.evil.example/x.mp4",  # suffix-confusion
            "https://notd8j0ntlcm91z4.cloudfront.net/x.mp4",
            "https://raw.githubusercontent.com/o/r/main/x.mp4",
            # Any host under the SAME vendor domain that has NOT itself been named by a
            # live response stays refused. Pinning is per-host, not per-vendor: files2
            # and resource2 are each allowed only because each was independently pinned
            # from its own live response — this negative control is what would catch a
            # future PR that widens the entry to a bare `*.heygen.ai` instead of adding
            # the next specific host the same way these two were added.
            "https://upload.heygen.ai/x.mp4",
            "https://files2.heygen.ai.evil.example/x.mp4",
            "https://resource2.heygen.ai.evil.example/x.mp4",
        ],
    )
    def test_off_allowlist_hosts_refused(self, url):
        with pytest.raises(EgressRefused):
            _check_url(url)

    def test_wildcard_cloudfront_is_not_allowed(self):
        """The two distributions are pinned exactly. A wildcard would admit every
        CloudFront tenant on the internet, which is not an allowlist."""
        with pytest.raises(EgressRefused):
            _check_url("https://d111111abcdef8.cloudfront.net/x.mp4")

    @pytest.mark.parametrize(
        "url",
        [
            "http://d8j0ntlcm91z4.cloudfront.net/x.mp4",  # plain http, allowlisted host
            "file:///etc/passwd",
            "ftp://d8j0ntlcm91z4.cloudfront.net/x.mp4",
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
                newurl="https://evil.example/x.mp4",
            )


class TestDestinationConfinement:
    """The write side of the boundary: a fetch can neither read an arbitrary host nor
    write to an arbitrary path."""

    @pytest.fixture(autouse=True)
    def _root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mf, "resolve_content_root", lambda: tmp_path)
        self.root = tmp_path

    @pytest.mark.parametrize(
        "dest",
        [
            "../../../etc/passwd",
            "video/../../../../etc/passwd",
            "a/../../outside.mp4",
        ],
    )
    def test_traversal_out_of_content_root_refused(self, dest):
        with pytest.raises(EgressRefused):
            resolve_dest("acme", dest)

    def test_absolute_dest_refused(self):
        with pytest.raises(EgressRefused):
            resolve_dest("acme", "/etc/passwd")

    @pytest.mark.parametrize("profile", ["../other", "a/b", "", ".", ".."])
    def test_unsafe_profile_segment_refused(self, profile):
        with pytest.raises(EgressRefused):
            resolve_dest(profile, "video/x.mp4")

    def test_cross_profile_write_refused(self):
        """The tenant boundary holds here too — acme cannot write into other."""
        with pytest.raises(EgressRefused):
            resolve_dest("acme", "../other/video/x.mp4")

    def test_nested_relative_dest_resolves_inside_root(self):
        got = resolve_dest("acme", "video/2026-08-17-slug/raw/shot1.mp4")
        assert got == self.root / "acme" / "video" / "2026-08-17-slug" / "raw" / "shot1.mp4"
        got.relative_to(self.root / "acme")  # raises if it escaped


class TestSizeCap:
    def test_oversize_response_refused_and_nothing_written(self, tmp_path, monkeypatch):
        """A runaway response must fail closed, not land a truncated file on disk."""
        monkeypatch.setattr(mf, "resolve_content_root", lambda: tmp_path)

        class _Resp:
            def read(self, n=-1):
                return b"x" * (MAX_BYTES + 1)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            mf.urllib.request,
            "build_opener",
            lambda *a: type("O", (), {"open": staticmethod(lambda req, timeout=None: _Resp())})(),
        )

        with pytest.raises(EgressRefused, match="byte cap"):
            mf.fetch(
                "https://d8j0ntlcm91z4.cloudfront.net/user_X/huge.mp4",
                "acme",
                "video/huge.mp4",
            )
        assert not (tmp_path / "acme" / "video" / "huge.mp4").exists()


def test_fetch_writes_bytes_under_the_content_root(tmp_path, monkeypatch):
    """Positive control. Without this, a uniformly-refusing module would pass every
    test above while being completely broken."""
    monkeypatch.setattr(mf, "resolve_content_root", lambda: tmp_path)

    class _Resp:
        def read(self, n=-1):
            return b"\x00\x00\x00 ftypisom fake-mp4-bytes"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        mf.urllib.request,
        "build_opener",
        lambda *a: type("O", (), {"open": staticmethod(lambda req, timeout=None: _Resp())})(),
    )

    dest = mf.fetch(
        "https://d8j0ntlcm91z4.cloudfront.net/user_X/hf_abc.mp4",
        "acme",
        "video/2026-08-17-slug/raw/shot1.mp4",
    )
    assert dest.exists()
    assert dest.read_bytes().endswith(b"fake-mp4-bytes")
    assert dest.parent.is_dir(), "parent directories are created on demand"


def test_no_auth_headers_are_sent(tmp_path, monkeypatch):
    """These are public CDN URLs. If a fetch ever needs a secret, it does not belong here."""
    monkeypatch.setattr(mf, "resolve_content_root", lambda: tmp_path)
    seen = {}

    class _Resp:
        def read(self, n=-1):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        seen["headers"] = dict(req.header_items())
        seen["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(
        mf.urllib.request,
        "build_opener",
        lambda *a: type("O", (), {"open": staticmethod(_open)})(),
    )

    mf.fetch("https://d8j0ntlcm91z4.cloudfront.net/user_X/a.mp4", "acme", "video/a.mp4")
    assert seen["method"] == "GET"
    lowered = {k.lower() for k in seen["headers"]}
    assert not lowered & {"authorization", "cookie", "x-api-key"}
