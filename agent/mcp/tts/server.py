"""ElevenLabs TTS worker MCP server (FastMCP, stdio) — podcast-audio render path.

A thin wrapper around the ElevenLabs text-to-speech REST API
(``api.elevenlabs.io``). One tool, the missing studio→audio piece:

  - ``render_podcast`` — read a podcast **script** markdown file (the artifact
    ``builder-studio`` already writes), strip the stage directions / headings /
    horizontal rules so only the *spoken* prose remains, chunk it under the
    per-request character limit, synthesize each chunk, concatenate the MP3
    bytes, and write a single ``.mp3`` next to the script. Returns the audio
    path. Blocking but bounded (a 6-min monologue is a handful of chunks).

Why an MCP worker (not the brain making the call): per CLAUDE.md §R6 all
external I/O goes through MCP tools — the brain never sees the ElevenLabs key.
This mirrors the ``higgsfield_video`` / ``gemini_image`` workers exactly: the
SDK spawns it as a stdio child (see :mod:`agent.mcp_config`); the API key is
passed via env, never on the cmdline.

Operator gate (UX contract): this renders audio on demand. The intended flow is
studio drafts ``podcast-script.md`` → the operator reads and approves the script
→ *then* the brain calls this tool. It is metered (TTS is paid), so the brain
must not render before the operator OKs the script. There is no autopublish here:
the MP3 is an internal artifact under ``content/<active>/`` — getting it anywhere
external still goes through Gate 2 like everything else.

Cost metering (NIST AU-12; budget integrity): ElevenLabs bills per character
(credit-based on subscription plans, with no per-call USD on the wire), so the
worker logs a documented USD estimate per render to ``costs.jsonl``
(``units``/``unit_kind=tts_char`` + a char-rate, env-overridable via
``ELEVENLABS_TTS_USD_PER_1K_CHARS``). Replace with the exact plan overage rate
once confirmed.

Model discipline: the TTS model defaults to ``eleven_multilingual_v2`` (stable,
high quality for long-form) and is env-overridable (``ELEVENLABS_TTS_MODEL``) —
it is a voice model, not a brain/LLM role in ``gtm_core/models.toml``.

Robustness contract: all failures return ``[tts-error] …`` strings — never raise
from a tool — so the brain gets a usable result and the MCP connection never drops.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# --- ElevenLabs wiring ------------------------------------------------------ #
_TTS_BASE_URL = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
# Stable, high-quality long-form model. Override via env if a newer one is preferred.
_TTS_MODEL = (
    os.getenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2").strip() or "eleven_multilingual_v2"
)
_DEFAULT_VOICE_ID = "Fahco4VZzobUeiPqni1S"  # mirrors agent.config.DEFAULT_VOICE_ID
# Per-request character ceiling. ElevenLabs accepts several thousand chars per
# call; we chunk well under that on paragraph boundaries to keep prosody clean
# and concatenation seams between natural pauses.
_CHUNK_CHARS = 2200
_HTTP_TIMEOUT_S = 90.0  # synthesis of one chunk; long-form chunks can be slow

# --- Per-render USD estimate (env-overridable) ------------------------------ #
# ElevenLabs bills in credits, not USD on the wire. Effective rate measured from
# the account's Creator plan (2026-06-24): 121k credits / $22/mo ≈ $0.18 per 1k
# credits, confirmed by the usage dashboard ($0.157 / 865 credits = $0.000181).
# The pinned TTS model `eleven_multilingual_v2` is 1 credit/char, so ≈ $0.18 per
# 1k chars. (A flash/turbo model is 0.5 credit/char → ≈ $0.09 per 1k chars — halve
# this if the model is switched.) Override via env if the plan changes.
_USD_PER_1K_CHARS = float(os.getenv("ELEVENLABS_TTS_USD_PER_1K_CHARS") or "0.18")
_CHAR_ESTIMATE_NOTE = "char-estimated-elevenlabs-creator"

mcp = FastMCP("tts-worker")


def _voice_id() -> str:
    """Resolve the voice id from env, validated; fall back to the default."""
    vid = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    if re.match(r"^[A-Za-z0-9]{16,40}$", vid):
        return vid
    return _DEFAULT_VOICE_ID


def script_to_speech(markdown: str) -> str:
    """Reduce a podcast-script markdown to the spoken prose only.

    Drops, line by line: markdown headings / leading-``#`` comment lines,
    horizontal rules (``---``), and stage-direction cue lines that are entirely
    bracketed (``[COLD OPEN — ~30 sec]``). Strips inline markdown emphasis
    (``*``/``_``/`` ` ``) that a voice would otherwise read literally. Collapses
    runs of blank lines so paragraphs stay separated by a single blank line.

    Pure + deterministic so it is unit-testable without any network.
    """
    kept: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if stripped.startswith("#"):  # heading / comment
            continue
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):  # horizontal rule
            continue
        if re.fullmatch(r"\[.*\]", stripped):  # entirely-bracketed stage cue
            continue
        # Strip inline emphasis markers a TTS engine would vocalize.
        line = re.sub(r"[*_`]+", "", line)
        kept.append(line.strip())

    # Collapse multiple blank lines into one, trim leading/trailing blanks.
    out: list[str] = []
    for line in kept:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return "\n".join(out).strip()


