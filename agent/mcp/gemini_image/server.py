"""Gemini image-generation worker MCP server (FastMCP, stdio).

A thin wrapper around the Google Gemini REST API
(``/v1beta/models/{model}:generateContent``): one tool (``generate_image``) that
calls ``gemini-3-pro-image-preview`` with a text prompt — and, since 2026-09-06,
an ordered list of reference images — decodes the returned inline base64 image,
and writes it to the requested output path. The brain reasons over the generated
image path; it never sees raw bytes.

Reference images are a NEW INPUT CLASS on an existing destination: a file-path
parameter on a tool that ships the bytes to a third party. Every path is confined
to the resolved content root and size-capped in :mod:`.references`; the output
path is confined the same way. Nothing here adds an egress destination.

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

Transport contract (added 2026-08-27 after a live 503/503/ReadTimeout run): the
call is retried on failures that provably cost nothing and NOT retried on the one
failure that may already have been billed — see :func:`_request_image` for why
that asymmetry is a cost-cap property (§R2), not timidity. Read timeouts are
sized per resolution band by :func:`_read_timeout_for`.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from agent.mcp import nonneg_price_env
from gtm_core.confine import ConfinementError, confined_output_path

from .references import (
    PRICE_PER_REF_USD,
    READ_TIMEOUT_PER_REF_S,
    manifest_line,
    resolve_references,
)

# --- Gemini wiring (pinned) ------------------------------------------------- #
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip(
    "/"
)
# PINNED — Nano Banana Pro / Google Gemini 3 Pro Image. Do not template from env.
GEMINI_IMAGE_MODEL = "gemini-3-pro-image-preview"

# --- Timeouts (granular, per-resolution read) ------------------------------- #
# The flat 120s ceiling this replaced was the proximate cause of the 2026-08-27
# ReadTimeout. A 2K Nano Banana Pro render routinely runs 60-90s, and every skill
# that drives this worker (carousel-visuals, infographic-data,
# infographic-handwritten) asks for 2K — so the slow path IS the default path and
# 120s left almost no headroom.
#
# Split into a granular httpx.Timeout rather than one number: connect/write/pool
# stay tight, so a genuine network fault still fails in seconds instead of
# minutes, while the READ phase — the only one that waits on generation — is
# sized per resolution band. A single read timeout wide enough for 4K would make
# a hung 1K request burn six minutes before it reported anything.
_CONNECT_TIMEOUT_S = 10.0
_WRITE_TIMEOUT_S = 30.0
_POOL_TIMEOUT_S = 10.0
_READ_TIMEOUT_BY_RESOLUTION: dict[str, float] = {
    "1K": 120.0,
    "2K": 240.0,
    "4K": 360.0,
}
_READ_TIMEOUT_ENV = "GEMINI_IMAGE_READ_TIMEOUT_S"

# --- Retry ladder ----------------------------------------------------------- #
# Capped far below the syften pager's 330s: this runs INSIDE a tool call the brain
# is awaiting, so a multi-minute backoff would stall the pipeline rather than
# rescue it. Worst-case added latency is the sleep ladder (2+4+8 = 14s), because
# a retryable status is rejected by the server immediately and never spends the
# read timeout.
_MAX_RETRIES = 3
_RETRY_BASE_S = 2.0
_RETRY_MAX_S = 30.0
# 429 (rate/quota) and 5xx (overload, backend fault) are the statuses Google
# documents as retryable. 503 in particular is "model overloaded" — exactly what
# the operator hit twice on 2026-08-27 and had to retry by hand.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# --- Per-image pricing (USD) by resolution band ----------------------------- #
# Overridable via env so a price change doesn't require a code edit; validated so a
# negative override cannot corrupt the cost cap (see agent.mcp.nonneg_price_env).
# Defaults: $0.134/image for 1K/2K, $0.24/image for 4K (Gemini 3 Pro Image list pricing).
_PRICE_1K_USD = nonneg_price_env("GEMINI_IMAGE_PRICE_1K_USD", "0.134")
_PRICE_2K_USD = nonneg_price_env("GEMINI_IMAGE_PRICE_2K_USD", "0.134")
_PRICE_4K_USD = nonneg_price_env("GEMINI_IMAGE_PRICE_4K_USD", "0.240")

_RESOLUTION_PRICES: dict[str, float] = {
    "1K": _PRICE_1K_USD,
    "2K": _PRICE_2K_USD,
    "4K": _PRICE_4K_USD,
}

# Valid values for the ``image_size`` field in the Gemini interactions API.
_VALID_RESOLUTIONS = {"1K", "2K", "4K"}

mcp = FastMCP("gemini-image-worker")


def _meter(resolution: str, n_references: int = 0) -> None:
    """Append a cost record for one generation to the owning profile's ledger.

    The fee is the resolution band's output price plus ``n_references`` times the
    per-reference input price, and ``n_references`` is recorded on the row so the
    ledger says what was priced rather than leaving it to be inferred.

    Best-effort: any failure (no profile, ledger error) is swallowed — metering
    must never break a generation or the MCP connection.
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile:
        return
    cost = _RESOLUTION_PRICES.get(resolution, _PRICE_1K_USD) + n_references * PRICE_PER_REF_USD
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "gemini-image-worker",
                "op": "generate_image",
                "model": GEMINI_IMAGE_MODEL,
                "resolution": resolution,
                "n_references": n_references,
                "cost_usd": round(cost, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort
        return


def _read_timeout_for(resolution: str, n_references: int = 0) -> float:
    """Seconds to wait for the generated image, sized to the resolution band and reference count.

    ``GEMINI_IMAGE_READ_TIMEOUT_S`` overrides every band — the redeploy-free
    break-glass for an operator watching renders time out on the VPS, in the
    spirit of ``HERMES_MODEL``. It must be a POSITIVE number: httpx reads ``0``
    as "no timeout at all", which would hand a hung connection an unbounded
    stall inside a tool call the brain is awaiting. A bad value is ignored in
    favour of the band default rather than raised — a typo in an ops override
    must not take image generation down. References add to whichever base applies.
    """
    base = _READ_TIMEOUT_BY_RESOLUTION.get(resolution, _READ_TIMEOUT_BY_RESOLUTION["1K"])
    raw = (os.getenv(_READ_TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            override = float(raw)
        except ValueError:
            override = 0.0
        if override > 0:
            base = override
    return base + n_references * READ_TIMEOUT_PER_REF_S


def _status_error(status: int) -> str:
    """Map an HTTP status to an actionable ``[gemini-image-error] …`` line.

    Response bodies are never echoed (they can carry request context and the
    other in-repo workers hold the same line) — but the status CLASS is exactly
    what the operator needs and cannot otherwise see. A bare "HTTP 503" sent the
    2026-08-27 triage down a "check the credentials" path that a 503 had already
    ruled out; an auth failure is a distinct number and now says so itself.
    """
    if status in (401, 403):
        return (
            f"[gemini-image-error] Gemini HTTP {status} — credentials rejected. "
            "Check GEMINI_API_KEY is current and that the key is enabled for the "
            "Generative Language API. Retrying will not help."
        )
    if status == 429:
        return (
            "[gemini-image-error] Gemini HTTP 429 — rate limit or quota exhausted "
            f"after {_MAX_RETRIES} retries. Check the project's image-generation "
            "quota; back off before trying again."
        )
    if status == 400:
        return (
            "[gemini-image-error] Gemini HTTP 400 — the request was rejected. "
            "Usually the prompt tripped a safety filter or the aspect_ratio is "
            "not one Gemini accepts. Retrying the same prompt will not help."
        )
    if status in _RETRYABLE_STATUSES:
        return (
            f"[gemini-image-error] Gemini HTTP {status} — the image service is "
            f"unavailable or overloaded; {_MAX_RETRIES} retries with backoff all "
            "failed. Nothing was generated or billed. Try again later, or use the "
            "Higgsfield MCP-OAuth path."
        )
    return f"[gemini-image-error] Gemini HTTP {status}."


def _slow_or_broken_write_error(
    exc: httpx.HTTPError, read_timeout: float, has_references: bool
) -> str:
    """The error line for a request that may already have been accepted, and so billed.

    Three cases, one message shape. A READ timeout always means the request was accepted. A
    write failure carrying reference bytes is ambiguous — a multi-megabyte body that broke
    part-way through the write may still have reached the server — so it gets the same
    treatment. A write failure with no references is a few hundred bytes that plainly did not
    land, and keeps its pre-2026-09-06 wording, which claims nothing about billing.
    """
    if isinstance(exc, httpx.ReadTimeout):
        return (
            "[gemini-image-error] Gemini did not return the image within "
            f"{read_timeout:.0f}s. The request WAS accepted, so the render may "
            "have completed and been billed without being delivered or logged "
            "to costs.jsonl. Not retried automatically for that reason. Retry "
            f"at a lower resolution, or raise {_READ_TIMEOUT_ENV}."
        )
    if has_references:
        return (
            f"[gemini-image-error] the request failed during upload "
            f"({type(exc).__name__}) while carrying reference images. It may "
            "have been accepted, so the render may have completed and been "
            "billed without being delivered or logged to costs.jsonl. Not "
            "retried automatically for that reason. Check the connection, "
            "then retry with fewer or smaller references."
        )
    return f"[gemini-image-error] Gemini request failed: {type(exc).__name__}."


async def _request_image(
    url: str,
    payload: dict,
    headers: dict[str, str],
    read_timeout: float,
    *,
    has_references: bool = False,
) -> dict | str:
    """POST one generation request; return the parsed body, or an error string.

    The retry policy is deliberately ASYMMETRIC, because Gemini bills per
    generated image and this worker is what writes the cost row (§R2):

      * **Retried** — a retryable status (429/5xx) and connect-phase failures.
        The server rejected the request or never received it, so no image was
        generated and nothing was billed; retrying costs nothing. This is the
        503 the operator hit twice on 2026-08-27 and had to retry by hand.
      * **NOT retried** — a read/write timeout. The request was ACCEPTED, so the
        image may have been generated and billed even though no bytes came back.
        Retrying that automatically would silently double-charge against the
        monthly cap while still writing no cost row, because ``_meter`` only runs
        on a delivered image. The operator makes that call, with the possible
        charge named in the error.

    Declining to retry the slow failure also bounds worst-case latency: a full
    read timeout is spent at most once per tool call.

    ``has_references`` re-derives one rung of the ladder rather than inheriting it.
    "Never reached the server" is a safe premise for a few hundred bytes of text;
    with a multi-megabyte body a failure part-way through the WRITE is ambiguous —
    the server may have accepted it. So a write error or write timeout on a request
    carrying references is not retried and carries the same billing caveat as the
    read timeout. Without references those two paths behave exactly as before.
    """
    timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT_S,
        read=read_timeout,
        write=_WRITE_TIMEOUT_S,
        pool=_POOL_TIMEOUT_S,
    )
    delay = _RETRY_BASE_S
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in _RETRYABLE_STATUSES or attempt == _MAX_RETRIES:
                return _status_error(status)
            # Honour Retry-After as a FLOOR when the server sends one — it knows
            # how long the overload will last better than a fixed ladder does.
            wait = delay
            hdr = exc.response.headers.get("Retry-After")
            if hdr:
                try:
                    wait = max(wait, float(hdr))
                except ValueError:
                    pass
            await asyncio.sleep(min(wait, _RETRY_MAX_S))
            delay *= 2
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.WriteError) as exc:
            # Accepted-then-silent: assume the render may have happened and been
            # billed. Do not retry (see the docstring) — hand the decision over.
            return _slow_or_broken_write_error(exc, read_timeout, has_references)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Never reached the server: no generation, no charge — safe to retry.
            if attempt == _MAX_RETRIES:
                return (
                    "[gemini-image-error] could not reach Gemini "
                    f"({type(exc).__name__}) after {_MAX_RETRIES} retries. "
                    "Nothing was generated or billed; check egress from the host."
                )
            await asyncio.sleep(min(delay, _RETRY_MAX_S))
            delay *= 2
        except httpx.HTTPError as exc:
            return f"[gemini-image-error] Gemini request failed: {type(exc).__name__}."
        except ValueError:
            return "[gemini-image-error] Gemini returned a non-JSON response."
    return f"[gemini-image-error] Gemini unreachable after {_MAX_RETRIES} retries."


def _decode_and_write(body: dict, dest: Path) -> tuple[str, str]:
    """Pull the first inline image out of a response and write it. ``(path, "")`` or ``("", err)``.

    Split out of :func:`_generate` so that function stays one job — build a request, send it,
    hand back a path — rather than also owning the response format. The extension is realigned
    to the returned MIME type here because the API may return PNG or JPEG regardless of the
    requested filename; that never changes the parent directory, so the confinement already
    performed on ``dest`` still holds.
    """
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
        return "", "[gemini-image-error] Gemini response contained no image data."

    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type)
    if ext and dest.suffix.lower() != ext:
        dest = dest.with_suffix(ext)

    try:
        dest.write_bytes(base64.b64decode(image_b64))
    except Exception as exc:  # noqa: BLE001
        return "", f"[gemini-image-error] failed to write image: {exc}"
    return str(dest.resolve()), ""


