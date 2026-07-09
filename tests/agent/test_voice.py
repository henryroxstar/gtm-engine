"""Unit tests for the shared ElevenLabs voice helpers (agent.voice).

Network-free: ``httpx.AsyncClient`` is replaced with a fake that records the
request and returns a canned response. Drives the async helpers via
``asyncio.run`` (the suite has no asyncio auto-mode). Covers STT + streaming TTS
happy paths and every VoiceError branch (missing key, empty input, non-200,
timeout), plus voice-id fallback.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent import voice  # noqa: E402

VALID_VOICE_ID = "Abcd1234Efgh5678Ijkl"  # 20 alnum chars → passes the id regex


def _cfg(key: str | None = "xi-key", voice_id: str = "DefaultVoiceId0000000"):
    return SimpleNamespace(elevenlabs_api_key=key, elevenlabs_voice_id=voice_id)


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakePostResp:
    def __init__(self, *, content=b"", status=200, payload=None):
        self.content = content
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeStreamResp:
    """Async context manager mimicking httpx's streaming response."""

    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def aread(self):
        return b"".join(self._chunks)

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeClient:
    def __init__(
        self,
        captured,
        *,
        post_resp=None,
        stream_chunks=None,
        stream_status=200,
        raise_timeout=False,
    ):
        self._captured = captured
        self._post_resp = post_resp
        self._stream_chunks = stream_chunks or []
        self._stream_status = stream_status
        self._raise_timeout = raise_timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, headers=None, json=None, data=None, files=None):  # noqa: A002
        if self._raise_timeout:
            raise httpx.TimeoutException("slow")
        self._captured.update(url=url, headers=headers, json=json, data=data, files=files)
        return self._post_resp

    def stream(self, method, url, headers=None, json=None):  # noqa: A002 — httpx signature
        if self._raise_timeout:
            raise httpx.TimeoutException("slow")
        self._captured.update(method=method, url=url, headers=headers, json=json)
        return _FakeStreamResp(self._stream_chunks, self._stream_status)


def _patch(monkeypatch, **kw):
    captured: dict = {}
    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, **kw))
    return captured


# ── transcribe (STT) ────────────────────────────────────────────────────────


def test_transcribe_happy_path(monkeypatch):
    cap = _patch(monkeypatch, post_resp=_FakePostResp(payload={"text": "  hello world  "}))
    out = asyncio.run(
        voice.transcribe(_cfg(), b"audio-bytes", filename="v.ogg", content_type="audio/ogg")
    )
    assert out == "hello world"
    assert cap["url"].endswith("/speech-to-text")
    assert cap["files"]["file"][0] == "v.ogg"


def test_transcribe_no_key_raises(monkeypatch):
    _patch(monkeypatch, post_resp=_FakePostResp())
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.transcribe(_cfg(key=None), b"audio"))


def test_transcribe_empty_audio_raises(monkeypatch):
    _patch(monkeypatch, post_resp=_FakePostResp())
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.transcribe(_cfg(), b""))


def test_transcribe_non_200_raises(monkeypatch):
    _patch(monkeypatch, post_resp=_FakePostResp(status=502))
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.transcribe(_cfg(), b"audio"))


def test_transcribe_timeout_raises(monkeypatch):
    _patch(monkeypatch, raise_timeout=True)
    with pytest.raises(voice.VoiceError, match="timed out"):
        asyncio.run(voice.transcribe(_cfg(), b"audio"))


# ── synthesize (TTS, streaming) ──────────────────────────────────────────────


def test_synthesize_streams_and_assembles_one_clip(monkeypatch):
    cap = _patch(monkeypatch, stream_chunks=[b"aa", b"bb", b"", b"cc"])
    out = asyncio.run(voice.synthesize(_cfg(), "say this", voice_id=VALID_VOICE_ID))
    assert out == b"aabbcc"
    assert cap["method"] == "POST"
    assert cap["url"].endswith(f"/text-to-speech/{VALID_VOICE_ID}/stream")
    assert cap["json"]["text"] == "say this"


def test_synthesize_non_streaming_uses_plain_endpoint(monkeypatch):
    cap = _patch(monkeypatch, post_resp=_FakePostResp(content=b"mp3"))
    out = asyncio.run(voice.synthesize(_cfg(), "hi", voice_id=VALID_VOICE_ID, stream=False))
    assert out == b"mp3"
    assert cap["url"].endswith(f"/text-to-speech/{VALID_VOICE_ID}")
    assert not cap["url"].endswith("/stream")


def test_synthesize_invalid_voice_id_falls_back_to_default(monkeypatch):
    cap = _patch(monkeypatch, stream_chunks=[b"x"])
    asyncio.run(voice.synthesize(_cfg(voice_id="DefaultVoiceId0000000"), "hi", voice_id="../evil"))
    assert cap["url"].endswith("/text-to-speech/DefaultVoiceId0000000/stream")


def test_synthesize_clamps_to_max_chars(monkeypatch):
    cap = _patch(monkeypatch, stream_chunks=[b"x"])
    long_text = "a" * (voice.TTS_MAX_CHARS + 500)
    asyncio.run(voice.synthesize(_cfg(), long_text, voice_id=VALID_VOICE_ID))
    assert len(cap["json"]["text"]) == voice.TTS_MAX_CHARS


def test_synthesize_no_key_raises(monkeypatch):
    _patch(monkeypatch, stream_chunks=[b"x"])
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.synthesize(_cfg(key=None), "hi"))


def test_synthesize_empty_text_raises(monkeypatch):
    _patch(monkeypatch, stream_chunks=[b"x"])
    with pytest.raises(voice.VoiceError):
        asyncio.run(voice.synthesize(_cfg(), "   "))


def test_synthesize_non_200_raises(monkeypatch):
    _patch(monkeypatch, stream_chunks=[b"x"], stream_status=429)
    with pytest.raises(voice.VoiceError, match="429"):
        asyncio.run(voice.synthesize(_cfg(), "hi", voice_id=VALID_VOICE_ID))
