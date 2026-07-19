"""Higgsfield REST video-generation worker MCP server (FastMCP, stdio).

A thin wrapper around the Higgsfield developer REST API (``platform.higgsfield.ai``):
three tools for the headless DoP Standard image-to-video path:

  - ``upload_image`` — uploads a local image to Higgsfield storage and returns a
    public URL usable as ``image_url`` (DoP requires a hosted image, not bytes).
  - ``generate_video`` — submits a generation job, returns a ``request_id``
    immediately (non-blocking). The brain stores the ID and polls later.
  - ``check_video_status`` — polls a job by ID; returns the current status and,
    when complete, the video URL.

Headless video path: DoP Standard is the only Higgsfield first-party model
available on the developer REST platform. Third-party models (Kling 3.0, Nano
Banana Pro) are available only via the Higgsfield MCP-OAuth (consumer) path.
See docs/prds/2026-06-19-higgsfield-integration.md §3.2 for the model roster.

Model discipline: the model is PINNED to ``higgsfield-ai/dop/standard``.
Do not template it from env. See docs/RULES.md (model discipline).

Auth: ``HIGGSFIELD_API_KEY`` and ``HIGGSFIELD_API_SECRET`` are passed via env
(never on the cmdline). The REST API expects ``Authorization: Key {key}:{secret}``
(verified 2026-06-20 against docs.higgsfield.ai). The ``upload_image`` tool reuses
the official ``higgsfield-client`` SDK, which reads ``HF_API_KEY`` +
``HF_API_SECRET`` — the worker maps the env vars across before calling it.

Submit is path-style: ``POST {base}/{model_id}`` (model in the path, not the
body); poll ``GET {base}/requests/{request_id}/status``; the completed payload
nests the URL at ``video.url``.

Cost metering (NIST AU-12; budget integrity): video generation is credit-based
and the REST API exposes no balance/transactions endpoint, so the worker logs a
documented USD estimate per generation to ``costs.jsonl`` (env-overridable via
``HIGGSFIELD_VIDEO_EST_USD``). Replace with the exact developer-portal rate once
confirmed at cloud.higgsfield.ai.

Robustness contract: all failures return ``[higgsfield-error] …`` strings —
never raise from a tool — so the brain gets a usable result and the MCP
connection never drops.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# --- Higgsfield wiring (pinned) --------------------------------------------- #
HIGGSFIELD_BASE_URL = os.getenv("HIGGSFIELD_BASE_URL", "https://platform.higgsfield.ai").rstrip("/")
# PINNED — the only first-party Higgsfield model on the developer REST platform.
# Do not template from env. See §3.2 of the integration PRD.
HIGGSFIELD_VIDEO_MODEL = "higgsfield-ai/dop/standard"
_HTTP_TIMEOUT_S = 30.0  # submit is fast; polling is cheap

# --- Per-generation USD estimate (env-overridable) -------------------------- #
# The REST API exposes no balance/transactions endpoint and the developer-portal
# per-model rate card is not published (PRD open decision #4), so we log a
# documented estimate rather than 0. Default $0.35 ≈ consumer DoP Standard 720p
# (7 cr/3s × ~$0.05/cr). Reference floor: WaveSpeed DoP ≈ $0.13/run. Replace with
# the exact developer-portal rate once confirmed at cloud.higgsfield.ai.
_VIDEO_EST_USD = float(os.getenv("HIGGSFIELD_VIDEO_EST_USD") or "0.35")
_CREDIT_ESTIMATE_NOTE = "credit-estimated-dop-standard"

mcp = FastMCP("higgsfield-video-worker")


def _auth_header() -> str:
    """Build the Authorization header value from env-injected key + secret.

    Scheme: ``Key {key}:{secret}`` — verified 2026-06-20 against docs.higgsfield.ai.
    """
    key = os.getenv("HIGGSFIELD_API_KEY") or ""
    secret = os.getenv("HIGGSFIELD_API_SECRET") or ""
    return f"Key {key}:{secret}"


def _meter(op: str) -> None:
    """Log a credit-estimated cost record for one video generation.

    Best-effort: any failure is swallowed — metering must never break a call.
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile:
        return
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "higgsfield-video-worker",
                "op": op,
                "model": HIGGSFIELD_VIDEO_MODEL,
                "cost_usd": round(_VIDEO_EST_USD, 6),
                "note": _CREDIT_ESTIMATE_NOTE,
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort
        return


@mcp.tool()
async def upload_image(image_path: str) -> str:
    """Upload a local image to Higgsfield storage and return a public URL.

    DoP Standard requires a hosted ``image_url`` (it does not accept raw bytes),
    so a locally-generated cover frame (e.g. from the ``gemini_image`` worker)
    must be uploaded first. Uses the official ``higgsfield-client`` SDK.

    Args:
        image_path: Absolute path to a local image file (PNG/JPEG/WebP).

    Returns the public URL on success, or ``[higgsfield-error] …`` on failure.
    """
    key = os.getenv("HIGGSFIELD_API_KEY")
    secret = os.getenv("HIGGSFIELD_API_SECRET")
    if not key or not secret:
        return "[higgsfield-error] HIGGSFIELD_API_KEY and/or HIGGSFIELD_API_SECRET are not set."

    path = Path(image_path).expanduser()
    if not path.is_file():
        return f"[higgsfield-error] image file not found: {path}"

    # The SDK reads HF_API_KEY / HF_API_SECRET — map our env vars across.
    os.environ["HF_API_KEY"] = key
    os.environ["HF_API_SECRET"] = secret

    try:
        import higgsfield_client
    except ImportError:
        return (
            "[higgsfield-error] higgsfield-client SDK is not installed (uv add higgsfield-client)."
        )

    try:
        url = await higgsfield_client.upload_file_async(str(path))
    except Exception as exc:  # noqa: BLE001 — surface, never raise
        return f"[higgsfield-error] upload failed: {type(exc).__name__}."

    if not url:
        return "[higgsfield-error] upload returned no URL."
    return str(url)


