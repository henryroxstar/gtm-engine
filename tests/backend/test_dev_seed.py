"""Contract tests for scripts/dev_seed.py: loopback enforcement and pack provisioning."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.pack_catalog import resolve_variant, variant_readiness
from gtm_core.paths import resolve_workspaces_root, workspace_profiles_root

try:
    from scripts.dev_seed import (
        DEV_PROFILE_FIXTURES,
        SeedError,
        _fail,
        _ok,
        _print_summary,
        _provision_profile_files,
        require_loopback,
    )
except ModuleNotFoundError:
    pytest.skip("scripts.dev_seed is not present in this distribution", allow_module_level=True)

REPO = Path(__file__).resolve().parents[2]
_DEPLOY = REPO / "deploy"

if not (_DEPLOY / "docker-compose.yml").exists():
    # deploy/ is excluded from the OSS carve (tenant-soaked deploy surface) — same
    # guard as tests/contracts/test_deploy_env_sync.py.
    _COMPOSE_TESTABLE = False
else:
    _COMPOSE_TESTABLE = True

# Matches a GTM_WORKSPACES_ROOT assignment in either compose environment-block spelling:
# mapping (`GTM_WORKSPACES_ROOT: /app/...`) or list (`- GTM_WORKSPACES_ROOT=/app/...`).
_WORKSPACES_ROOT_OVERRIDE_RE = r"GTM_WORKSPACES_ROOT\s*[:=]"


def _service_block(compose_text: str, name: str) -> str:
    """Raw text of a top-level (2-space indented) service's block, e.g. ``api:``.

    Stops at the next line indented at 2 spaces or less (a sibling service, a
    section-header comment, or a dedent to a top-level ``volumes:``/``networks:``
    key) — the same "stop at the next same-or-lower indent" rule as a real YAML
    mapping boundary, without pulling in a parser for one lookup.
    """
    lines = compose_text.splitlines()
    header = re.compile(rf"^  {re.escape(name)}:\s*$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    assert start is not None, f"no `  {name}:` service block found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^(\S|  \S)", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _bind_mounts(service_block: str) -> dict[str, str]:
    """``{host_path: container_path}`` for short-syntax bind mounts in a service block.

    Skips named-volume entries (``- content:/app/content``), whose host side is a
    bare volume name rather than a path — only ``./``, ``../`` and ``/``-rooted host
    sides are binds.
    """
    out: dict[str, str] = {}
    for line in service_block.splitlines():
        m = re.match(r"^\s*-\s*([^:\s][^:]*):(/[^:\s]+)(?::\w+)?\s*$", line)
        if m and m.group(1).startswith((".", "/")):
            out[m.group(1)] = m.group(2)
    return out


def test_require_loopback_allowed():
    assert require_loopback("http://localhost:8000") == "http://localhost:8000"
    assert require_loopback("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert require_loopback("http://[::1]:8000") == "http://[::1]:8000"


def test_require_loopback_rejected():
    with pytest.raises(ValueError, match="refusing"):
        require_loopback("https://api.example.com")
    with pytest.raises(ValueError, match="refusing"):
        require_loopback("http://remote.host.example:8000")
    with pytest.raises(ValueError, match="refusing"):
        require_loopback("http://127.0.0.1@api.example.com")


def test_provision_profile_files_enables_pack_admission(tmp_path, monkeypatch):
    """Synthetic provisioning enables pack activation and readiness check without LLM key."""
    ws_id = "test-workspace-seed-uuid"
    profile = "example-widgets"

    # Route workspace_profiles_root to tmp_path
    monkeypatch.setattr("scripts.dev_seed.REPO", tmp_path)
    monkeypatch.setenv("GTM_WORKSPACE_DIR", str(tmp_path / "workspaces"))

    _provision_profile_files(ws_id, profile)

    profiles_root = workspace_profiles_root(ws_id, tmp_path)
    pdir = profiles_root / profile

    # Verify files created on disk
    for rel_path in DEV_PROFILE_FIXTURES:
        assert (pdir / rel_path).is_file(), f"Missing provisioned file {rel_path}"

    # Verify pack resolution succeeds
    resolved = resolve_variant(REPO, profiles_root, profile, "marketing", "linkedin-post")
    assert resolved.graph.pack == "marketing"
    assert resolved.graph.variant == "linkedin-post"

    # Verify variant readiness passes (not blocked)
    report = variant_readiness(profiles_root, profile, resolved)
    assert not report.blocked, (
        f"Pack readiness is blocked: {[i for i in report.items if i.status == 'red']}"
    )


def test_dev_seed_cp1252_encoding_safety(monkeypatch):
    """Ensure _ok, _fail, and _print_summary do not raise UnicodeEncodeError on cp1252 streams."""
    import sys

    class StrictCp1252Stream:
        def __init__(self):
            self.encoding = "cp1252"
            self.errors = "strict"
            self.buffer = []

        def write(self, s: str):
            # Strict encoding simulates a Windows console pipe with cp1252
            s.encode(self.encoding, errors=self.errors)
            self.buffer.append(s)

        def flush(self):
            pass

    stream = StrictCp1252Stream()
    monkeypatch.setattr(sys, "stdout", stream)

    _ok("registered user@example.com")
    with pytest.raises(SeedError):
        _fail("registration failed")
    _print_summary(
        "http://127.0.0.1:8000",
        "ws-123",
        {"access_token": "tok", "refresh_token": "ref"},
        "profile-a",
    )

    joined = "".join(stream.buffer)
    assert "[ok] registered user@example.com" in joined
    assert "[fail] registration failed" in joined
    assert "workspace_id:  ws-123" in joined


@pytest.mark.skipif(
    not _COMPOSE_TESTABLE, reason="deploy/ not present (private deployment surface)"
)
def test_dev_api_container_binds_the_seeds_workspaces_root(monkeypatch):
    """LD-01: scripts/dev_seed.py's ``_provision_profile_files`` writes pack-mode
    fixtures (packs.toml, PROFILE.md, knowledge/*) straight to disk under
    ``workspace_profiles_root()``, which resolves under the HOST's
    ``resolve_workspaces_root()`` (default ``<repo>/data/workspaces``). The base
    ``deploy/docker-compose.yml`` instead pins the api container's
    ``GTM_WORKSPACES_ROOT`` to the named ``workspaces`` volume, which the seed script
    never touches — so the fixtures never reach the container and a local pack-mode
    run 403s with ``pack_not_activated``. The dev override must bind-mount the exact
    directory the seed writes to at the api's own ``GTM_WORKSPACES_ROOT``.
    """
    monkeypatch.delenv("GTM_WORKSPACES_ROOT", raising=False)

    base_text = (_DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    dev_text = (_DEPLOY / "docker-compose.dev.yml").read_text(encoding="utf-8")

    base_api = _service_block(base_text, "api")
    m = re.search(r"GTM_WORKSPACES_ROOT:\s*(\S+)", base_api)
    assert m, "deploy/docker-compose.yml's api service no longer sets GTM_WORKSPACES_ROOT"
    container_root = m.group(1).strip()

    dev_api = _service_block(dev_text, "api")
    assert not re.search(_WORKSPACES_ROOT_OVERRIDE_RE, dev_api), (
        "deploy/docker-compose.dev.yml's api service sets its own GTM_WORKSPACES_ROOT — "
        "this test only reads the value from the base compose file, so an override here "
        "could silently desync the bind mount's target from the container's actual root"
    )

    binds = _bind_mounts(dev_api)
    matching_hosts = [host for host, container in binds.items() if container == container_root]
    assert matching_hosts, (
        f"deploy/docker-compose.dev.yml's api service has no bind mount targeting "
        f"{container_root!r} — seeded pack fixtures (scripts/dev_seed.py) never reach "
        f"the dev api container, so a local pack-mode run 403s with pack_not_activated"
    )

    expected_host_dir = resolve_workspaces_root(REPO)
    # Compose resolves a relative bind source against the PROJECT directory — the
    # first `-f` file's directory (deploy/, for every `-f ... -f ...` invocation in
    # this repo) — not each `-f` file's own directory. Both compose files happen to
    # live in deploy/ here, so this is also each file's own directory, but the
    # project directory is what actually governs it.
    actual_host_dir = (_DEPLOY / matching_hosts[0]).resolve()
    assert actual_host_dir == expected_host_dir, (
        f"the api service's bind mount resolves to {actual_host_dir}, but "
        f"scripts/dev_seed.py writes fixtures under {expected_host_dir} "
        f"(gtm_core.paths.workspace_profiles_root) — they are different directories"
    )


def test_the_workspaces_root_override_regex_also_catches_the_list_env_form():
    """Self-test for ``_WORKSPACES_ROOT_OVERRIDE_RE``: a compose ``environment:`` block
    can be written as a mapping (what the base and dev files use today) or as a list —
    ``- GTM_WORKSPACES_ROOT=/x``. The override guard above must catch either spelling,
    or a future list-form override in the dev file would slip past it silently."""
    assert re.search(
        _WORKSPACES_ROOT_OVERRIDE_RE, "      GTM_WORKSPACES_ROOT: /app/data/workspaces"
    )
    assert re.search(_WORKSPACES_ROOT_OVERRIDE_RE, "      - GTM_WORKSPACES_ROOT=/x")
    # Negative control: a same-prefixed name must not false-positive.
    assert not re.search(_WORKSPACES_ROOT_OVERRIDE_RE, "      GTM_WORKSPACES_ROOT_OTHER: /x")
