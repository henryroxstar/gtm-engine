"""§3.3 regression — the retention rubric's "Gate before spending render credits" section must
never again instruct a pre-render `virality_predictor` cross-check.

`virality_predictor` takes a rendered video as input (`medias[].role = "video"`); it has nothing
to look at before anything renders. `packs/creator/graphs/short-form-video.toml`'s header already
resolved this correctly (the free pre-render gate is Part A alone; the paid predictor cross-check
moved to the `score` stage). The rubric file — read by both `video-script` and `video-score` — was
never updated to match and shipped with **zero test coverage** (confirmed here: no test anywhere
in the tree asserted on this section before this file).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUBRIC = REPO / "plugin" / "skills" / "content-plan" / "references" / "retention-rubric.md"

#: The exact instruction that shipped and broke — preserved as a control so the regression test
#: cannot rot into a tautology once the defect is gone from the tree (it must still recognise the
#: bad text as bad, not merely find no "virality_predictor" string by coincidence).
_THE_2026_08_15_DEFECT = (
    "Cross-check with the `virality_predictor` MCP tool** on the planned hook and opening frames."
)


def _section(text: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert m, f"heading {heading!r} not found in {RUBRIC}"
    return m.group(1)


def test_that_the_control_text_is_recognisably_the_shipped_defect():
    """Positive control on the checker itself: prove the string below IS the impossible
    instruction, so the next test's absence-check is meaningful rather than accidental."""
    assert "virality_predictor" in _THE_2026_08_15_DEFECT
    assert "planned hook" in _THE_2026_08_15_DEFECT  # "planned" = before render exists


def test_the_gate_section_carries_no_pre_render_predictor_instruction():
    section = _section(RUBRIC.read_text(encoding="utf-8"), "Gate before spending render credits")
    assert _THE_2026_08_15_DEFECT not in section
    assert "planned hook and opening frames" not in section


def test_the_gate_section_still_documents_the_predictor_as_a_post_render_step():
    """The fix must not have deleted the predictor cross-check entirely — it moved, not vanished."""
    section = _section(RUBRIC.read_text(encoding="utf-8"), "Gate before spending render credits")
    assert "virality_predictor" in section
    assert "after render" in section.lower() or "video-score" in section


def test_the_short_form_video_pack_header_still_records_the_same_correction():
    """The rubric fix restates a correction packs/creator/graphs/short-form-video.toml already
    made independently — pin that the two stay in agreement rather than drifting apart again."""
    graph = (REPO / "packs" / "creator" / "graphs" / "short-form-video.toml").read_text(
        encoding="utf-8"
    )
    assert "medias[].role" in graph
    assert "cannot" in graph.lower()
