"""gtm_core.reap_upload — the PUT-side mirror of dataset_fetch.py's GET-side §R6 exception.

Every confinement layer is exercised with a positive AND a negative case (host, shape,
signature freshness, source-file location/size, redirect refusal). The network call itself is
mocked (urllib.request.build_opener) — this module's job is confinement, not proving PUT works.
"""

from __future__ import annotations

import json
import time

import pytest

from gtm_core import reap_upload as ru

ALLOWED = frozenset({"upload.example.test"})


def _good_result(url="https://upload.example.test/bucket/key?X-Amz-Signature=abc"):
    return json.dumps({"uploadUrl": url, "id": "upload-123", "fileName": "clip.mp4"})


def _signed_url(*, host="upload.example.test", age_s=10, expires_s=900):
    signed_at = time.gmtime(time.time() - age_s)
    date_str = time.strftime("%Y%m%dT%H%M%SZ", signed_at)
    return (
        f"https://{host}/bucket/key?X-Amz-Date={date_str}&X-Amz-Expires={expires_s}"
        "&X-Amz-Signature=abc"
    )


# --- host allowlist -------------------------------------------------------------------------


def test_allowed_hosts_holds_exactly_the_one_observed_host():
    """The rule was never "stay empty" — it was "never guess ahead of a live observation".

    The allowlist shipped empty from 2026-08-15 until 2026-08-20, when Reap's own
    `request_upload_url` tool was called and the host read out of the `uploadUrl` it minted. This
    asserts the *shape* that matters: exactly one exact bucket host, and no wildcard. A
    `*.amazonaws.com` or `*.s3-accelerate.amazonaws.com` entry would admit every S3 tenant on the
    internet, which is not an allowlist.
    """
    assert ru.ALLOWED_UPLOAD_HOSTS == frozenset(
        {"reap-user-upload-bkt-prod.s3-accelerate.amazonaws.com"}
    )
    assert not any("*" in h for h in ru.ALLOWED_UPLOAD_HOSTS), "no wildcards in an allowlist"


def test_a_sibling_bucket_on_the_same_provider_is_still_refused():
    """Pinning one bucket must not admit the provider's whole namespace."""
    with pytest.raises(ru.EgressRefused, match="not in ALLOWED_UPLOAD_HOSTS"):
        ru._check_url("https://some-other-bkt.s3-accelerate.amazonaws.com/x/y.mp4")


def test_check_url_refuses_an_unrelated_host_against_the_real_allowlist():
    """No monkeypatch: exercises the SHIPPED allowlist, not a test double."""
    with pytest.raises(ru.EgressRefused, match="not in ALLOWED_UPLOAD_HOSTS"):
        ru._check_url("https://upload.example.test/bucket/key")


def test_check_url_refuses_non_https(monkeypatch):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    with pytest.raises(ru.EgressRefused, match="non-https"):
        ru._check_url("http://upload.example.test/bucket/key")


def test_check_url_refuses_a_host_outside_the_allowlist(monkeypatch):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    with pytest.raises(ru.EgressRefused, match="not in ALLOWED_UPLOAD_HOSTS"):
        ru._check_url("https://evil.test/bucket/key")


def test_check_url_accepts_an_allowlisted_https_host(monkeypatch):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    assert ru._check_url("https://upload.example.test/bucket/key") == (
        "https://upload.example.test/bucket/key"
    )


def test_check_url_refuses_suffix_confusion(monkeypatch):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    with pytest.raises(ru.EgressRefused):
        ru._check_url("https://upload.example.test.evil.test/bucket/key")


def test_check_url_refuses_userinfo_confusion(monkeypatch):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    with pytest.raises(ru.EgressRefused):
        ru._check_url("https://upload.example.test@evil.test/bucket/key")


# --- tool-result shape ------------------------------------------------------------------------


def test_parse_tool_result_requires_valid_json():
    with pytest.raises(ru.EgressRefused, match="not valid JSON"):
        ru.parse_tool_result("not json")


def test_parse_tool_result_refuses_a_bare_url_string():
    with pytest.raises(ru.EgressRefused, match="JSON object"):
        ru.parse_tool_result('"https://upload.example.test/bucket/key"')


