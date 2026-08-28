"""Unit tests for the Haiku vision worker MCP (``agent.mcp.vision``).

These exercise the SDK-free, network-free halves: the image-file guards
(``_load_image``), the no-key / bad-path degradation of ``_extract`` (returns a
``[vision-error] …`` string, never raises), and the ``mcp_config`` wiring gate.
No real Anthropic call is made.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

from agent.mcp.vision import server  # noqa: E402

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


# ── _load_image guards ────────────────────────────────────────────────────────


def test_load_image_happy_path(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    media_type, data = server._load_image(str(img))
    assert media_type == "image/png"
    assert base64.standard_b64decode(data) == img.read_bytes()


def test_load_image_jpeg_alias(tmp_path):
    img = tmp_path / "shot.jpeg"
    img.write_bytes(b"\xff\xd8\xff fake jpeg")
    media_type, _ = server._load_image(str(img))
    assert media_type == "image/jpeg"


def test_load_image_rejects_unsupported_suffix(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    with pytest.raises(ValueError, match="unsupported image type"):
        server._load_image(str(bad))


def test_load_image_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError, match="no readable image file"):
        server._load_image(str(tmp_path / "nope.png"))


def test_load_image_rejects_empty_path():
    with pytest.raises(ValueError, match="no image_path"):
        server._load_image("   ")


def test_load_image_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_MAX_IMAGE_BYTES", 16)
    img = tmp_path / "big.png"
    img.write_bytes(b"x" * 64)
    with pytest.raises(ValueError, match="downscale it first"):
        server._load_image(str(img))


# ── _extract degradation (no network) ─────────────────────────────────────────


def test_extract_no_key_returns_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = asyncio.run(server._extract("/tmp/whatever.png", ""))
    assert out.startswith("[vision-error]")
    assert "ANTHROPIC_API_KEY" in out


def test_extract_bad_path_returns_error_before_network(monkeypatch, tmp_path):
    # Key present, but the image guard fails — must error out without an HTTP call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    out = asyncio.run(server._extract(str(tmp_path / "missing.png"), ""))
    assert out.startswith("[vision-error]")
    assert "no readable image file" in out


# ── mcp_config wiring gate ────────────────────────────────────────────────────


def test_vision_server_wired_when_key_present():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, anthropic_api_key="sk-test")
    servers = build_mcp_servers(cfg, "example")
    assert "vision" in servers
    assert servers["vision"]["env"]["ANTHROPIC_API_KEY"] == "sk-test"
    assert servers["vision"]["env"]["GTM_PROFILE"] == "example"
    assert servers["vision"]["args"] == ["-m", "agent.mcp.vision", "--transport", "stdio"]


def test_vision_server_absent_without_key():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, anthropic_api_key=None)
    assert "vision" not in build_mcp_servers(cfg, "example")
