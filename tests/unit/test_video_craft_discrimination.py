"""Can the craft checks discriminate? Test plan §4.8, as a table of parameter-built fixtures.

``good`` must come out checked-clean (no findings AND nothing "not checked"); each single-fault
mutant must fire exactly its own rule id on the named cut and nothing else; ``bad_all`` must fire
exactly the listed set. Every assertion keys on the rule id, never on message text. Thresholds are
imported and offset, never typed. Fictional shot lists only.
"""

from __future__ import annotations

import copy

import pytest

pytest.importorskip("gtm_core.video_craft")

from gtm_core.shots_lint.result import lint_shotlist_detailed  # noqa: E402
from gtm_core.video_craft import craft_verdict as cv  # noqa: E402
from gtm_core.video_craft import hook, share  # noqa: E402
from gtm_core.video_craft.share import STATIC_SHARE_WARN  # noqa: E402
from gtm_core.video_craft.words import ONSCREEN_WORDS_WARN  # noqa: E402
from tests.unit import video_craft_fixtures as fx  # noqa: E402

CEILING = ONSCREEN_WORDS_WARN[fx.RATIO]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    fx.isolate(monkeypatch)


def _fired(document: dict) -> set[tuple[str, int | None]]:
    result = lint_shotlist_detailed(document)
    return {(f.rule_id, f.cut) for f in result.findings}


def _clean_problems(document: dict) -> list[str]:
    """Everything that stops ``document`` reading as checked-clean. Empty means clean."""
    result = lint_shotlist_detailed(document)
    verdict = cv.craft_verdict(document, require_craft=False)
    problems = [f"finding {f.rule_id} on cut {f.cut}" for f in result.findings]
    problems += [f"not checked: {n}" for n in result.not_checked]
    problems += [
        f"verdict line {line.question!r} is {line.status}"
        for line in verdict.lines
        if line.question != cv.LINE_STRANGER and line.status != "pass"
    ]
    return problems


# ── good ──────────────────────────────────────────────────────────────────────────────────────


def test_good_is_checked_clean():
    good = fx.good(CEILING)
    assert fx.static_share(good) <= STATIC_SHARE_WARN, "fixture premise: good is under the seed"
    assert _clean_problems(good) == []


def test_good_covers_every_premise_it_claims():
    """The instrument check on the fixture itself: what §4.8 says ``good`` contains, it contains."""
    from gtm_core.video_craft import motion, words

    good = fx.good(CEILING)
    classes = {motion.derive(s).cls for s in good["shots"]}
    assert classes == set(motion.MOTION_CLASSES)
    assert any(words.words_on_screen(s) == CEILING for s in good["shots"])
    kinds = {s["asset"]["class"] for s in good["shots"]}
    assert {"presenter", "diagram"} <= kinds
    assert any(
        (s.get("production") or {}).get("screen_ui", {}).get("scene") == "diagram-reveal"
        for s in good["shots"]
    )


@pytest.mark.parametrize("kit", [None, {}, {"design_system": {}}])
def test_a_good_list_that_merely_reports_not_checked_is_not_clean(monkeypatch, kit):
    """A good twin whose design-system half could not run must FAIL the clean check."""
    fx.isolate(monkeypatch, kit=kit)
    assert _clean_problems(fx.good(CEILING)) != []


def test_a_good_list_with_no_ratio_is_not_clean():
    good = fx.good(CEILING)
    good["deliverable_ratios"] = []
    assert _clean_problems(good) != []


# ── single-fault mutants: each fires exactly its own rule, on its own cut ─────────────────────


def _mut_no_class() -> tuple[dict, set]:
    doc = fx.good(CEILING)
    del doc["shots"][3]["asset"]
    return doc, {(hook.ASSET_MISSING, 4)}


def _mut_unknown_class() -> tuple[dict, set]:
    doc = fx.good(CEILING)
    doc["shots"][4]["asset"]["class"] = "photo"
    return doc, {(hook.ASSET_UNKNOWN, 5)}


def _mut_words() -> tuple[dict, set]:
    doc = fx.good(CEILING)
    doc["shots"][1]["production"]["overlay"] = [{"text": fx.words(CEILING + 1)}]
    return doc, {(hook.WORD_BUDGET, 2)}


def _mut_static() -> tuple[dict, set]:
    """Swap moving cuts onto the stubbed-static scene, one at a time, until the share crosses."""
    doc = fx.good(CEILING)
    for index in (1, 4, 0, 2):
        if fx.static_share(doc) > STATIC_SHARE_WARN:
            break
        production = doc["shots"][index].setdefault("production", {})
        production["screen_ui"] = {"scene": "still-push"}
    assert fx.static_share(doc) > STATIC_SHARE_WARN, "fixture premise: the mutant crosses the seed"
    return doc, {(hook.STATIC_SHARE, None)}


