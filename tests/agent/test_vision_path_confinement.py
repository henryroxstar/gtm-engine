"""The vision worker ships local file bytes to Anthropic, so its path parameter is an
egress primitive. `_load_image` resolved `Path(raw).expanduser()` with no root check, so
any absolute path the brain composed — from untrusted OCR'd text, a scraped page, a news
row — was readable and exfiltratable. It is the same input class as the gemini_image
reference images and the higgsfield upload, and now gets the same confinement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

server = pytest.importorskip("agent.mcp.vision.server")

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64


@pytest.fixture()
def content_root(tmp_path, monkeypatch):
    root = tmp_path / "content"
    (root / "example" / "uploads").mkdir(parents=True)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    return root


def test_an_image_inside_the_content_root_loads(content_root):
    """Positive control (§R12). Confinement that refuses everything is not confinement —
    and the cockpit's own upload path (content/<profile>/uploads/) must keep working."""
    img = content_root / "example" / "uploads" / "shot.png"
    img.write_bytes(PNG)
    media_type, data = server._load_image(str(img))
    assert media_type == "image/png"
    assert data


def test_a_path_outside_the_content_root_is_refused(content_root, tmp_path):
    """The regression: an absolute path anywhere on the box used to be read and shipped."""
    outside = tmp_path / "secrets.png"
    outside.write_bytes(PNG)
    with pytest.raises(ValueError) as exc:
        server._load_image(str(outside))
    assert "content root" in str(exc.value).lower() or "outside" in str(exc.value).lower()


def test_a_traversal_out_of_the_content_root_is_refused(content_root, tmp_path):
    outside = tmp_path / "secrets.png"
    outside.write_bytes(PNG)
    sneaky = content_root / "example" / "uploads" / ".." / ".." / ".." / "secrets.png"
    with pytest.raises(ValueError):
        server._load_image(str(sneaky))


def test_a_symlink_out_of_the_content_root_is_refused(content_root, tmp_path):
    """Lexical checks cannot catch this; resolving the path can."""
    outside = tmp_path / "secrets.png"
    outside.write_bytes(PNG)
    link = content_root / "example" / "uploads" / "innocent.png"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        server._load_image(str(link))


def test_the_size_cap_still_applies(content_root):
    """Confinement subsumed the old size check — prove it did not drop it."""
    big = content_root / "example" / "uploads" / "big.png"
    big.write_bytes(b"\x89PNG" + b"\x00" * (server._MAX_IMAGE_BYTES + 1))
    with pytest.raises(ValueError):
        server._load_image(str(big))


def test_an_unsupported_suffix_is_still_refused(content_root):
    bad = content_root / "example" / "uploads" / "notes.txt"
    bad.write_bytes(b"hello")
    with pytest.raises(ValueError, match="unsupported image type"):
        server._load_image(str(bad))


def test_an_empty_path_is_still_refused(content_root):
    with pytest.raises(ValueError, match="no image_path"):
        server._load_image("")
