"""Pillow scene freeze contract test.

Per PRD D5 / R21: No new Pillow procedural scenes may be added to
gtm_core/screen_ui/registry.py (_SCENES is frozen at <= 30 scenes).
All new motion scenes must be authored in HTML/WAAPI (_HTML_SCENES).
"""

from __future__ import annotations

from gtm_core.screen_ui.registry import _HTML_SCENES, _PILLOW_SCENES

_FROZEN_PILLOW_SCENE_COUNT_CAP = 30


def test_pillow_scenes_count_does_not_exceed_frozen_cap():
    """_PILLOW_SCENES must never exceed 30 scenes."""
    assert len(_PILLOW_SCENES) <= _FROZEN_PILLOW_SCENE_COUNT_CAP, (
        f"Pillow scenes frozen at {_FROZEN_PILLOW_SCENE_COUNT_CAP}, found {len(_PILLOW_SCENES)}. "
        "Per PRD D5/R21, new scenes must be authored as HTML scenes in _HTML_SCENES."
    )


def test_html_scenes_are_registered():
    """At least the initial three HTML scenes must be registered."""
    expected = {"kinetic-type", "hero-reveal", "phone-walkthrough"}
    assert expected <= set(_HTML_SCENES), (
        f"Missing expected HTML scenes: {expected - set(_HTML_SCENES)}"
    )
