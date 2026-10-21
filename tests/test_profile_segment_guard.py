"""Tenant-boundary guard: every ``--profile`` path builder rejects a traversal segment.

``profile`` arrives from a CLI flag / env and is joined straight into a filesystem
path under the content root. CLAUDE.md makes directory traversal the highest-risk
tenant error, so each entry point guards it with ``gtm_core.paths._safe_segment``
BEFORE any path is built — not after, and not by relying on the target happening
not to exist.

One test per module rather than a shared loop: these are independent entry points
that regress independently, and a loop would hide which one lost its guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import outcomes, prospects_consolidate, prospects_state

#: Segments that must never reach a path join. Mirrors ``_safe_segment``'s contract.
UNSAFE = ["../../etc", "..", ".", "a/b", "a\\b", "evil\x00", ""]

ROOT = Path("/tmp/gtm-guard-test-root")


@pytest.mark.parametrize("profile", UNSAFE)
def test_outcomes_path_rejects_unsafe_profile(profile):
    with pytest.raises(ValueError, match="unsafe profile"):
        outcomes.outcomes_path(ROOT, profile)


@pytest.mark.parametrize("profile", UNSAFE)
def test_prospects_latest_path_rejects_unsafe_profile(profile):
    with pytest.raises(ValueError, match="unsafe profile"):
        prospects_state.latest_path(profile, ROOT)


@pytest.mark.parametrize("profile", UNSAFE)
def test_prospects_dir_rejects_unsafe_profile(profile):
    with pytest.raises(ValueError, match="unsafe profile"):
        prospects_consolidate._prospects_dir(profile, ROOT)


@pytest.mark.parametrize("profile", UNSAFE)
def test_accounts_dir_rejects_unsafe_profile(profile):
    with pytest.raises(ValueError, match="unsafe profile"):
        prospects_consolidate._accounts_dir(profile, ROOT)


def test_ordinary_profile_names_still_resolve():
    """The guard must be invisible to every real profile name."""
    assert outcomes.outcomes_path(ROOT, "acme") == ROOT / "acme" / "outcomes.jsonl"
    assert prospects_state.latest_path("globex", ROOT) == (
        ROOT / "globex" / "prospects" / "latest.json"
    )
    assert prospects_consolidate._prospects_dir("initech", ROOT) == ROOT / "initech" / "prospects"
    assert prospects_consolidate._accounts_dir("_template", ROOT) == (
        ROOT / "_template" / "accounts"
    )


def test_derived_paths_inherit_the_guard():
    """Guarding the base builder must cover everything derived from it."""
    for builder in (
        prospects_consolidate._sequences_dir,
        prospects_consolidate.ready_to_load_path,
        prospects_consolidate.needs_verification_path,
    ):
        with pytest.raises(ValueError, match="unsafe profile"):
            builder("../escape", ROOT)
