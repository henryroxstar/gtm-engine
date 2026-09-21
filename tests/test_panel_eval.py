"""The avatar panel harness — including the tests that prove it can FAIL.

A pre-registered evaluation whose harness has only ever been exercised on passing data is not a
gate. These tests drive it to KILL on each threshold separately, and check the control readout,
because the single most expensive lesson in this pipeline was a decision made on a proxy that
never disagreed with the thing it stood in for.
"""

from __future__ import annotations

import pytest

from gtm_core import panel_eval as pe


def _clips(real: int = 4, avatar: int = 4, hybrid: int = 4) -> list[pe.Clip]:
    out = []
    for cond, n in (("real", real), ("avatar", avatar), ("hybrid", hybrid)):
        out += [pe.Clip(clip_id=f"{cond}-{i}", condition=cond) for i in range(1, n + 1)]
    return out


def _panel(sheet, *, judges=5, called_real=True, naturalness=4, tell="", condition="avatar"):
    slots = [c.slot for c in sheet if c.condition == condition]
    return [
        pe.Judgement(
            judge_id=f"j{j}", slot=s, called_real=called_real, naturalness=naturalness, tell=tell
        )
        for j in range(judges)
        for s in slots
    ]


# --- the sheet -----------------------------------------------------------------------------


def test_the_sheet_never_carries_the_condition():
    """The judge-facing artifact must not leak which clips are supposed to be synthetic.

    Note the word "real" DOES appear on the sheet — as the answer the judge is asked to give
    ("real / generated"). That is the question, not the answer key. What must never appear is a
    clip id (they are named by condition) or any per-row condition column.
    """
    sheet, _key = pe.build_sheet(_clips(), salt="s")
    rendered = pe.render_sheet(sheet)

    for clip in sheet:
        assert clip.clip_id not in rendered, f"clip id {clip.clip_id!r} leaks the condition"

    # Every data row is `| <slot> | | | |` — slot number and three empty cells, nothing else.
    rows = [ln for ln in rendered.splitlines() if ln.startswith("| ") and ln.rstrip().endswith("|")]
    data_rows = [ln for ln in rows if ln.split("|")[1].strip().isdigit()]
    assert len(data_rows) == len(sheet)
    for row in data_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[1:] == ["", "", ""], f"a data row carries content: {row!r}"


def test_the_key_is_returned_separately_from_the_sheet():
    sheet, key = pe.build_sheet(_clips(), salt="s")
    assert set(key) == {c.slot for c in sheet}
    assert set(key.values()) == set(pe.CONDITIONS)


def test_ordering_is_reproducible_from_the_inputs():
    """A disputed result must be re-derivable; this is why it is not random.shuffle."""
    a, _ = pe.build_sheet(_clips(), salt="same")
    b, _ = pe.build_sheet(_clips(), salt="same")
    assert [c.clip_id for c in a] == [c.clip_id for c in b]


def test_a_different_salt_reorders():
    a, _ = pe.build_sheet(_clips(), salt="one")
    b, _ = pe.build_sheet(_clips(), salt="two")
    assert [c.clip_id for c in a] != [c.clip_id for c in b]


def test_the_sheet_is_shuffled_not_grouped_by_condition():
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    conditions = [c.condition for c in sheet]
    assert len(set(conditions[:4])) > 1, f"first four slots are one block: {conditions}"


def test_a_panel_without_the_real_control_is_refused():
    """Without genuine footage the panel measures suspicion, not the avatar."""
    with pytest.raises(pe.PanelError, match="every condition needs"):
        pe.build_sheet(_clips(real=0), salt="s")


def test_a_panel_with_one_avatar_clip_is_refused():
    with pytest.raises(pe.PanelError, match="every condition needs"):
        pe.build_sheet(_clips(avatar=1), salt="s")


# --- the verdict: it must be able to fail ----------------------------------------------------


def test_a_convincing_avatar_passes():
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, called_real=True, naturalness=4) + _panel(
        sheet, called_real=True, naturalness=5, condition="real"
    )
    verdict = pe.score_panel(sheet, judgements)
    assert verdict.passed, verdict.reasons