def chunk_text(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split prose into <= ``limit``-char chunks on paragraph then sentence breaks.

    Paragraphs (blank-line separated) are the primary seam; a single paragraph
    longer than ``limit`` is further split on sentence boundaries; a single
    sentence longer than ``limit`` is hard-wrapped. Never returns empty chunks.
    """
    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            flush()
            # Split the oversized paragraph on sentence boundaries.
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                if len(sent) > limit:
                    # Pathological single sentence — hard wrap.
                    for i in range(0, len(sent), limit):
                        chunks.append(sent[i : i + limit].strip())
                    continue
                if len(buf) + len(sent) + 1 > limit:
                    flush()
                buf = f"{buf} {sent}".strip()
            flush()
            continue
        if len(buf) + len(para) + 2 > limit:
            flush()
        buf = f"{buf}\n\n{para}".strip()
    flush()
    return [c for c in chunks if c]


def _meter(chars: int) -> None:
    """Log a char-estimated cost record for one render. Best-effort."""
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile or chars <= 0:
        return
    try:
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "tts-worker",
                "op": "render_podcast",
                "model": _TTS_MODEL,
                "units": chars,
                "unit_kind": "tts_char",
                "cost_usd": round(chars / 1000.0 * _USD_PER_1K_CHARS, 6),
                "note": _CHAR_ESTIMATE_NOTE,
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort
        return


def _default_output_path(script_path: Path) -> Path:
    """Audio sibling of the script: ``<id>.podcast-script.md`` → ``<id>.podcast.mp3``."""
    name = script_path.name
    if name.endswith(".podcast-script.md"):
        return script_path.with_name(name[: -len(".podcast-script.md")] + ".podcast.mp3")
    return script_path.with_suffix(".mp3")


async def _synthesize(client: httpx.AsyncClient, text: str, voice_id: str, key: str) -> bytes:
    """Synthesize one chunk; raises httpx errors (caller wraps them)."""
    resp = await client.post(
        f"{_TTS_BASE_URL}/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": _TTS_MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0},
        },
    )
    resp.raise_for_status()
    return resp.content


@mcp.tool()
async def render_podcast(
    script_path: str,
    output_path: str = "",
    voice_id: str = "",
) -> str:
    """Render a podcast-script markdown file to a single narrated MP3.

    OPERATOR-GATED: only call this after the operator has read and approved the
    podcast script. It is metered (TTS is paid). The output is an internal audio
    artifact under ``content/<active>/`` — publishing it anywhere external still
    goes through Gate 2.

    Args:
        script_path: Absolute path to the ``…podcast-script.md`` produced by
            ``builder-studio``. Stage directions, headings, and horizontal rules
            are stripped automatically; only the spoken prose is synthesized.
        output_path: Where to write the MP3. Defaults to a sibling of the script
            (``<id>.podcast.mp3``). The parent directory is created if needed.
        voice_id: ElevenLabs voice id override. Defaults to the configured
            profile voice (``ELEVENLABS_VOICE_ID`` / the platform default).

    Returns ``audio:<path> chars:<n> chunks:<k>`` on success, or
    ``[tts-error] …`` on any failure.
    """
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return "[tts-error] ELEVENLABS_API_KEY is not set — cannot render audio."

    src = Path(script_path).expanduser()
    if not src.is_file():
        return f"[tts-error] script file not found: {src}"

    try:
        markdown = src.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — surface, never raise
        return f"[tts-error] could not read script: {type(exc).__name__}."

    spoken = script_to_speech(markdown)
    if not spoken:
        return "[tts-error] script has no spoken prose after stripping directions/headings."

    chunks = chunk_text(spoken)
    if not chunks:
        return "[tts-error] script produced no synthesizable chunks."

    vid = (voice_id or "").strip()
    if not re.match(r"^[A-Za-z0-9]{16,40}$", vid):
        vid = _voice_id()

    out = Path(output_path).expanduser() if output_path.strip() else _default_output_path(src)

    audio_parts: list[bytes] = []
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            for chunk in chunks:
                audio_parts.append(await _synthesize(client, chunk, vid, key))
    except httpx.HTTPStatusError as exc:
        return f"[tts-error] ElevenLabs HTTP {exc.response.status_code} (rendered {len(audio_parts)}/{len(chunks)} chunks)."
    except httpx.HTTPError as exc:
        return f"[tts-error] ElevenLabs request failed: {type(exc).__name__}."

    # Raw MP3 frames concatenate for playback; ElevenLabs returns frame data, not
    # a container that would need remuxing. Joining bytes is the pragmatic path.
    audio = b"".join(audio_parts)
    if not audio:
        return "[tts-error] ElevenLabs returned no audio bytes."

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
    except Exception as exc:  # noqa: BLE001 — surface, never raise
        return f"[tts-error] could not write audio: {type(exc).__name__}."

    _meter(len(spoken))
    return f"audio:{out} chars:{len(spoken)} chunks:{len(chunks)}"