def test_parse_tool_result_requires_upload_url():
    with pytest.raises(ru.EgressRefused, match="uploadUrl"):
        ru.parse_tool_result(json.dumps({"id": "x", "fileName": "y.mp4"}))


def test_parse_tool_result_requires_id():
    with pytest.raises(ru.EgressRefused, match="'id'"):
        ru.parse_tool_result(json.dumps({"uploadUrl": "https://x/y", "fileName": "y.mp4"}))


def test_parse_tool_result_accepts_the_documented_shape():
    data = ru.parse_tool_result(_good_result())
    assert data["id"] == "upload-123"


# --- signature freshness ----------------------------------------------------------------------


def test_signature_freshness_accepts_a_fresh_signed_url():
    ru._check_signature_freshness(_signed_url(age_s=10))  # must not raise


def test_signature_freshness_refuses_a_stale_signature():
    with pytest.raises(ru.EgressRefused, match="freshness window"):
        ru._check_signature_freshness(_signed_url(age_s=ru.MAX_SIGNATURE_AGE_SECONDS + 60))


def test_signature_freshness_refuses_an_elapsed_expiry_even_if_recently_signed():
    with pytest.raises(ru.EgressRefused, match="Expires window"):
        ru._check_signature_freshness(_signed_url(age_s=10, expires_s=1), now=time.time() + 5)


def test_signature_freshness_is_a_noop_when_no_amz_params_present():
    ru._check_signature_freshness("https://upload.example.test/bucket/key")  # must not raise


def test_signature_freshness_refuses_an_unparseable_date():
    bad = "https://x/y?X-Amz-Date=not-a-date&X-Amz-Expires=900"
    with pytest.raises(ru.EgressRefused, match="unparseable"):
        ru._check_signature_freshness(bad)


# --- source-file confinement -------------------------------------------------------------------


def test_safe_source_accepts_a_file_under_the_content_root(tmp_path):
    root = tmp_path / "content"
    f = root / "acme" / "video" / "demo" / "raw.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"bytes")
    resolved = ru._safe_source(f, content_root=root)
    assert resolved == f.resolve()


def test_safe_source_refuses_a_file_outside_the_content_root(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"bytes")
    with pytest.raises(ru.EgressRefused, match="outside the resolved content root"):
        ru._safe_source(outside, content_root=root)


def test_safe_source_refuses_a_missing_file(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    with pytest.raises(ru.EgressRefused, match="does not exist"):
        ru._safe_source(root / "nope.mp4", content_root=root)


def test_safe_source_refuses_an_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ru, "MAX_BYTES", 4)
    root = tmp_path / "content"
    f = root / "acme" / "big.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"way too big")
    with pytest.raises(ru.EgressRefused, match="byte cap"):
        ru._safe_source(f, content_root=root)


# --- redirect refusal -------------------------------------------------------------------------


def test_no_redirect_handler_refuses_rather_than_follows():
    handler = ru._NoRedirect()
    with pytest.raises(ru.EgressRefused, match="attempted a redirect"):
        handler.redirect_request(None, None, 302, "moved", None, "https://evil.test/steal")


# --- upload() end-to-end (network mocked) ------------------------------------------------------


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return _FakeResponse()


def _seed_source(tmp_path, *, profile="acme", body=b"clip-bytes"):
    root = tmp_path / "content"
    f = root / profile / "video" / "demo" / "raw.mp4"
    f.parent.mkdir(parents=True)
    f.write_bytes(body)
    return root, f


def test_upload_puts_with_no_auth_headers_and_falls_back_to_octet_stream(tmp_path, monkeypatch):
    """When the URL's own signature carries no content-type parameter, this module has no
    business guessing one from the file's extension — application/octet-stream is the neutral
    fallback, not a claim about the file."""
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    root, f = _seed_source(tmp_path)
    fake = _FakeOpener()
    monkeypatch.setattr(ru.urllib.request, "build_opener", lambda *a: fake)

    result = ru.upload(f, _good_result(_signed_url()), profile="acme", content_root=root)

    assert result["upload_id"] == "upload-123"
    assert result["bytes"] == len(b"clip-bytes")
    assert result["status"] == 200
    sent = fake.requests[0]
    assert sent.get_method() == "PUT"
    assert sent.get_header("Content-type") == "application/octet-stream"
    assert sent.get_header("Authorization") is None
    assert sent.get_header("Cookie") is None


