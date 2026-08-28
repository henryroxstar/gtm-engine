"""Tests for inbound-file ingestion (gtm_core.uploads).

Covers the tenant-safe storage contract the image + CSV pipelines rely on:
  - bytes land under <content_root>/<profile>/uploads/<date>/<hash><ext> (images)
    or <content_root>/<profile>/prospects/imports/<stamp>-<hash>.csv (CSV exports)
  - account-scoped images route to the per-account folder (CLAUDE.md)
  - content-addressed name (same bytes → same path; dedup)
  - suffix is validated + normalized; unsupported types rejected
  - size ceiling and empty input rejected
  - path-traversal segments rejected (tenant boundary)
  - the brain prompt names the path, the caption, and the §R5 reminder

SDK-INDEPENDENT: gtm_core.uploads is pure stdlib, so this runs in CI even when
the editable install (claude-agent-sdk) is unavailable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from gtm_core.uploads import (
    IMAGE_MEDIA_TYPES,
    MAX_CSV_BYTES,
    MAX_IMAGE_BYTES,
    build_csv_prompt,
    build_image_prompt,
    normalize_suffix,
    save_inbound_csv,
    save_inbound_image,
    suffix_for_media_type,
)

_DAY = date(2026, 6, 24)
_PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
_CSV = b"business_name,business_domain\nAcme Inc.,acme.example\n"


def test_saves_under_profile_uploads_with_hashed_name(tmp_path: Path) -> None:
    dest = save_inbound_image(tmp_path, "example", _PNG, ".png", today=_DAY)
    assert dest.is_file()
    assert dest.read_bytes() == _PNG
    # <content_root>/example/uploads/2026-06-24/<hash>.png
    rel = dest.relative_to(tmp_path)
    assert rel.parts[:3] == ("example", "uploads", "2026-06-24")
    assert dest.suffix == ".png"
    assert len(dest.stem) == 16  # sha256 prefix


def test_account_scoped_routes_to_account_folder(tmp_path: Path) -> None:
    dest = save_inbound_image(
        tmp_path, "example2", _PNG, ".png", account="sggc-nanoclaw", today=_DAY
    )
    rel = dest.relative_to(tmp_path)
    assert rel.parts[:4] == ("example2", "accounts", "sggc-nanoclaw", "uploads")


def test_content_addressed_dedup(tmp_path: Path) -> None:
    a = save_inbound_image(tmp_path, "example", _PNG, ".png", today=_DAY)
    b = save_inbound_image(tmp_path, "example", _PNG, ".png", today=_DAY)
    assert a == b  # same bytes → same path
    different = save_inbound_image(tmp_path, "example", _PNG + b"x", ".png", today=_DAY)
    assert different != a


def test_suffix_normalized(tmp_path: Path) -> None:
    # bare, upper-case, no-dot all normalize to a supported lower-case suffix.
    dest = save_inbound_image(tmp_path, "example", _PNG, "JPG", today=_DAY)
    assert dest.suffix == ".jpg"


@pytest.mark.parametrize("bad", [".bmp", ".svg", ".pdf", "", ".exe"])
def test_unsupported_suffix_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        save_inbound_image(tmp_path, "example", _PNG, bad, today=_DAY)


def test_empty_and_oversize_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_inbound_image(tmp_path, "example", b"", ".png", today=_DAY)
    with pytest.raises(ValueError):
        save_inbound_image(tmp_path, "example", b"x" * (MAX_IMAGE_BYTES + 1), ".png", today=_DAY)


@pytest.mark.parametrize("evil", ["../escape", "a/b", "..", "x\x00y"])
def test_traversal_segments_rejected(tmp_path: Path, evil: str) -> None:
    with pytest.raises(ValueError):
        save_inbound_image(tmp_path, evil, _PNG, ".png", today=_DAY)
    with pytest.raises(ValueError):
        save_inbound_image(tmp_path, "example", _PNG, ".png", account=evil, today=_DAY)


def test_normalize_and_media_helpers() -> None:
    assert normalize_suffix("PNG") == ".png"
    assert normalize_suffix(".jpeg") == ".jpeg"
    assert IMAGE_MEDIA_TYPES[".png"] == "image/png"
    assert suffix_for_media_type("image/jpeg") == ".jpg"
    assert suffix_for_media_type("application/pdf") is None


def test_build_image_prompt_mentions_path_caption_and_r5() -> None:
    p = Path("/x/content/example/uploads/2026-06-24/deadbeef.png")
    prompt = build_image_prompt(p, "  who is in this list?  ")
    assert str(p) in prompt
    assert "who is in this list?" in prompt
    assert "§R5" in prompt
    assert "Read tool" in prompt
    assert "mcp__vision__extract_text" in prompt
    # no caption → "none"
    assert 'caption: "none"' in build_image_prompt(p, None)


# --- CSV export receiver (Phase 1.5, bulk-mode prospect discovery) ---

_STAMP = datetime(2026, 7, 19, 14, 30, 0, tzinfo=UTC)


def test_csv_saves_under_prospects_imports_with_stamped_name(tmp_path: Path) -> None:
    dest = save_inbound_csv(tmp_path, "example", _CSV, now=_STAMP)
    assert dest.is_file()
    assert dest.read_bytes() == _CSV
    rel = dest.relative_to(tmp_path)
    assert rel.parts[:3] == ("example", "prospects", "imports")
    assert dest.name.startswith("20260719-143000-")
    assert dest.suffix == ".csv"


def test_csv_content_addressed_dedup(tmp_path: Path) -> None:
    a = save_inbound_csv(tmp_path, "example", _CSV, now=_STAMP)
    b = save_inbound_csv(tmp_path, "example", _CSV, now=_STAMP)
    assert a == b  # same bytes + same stamp → same path
    different = save_inbound_csv(tmp_path, "example", _CSV + b"x", now=_STAMP)
    assert different != a


def test_csv_empty_and_oversize_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_inbound_csv(tmp_path, "example", b"", now=_STAMP)
    with pytest.raises(ValueError):
        save_inbound_csv(tmp_path, "example", b"x" * (MAX_CSV_BYTES + 1), now=_STAMP)


@pytest.mark.parametrize("evil", ["../escape", "a/b", "..", "x\x00y"])
def test_csv_traversal_segments_rejected(tmp_path: Path, evil: str) -> None:
    with pytest.raises(ValueError):
        save_inbound_csv(tmp_path, evil, _CSV, now=_STAMP)


def test_build_csv_prompt_mentions_path_caption_ingest_command_and_r5() -> None:
    p = Path("/x/content/example/prospects/imports/20260719-143000-deadbeef.csv")
    prompt = build_csv_prompt(p, "  vibe export  ")
    assert str(p) in prompt
    assert "vibe export" in prompt
    assert "§R5" in prompt
    assert "python -m gtm_core.prospects_import ingest" in prompt
    assert "--profile <active>" in prompt
    # no caption → "none"
    assert 'caption: "none"' in build_csv_prompt(p, None)
