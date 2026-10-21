"""The groundedness cascade settles a row the render lint passes on its own verified record.

The lint (`premise_unsupported`) and this cascade must agree: a row the lint passes through the
record route may not then be sent to the judge as "ambiguous" (verification audit 2026-10-02).
"""

from __future__ import annotations

import datetime

from gtm_core import signal_sources
from gtm_core.groundedness import premise_cascade
from tests.unit.test_source_attests import OWN, ROW, TODAY, _premise


def test_a_row_with_a_verified_own_announcement_is_settled_not_escalated(tmp_path):
    sources = tmp_path / "sources"
    signal_sources.store_capture(
        OWN["signal_source_url"],
        f"# News\n\n{OWN['signal_evidence']}. More.\n",
        sources_dir=sources,
    )
    settled, escalate = premise_cascade([OWN], _premise(), today=TODAY, sources_dir=sources)
    assert [c.entailed for c in settled] == [True] and escalate == []
    assert "recorded announcement" in settled[0].detail


def test_a_row_without_a_stored_page_is_not_settled_by_the_record_route():
    settled, escalate = premise_cascade([OWN], _premise(), today=TODAY)
    assert not any(c.entailed and "recorded announcement" in c.detail for c in settled)


def test_a_bare_row_is_unchanged():
    settled, _ = premise_cascade([ROW], _premise(), today=datetime.date(2026, 10, 1))
    assert all(not c.entailed for c in settled)