def test_judges_who_can_tell_produce_a_kill():
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, called_real=False, naturalness=4) + _panel(
        sheet, called_real=True, naturalness=5, condition="real"
    )
    verdict = pe.score_panel(sheet, judgements)
    assert not verdict.passed
    assert any("Judges can tell" in r for r in verdict.reasons)


def test_a_shrug_score_produces_a_kill_even_when_judges_cannot_tell():
    """Indistinguishable but lifeless is still a fail — 3 is 'fine, I guess'."""
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, called_real=True, naturalness=3) + _panel(
        sheet, called_real=True, naturalness=5, condition="real"
    )
    verdict = pe.score_panel(sheet, judgements)
    assert not verdict.passed
    assert any("naturalness" in r for r in verdict.reasons)


def test_one_tell_named_by_most_judges_kills_a_clip_that_otherwise_passes():
    """The averages-hide-it case: good scores, and everyone spotted the same wrong thing."""
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, called_real=True, naturalness=4, tell="the mouth") + _panel(
        sheet, called_real=True, naturalness=5, condition="real"
    )
    verdict = pe.score_panel(sheet, judgements)
    assert not verdict.passed
    assert verdict.dominant_tell is not None and verdict.dominant_tell[0] == "the mouth"
    assert any("named the same tell" in r for r in verdict.reasons)


def test_a_tell_is_counted_once_per_judge_not_once_per_clip():
    """Otherwise one talkative judge outvotes the panel."""
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    chatty = [
        pe.Judgement(judge_id="j0", slot=c.slot, called_real=True, naturalness=4, tell="the hands")
        for c in sheet
        if c.condition == "avatar"
    ]
    quiet = _panel(sheet, judges=5, called_real=True, naturalness=4)
    quiet = [j for j in quiet if j.judge_id != "j0"]
    verdict = pe.score_panel(sheet, chatty + quiet + _panel(sheet, condition="real"))
    assert verdict.dominant_tell is not None
    # one of five judges named it, not four of four avatar clips
    assert verdict.dominant_tell[1] <= 0.25, verdict.dominant_tell


def test_too_few_judges_is_refused_however_good_the_scores():
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, judges=2, called_real=True, naturalness=5)
    verdict = pe.score_panel(sheet, judgements)
    assert not verdict.passed
    assert any("judge(s)" in r for r in verdict.reasons)


def test_a_suspicious_panel_is_reported_rather_than_credited_to_the_avatar():
    """Judges who call genuine footage synthetic invalidate the run in BOTH directions."""
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    judgements = _panel(sheet, called_real=True, naturalness=4) + _panel(
        sheet, called_real=False, naturalness=4, condition="real"
    )
    verdict = pe.score_panel(sheet, judgements)
    assert not verdict.passed
    assert any("CONTROL WARNING" in r for r in verdict.reasons)


def test_a_judgement_for_a_slot_not_on_the_sheet_is_refused():
    sheet, _ = pe.build_sheet(_clips(), salt="s")
    bogus = [pe.Judgement(judge_id="j0", slot=999, called_real=True, naturalness=4)]
    with pytest.raises(pe.PanelError, match="not on the sheet"):
        pe.score_panel(sheet, bogus)


def test_naturalness_outside_one_to_five_is_refused():
    with pytest.raises(pe.PanelError, match="naturalness must be"):
        pe.Judgement(judge_id="j", slot=1, called_real=True, naturalness=9)


def test_an_unknown_condition_is_refused():
    with pytest.raises(pe.PanelError, match="unknown condition"):
        pe.Clip(clip_id="x", condition="deepfake")


# --- the pre-registration itself ---------------------------------------------------------------


def test_the_thresholds_are_the_pre_registered_ones():
    """This test IS the pre-registration. Changing a number here is a visible diff with a
    reviewer, which is the entire mechanism — not a judgement made while staring at a
    disappointing result."""
    assert pe.PASS_REAL_RATE_MIN == 0.50
    assert pe.PASS_NATURALNESS_MIN == 3.5
    assert pe.KILL_TELL_SHARE == 0.60
    assert pe.MIN_JUDGES == 5
    assert pe.MIN_CLIPS_PER_CONDITION == 3
    assert pe.CONDITIONS == ("real", "avatar", "hybrid")


