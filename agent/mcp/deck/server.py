"""Deck-export MCP server — thin bridge from the brain to the deck-renderer sidecar.

Exposes one tool:
  export_deck(slides_md_path, format) → output file path or [deck-error]

The sidecar (a Node container) renders a brain-authored slides.md against the
baked default deck-theme using Slidev/Playwright/Chromium. Composition (inputs →
slides.md) is the brain's job; this server handles path validation and the
MCP ↔ HTTP bridge only.

Design note: FastMCP is imported lazily in _build_mcp() so that server.py can be
imported (and export_deck tested) without the mcp package being importable in the
test environment. Only __main__.py triggers the MCP import at server start.

Robustness contract: always returns a string. On any failure returns "[deck-error] …"
so the brain can surface the blocker. Never raises.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


# Lazily read env so tests can monkeypatch without module reload.
def _content_root() -> Path:
    return Path(os.getenv("GTM_CONTENT_ROOT") or "/app/content").resolve()


def _renderer_url() -> str:
    return (os.getenv("DECK_RENDERER_URL") or "http://deck-renderer:3000").rstrip("/")


_ALLOWED_FORMATS = frozenset({"pdf", "png", "pptx"})
_HTTP_TIMEOUT_S = 300.0  # Slidev/Playwright can be slow (Chromium cold start + per-slide render)


def _validate_content_path(raw: str, label: str) -> tuple[Path, str]:
    """Resolve and validate that `raw` is inside the content root.

    Returns ``(resolved_path, "")`` on success, ``(Path(), error_string)`` on failure.
    """
    try:
        target = Path(raw).resolve()
    except (TypeError, ValueError) as exc:
        return Path(), f"[deck-error] bad {label}: {exc}"
    try:
        target.relative_to(_content_root())
    except ValueError:
        return Path(), f"[deck-error] {label} must be inside the content root ({_content_root()})"
    return target, ""


async def export_deck(slides_md_path: str, format: str = "pdf") -> str:
    """Export a brain-authored Slidev deck to PDF, per-slide PNGs, or a compressed PPTX.

    The brain composes slides.md (studio stage) and calls this tool to render it.
    The sidecar pins the deck to the baked deck-theme and runs Slidev export —
    the brain never touches node/npm.

    Args:
        slides_md_path: Absolute path to a file named slides.md inside the content
            root. Any relative asset refs in it (./images/…) must live beside it.
        format: "pdf" | "png" (per-slide folder) | "pptx" (flattened, fast-loading).

    Returns the output file/folder path on success, or a "[deck-error] …" string.
    The brain MUST NOT retry on [deck-error] — surface it to the operator.
    """
    # ── 1. Validate format ─────────────────────────────────────────────────────
    fmt = (format or "").lower().strip()
    if fmt not in _ALLOWED_FORMATS:
        return f"[deck-error] unsupported format {format!r} — use pdf, png, or pptx"

    # ── 2. Validate slides_md_path ─────────────────────────────────────────────
    slides_path, err = _validate_content_path(slides_md_path, "slides_md_path")
    if err:
        return err
    if slides_path.name != "slides.md":
        return "[deck-error] slides_md_path must point to a file named slides.md"
    if not slides_path.is_file():
        return f"[deck-error] slides.md not found: {slides_path}"

    # ── 3. Call sidecar ────────────────────────────────────────────────────────
    payload = {"slides_md_path": str(slides_path), "format": fmt}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(f"{_renderer_url()}/export", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[deck-error] sidecar HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    except httpx.HTTPError as exc:
        return f"[deck-error] sidecar unreachable: {type(exc).__name__}"
    except ValueError:
        return "[deck-error] sidecar returned non-JSON"

    output_path = data.get("output_path")
    if not output_path:
        return f"[deck-error] sidecar returned no output_path: {data}"
    return str(output_path)


def _build_mcp():
    """Build and return the FastMCP server with export_deck registered.

    Called only by __main__.py — lazy so that server.py can be imported
    (and export_deck tested) without mcp being on sys.path.
    """
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415 — intentional lazy import

    mcp = FastMCP("deck-renderer")
    mcp.tool()(export_deck)
    return mcp
