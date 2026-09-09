"""C2-T11 — a gate edit becomes one dated house note, capped, and only where it teaches something.

The signal this captures is the operator correcting a draft at Gate 1. The two things that would
make it worthless are both tested here: silently growing without bound (a note file nobody reads is
the same as no notes), and recording every edit on every profile (an unrelated correction dilutes
the ones that mattered).
"""

from __future__ import annotations

import pytest

from gtm_core import house_notes as hn


def test_one_append_writes_one_dated_line(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    path = hn.append("testco", "the opener buried the number — lead with it", today="2026-09-06")
    lines = hn.read_lines("testco")
    assert lines == ["- 2026-09-06 — the opener buried the number — lead with it"]
    assert path == tmp_path / "content" / "testco" / "exemplars" / "house-notes.md"


def test_the_cap_holds_and_drops_the_oldest(tmp_path, monkeypatch):
    """Twenty lines. A cap that is never hit is a cap nobody chose."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    for i in range(hn.MAX_LINES + 1):
        hn.append("testco", f"note {i}", today="2026-09-06")
    lines = hn.read_lines("testco")
    assert len(lines) == hn.MAX_LINES
    assert "note 0" not in lines[0], "the oldest line must be the one dropped"
    assert lines[-1].endswith(f"note {hn.MAX_LINES}")


def test_an_empty_note_is_refused(tmp_path, monkeypatch):
    """A blank line is not taste; it is a gate edit that said nothing."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    with pytest.raises(ValueError):
        hn.append("testco", "   ")


def test_notes_are_confined_to_the_profile(tmp_path, monkeypatch):
    """Same tenant rule as everything else under the content root."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    with pytest.raises(ValueError):
        hn.notes_path("../escape")
    hn.append("testco", "a real note", today="2026-09-06")
    assert hn.read_lines("othertenant") == []


def test_reading_a_profile_with_no_notes_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    assert hn.read_lines("nobody") == []


def test_the_cockpit_records_an_edit_only_when_a_brief_exists(tmp_path, monkeypatch):
    """The narrowing that keeps the file worth reading, exercised on the real cockpit helper."""
    pytest.importorskip("telegram")
    from cockpit.ingress import IngressHandlers

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    handlers = IngressHandlers.__new__(IngressHandlers)

    # No brief anywhere for this profile: nothing is recorded.
    handlers._record_house_note("testco", "tighten the open")
    assert hn.read_lines("testco") == []

    # A brief exists: the edit is worth keeping.
    run = tmp_path / "content" / "testco" / "video" / "2026-09-06-quiet-handoff"
    run.mkdir(parents=True)
    (run / "brief.json").write_text("{}")
    handlers._record_house_note("testco", "tighten the open")
    assert len(hn.read_lines("testco")) == 1


def test_a_failure_to_record_never_costs_the_operator_their_edit(tmp_path, monkeypatch):
    """Taste capture is best-effort; a gate edit must land even if the note cannot be written."""
    pytest.importorskip("telegram")
    from cockpit.ingress import IngressHandlers

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    run = tmp_path / "content" / "testco" / "video" / "2026-09-06-quiet-handoff"
    run.mkdir(parents=True)
    (run / "brief.json").write_text("{}")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(hn, "append", _boom)
    handlers = IngressHandlers.__new__(IngressHandlers)
    handlers._record_house_note("testco", "tighten the open")  # must not raise
