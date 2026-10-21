"""Unit tests for Cloudflare R2 client and artifact serving presigned redirects.

Verifies:
  - R2 configuration detection and S3 client initialization.
  - File upload and presigned URL generation via boto3 mocking.
  - Media host uploader containment (§R6, PRD §5: content root containment, PII account blocking).
  - Backend artifact query resolution: 307 redirect when R2 configured vs FileResponse fallback.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from backend.services.runs.queries import resolve_artifact_response
from gtm_core import r2_client
from gtm_core.media_host import EgressRefused, MediaHostError


def test_is_configured(monkeypatch):
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    assert not r2_client.is_configured()

    monkeypatch.setenv("R2_ACCOUNT_ID", "acc-123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key-456")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sec-789")
    assert r2_client.is_configured()


def test_get_s3_client_missing_creds(monkeypatch):
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(MediaHostError, match="R2 credentials not configured"):
        r2_client.get_s3_client()


def test_upload_file_calls_s3(tmp_path):
    mock_s3 = MagicMock()
    test_file = tmp_path / "report.pdf"
    test_file.write_bytes(b"%PDF-1.4 mock content")

    key = r2_client.upload_file(
        test_file,
        "tenant/report.pdf",
        bucket="test-bucket",
        s3_client=mock_s3,
    )
    assert key == "tenant/report.pdf"
    mock_s3.upload_file.assert_called_once()
    args, kwargs = mock_s3.upload_file.call_args
    assert args[0] == str(test_file)
    assert args[1] == "test-bucket"
    assert args[2] == "tenant/report.pdf"
    assert kwargs.get("ExtraArgs", {}).get("ContentType") == "application/pdf"


def test_upload_file_nonexistent_fails(tmp_path):
    mock_s3 = MagicMock()
    missing_file = tmp_path / "nonexistent.txt"
    with pytest.raises(MediaHostError, match="Cannot upload non-existent file"):
        r2_client.upload_file(missing_file, "key", s3_client=mock_s3)


def test_generate_presigned_url():
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed?token=xyz"

    url = r2_client.generate_presigned_url(
        "tenant/artifact.png",
        bucket="my-bucket",
        expires_in=1800,
        s3_client=mock_s3,
    )
    assert url == "https://r2.example.com/signed?token=xyz"
    mock_s3.generate_presigned_url.assert_called_once_with(
        ClientMethod="get_object",
        Params={"Bucket": "my-bucket", "Key": "tenant/artifact.png"},
        ExpiresIn=1800,
    )


def test_r2_media_uploader_containment_and_pii(tmp_path):
    content_root = tmp_path / "content"
    content_root.mkdir()
    render_dir = content_root / "renders"
    render_dir.mkdir()
    img_file = render_dir / "hero.png"
    img_file.write_bytes(b"PNG fake data")

    mock_s3 = MagicMock()
    with patch.dict(os.environ, {"R2_PUBLIC_BASE_URL": "https://cdn.example.com"}):
        urls = r2_client.r2_media_uploader(
            str(img_file),
            profile="test-profile",
            content_root=content_root,
            s3_client=mock_s3,
        )
    assert len(urls) == 1
    assert urls[0].startswith("https://cdn.example.com/test-profile/")
    assert urls[0].endswith("/hero.png")
    mock_s3.upload_file.assert_called_once()

    # PII rejection: files under accounts/ cannot be uploaded to public bucket
    accounts_dir = content_root / "accounts" / "acme"
    accounts_dir.mkdir(parents=True)
    dossier = accounts_dir / "dossier.pdf"
    dossier.write_bytes(b"confidential dossier")

    with pytest.raises(EgressRefused, match="Refusing to upload customer account data"):
        r2_client.r2_media_uploader(
            str(dossier),
            profile="test-profile",
            content_root=content_root,
            s3_client=mock_s3,
        )


def test_resolve_artifact_response_r2_redirect(tmp_path):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "rel_path": "reports/summary.pdf",
        "name": "summary.pdf",
        "media_type": "application/pdf",
    }
    repo_root = tmp_path

    with (
        patch("gtm_core.r2_client.is_configured", return_value=True),
        patch(
            "gtm_core.r2_client.generate_presigned_url",
            return_value="https://presigned.r2.example.com/test",
        ),
    ):
        resp = resolve_artifact_response(row, "workspace-123", repo_root)
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 307
        assert resp.headers["location"] == "https://presigned.r2.example.com/test"


def test_resolve_artifact_response_fallback_local(tmp_path):
    content_dir = tmp_path / "data" / "workspaces" / "workspace-123" / "content"
    content_dir.mkdir(parents=True)
    report_file = content_dir / "reports" / "summary.pdf"
    report_file.parent.mkdir(parents=True)
    report_file.write_bytes(b"local pdf data")

    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "rel_path": "reports/summary.pdf",
        "name": "summary.pdf",
        "media_type": "application/pdf",
    }

    with patch("gtm_core.r2_client.is_configured", return_value=False):
        resp = resolve_artifact_response(row, "workspace-123", tmp_path)
        assert isinstance(resp, FileResponse)
        assert resp.status_code == 200
        assert resp.media_type == "application/pdf"
        assert "attachment" in resp.headers["content-disposition"]


def test_resolve_artifact_response_gone_410(tmp_path):
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "rel_path": "reports/missing.pdf",
        "name": "missing.pdf",
        "media_type": "application/pdf",
    }

    with patch("gtm_core.r2_client.is_configured", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            resolve_artifact_response(row, "workspace-123", tmp_path)
        assert exc_info.value.status_code == 410
