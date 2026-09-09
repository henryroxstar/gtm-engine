"""Every env var the VPS agent reads must actually reach the VPS agent's container.

The gap this closes, concretely (2026-09-01): ``SALESHANDY_API_KEY`` was set in Doppler,
documented in ``.env.example``, wired into ``deploy/docker-compose.yml`` — and absent
from the ``gtm-agent`` service in the ROOT ``docker-compose.yml``, which is the stack
every ``systemd/*.service`` unit runs (``docker compose run --rm --no-deps gtm-agent``).
Compose uses an explicit env ALLOWLIST with no ``env_file:``, so an omitted var does not
"default to unset" — it never reaches the container at all, however carefully the
operator set it. ``agent/optout_sweep.py`` therefore failed on every scheduled run and
Telegram-pinged the operator six times a day; the whole Saleshandy, RocketReach, Apollo
and Syften connector surface was dark on the box for the same reason, silently, because
each of those fails closed to "connector absent" rather than to an alert.

``tests/contracts/test_deploy_env_sync.py`` is the same idea for the ``HERMES_*`` publish
switches and the backend deploy surface; it skips entirely when ``deploy/`` is absent
(the OSS carve). This one covers the agent/cockpit stack, needs no ``deploy/``, and
derives its var names from source so it cannot go stale by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

COMPOSE = REPO / "docker-compose.yml"
ENV_EXAMPLE = REPO / ".env.example"

# `oss/overlays/docker-compose.yml` REPLACES the private one in the public export
# (scripts/oss-export.sh), and the connector wrappers themselves are carved in — so the
# overlay drifting is the same bug shipped to outsiders, who have no way to see it: each
# connector fails closed to "absent" rather than to an error. Checked only when present;
# in the public carve `oss/` is never carved, so this simply doesn't apply there.
_OSS_OVERLAY = REPO / "oss" / "overlays" / "docker-compose.yml"
COMPOSE_FILES = tuple(p for p in (COMPOSE, _OSS_OVERLAY) if p.exists())

_GETENV_RE = re.compile(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]+)"')

# Read by a module OTHER than agent/config.py, so a config-derived scan misses them.
# Same failure shape: a switch the container never receives can never be opened.
_EXTRA_SOURCES = (Path("agent") / "reply.py",)


def _env_names(rel: Path) -> frozenset[str]:
    return frozenset(_GETENV_RE.findall((REPO / rel).read_text(encoding="utf-8")))


def _config_env_names() -> frozenset[str]:
    names = _env_names(Path("agent") / "config.py")
    assert names, "the regex found nothing — it has drifted from agent/config.py's shape"
    return names


def test_env_name_extraction_is_non_vacuous_and_pinned():
    """Anti-vacuity: pin a known set so a broken regex fails LOUD, not silent — a
    vacuous scan here reports green while the exact drift it guards is back."""
    names = _config_env_names()
    assert {"SALESHANDY_API_KEY", "ROCKETREACH_API_KEY", "ANTHROPIC_API_KEY"} <= names


def test_every_config_env_var_reaches_the_agent_container():
    for path in COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        missing = {name for name in _config_env_names() if name not in text}
        assert not missing, (
            f"{path.relative_to(REPO)} (the stack every systemd unit runs) is missing env "
            f"passthrough for {sorted(missing)} — agent/config.py reads them, so on the box "
            f"they are permanently unset no matter what the secret manager holds"
        )


def test_reply_send_switches_reach_the_agent_container():
    names = {n for n in _env_names(_EXTRA_SOURCES[0]) if n.startswith("REPLY_SEND_")}
    assert names, "expected REPLY_SEND_* switches in agent/reply.py"
    for path in COMPOSE_FILES:
        text = path.read_text(encoding="utf-8")
        missing = {n for n in names if n not in text}
        assert not missing, (
            f"{path.relative_to(REPO)} is missing env passthrough for {sorted(missing)}"
        )


def test_every_config_env_var_is_documented_for_the_operator():
    """Wired but undocumented is only half a fix — an operator sets what .env.example
    tells them exists."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    missing = {name for name in _config_env_names() if name not in text}
    assert not missing, f".env.example does not document {sorted(missing)}"


def test_the_approve_resume_switch_defaults_closed_where_it_is_wired():
    """Present is not enough: wiring it open-by-default would arm approve-and-resume
    for every operator who never set it, inverting the code's own fail-closed default."""
    for path in COMPOSE_FILES:
        text = path.read_text(encoding="utf-8").replace(" ", "")
        assert "APPROVE_RESUME_ENABLED:${APPROVE_RESUME_ENABLED:-false}" in text, (
            f"{path.relative_to(REPO)} must wire APPROVE_RESUME_ENABLED closed by default"
        )
