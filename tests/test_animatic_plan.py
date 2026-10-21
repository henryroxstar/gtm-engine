"""C12 — the animatic's PURE half: joining a storyboard to a shot list, and the verdict.

No ffmpeg, no pixels. These are the checks that must hold before a single frame is encoded, and
the reason they are separate from `tests/media/test_animatic.py` is that every one of them is
about two files agreeing — which is cheaper to get wrong than to render.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.animatic import AnimaticError, plan, verdict


def _sb(*entries) -> dict:
    return {"entries": list(entries)}


def _entry(n: int, image: str = "", ratio: str = "9:16") -> dict:
    return {"shot": n, "image_path": image or f"storyboard-{n:02d}.png", "aspect_ratio": ratio}


def _shots(*durations: float) -> dict:
    return {"shots": [{"n": i, "duration_s": d} for i, d in enumerate(durations, 1)]}


class _M:
    """A NarrationLineMeasurement stand-in — only the fields the verdict reads.

    `declared_len_s` is the VOICED span, not the file length. The verdict measures the gap between
    the last word and the cut that follows, so those are the two numbers it needs.
    """

    def __init__(self, voice_onset_s: float, declared_len_s: float) -> None:
        self.voice_onset_s = voice_onset_s
        self.declared_len_s = declared_len_s


# ── plan(): the two files describe one film, or neither is used ───────────────────────────────


def test_a_matched_storyboard_and_shot_list_plan_cleanly():
    segments = plan(_sb(_entry(1), _entry(2)), _shots(2.0, 3.0))
    assert [(s.n, s.len_s) for s in segments] == [(1, 2.0), (2, 3.0)]
    assert all(s.ratio == "9:16" for s in segments)


def test_durations_come_from_the_shot_list_and_are_never_adjusted():
    """An animatic whose timings were massaged to look right tells you nothing about the film."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(0.4, 41.7))
    assert [s.len_s for s in segments] == [0.4, 41.7]


def test_a_storyboard_entry_with_no_matching_shot_is_refused():
    with pytest.raises(AnimaticError, match="no matching shot"):
        plan(_sb(_entry(1), _entry(9)), _shots(2.0))


def test_a_shot_with_no_storyboard_still_is_refused_by_name():
    """Silently building a shorter animatic is the failure this catches: it would look fine."""
    with pytest.raises(AnimaticError, match=r"shot\(s\) \[2\]"):
        plan(_sb(_entry(1)), _shots(2.0, 3.0))


def test_an_entry_that_names_no_shot_is_refused():
    with pytest.raises(AnimaticError, match="names no shot"):
        plan(_sb({"image_path": "x.png"}), _shots(2.0))


def test_an_entry_with_no_image_path_is_refused():
    with pytest.raises(AnimaticError, match="no `image_path`"):
        plan(_sb({"shot": 1}), _shots(2.0))


@pytest.mark.parametrize("bad", [0, -1.0, None, "two seconds"])
def test_a_shot_with_no_usable_duration_is_refused(bad):
    shots = {"shots": [{"n": 1, "duration_s": bad}]}
    with pytest.raises(AnimaticError, match="usable `duration_s`"):
        plan(_sb(_entry(1)), shots)


def test_mixed_aspect_ratios_are_refused_before_any_encode():
    """A stitch would pad the odd ones out, misrepresenting the very framing being judged."""
    with pytest.raises(AnimaticError, match="mixes aspect ratios"):
        plan(_sb(_entry(1, ratio="9:16"), _entry(2, ratio="16:9")), _shots(2.0, 2.0))


def test_an_empty_storyboard_or_shot_list_is_refused():
    with pytest.raises(AnimaticError, match="no `entries`"):
        plan({"entries": []}, _shots(2.0))
    with pytest.raises(AnimaticError, match="no `shots`"):
        plan(_sb(_entry(1)), {"shots": []})


# ── verdict(): three questions, and the difference between "no" and "unmeasurable" ────────────


def test_a_hook_landing_inside_the_first_shot_passes():
    segments = plan(_sb(_entry(1), _entry(2)), _shots(3.0, 3.0))
    out = verdict(segments, measurements=[_M(0.4, 1.0), _M(3.5, 1.0)])
    assert out["hook_in_first_beat"] is True
    assert out["hook_onset_s"] == 0.4


def test_a_hook_landing_after_the_first_shot_fails():
    segments = plan(_sb(_entry(1), _entry(2)), _shots(2.0, 3.0))
    assert verdict(segments, measurements=[_M(3.0, 1.0)])["hook_in_first_beat"] is False


def test_an_absent_read_is_reported_as_absent_and_not_as_a_failure():
    """ "No read" and "a read that does not fit" are different states, and collapsing them is how
    absent gets read as fine."""
    segments = plan(_sb(_entry(1)), _shots(2.0))
    out = verdict(segments)
    assert out["hook_in_first_beat"] is None
    assert out["read"] == "absent"
    assert "read_fits" not in out