@mcp.tool()
async def generate_video(
    image_url: str,
    prompt: str,
    duration: int = 5,
    seed: int | None = None,
    motions: list[str] | None = None,
    enhance_prompt: bool = False,
) -> str:
    """Submit a DoP Standard video-generation job to the Higgsfield REST API.

    This is the HEADLESS VIDEO path. The call returns immediately with a
    ``request_id`` — it does NOT wait for the video to finish. Use
    ``check_video_status(request_id)`` to poll for completion.

    Typical flow:
      1. Brain calls ``generate_video(...)`` → stores the returned ``request_id``.
      2. Brain waits ~30–60 s, then calls ``check_video_status(request_id)``.
      3. When status is ``"completed"``, brain receives the ``video_url``.

    Args:
        image_url: Public URL of the reference image that anchors the video
            (required by DoP Standard). Must be reachable by Higgsfield servers —
            use ``upload_image`` to turn a local file into such a URL.
        prompt: Motion/scene description. DoP Standard animates the reference
            image according to this prompt.
        duration: Output length in seconds (DoP Standard accepts a duration;
            defaults to 5).
        seed: Optional deterministic seed (1–1 000 000). Omit for random.
        motions: Optional list of motion directives (max 2; support unverified
            for DoP — sent only if provided).
        enhance_prompt: If True, Higgsfield will rewrite the prompt for better
            results. Defaults to False (use the prompt verbatim).

    Returns ``request_id:<id>`` on success, or ``[higgsfield-error] …`` on failure.
    """
    key = os.getenv("HIGGSFIELD_API_KEY")
    secret = os.getenv("HIGGSFIELD_API_SECRET")
    if not key or not secret:
        return (
            "[higgsfield-error] HIGGSFIELD_API_KEY and/or HIGGSFIELD_API_SECRET are not set "
            "— complete H0 (fund account + mint keys in Doppler) before calling this tool."
        )

    if not image_url or not image_url.strip():
        return "[higgsfield-error] image_url is required for DoP Standard."
    if not prompt or not prompt.strip():
        return "[higgsfield-error] prompt is required."

    # Model is path-style (POST {base}/{model_id}); it does NOT go in the body.
    body: dict = {
        "image_url": image_url.strip(),
        "prompt": prompt.strip(),
        "duration": int(duration),
    }
    if enhance_prompt:
        body["enhance_prompt"] = True
    if seed is not None:
        body["seed"] = max(1, min(1_000_000, int(seed)))
    if motions:
        body["motions"] = motions[:2]  # max 2

    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{HIGGSFIELD_BASE_URL}/{HIGGSFIELD_VIDEO_MODEL}"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[higgsfield-error] Higgsfield HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"[higgsfield-error] Higgsfield request failed: {type(exc).__name__}."
    except ValueError:
        return "[higgsfield-error] Higgsfield returned a non-JSON response."

    request_id = data.get("request_id") or data.get("id") or data.get("job_id") or ""
    if not request_id:
        return f"[higgsfield-error] no request_id in response: {data}"

    _meter("generate_video")
    return f"request_id:{request_id}"


@mcp.tool()
async def check_video_status(request_id: str) -> str:
    """Poll the status of a Higgsfield DoP video-generation job.

    Call this after ``generate_video(...)`` to check if the video is ready.
    Poll every 30–60 seconds — generating a DoP video typically takes 60–120 s.

    Args:
        request_id: The ID returned by ``generate_video`` (strip the
            ``request_id:`` prefix if present).

    Returns one of:
      - ``status:queued`` / ``status:in_progress`` — not yet done; poll again.
      - ``status:completed video_url:<url>`` — the video is ready at the URL.
      - ``status:failed reason:<reason>`` — failed/nsfw/cancelled (credits refund
        automatically on failed/nsfw); retry or escalate.
      - ``[higgsfield-error] …`` — network/auth error; check keys and retry.
    """
    key = os.getenv("HIGGSFIELD_API_KEY")
    secret = os.getenv("HIGGSFIELD_API_SECRET")
    if not key or not secret:
        return "[higgsfield-error] HIGGSFIELD_API_KEY and/or HIGGSFIELD_API_SECRET are not set."

    rid = (request_id or "").strip().removeprefix("request_id:")
    if not rid:
        return "[higgsfield-error] request_id is required."

    headers = {"Authorization": _auth_header(), "Accept": "application/json"}
    url = f"{HIGGSFIELD_BASE_URL}/requests/{rid}/status"

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"[higgsfield-error] Higgsfield HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:
        return f"[higgsfield-error] Higgsfield request failed: {type(exc).__name__}."
    except ValueError:
        return "[higgsfield-error] Higgsfield returned a non-JSON response."

    status = (data.get("status") or "unknown").lower()
    if status in ("completed", "done", "succeeded"):
        video = data.get("video") if isinstance(data.get("video"), dict) else {}
        images = data.get("images") or []
        first_image = images[0] if images and isinstance(images[0], dict) else {}
        video_url = (
            (video or {}).get("url") or first_image.get("url") or data.get("video_url") or ""
        )
        if video_url:
            return f"status:completed video_url:{video_url}"
        return f"status:completed (no video url in response: {data})"
    elif status in ("failed", "error", "nsfw"):
        reason = data.get("error") or data.get("reason") or data.get("message") or status
        return f"status:failed reason:{reason}"
    elif status == "cancelled":
        return "status:failed reason:cancelled"
    else:
        return f"status:{status}"
