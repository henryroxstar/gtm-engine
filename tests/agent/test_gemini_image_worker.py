"""Unit tests for the Gemini image worker MCP (``agent.mcp.gemini_image``).

Network-free: ``httpx.AsyncClient`` is monkeypatched so no real Gemini call is
made. These pin the verified request/response contract (endpoint, headers, body
shape, inline-image decode) so the fabricated ``/v1beta/interactions`` shape can
never silently come back, plus the ``[gemini-image-error] …`` degradation paths
and the cost-metering row.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.gemini_image import server  # noqa: E402

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
        self._captured.update(url=url, json=json, headers=headers)
        return _FakeResp(self._payload, self._status)


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _image_payload(b64: str, mime: str = "image/png") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": b64}}]}}]
    }


# ── happy path: request contract + decode + metering ──────────────────────────


def test_generate_image_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    raw = b"\x89PNG\r\n\x1a\n pretend image"
    b64 = base64.b64encode(raw).decode()
    captured: dict = {}
    _patch_client(monkeypatch, captured, _image_payload(b64))

    result = asyncio.run(server._generate("a chart", str(out_dir / "x.png"), "2K", "16:9"))

    # Returns the written path; bytes are the decoded inline image.
    assert result.endswith("x.png")
    assert (out_dir / "x.png").read_bytes() == raw

    # Verified endpoint + auth header.
    assert captured["url"].endswith("/v1beta/models/gemini-3-pro-image-preview:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "test-key"

    # Verified body shape.
    body = captured["json"]
    assert body["contents"][0]["parts"][0]["text"] == "a chart"
    cfg = body["generationConfig"]
    assert cfg["responseModalities"] == ["TEXT", "IMAGE"]
    assert cfg["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
    assert "input" not in body and "response_format" not in body

    # Exact-USD cost row landed.
    costs = (tmp_path / "content" / "example2" / "costs.jsonl").read_text().splitlines()
    row = json.loads(costs[-1])
    assert row["tool"] == "gemini-image-worker"
    assert row["cost_usd"] == 0.134
    assert row["resolution"] == "2K"


def test_generate_image_corrects_extension_to_mime(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    b64 = base64.b64encode(b"jpegbytes").decode()
    _patch_client(monkeypatch, {}, _image_payload(b64, mime="image/jpeg"))

    result = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert result.endswith("x.jpg")  # extension realigned to image/jpeg


# ── degradation paths (no network) ────────────────────────────────────────────


def test_generate_image_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = asyncio.run(server._generate("p", str(tmp_path / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "GEMINI_API_KEY" in out


def test_generate_image_bad_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "5K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "resolution" in out


def test_generate_image_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out = asyncio.run(server._generate("p", str(tmp_path / "nope" / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "output directory" in out


def test_generate_image_http_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _patch_client(monkeypatch, {}, {}, status=500)
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "500" in out


def test_generate_image_no_image_in_response(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _patch_client(monkeypatch, {}, {"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]})
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "no image data" in out


# ── mcp_config wiring gate ────────────────────────────────────────────────────


def test_gemini_server_wired_when_key_present():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, gemini_api_key="g-key")
    servers = build_mcp_servers(cfg, "example2")
    assert "gemini_image" in servers
    assert servers["gemini_image"]["env"]["GEMINI_API_KEY"] == "g-key"
    assert servers["gemini_image"]["env"]["GTM_PROFILE"] == "example2"


def test_gemini_server_absent_without_key():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, gemini_api_key=None)
    assert "gemini_image" not in build_mcp_servers(cfg, "example2")
