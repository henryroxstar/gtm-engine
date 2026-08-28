"""Unit tests for the Gemini image worker MCP (``agent.mcp.gemini_image``).

Network-free: ``httpx.AsyncClient`` is monkeypatched so no real Gemini call is
made. These pin the verified request/response contract (endpoint, headers, body
shape, inline-image decode) so the fabricated ``/v1beta/interactions`` shape can
never silently come back, plus the ``[gemini-image-error] …`` degradation paths
and the cost-metering row.

Also pins the transport contract added 2026-08-27 after a live 503/503/ReadTimeout
run: which failures are retried (nothing was billed) and which deliberately are
NOT (the render may already have been billed), and that the read-timeout budget
scales with the requested resolution band.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.gemini_image import server  # noqa: E402

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Scripted transport. ``steps`` are replayed one per POST; the last step
    repeats once the script runs dry, so a single step models "always fails".
    A step is either an exception instance (raised) or a ``_FakeResp``."""

    def __init__(self, captured: dict, steps: list, calls: list, timeout) -> None:
        self._captured = captured
        self._steps = steps
        self._calls = calls
        self._timeout = timeout

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._captured.update(url=url, json=json, headers=headers, timeout=self._timeout)
        step = self._steps[min(len(self._calls), len(self._steps) - 1)]
        self._calls.append(url)
        if isinstance(step, BaseException):
            raise step
        return step


def _patch_steps(monkeypatch, captured, steps: list) -> list:
    """Install a scripted transport; returns the list that accrues one entry per POST."""
    calls: list = []
    monkeypatch.setattr(
        server.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(captured, steps, calls, k.get("timeout")),
    )
    return calls


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    _patch_steps(monkeypatch, captured, [_FakeResp(payload, status)])


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Record the retry ladder instead of actually sleeping through it."""
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(server.asyncio, "sleep", _fake_sleep)
    monkeypatch.delenv(server._READ_TIMEOUT_ENV, raising=False)
    return slept


def _image_payload(b64: str, mime: str = "image/png") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime, "data": b64}}]}}]
    }


# ── happy path: request contract + decode + metering ──────────────────────────


def test_generate_image_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    raw = b"\x89PNG\r\n\x1a\n pretend image"
    b64 = base64.b64encode(raw).decode()
    captured: dict = {}
    _patch_client(monkeypatch, captured, _image_payload(b64))

    result = asyncio.run(server._generate("a chart", str(out_dir / "x.png"), "2K", "16:9"))

    # Returns the written path; bytes are the decoded inline image.
    assert result.endswith("x.png")
    assert (out_dir / "x.png").read_bytes() == raw

    # Verified endpoint + auth header.
    assert captured["url"].endswith("/v1beta/models/gemini-3-pro-image-preview:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "test-key"

    # Verified body shape.
    body = captured["json"]
    assert body["contents"][0]["parts"][0]["text"] == "a chart"
    cfg = body["generationConfig"]
    assert cfg["responseModalities"] == ["TEXT", "IMAGE"]
    assert cfg["imageConfig"] == {"aspectRatio": "16:9", "imageSize": "2K"}
    assert "input" not in body and "response_format" not in body

    # Exact-USD cost row landed.
    costs = (tmp_path / "content" / "example2" / "costs.jsonl").read_text().splitlines()
    row = json.loads(costs[-1])
    assert row["tool"] == "gemini-image-worker"
    assert row["cost_usd"] == 0.134
    assert row["resolution"] == "2K"


def test_generate_image_corrects_extension_to_mime(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    b64 = base64.b64encode(b"jpegbytes").decode()
    _patch_client(monkeypatch, {}, _image_payload(b64, mime="image/jpeg"))

    result = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert result.endswith("x.jpg")  # extension realigned to image/jpeg


# ── degradation paths (no network) ────────────────────────────────────────────


def test_generate_image_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = asyncio.run(server._generate("p", str(tmp_path / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "GEMINI_API_KEY" in out


def test_generate_image_bad_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "5K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "resolution" in out


def test_generate_image_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out = asyncio.run(server._generate("p", str(tmp_path / "nope" / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "output directory" in out


def test_generate_image_http_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _patch_client(monkeypatch, {}, {}, status=500)
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "500" in out


def test_generate_image_no_image_in_response(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _patch_client(monkeypatch, {}, {"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]})
    out = asyncio.run(server._generate("p", str(out_dir / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]")
    assert "no image data" in out


# ── mcp_config wiring gate ────────────────────────────────────────────────────


def test_gemini_server_wired_when_key_present():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, gemini_api_key="g-key")
    servers = build_mcp_servers(cfg, "example2")
    assert "gemini_image" in servers
    assert servers["gemini_image"]["env"]["GEMINI_API_KEY"] == "g-key"
    assert servers["gemini_image"]["env"]["GTM_PROFILE"] == "example2"


def test_gemini_server_absent_without_key():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    cfg = dataclasses.replace(base, gemini_api_key=None)
    assert "gemini_image" not in build_mcp_servers(cfg, "example2")


# ── transport contract: retry the free failures, not the billed one ───────────


def _run(tmp_path, resolution="1K"):
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    return asyncio.run(server._generate("p", str(out_dir / "x.png"), resolution, "1:1"))


def test_503_is_retried_and_then_succeeds(tmp_path, monkeypatch, _no_real_sleep):
    """The exact 2026-08-27 failure: transient overload the operator had to retry by hand."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    b64 = base64.b64encode(b"png").decode()
    captured: dict = {}
    calls = _patch_steps(
        monkeypatch,
        captured,
        [_FakeResp({}, 503), _FakeResp({}, 503), _FakeResp(_image_payload(b64))],
    )

    result = _run(tmp_path)

    assert result.endswith("x.png"), result
    assert len(calls) == 3  # two rejections absorbed inside one tool call
    assert _no_real_sleep == [2.0, 4.0]  # exponential ladder


