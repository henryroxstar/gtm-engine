"""Tests for agent.mcp.deck.server — the Python-side MCP bridge.

Uses monkeypatch + AsyncMock to avoid any real HTTP or filesystem dependency.
Tests exercise both the workspace (branded) and no-workspace (fallback) code paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("agent.mcp.deck.server", reason="agent.mcp.deck not built yet")

from agent.mcp.deck.server import export_deck  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("DECK_RENDERER_URL", "http://fake-renderer:3000")
    return tmp_path


def _make_deck(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal deck folder: inputs/ + slides.md."""
    deck_dir = tmp_path / "example" / "accounts" / "testco" / "deck-2026"
    deck_dir.mkdir(parents=True)
    inputs = deck_dir / "inputs"
    inputs.mkdir()
    (inputs / "config.yml").write_text("deck:\n  name: testco\n")
    (inputs / "outline.md").write_text("## Slide 1\n")
    slides = deck_dir / "slides.md"
    slides.write_text("---\n---\n# Hello\n")
    return slides, inputs


# ── format validation ─────────────────────────────────────────────────────────


def test_bad_format_returns_error(tmp_path):
    slides, _ = _make_deck(tmp_path)
    result = asyncio.run(export_deck(str(slides), format="docx"))
    assert result.startswith("[deck-error]")
    assert "format" in result


# ── slides_md_path validation ─────────────────────────────────────────────────


def test_slides_path_outside_content_root_rejected(tmp_path):
    result = asyncio.run(export_deck("/etc/passwd", format="pdf"))
    assert result.startswith("[deck-error]")
    assert "content root" in result


def test_slides_path_must_be_named_slides_md(tmp_path):
    slides, _ = _make_deck(tmp_path)
    result = asyncio.run(export_deck(str(slides.with_name("evil.md")), format="pdf"))
    assert result.startswith("[deck-error]")
    assert "slides.md" in result


def test_slides_md_not_found_returns_error(tmp_path):
    result = asyncio.run(
        export_deck(str(tmp_path / "example" / "accounts" / "ghost" / "slides.md"), format="pdf")
    )
    assert result.startswith("[deck-error]")
    assert "not found" in result


# ── happy paths ───────────────────────────────────────────────────────────────


def _mock_client(output_path: str):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {"output_path": output_path}
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.parametrize("fmt", ["pdf", "png", "pptx"])
def test_happy_path(tmp_path, fmt):
    slides, _ = _make_deck(tmp_path)
    expected = str(slides.parent / f"testco-deck.{fmt}")
    with patch("agent.mcp.deck.server.httpx.AsyncClient", return_value=_mock_client(expected)):
        result = asyncio.run(export_deck(str(slides), format=fmt))
    assert result == expected
    assert "[deck-error]" not in result


def test_payload_is_minimal(tmp_path):
    """The sidecar receives exactly slides_md_path + format — no compose-era fields."""
    slides, _ = _make_deck(tmp_path)
    expected = str(slides.parent / "testco-deck.pdf")
    captured_payload = {}

    async def mock_post(url, json):
        captured_payload.update(json)
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"output_path": expected}
        return resp

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(post=mock_post))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("agent.mcp.deck.server.httpx.AsyncClient", return_value=cm):
        result = asyncio.run(export_deck(str(slides), format="pdf"))
    assert result == expected
    assert captured_payload == {"slides_md_path": str(slides.resolve()), "format": "pdf"}
    assert "deck_inputs_path" not in captured_payload


# ── sidecar error handling ────────────────────────────────────────────────────


def test_sidecar_unreachable_returns_error(tmp_path):
    import httpx as _httpx

    slides, _ = _make_deck(tmp_path)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(
        return_value=MagicMock(post=AsyncMock(side_effect=_httpx.ConnectError("refused")))
    )
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("agent.mcp.deck.server.httpx.AsyncClient", return_value=cm):
        result = asyncio.run(export_deck(str(slides), format="pdf"))
    assert result.startswith("[deck-error]")
    assert "unreachable" in result


def test_sidecar_no_output_path_returns_error(tmp_path):
    slides, _ = _make_deck(tmp_path)
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: {}  # missing output_path
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=resp)))
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("agent.mcp.deck.server.httpx.AsyncClient", return_value=cm):
        result = asyncio.run(export_deck(str(slides), format="pdf"))
    assert result.startswith("[deck-error]")
    assert "output_path" in result
