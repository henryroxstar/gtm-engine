"""F2 regression net: every ``HERMES_*`` var ``agent/publish.py`` reads from the
environment must actually reach a deployed container.

The gap this closes: ``HERMES_SCHEDULE_ENABLED`` (the schedule kill switch) was read
in ``agent/publish.py`` for weeks with zero deployment wiring — absent from every
compose file's explicit env allowlist and both ``.env.example`` files — so the switch
could never be opened in production; scheduling was permanently dead regardless of what
an operator set. Compose files use an explicit env ALLOWLIST (no ``env_file``), so a var
missing here is not "defaults to unset" — it never reaches the container at all.

This test derives the var names from source (never hand-lists them, so it can't go
stale the same way) and asserts each one appears in every deploy surface. Pure text
containment, no compose/dotenv parsing — cheap and hard to defeat by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

if not (REPO / "deploy" / "docker-compose.yml").exists():
    # deploy/ is excluded from the OSS carve by design (tenant-soaked deploy surface) —
    # this is a private-repo deployment-hygiene gate, not a public-distribution contract.
    pytest.skip("deploy/ not present (private deployment surface)", allow_module_level=True)

_ENV_NAME_RE = re.compile(r'(?:os\.getenv|_int)\(\s*"(HERMES_[A-Z_]+)"')


def _hermes_env_names() -> frozenset[str]:
    src = (REPO / "agent" / "publish.py").read_text(encoding="utf-8")
    names = frozenset(_ENV_NAME_RE.findall(src))
    assert names, "the regex found nothing — it has drifted from agent/publish.py's shape"
    return names


HERMES_ENV_NAMES = _hermes_env_names()

_COMPOSE_FILES = (
    REPO / "docker-compose.yml",
    REPO / "deploy" / "docker-compose.yml",
    REPO / "oss" / "overlays" / "docker-compose.yml",
)
_ENV_EXAMPLE_FILES = (
    REPO / ".env.example",
    REPO / "deploy" / ".env.example",
)


def test_env_name_extraction_is_non_vacuous_and_pinned():
    """Anti-vacuity: pin the known set so a broken regex fails LOUD, not silent."""
    assert {"HERMES_PUBLISH_URL", "HERMES_PUBLISH_ENABLED", "HERMES_SCHEDULE_ENABLED"} <= (
        HERMES_ENV_NAMES
    )


def test_every_hermes_env_var_is_wired_into_every_compose_file():
    for path in _COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        missing = {name for name in HERMES_ENV_NAMES if name not in text}
        assert not missing, f"{path.relative_to(REPO)} is missing env passthrough for {missing}"


def test_every_hermes_env_var_is_documented_in_every_env_example():
    for path in _ENV_EXAMPLE_FILES:
        text = path.read_text(encoding="utf-8")
        missing = {name for name in HERMES_ENV_NAMES if name not in text}
        assert not missing, f"{path.relative_to(REPO)} is missing documentation for {missing}"


def test_schedule_kill_switch_defaults_closed_everywhere_it_is_wired():
    """Not just present — present with a closed default, matching the code's own
    default (`_truthy(os.getenv("HERMES_SCHEDULE_ENABLED"))` treats unset as False).
    A compose file that wired the var open-by-default would silently defeat the
    switch for every operator who never set it explicitly."""
    for path in _COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        assert "HERMES_SCHEDULE_ENABLED:${HERMES_SCHEDULE_ENABLED:-false}" in text.replace(
            " ", ""
        ), f"{path.relative_to(REPO)} must default HERMES_SCHEDULE_ENABLED to false"
