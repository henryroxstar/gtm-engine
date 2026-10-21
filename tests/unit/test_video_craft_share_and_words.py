"""Edge cases of the planned-static-share arithmetic and the on-screen word budget.

Test plan 3.A "Planned-static-share arithmetic (R5)" and "On-screen word budget (R6)", plus the
ratio keying of PRD §1.1 item 2. Thresholds are imported and offset, never typed. Fictional only.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gtm_core.video_craft")

from gtm_core.video_craft import craft_verdict as cv  # noqa: E402
from gtm_core.video_craft import hook, share, words  # noqa: E402
from gtm_core.video_craft.share import MIN_SHOTS_TO_JUDGE, STATIC_SHARE_WARN  # noqa: E402
from gtm_core.video_craft.words import ONSCREEN_WORDS_WARN  # noqa: E402
from tests.unit import video_craft_fixtures as fx  # noqa: E402

CEILING = ONSCREEN_WORDS_WARN["16:9"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    fx.isolate(monkeypatch)


# ── share.py ──────────────────────────────────────────────────────────────────────────────────


def _mostly_static(bad_duration: object) -> list[dict]:
    """One static shot whose duration is broken, then moving shots: dropping the broken shot from
    the denominator would turn this all-moving and read it as a pass."""
    still = fx.shot(1, "ui_mock", 1.0, scene="still-push")
    if bad_duration is None:
        del still["duration_s"]
    else:
        still["duration_s"] = bad_duration
    return [still, *(fx.shot(n, "photo_broll", 3.0) for n in (2, 3, 4))]


@pytest.mark.parametrize("bad", [None, 0, 0.0, -2.0, "3", True, float("nan")])
def test_an_unusable_duration_makes_the_share_unjudgeable_never_dropped(bad):
    shots = _mostly_static(bad)
    classes = ["static", "moving", "moving", "moving"]
    result = share.planned_static_share(shots, classes)
    assert result.share is None
    assert result.reason
    assert not result.warns
    # Nothing was summed: neither 0 s still nor 0 s moving was counted for the broken shot.
    assert (result.static_s, result.total_s) == (0.0, 0.0)


def test_the_denominator_is_the_sum_of_shot_durations():
    shots = [
        fx.shot(1, "ui_mock", 2.5, scene="still-push"),
        fx.shot(2, "photo_broll", 4.0),
        fx.shot(3, "presenter", 3.5, role="presenter"),
    ]
    document = fx.doc(shots)
    document["total_duration_s"] = 99.0  # the top-level total is NOT the denominator
    _f, _c, _w, result = hook.motion_findings(document["shots"])
    assert result.total_s == pytest.approx(10.0)
    assert result.static_s == pytest.approx(2.5)
    assert result.share == pytest.approx(2.5 / 10.0)


def test_screen_ui_duration_is_ignored_in_favour_of_the_shot():
    long_render = fx.shot(1, "ui_mock", 2.0, scene="still-push")
    long_render["production"]["screen_ui"]["duration_s"] = 60.0
    shots = [long_render, fx.shot(2, "photo_broll", 4.0), fx.shot(3, "photo_broll", 4.0)]
    _f, _c, _w, result = hook.motion_findings(shots)
    assert result.static_s == pytest.approx(2.0)
    assert result.total_s == pytest.approx(10.0)


def test_fewer_than_the_minimum_shots_is_too_short_to_judge():
    shots = [fx.shot(n, "ui_mock", 5.0, scene="still-push") for n in range(1, MIN_SHOTS_TO_JUDGE)]
    result = share.planned_static_share(shots, ["static"] * len(shots))
    assert result.share is None
    assert result.reason == "too short to judge"
    assert not result.warns


def test_the_minimum_is_the_boundary():
    shots = [
        fx.shot(n, "ui_mock", 5.0, scene="still-push") for n in range(1, MIN_SHOTS_TO_JUDGE + 1)
    ]
    result = share.planned_static_share(shots, ["static"] * len(shots))
    assert result.share == pytest.approx(1.0)
    assert result.share > STATIC_SHARE_WARN and result.warns


@pytest.mark.parametrize("case", ["too_short", "no_duration"])
def test_an_unjudgeable_share_is_reported_never_a_pass(case):
    if case == "too_short":
        shots = [
            fx.shot(n, "ui_mock", 5.0, scene="still-push") for n in range(1, MIN_SHOTS_TO_JUDGE)
        ]
    else:
        shots = [fx.shot(n, "ui_mock", 5.0, scene="still-push") for n in range(1, 5)]
        del shots[0]["duration_s"]
    document = fx.doc(shots)
    _findings, not_checked = hook.craft_findings(document)
    verdict = cv.craft_verdict(document)
    moves = next(line for line in verdict.lines if line.question == cv.LINE_MOVES)
    assert moves.status != "pass"
    assert any("judge" in n or "duration" in n for n in not_checked)


# ── words.py: counting ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("", 0),
        ("   ", 0),
        ("one", 1),
        ("two words", 2),
        ("real-time sync", 2),  # a hyphenated compound is one word
        ("$1,200 saved", 2),  # a figure is one token
        ("3.5x faster", 2),
        ("- -", 0),  # bare hyphens are not words
        ("一二三", 3),  # CJK per character
        ("日本語 text", 4),
        ("ひらがな", 4),
        ("한국어", 3),
        ("API連携", 3),  # a mixed token: its CJK characters plus one for the rest
    ],
)
def test_count_words(text, count):
    assert words.count_words(text) == count


def test_exactly_the_documented_fields_are_counted():
    shot = {
        "n": 1,
        "caption": fx.words(50),
        "spoken": fx.words(50),
        "visual": fx.words(50),
        "production": {
            "screen_ui": {
                "headline": "one two",
                "subline": "three",
                "eyebrow": "four",
                "badge": "five",
                "kicker": "six",
                "scene": "kinetic-type",
                "image": "not words on screen.png",
                "duration_s": 3,
            },
            "overlay": [{"text": "seven eight"}, {"text": "nine"}, {"position": "top"}],
            "captions": [{"text": fx.words(50)}],
        },
    }
    assert words.words_on_screen(shot) == 9
    assert words.words_on_screen(shot, diagram_labels=["ten", "eleven twelve"]) == 12


def test_a_single_overlay_object_is_counted_too():
    shot = {"production": {"overlay": {"text": "one two three"}}}
    assert words.words_on_screen(shot) == 3


def test_captions_and_spoken_never_reach_the_budget():
    heavy = fx.shot(1, "kinetic_type", 3.0, caption=fx.words(CEILING * 3))
    heavy["spoken"] = fx.words(CEILING * 3)
    findings, _ = hook.word_findings(fx.doc([heavy]), [heavy])
    assert findings == []


@pytest.mark.parametrize("kind", ["ui_mock", "diagram", "data_visual", "kinetic_type"])
def test_every_in_scope_kind_is_budgeted(kind):
    heavy = fx.shot(1, kind, 3.0, overlay_words=CEILING + 1)
    findings, _ = hook.word_findings(fx.doc([heavy]), [heavy])
    assert [(f.rule_id, f.cut) for f in findings] == [(hook.WORD_BUDGET, 1)]


@pytest.mark.parametrize(
    "kind", ["photo_broll", "screen_capture", "brand_plate", "presenter", "live_action"]
)
def test_out_of_scope_kinds_are_not_warned_however_heavy(kind):
    heavy = fx.shot(1, kind, 3.0, overlay_words=CEILING * 4)
    findings, _ = hook.word_findings(fx.doc([heavy]), [heavy])
    assert findings == []


def test_a_diagram_whose_labels_exceed_the_ceiling_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    ir = {
        "nodes": [{"id": f"n{i}", "label": fx.words(4)} for i in range(CEILING // 4 + 2)],
        "edges": [],
        "groups": [],
    }
    (tmp_path / "flow.json").write_text(json.dumps(ir), encoding="utf-8")
    shot = fx.shot(1, "diagram", 4.0, scene="diagram-reveal", backend="html")
    shot["production"]["screen_ui"]["diagram"] = str(tmp_path / "flow.json")
    findings, _ = hook.word_findings(fx.doc([shot]), [shot])
    assert [(f.rule_id, f.cut) for f in findings] == [(hook.WORD_BUDGET, 1)]


# ── ratio keying (PRD §1.1 item 2) ────────────────────────────────────────────────────────────


def _over_16_9() -> dict:
    return fx.shot(1, "kinetic_type", 3.0, overlay_words=CEILING + 1)


def test_a_9_16_list_at_the_16_9_ceiling_plus_one_is_not_checked_not_warned():
    heavy = _over_16_9()
    findings, unchecked = hook.word_findings(fx.doc([heavy], ratios=["9:16"]), [heavy])
    assert findings == []
    assert unchecked == ["word budget at 9:16"]


def test_no_ratio_declared_is_not_checked():
    heavy = _over_16_9()
    findings, unchecked = hook.word_findings(fx.doc([heavy], ratios=[]), [heavy])
    assert findings == []
    assert unchecked and "ratio" in unchecked[0]


def test_setting_a_9_16_value_makes_it_checked(monkeypatch):
    monkeypatch.setitem(ONSCREEN_WORDS_WARN, "9:16", CEILING - 5)
    heavy = fx.shot(1, "kinetic_type", 3.0, overlay_words=CEILING - 4)
    findings, unchecked = hook.word_findings(fx.doc([heavy], ratios=["9:16"]), [heavy])
    assert unchecked == []
    assert [(f.rule_id, f.cut) for f in findings] == [(hook.WORD_BUDGET, 1)]


def _9_16_problems(ceiling_for) -> list[str]:
    """Why ``ceiling_for`` is not keyed independently for 9:16 (empty means it is)."""
    problems = []
    if "9:16" not in ONSCREEN_WORDS_WARN and ceiling_for("9:16") is not None:
        problems.append("an unset 9:16 borrowed a value")
    if ceiling_for("9:16") == ceiling_for("16:9") and "9:16" not in ONSCREEN_WORDS_WARN:
        problems.append("9:16 reads the 16:9 value")
    return problems


def test_9_16_never_reads_the_16_9_value():
    assert "9:16" not in ONSCREEN_WORDS_WARN, "premise: only 16:9 is seeded"
    assert _9_16_problems(words.ceiling_for) == []


def test_9_16_uses_its_own_value_not_the_16_9_one(monkeypatch):
    """With 9:16 set ABOVE the shot and 16:9 BELOW it, only the 9:16 value decides."""
    monkeypatch.setitem(ONSCREEN_WORDS_WARN, "9:16", CEILING + 10)
    heavy = _over_16_9()
    findings, unchecked = hook.word_findings(fx.doc([heavy], ratios=["9:16"]), [heavy])
    assert (findings, unchecked) == ([], [])


def test_negative_control_a_9_16_fallback_to_16_9_is_caught(monkeypatch):
    def falls_back(ratio):
        return ONSCREEN_WORDS_WARN.get(ratio, ONSCREEN_WORDS_WARN["16:9"])

    assert _9_16_problems(falls_back) != []
    monkeypatch.setattr(words, "ceiling_for", falls_back)
    heavy = _over_16_9()
    findings, _ = hook.word_findings(fx.doc([heavy], ratios=["9:16"]), [heavy])
    assert findings, "the fallback must be visible end to end, or the test above proves nothing"


def test_both_ratios_each_judged_on_their_own():
    heavy = _over_16_9()
    findings, unchecked = hook.word_findings(fx.doc([heavy], ratios=["16:9", "9:16"]), [heavy])
    assert [(f.rule_id, f.cut) for f in findings] == [(hook.WORD_BUDGET, 1)]
    assert unchecked == ["word budget at 9:16"]


def test_missing_diagram_file_does_not_crash_word_findings(tmp_path):
    shot = fx.shot(1, "diagram", 3.0, scene="diagram-reveal")
    shot["production"]["screen_ui"]["diagram"] = "does-not-exist.json"
    doc = fx.doc([shot], ratios=["16:9"])
    findings, unchecked = hook.word_findings(doc, [shot], base_dir=tmp_path)
    assert findings == []
    assert any("words on cut 1's diagram" in u for u in unchecked)