MUTANTS = {
    "mut_no_class": _mut_no_class,
    "mut_unknown_class": _mut_unknown_class,
    "mut_words": _mut_words,
    "mut_static": _mut_static,
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_mutant_fires_exactly_its_own_rule(name):
    doc, expected = MUTANTS[name]()
    assert _fired(doc) == expected


def test_mut_static_share_is_the_one_the_fixture_durations_give():
    doc, _ = _mut_static()
    _f, _c, warn, result = hook.motion_findings(doc["shots"])
    assert warn is not None and warn.rule_id == hook.STATIC_SHARE
    assert result.share == pytest.approx(fx.static_share(doc))
    assert result.total_s == pytest.approx(sum(s["duration_s"] for s in doc["shots"]))


def test_mutants_do_not_touch_the_good_fixture():
    before = fx.good(CEILING)
    for build in MUTANTS.values():
        build()
    assert fx.good(CEILING) == before


# ── bad_all ───────────────────────────────────────────────────────────────────────────────────


def test_bad_all_fires_exactly_the_listed_set():
    bad = fx.bad_all(CEILING)
    assert len(bad["shots"]) == 14
    assert fx.static_share(bad) > STATIC_SHARE_WARN, "fixture premise: bad_all is over the seed"
    expected = (
        {(hook.STATIC_SHARE, None)}
        | {(hook.WORD_BUDGET, n) for n in fx.BAD_ALL_OVER_WORDS}
        | {(hook.ASSET_MISSING, n) for n in fx.BAD_ALL_NO_CLASS}
    )
    assert _fired(bad) == expected
    # And literally: cuts 2, 5, 7, 9, 13 over the word ceiling; cuts 4 and 10 with no kind.
    assert fx.BAD_ALL_OVER_WORDS == (2, 5, 7, 9, 13)
    assert fx.BAD_ALL_NO_CLASS == (4, 10)


def test_bad_all_verdict_fails_moves_and_makeable():
    verdict = cv.craft_verdict(fx.bad_all(CEILING))
    status = {line.question: line.status for line in verdict.lines}
    assert status[cv.LINE_MOVES] == "warn"
    assert status[cv.LINE_MAKEABLE] == "fail"


# ── thresholds: imported and offset ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("offset", "warns"), [(-1, False), (0, False), (1, True)])
def test_word_ceiling_boundary(offset, warns):
    doc = fx.good(CEILING)
    doc["shots"][1]["production"]["overlay"] = [{"text": fx.words(CEILING + offset)}]
    assert ((hook.WORD_BUDGET, 2) in _fired(doc)) is warns


def _three_shots_at(static_s: float, moving_s: float) -> list[dict]:
    return [
        fx.shot(1, "ui_mock", static_s, scene="still-push"),
        fx.shot(2, "presenter", moving_s, role="presenter"),
        fx.shot(3, "photo_broll", moving_s),
    ]


def _exact_seed_durations() -> tuple[float, float]:
    """Durations whose static share equals the seed exactly in floating point."""
    for scale in (10.0, 100.0, 1000.0, 1024.0):
        static_s = STATIC_SHARE_WARN * scale
        moving_s = (scale - static_s) / 2
        if static_s / (static_s + moving_s + moving_s) == STATIC_SHARE_WARN:
            return static_s, moving_s
    pytest.fail("no scale gives the seed exactly; pick durations by hand")


@pytest.mark.parametrize(("nudge", "warns"), [(-1e-6, False), (0.0, False), (1e-6, True)])
def test_share_boundary_greater_than_warns_equal_passes(nudge, warns):
    static_s, moving_s = _exact_seed_durations()
    shots = _three_shots_at(static_s + nudge, moving_s)
    result = share.planned_static_share(shots, ["static", "moving", "moving"])
    assert result.warns is warns
    assert ((hook.STATIC_SHARE, None) in _fired(fx.doc(shots))) is warns


def test_the_seed_offsets_are_not_vacuous():
    """If the seed were moved to 1.0 nothing could warn, and the cases above would lie."""
    assert 0 < STATIC_SHARE_WARN < 1
    assert CEILING > 1


def test_widening_the_seed_turns_mut_static_green(monkeypatch):
    """Deliberate breakage from §4.8: STATIC_SHARE_WARN = 1.0 must stop mut_static firing."""
    doc, expected = _mut_static()
    monkeypatch.setattr(share, "STATIC_SHARE_WARN", 1.0)
    assert _fired(copy.deepcopy(doc)) != expected


def test_classifying_still_push_as_moving_turns_mut_static_green(monkeypatch):
    """Deliberate breakage from §4.8: still-push read as moving must stop the share warning."""
    doc, expected = _mut_static()
    fx.isolate(monkeypatch, classes={**fx.STUB_CLASSES, "pillow:still-push": "moving"})
    assert _fired(doc) != expected
