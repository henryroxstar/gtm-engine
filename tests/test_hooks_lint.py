"""Tests for gtm_core.hooks_lint — the hook bank pattern linter.

Historically covered only indirectly (via tests/test_hooks.py and hook_score's use of
lint_text). No dedicated test file existed before this one, and — separately — hooks_lint had
no CI gate at all: no shell lint script, no pytest module. `test_committed_banks_lint_clean`
below closes that hole by running every committed profile's hooks.toml through lint_bank() on
every `pytest tests/` run (gate 8 of the Definition of Done), rather than leaving the linter
wired to nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import hooks as hk
from gtm_core import hooks_lint as hl


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bank_with_beat(beat: hk.OpeningBeat, formats: list[str] | None = None) -> hk.HookBank:
    return hk.HookBank(
        hooks=[
            hk.Hook(
                id="test-hook",
                angle="A specific, evidenced angle",
                payoff_promise="A specific, evidenced payoff",
                formats=formats if formats is not None else [beat.format],
                opening_beats=[beat],
            )
        ]
    )


# --- pattern_id validation -----------------------------------------------------------------


def test_lint_accepts_known_pattern_id_matching_format():
    # "harsh-truth" supports only ["single"] (see gtm_core/tweet_patterns.toml).
    bank = _bank_with_beat(hk.OpeningBeat(format="single", text="...", pattern_id="harsh-truth"))
    assert hl.lint_bank(bank) == []


def test_lint_ignores_beats_without_pattern_id():
    bank = _bank_with_beat(hk.OpeningBeat(format="single", text="..."))
    assert hl.lint_bank(bank) == []


def test_lint_rejects_unknown_pattern_id():
    bank = _bank_with_beat(
        hk.OpeningBeat(format="single", text="...", pattern_id="not-a-real-pattern")
    )
    errors = hl.lint_bank(bank)
    assert len(errors) == 1
    assert "not-a-real-pattern" in errors[0]
    assert "X tweet-pattern catalog" in errors[0]


def test_lint_rejects_pattern_id_on_non_x_beat():
    """A pattern_id sitting on a linkedin-text or reel beat (never single/thread) is exactly
    the case this check exists to catch — the registry's own `formats` field makes it
    impossible for a pattern to legally claim either."""
    bank = _bank_with_beat(
        hk.OpeningBeat(format="linkedin-text", text="...", pattern_id="harsh-truth")
    )
    errors = hl.lint_bank(bank)
    assert len(errors) == 1
    assert "harsh-truth" in errors[0]
    assert "linkedin-text" in errors[0]


def test_lint_accepts_thread_only_pattern_on_thread_beat():
    # "story-thread" supports only ["thread"].
    bank = _bank_with_beat(
        hk.OpeningBeat(format="thread", text="1/ ...", pattern_id="story-thread")
    )
    assert hl.lint_bank(bank) == []


def test_lint_rejects_thread_only_pattern_on_single_beat():
    bank = _bank_with_beat(hk.OpeningBeat(format="single", text="...", pattern_id="story-thread"))
    errors = hl.lint_bank(bank)
    assert len(errors) == 1
    assert "story-thread" in errors[0]
    assert "single" in errors[0]


# --- CI gate: every committed bank must lint clean ------------------------------------------


@pytest.mark.private_tree  # carve ships profiles/_template only; the >=3 census is private
def test_committed_banks_lint_clean():
    """Every committed profile's hooks.toml must pass hl.lint_bank() with zero errors.

    hooks_lint has no other CI gate — no shell lint script, no other pytest coverage of
    lint_bank() against real content. This is what makes a backfilled `pattern_id` typo (wrong
    id, or a pattern attached to a format it doesn't support) fail CI instead of shipping
    silently, and it is what proves every committed tenant bank is lint-clean today, including
    after a pattern_id backfill lands.
    """
    # Not parametrized by literal profile name on purpose — tests/ is scanned for tenant
    # tokens in release-mode debrand lint (tests/lint/debrand_check.sh --release), and a
    # hardcoded real tenant slug here would trip it. Globbing keeps this test tenant-agnostic
    # while still covering every real profile, including the two the backfill touches.
    real_profiles_root = _repo_root() / "profiles"
    checked = 0
    for hooks_toml in sorted(real_profiles_root.glob("*/knowledge/hooks.toml")):
        profile = hooks_toml.parent.parent.name
        bank = hk.load_hooks(real_profiles_root, profile)
        errors = hl.lint_bank(bank)
        assert errors == [], f"profile {profile!r} hooks.toml fails lint_bank(): {errors}"
        checked += 1
    assert checked >= 3, "expected to find hooks.toml under at least 3 committed profiles"
