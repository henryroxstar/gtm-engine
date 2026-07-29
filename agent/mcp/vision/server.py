"""The Haiku vision worker MCP server (FastMCP, stdio).

A deliberately thin wrapper: one tool (``extract_text``) that loads an image file,
sends it to the Anthropic Messages API with a PINNED cheap model, and returns the
extracted text. The brain reasons over the returned text — it never spends its own
(expensive) vision tokens on the screenshot.

Cost metering at the code boundary (NIST AU-12; budget integrity): the worker
writes its OWN cost record to ``content/<profile>/costs.jsonl`` after each call,
computed from the response's token ``usage`` × pinned per-token rates — the
component that spends the money records the spend. The owning profile is passed in
at spawn via ``GTM_PROFILE`` (see :func:`agent.mcp_config.build_mcp_servers`); if
it is absent or the ledger write fails, metering is skipped silently — it must
never break an extraction.

Robustness contract: the tool returns a string. On any failure (no key, bad path,
oversize/unsupported image, HTTP error, malformed response) it returns a
``[vision-error] …`` string rather than raising — so the brain sees a tool result
it can react to (read the image itself, or ask for pasted text), and the SDK ↔ MCP
connection never breaks.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from gtm_core.metering import resolve_rates
from gtm_core.models import resolve_model

# --- Anthropic wiring (registry-resolved) ------------------------------------ #
# The model id and base_url come from the single source of truth (gtm_core/models.toml,
# role `vision`) — no hardcoded id here, so a version swap is a config edit and the P5
# deprecation gate can assert no model id is hardcoded outside the registry. image→text
# OCR runs on the cheapest vision-capable Claude model. See docs/RULES.md (model discipline).
_SPEC = resolve_model("vision")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", _SPEC.base_url).rstrip("/")
ANTHROPIC_VERSION = "2023-06-01"
VISION_MODEL = _SPEC.model  # resolved from the registry (single source of truth)
_HTTP_TIMEOUT_S = 60.0
_MAX_OUTPUT_TOKENS = 2048

# Image guards: the worker reads a local file and ships its bytes to Anthropic, so
# bound what it will open. Bare image suffixes only; cap size at the API's per-image limit.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB — Anthropic base64 per-image ceiling.

# Per-1K-token rates (USD) for the cost ledger resolve through the shared metering
# contract: env override (HAIKU_*) → the registry rate pinned for the resolved vision
# model → list-pricing default. Sourcing from _SPEC keeps the rate aligned with the
# model id the registry hands us. Image tokens are billed as input.
_INPUT_USD_PER_1K, _OUTPUT_USD_PER_1K = resolve_rates(
    _SPEC, env_input="HAIKU_INPUT_USD_PER_1K", env_output="HAIKU_OUTPUT_USD_PER_1K"
)

_DEFAULT_INSTRUCTIONS = (
    "Extract all text visible in this image, verbatim and in reading order. "
    "Output only the extracted text — no commentary, no description of the layout."
)

mcp = FastMCP("vision-worker")


def _load_image(image_path: str) -> tuple[str, str]:
    """Resolve, guard, and base64-encode an image file.

    Returns ``(media_type, base64_data)``. Raises ``ValueError`` with a clean,
    operator-readable message on any guard failure (missing/oversize/unsupported)
    — the caller maps that to a ``[vision-error] …`` tool result.
    """
    raw = (image_path or "").strip()
    if not raw:
        raise ValueError("no image_path provided")
    path = Path(raw).expanduser()
    suffix = path.suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise ValueError(
            f"unsupported image type {suffix!r} (expected one of {', '.join(sorted(_MEDIA_TYPES))})"
        )
    if not path.is_file():
        raise ValueError(f"no readable image file at {raw}")
    size = path.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        raise ValueError(f"image is {size} bytes (> {_MAX_IMAGE_BYTES} limit); downscale it first")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return media_type, data


def _meter(usage: dict | None) -> None:
    """Append a cost record for one extraction to the owning profile's cost ledger.

    Best-effort and import-light: any failure (no profile, no usage, ledger error) is
    swallowed so metering can never break an extraction or the MCP connection.
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile or not isinstance(usage, dict):
        return
    try:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cost = (
            input_tokens / 1000.0 * _INPUT_USD_PER_1K + output_tokens / 1000.0 * _OUTPUT_USD_PER_1K
        )
        # Imported lazily so importing the worker stays cheap and SDK-free.
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "vision-worker",
                "op": "extract_text",
                "model": VISION_MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort; never break an extraction
        return


async def _extract(image_path: str, instructions: str) -> str:
    """Load the image, call Anthropic with the pinned model, return text or an error string.

    Meters the call's token cost to the owning profile's ledger on success. Never
    raises: a missing key / bad path / HTTP error / bad payload all map to a
    ``[vision-error] …`` string so the brain gets a usable tool result.
    """
    key = _SPEC.api_key()  # reads the registry-named env var (ANTHROPIC_API_KEY) at call time
    if not key:
        return f"[vision-error] {_SPEC.api_key_env} is not set — worker cannot extract."

    try:
        media_type, data = _load_image(image_path)
    except ValueError as exc:
        return f"[vision-error] {exc}"

    prompt = instructions.strip() or _DEFAULT_INSTRUCTIONS
    payload = {
        "model": VISION_MODEL,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    url = f"{ANTHROPIC_BASE_URL}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:  # 4xx/5xx — surface status, redact body
        return f"[vision-error] Anthropic HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:  # network/timeout
        return f"[vision-error] Anthropic request failed: {type(exc).__name__}."
    except ValueError:  # non-JSON body
        return "[vision-error] Anthropic returned a non-JSON response."

    try:
        # content is a list of blocks; take the first text block.
        text = next(b["text"] for b in body["content"] if b.get("type") == "text").strip()
    except (KeyError, IndexError, TypeError, StopIteration):
        return "[vision-error] Anthropic response had no text block."

    _meter(body.get("usage") if isinstance(body, dict) else None)
    return text


@mcp.tool()
async def extract_text(image_path: str, instructions: str = "") -> str:
    """Extract text from an image FILE using a cheap pinned model (claude-haiku-4-5).

    Use this to read a screenshot — e.g. a LinkedIn reactions or comments list —
    without spending the brain model's expensive vision tokens. The image must be a
    **file on disk** the worker can open (an absolute path is safest); an image
    pasted directly into chat is already in the brain's context and cannot be routed
    here. Images sent to the Telegram cockpit are persisted to
    ``content/<profile>/uploads/`` (gtm_core.uploads.save_inbound_image) and that
    path is handed to the brain, so this worker is reachable from the cockpit too.

    Args:
        image_path: Absolute path to a local image file (.png/.jpg/.jpeg/.gif/.webp,
            ≤ 5 MB).
        instructions: Optional steer for what to pull out — e.g. "List each person's
            name, headline, and reaction type, one per line." Defaults to a verbatim
            full-text extraction.

    Returns the extracted text as a string. This is a DRAFT perception pass — the
    brain reviews and structures it. On any failure returns a ``[vision-error] …``
    string (the brain should then read the image itself or ask for pasted text).
    """
    return await _extract(image_path, instructions)
