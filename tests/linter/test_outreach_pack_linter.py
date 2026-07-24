"""Outreach pack linter — regression tests (rules 2026-07-16).

The linter exists because a cold-email pack can pass word-level lint while violating
the drafting spec (unhedged gaps, mail-merge stems, "teardown" CTA, high template
share). These tests pin the checks that catch that. Tenant-specific lists (case-study
names, banned stems) are file-driven and empty by default, so tests that exercise
those two rules pass explicit fixtures.
"""

from __future__ import annotations

from outreach_pack_linter import (
    _GOOD,
    RULES_VERSION,
    lint_pack,
    parse_pack,
)


def _errors(text: str, **kwargs) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in lint_pack(text, **kwargs):
        if v.level == "ERROR":
            out.setdefault(v.rule, []).append(v.detail)
    return out


def test_good_block_passes() -> None:
    assert _errors(_GOOD) == {}


def test_missing_rules_version_fails_closed() -> None:
    text = _GOOD.replace(f"Rules-Version: {RULES_VERSION}\n\n", "")
    assert "rules-version-missing" in _errors(text)


def test_stale_rules_version_fails_closed() -> None:
    text = _GOOD.replace(RULES_VERSION, "2026-07-14")
    assert "rules-version-stale" in _errors(text)


def test_mail_merge_skeleton_is_rejected() -> None:
    stem = "share one service account per task"
    body = (
        "Hi Jane,\n\n"
        "Acme is shipping agents past the demo. My hunch, tell me if you've got this "
        "covered: those agents likely share one service account per task, so when one "
        "delegates to another, no one can tell which agent acted. "
        "Worth me sending the teardown?\n\nAlex"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Jane Doe · CEO, Acme\n**To:** jane@acme.com\n**Subject:** shipping agents\n\n"
        f"{body}\n\n---\n"
    )
    errs = _errors(text, banned_stems=(stem,))
    assert "banned-stem" in errs
    assert "cta-teardown" in errs


def test_unhedged_gap_fails() -> None:
    text = _GOOD.replace("My bet on the open piece:", "The open piece:")
    assert "hedge-missing" in _errors(text)


def test_template_share_ceiling_across_emails() -> None:
    shared = (
        "the same six gram sentence repeated verbatim across many emails proves the "
        "skeleton survived, and a second shared clause rides along here too. My hunch, "
        "tell me if you've got this covered: something structural. "
        "Acme2000 shipped Widget3000 last May. Want the one-pager?"
    )
    blocks = []
    for i, (first, dom) in enumerate(
        [("Ann", "a.com"), ("Bob", "b.com"), ("Cat", "c.com"), ("Dan", "d.com")], start=1
    ):
        blocks.append(
            f"### {i}. {first} X · CEO, {dom}\n**To:** {first.lower()}@{dom}\n"
            f"**Subject:** subject {'one two three four'.split()[i - 1]}\n\n"
            f"Hi {first},\n\n{shared}\n\nAlex\n\n---\n"
        )
    text = f"Rules-Version: {RULES_VERSION}\n\n" + "\n".join(blocks)
    assert "template-share" in _errors(text)


def test_same_company_clone_pair_rejected() -> None:
    body = (
        "Your four agents off the Series C put autonomous actors on regulated flows. "
        "My read, tell me if you've got this covered: the Activant2025 raise and the "
        "KYC4 rollout leave attribution open once agents delegate. Zest9 proved the "
        "shape with examiner-grade trails. Want the one-pager plus a short demo?"
    )
    text = (
        f"Rules-Version: {RULES_VERSION}\n\n"
        "### 1. Ann One · CISO, Acme\n**To:** ann@acme.com\n**Subject:** your four agents\n\n"
        f"Hi Ann,\n\n{body}\n\nAlex\n\n---\n\n"
        "### 2. Bob Two · GC, Acme\n**To:** bob@acme.com\n**Subject:** your four agents\n\n"
        f"Hi Bob,\n\n{body}\n\nAlex\n\n---\n"
    )
    errs = _errors(text)
    assert "same-company-subject" in errs
    assert "same-company-overlap" in errs


def test_parse_pack_shape() -> None:
    version, blocks = parse_pack(_GOOD)
    assert version == RULES_VERSION
    assert len(blocks) == 1
    assert blocks[0].first == "Dana"
    assert blocks[0].to == "dana@acmerobotics.dev"
