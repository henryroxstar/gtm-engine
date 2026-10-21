"""Pattern D: Ephemeral Container Per Run (R8).

Generates a ContainerLaunchPlan and wrapper script that executes Claude Code CLI
inside a single-tenant Docker container with dropped capabilities, read-only root,
isolated tmpfs, kernel resource caps, and only the tenant's workspace tree mounted.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from gtm_core.confine import ConfinementError

_SECRET_NAME = re.compile(
    r"KEY|KEK|SECRET|TOKEN|DSN|PASSWORD|PASSWD|CREDENTIAL|WEBHOOK", re.IGNORECASE
)
_URL_WITH_PASSWORD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_ALSO_SECRET = frozenset(
    {
        "DATABASE_URL",
        "POSTGRES_MIGRATION_URL",
        "POSTGRES_READONLY_URL",
        "REDIS_URL",
        "RATELIMIT_STORAGE_URI",
        "API_KEY_PEPPER",
        "REVENUECAT_SECRET_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GTM_INTERNAL_BACKEND_TOKEN",
        "VAULT_KEK",
        "POSTGRES_GTMAPI_PASSWORD",
        "POSTGRES_PASSWORD",
        "JWT_SECRET",
        "BACKEND_JWT_SECRET",
        "COOKIE_SECRET",
    }
)
_CLI_OWN_PREFIXES = ("ANTHROPIC_", "CLAUDE_")

#: Where the container's bootstrap writes a run's private files: the container's own tmpfs, never the bind mount.
_PRIVATE_DIR = "/tmp/gtm-private"  # nosec B108 — inside the ephemeral container, tmpfs, 0700, owned by the run's uid
#: ``docker run --env-file`` refuses a line over 64 KiB (bufio.Scanner), and the kernel caps one env string at
#: 128 KiB; a payload is split so every line stays under the smaller limit.
_CHUNK = 48 * 1024

#: Runs inside the container as the workspace uid before the CLI: turn each ``GTM_PRIVATE_MANIFEST_*`` entry
#: (``<path>:<var>,<var>...``) into a file the uid alone can read (umask 077), drop every ``GTM_PRIVATE_*`` variable
#: so the CLI and its subprocesses never inherit the payload, then exec the CLI. Written without single quotes
#: because the wrapper hands it to ``sh -c '...'``.
_BOOTSTRAP = (
    "set -eu; umask 077; mkdir -p " + _PRIVATE_DIR + "; "
    'for e in $(env | sed -n "s/^GTM_PRIVATE_MANIFEST_[0-9a-f]*=//p"); do '
    'p=${e%%:*}; vs=$(echo "${e#*:}" | tr , " "); '
    'for v in $vs; do eval "printf %s \\"\\$$v\\""; done | base64 -d > "$p" || exit 1; done; '
    'for n in $(env | sed -n "s/^\\(GTM_PRIVATE_[A-Za-z0-9_]*\\)=.*/\\1/p"); do unset "$n"; done; '
    'exec "$@"'
)


@dataclass(frozen=True)
class ContainerLaunchPlan:
    """Launch execution plan and wrapper script for an isolated container run."""

    cli_path: Path
    env_file: Path
    uid: int
    docker_argv: list[str]
    run_dir: Path
    launch_dir: Path
    written: list[Path] = field(default_factory=list, compare=False)

    def private_file(self, name: str, text: str) -> str:
        """Hand ``text`` to the run as a file only the run's own uid can read, and return the path it will have.

        A file written here by the backend worker (uid 10001, mode 0600) is unreadable to the container's
        ``--user <workspace uid>``, so the CLI could not open the ``--mcp-config`` it was given. Instead the payload
        travels in the 0600 env-file (read by the docker client as the worker, never on a command line) and the
        container's bootstrap writes it to the container's own ``/tmp/gtm-private`` (tmpfs, ``umask 077``) as the
        workspace uid, then removes it from the environment before the CLI starts.
        """
        token = secrets.token_hex(8)
        path = f"{_PRIVATE_DIR}/{token}-{name}"
        blob = base64.b64encode(text.encode("utf-8")).decode("ascii")
        chunks = [blob[i : i + _CHUNK] for i in range(0, len(blob), _CHUNK)] or [""]
        names = [f"GTM_PRIVATE_{token}_{i}" for i in range(len(chunks))]
        # Append in one write so a failure cannot leave half a payload behind.
        fd = os.open(self.env_file, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{n}={c}\n" for n, c in zip(names, chunks, strict=True)))
            fh.write(f"GTM_PRIVATE_MANIFEST_{token}={path}:{','.join(names)}\n")
        return path

    def discard(self) -> None:
        """Delete temporary wrapper script, env file, and private files."""
        for path in self.written:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self.written.clear()
        try:
            self.cli_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self.env_file.unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup(self) -> None:
        """Alias for discard."""
        self.discard()


def prepare_container_launch(
    workspace_id: str,
    registry_dir: Path,
    *,
    run_dir: Path,
    env: Mapping[str, str],
    real_cli: str | None = None,
) -> ContainerLaunchPlan | None:
    """Prepare a ContainerLaunchPlan if GTM_RUN_ISOLATION=container, else None."""
    if os.environ.get("GTM_RUN_ISOLATION") != "container":
        return None

    docker_host = os.environ.get("GTM_DOCKER_HOST", "").strip()
    allow_raw = os.environ.get("GTM_ALLOW_RAW_DOCKER_SOCKET", "").lower() in (
        "true",
        "1",
        "yes",
    )
    if not docker_host:
        raise RuntimeError(
            "GTM_RUN_ISOLATION=container requires GTM_DOCKER_HOST to be set (e.g. tcp://docker-proxy:2375). "
            "If you must use the raw docker socket, set GTM_ALLOW_RAW_DOCKER_SOCKET=true."
        )
    is_raw_socket = (
        docker_host.startswith("unix://")
        or "docker.sock" in docker_host
        or docker_host.endswith(".sock")
    )
    if is_raw_socket and not allow_raw:
        raise RuntimeError(
            f"Refusing raw docker socket at {docker_host} for GTM_RUN_ISOLATION=container. "
            "Use a scoped docker-proxy via GTM_DOCKER_HOST, or explicitly override with GTM_ALLOW_RAW_DOCKER_SOCKET=true."
        )

    from agent.tenant_identity import identity_for

    ws_tree = (registry_dir / workspace_id).resolve()
    run_dir_resolved = run_dir.resolve()
    if not run_dir_resolved.is_relative_to(ws_tree):
        raise ConfinementError(f"run_dir {run_dir_resolved} escapes workspace tree {ws_tree}")

    uid = identity_for(workspace_id, registry_dir)

    # Ensure tenant workspace directories exist
    ws_tree.mkdir(parents=True, exist_ok=True)
    (ws_tree / "profiles").mkdir(parents=True, exist_ok=True)
    (ws_tree / "content").mkdir(parents=True, exist_ok=True)
    (run_dir_resolved / "home").mkdir(parents=True, exist_ok=True)
    (run_dir_resolved / "tmp").mkdir(parents=True, exist_ok=True)

    launch_dir = (registry_dir / ".launch").resolve()
    launch_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions -- 0o700 is owner-only for directory traversal
    os.chmod(launch_dir, 0o700)

    token = secrets.token_hex(8)
    env_file = launch_dir / f"env-{token}.list"
    cli_wrapper = launch_dir / f"run-{token}.sh"

    # Scrub control plane and provider secrets (regex shape + allowlist)
    scrubbed_env: dict[str, str] = {}
    for k, v in env.items():
        if k.startswith(_CLI_OWN_PREFIXES):
            scrubbed_env[k] = v
            continue
        if (
            _SECRET_NAME.search(k)
            or k in _ALSO_SECRET
            or (isinstance(v, str) and _URL_WITH_PASSWORD.search(v))
        ):
            continue
        scrubbed_env[k] = v

    # Write scrubbed env file (mode 0600)
    fd_env = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd_env, "w", encoding="utf-8") as fh:
        for k, v in sorted(scrubbed_env.items()):
            # Escape newlines in env values if any
            clean_v = v.replace("\n", "\\n")
            fh.write(f"{k}={clean_v}\n")

    memory = os.environ.get("GTM_CONTAINER_MEMORY", "1024m")
    cpus = os.environ.get("GTM_CONTAINER_CPUS", "1.0")
    pids_limit = os.environ.get("GTM_CONTAINER_PIDS_LIMIT", "256")
    image = os.environ.get("GTM_RUNNER_IMAGE", "gtm-backend:latest")
    cli_cmd = real_cli or "/app/_bundled/claude"

    docker_args = [
        "-H",
        docker_host,
        "run",
        "--rm",
        "-i",
        "--init",
        f"--user {uid}:{uid}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs /tmp:rw,noexec,nosuid,size=256m",
        f"--memory {memory}",
        f"--cpus {cpus}",
        f"--pids-limit {pids_limit}",
        "--workdir /app",
        f"--env-file {env_file}",
        f"-v {ws_tree}:{ws_tree}:rw",
        image,
        "/bin/sh",
        "-c",
        _BOOTSTRAP,
        "gtm-run",
        cli_cmd,
    ]

    bootstrap = _BOOTSTRAP
    wrapper_content = f"""#!/bin/sh
set -eu
exec docker -H "{docker_host}" run \\
  --rm \\
  -i \\
  --init \\
  --user {uid}:{uid} \\
  --cap-drop=ALL \\
  --security-opt=no-new-privileges \\
  --read-only \\
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \\
  --memory {memory} \\
  --cpus {cpus} \\
  --pids-limit {pids_limit} \\
  --workdir /app \\
  --env-file {env_file} \\
  -v {ws_tree}:{ws_tree}:rw \\
  {image} \\
  /bin/sh -c '{bootstrap}' gtm-run {cli_cmd} "$@"
"""

    fd_cli = os.open(cli_wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd_cli, "w", encoding="utf-8") as fh:
        fh.write(wrapper_content)

    return ContainerLaunchPlan(
        cli_path=cli_wrapper,
        env_file=env_file,
        uid=uid,
        docker_argv=docker_args,
        run_dir=run_dir_resolved,
        launch_dir=launch_dir,
    )
