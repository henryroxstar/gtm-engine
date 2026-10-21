"""MM11 (SECURITY-SELF-ASSESSMENT §6 item 22): a tenant run leaves no transcript behind.

The Claude CLI writes every session to ``<config dir>/projects/<cwd>/<session>.jsonl`` — the prompts, the
tool results, the pages it read. On the backend that directory sits OUTSIDE every workspace tree, in the
container's home, so it is pooled across tenants and survives account erasure (which removes the
workspace tree and nothing else). ``--no-session-persistence`` makes the CLI write nothing.

Two things are pinned, because either alone is the wrong evidence:

* the OPTIONS: a workspace-scoped config builds options that carry the flag, a single-tenant config does
  not (the operator's own transcripts are theirs, and the cockpit resumes sessions);
* the BEHAVIOUR: those exact options, driven through the bundled CLI against a local stand-in for the
  Messages API, answer BOTH turns of a warm multi-turn session and write no transcript — and the same
  session without the flag does write one, so the empty directory is not just a CLI that writes nothing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")

from agent.config import Config  # noqa: E402
from agent.session import build_agent_options  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WS = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _bundled_cli() -> Path | None:
    import claude_agent_sdk

    path = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return path if path.exists() else None


def _tenant_cfg(tmp_path, monkeypatch) -> Config:
    from backend.session import _workspace_scoped_config

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    return _workspace_scoped_config(Config.from_env(repo_root=REPO), WS, REPO, credentials={})


# ── the options ───────────────────────────────────────────────────────────────


def _confined_callback():
    from agent.path_confinement import Confinement
    from agent.permissions import make_headless_can_use_tool

    return make_headless_can_use_tool(confine=Confinement.for_workspace(WS, REPO))


def test_a_tenant_config_with_no_explicit_callback_is_refused_not_left_unconfined(
    tmp_path, monkeypatch
):
    """The default callback carries no confinement. A tenant config that reached it would run with every
    path check silently off — so it refuses to build."""
    with pytest.raises(ValueError, match="confine"):
        build_agent_options(_tenant_cfg(tmp_path, monkeypatch), "acme")


def test_a_tenant_config_builds_options_that_persist_no_session(tmp_path, monkeypatch):
    options = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    assert "no-session-persistence" in (options.extra_args or {})


def test_a_single_tenant_config_keeps_its_sessions(tmp_path):
    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    options = build_agent_options(cfg, "acme")
    assert "no-session-persistence" not in (options.extra_args or {})


# ── the behaviour, through the real CLI ───────────────────────────────────────


class _Messages(BaseHTTPRequestHandler):
    """A stand-in for ``POST /v1/messages`` that streams one short assistant turn."""

    def log_message(self, *_a):
        pass

    def _send(self, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path.startswith("/v1/messages/count_tokens"):
            return self._send({"input_tokens": 5})
        if not self.path.startswith("/v1/messages"):
            return self._send({})
        try:
            streaming = json.loads(raw).get("stream")
        except ValueError:
            streaming = True
        msg = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "stub",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if not streaming:
            return self._send(
                {**msg, "content": [{"type": "text", "text": "pong"}], "stop_reason": "end_turn"}
            )

        def ev(kind, payload):
            return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"

        self._send(
            "".join(
                [
                    ev("message_start", {"type": "message_start", "message": msg}),
                    ev(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                    ),
                    ev(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "pong"},
                        },
                    ),
                    ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ev(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                            "usage": {"output_tokens": 2},
                        },
                    ),
                    ev("message_stop", {"type": "message_stop"}),
                ]
            ).encode(),
            "text/event-stream",
        )

    do_GET = do_POST


async def _two_turns(options, port: int, config_dir: Path) -> tuple[list[str], list[str]]:
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

    options.env = {
        **(options.env or {}),
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-stub",
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    options.cwd = tempfile.mkdtemp(
        prefix="cwd_"
    )  # the repo checkout would make the CLI index a project
    options.max_budget_usd = None
    replies: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        for turn in range(2):
            await client.query(f"say pong ({turn})")
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    replies += [b.text for b in message.content if isinstance(b, TextBlock)]
                if isinstance(message, ResultMessage) and message.is_error:
                    replies.append("<error>")
    return replies, [str(p.relative_to(config_dir)) for p in config_dir.rglob("*.jsonl")]


@pytest.fixture(scope="module")
def stub_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Messages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_a_warm_multi_turn_tenant_session_answers_every_turn_and_writes_no_transcript(
    tmp_path, monkeypatch, stub_api
):
    options = build_agent_options(
        _tenant_cfg(tmp_path, monkeypatch), "acme", can_use_tool=_confined_callback()
    )
    replies, transcripts = asyncio.run(
        asyncio.wait_for(_two_turns(options, stub_api, tmp_path / "cfg"), 120)
    )
    assert replies == ["pong", "pong"]  # the flag does not break a second turn
    assert transcripts == []


@pytest.mark.skipif(_bundled_cli() is None, reason="the SDK's bundled CLI is not installed")
def test_the_same_session_without_the_flag_does_write_a_transcript(tmp_path, stub_api):
    """The control for the test above: an empty directory proves nothing unless the CLI would have
    filled it."""
    cfg = dataclasses.replace(Config.from_env(repo_root=REPO), content_root=tmp_path / "content")
    options = build_agent_options(cfg, "acme")
    replies, transcripts = asyncio.run(
        asyncio.wait_for(_two_turns(options, stub_api, tmp_path / "cfg"), 120)
    )
    assert replies == ["pong", "pong"]
    assert len(transcripts) == 1
