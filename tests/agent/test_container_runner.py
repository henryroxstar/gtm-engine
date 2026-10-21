"""Tests for Pattern D Ephemeral Container Per Run (T8, T9, T10)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gtm_core.confine import ConfinementError

REPO = Path(__file__).resolve().parents[2]
WS_A = "aaaaaaaa-1111-2222-3333-444444444444"
WS_B = "bbbbbbbb-1111-2222-3333-444444444444"


def test_t8_container_launch_plan_flags_single_tenant_mount_and_secret_scrubbing(
    tmp_path, monkeypatch
):
    """T8 (R8): ContainerLaunchPlan derives deterministic UID 20000..29999, writes 0700 wrapper script
    invoking docker run --rm -i --init --cap-drop=ALL --security-opt=no-new-privileges --read-only
    --workdir /app with only <ws_root> mounted, and writes scrubbed secrets to 0600 .launch/env-*.list.
    """
    from agent.container_runner import prepare_container_launch
    from agent.tenant_identity import POOL_MAX, POOL_MIN, identity_for

    monkeypatch.setenv("GTM_RUN_ISOLATION", "container")
    monkeypatch.setenv("GTM_DOCKER_HOST", "tcp://docker-proxy:2375")

    ws_root = tmp_path / "workspaces"
    run_dir = ws_root / WS_A / ".run"
    raw_env = {
        "ANTHROPIC_API_KEY": "sk-ant-tenant-key",
        "DATABASE_URL": "postgresql://postgres:supersecret@db:5432/gtm",
        "REDIS_URL": "redis://:secret@redis:6379/0",
        "API_KEY_PEPPER": "top-secret-pepper",
        "REVENUECAT_SECRET_KEY": "rc-secret",
        "SUPABASE_SERVICE_ROLE_KEY": "sb-secret",
        "GTM_INTERNAL_BACKEND_TOKEN": "internal-token",
        "GTM_ACTIVE_PROFILE": "acme",
    }

    plan = prepare_container_launch(
        WS_A,
        ws_root,
        run_dir=run_dir,
        env=raw_env,
        real_cli="/usr/local/bin/claude",
    )
    assert plan is not None
    expected_uid = identity_for(WS_A, ws_root)
    assert POOL_MIN <= expected_uid <= POOL_MAX
    assert plan.uid == expected_uid

    # Wrapper script and env file live in <workspaces_root>/.launch (NOT inside <ws_root>/<ws_a>)
    assert plan.cli_path.parent == (ws_root / ".launch").resolve()
    assert plan.env_file.parent == (ws_root / ".launch").resolve()
    assert plan.cli_path.stat().st_mode & 0o777 == 0o700
    assert plan.env_file.stat().st_mode & 0o777 == 0o600

    wrapper = plan.cli_path.read_text(encoding="utf-8")
    for flag in (
        "tcp://docker-proxy:2375",
        "run",
        "--rm",
        "-i",
        "--init",
        f"--user {expected_uid}:{expected_uid}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs /tmp:rw,noexec,nosuid,size=256m",
        "--memory 1024m",
        "--cpus 1.0",
        "--pids-limit 256",
        "--workdir /app",
        f"--env-file {plan.env_file}",
    ):
        assert flag in wrapper, f"Missing {flag!r} in container wrapper script:\n{wrapper}"

    # Single-tenant mount check: mounts ONLY WS_A's directory, never parent workspaces root or WS_B
    ws_a_tree = (ws_root / WS_A).resolve()
    assert f"-v {ws_a_tree}:{ws_a_tree}:rw" in wrapper
    assert f"-v {ws_root.resolve()}:{ws_root.resolve()}" not in wrapper
    assert WS_B not in wrapper

    # No secret values appear in wrapper script or docker argv
    for secret in ("sk-ant-tenant-key", "supersecret", "top-secret-pepper", "sb-secret"):
        assert secret not in wrapper
        assert all(secret not in arg for arg in plan.docker_argv)

    # Env file contains scrubbed tenant env (ANTHROPIC_API_KEY allowed, control-plane secrets stripped)
    env_content = plan.env_file.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-ant-tenant-key" in env_content
    assert "GTM_ACTIVE_PROFILE=acme" in env_content
    for forbidden in (
        "DATABASE_URL",
        "supersecret",
        "REDIS_URL",
        "API_KEY_PEPPER",
        "top-secret-pepper",
        "REVENUECAT_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GTM_INTERNAL_BACKEND_TOKEN",
    ):
        assert forbidden not in env_content

    plan.discard()
    assert not plan.cli_path.exists()
    assert not plan.env_file.exists()


def test_t9_container_launch_refuses_raw_docker_socket_and_unconfined_run_dir_and_cleans_up(
    tmp_path, monkeypatch
):
    """T9 (R8):
    (a) Refuses unset GTM_DOCKER_HOST or raw unix:///var/run/docker.sock unless GTM_ALLOW_RAW_DOCKER_SOCKET=true,
        with error naming GTM_DOCKER_HOST and GTM_ALLOW_RAW_DOCKER_SOCKET=true.
    (b) Refuses run_dir outside workspace tree (../ or symlink escape) with ConfinementError.
    (c) plan.discard() unlinks .launch/ files and .run/ private files.
    """
    from agent.container_runner import prepare_container_launch

    ws_root = tmp_path / "workspaces"
    run_dir = ws_root / WS_A / ".run"
    monkeypatch.setenv("GTM_RUN_ISOLATION", "container")

    # (a1) Unset GTM_DOCKER_HOST -> refused with actionable message
    monkeypatch.delenv("GTM_DOCKER_HOST", raising=False)
    monkeypatch.delenv("GTM_ALLOW_RAW_DOCKER_SOCKET", raising=False)
    with pytest.raises(RuntimeError) as exc_unset:
        prepare_container_launch(WS_A, ws_root, run_dir=run_dir, env={})
    assert "GTM_DOCKER_HOST" in str(exc_unset.value)
    assert "GTM_ALLOW_RAW_DOCKER_SOCKET=true" in str(exc_unset.value)

    # (a2) Raw /var/run/docker.sock -> refused unless GTM_ALLOW_RAW_DOCKER_SOCKET=true
    monkeypatch.setenv("GTM_DOCKER_HOST", "unix:///var/run/docker.sock")
    with pytest.raises(RuntimeError) as exc_raw:
        prepare_container_launch(WS_A, ws_root, run_dir=run_dir, env={})
    assert "GTM_DOCKER_HOST" in str(exc_raw.value)
    assert "GTM_ALLOW_RAW_DOCKER_SOCKET=true" in str(exc_raw.value)

    # (b) Path traversal / escape on run_dir -> ConfinementError
    monkeypatch.setenv("GTM_DOCKER_HOST", "tcp://docker-proxy:2375")
    escaped_dir = ws_root / WS_B / ".run"
    with pytest.raises(ConfinementError):
        prepare_container_launch(WS_A, ws_root, run_dir=escaped_dir, env={})

    # (c) A private file travels in the 0600 env-file (never as a host file the run's uid cannot open) and
    # discard() removes it with the env-file
    plan = prepare_container_launch(WS_A, ws_root, run_dir=run_dir, env={})
    assert plan is not None
    secret_mcp = plan.private_file("mcp.json", '{"secret": "xyz"}')
    assert secret_mcp.startswith(
        "/tmp/gtm-private/"
    )  # a path inside the container, not on the host
    assert not Path(secret_mcp).exists()
    assert plan.env_file.stat().st_mode & 0o777 == 0o600
    assert "xyz" not in plan.env_file.read_text(encoding="utf-8")  # encoded, not plaintext

    plan.cleanup()
    assert not plan.env_file.exists()
    assert not plan.env_file.exists()
    assert not plan.cli_path.exists()


def test_t10_agent_session_wires_container_launch_plan_and_cleans_up(tmp_path, monkeypatch):
    """T10 (R9): build_agent_options wires ContainerLaunchPlan when GTM_RUN_ISOLATION=container,
    keeps secrets off SubprocessCLITransport._build_command(), and stream_brain_messages / AgentSession.close()
    cleans up the plan's private files.
    """
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from agent.config import Config
    from agent.session import AgentSession, build_agent_options
    from backend.session import _workspace_scoped_config
    from tests.agent.test_tenant_transcripts import _confined_callback

    ws_root = tmp_path / "workspaces"
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(ws_root))
    monkeypatch.setenv("GTM_RUN_ISOLATION", "container")
    monkeypatch.setenv("GTM_DOCKER_HOST", "tcp://docker-proxy:2375")

    base_cfg = Config.from_env(repo_root=REPO)
    cfg = _workspace_scoped_config(
        base_cfg,
        WS_A,
        REPO,
        credentials={"firecrawl_api_key": "SECRET-CONTAINER-BYOK-KEY"},
    )
    (Path(cfg.profiles_root) / "acme").mkdir(parents=True, exist_ok=True)
    (Path(cfg.profiles_root) / "acme" / "PROFILE.md").write_text("# Acme\n", encoding="utf-8")

    options = build_agent_options(cfg, "acme", can_use_tool=_confined_callback())
    transport = SubprocessCLITransport(prompt="hi", options=options)
    transport._cli_path = str(options.cli_path)
    cmd = transport._build_command()
    flat = " ".join(cmd)

    # cli_path is the .launch/run-*.sh wrapper script, and SECRET-CONTAINER-BYOK-KEY is NOT on argv!
    assert Path(cmd[0]).name.startswith("run-")
    assert "SECRET-CONTAINER-BYOK-KEY" not in flat

    plan = getattr(options, "_tenant_plan", None)
    assert plan is not None
    assert plan.cli_path.exists()
    assert plan.env_file.exists()

    # Closing an AgentSession holding this options object discards all private files
    sess = AgentSession(cfg, "acme", can_use_tool=_confined_callback())
    sess._tenant_plan = plan
    import asyncio

    asyncio.run(sess.close())
    assert not plan.cli_path.exists()
    assert not plan.env_file.exists()


def test_re_exports_and_hardening_edges(tmp_path, monkeypatch):
    """Verify PRD R8 and R10 re-exports, connect failure cleanup, and socket/secret guards."""
    from agent.tenant_identity import (  # noqa: F401
        ContainerLaunchPlan as ReexportedPlan,
    )
    from agent.tenant_identity import (
        prepare_container_launch as reexported_prepare,
    )
    from backend.services.runs.lifecycle import (  # noqa: F401
        cancel_workspace_runs as reexported_cancel,
    )

    # Raw socket refusal for /run/docker.sock
    monkeypatch.setenv("GTM_RUN_ISOLATION", "container")
    monkeypatch.setenv("GTM_DOCKER_HOST", "/run/docker.sock")
    monkeypatch.delenv("GTM_ALLOW_RAW_DOCKER_SOCKET", raising=False)
    with pytest.raises(RuntimeError, match="Refusing raw docker socket"):
        reexported_prepare("ws-1", tmp_path, run_dir=tmp_path / "ws-1" / ".run", env={})

    # Secret scrubbing in env file
    monkeypatch.setenv("GTM_DOCKER_HOST", "tcp://docker-proxy:2375")
    plan = reexported_prepare(
        "ws-1",
        tmp_path,
        run_dir=tmp_path / "ws-1" / ".run",
        env={
            "ELEVENLABS_API_KEY": "secret-eleven",
            "POSTGRES_READONLY_URL": "postgresql://usr:pwd@host:5432/db",
            "SAFE_VAR": "hello",
        },
    )
    assert plan is not None
    content = plan.env_file.read_text(encoding="utf-8")
    assert "SAFE_VAR=hello" in content
    assert "ELEVENLABS_API_KEY" not in content
    assert "POSTGRES_READONLY_URL" not in content
    plan.discard()


def _launch(tmp_path, monkeypatch, *, docker_host="tcp://docker-proxy:2375", real_cli=None):
    from agent.container_runner import prepare_container_launch

    monkeypatch.setenv("GTM_RUN_ISOLATION", "container")
    monkeypatch.setenv("GTM_DOCKER_HOST", docker_host)
    monkeypatch.setenv("GTM_ALLOW_RAW_DOCKER_SOCKET", "true")
    ws_root = tmp_path / "workspaces"
    plan = prepare_container_launch(
        WS_A, ws_root, run_dir=ws_root / WS_A / ".run", env={}, real_cli=real_cli
    )
    assert plan is not None
    return plan


def test_a_private_file_is_not_a_host_file_the_run_uid_cannot_open(tmp_path, monkeypatch):
    """The container runs as the workspace uid (20000+); a 0600 file the worker (uid 10001) wrote under .run/ is
    unreadable to it, so the CLI lost every MCP server. The payload must not be a host file at all, and must
    never be on a command line or in the wrapper script."""
    import base64

    plan = _launch(tmp_path, monkeypatch)
    secret = "rr-key-SECRET-" + "é\n" * 3
    path = plan.private_file("mcp.json", secret)

    assert (
        list((plan.run_dir).glob("*mcp.json")) == []
    )  # nothing the worker owns for the uid to trip on
    env_text = plan.env_file.read_text(encoding="utf-8")
    assert base64.b64encode(secret.encode()).decode() in env_text
    assert "rr-key-SECRET" not in env_text
    assert path in env_text  # the manifest names where the bootstrap will put it
    for surface in (plan.cli_path.read_text(encoding="utf-8"), " ".join(plan.docker_argv)):
        assert "rr-key-SECRET" not in surface
        assert base64.b64encode(secret.encode()).decode() not in surface
    assert plan.env_file.stat().st_mode & 0o777 == 0o600
    plan.discard()
    assert not plan.env_file.exists()


def test_a_payload_over_the_env_string_limit_is_split_and_every_chunk_is_listed(
    tmp_path, monkeypatch
):
    from agent.container_runner import _CHUNK

    plan = _launch(tmp_path, monkeypatch)
    plan.private_file("system-prompt.txt", "x" * (2 * _CHUNK))  # base64 is 4/3 larger: 3 chunks
    lines = plan.env_file.read_text(encoding="utf-8").splitlines()
    chunks = [ln for ln in lines if ln.startswith("GTM_PRIVATE_") and "MANIFEST" not in ln]
    assert len(chunks) == 3
    assert all(len(ln.split("=", 1)[1]) <= _CHUNK for ln in chunks)
    manifest = next(ln for ln in lines if "MANIFEST" in ln)
    assert manifest.split(":", 1)[1].split(",") == [ln.split("=", 1)[0] for ln in chunks]
    plan.discard()


def _docker_ready(image: str) -> str | None:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return None
    ctx = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    host = ctx.stdout.strip()
    have = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=False
    ).returncode
    return host if ctx.returncode == 0 and host and have == 0 else None


def test_live_the_run_uid_reads_its_own_private_files_and_nobody_else_can(tmp_path, monkeypatch):
    """Through the real wrapper and a real container as the workspace uid: the files exist, hold exactly what was
    handed over (multi-line, non-ASCII, bigger than one env string), are owned by that uid with mode 0600 inside a
    0700 directory, and no GTM_PRIVATE_* variable survives into the CLI's environment."""
    import subprocess

    image = "node:22-bookworm-slim"
    host = _docker_ready(image)
    if host is None:
        pytest.skip(f"docker with {image} not available")
    monkeypatch.setenv("GTM_RUNNER_IMAGE", image)
    from agent.container_runner import _CHUNK

    plan = _launch(tmp_path, monkeypatch, docker_host=host, real_cli="/bin/sh")
    mcp = '{"mcpServers": {"rocketreach": {"env": {"KEY": "rr-live-secret"}}}}'
    prompt = "line one\nline two é ✓\n" + "p" * (_CHUNK + 5)
    mcp_path = plan.private_file("mcp.json", mcp)
    prompt_path = plan.private_file("system-prompt.txt", prompt)
    probe = (
        f'echo "uid=$(id -u)"; stat -c "mode=%a owner=%u" "{mcp_path}" "{prompt_path}" /tmp/gtm-private; '
        f'cat "{mcp_path}"; echo; cat "{prompt_path}" | md5sum; env | grep -c GTM_PRIVATE || true'
    )
    try:
        done = subprocess.run(
            [str(plan.cli_path), "-c", probe],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        plan.discard()
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert f"uid={plan.uid}" in out
    assert out.count(f"mode=600 owner={plan.uid}") == 2
    assert f"mode=700 owner={plan.uid}" in out  # the directory
    assert mcp in out
    import hashlib

    assert hashlib.md5(prompt.encode(), usedforsecurity=False).hexdigest() in out
    assert out.strip().splitlines()[-1] == "0"


_STDIO_PROBE = """import sys, json
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    r = {"jsonrpc": "2.0", "id": m["id"], "result": {}}
    if m["method"] == "initialize":
        r["result"] = {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {"tools": {}},
                       "serverInfo": {"name": "probe", "version": "1"}}
    elif m["method"] == "tools/list":
        r["result"] = {"tools": [{"name": "ping", "description": "ping", "inputSchema": {"type": "object"}}]}
    print(json.dumps(r), flush=True)
"""


def test_live_the_bundled_cli_in_a_container_lists_its_mcp_servers_as_connected(
    tmp_path, monkeypatch
):
    """The defect this guards: the container's CLI could not open its --mcp-config, so every MCP server (RocketReach,
    Saleshandy, judge) was missing from a tenant run. Real bundled CLI, real container as the workspace uid, the
    config handed over exactly as agent/session.py hands it. Needs a runner image that carries the bundled CLI
    (GTM_TEST_RUNNER_IMAGE, default gtm-backend-mm:test); skipped when docker or the image is absent."""
    pytest.importorskip("claude_agent_sdk")
    import asyncio
    import subprocess

    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    from agent.session import _keep_secrets_off_the_command_line

    image = os.environ.get("GTM_TEST_RUNNER_IMAGE", "gtm-backend-mm:test")
    host = _docker_ready(image)
    if host is None:
        pytest.skip(f"docker with {image} not available")
    found = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
         "ls /usr/local/lib/python*/site-packages/claude_agent_sdk/_bundled/claude"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    cli = found.stdout.strip()
    if found.returncode != 0 or not cli:
        pytest.skip(f"{image} carries no bundled CLI")
    monkeypatch.setenv("GTM_RUNNER_IMAGE", image)

    ws_root = tmp_path / "workspaces"
    tree = ws_root / WS_A
    tree.mkdir(parents=True)
    (tree / "server.py").write_text(_STDIO_PROBE, encoding="utf-8")
    os.chmod(tree / "server.py", 0o644)
    # The container's non-root uid must traverse this fixture tree, so it needs o+x.
    os.chmod(tree, 0o755)  # nosemgrep: insecure-file-permissions
    plan = _launch(tmp_path, monkeypatch, docker_host=host, real_cli=cli)
    plan_env = {"ANTHROPIC_API_KEY": "sk-ant-dummy", "HOME": "/tmp/h"}
    for (
        k,
        v,
    ) in plan_env.items():  # the env-file was written empty by _launch; add what the CLI needs
        with open(plan.env_file, "a", encoding="utf-8") as fh:
            fh.write(f"{k}={v}\n")
    kw = {
        "mcp_servers": {
            "probe": {
                "type": "stdio",
                "command": "python3",
                "args": [str(tree / "server.py")],
                "env": {"PROVIDER_KEY": "k"},
            }
        },
        "extra_args": {"no-session-persistence": None},
        "strict_mcp_config": True,
    }
    _keep_secrets_off_the_command_line(kw, plan)

    async def statuses():
        async with ClaudeSDKClient(ClaudeAgentOptions(cli_path=str(plan.cli_path), **kw)) as c:
            for _ in range(30):
                got = (await c.get_mcp_status()).get("mcpServers", [])
                if got and all(s.get("status") != "pending" for s in got):
                    return got
                await asyncio.sleep(1)
            return got

    try:
        got = asyncio.run(statuses())
    finally:
        plan.discard()
    assert [(s["name"], s["status"]) for s in got] == [("probe", "connected")]
