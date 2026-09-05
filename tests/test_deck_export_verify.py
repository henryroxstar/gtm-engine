"""Prove deck_export_verify's two failure modes both trip: a blank export (too thin) and a
theme rasterizing something it shouldn't (too heavy) — the second is what caught a customer
deck exporting at 1633 KB/page from unguarded theme CSS. See gtm_core.deck_lint D9 for the
static check on the theme that this script's ceiling backstops at the artifact.

scripts/ has no __init__.py (deliberately — not part of the installed package set), so the
module under test is loaded by file path rather than imported as a package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deck_export_verify.py"
if not _SCRIPT_PATH.exists():
    # scripts/ (besides bootstrap.sh) is excluded from the OSS carve by design — this test
    # is a private-repo dev-tooling check, not a public-distribution contract.
    pytest.skip(
        "scripts/deck_export_verify.py not present (private dev tooling)", allow_module_level=True
    )

_SPEC = importlib.util.spec_from_file_location("deck_export_verify", _SCRIPT_PATH)
dev = importlib.util.module_from_spec(_SPEC)
sys.modules["deck_export_verify"] = dev
_SPEC.loader.exec_module(dev)


def _fake_pdf(pages: int, page_bytes: int) -> bytes:
    """A minimal blob dev.page_count() and dev.has_ink() both read correctly, padded to
    an exact total size so bytes-per-page is exact for the floor/ceiling math."""
    body = f"/Type /Page /Count {pages} /Image /FontFile".encode()
    pad = b"0" * max(0, pages * page_bytes - len(body))
    return body + pad


def test_page_count_reads_a_plain_count_marker():
    assert dev.page_count(_fake_pdf(15, 1024)) == 15


def test_weight_floor_flags_a_near_empty_export(tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(_fake_pdf(15, 100))  # ~0.1 KB/page — the empty-export bug
    rc = dev.main([str(pdf), "--pages", "15"])
    assert rc == 1


def test_weight_ceiling_flags_a_bloated_export(tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(_fake_pdf(15, int(1700 * 1024)))  # 1700 KB/page — the real-world bug
    rc = dev.main([str(pdf), "--pages", "15"])
    assert rc == 1


def test_a_reasonably_sized_export_passes_both_floor_and_ceiling(tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(_fake_pdf(15, int(400 * 1024)))  # 400 KB/page — real content, not bloat
    rc = dev.main([str(pdf), "--pages", "15"])
    assert rc == 0


def test_ceiling_is_configurable(tmp_path):
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(_fake_pdf(15, int(400 * 1024)))
    rc = dev.main([str(pdf), "--pages", "15", "--max-kb-per-page", "100"])
    assert rc == 1
