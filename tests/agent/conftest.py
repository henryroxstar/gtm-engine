"""Shared fixtures for the agent unit tests (spec §13).

These tests exercise the SDK-INDEPENDENT halves of the brain — `agent.profiles` and
`agent.ledgers` — without importing `claude_agent_sdk`. Two jobs here:

1. Put the repo root on `sys.path` so `import agent.profiles` / `import agent.ledgers`
   resolve to the in-tree packages when pytest is run from the repo root.
2. Expose `REPO_ROOT` and a `cfg` fixture that points `Config.content_root` at pytest's
   `tmp_path`, so the ledger tests never touch the real (gitignored) `content/` volume.

The agent package modules are authored by sibling component 0.12. If they are not present
yet (parallel build), the imports here are deferred to the test modules, which `skip`
cleanly rather than error — see the top of each test file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/agent/conftest.py → parents[2] == repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def cfg(tmp_path):
    """A `Config` whose `content_root` is redirected at a throwaway tmp dir.

    Built via `Config.from_env(repo_root=REPO_ROOT)` so `repo_root`, `plugin_path`,
    and `profiles_root` point at the real checkout (the ledger/profile code reads the
    real `profiles/` bundles), while runtime state writes land under `tmp_path / "content"`.

    `dataclasses.replace` is used because `Config` is a frozen dataclass per the
    locked interface — we don't mutate the instance in place.

    For tests that must not touch the real `profiles/` tree (e.g. onboarding staging /
    promote tests), use the `cfg_isolated` fixture instead, which redirects both
    `content_root` and `profiles_root` under `tmp_path`. Profile-reading tests
    (test_profiles.py) use their own module-level `PROFILES_ROOT` constant.
    """
    import dataclasses

    from agent.config import Config

    base = Config.from_env(repo_root=REPO_ROOT)
    return dataclasses.replace(base, content_root=tmp_path / "content")


@pytest.fixture()
def cfg_isolated(tmp_path):
    """A `Config` with BOTH `content_root` and `profiles_root` redirected under `tmp_path`.

    Use this fixture for onboarding staging/promote tests and any test that writes to the
    `profiles/` tree — it ensures the test never touches the real `profiles/` directory.

    `repo_root` and `plugin_path` still point at the real checkout so skill lookups work.
    Profile-reading tests (test_profiles.py) use their own module-level `PROFILES_ROOT`
    constant and are unaffected by this fixture.
    """
    import dataclasses

    from agent.config import Config

    base = Config.from_env(repo_root=REPO_ROOT)
    return dataclasses.replace(
        base,
        content_root=tmp_path / "content",
        profiles_root=tmp_path / "profiles",
    )
