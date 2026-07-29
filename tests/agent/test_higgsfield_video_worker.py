"""Unit tests for the Higgsfield video worker MCP (``agent.mcp.higgsfield_video``).

Network-free: ``httpx.AsyncClient`` and the ``higgsfield_client`` SDK upload are
monkeypatched. These pin the verified REST contract (path-style model submit,
``Key`` auth scheme, ``/requests/{id}/status`` poll, nested ``video.url``),
the ``upload_image`` env-mapping, the status mapping, and the estimate cost row —
so the fabricated ``/api/v1/video-generations`` shape can never silently return.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.higgsfield_video import server  # noqa: E402

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, captured: dict, payload: dict, status: int) -> None:
        self._captured = captured
        self._payload = payload
        self._status = status

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._captured.update(method="POST", url=url, json=json, headers=headers)
        return _FakeResp(self._payload, self._status)

    async def get(self, url, headers=None):
        self._captured.update(method="GET", url=url, headers=headers)
        return _FakeResp(self._payload, self._status)


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _set_keys(monkeypatch) -> None:
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "kk")
    monkeypatch.setenv("HIGGSFIELD_API_SECRET", "ss")


# ── auth scheme ───────────────────────────────────────────────────────────────


def test_auth_header_uses_key_scheme(monkeypatch):
    _set_keys(monkeypatch)
    assert server._auth_header() == "Key kk:ss"


# ── generate_video: submit contract + metering ────────────────────────────────


def test_generate_video_happy_path(tmp_path, monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc-123"})

    result = asyncio.run(server.generate_video("https://img/x.png", "slow dolly in", duration=5))
    assert result == "request_id:abc-123"

    # Path-style model submit + Key auth.
    assert captured["url"].endswith("/higgsfield-ai/dop/standard")
    assert captured["headers"]["Authorization"].startswith("Key ")

    # Body: image_url + prompt + duration, and NO model key in the body.
    body = captured["json"]
    assert body["image_url"] == "https://img/x.png"
    assert body["prompt"] == "slow dolly in"
    assert body["duration"] == 5
    assert "model" not in body

    # Estimate cost row (non-zero).
    costs = (tmp_path / "content" / "example2" / "costs.jsonl").read_text().splitlines()
    row = json.loads(costs[-1])
    assert row["tool"] == "higgsfield-video-worker"
    assert row["cost_usd"] == 0.35
    assert row["note"] == "credit-estimated-dop-standard"


def test_generate_video_no_keys(monkeypatch):
    monkeypatch.delenv("HIGGSFIELD_API_KEY", raising=False)
    monkeypatch.delenv("HIGGSFIELD_API_SECRET", raising=False)
    out = asyncio.run(server.generate_video("https://img/x.png", "p"))
    assert out.startswith("[higgsfield-error]")


def test_generate_video_requires_image_url(monkeypatch):
    _set_keys(monkeypatch)
    out = asyncio.run(server.generate_video("  ", "p"))
    assert out.startswith("[higgsfield-error]")
    assert "image_url" in out


def test_generate_video_http_error(monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    _patch_client(monkeypatch, {}, {}, status=402)
    out = asyncio.run(server.generate_video("https://img/x.png", "p"))
    assert out.startswith("[higgsfield-error]")
    assert "402" in out


# ── check_video_status: poll contract + status mapping ────────────────────────


def test_check_status_completed_reads_nested_video_url(monkeypatch):
    _set_keys(monkeypatch)
    captured: dict = {}
    _patch_client(
        monkeypatch,
        captured,
        {"status": "completed", "video": {"url": "https://v/out.mp4"}},
    )
    out = asyncio.run(server.check_video_status("request_id:abc-123"))
    assert out == "status:completed video_url:https://v/out.mp4"
    assert captured["url"].endswith("/requests/abc-123/status")


def test_check_status_in_progress(monkeypatch):
    _set_keys(monkeypatch)
    _patch_client(monkeypatch, {}, {"status": "in_progress"})
    out = asyncio.run(server.check_video_status("abc"))
    assert out == "status:in_progress"


def test_check_status_nsfw_is_failure(monkeypatch):
    _set_keys(monkeypatch)
    _patch_client(monkeypatch, {}, {"status": "nsfw"})
    out = asyncio.run(server.check_video_status("abc"))
    assert out.startswith("status:failed")
    assert "nsfw" in out


# ── upload_image: env mapping + SDK call ──────────────────────────────────────


def test_upload_image_maps_creds_and_returns_url(tmp_path, monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_SECRET", raising=False)
    img = tmp_path / "cover.png"
    img.write_bytes(b"img")

    import higgsfield_client

    seen: dict = {}

    async def _fake_upload(path):
        seen["path"] = path
        return "https://hf-storage/cover.png"

    monkeypatch.setattr(higgsfield_client, "upload_file_async", _fake_upload)

    out = asyncio.run(server.upload_image(str(img)))
    assert out == "https://hf-storage/cover.png"
    assert seen["path"] == str(img)
    # SDK env vars were mapped across from our credential names.
    assert server.os.environ["HF_API_KEY"] == "kk"
    assert server.os.environ["HF_API_SECRET"] == "ss"


def test_upload_image_missing_file(monkeypatch, tmp_path):
    _set_keys(monkeypatch)
    out = asyncio.run(server.upload_image(str(tmp_path / "nope.png")))
    assert out.startswith("[higgsfield-error]")
    assert "not found" in out


# ── mcp_config wiring gate ────────────────────────────────────────────────────


def test_video_server_wired_only_with_both_creds():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    both = dataclasses.replace(base, higgsfield_api_key="k", higgsfield_api_secret="s")
    servers = build_mcp_servers(both, "example2")
    assert "higgsfield_video" in servers
    assert servers["higgsfield_video"]["env"]["GTM_PROFILE"] == "example2"

    half = dataclasses.replace(base, higgsfield_api_key="k", higgsfield_api_secret=None)
    assert "higgsfield_video" not in build_mcp_servers(half, "example2")
