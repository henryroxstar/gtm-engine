"""Shared ElevenLabs voice helpers — STT (speech→text) and TTS (text→speech).

Operator-facing transport only. These run in the **cockpit process**, which
already holds ``ELEVENLABS_API_KEY``. The brain never calls these and never
sees the key — §R6 (every brain external call goes through an MCP tool) is
unaffected: this is the same egress the cockpit voice reply already uses,
kept in one module so the STT/TTS logic exists exactly once and cannot drift.

See ``docs/prds/2026-06-27-telegram-voice-bridge.md``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:  # type hints only — avoid importing Config at module load
    from agent.config import Config

_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
_STT_MODEL = "scribe_v1"
_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
_TTS_MODEL = "eleven_turbo_v2_5"
_VOICE_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75}
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{16,40}$")

#: Upper bound on text sent to TTS — caps cost/latency on a long reply. Far above
#: the old 400-char truncation (effectively "speak the whole thing"); callers log
#: when they clamp to it so the trim is never silent.
TTS_MAX_CHARS = 5000


class VoiceError(RuntimeError):
    """An ElevenLabs STT/TTS call failed (missing key, bad status, timeout, empty input)."""


def _require_key(cfg: Config) -> str:
    key = cfg.elevenlabs_api_key
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY not configured")
    return key


def _resolve_voice_id(cfg: Config, voice_id: str) -> str:
    """Return a validated ElevenLabs voice id, falling back to the configured default."""
    vid = (voice_id or "").strip()
    return vid if _VOICE_ID_RE.match(vid) else cfg.elevenlabs_voice_id


async def transcribe(
    cfg: Config,
    data: bytes,
    *,
    filename: str = "audio.ogg",
    content_type: str = "audio/ogg",
) -> str:
    """Transcribe an audio blob to text via ElevenLabs Speech-to-Text.

    Args:
        cfg: holds the (server-side) API key.
        data: raw audio bytes (Telegram voice notes are OGG/OPUS).

    Returns:
        The transcript (possibly empty if the model heard nothing).

    Raises:
        VoiceError: no key, empty audio, non-200, or timeout.
    """
    key = _require_key(cfg)
    if not data:
        raise VoiceError("empty audio")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                _STT_URL,
                headers={"xi-api-key": key},
                data={"model_id": _STT_MODEL},
                files={"file": (filename, data, content_type)},
            )
    except httpx.TimeoutException as exc:
        raise VoiceError("ElevenLabs STT timed out") from exc
    if r.status_code != 200:
        raise VoiceError(f"ElevenLabs STT returned {r.status_code}")
    try:
        text = (r.json() or {}).get("text", "")
    except Exception:  # noqa: BLE001 — any malformed body → empty transcript
        text = ""
    return (text or "").strip()


async def synthesize(
    cfg: Config,
    text: str,
    *,
    voice_id: str = "",
    stream: bool = True,
) -> bytes:
    """Synthesize ``text`` to speech via ElevenLabs TTS and return the audio bytes.

    With ``stream=True`` (default) the streaming endpoint is used to minimise
    time-to-first-byte; the bytes are assembled and returned as one complete clip
    (a Telegram voice message is a single uploaded file — it cannot be streamed
    *into*). Text is clamped to :data:`TTS_MAX_CHARS` defensively.

    Raises:
        VoiceError: no key, empty text, non-200, or timeout.
    """
    key = _require_key(cfg)
    snippet = (text or "").strip()[:TTS_MAX_CHARS]
    if not snippet:
        raise VoiceError("empty text")
    vid = _resolve_voice_id(cfg, voice_id)
    url = f"{_TTS_BASE}/{vid}/stream" if stream else f"{_TTS_BASE}/{vid}"
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    payload = {"text": snippet, "model_id": _TTS_MODEL, "voice_settings": _VOICE_SETTINGS}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if not stream:
                r = await client.post(url, headers=headers, json=payload)
                if r.status_code != 200:
                    raise VoiceError(f"ElevenLabs TTS returned {r.status_code}")
                return r.content
            chunks: list[bytes] = []
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    await resp.aread()  # drain so the status is final before we raise
                    raise VoiceError(f"ElevenLabs TTS returned {resp.status_code}")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        chunks.append(chunk)
            return b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise VoiceError("ElevenLabs TTS timed out") from exc