# --- the CLI (PS4.1) ---------------------------------------------------------------------------


def test_panel_eval_cli_build_sheet_and_score(tmp_path):
    """Test CLI commands build-sheet and score end-to-end."""
    import json
    from dataclasses import asdict

    clips = _clips(real=4, avatar=4, hybrid=4)
    clips_file = tmp_path / "clips.json"
    clips_file.write_text(json.dumps([asdict(c) for c in clips]), encoding="utf-8")

    out_sheet = tmp_path / "sheet.md"
    out_key = tmp_path / "key.json"

    # 1. Run build-sheet
    rc = pe.main(
        [
            "build-sheet",
            "--clips",
            str(clips_file),
            "--salt",
            "test-salt-123",
            "--out-sheet",
            str(out_sheet),
            "--out-key",
            str(out_key),
        ]
    )
    assert rc == 0
    assert out_sheet.is_file()
    assert out_key.is_file()

    # 2. Build sheet object and mock judgements
    sheet_data, _ = pe.build_sheet(clips, salt="test-salt-123")
    sheet_file = tmp_path / "sheet.json"
    sheet_file.write_text(json.dumps([asdict(c) for c in sheet_data]), encoding="utf-8")

    # 5 judges, passing
    judgements = _panel(
        sheet_data, judges=5, called_real=True, naturalness=4, condition="avatar"
    ) + _panel(sheet_data, judges=5, called_real=True, naturalness=5, condition="real")
    judgements_file = tmp_path / "judgements.json"
    judgements_file.write_text(json.dumps([asdict(j) for j in judgements]), encoding="utf-8")

    # 3. Run score
    rc = pe.main(
        [
            "score",
            "--sheet",
            str(sheet_file),
            "--judgements",
            str(judgements_file),
            "--json",
        ]
    )
    assert rc == 0


def test_panel_eval_docstring_and_toml_drift():
    """Q2: Docstring parity between panel_eval.py and render_engines.toml must hold verbatim."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    normative_sentence = (
        "It answers indistinguishability and naturalness, never impact — "
        "a natural, undetectable, inert film passes it."
    )

    panel_eval_src = (repo_root / "gtm_core" / "panel_eval.py").read_text(encoding="utf-8")
    render_engines_src = (repo_root / "gtm_core" / "render_engines.toml").read_text(
        encoding="utf-8"
    )

    assert normative_sentence in panel_eval_src, (
        "gtm_core/panel_eval.py docstring missing normative sentence from Q2"
    )
    assert normative_sentence in render_engines_src, (
        "gtm_core/render_engines.toml missing normative sentence from Q2"
    )


def test_panel_eval_reference_run_fixtures():
    """Q2: Reference run fixtures execute and produce PASS and FAIL verdicts."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    fixtures_dir = repo_root / "tests" / "fixtures" / "panel_eval"

    clips_file = fixtures_dir / "clips.json"
    pass_file = fixtures_dir / "judgements_pass.json"
    fail_file = fixtures_dir / "judgements_fail.json"

    assert clips_file.is_file(), f"missing clips fixture {clips_file}"
    assert pass_file.is_file(), f"missing pass fixture {pass_file}"
    assert fail_file.is_file(), f"missing fail fixture {fail_file}"

    sheet_raw = json.loads(clips_file.read_text(encoding="utf-8"))
    sheet = [pe.Clip(**c) for c in sheet_raw]
    assert len(sheet) == 9

    # Verify PASS fixture
    judgements_pass = [pe.Judgement(**j) for j in json.loads(pass_file.read_text(encoding="utf-8"))]
    verdict_pass = pe.score_panel(sheet, judgements_pass)
    assert verdict_pass.passed is True, f"expected PASS, got {verdict_pass.reasons}"
    assert verdict_pass.judges == 5

    # Verify FAIL fixture
    judgements_fail = [pe.Judgement(**j) for j in json.loads(fail_file.read_text(encoding="utf-8"))]
    verdict_fail = pe.score_panel(sheet, judgements_fail)
    assert verdict_fail.passed is False, "expected FAIL for judgements_fail fixture"
    assert len(verdict_fail.reasons) > 0