async def _generate(
    prompt: str,
    output_path: str,
    resolution: str,
    aspect_ratio: str,
    reference_images: list[str] | None = None,
) -> str:
    """Call Gemini interactions API, decode the image, write to output_path.

    Returns the absolute output path on success — followed, when references were
    sent, by one line naming their count and order — or ``[gemini-image-error] …``.
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

    try:
        dest = confined_output_path(output_path, content_root=None)
    except ConfinementError as exc:
        return f"[gemini-image-error] {exc}"
    if not dest.parent.exists():
        return f"[gemini-image-error] output directory does not exist: {dest.parent}"

    # References go AHEAD of the text part, in list order, so "the subject from the
    # first reference" addresses what the author meant. None/[] leaves the request
    # byte-identical to the text-only shape every existing caller sends.
    reference_parts, ref_error = resolve_references(reference_images)
    if ref_error:
        return ref_error
    n_references = len(reference_parts)

    payload: dict = {
        "contents": [{"parts": [*reference_parts, {"text": prompt.strip()}]}],
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

    result = await _request_image(
        url,
        payload,
        headers,
        _read_timeout_for(resolution, n_references),
        has_references=n_references > 0,
    )
    if isinstance(result, str):  # already a "[gemini-image-error] …" line
        return result
    body = result

    written, decode_error = _decode_and_write(body, dest)
    if decode_error:
        return decode_error

    _meter(resolution, n_references)
    # Path first, always — five shipped callers use the return value AS a path, and a
    # text-only call must stay byte-identical to its pre-2026-09-06 bare-path answer.
    manifest = f"\n{manifest_line(list(reference_images or []))}" if n_references else ""
    return f"{written}{manifest}"


@mcp.tool()
async def generate_image(
    prompt: str,
    output_path: str,
    resolution: str = "1K",
    aspect_ratio: str = "1:1",
    reference_images: list[str] | None = None,
) -> str:
    """Generate an image from a text prompt — optionally conditioned on reference images — using Gemini 3 Pro Image (Nano Banana Pro).

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
            ``"4K"``. Higher resolutions cost more and take longer, and the wait
            budget scales with the band (2 / 4 / 6 minutes). For most infographic
            work ``"1K"`` or ``"2K"`` is sufficient.
        aspect_ratio: Image aspect ratio string, e.g. ``"1:1"``, ``"16:9"``,
            ``"9:16"``, ``"4:3"``. Defaults to ``"1:1"``.
        reference_images: Optional ORDERED list of absolute paths to existing
            image files (png/jpg/webp/heic), each sent to the model ahead of the
            prompt in exactly this order — so ``"the subject from the first
            reference"`` means position 1. Never sorted or deduplicated: pass the
            same file twice and it is sent twice. Every path must live inside the
            profile's content root; anything outside is refused unread. At most 14,
            each at most 5 MB, refused (never resized) when over. For a delta edit,
            pass the prior output as reference 1 and prompt only the change, holding
            ``aspect_ratio`` and ``resolution`` fixed so the pair stays
            frame-compatible.

    Transient overload (HTTP 503) and rate limiting (429) are retried internally
    with exponential backoff, so the brain should NOT hand-retry on those — the
    error it sees is already the post-retry verdict. Two errors are worth reading
    closely before spending another call:

      * a **timeout** error means the request was accepted and the render may have
        been billed without being delivered; a blind retry may pay twice;
      * an **HTTP 401/403** error means the key is bad — retrying cannot fix it.

    Returns the absolute path to the saved image on success — and, when
    references were sent, a second line naming their count and order as actually
    sent, so a mismatch is visible rather than inferred. On any failure returns a
    ``[gemini-image-error] …`` string (the brain should surface this to the
    operator and suggest the Higgsfield MCP-OAuth path instead).
    """
    return await _generate(prompt, output_path, resolution, aspect_ratio, reference_images)
