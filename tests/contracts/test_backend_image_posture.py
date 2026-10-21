"""MM10/MM11: the backend image runs unprivileged and installs exactly what ``uv.lock`` pins.

The backend executes tenant-influenced agent runs (shell, file tools, MCP workers), and every workspace's
tree lives in this one container. A process running as root there can read and chown anything, including
the other tenants' trees and the volume's ownership itself, so the first thing a compromised or merely
mis-prompted run would need — a privilege — must simply not be on offer. This is a contract on the
Dockerfile's TEXT (cheap, runs everywhere); the one real build-and-``id -u`` check cannot run in the unit
tier and is done by hand against an image built from the branch.

The image is shared with production: the compose files are not changed by this, but a volume that a
root-owned older deploy left behind must be chowned once (see the runbook line the startup check prints).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[2] / "deploy" / "Dockerfile.backend"


def _instructions() -> list[tuple[str, str]]:
    """``(INSTRUCTION, rest)`` with line continuations joined and comments dropped."""
    if not DOCKERFILE.exists():
        pytest.skip("deploy/Dockerfile.backend not present in OSS carve")
    out: list[tuple[str, str]] = []
    buf = ""
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        head, _, rest = buf.partition(" ")
        out.append((head.upper(), rest.strip()))
        buf = ""
    return out


def test_the_image_ends_as_a_non_root_user():
    users = [(i, rest) for i, (ins, rest) in enumerate(_instructions()) if ins == "USER"]
    assert users, "Dockerfile.backend has no USER: the process runs as root"
    _, who = users[-1]
    assert who.split(":")[0] not in ("root", "0"), f"the last USER is {who!r}"


def test_the_user_is_set_after_every_instruction_that_needs_root():
    ins = _instructions()
    last_user = max(i for i, (name, _r) in enumerate(ins) if name == "USER")
    last_run = max(i for i, (name, _r) in enumerate(ins) if name == "RUN")
    assert last_user > last_run, (
        "a RUN after the final USER would either fail or run as the unprivileged user"
    )


def test_the_user_has_a_fixed_high_uid_so_a_volume_can_be_chowned_to_it():
    runs = " ".join(rest for name, rest in _instructions() if name == "RUN")
    m = re.search(r"useradd[^&;]*--uid\s+(\d+)", runs)
    assert m, "the non-root user is created without a fixed --uid"
    assert int(m.group(1)) >= 1000


def _chowned_paths() -> set[str]:
    runs = " ".join(rest for name, rest in _instructions() if name == "RUN")
    paths: set[str] = set()
    for stmt in re.split(r"&&|;", runs):
        words = stmt.split()
        if "chown" in words:
            paths |= {w for w in words[words.index("chown") + 1 :] if w.startswith("/")}
    return paths


def test_the_state_directories_belong_to_that_user_so_a_fresh_volume_inherits_them():
    runs = " ".join(rest for name, rest in _instructions() if name == "RUN")
    assert "/app/data/workspaces" in runs, "the per-workspace tree is not created in the image"
    assert {"/app/data", "/app/content", "/app/profiles"} <= _chowned_paths()


def test_the_application_code_is_not_owned_by_the_user_that_runs_it():
    """The same container runs every tenant's agent. If the unprivileged user could rewrite ``/app`` — the
    skills, the policy, ``gtm_core`` — then ANY way past a path check becomes a persistent change to code
    that every other tenant's run executes. Only the state directories are handed over."""
    owned = _chowned_paths()
    assert "/app" not in owned and "/app/" not in owned, owned
    for code in (
        "agent",
        "backend",
        "gtm_core",
        "mcp_server",
        "plugin",
        "packs",
        "schemas",
        "cockpit",
    ):
        assert f"/app/{code}" not in owned


def test_the_install_is_the_lock_and_nothing_else():
    """MM11 (SECURITY-SELF-ASSESSMENT §6 item 24): a bare ``pip install .`` re-resolves every ``>=`` to the
    newest release at build time, so the deployed Agent SDK drifted from the one the tests ran against."""
    runs = [rest for name, rest in _instructions() if name == "RUN"]
    assert any("uv export --frozen" in r and "uv pip install" in r for r in runs)
    for r in runs:
        for stmt in re.split(r"&&|;", r):
            if re.search(r"\bpip install\b", stmt) and "uv==" not in stmt:
                assert "-r " in stmt or "--no-deps" in stmt, (
                    f"an unlocked install: {stmt.strip()!r}"
                )
