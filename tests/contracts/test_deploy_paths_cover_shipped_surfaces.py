"""Contract: the deploy fires on everything that ships, and ships everything it fires on.

Two defects, measured 2026-08-27, in opposite directions.

FORWARD — a shipped surface that triggers no deploy. ``deploy.yml``'s ``paths:`` filter
omits ``gtm_core/**`` (baked into the image), ``packs/**``, ``backend/**``,
``mcp_server/**``, ``tests/linter/**`` and ``docs/**``. A change to any of them merges to
main and the box never learns: the baked ones need a rebuild that never fires, and the
bind-mounted ones come from the box's ``git pull``, which only runs during a deploy — so
the mounted tree silently goes stale against the repo.

REVERSE — a filter entry that ships nothing. ``.claude/**`` IS in the filter, carrying the
comment *"runtime-loaded permission backstop + skills/commands"* — and ``.claude/`` is
neither ``COPY``'d nor mounted, so the entry deploys nothing at all. A path filter entry
whose comment asserts a runtime role it does not have is the same failure shape as a
registry role with no consumer, pointed the other way. Hence: both directions, one rule.

Plus T5 — the deploy script pulls and rebuilds but never runs ``systemd/install.sh``, so
repo unit files never reach ``/etc/systemd/system``. That is the verbatim 2026-06-26
incident that ``install.sh``'s own header documents ("the box ran Jun-16 units while the
repo was Jun-22"), still open.

Fixtures: every synthetic workflow/Dockerfile below is invented (fictional directory names
only), per the third-party-PII rule (docs/RULES.md §R9).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts._deploy_surface import (
    copy_targets,
    deploy_path_filter,
    mount_sources,
    undelivered,
)

REPO = Path(__file__).resolve().parents[2]
DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"

if not DEPLOY_WORKFLOW.exists() or not (REPO / "docker-compose.yml").exists():
    # Both are excluded from the OSS carve by design (private deploy surface).
    pytest.skip("deploy workflow/compose not present (private surface)", allow_module_level=True)

#: FORWARD exemptions — delivered, but deliberately not deploy triggers.
#: `.git`   — VCS metadata mounted for the journey gitscan; not a committable source path.
#: `content`— gitignored runtime state; never in git, so it can never trigger a path filter.
_NOT_A_TRIGGER = frozenset({".git", "content"})

#: REVERSE exemptions — legitimate filter entries that are build inputs rather than
#: paths delivered INTO the container. Changing any of them must rebuild.
_BUILD_INPUTS = frozenset({"Dockerfile", "docker-compose.yml", "pyproject.toml"})

#: REVERSE exemptions — shipped to the HOST rather than into the container. `systemd/`
#: is installed to /etc/systemd/system by the deploy's install.sh step, so "does a
#: container receive it?" is the wrong question for it. This exemption is not a free
#: pass: `test_the_host_installed_exemption_is_earned` below asserts the deploy really
#: does install it, so the entry cannot become orphaned again behind this comment.
_HOST_INSTALLED = frozenset({"systemd"})


def _filter_entries() -> set[str]:
    return deploy_path_filter(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))


def _baked() -> set[str]:
    return copy_targets((REPO / "Dockerfile").read_text(encoding="utf-8"))


def _mounted() -> set[str]:
    return mount_sources((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


def _deploy_script() -> str:
    """The workflow's inline deploy `script:` body."""
    return DEPLOY_WORKFLOW.read_text(encoding="utf-8")


# ── non-vacuity: a broken parser must fail LOUD, not pass empty ───────────────


def test_the_deploy_path_filter_parse_is_non_vacuous():
    entries = _filter_entries()
    assert {"agent", "cockpit", "plugin", "profiles"} <= entries, (
        f"deploy.yml paths: parse looks broken — got {sorted(entries)}. "
        "Every assertion in this file is vacuous if this set is wrong."
    )


def test_the_baked_and_mounted_extraction_is_non_vacuous():
    assert {"agent", "gtm_core"} <= _baked(), "Dockerfile COPY parse drifted"
    assert {"plugin", "profiles"} <= _mounted(), "compose source: parse drifted"


# ── T2: the filter is closed in BOTH directions ──────────────────────────────


