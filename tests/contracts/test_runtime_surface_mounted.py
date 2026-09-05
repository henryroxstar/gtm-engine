"""Contract: every repo path the RUNTIME resolves must actually exist in the container.

The defect this closes (measured 2026-08-27): ``packs/`` is neither ``COPY``'d in the
Dockerfile nor bind-mounted in ``docker-compose.yml``, while two runtime call sites build
paths under it from ``cfg.repo_root`` — ``agent/__main__.py`` (the ``--pack`` graph loader)
and ``agent/onboard.py`` (the onboarding wizard's pack roster). ``cfg.repo_root`` is
``/app`` in the container, so both resolve a directory that does not exist there. Seven
pack graphs, a fail-closed loader and a whole tenant-override layer were unreachable from
the only place they run — which is why no ``systemd`` unit uses ``--pack``: not an
oversight in the units, but a capability never reachable from where they run.

Same shape as ``test_deploy_env_sync.py``: derive both sides from source and diff them, so
the check cannot go stale by hand-listing. Same family as ``test_email_quality_wired.py``
— assert WIRING, not behaviour, and be annoying to delete.

Fixtures: every synthetic tree below is invented (fictional module and directory names
only), per the third-party-PII rule (docs/RULES.md §R9).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts._deploy_surface import (
    copy_targets,
    mount_sources,
    repo_root_literals,
    undelivered,
)

REPO = Path(__file__).resolve().parents[2]

if not (REPO / "docker-compose.yml").exists():
    # Belt-and-braces only. This guard used to claim docker-compose.yml is excluded from
    # the OSS carve — it is not: `oss/overlays/docker-compose.yml` replaces the private
    # one, so the file is always present and this branch has never fired. That mattered,
    # because the assertions below then ran against the OVERLAY and found it two mounts
    # behind (packs/, CLAUDE.md) — real drift in the shipped compose, fixed rather than
    # skipped. Kept as a guard against a future cut that drops the overlay entirely.
    pytest.skip("no docker-compose.yml in this tree", allow_module_level=True)

#: Trees whose modules run INSIDE the gtm-agent container.
_RUNTIME_TREES = ("agent", "gtm_core", "cockpit")


def _delivered() -> set[str]:
    return copy_targets((REPO / "Dockerfile").read_text(encoding="utf-8")) | mount_sources(
        (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    )


def _resolved() -> set[str]:
    out: set[str] = set()
    for tree in _RUNTIME_TREES:
        if (REPO / tree).exists():
            out |= repo_root_literals(REPO / tree)
    return out


# ── non-vacuity: a broken extractor must fail LOUD, not pass empty ────────────


def test_the_delivered_path_set_is_non_vacuous():
    delivered = _delivered()
    assert {"agent", "gtm_core", "plugin", "profiles"} <= delivered, (
        f"Dockerfile/compose extraction looks broken — got {sorted(delivered)}. "
        "Every assertion in this file is vacuous if this set is wrong."
    )


def test_the_repo_root_literal_extraction_is_non_vacuous():
    resolved = _resolved()
    assert "packs" in resolved, (
        'the AST matcher found no `repo_root / "packs"`, but agent/__main__.py and '
        "agent/onboard.py both build one. The matcher has drifted from the code's shape "
        "and this file is now asserting nothing."
    )


# ── T1: the contract ─────────────────────────────────────────────────────────


def test_every_repo_root_path_the_runtime_resolves_is_baked_or_mounted():
    """A path the runtime builds but the container never receives is a runtime error.

    Not a style rule: `--pack` and the onboarding wizard both fail outright on the VPS
    today because `packs/` is resolved and never delivered.
    """
    missing = undelivered(_resolved(), _delivered())
    assert not missing, (
        f"the runtime resolves {sorted(missing)} under cfg.repo_root, but no Dockerfile "
        "COPY and no compose bind-mount delivers it to the container. Add a read-only "
        "mount (the plugin/ pattern) or a COPY, or stop resolving the path."
    )


# ── T14: the SDK's declared project settings must be delivered too ───────────


@pytest.mark.private_tree  # `.claude/` is on the carve's must-not-ship list (oss-export.sh §6),
# so the public cut cannot satisfy the `.claude` half of this assertion by construction. The
# CLAUDE.md half IS satisfied there — the overlay compose mounts it — and agent/permissions.py
# remains the primary permission control in both trees; only the settings.json backstop differs.
def test_the_project_setting_sources_the_session_declares_are_delivered():
    """`setting_sources=["project"]` with `cwd=repo_root` loads CLAUDE.md + .claude/.

    `agent/session.py` sets both and its own comment says this loads "the repo's
    CLAUDE.md". Declaring a settings source the container never receives is the same
    defect as resolving an unmounted path — it just fails quietly instead of loudly.
    """
    session = (REPO / "agent" / "session.py").read_text(encoding="utf-8")
    if '"setting_sources": ["project"]' not in session:
        pytest.skip("agent/session.py no longer declares project setting_sources")

    delivered = _delivered()
    missing = {p for p in ("CLAUDE.md", ".claude") if p not in delivered}
    assert not missing, (
        f"agent/session.py declares setting_sources=['project'] with cwd=cfg.repo_root, "
        f"but {sorted(missing)} reaches no container. The tenant BINDING still holds "
        "(agent/profiles.py system_prompt_for() is injected separately), but the rest of "
        "the CLAUDE.md contract and the .claude/settings.json permission backstop do not."
    )


# ── checker self-tests: prove the checkers fire on a known-bad tree ──────────


def test_the_checker_self_test_detects_a_synthetic_undelivered_read(tmp_path: Path):
    """Write a module that resolves a path nothing delivers; the checker must catch it."""
    pkg = tmp_path / "widgetsvc"
    pkg.mkdir()
    (pkg / "loader.py").write_text(
        'def load(cfg):\n    return cfg.repo_root / "sprockets" / "index.toml"\n',
        encoding="utf-8",
    )
    resolved = repo_root_literals(pkg)
    assert resolved == {"sprockets"}

    delivered = copy_targets("COPY widgetsvc/ ./widgetsvc/\n") | mount_sources(
        "      - type: bind\n        source: ./gadgets\n"
    )
    assert delivered == {"widgetsvc", "gadgets"}
    assert undelivered(resolved, delivered) == {"sprockets"}


def test_the_checker_self_test_passes_a_synthetic_delivered_read(tmp_path: Path):
    """The negative control: a resolved path that IS mounted must not be reported."""
    pkg = tmp_path / "widgetsvc"
    pkg.mkdir()
    (pkg / "loader.py").write_text(
        'def load(cfg):\n    return cfg.repo_root / "sprockets"\n', encoding="utf-8"
    )
    delivered = mount_sources("      - type: bind\n        source: ./sprockets\n")
    assert undelivered(repo_root_literals(pkg), delivered) == set()


def test_a_docstring_mentioning_a_directory_does_not_false_positive(tmp_path: Path):
    """AST, not grep: prose naming a path is not path construction."""
    pkg = tmp_path / "widgetsvc"
    pkg.mkdir()
    (pkg / "docs_only.py").write_text(
        '"""This module never touches cfg.repo_root / \\"sprockets\\" at all."""\n'
        "# cfg.repo_root / 'gadgets' in a comment is also not a read\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )
    assert repo_root_literals(pkg) == set()
