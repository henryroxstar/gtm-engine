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

And, since 2026-09-06, the reference-image surface (C1 of the video-craft corpus PRD):
confinement of every reference path AND the output path to the resolved content root — the
security core, because a path parameter on a tool that ships bytes to a third party is an
exfiltration primitive — plus order-as-contract, the caps, per-reference metering, and the
one rung of the retry ladder that a multi-megabyte body re-derives.
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
def _content_root_is_tmp(tmp_path, monkeypatch):
    """The worker confines output_path (and every reference) to the resolved content root.

    Tests that need a NARROWER root than tmp_path set it themselves; this keeps every
    pre-existing test's `tmp_path / "out"` inside the boundary rather than silently turning
    each of them into a confinement refusal.
    """
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))


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
    out_dir = tmp_path / "content" / "out"  # inside the root the test just narrowed to
    out_dir.mkdir(parents=True)

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
    assert row["n_references"] == 0, "a text-only call records that it priced no references"


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
    monkeypatch.setenv("GTM_PROFILE", "example2")
    _patch_steps(monkeypatch, {}, [_FakeResp({}, 503)])

    out = _run(tmp_path)
    assert out.startswith("[gemini-image-error]") and "503" in out, (
        "the refusal must be the 503, not a confinement refusal that happens to look the same"
    )
    assert not (tmp_path / "example2" / "costs.jsonl").exists()


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


# ═════════════════════════════════════════════════════════════════════════════
# reference images (2026-09-06, C1) — confinement is the security core
# ═════════════════════════════════════════════════════════════════════════════

from pathlib import Path  # noqa: E402

from agent.mcp.gemini_image import references  # noqa: E402


def _ref(tmp_path, name: str, content: bytes = b"\x89PNG fake", sub: str = "refs") -> Path:
    """A reference file INSIDE the default test root (tmp_path)."""
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_bytes(content)
    return f


def _run_refs(tmp_path, monkeypatch, refs, *, captured=None, steps=None, resolution="1K"):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    b64 = base64.b64encode(b"png").decode()
    calls = _patch_steps(
        monkeypatch,
        captured if captured is not None else {},
        steps or [_FakeResp(_image_payload(b64))],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    result = asyncio.run(server._generate("p", str(out_dir / "x.png"), resolution, "1:1", refs))
    return result, calls


# ── confinement (C1-T1..T5) ───────────────────────────────────────────────────


def test_a_reference_inside_the_root_is_accepted_and_sent(tmp_path, monkeypatch):
    """Positive control for the whole confinement block: without it, a tool that accepts
    nothing passes every refusal below."""
    ref = _ref(tmp_path, "hero.png")
    captured: dict = {}
    result, calls = _run_refs(tmp_path, monkeypatch, [str(ref)], captured=captured)
    assert result.splitlines()[0].endswith("x.png"), result
    parts = captured["json"]["contents"][0]["parts"]
    assert "inlineData" in parts[0] and parts[0]["inlineData"]["mimeType"] == "image/png"
    assert len(calls) == 1


def test_a_reference_outside_the_root_is_refused_and_never_read(tmp_path, monkeypatch):
    """Exfiltration IS the success path here, so the refusal must happen before the read."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "out").mkdir()
    leak = tmp_path / "secret.png"
    leak.write_bytes(b"secret")

    read_paths: list[Path] = []
    real_read = Path.read_bytes

    def spy(self):
        read_paths.append(self.resolve())
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    out = asyncio.run(
        server._generate("p", str(tmp_path / "content" / "out" / "x.png"), "1K", "1:1", [str(leak)])
    )

    assert out.startswith("[gemini-image-error]") and "outside the resolved content root" in out
    assert leak.resolve() not in read_paths, "the bytes were read before the refusal"
    assert calls == [], "the request was sent despite the refusal"


def test_dot_dot_traversal_out_of_the_root_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content" / "out").mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(b"secret")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    sneaky = str(tmp_path / "content" / "out" / ".." / ".." / "secret.png")
    out = asyncio.run(
        server._generate("p", str(tmp_path / "content" / "out" / "x.png"), "1K", "1:1", [sneaky])
    )
    assert "outside the resolved content root" in out and calls == []


def test_a_symlink_inside_the_root_pointing_outside_is_refused(tmp_path, monkeypatch):
    """The check runs on the resolved target, not the link's name."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content" / "out").mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    link = tmp_path / "content" / "innocent.png"
    link.symlink_to(outside)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    out = asyncio.run(
        server._generate("p", str(tmp_path / "content" / "out" / "x.png"), "1K", "1:1", [str(link)])
    )
    assert "outside the resolved content root" in out and calls == []


def test_the_workspace_root_moves_the_boundary_between_tenants(tmp_path, monkeypatch):
    """Tenant isolation: bound to workspace A, a file in workspace B's tree is refused — and
    A's own file is accepted, so the refusal is the boundary and not a broken tool."""
    from gtm_core.paths import workspace_content_root

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "ws"))
    root_a = workspace_content_root("ws-a")
    root_b = workspace_content_root("ws-b")
    (root_a / "out").mkdir(parents=True)
    root_b.mkdir(parents=True)
    a_ref = root_a / "hero.png"
    a_ref.write_bytes(b"a")
    b_ref = root_b / "hero.png"
    b_ref.write_bytes(b"b")
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root_a))  # what backend/session.py injects
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    refused = asyncio.run(
        server._generate("p", str(root_a / "out" / "x.png"), "1K", "1:1", [str(b_ref)])
    )
    assert "outside the resolved content root" in refused

    _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    accepted = asyncio.run(
        server._generate("p", str(root_a / "out" / "x.png"), "1K", "1:1", [str(a_ref)])
    )
    assert accepted.splitlines()[0].endswith("x.png"), accepted


