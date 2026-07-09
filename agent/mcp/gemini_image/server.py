"""Gemini image-generation worker MCP server (FastMCP, stdio).

A thin wrapper around the Google Gemini REST API
(``/v1beta/models/{model}:generateContent``): one tool (``generate_image``) that
calls ``gemini-3-pro-image-preview`` with a text prompt, decodes the returned
inline base64 image, and writes it to the requested output path. The brain
reasons over the generated image path — it never sees raw bytes.

Request/response contract verified 2026-06-20 against ai.google.dev image
generation docs: prompt goes in ``contents[].parts[].text``; size/ratio in
``generationConfig.imageConfig.{aspectRatio,imageSize}`` with
``responseModalities:["TEXT","IMAGE"]``; the image returns as
``candidates[0].content.parts[].inlineData.{data,mimeType}``.

Model discipline: the model is PINNED to ``gemini-3-pro-image-preview``
(= Nano Banana Pro, Google DeepMind's highest-fidelity image model as of H1).
A silent env-var override would quietly change quality and billing; the model
string must be edited in code. See docs/RULES.md (model discipline).

Cost metering (NIST AU-12; budget integrity): a flat per-image cost is written
to ``content/<profile>/costs.jsonl`` after each successful generation, computed
from the resolution-band pricing. Token billing does not apply to this API.
The owning profile is passed at spawn via ``GTM_PROFILE``. Metering failure is
swallowed — it must never break a generation.

Robustness contract: on any failure (no key, bad path, HTTP error, empty
response) the tool returns ``[gemini-image-error] …`` rather than raising, so
the brain gets a tool result it can react to and the MCP connection never drops.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# --- Gemini wiring (pinned) ------------------------------------------------- #
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip(
    "/"
)
# PINNED — Nano Banana Pro / Google Gemini 3 Pro Image. Do not template from env.
GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"
_HTTP_TIMEOUT_S = 120.0  # image generation is slow; 2-minute ceiling

# --- Per-image pricing (USD) by resolution band ----------------------------- #
# Overridable via env so a price change doesn't require a code edit.
# Defaults: $0.134/image for 1K/2K, $0.24/image for 4K (Gemini 3 Pro Image list pricing).
_PRICE_1K_USD = float(os.getenv("GEMINI_IMAGE_PRICE_1K_USD") or "0.134")
_PRICE_2K_USD = float(os.getenv("GEMINI_IMAGE_PRICE_2K_USD") or "0.134")
_PRICE_4K_USD = float(os.getenv("GEMINI_IMAGE_PRICE_4K_USD") or "0.240")

_RESOLUTION_PRICES: dict[str, float] = {
    "1K": _PRICE_1K_USD,
    "2K": _PRICE_2K_USD,
    "4K": _PRICE_4K_USD,
}

# Valid values for the ``image_size`` field in the Gemini interactions API.
_VALID_RESOLUTIONS = {"1K", "2K", "4K"}

mcp = FastMCP("gemini-image-worker")


def _meter(resolution: str) -> None:
    """Append a flat-fee cost record for one generation to the owning profile's ledger.

    Best-effort: any failure (no profile, ledger error) is swallowed — metering
    must never break a generation or the MCP connection.
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile:
        return
    cost = _RESOLUTION_PRICES.get(resolution, _PRICE_1K_USD)
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "gemini-image-worker",
                "op": "generate_image",
                "model": GEMINI_IMAGE_MODEL,
                "resolution": resolution,
                "cost_usd": round(cost, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort
        return


async def _generate(
    prompt: str,
    output_path: str,
    resolution: str,
    aspect_ratio: str,
) -> str:
    """Call Gemini interactions API, decode the image, write to output_path.

    Returns the absolute output path on success, or ``[gemini-image-error] …``.
    """
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return "[gemini-image-error] GEMINI_API_KEY is not set — worker cannot generate."

    resolution = resolution.upper()
    if resolution not in _VALID_RESOLUTIONS:
        return (
            f"[gemini-image-error] unsupported resolution {resolution!r} "
            f"(expected one of {', '.join(sorted(_VALID_RESOLUTIONS))})"
        )

    dest = Path(output_path).expanduser()
    if not dest.parent.exists():
        return f"[gemini-image-error] output directory does not exist: {dest.parent}"

    payload: dict = {
        "contents": [{"parts": [{"text": prompt.strip()}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": resolution,
            },
        },
    }
    headers = {
        "x-goog-api-key": key,
        "content-type": "application/json",
    }
    url = f"{GEMINI_BASE_URL}/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[gemini-image-error] Gemini HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"[gemini-image-error] Gemini request failed: {type(exc).__name__}."
    except ValueError:
        return "[gemini-image-error] Gemini returned a non-JSON response."

    # Extract the first inline image from candidates[].content.parts[].
    image_b64: str | None = None
    mime_type = "image/png"
    try:
        for candidate in body.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    image_b64 = inline["data"]
                    mime_type = inline.get("mimeType") or inline.get("mime_type") or mime_type
                    break
            if image_b64:
                break
    except (AttributeError, TypeError):
        pass

    if not image_b64:
        return "[gemini-image-error] Gemini response contained no image data."

    # Align the saved extension with the returned MIME type when they disagree
    # (the API may return PNG or JPEG regardless of the requested filename).
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type)
    if ext and dest.suffix.lower() != ext:
        dest = dest.with_suffix(ext)

    try:
        image_bytes = base64.b64decode(image_b64)
        dest.write_bytes(image_bytes)
    except Exception as exc:  # noqa: BLE001
        return f"[gemini-image-error] failed to write image: {exc}"

    _meter(resolution)
    return str(dest.resolve())


@mcp.tool()
async def generate_image(
    prompt: str,
    output_path: str,
    resolution: str = "1K",
    aspect_ratio: str = "1:1",
) -> str:
    """Generate an image from a text prompt using Gemini 3 Pro Image (Nano Banana Pro).

    This is the HEADLESS IMAGE path for PRODUCTION-tier skills (infographics,
    handwritten-note visuals). For interactive use, the Higgsfield MCP-OAuth
    path (with nano_banana_pro) is preferred.

    Args:
        prompt: Detailed description of the image to generate. More specific
            prompts produce higher-fidelity results.
        output_path: Absolute path where the generated image should be saved
            (the directory must exist). Extension determines the filename; the
            API may return PNG or JPEG regardless of extension.
        resolution: Output resolution band — ``"1K"`` (default), ``"2K"``, or
            ``"4K"``. Higher resolutions cost more and take longer. For most
            infographic work ``"1K"`` or ``"2K"`` is sufficient.
        aspect_ratio: Image aspect ratio string, e.g. ``"1:1"``, ``"16:9"``,
            ``"9:16"``, ``"4:3"``. Defaults to ``"1:1"``.

    Returns the absolute path to the saved image on success. On any failure
    returns a ``[gemini-image-error] …`` string (the brain should surface this
    to the operator and suggest the Higgsfield MCP-OAuth path instead).
    """
    return await _generate(prompt, output_path, resolution, aspect_ratio)