def test_503_exhausted_reports_retries_and_no_billing(tmp_path, monkeypatch, _no_real_sleep):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp({}, 503)])

    out = _run(tmp_path)

    assert out.startswith("[gemini-image-error]")
    assert "503" in out and "retries" in out
    assert "billed" in out  # tells the operator the cap is unaffected
    assert len(calls) == server._MAX_RETRIES + 1 == 4
    assert _no_real_sleep == [2.0, 4.0, 8.0]


def test_retry_after_header_is_honoured_as_a_floor(tmp_path, monkeypatch, _no_real_sleep):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b64 = base64.b64encode(b"png").decode()
    _patch_steps(
        monkeypatch,
        {},
        [_FakeResp({}, 429, headers={"Retry-After": "17"}), _FakeResp(_image_payload(b64))],
    )

    assert _run(tmp_path).endswith("x.png")
    assert _no_real_sleep == [17.0]  # server's advice beat the 2s ladder step


def test_retry_backoff_is_capped(tmp_path, monkeypatch, _no_real_sleep):
    """A wild Retry-After cannot stall the tool call the brain is awaiting."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b64 = base64.b64encode(b"png").decode()
    _patch_steps(
        monkeypatch,
        {},
        [_FakeResp({}, 503, headers={"Retry-After": "9999"}), _FakeResp(_image_payload(b64))],
    )

    assert _run(tmp_path).endswith("x.png")
    assert _no_real_sleep == [server._RETRY_MAX_S]


def test_auth_failure_is_not_retried(tmp_path, monkeypatch, _no_real_sleep):
    """401 is what "confirm the credentials" should have looked like — and retrying cannot fix it."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp({}, 401)])

    out = _run(tmp_path)

    assert out.startswith("[gemini-image-error]")
    assert "401" in out and "GEMINI_API_KEY" in out
    assert len(calls) == 1
    assert _no_real_sleep == []


