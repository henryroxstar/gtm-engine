"""Tests for gtm_core.media_host."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import media_host as mh


@pytest.fixture
def tmp_profile(tmp_path: Path) -> tuple[Path, Path, str]:
    """Return (content_root, profiles_root, profile_slug) for a temp tenant."""
    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"
    profile = "testco"
    (content_root / profile / "video" / "slug").mkdir(parents=True)
    return content_root, profiles_root, profile


def _write_asset(content_root: Path, profile: str, rel: str, data: bytes = b"fake video") -> Path:
    path = content_root / profile / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _write_finish(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(data), encoding="utf-8")


def test_upload_with_injected_uploader_returns_urls(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    asset = _write_asset(content_root, profile, "video/slug/finish.mp4")

    def _uploader(path: str, prof: str) -> list[str]:
        return ["https://cdn.example.com/finish.mp4"]

    urls = mh.upload(asset, profile, content_root=content_root, uploader=_uploader)
    assert urls == ["https://cdn.example.com/finish.mp4"]


def test_upload_without_uploader_fails_closed(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    asset = _write_asset(content_root, profile, "video/slug/finish.mp4")
    with pytest.raises(mh.MediaHostError, match="No media-host MCP tool is configured"):
        mh.upload(asset, profile, content_root=content_root)


def test_upload_refuses_absolute_path(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    outside = Path("/etc/passwd")
    with pytest.raises(mh.EgressRefused, match="outside the resolved content root"):
        mh.upload(outside, profile, content_root=content_root, uploader=lambda p, pr: [])


def test_upload_refuses_path_outside_root_via_dotdot(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    outside_file = content_root.parent.parent / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    (content_root / profile / "video" / "slug").mkdir(parents=True, exist_ok=True)
    asset = content_root / profile / "video" / "slug" / ".." / ".." / ".." / ".." / "outside.txt"
    with pytest.raises(mh.EgressRefused, match="outside the resolved content root"):
        mh.upload(asset, profile, content_root=content_root, uploader=lambda p, pr: [])


def test_upload_refuses_missing_file(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    missing = content_root / profile / "video" / "slug" / "nope.mp4"
    with pytest.raises(mh.EgressRefused, match="source file does not exist"):
        mh.upload(missing, profile, content_root=content_root, uploader=lambda p, pr: [])


def test_write_hosted_urls_updates_finish_json(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    finish = content_root / profile / "video" / "slug" / "finish.json"
    _write_finish(finish, {"asset_path": "video/slug/finish.mp4"})
    mh.write_hosted_urls(finish, ["https://cdn.example.com/finish.mp4"])
    data = __import__("json").loads(finish.read_text(encoding="utf-8"))
    assert data["hosted_media_urls"] == ["https://cdn.example.com/finish.mp4"]
    assert data["asset_path"] == "video/slug/finish.mp4"


def test_upload_and_records_updates_finish_json(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    asset = _write_asset(content_root, profile, "video/slug/finish.mp4")
    finish = content_root / profile / "video" / "slug" / "finish.json"
    _write_finish(finish, {"asset_path": "video/slug/finish.mp4"})

    urls = mh.upload_and_record(
        asset,
        finish,
        profile,
        content_root=content_root,
        uploader=lambda p, pr: ["https://cdn.example.com/finish.mp4"],
    )
    assert urls == ["https://cdn.example.com/finish.mp4"]
    data = __import__("json").loads(finish.read_text(encoding="utf-8"))
    assert data["hosted_media_urls"] == urls


def test_cli_records_precomputed_urls(tmp_profile: tuple[Path, Path, str]) -> None:
    content_root, _, profile = tmp_profile
    asset = _write_asset(content_root, profile, "video/slug/finish.mp4")
    finish = content_root / profile / "video" / "slug" / "finish.json"
    _write_finish(finish, {"asset_path": "video/slug/finish.mp4"})

    code = mh.main(
        [
            "--profile",
            profile,
            "--asset",
            str(asset),
            "--finish",
            str(finish),
            "--urls",
            "https://cdn.example.com/finish.mp4",
        ]
    )
    assert code == 0
    data = __import__("json").loads(finish.read_text(encoding="utf-8"))
    assert data["hosted_media_urls"] == ["https://cdn.example.com/finish.mp4"]