def test_a_line_landing_on_the_cut_is_named():
    """Cuts at 3.0s and 6.0s. The second line ends at 5.95s — 0.05s before its cut."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(3.0, 3.0))
    out = verdict(segments, measurements=[_M(0.2, 1.0), _M(3.2, 2.75)])
    assert out["read_fits"] is False
    assert out["tight_lines"] == [{"line": 2, "ends_at_s": 5.95, "cut_at_s": 6.0, "gap_s": 0.05}]


def test_a_read_with_room_at_every_cut_passes():
    """Same cuts; both lines finish well clear of them."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(3.0, 3.0))
    out = verdict(segments, measurements=[_M(0.2, 1.0), _M(3.2, 1.0)])
    assert out["read_fits"] is True and out["tight_lines"] == []


def test_a_line_with_room_in_its_FILE_but_none_before_the_CUT_is_still_tight():
    """The distinction that matters, and the one the first draft got wrong: a line can have a
    second of silence inside its own audio and still sound clipped, because the PICTURE cuts a
    frame after the last word. The animatic exists to expose exactly that."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(2.0, 2.0))
    out = verdict(segments, measurements=[_M(0.1, 1.85)])  # last word at 1.95, cut at 2.0
    assert out["read_fits"] is False, "a word landing on the cut was read as fitting"
    assert out["tight_lines"][0]["cut_at_s"] == 2.0


def test_a_line_that_CROSSES_a_cut_is_measured_against_the_NEXT_one():
    """Narration is a track laid over the picture, so a line running across a cut is normal —
    that is what distinguishes it from the per-shot spoken lane. Measuring it against the cut it
    passed through would report every long line as clipped."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(2.0, 2.0))
    out = verdict(segments, measurements=[_M(0.1, 1.95)])  # last word at 2.05, past the 2.0 cut
    assert out["read_fits"] is True
    assert out["tight_lines"] == []


def test_the_cover_check_compares_against_the_first_frame():
    segments = plan(
        _sb(_entry(1, "storyboard-01.png"), _entry(2, "storyboard-02.png")), _shots(2.0, 2.0)
    )
    brief = {"decisions": {"cover": {"value": {"still": "storyboard-01.png"}}}}
    assert verdict(segments, brief=brief)["cover_is_frame_one"] is True

    wrong = {"decisions": {"cover": {"value": {"still": "storyboard-02.png"}}}}
    assert verdict(segments, brief=wrong)["cover_is_frame_one"] is False


def test_an_absent_brief_reports_the_cover_as_unmeasurable():
    segments = plan(_sb(_entry(1)), _shots(2.0))
    out = verdict(segments)
    assert out["cover_is_frame_one"] is None and out["cover"] == "absent"


def test_a_total_duration_disagreement_is_reported_and_not_corrected():
    """A drift means two files disagree; silently preferring one is how that survives."""
    segments = plan(_sb(_entry(1), _entry(2)), _shots(2.0, 3.0))
    out = verdict(segments, declared_total_s=7.0)
    assert out["declared_total_vs_sum"]["agrees"] is False
    assert out["declared_total_vs_sum"]["drift_s"] == 2.0
    assert out["total_s"] == 5.0, "the verdict adjusted a duration instead of reporting the drift"


def test_a_stretched_mux_is_surfaced_rather_than_hidden():
    """A non-1.0 atempo means our OWN numbers disagreed — on an animatic the durations are ours."""

    class _R:
        strategy = "atempo"
        atempo = 1.04

    segments = plan(_sb(_entry(1)), _shots(2.0))
    assert verdict(segments, mux_result=_R())["mux"] == {"strategy": "atempo", "atempo": 1.04}


# ── C12-T7: it cannot become a publishable thing ──────────────────────────────────────────────


def test_the_module_never_imports_the_publish_path():
    """An animatic is six stills where a film was promised. It must not be reachable from a gate
    that ships bytes, and the cheapest way to guarantee that is for the code not to know how.

    Asserted on the IMPORT GRAPH, not on the file's text: the module docstring names
    ``agent.publish`` in order to explain this very rule, and a text scan would either fail on
    that or force the explanation out of the file. An import is the thing that gives code the
    capability; a sentence about one does not.
    """
    import ast

    source_path = Path(__import__("gtm_core.animatic", fromlist=["x"]).__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    reaches_publish = sorted(m for m in imported if m.split(".")[0] == "agent")
    assert not reaches_publish, (
        f"gtm_core.animatic imports from the agent package: {reaches_publish}. The publish gate "
        "lives there, and an animatic must not be able to reach it."
    )

    # The two markers that would make an animatic representable as something shippable. Checked as
    # the literal SENTINELS the parser looks for, so ordinary prose about gates and identity is
    # unaffected.
    body = source_path.read_text(encoding="utf-8")
    for sentinel in ("\u27e6IDENTITY\u27e7", "\u27e6GATE:", "\u27e6POST\u27e7"):
        assert sentinel not in body, f"the animatic can emit {sentinel!r}"


def test_the_verdict_carries_no_identity_or_disclosure_keys():
    segments = plan(_sb(_entry(1)), _shots(2.0))
    out = verdict(segments, measurements=[_M(0.1, 1.0)])
    assert not [k for k in out if "identity" in k.lower() or "disclos" in k.lower()]
