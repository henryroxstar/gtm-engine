"""Unit tests for .txt file staging, validation, and promotion in gtm_core.knowledge_staging."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gtm_core import knowledge_staging as ks

TODAY = date(2026, 10, 17)


def _profile(tmp_path: Path, name: str = "acme") -> tuple[Path, Path]:
    prof = tmp_path / "profiles" / name
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")
    return tmp_path / "profiles", tmp_path / "content"


def test_txt_allowed_in_safe_topic_relpath() -> None:
    p = ks._safe_topic_relpath("gift-artifacts.txt")
    assert p == Path("gift-artifacts.txt")

    p_voice = ks._safe_topic_relpath("voice-bans.txt")
    assert p_voice == Path("voice-bans.txt")


def test_txt_arbitrary_filename_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported .txt topic filename"):
        ks._safe_topic_relpath("arbitrary_notes.txt")

    with pytest.raises(ValueError, match="unsupported .txt topic filename"):
        ks._safe_topic_relpath("passwords.txt")


def test_txt_traversal_rejected() -> None:
    with pytest.raises(ValueError, match="unsafe topic"):
        ks._safe_topic_relpath("../gift-artifacts.txt")


def test_stage_and_promote_txt_round_trip(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path, "acme")
    live_gift = profiles_root / "acme" / "knowledge" / "gift-artifacts.txt"
    live_gift.parent.mkdir(parents=True, exist_ok=True)
    live_gift.write_text("# Old gift artifacts\none-pager\n", encoding="utf-8")

    candidate_content = "# New gift artifacts\nscorecard\nreadiness brief\n"
    staged = ks.stage(content_root, "acme", "gift-artifacts.txt", candidate_content)
    assert staged.is_file()

    # Verify diff works
    diff_text = ks.diff(profiles_root, content_root, "acme", "gift-artifacts.txt")
    assert "+scorecard" in diff_text
    assert "+readiness brief" in diff_text

    # Promote
    promoted = ks.promote(
        profiles_root,
        content_root,
        "acme",
        "gift-artifacts.txt",
        today=TODAY,
        source="manual",
    )
    assert promoted == live_gift
    content = live_gift.read_text(encoding="utf-8")
    assert "# refreshed: 2026-10-17" in content
    assert "scorecard" in content
    assert "readiness brief" in content


def test_txt_empty_candidate_rejected(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path, "acme")
    with pytest.raises(ValueError, match="empty.*refusing to blank the live file"):
        ks.stage(content_root, "acme", "gift-artifacts.txt", "   \n\n  ")
        ks.promote(
            profiles_root,
            content_root,
            "acme",
            "gift-artifacts.txt",
            today=TODAY,
        )

    # Comments-only candidate must also be rejected
    with pytest.raises(ValueError, match="empty.*refusing to blank the live file"):
        ks.stage(
            content_root, "acme", "gift-artifacts.txt", "# Header comment\n# Another comment\n"
        )
        ks.promote(
            profiles_root,
            content_root,
            "acme",
            "gift-artifacts.txt",
            today=TODAY,
        )


def test_txt_null_bytes_rejected(tmp_path: Path) -> None:
    profiles_root, content_root = _profile(tmp_path, "acme")
    with pytest.raises(ValueError, match="null bytes"):
        ks.stage(content_root, "acme", "gift-artifacts.txt", "artifact\x00corrupt\n")
        ks.promote(
            profiles_root,
            content_root,
            "acme",
            "gift-artifacts.txt",
            today=TODAY,
        )