def test_upload_honours_the_content_type_the_signature_was_computed_against(tmp_path, monkeypatch):
    """A pre-signed S3-style PUT URL can bind its signature to a specific Content-Type; sending
    a different one is a signature mismatch (403), confirmed live 2026-08-21 on a real Reap
    upload URL carrying content-type=video%2Fmp4. Regression test for that exact defect."""
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    root, f = _seed_source(tmp_path)
    fake = _FakeOpener()
    monkeypatch.setattr(ru.urllib.request, "build_opener", lambda *a: fake)

    url = _signed_url() + "&content-type=video%2Fmp4"
    result = ru.upload(f, _good_result(url), profile="acme", content_root=root)

    assert result["status"] == 200
    sent = fake.requests[0]
    assert sent.get_header("Content-type") == "video/mp4"


def test_signed_content_type_helper_decodes_the_url_encoded_value():
    assert ru._signed_content_type("https://h/x?content-type=video%2Fmp4") == "video/mp4"
    assert ru._signed_content_type("https://h/x?content-type=image%2Fpng") == "image/png"


def test_signed_content_type_helper_falls_back_when_absent():
    assert ru._signed_content_type("https://h/x?other=1") == "application/octet-stream"


def test_upload_refuses_before_touching_the_network_when_host_not_allowlisted(
    tmp_path, monkeypatch
):
    root, f = _seed_source(tmp_path)
    fake = _FakeOpener()
    monkeypatch.setattr(ru.urllib.request, "build_opener", lambda *a: fake)

    with pytest.raises(ru.EgressRefused):
        ru.upload(f, _good_result(_signed_url()), profile="acme", content_root=root)
    assert fake.requests == []  # never reached the network


def test_upload_refuses_before_touching_the_network_when_source_is_outside_root(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    root = tmp_path / "content"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    fake = _FakeOpener()
    monkeypatch.setattr(ru.urllib.request, "build_opener", lambda *a: fake)

    with pytest.raises(ru.EgressRefused, match="outside the resolved content root"):
        ru.upload(outside, _good_result(_signed_url()), profile="acme", content_root=root)
    assert fake.requests == []


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_reads_the_raw_tool_result_from_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ru, "ALLOWED_UPLOAD_HOSTS", ALLOWED)
    root, f = _seed_source(tmp_path)
    fake = _FakeOpener()
    monkeypatch.setattr(ru.urllib.request, "build_opener", lambda *a: fake)
    monkeypatch.setattr(ru, "resolve_content_root", lambda: root)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(_good_result(_signed_url())))

    rc = ru.main(["--profile", "acme", "--file", str(f)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["upload_id"] == "upload-123"


def test_cli_exits_nonzero_and_prints_the_reason_on_refusal(tmp_path, monkeypatch, capsys):
    root, f = _seed_source(tmp_path)
    monkeypatch.setattr(ru, "resolve_content_root", lambda: root)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(_good_result(_signed_url())))

    rc = ru.main(["--profile", "acme", "--file", str(f)])
    assert rc == 1
    assert "not in ALLOWED_UPLOAD_HOSTS" in capsys.readouterr().err


def test_cli_errors_on_empty_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
    with pytest.raises(SystemExit):
        ru.main(["--profile", "acme", "--file", "content/acme/x.mp4"])


# --- grep-confinement (defense in depth alongside the semgrep rule) ---------------------------


def test_only_the_documented_gtm_core_modules_import_urllib_request():
    """Any NEW gtm_core module importing urllib.request must be a deliberate, reviewed egress
    point registered in .semgrep/gtm-invariants.yml's exclude list — not a silent reintroduction
    of a general HTTP client. Pins the modules that ACTUALLY import it today (dataset_fetch.py,
    reap_upload.py, media_fetch.py; ingest.py and calendly_poll.py are pre-registered in the
    semgrep exclude list defensively but take an injected client rather than importing
    urllib.request themselves) so a stray import trips this test even before semgrep runs."""
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    expected = {"dataset_fetch.py", "reap_upload.py", "media_fetch.py"}
    found = set()
    for path in sorted((repo / "gtm_core").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "urllib.request" for a in node.names):
                found.add(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "urllib.request":
                found.add(path.name)
    assert found == expected