def test_output_path_outside_the_root_is_refused(tmp_path, monkeypatch):
    """The write side of the same boundary — the worker will no longer write wherever the parent
    directory happens to exist."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content").mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = _patch_steps(monkeypatch, {}, [_FakeResp(_image_payload("eA=="))])
    out = asyncio.run(server._generate("p", str(tmp_path / "elsewhere" / "x.png"), "1K", "1:1"))
    assert out.startswith("[gemini-image-error]") and "refusing to write outside" in out
    assert calls == []


# ── ordering and identity (C1-T6..T9) ────────────────────────────────────────


def test_references_arrive_in_list_order_ahead_of_the_text(tmp_path, monkeypatch):
    a = _ref(tmp_path, "a.png", b"AAA")
    b = _ref(tmp_path, "b.png", b"BBB")
    c = _ref(tmp_path, "c.png", b"CCC")
    captured: dict = {}
    result, _ = _run_refs(tmp_path, monkeypatch, [str(c), str(a), str(b)], captured=captured)

    parts = captured["json"]["contents"][0]["parts"]
    datas = [base64.b64decode(p["inlineData"]["data"]) for p in parts[:3]]
    assert datas == [b"CCC", b"AAA", b"BBB"], (
        "order drifted — position is what the prompt addresses"
    )
    assert parts[3] == {"text": "p"}, "the text part must come LAST"
    assert result.splitlines()[1] == "references sent (3, in order): 1=c.png 2=a.png 3=b.png"


def test_the_same_file_twice_is_sent_twice(tmp_path, monkeypatch):
    a = _ref(tmp_path, "a.png", b"AAA")
    captured: dict = {}
    result, _ = _run_refs(tmp_path, monkeypatch, [str(a), str(a)], captured=captured)
    parts = captured["json"]["contents"][0]["parts"]
    assert len(parts) == 3 and parts[0] == parts[1], "dedup would shift every later position"
    assert "references sent (2, in order): 1=a.png 2=a.png" in result


def test_no_references_is_byte_identical_to_the_pre_c1_request_and_return(tmp_path, monkeypatch):
    """The additive claim. Four shipped skills call this tool with no references; their request
    and their bare-path return must not move by a byte."""
    captured: dict = {}
    result, _ = _run_refs(tmp_path, monkeypatch, None, captured=captured)
    assert captured["json"] == {
        "contents": [{"parts": [{"text": "p"}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "1:1", "imageSize": "1K"},
        },
    }
    assert "\n" not in result and result.endswith("x.png"), "the bare-path return changed"


def test_an_empty_list_behaves_as_none(tmp_path, monkeypatch):
    c1: dict = {}
    r1, _ = _run_refs(tmp_path, monkeypatch, None, captured=c1)
    c2: dict = {}
    r2, _ = _run_refs(tmp_path, monkeypatch, [], captured=c2)
    assert c1["json"] == c2["json"] and r1 == r2


# ── limits (C1-T10..T13) ─────────────────────────────────────────────────────


def test_fourteen_references_are_accepted_and_fifteen_refused(tmp_path, monkeypatch):
    refs = [str(_ref(tmp_path, f"r{i:02d}.png", b"x")) for i in range(15)]
    ok, calls = _run_refs(tmp_path, monkeypatch, refs[:14])
    assert "references sent (14, in order)" in ok and len(calls) == 1

    refused, calls = _run_refs(tmp_path, monkeypatch, refs)
    assert refused.startswith("[gemini-image-error]") and "14" in refused and "15" in refused
    assert calls == [], "the cap must refuse before any network call"


def test_an_over_cap_file_is_refused_by_name_and_size_and_not_resized(tmp_path, monkeypatch):
    monkeypatch.setattr(references, "MAX_REFERENCE_BYTES", 10)
    big = _ref(tmp_path, "big.png", b"x" * 11)
    out, calls = _run_refs(tmp_path, monkeypatch, [str(big)])
    assert out.startswith("[gemini-image-error]")
    assert "big.png" in out and "11 bytes" in out and "10" in out
    assert big.stat().st_size == 11, "the file was altered — refuse, never resize"
    assert calls == []


def test_an_under_cap_set_over_the_encoded_total_is_refused_naming_the_total(tmp_path, monkeypatch):
    """The per-file check alone does not catch this."""
    monkeypatch.setattr(references, "MAX_ENCODED_TOTAL_BYTES", 20)
    a = _ref(tmp_path, "a.png", b"x" * 9)  # 12 bytes encoded each
    b = _ref(tmp_path, "b.png", b"x" * 9)
    ok, _ = _run_refs(tmp_path, monkeypatch, [str(a)])
    assert "references sent (1" in ok, "positive control: one file fits"
    out, calls = _run_refs(tmp_path, monkeypatch, [str(a), str(b)])
    assert out.startswith("[gemini-image-error]") and "24 bytes encoded" in out and "cap 20" in out
    assert calls == []


@pytest.mark.parametrize(
    "case",
    ["missing", "zero_byte", "wrong_suffix", "directory"],
)
def test_an_unusable_reference_is_refused_before_any_network_call(tmp_path, monkeypatch, case):
    if case == "missing":
        ref = tmp_path / "refs" / "nope.png"
        (tmp_path / "refs").mkdir()
    elif case == "zero_byte":
        ref = _ref(tmp_path, "empty.png", b"")
    elif case == "wrong_suffix":
        ref = _ref(tmp_path, "notes.txt", b"text")
    else:
        ref = tmp_path / "refs" / "adir.png"
        ref.mkdir(parents=True)
    out, calls = _run_refs(tmp_path, monkeypatch, [str(ref)])
    assert out.startswith("[gemini-image-error]"), out
    assert calls == [], f"{case}: the request went out anyway"


# ── metering — §R2 (C1-T14..T16) ─────────────────────────────────────────────


def _last_cost_row(tmp_path) -> dict:
    rows = (tmp_path / "example2" / "costs.jsonl").read_text().splitlines()
    return json.loads(rows[-1])


def test_a_reference_call_meters_strictly_more_and_records_the_count(tmp_path, monkeypatch):
    """The flat-fee bug, caught directly: a row that under-counts makes the cap arithmetic
    confidently wrong."""
    monkeypatch.setenv("GTM_PROFILE", "example2")
    _run_refs(tmp_path, monkeypatch, None)
    bare = _last_cost_row(tmp_path)
    refs = [str(_ref(tmp_path, f"r{i}.png", b"x")) for i in range(3)]
    _run_refs(tmp_path, monkeypatch, refs)
    with_refs = _last_cost_row(tmp_path)

    assert bare["n_references"] == 0 and with_refs["n_references"] == 3
    assert with_refs["cost_usd"] > bare["cost_usd"]
    assert with_refs["cost_usd"] == round(server._PRICE_1K_USD + 3 * server.PRICE_PER_REF_USD, 6)


def test_a_failed_reference_generation_writes_no_cost_row(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILE", "example2")
    ref = _ref(tmp_path, "a.png", b"x")
    out, _ = _run_refs(tmp_path, monkeypatch, [str(ref)], steps=[_FakeResp({}, 503)])
    assert "503" in out
    assert not (tmp_path / "example2" / "costs.jsonl").exists()


def test_the_cost_row_round_trips_through_the_ledger_reader(tmp_path, monkeypatch):
    """A new field that breaks the reader is a silent audit gap."""
    from agent.config import Config
    from gtm_core.ledgers import Ledgers

    monkeypatch.setenv("GTM_PROFILE", "example2")
    refs = [str(_ref(tmp_path, f"r{i}.png", b"x")) for i in range(2)]
    _run_refs(tmp_path, monkeypatch, refs)
    ledger = Ledgers(Config.from_env(), "example2")
    total = ledger.month_cost_total()
    assert total == pytest.approx(server._PRICE_1K_USD + 2 * server.PRICE_PER_REF_USD)
    assert _last_cost_row(tmp_path)["n_references"] == 2


# ── retry ladder — the double-billing frontier (C1-T17..T20) ─────────────────


def test_connect_error_with_references_is_still_retried(tmp_path, monkeypatch, _no_real_sleep):
    """Nothing was sent, so nothing was billed — the pre-write failure keeps its free retry."""
    ref = _ref(tmp_path, "a.png", b"x")
    b64 = base64.b64encode(b"png").decode()
    out, calls = _run_refs(
        tmp_path,
        monkeypatch,
        [str(ref)],
        steps=[httpx.ConnectError("refused"), _FakeResp(_image_payload(b64))],
    )
    assert out.splitlines()[0].endswith("x.png") and len(calls) == 2
    assert _no_real_sleep == [2.0]


@pytest.mark.parametrize("exc", [httpx.WriteError("broke"), httpx.WriteTimeout("slow")])
def test_a_write_failure_with_references_is_not_retried_and_names_the_billing_risk(
    tmp_path, monkeypatch, _no_real_sleep, exc
):
    """A multi-megabyte body that failed part-way through the write may have been accepted."""
    ref = _ref(tmp_path, "a.png", b"x")
    out, calls = _run_refs(tmp_path, monkeypatch, [str(ref)], steps=[exc])
    assert out.startswith("[gemini-image-error]")
    assert "billed" in out and type(exc).__name__ in out and "reference" in out
    assert len(calls) == 1 and _no_real_sleep == []


def test_a_write_error_without_references_keeps_its_pre_c1_behaviour(
    tmp_path, monkeypatch, _no_real_sleep
):
    """Positive control for the rule above: scoped to the ambiguous case, not a blanket change.

    Today a WriteError on a text-only body is not retried and carries NO billing caveat — a few
    hundred bytes that failed mid-write did not reach acceptance. That stays exactly as it is.
    """
    out, calls = _run_refs(tmp_path, monkeypatch, None, steps=[httpx.WriteError("broke")])
    assert out == "[gemini-image-error] Gemini request failed: WriteError."
    assert len(calls) == 1 and _no_real_sleep == []


def test_read_timeout_budget_scales_with_the_reference_count(tmp_path, monkeypatch):
    c0: dict = {}
    _run_refs(tmp_path, monkeypatch, None, captured=c0)
    refs = [str(_ref(tmp_path, f"r{i}.png", b"x")) for i in range(3)]
    c3: dict = {}
    _run_refs(tmp_path, monkeypatch, refs, captured=c3)
    assert c3["timeout"].read == c0["timeout"].read + 3 * server.READ_TIMEOUT_PER_REF_S
    assert c3["timeout"].connect == c0["timeout"].connect, "only the READ phase waits on uploads"


def test_read_timeout_with_references_is_still_not_retried(tmp_path, monkeypatch, _no_real_sleep):
    ref = _ref(tmp_path, "a.png", b"x")
    out, calls = _run_refs(tmp_path, monkeypatch, [str(ref)], steps=[httpx.ReadTimeout("slow")])
    assert "billed" in out and len(calls) == 1 and _no_real_sleep == []


# ── the manifest (C1-T25, and what it must NOT carry) ────────────────────────


def test_an_angle_alternative_sends_hero_then_camera_reference_in_that_order(tmp_path, monkeypatch):
    """The n=2 two-references law: identity first, where-the-lens-is second."""
    hero = _ref(tmp_path, "hero.png", b"HERO")
    cam = _ref(tmp_path, "camera-ref.png", b"CAM")
    captured: dict = {}
    result, _ = _run_refs(tmp_path, monkeypatch, [str(hero), str(cam)], captured=captured)
    parts = captured["json"]["contents"][0]["parts"]
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == b"HERO"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"CAM"
    assert result.splitlines()[1] == "references sent (2, in order): 1=hero.png 2=camera-ref.png"


def test_the_manifest_names_basenames_only(tmp_path, monkeypatch):
    """A full path would put the tenant's directory tree into a string the brain may echo."""
    ref = _ref(tmp_path, "hero.png", b"x", sub="deep/tenant/tree")
    result, _ = _run_refs(tmp_path, monkeypatch, [str(ref)])
    manifest = result.splitlines()[1]
    assert "/" not in manifest and "tenant" not in manifest, manifest


def test_the_tool_schema_exposes_reference_images_as_optional():
    """The brain reads the FastMCP schema, not _generate — the parameter must be there, optional."""
    import inspect

    sig = inspect.signature(server.generate_image)
    param = sig.parameters["reference_images"]
    assert param.default is None
