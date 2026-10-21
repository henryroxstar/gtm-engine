"""An overlay must change WHAT YOU SAY, not only who you say it to (Phase B / P1).

The targeting half of `OVERLAYABLE` shipped 2026-09-21; the messaging half was on the
allowlist with no reader that could be told about an overlay, so an "experiment" swapped
the rubric and silently kept the live hook bank. `tests/contracts/test_overlay_reach.py`
proves every resolver now ACCEPTS the argument. This file proves the argument does
something: two deliberately different matrices, one spec, and a different answer.

Everything runs against a real temporary profile tree, never a mocked resolver — a mocked
`resolve_knowledge_file` and the code under test share one assumption and agree by
construction (§3.C, and the Fleet Phase A incident it cites).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.build_eval_sheet import _load_matrix_if_present
from gtm_core.content_quality.sources import load_hook_matrix
from gtm_core.hook_coverage.backlog import backlog
from gtm_core.hook_coverage.premise import load_premise_vocab
from gtm_core.hooks import hooks_matrix_path, hooks_toml_path, load_hooks
from gtm_core.paths import resolve_knowledge_file

PROFILE = "acme"
OVERLAY = "q4-copy-test"

_LIVE_MATRIX = """\
# Hook matrix

## Startup

| id | Persona | Signal to open on | Hook angle |
|---|---|---|---|
| h1 | CTO | Questionnaire arrived | Answer it in code once. |
"""

_EXPERIMENT_MATRIX = """\
# Hook matrix

## Startup

