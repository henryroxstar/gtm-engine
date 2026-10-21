"""Unit tests for the Higgsfield video worker MCP (``agent.mcp.higgsfield_video``).

Network-free: ``httpx.AsyncClient`` and the ``higgsfield_client`` SDK upload are
monkeypatched. These pin the verified REST contract (path-style model submit,
``Key`` auth scheme, ``/requests/{id}/status`` poll, nested ``video.url``),
the ``upload_image`` env-mapping, the status mapping, and the estimate cost row —
so the fabricated ``/api/v1/video-generations`` shape can never silently return.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import httpx  # noqa: E402

from agent.mcp.higgsfield_video import server  # noqa: E402

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


# ── fake httpx client ─────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, captured: dict, payload: dict, status: int) -> None:
        self._captured = captured
        self._payload = payload
        self._status = status

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002
        self._captured.update(method="POST", url=url, json=json, headers=headers)
        return _FakeResp(self._payload, self._status)

    async def get(self, url, headers=None):
        self._captured.update(method="GET", url=url, headers=headers)
        return _FakeResp(self._payload, self._status)


def _patch_client(monkeypatch, captured, payload, status=200) -> None:
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: _FakeClient(captured, payload, status)
    )


def _set_keys(monkeypatch) -> None:
    monkeypatch.setenv("HIGGSFIELD_API_KEY", "kk")
    monkeypatch.setenv("HIGGSFIELD_API_SECRET", "ss")


# ── auth scheme ───────────────────────────────────────────────────────────────


def test_auth_header_uses_key_scheme(monkeypatch):
    _set_keys(monkeypatch)
    assert server._auth_header() == "Key kk:ss"


# ── generate_video: submit contract + metering ────────────────────────────────


def test_generate_video_happy_path(tmp_path, monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc-123"})

    result = asyncio.run(server.generate_video("https://img/x.png", "slow dolly in", duration=5))
    assert result == "request_id:abc-123"

    # Path-style model submit + Key auth.
    assert captured["url"].endswith("/higgsfield-ai/dop/standard")
    assert captured["headers"]["Authorization"].startswith("Key ")

    # Body: image_url + prompt + duration, and NO model key in the body.
    body = captured["json"]
    assert body["image_url"] == "https://img/x.png"
    assert body["prompt"] == "slow dolly in"
    assert body["duration"] == 5
    assert "model" not in body

    # Estimate cost row (non-zero).
    costs = (tmp_path / "content" / "example2" / "costs.jsonl").read_text().splitlines()
    row = json.loads(costs[-1])
    assert row["tool"] == "higgsfield-video-worker"
    assert row["cost_usd"] == 0.35
    assert row["note"] == "credit-estimated-dop-standard"


def test_generate_video_no_keys(monkeypatch):
    monkeypatch.delenv("HIGGSFIELD_API_KEY", raising=False)
    monkeypatch.delenv("HIGGSFIELD_API_SECRET", raising=False)
    out = asyncio.run(server.generate_video("https://img/x.png", "p"))
    assert out.startswith("[higgsfield-error]")


def test_generate_video_requires_image_url(monkeypatch):
    _set_keys(monkeypatch)
    out = asyncio.run(server.generate_video("  ", "p"))
    assert out.startswith("[higgsfield-error]")
    assert "image_url" in out


def test_generate_video_always_sends_enhance_prompt_explicitly(monkeypatch):
    """Regression: Higgsfield's own API default for enhance_prompt is true
    (confirmed against its published OpenAPI schema, 2026-08-17). Omitting the key
    on a False call used to silently opt into server-side prompt rewriting, which
    would make the manifest's recorded prompt (Phase 18 R2) no longer the string
    that actually produced the asset. The key must always be present, explicitly."""
    _set_keys(monkeypatch)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc"})

    asyncio.run(server.generate_video("https://img/x.png", "p"))
    assert captured["json"]["enhance_prompt"] is False

    captured.clear()
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc"})
    asyncio.run(server.generate_video("https://img/x.png", "p", enhance_prompt=True))
    assert captured["json"]["enhance_prompt"] is True


def test_generate_video_motions_shape_and_cap(monkeypatch):
    """motions must round-trip as the documented {id, strength} object list
    (never the old, never-correct list[str] shape), capped at 2 entries."""
    _set_keys(monkeypatch)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc"})

    motions = [
        {"id": "11111111-1111-1111-1111-111111111111", "strength": 0.7},
        {"id": "22222222-2222-2222-2222-222222222222", "strength": 0.3},
        {"id": "33333333-3333-3333-3333-333333333333", "strength": 0.1},
    ]
    asyncio.run(server.generate_video("https://img/x.png", "p", motions=motions))
    assert captured["json"]["motions"] == motions[:2]


def test_generate_video_http_error(monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    _patch_client(monkeypatch, {}, {}, status=402)
    out = asyncio.run(server.generate_video("https://img/x.png", "p"))
    assert out.startswith("[higgsfield-error]")
    assert "402" in out


# ── check_video_status: poll contract + status mapping ────────────────────────


def test_check_status_completed_reads_nested_video_url(monkeypatch):
    _set_keys(monkeypatch)
    captured: dict = {}
    _patch_client(
        monkeypatch,
        captured,
        {"status": "completed", "video": {"url": "https://v/out.mp4"}},
    )
    out = asyncio.run(server.check_video_status("request_id:abc-123"))
    assert out == "status:completed video_url:https://v/out.mp4"
    assert captured["url"].endswith("/requests/abc-123/status")


def test_check_status_in_progress(monkeypatch):
    _set_keys(monkeypatch)
    _patch_client(monkeypatch, {}, {"status": "in_progress"})
    out = asyncio.run(server.check_video_status("abc"))
    assert out == "status:in_progress"


def test_check_status_nsfw_is_failure(monkeypatch):
    _set_keys(monkeypatch)
    _patch_client(monkeypatch, {}, {"status": "nsfw"})
    out = asyncio.run(server.check_video_status("abc"))
    assert out.startswith("status:failed")
    assert "nsfw" in out


# ── upload_image: env mapping + SDK call ──────────────────────────────────────


def test_upload_image_maps_creds_and_returns_url(tmp_path, monkeypatch):
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_SECRET", raising=False)
    img = tmp_path / "cover.png"
    img.write_bytes(b"img")

    import higgsfield_client

    seen: dict = {}

    async def _fake_upload(path):
        seen["path"] = path
        return "https://hf-storage/cover.png"

    monkeypatch.setattr(higgsfield_client, "upload_file_async", _fake_upload)

    out = asyncio.run(server.upload_image(str(img)))
    assert out == "https://hf-storage/cover.png"
    assert seen["path"] == str(img)
    # SDK env vars were mapped across from our credential names.
    assert server.os.environ["HF_API_KEY"] == "kk"
    assert server.os.environ["HF_API_SECRET"] == "ss"


def test_upload_image_missing_file(monkeypatch, tmp_path):
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    out = asyncio.run(server.upload_image(str(tmp_path / "nope.png")))
    assert out.startswith("[higgsfield-error]")
    assert "does not exist" in out


def test_upload_image_refuses_a_path_outside_the_content_root(tmp_path, monkeypatch):
    """The egress half of C1's containment: a readable file is not an uploadable one.

    Without this the tool took any absolute path, checked only that it was a file, and shipped
    the bytes to a third party — the same class the `gemini_image` worker closed on its own
    reference-image and output paths. The negative control matters as much as the refusal: the
    file EXISTS and is readable, so a passing assertion here cannot be explained by the path
    simply being wrong.
    """
    _set_keys(monkeypatch)
    root = tmp_path / "content"
    root.mkdir()
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))

    outsider = tmp_path / "elsewhere" / "someone-elses.png"
    outsider.parent.mkdir()
    outsider.write_bytes(b"img")
    assert outsider.is_file()  # negative control: refused for WHERE it is, not whether it is

    import higgsfield_client

    def _explode(path):  # pragma: no cover — reaching the SDK is the failure
        raise AssertionError(f"upload_file_async must not be reached for {path}")

    monkeypatch.setattr(higgsfield_client, "upload_file_async", _explode)

    out = asyncio.run(server.upload_image(str(outsider)))
    assert out.startswith("[higgsfield-error]")
    assert "refusing to upload outside the resolved content root" in out

    # ... and the same bytes, inside the root, still upload.
    inside = root / "cover.png"
    inside.write_bytes(b"img")

    async def _ok(path):
        return "https://hf-storage/cover.png"

    monkeypatch.setattr(higgsfield_client, "upload_file_async", _ok)
    assert asyncio.run(server.upload_image(str(inside))) == "https://hf-storage/cover.png"


# ── mcp_config wiring gate ────────────────────────────────────────────────────


def test_video_server_wired_only_with_both_creds():
    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO_ROOT)
    both = dataclasses.replace(base, higgsfield_api_key="k", higgsfield_api_secret="s")
    servers = build_mcp_servers(both, "example2")
    assert "higgsfield_video" in servers
    assert servers["higgsfield_video"]["env"]["GTM_PROFILE"] == "example2"

    half = dataclasses.replace(base, higgsfield_api_key="k", higgsfield_api_secret=None)
    assert "higgsfield_video" not in build_mcp_servers(half, "example2")


# ── C10: end frames, VERIFIED honoured on this endpoint 2026-09-07 ────────────


def test_an_end_frame_is_sent_as_the_field_name_that_was_measured(tmp_path, monkeypatch):
    """C10-T5. `end_image_url` is honoured; `end_image` is accepted and silently DROPPED.

    Both spellings returned HTTP 200 queued in the probe. Only one produced a clip that arrived on
    the end still — the other charged for a render that went nowhere. So the field name is pinned
    here by exact dict equality rather than by a substring check: a rename would pass a laxer
    assertion and fail silently in production, which is the precise failure this tier exists for.
    """
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc-123"})

    result = asyncio.run(
        server.generate_video(
            "https://img/start.png",
            "the cube shifts colour",
            duration=5,
            end_image_url="https://img/end.png",
        )
    )
    assert result == "request_id:abc-123"
    assert captured["json"] == {
        "image_url": "https://img/start.png",
        "prompt": "the cube shifts colour",
        "duration": 5,
        "enhance_prompt": False,
        "end_image_url": "https://img/end.png",
    }, "the keyframe request shape changed — re-probe before accepting a rename"

    costs = (tmp_path / "content" / "example2" / "costs.jsonl").read_text().splitlines()
    assert len(costs) == 1, "a keyframe render must meter exactly once, like any other"


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_absent_end_image_url_leaves_the_request_byte_identical(tmp_path, monkeypatch, empty):
    """The additive-change proof for every render that predates keyframes.

    Asserted as dict EQUALITY against the body the pre-C10 contract produced: a new optional
    parameter that alters the request for callers who never pass it is not an optional parameter.
    """
    _set_keys(monkeypatch)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILE", "example2")
    captured: dict = {}
    _patch_client(monkeypatch, captured, {"status": "queued", "request_id": "abc-123"})

    result = asyncio.run(
        server.generate_video("https://img/x.png", "slow dolly in", duration=5, end_image_url=empty)
    )
    assert result == "request_id:abc-123"
    assert captured["json"] == {
        "image_url": "https://img/x.png",
        "prompt": "slow dolly in",
        "duration": 5,
        "enhance_prompt": False,
    }, "the DoP Standard request body changed for a caller that passed no end frame"


def test_an_end_frame_still_needs_credentials_like_any_other_call(monkeypatch):
    """The keyframe path is not a way around the credential check."""
    monkeypatch.delenv("HIGGSFIELD_API_KEY", raising=False)
    monkeypatch.delenv("HIGGSFIELD_API_SECRET", raising=False)
    out = asyncio.run(server.generate_video("https://img/x.png", "p", end_image_url="https://e"))
    assert out.startswith("[higgsfield-error]") and "HIGGSFIELD_API_KEY" in out
