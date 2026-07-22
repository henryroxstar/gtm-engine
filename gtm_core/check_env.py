"""Environment self-check for the GTM engine.

Run it to see, at a glance, which capability tier your environment unlocks:

    uv run python -m gtm_core.check_env

The check is read-only and keyless — it inspects which env vars are present and
prints a per-tier pass/fail with what each tier unlocks. It NEVER prints a secret
value (only whether each var is set) and it makes no network calls.

Tiers mirror ``.env.example``:
  - TIER 0  required for ANY use (Claude Cowork skills, drafting, planning)
  - TIER 1  optional; unlocks the content pipeline + richer research
  - TIER 2  self-hosting / advanced only (Telegram, publishing, media)

Exit code is 0 when TIER 0 is satisfied (the system can run), 1 otherwise — so
it doubles as a CI / setup-skill gate.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class _Var:
    name: str
    unlocks: str


@dataclass(frozen=True)
class _Tier:
    key: str
    title: str
    required: bool  # True only for TIER 0 — the others are optional
    vars: tuple[_Var, ...]


TIERS: tuple[_Tier, ...] = (
    _Tier(
        key="0",
        title="REQUIRED — the brain",
        required=True,
        vars=(
            _Var("ANTHROPIC_API_KEY", "Claude Agent SDK orchestrator (nothing runs without it)"),
        ),
    ),
    _Tier(
        key="1",
        title="OPTIONAL — content pipeline + richer research (fallbacks exist)",
        required=False,
        vars=(
            _Var("DEEPSEEK_API_KEY", "cheap bulk worker drafts (else a Claude worker, costs more)"),
            _Var("FIRECRAWL_API_KEY", "structured web scraping (else keyless WebSearch/WebFetch)"),
        ),
    ),
    _Tier(
        key="2",
        title="SELF-HOSTING / ADVANCED — not needed for Claude Cowork",
        required=False,
        vars=(
            _Var("TELEGRAM_BOT_TOKEN", "Telegram cockpit bot (Cowork chat is the UI instead)"),
            _Var("TELEGRAM_ALLOWED_CHAT_ID", "cockpit allow-list (paired with the bot token)"),
            _Var("NEWS_DB_DSN", "read-only news feed (else keyless web-sweep fallback)"),
            _Var("HERMES_PUBLISH_URL", "LinkedIn publish webhook (Gate 2)"),
            _Var("HERMES_PUBLISH_SECRET", "dedicated publish bearer (paired with the URL)"),
            _Var("ELEVENLABS_API_KEY", "podcast / cockpit voice TTS"),
            _Var("HIGGSFIELD_API_KEY", "headless image + video generation"),
            _Var("GOOGLE_OAUTH_REFRESH_TOKEN", "Google Workspace MCP (calendar/gmail/drive)"),
        ),
    ),
)

_OK = "✓"
_MISSING = "·"


def _is_set(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def render(stream=sys.stdout) -> bool:
    """Print the tiered report. Return True iff TIER 0 is satisfied."""
    tier0_ok = True
    print("gtm-engine — environment check\n", file=stream)

    for tier in TIERS:
        set_count = sum(1 for v in tier.vars if _is_set(v.name))
        total = len(tier.vars)

        if tier.required:
            status = "READY" if set_count == total else "NOT READY"
        elif set_count == 0:
            status = "off (using fallbacks)"
        elif set_count == total:
            status = "fully on"
        else:
            status = "partial"

        print(f"TIER {tier.key} — {tier.title}", file=stream)
        print(f"        [{status}]  {set_count}/{total} set", file=stream)
        for v in tier.vars:
            mark = _OK if _is_set(v.name) else _MISSING
            print(f"        {mark} {v.name:<28} {v.unlocks}", file=stream)
        print("", file=stream)

        if tier.required and set_count != total:
            tier0_ok = False

    if tier0_ok:
        print("Ready: TIER 0 is satisfied — the GTM skills will run.", file=stream)
        print(
            "Set TIER 1 keys for the full content pipeline; TIER 2 is self-hosting only.",
            file=stream,
        )
    else:
        print("Not ready: set ANTHROPIC_API_KEY (TIER 0) before running anything.", file=stream)

    return tier0_ok


def main() -> int:
    return 0 if render() else 1


if __name__ == "__main__":
    raise SystemExit(main())
