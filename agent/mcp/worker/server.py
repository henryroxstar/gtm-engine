"""The DeepSeek worker MCP server (FastMCP, stdio).

A deliberately thin wrapper: two tools (``summarize`` / ``draft``) that each shape
a prompt and call the DeepSeek chat-completions endpoint (OpenAI-compatible) with
a PINNED model id.

Cost metering at the code boundary (NIST AU-12; budget integrity): the worker
writes its OWN cost record to ``content/<profile>/costs.jsonl`` after each call,
computed from the response's token ``usage`` × pinned per-token rates. This does
NOT depend on the brain remembering to log the call — the component that spends
the money records the spend (the same principle the publish path already uses for
its audit record). The owning profile is passed in at spawn via ``GTM_PROFILE``
(see :func:`agent.mcp_config.build_mcp_servers`); if it is absent or the ledger
write fails, metering is skipped silently — it must never break a generation.

Robustness contract: every tool returns a string. On any failure (no key, HTTP
error, malformed response) it returns a ``[worker-error] …`` string rather than
raising — so the brain sees a tool result it can react to (fall back to doing the
work itself), and the SDK ↔ MCP connection never breaks.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

from gtm_core.models import resolve_model

# --- DeepSeek wiring (registry-resolved) ------------------------------------- #
# The model id, base_url, key-env name, and cost rates all come from the single
# source of truth (gtm_core/models.toml, role `worker_draft`) — no hardcoded id
# here, so a provider/version swap is a config edit and the P5 deprecation gate
# can assert no model id is hardcoded outside the registry. Env vars still win
# (break-glass) so a price/endpoint change is config, not a code edit.
_SPEC = resolve_model("worker_draft")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", _SPEC.base_url).rstrip("/")
DEEPSEEK_MODEL = _SPEC.model  # resolved from the registry (single source of truth)
_HTTP_TIMEOUT_S = 60.0

# Per-1K-token rates (USD) for the cost ledger — sourced from the registry spec
# (V4 Flash), env-overridable so a price change doesn't require a code edit. Cost
# is an estimate logged alongside the raw token counts so it can be recomputed.
_INPUT_USD_PER_1K = float(os.getenv("DEEPSEEK_INPUT_USD_PER_1K") or str(_SPEC.input_usd_per_1k))
_OUTPUT_USD_PER_1K = float(os.getenv("DEEPSEEK_OUTPUT_USD_PER_1K") or str(_SPEC.output_usd_per_1k))

mcp = FastMCP("deepseek-worker")


def _meter(op: str, usage: dict) -> None:
    """Append a cost record for one worker call to the owning profile's cost ledger.

    Best-effort and import-light: any failure (no profile, no usage, ledger error) is
    swallowed so metering can never break a generation or the MCP connection.
    """
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile or not isinstance(usage, dict):
        return
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = (
            prompt_tokens / 1000.0 * _INPUT_USD_PER_1K
            + completion_tokens / 1000.0 * _OUTPUT_USD_PER_1K
        )
        # Imported lazily so importing the worker stays cheap and SDK-free.
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "deepseek-worker",
                "op": op,
                "model": DEEPSEEK_MODEL,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort; never break a generation
        return


async def _chat(
    messages: list[dict],
    *,
    op: str = "chat",
    max_tokens: int = 1024,
    temperature: float = 0.3,
) -> str:
    """Call DeepSeek chat-completions and return the assistant text (or an error string).

    Meters the call's token cost to the owning profile's ledger on success (``op`` labels
    the record). Never raises: a missing key / HTTP error / bad payload all map to a
    ``[worker-error] …`` string so the brain gets a usable tool result.
    """
    key = _SPEC.api_key()  # reads the registry-named env var (DEEPSEEK_API_KEY) at call time
    if not key:
        return f"[worker-error] {_SPEC.api_key_env} is not set — worker cannot generate."

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:  # 4xx/5xx — surface status, redact body
        return f"[worker-error] DeepSeek HTTP {exc.response.status_code}."
    except httpx.HTTPError as exc:  # network/timeout
        return f"[worker-error] DeepSeek request failed: {type(exc).__name__}."
    except ValueError:  # non-JSON body
        return "[worker-error] DeepSeek returned a non-JSON response."

    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return "[worker-error] DeepSeek response missing choices[0].message.content."

    # Meter AFTER we have a usable result, from the authoritative token usage.
    _meter(op, data.get("usage") if isinstance(data, dict) else None)
    return content


@mcp.tool()
async def summarize(texts: list[str], style: str = "concise") -> str:
    """Bulk-summarise source snippets into tight, factual notes.

    Args:
        texts: One or more source snippets (article blurbs, story rows) to summarise.
        style: ``"concise"`` (default), ``"bullets"``, or ``"headline"``.

    Returns one block of text: a numbered summary per input snippet. Used by
    content-radar to summarise clustered stories before Claude ranks them. Output
    is a DRAFT — the brain reviews it.
    """
    if not texts:
        return "[worker-error] no texts provided to summarize."
    joined = "\n\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts))
    style_hint = {
        "bullets": "Summarise each numbered item as 2-3 terse bullets.",
        "headline": "Give each numbered item a single ≤12-word headline.",
    }.get(style, "Summarise each numbered item in 1-2 factual sentences.")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a fast summarisation worker for a GTM content engine. "
                "Be factual and specific; never invent facts not present in the input. "
                "Preserve the input numbering exactly."
            ),
        },
        {"role": "user", "content": f"{style_hint}\n\n{joined}"},
    ]
    return await _chat(messages, op="summarize", max_tokens=1200, temperature=0.2)


@mcp.tool()
async def draft(brief: str, format: str = "linkedin-post") -> str:
    """Produce first-draft copy from a brief. Claude always reviews the result.

    Args:
        brief: What to write — topic, angle, key facts, audience, constraints.
        format: ``"linkedin-post"`` (default), ``"linkedin-carousel"``,
            ``"x-thread"``, ``"x-single"``, ``"instagram-reel"``,
            ``"instagram-carousel"``, or ``"notes"``.

    Returns draft copy as text. This is a fast first pass, NOT publish-ready: the
    brain edits it and every asset still passes the content linter before review.
    """
    if not brief.strip():
        return "[worker-error] empty brief provided to draft."
    fmt_hint = {
        "linkedin-carousel": (
            "Draft a LinkedIn carousel as 8-12 slides. One idea per slide. "
            "Slide 1 is a hook ≤140 characters. No URLs in the body."
        ),
        "x-thread": (
            "Draft an X thread as 5-9 tweets, each ≤280 characters, numbered 1/, 2/, …. "
            "Tweet 1/ must stand alone and contain NO link (it is the whole hook + promise). "
            "One idea per tweet; the last tweet recaps with a CTA."
        ),
        "x-single": "Draft a single X post ≤280 characters. Strong standalone POV, no link.",
        "instagram-reel": (
            "Draft a 30-90s Instagram Reel: a 1-2 second hook line, then a short beat/shot list, "
            "plus an SEO-friendly caption. Keep it punchy and visual."
        ),
        "instagram-carousel": (
            "Draft an Instagram carousel as 7-10 slides, one idea per slide, plus an SEO caption. "
            "Slide 1 is the hook."
        ),
        "notes": "Draft working notes / an outline — not finished prose.",
    }.get(format, "Draft a single LinkedIn post. Strong POV, no body URLs, ≤2 hashtags.")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a fast first-draft copy worker for a GTM content engine. "
                "Write in a direct, signal-first voice with no fluff or buzzwords. "
                "Only use facts present in the brief; flag anything you are unsure of."
            ),
        },
        {"role": "user", "content": f"{fmt_hint}\n\nBrief:\n{brief}"},
    ]
    return await _chat(messages, op="draft", max_tokens=1500, temperature=0.4)
