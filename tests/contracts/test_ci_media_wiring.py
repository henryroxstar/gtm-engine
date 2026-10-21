"""CI wiring for tests/media/ — pins the binding decision: ffmpeg + pillow install into the
EXISTING `tests` job, no separate `media` job, and
the escape hatch that would silently delete tiers B/C never appears in a workflow file.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"


def _tests_job_text() -> str:
    text = CI.read_text(encoding="utf-8")
    start = text.index("\n  tests:\n")
    # Next top-level job starts at a line with exactly two-space indent + a bare key.
    end = text.index("\n  backend-db:\n", start)
    return text[start:end]


def test_ffmpeg_is_installed_in_the_tests_job():
    assert "apt-get install -y ffmpeg" in _tests_job_text()


def test_pillow_is_installed_in_the_tests_job():
    assert "pip install pytest pillow" in _tests_job_text()


def test_no_separate_media_job_exists():
    text = CI.read_text(encoding="utf-8")
    assert "\n  media:\n" not in text


def test_the_no_ffmpeg_escape_hatch_appears_nowhere_in_any_workflow():
    """GTM_TEST_ALLOW_NO_FFMPEG is a local-only opt-out (tests/media/conftest.py) — if it ever
    appears in a workflow file, someone silently deleted tiers B/C to make a red run green."""
    for path in (REPO / ".github" / "workflows").glob("*.yml"):
        assert "GTM_TEST_ALLOW_NO_FFMPEG" not in path.read_text(encoding="utf-8"), path