| id | Persona | Signal to open on | Hook angle |
|---|---|---|---|
| h9 | CTO | Shared credentials | Eighty secrets live in agent memory. |
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A real profile tree with a live knowledge dir and one experiment overlay."""
    knowledge = tmp_path / PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "hook-matrix.md").write_text(_LIVE_MATRIX, encoding="utf-8")
    (knowledge / "hooks.toml").write_text(
        'authoritative = "hooks.toml"\n\n[[hook]]\nid = "h1"\nangle = "live angle"\n',
        encoding="utf-8",
    )
    (knowledge / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 1\nterms = ["agent"]\n',
        encoding="utf-8",
    )

    experiment = tmp_path / PROFILE / "experiments" / OVERLAY
    experiment.mkdir(parents=True)
    (experiment / "hook-matrix.md").write_text(_EXPERIMENT_MATRIX, encoding="utf-8")
    (experiment / "hooks.toml").write_text(
        'authoritative = "hooks.toml"\n\n[[hook]]\nid = "h9"\nangle = "experimental angle"\n',
        encoding="utf-8",
    )
    (experiment / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 2\nterms = ["agent", "assistant"]\n',
        encoding="utf-8",
    )
    return tmp_path


# --- each of the four modules, with and without the overlay ------------------


def test_hooks_resolves_a_different_bank(tree):
    live = load_hooks(tree, PROFILE)
    arm = load_hooks(tree, PROFILE, overlay=OVERLAY)
    assert [h.id for h in live.hooks] == ["h1"]
    assert [h.id for h in arm.hooks] == ["h9"], (
        "the hook bank did not change — an experiment that cannot vary the copy is a "
        "targeting experiment wearing a messaging experiment's name"
    )


def test_the_two_path_helpers_point_at_different_files(tree):
    assert hooks_toml_path(tree, PROFILE) != hooks_toml_path(tree, PROFILE, overlay=OVERLAY)
    assert hooks_matrix_path(tree, PROFILE) != hooks_matrix_path(tree, PROFILE, overlay=OVERLAY)
    assert OVERLAY in str(hooks_matrix_path(tree, PROFILE, overlay=OVERLAY).parent)


def test_the_eval_sheets_matrix_changes(tree):
    live = _load_matrix_if_present(PROFILE, tree)
    arm = _load_matrix_if_present(PROFILE, tree, overlay=OVERLAY)
    assert {c.signal for c in live.cells.values()} == {"Questionnaire arrived"}
    assert {c.signal for c in arm.cells.values()} == {"Shared credentials"}


def test_the_content_quality_reader_changes(tree):
    live = load_hook_matrix(tree, PROFILE)
    arm = load_hook_matrix(tree, PROFILE, overlay=OVERLAY)
    assert [r["id"] for r in live] == ["h1"]
    assert [r["id"] for r in arm] == ["h9"]


def test_the_premise_vocabulary_changes(tree):
    assert load_premise_vocab(PROFILE, tree)["ships-agents"].min_distinct == 1
    assert load_premise_vocab(PROFILE, tree, overlay=OVERLAY)["ships-agents"].min_distinct == 2


# --- the negative control the test plan asks for -----------------------------


def test_the_same_spec_validates_differently_under_the_two_matrices(tree, tmp_path):
    """§4.7: run the SAME spec against two deliberately different matrices and assert the
    declared cell validates differently. A wired module no caller invokes with `overlay=`
    is the dead-code shape the backend Gate-2 incident produced — a fabricated state made
    every test pass against code nothing reached.
    """
    content = tmp_path / "content"
    seq = content / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    spec = "```\nCampaign: q4\nhook_cell: CTO × Shared credentials\n```\n\nBody.\n"
    (seq / "spec-a.md").write_text(spec, encoding="utf-8")
    (seq / "spec-a.csv").write_text("email\na@x.example\n", encoding="utf-8")
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "s1"\ncsv = "spec-a.csv"\nspec = "spec-a.md"\ncampaign = "q4"\n',
        encoding="utf-8",
    )

    live = backlog(PROFILE, content_root=content, profiles_root=tree)
    arm = backlog(PROFILE, content_root=content, profiles_root=tree, overlay=OVERLAY)

    # Against the LIVE matrix the spec declares a cell that grid does not hold, so the live
    # cell is untouched and shows up as backlog.
    assert [c.signal for c in live.unused] == ["Questionnaire arrived"]
    # Against the EXPERIMENT matrix the same spec declares a cell that exists — and is used.
    assert arm.unused == [], f"the overlay's own cell still reads as unused: {arm.unused}"
    assert arm.matrix_path != live.matrix_path


# --- what the overlay still may NOT do ---------------------------------------


def test_an_absent_overlay_file_falls_back_rather_than_failing(tree):
    """The one legitimate non-refusal, tested explicitly so it is not read as a bug.

    An overlay that ships a matrix and no premise vocabulary is the normal case — the
    resolver's rung ladder falls through to the profile level. That is different from an
    INVALID or EXPIRED overlay, which `experiments.admit` refuses before any of this runs.
    """
    (tree / PROFILE / "experiments" / OVERLAY / "premise-vocab.toml").unlink()
    vocab = load_premise_vocab(PROFILE, tree, overlay=OVERLAY)
    assert vocab["ships-agents"].min_distinct == 1, "it must fall back, not vanish"


@pytest.mark.parametrize("slug", ["../escape", "..\\escape", "/etc/passwd", "a\x00b"])
def test_a_traversing_overlay_slug_is_refused_before_any_read(tree, slug):
    """`_safe_segment` guards every overlay path segment. Directory traversal here is the
    highest-risk tenant error: an overlay slug that escaped would read another profile's
    hook bank into this profile's copy."""
    with pytest.raises(ValueError):
        resolve_knowledge_file(tree, PROFILE, "hook-matrix.md", overlay=slug)


def test_the_overlay_allowlist_did_not_grow():
    """P1 makes three already-admitted files reachable. It admits nothing new — a messaging
    experiment must not become a route to experimenting on a market or a tone."""
    from gtm_core import experiments

    assert "PROFILE.md" in experiments.REFUSED
    assert "voice.md" in experiments.REFUSED
    for name in ("hook-matrix.md", "hooks.toml", "premise-vocab.toml"):
        assert name in experiments.OVERLAYABLE
    assert not (set(experiments.OVERLAYABLE) & set(experiments.REFUSED))
