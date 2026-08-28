"""Contract tests for gtm_core.media_host path confinement.

These tests treat media-host as a boundary: local files must stay inside the
active profile's content root, and the default upload path fails closed when no
MCP host is configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import media_host as mh

REPO = Path(__file__).resolve().parents[2]


def test_media_host_upload_refuses_absolute_path_outside_root():
    with pytest.raises(mh.EgressRefused):
        mh.upload(Path("/etc/passwd"), "example", content_root=REPO / "content")


def test_media_host_upload_refuses_nonexistent_file():
    root = REPO / "content"
    with pytest.raises(mh.EgressRefused, match="source file does not exist"):
        mh.upload(root / "example" / "missing.mp4", "example", content_root=root)


def test_media_host_upload_fails_closed_when_unconfigured(tmp_path: Path):
    asset = tmp_path / "content" / "example" / "video" / "finish.mp4"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"x")
    with pytest.raises(mh.MediaHostError, match="No media-host MCP tool is configured"):
        mh.upload(asset, "example", content_root=tmp_path / "content")


def test_media_host_records_hosted_urls_in_finish_json(tmp_path: Path):
    finish = tmp_path / "finish.json"
    finish.write_text('{"asset_path": "video/finish.mp4"}', encoding="utf-8")
    mh.write_hosted_urls(finish, ["https://cdn.example.com/finish.mp4"])
    data = __import__("json").loads(finish.read_text(encoding="utf-8"))
    assert data["hosted_media_urls"] == ["https://cdn.example.com/finish.mp4"]