def test_every_baked_or_mounted_path_triggers_a_deploy():
    """Forward: a surface that ships must be able to trigger the deploy that ships it."""
    shipped = (_baked() | _mounted()) - _NOT_A_TRIGGER
    missing = undelivered(shipped, _filter_entries())
    assert not missing, (
        f"{sorted(missing)} is baked into the image or bind-mounted, but is absent from "
        "deploy.yml's paths: filter — so changing it triggers no deploy and the box keeps "
        "running the old copy. Add it to the filter, or add it to _NOT_A_TRIGGER with a "
        "comment saying why it is deliberately not a trigger."
    )


def test_the_host_installed_exemption_is_earned():
    """`systemd/` may skip container delivery only because the deploy installs it to /etc.

    Without this, `_HOST_INSTALLED` would be a comment that permanently excuses an
    orphaned filter entry — the exact shape (a claim in prose, nothing behind it) that
    this file exists to catch.
    """
    assert "systemd/install.sh" in _deploy_script(), (
        "systemd/ is exempted from container delivery on the grounds that the deploy "
        "installs it to the host — but the deploy no longer runs systemd/install.sh, so "
        "the exemption is now excusing a filter entry that ships nowhere at all."
    )


def test_every_deploy_path_filter_entry_reaches_the_container():
    """Reverse: a filter entry that ships nothing is a comment pretending to be wiring."""
    delivered = _baked() | _mounted() | _BUILD_INPUTS | _HOST_INSTALLED
    orphaned = undelivered(_filter_entries(), delivered)
    assert not orphaned, (
        f"deploy.yml's paths: filter names {sorted(orphaned)}, but nothing COPYs or mounts "
        "it, so a change there rebuilds an image that never receives it. Either deliver the "
        "path (a read-only mount, per the plugin/ pattern) or drop the filter entry — and "
        "drop any comment claiming it is runtime-loaded."
    )


# ── T5: the deploy actually installs the units it pulls ──────────────────────


def test_the_deploy_script_installs_units_after_pulling():
    """`git pull` updates systemd/*.service on disk; only install.sh copies them to /etc."""
    script = _deploy_script()
    assert "systemd/install.sh" in script, (
        "the deploy script never runs systemd/install.sh, so repo unit files never reach "
        "/etc/systemd/system. This is the 2026-06-26 incident that install.sh's own header "
        "documents — the box ran Jun-16 units while the repo was Jun-22."
    )
    pull_at = script.index("git pull --ff-only")
    install_at = script.index("systemd/install.sh")
    assert install_at > pull_at, (
        "systemd/install.sh runs BEFORE git pull --ff-only, so it installs the previous "
        "revision's units — the same staleness the step exists to prevent."
    )


def test_the_deploy_script_runs_the_drift_check_unsuppressed():
    """A drift check whose failure is swallowed reports nothing.

    `check-drift.sh` also catches the case `install.sh` cannot: a missing gitignored
    `docker-compose.override.yml`, which once left the news radar resolving nothing for
    ~13 days.
    """
    script = _deploy_script()
    assert "check-drift.sh" in script, (
        "the deploy never runs systemd/check-drift.sh. systemd/README.md says to wire it "
        "into cron/CI; it never was, which is why the drift it guards went unnoticed."
    )
    for line in script.splitlines():
        if "check-drift.sh" not in line:
            continue
        stripped = line.strip()
        assert not stripped.startswith("-"), f"drift check failure is suppressed: {stripped!r}"
        assert "|| true" not in stripped, f"drift check failure is swallowed: {stripped!r}"


# ── checker self-tests: prove the checkers fire on a known-bad workflow ──────


def test_the_forward_checker_detects_a_synthetic_unshipped_surface():
    workflow = "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n\njobs:\n  deploy:\n"
    baked = copy_targets("COPY widgetsvc/ ./widgetsvc/\nCOPY sprockets/ ./sprockets/\n")
    assert undelivered(baked, deploy_path_filter(workflow)) == {"sprockets"}


def test_the_reverse_checker_detects_a_synthetic_orphaned_filter_entry():
    workflow = "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n      - 'gadgets/**'\n\njobs:\n"
    delivered = copy_targets("COPY widgetsvc/ ./widgetsvc/\n")
    assert undelivered(deploy_path_filter(workflow), delivered) == {"gadgets"}


def test_the_filter_parser_stops_at_the_end_of_the_paths_block():
    """A following key must not be swallowed as a path entry."""
    workflow = (
        "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n"
        "\nconcurrency:\n  group: deploy\n      - 'not-a-path'\n"
    )
    assert deploy_path_filter(workflow) == {"widgetsvc"}