def test_bad_request_is_not_retried(tmp_path, monkeypatch, _no_real_sleep):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp({}, 400)])

    out = _run(tmp_path)

    assert "400" in out
    assert len(calls) == 1 and _no_real_sleep == []


def test_read_timeout_is_not_retried_and_warns_about_billing(tmp_path, monkeypatch, _no_real_sleep):
    """A read timeout means the request was ACCEPTED — a silent retry could pay twice."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [httpx.ReadTimeout("too slow")])

    out = _run(tmp_path, resolution="2K")

    assert out.startswith("[gemini-image-error]")
    assert "240s" in out  # the 2K budget, named so the operator can size the override
    assert "billed" in out and server._READ_TIMEOUT_ENV in out
    assert len(calls) == 1, "retrying a possibly-billed render would double-charge"
    assert _no_real_sleep == []


def test_connect_error_is_retried(tmp_path, monkeypatch, _no_real_sleep):
    """Never reached the server: no generation, no charge — free to retry."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b64 = base64.b64encode(b"png").decode()
    calls = _patch_steps(
        monkeypatch, {}, [httpx.ConnectError("refused"), _FakeResp(_image_payload(b64))]
    )

    assert _run(tmp_path).endswith("x.png")
    assert len(calls) == 2 and _no_real_sleep == [2.0]


def test_connect_error_exhausted_states_nothing_was_billed(tmp_path, monkeypatch, _no_real_sleep):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _patch_steps(monkeypatch, {}, [httpx.ConnectError("refused")])

    out = _run(tmp_path)

    assert out.startswith("[gemini-image-error]")
    assert "ConnectError" in out and "billed" in out


def test_failed_generation_writes_no_cost_row(tmp_path, monkeypatch, _no_real_sleep):
    """Metering follows delivery: a 503 run must not spend against the §R2 cap."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    _patch_steps(monkeypatch, {}, [_FakeResp({}, 503)])

    assert _run(tmp_path).startswith("[gemini-image-error]")
    assert not (tmp_path / "content" / "example2" / "costs.jsonl").exists()


# ── read-timeout budget scales with the resolution band ──────────────────────


def test_read_timeout_scales_with_resolution(tmp_path, monkeypatch, _no_real_sleep):
    """2K — what every calling skill actually requests — gets more than the old flat 120s."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b64 = base64.b64encode(b"png").decode()

    seen = {}
    for band in ("1K", "2K", "4K"):
        captured: dict = {}
        _patch_steps(monkeypatch, captured, [_FakeResp(_image_payload(b64))])
        _run(tmp_path, resolution=band)
        seen[band] = captured["timeout"]

    assert seen["1K"].read < seen["2K"].read < seen["4K"].read
    assert seen["2K"].read >= 240.0, "2K renders run 60-90s; 120s left no headroom"
    # Connect stays tight so a real network fault still fails fast.
    assert seen["4K"].connect == server._CONNECT_TIMEOUT_S < seen["1K"].read


def test_read_timeout_env_override(tmp_path, monkeypatch, _no_real_sleep):
    """Redeploy-free break-glass for an operator watching renders time out on the VPS."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv(server._READ_TIMEOUT_ENV, "600")
    b64 = base64.b64encode(b"png").decode()
    captured: dict = {}
    _patch_steps(monkeypatch, captured, [_FakeResp(_image_payload(b64))])

    _run(tmp_path, resolution="1K")
    assert captured["timeout"].read == 600.0


@pytest.mark.parametrize("bad", ["0", "-5", "soon", ""])
def test_read_timeout_override_rejects_non_positive(bad, monkeypatch, _no_real_sleep):
    """httpx reads 0 as "wait forever" — a typo must fall back, not hang or crash."""
    monkeypatch.setenv(server._READ_TIMEOUT_ENV, bad)
    assert server._read_timeout_for("2K") == server._READ_TIMEOUT_BY_RESOLUTION["2K"]
