"""GTM MCP server — FastMCP (streamable-http transport).

Launch surface (Phase E, plan fix #7 — no publish/PRODUCTION tools):

  CORE (free, deterministic, zero inference cost to operator):
    radar_check      — has a story URL/title been covered before?
    profile_context  — read a profile knowledge file (ICP, strategy, etc.)

  PIPELINE (pro subscription required, DeepSeek-metered):
    draft_post       — first-draft LinkedIn post or carousel from a brief
    draft_outreach   — first-draft outreach message from a brief

Auth: Authorization: Bearer sk-<key> on every request.
      Falls back to MCP_API_KEY env var (local dev / testing).

Cost cap: enforced before every PIPELINE call (§R2).
Metering: every PIPELINE call is recorded in mcp_calls (V006).

Security invariants held:
  - No publish tool surface (plan fix #7).
  - No PRODUCTION tools (plan fix #7; capabilities.py decision matrix).
  - Raw API key never logged (only hash hits the DB).
  - Profile path segments validated by gtm_core.paths._safe_segment.
  - Untrusted input (brief, url, title) treated as data — never executed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from mcp.server.fastmcp import Context, FastMCP

from gtm_core.capabilities import Entitlement
from gtm_core.db import assert_runtime_role_least_privilege
from gtm_core.models import resolve_model
from gtm_core.paths import PathConfig, _safe_segment, resolve_knowledge_file

from .auth import ApiKeyCtx, validate_api_key
from .meter import check_budget, meter_call

# ── module-level state (DB pool) ──────────────────────────────────────────────


@dataclass
class _State:
    pool: Any = None


_state = _State()


@asynccontextmanager
async def _lifespan(app):
    dsn = os.environ["DATABASE_URL"]
    # search_path pinned for parity with the backend runtime pool (defensive; the
    # SECURITY DEFINER resolve_api_key pins its own search_path too).
    _state.pool = await asyncpg.create_pool(
        dsn, min_size=2, max_size=8, server_settings={"search_path": "public"}
    )
    # Loud boot failure if the runtime role can bypass RLS — a mis-deploy still
    # pointed at the owner DSN would run every MCP query RLS-exempt (mirrors
    # backend/main.py). The DATABASE_URL must point at the non-owner gtm_api role.
    await assert_runtime_role_least_privilege(_state.pool)
    yield
    await _state.pool.close()
    _state.pool = None


mcp = FastMCP(
    "gtm-mcp-server",
    lifespan=_lifespan,
    host=os.getenv("MCP_HOST", "0.0.0.0"),  # nosec B104 — containerised; MCP_HOST overrides in prod
    port=int(os.getenv("MCP_PORT", "8001")),
)


# ── DeepSeek wiring (identical rates/pinning to agent/mcp/worker/server.py) ───

# Registry-resolved (single source of truth; identical wiring to agent/mcp/worker/server.py).
# No hardcoded id — a provider/version swap is a config edit and the deprecation gate
# can assert no model id lives outside gtm_core/models.toml.
_DS_SPEC = resolve_model("worker_draft")
_DS_BASE = os.getenv("DEEPSEEK_BASE_URL", _DS_SPEC.base_url).rstrip("/")
_DS_MODEL = _DS_SPEC.model
_DS_TIMEOUT = 60.0
_DS_IN_USD = float(os.getenv("DEEPSEEK_INPUT_USD_PER_1K") or str(_DS_SPEC.input_usd_per_1k))
_DS_OUT_USD = float(os.getenv("DEEPSEEK_OUTPUT_USD_PER_1K") or str(_DS_SPEC.output_usd_per_1k))


async def _deepseek(
    messages: list[dict],
    *,
    op: str,
    max_tokens: int = 1024,
    temperature: float = 0.35,
) -> tuple[str, int, int, float]:
    """Call DeepSeek; return (text, prompt_tokens, completion_tokens, cost_usd).

    Raises ValueError on any failure so the tool can surface a clean error
    instead of swallowing the problem silently.
    """
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY not set — PIPELINE tools unavailable")

    payload = {
        "model": _DS_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=_DS_TIMEOUT) as client:
        resp = await client.post(f"{_DS_BASE}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    cost = pt / 1000.0 * _DS_IN_USD + ct / 1000.0 * _DS_OUT_USD
    return text, pt, ct, round(cost, 6)


# ── auth helpers ──────────────────────────────────────────────────────────────


async def _require_key(ctx: Context) -> ApiKeyCtx:
    """Extract and validate the API key from the request context or env."""
    raw = ""
    try:
        request = ctx.request_context.request
        if request is not None:
            auth = request.headers.get("authorization", "")
            raw = re.sub(r"^[Bb]earer\s+", "", auth).strip()
    except Exception:  # noqa: BLE001
        pass
    if not raw:
        raw = os.getenv("MCP_API_KEY", "")
    if not raw:
        raise ValueError("Missing API key — set Authorization: Bearer <key> header")
    result = await validate_api_key(raw, _state.pool)
    if result is None:
        raise ValueError("Invalid or expired API key")
    return result


async def _require_pipeline(ctx: Context) -> ApiKeyCtx:
    """Require a pro or pro_plus API key for PIPELINE tools."""
    key_ctx = await _require_key(ctx)
    if key_ctx.entitlement not in (Entitlement.PRO, Entitlement.PRO_PLUS):
        raise ValueError(
            "PIPELINE tools require a Pro subscription. "
            "Free-tier keys can only call radar_check and profile_context."
        )
    return key_ctx


# ── CORE tools (free, deterministic) ─────────────────────────────────────────


@mcp.tool()
async def radar_check(
    url: str,
    title: str,
    profile: str,
    ctx: Context,
) -> dict:
    """Check whether a story has already been covered by this profile's pipeline.

    Scans the profile's history ledger (content/<profile>/history.jsonl) for a
    URL match (exact) or title match (≥70 % Jaccard token overlap). Returns
    {seen: false} when the content root is not mounted or the ledger doesn't
    exist yet — never raises for a missing file.

    This is a CORE tool: free, deterministic, no inference cost.

    Args:
        url:     Story URL (used for exact match; pass "" to skip URL check).
        title:   Story headline (used for token-overlap match).
        profile: Profile name (e.g. "acme"). Must be a safe path segment.

    Returns:
        {"seen": bool, "story_id": str | None}
    """
    await _require_key(ctx)
    _safe_segment(profile, "profile")

    paths = PathConfig.from_env()
    history_path = paths.content_root / profile / "history.jsonl"

    result = await asyncio.to_thread(_scan_history, history_path, url.strip(), title)
    return result


def _scan_history(history_path: Path, url: str, title: str) -> dict:
    """Sync scan of history.jsonl. Runs in executor thread."""
    if not history_path.exists():
        return {"seen": False, "story_id": None}

    url_lower = url.lower()
    title_tokens = set(re.findall(r"[a-z0-9]+", title.lower()))

    with open(history_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Exact URL match
            if url_lower and url_lower == (rec.get("url") or "").lower().strip():
                return {"seen": True, "story_id": rec.get("id")}

            # Title token-overlap (Jaccard ≥ 0.7)
            rec_tokens = set(re.findall(r"[a-z0-9]+", (rec.get("title") or "").lower()))
            if title_tokens and rec_tokens:
                union = title_tokens | rec_tokens
                overlap = len(title_tokens & rec_tokens) / len(union)
                if overlap >= 0.70:
                    return {"seen": True, "story_id": rec.get("id")}

    return {"seen": False, "story_id": None}


@mcp.tool()
async def profile_context(
    profile: str,
    filename: str = "icp.md",
    ctx: Context = None,
) -> str:
    """Read a profile knowledge file and return its contents.

    Useful for understanding who the profile targets, what their content pillars
    are, or what voice/tone guidelines apply. Returns an empty string if the
    file is not found rather than raising.

    This is a CORE tool: free, no inference cost.

    Args:
        profile:  Profile name (e.g. "acme"). Must be a safe path segment.
        filename: Knowledge file to read (default "icp.md"). Other common files:
                  "strategy.md", "voice.md", "brand.md", "content-pillars.md".

    Returns:
        File contents as a string (empty string if not found).
    """
    await _require_key(ctx)
    _safe_segment(profile, "profile")
    _safe_segment(filename, "filename")

    paths = PathConfig.from_env()

    def _read() -> str:
        try:
            path = resolve_knowledge_file(paths.profiles_root, profile, filename)
            return path.read_text(encoding="utf-8")
        except (ValueError, FileNotFoundError, OSError):
            return ""

    return await asyncio.to_thread(_read)


# ── PIPELINE tools (pro required, metered) ───────────────────────────────────


@mcp.tool()
async def draft_post(
    brief: str,
    profile: str,
    format: str = "linkedin-post",
    ctx: Context = None,
) -> str:
    """Produce a first-draft post from a brief using DeepSeek.

    Claude should always review the output before it is used. This is a fast
    first pass — NOT publish-ready. No publish tool is available via MCP; all
    publishing goes through the approved platform gate.

    Requires a Pro subscription. Cost is metered against your workspace budget.

    Args:
        brief:   What to write — topic, angle, key facts, audience, constraints.
        profile: Profile name for metering and context labelling.
        format:  "linkedin-post" (default), "linkedin-carousel", or "notes".

    Returns:
        Draft copy as a string.
    """
    key_ctx = await _require_pipeline(ctx)
    _safe_segment(profile, "profile")

    if not await check_budget(key_ctx.workspace_id, _state.pool):
        raise ValueError("Monthly cost cap reached for this workspace.")

    fmt_hint = {
        "linkedin-carousel": (
            "Draft a LinkedIn carousel as 8-12 slides. One idea per slide. "
            "Slide 1 is a hook ≤140 characters. No URLs in the body."
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

    text, pt, ct, cost = await _deepseek(
        messages, op="draft_post", max_tokens=1500, temperature=0.4
    )

    await meter_call(
        workspace_id=key_ctx.workspace_id,
        api_key_id=key_ctx.key_id,
        tool_name="draft_post",
        profile_name=profile,
        model=_DS_MODEL,
        prompt_tokens=pt,
        completion_tokens=ct,
        cost_usd=cost,
        pool=_state.pool,
    )

    return text


@mcp.tool()
async def draft_outreach(
    brief: str,
    profile: str,
    format: str = "linkedin-dm",
    ctx: Context = None,
) -> str:
    """Produce a first-draft outreach message from a brief using DeepSeek.

    Claude should always review the output before it is used or sent.
    Sending is never done here — this tool only drafts.

    Requires a Pro subscription. Cost is metered against your workspace budget.

    Args:
        brief:   Who you're reaching out to, why, and what you want to achieve.
        profile: Profile name for metering and context labelling.
        format:  "linkedin-dm" (default), "email", or "follow-up".

    Returns:
        Draft outreach message as a string.
    """
    key_ctx = await _require_pipeline(ctx)
    _safe_segment(profile, "profile")

    if not await check_budget(key_ctx.workspace_id, _state.pool):
        raise ValueError("Monthly cost cap reached for this workspace.")

    fmt_hint = {
        "email": "Draft a cold outreach email. Subject line + body. ≤150 words.",
        "follow-up": "Draft a short follow-up message (≤80 words). Reference prior contact.",
    }.get(format, "Draft a LinkedIn DM. ≤100 words. No pitching in the first message.")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a GTM outreach assistant. Write concise, personalised messages "
                "with a genuine hook. Never use buzzwords. Never make up facts about "
                "the recipient — use only what the brief provides."
            ),
        },
        {"role": "user", "content": f"{fmt_hint}\n\nBrief:\n{brief}"},
    ]

    text, pt, ct, cost = await _deepseek(
        messages, op="draft_outreach", max_tokens=600, temperature=0.4
    )

    await meter_call(
        workspace_id=key_ctx.workspace_id,
        api_key_id=key_ctx.key_id,
        tool_name="draft_outreach",
        profile_name=profile,
        model=_DS_MODEL,
        prompt_tokens=pt,
        completion_tokens=ct,
        cost_usd=cost,
        pool=_state.pool,
    )

    return text
