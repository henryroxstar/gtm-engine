"""Unit tests for the ElevenLabs TTS worker MCP (``agent.mcp.tts``).

Network-free: ``httpx.AsyncClient`` is monkeypatched. These pin the script→speech
stripping (stage cues / headings / rules / emphasis), the paragraph chunker, the
output-path mapping, the fail-closed no-key path, and the happy-path render
(per-chunk POST to ``/v1/text-to-speech/{voice}``, MP3 bytes concatenated to one
file).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.tts import server  # noqa: E402

VALID_VOICE = "EXAVITQu4vr4xnSDxMaL"


# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, calls: list, content: bytes, status: int) -> None:
        self._calls = calls
        self._content = content
        self._status = status

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResp(self._content, self._status)


def _patch_client(monkeypatch, calls, content=b"MP3", status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(calls, content, status)
    )


# ── script_to_speech ──────────────────────────────────────────────────────────


def test_script_to_speech_strips_directions_and_keeps_prose() -> None:
    md = (
        "# Podcast Script — ci-1\n"
        "# ~6 min monologue\n"
        "\n"
        "---\n"
        "\n"
        "[COLD OPEN — ~30 sec]\n"
        "\n"
        "This week I built **something** strange.\n"
        "It read its own `git log`.\n"
        "\n"
        "[WHAT I BUILT — ~2 min]\n"
        "\n"
        "The system has three stages.\n"
    )
    spoken = server.script_to_speech(md)
    assert "Podcast Script" not in spoken  # heading dropped
    assert "COLD OPEN" not in spoken  # bracketed cue dropped
    assert "WHAT I BUILT" not in spoken
    assert "---" not in spoken  # rule dropped
    assert "**" not in spoken and "`" not in spoken  # emphasis stripped
    assert "This week I built something strange." in spoken
    assert "It read its own git log." in spoken
    assert "The system has three stages." in spoken


def test_script_to_speech_empty_when_only_directions() -> None:
    assert server.script_to_speech("# Title\n\n---\n\n[CUE]\n") == ""


# ── chunk_text ────────────────────────────────────────────────────────────────


def test_chunk_text_respects_limit_and_has_no_empty_chunks() -> None:
    paras = "\n\n".join(f"Paragraph number {i} with some words." for i in range(40))
    chunks = server.chunk_text(paras, limit=120)
    assert chunks, "expected at least one chunk"
    assert all(c.strip() for c in chunks), "no empty chunks"
    assert all(len(c) <= 120 for c in chunks), "every chunk under the limit"
    # Round-trips every paragraph's content.
    joined = " ".join(chunks)
    assert "Paragraph number 0 " in joined and "Paragraph number 39 " in joined


def test_chunk_text_splits_oversized_paragraph_on_sentences() -> None:
    para = " ".join(f"Sentence {i} here." for i in range(60))  # one long paragraph
    chunks = server.chunk_text(para, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


# ── output path mapping ───────────────────────────────────────────────────────


def test_default_output_path_maps_podcast_script_suffix() -> None:
    p = Path("/x/content/example2/journey/assets/ci-202625-01.podcast-script.md")
    assert server._default_output_path(p).name == "ci-202625-01.podcast.mp3"


def test_default_output_path_falls_back_to_mp3_suffix() -> None:
    p = Path("/x/notes.md")
    assert server._default_output_path(p).name == "notes.mp3"


# ── render_podcast ────────────────────────────────────────────────────────────


def test_render_podcast_no_key_is_fail_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    script = tmp_path / "ci-1.podcast-script.md"
    script.write_text("Hello there.\n", encoding="utf-8")
    out = asyncio.run(server.render_podcast(str(script)))
    assert out.startswith("[tts-error]")
    assert "ELEVENLABS_API_KEY" in out


def test_render_podcast_missing_file(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    out = asyncio.run(server.render_podcast("/no/such/script.podcast-script.md"))
    assert out.startswith("[tts-error]")
    assert "not found" in out


def test_render_podcast_only_directions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    script = tmp_path / "ci-1.podcast-script.md"
    script.write_text("# Title\n\n[CUE]\n", encoding="utf-8")
    out = asyncio.run(server.render_podcast(str(script)))
    assert out.startswith("[tts-error]")
    assert "no spoken prose" in out


def test_render_podcast_happy_path_writes_one_mp3(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", VALID_VOICE)
    monkeypatch.delenv("GTM_PROFILE", raising=False)  # metering no-op in test
    calls: list = []
    _patch_client(monkeypatch, calls, content=b"AUDIO")

    script = tmp_path / "ci-202625-01.podcast-script.md"
    body = "\n\n".join(f"This is spoken paragraph number {i}." for i in range(30))
    script.write_text(f"# Heading\n\n[CUE]\n\n{body}\n", encoding="utf-8")

    out = asyncio.run(server.render_podcast(str(script)))

    assert out.startswith("audio:")
    expected = script.with_name("ci-202625-01.podcast.mp3")
    assert str(expected) in out
    assert expected.is_file()
    # One POST per chunk; bytes concatenated.
    assert len(calls) >= 1
    assert expected.read_bytes() == b"AUDIO" * len(calls)
    # Posted to the resolved voice with the configured model.
    assert VALID_VOICE in calls[0]["url"]
    assert calls[0]["json"]["model_id"] == server._TTS_MODEL


def test_render_podcast_http_error_surfaces(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    calls: list = []
    _patch_client(monkeypatch, calls, content=b"", status=429)
    script = tmp_path / "ci-1.podcast-script.md"
    script.write_text("Spoken line one. Spoken line two.\n", encoding="utf-8")
    out = asyncio.run(server.render_podcast(str(script)))
    assert out.startswith("[tts-error]")
    assert "429" in out
