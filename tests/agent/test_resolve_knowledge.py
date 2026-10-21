"""Tests for the product→profile knowledge resolution helper (gtm_core.paths +
gtm_core.resolve_knowledge CLI).

Covers the contract every product-bound skill now relies on:
  - product-level file wins when present
  - falls back to profile-level when the product file is absent
  - falls back to profile-level when no product is given (example-style)
  - the profile-level path is returned even when it does not exist (stable path)
  - path-traversal segments are rejected (tenant boundary)
  - the CLI prints the resolved path and honours --require-exists

SDK-INDEPENDENT: gtm_core.paths is pure stdlib, so this runs in CI even when the
editable install (claude-agent-sdk) is unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import resolve_knowledge as cli
from gtm_core.paths import resolve_knowledge_file


def _make_profiles(tmp_path: Path) -> Path:
    """Build a minimal two-profile tree: example2 (per-product) + example (shared)."""
    root = tmp_path / "profiles"
    # example2: alpha has its own icp; beta does not (so beta falls back).
    pz_know = root / "example2" / "knowledge"
    pz_know.mkdir(parents=True)
    (pz_know / "icp-personas.md").write_text("example2 shared icp\n")
    (pz_know / "voice.md").write_text("example2 voice\n")
    alpha = root / "example2" / "products" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "icp-personas.md").write_text("alpha icp\n")
    (root / "example2" / "products" / "beta").mkdir(parents=True)
    # example: profile-level only, no product knowledge files.
    aff_know = root / "example" / "knowledge"
    aff_know.mkdir(parents=True)
    (aff_know / "icp-personas.md").write_text("example icp\n")
    return root


def test_product_file_wins_when_present(tmp_path):
    root = _make_profiles(tmp_path)
    p = resolve_knowledge_file(root, "example2", "icp-personas.md", product="alpha")
    assert p == root / "example2" / "products" / "alpha" / "icp-personas.md"
    assert p.read_text() == "alpha icp\n"


def test_falls_back_when_product_file_absent(tmp_path):
    root = _make_profiles(tmp_path)
    # beta has no icp-personas.md → profile-level.
    p = resolve_knowledge_file(root, "example2", "icp-personas.md", product="beta")
    assert p == root / "example2" / "knowledge" / "icp-personas.md"
    assert p.read_text() == "example2 shared icp\n"


def test_falls_back_when_no_product_given(tmp_path):
    root = _make_profiles(tmp_path)
    p = resolve_knowledge_file(root, "example", "icp-personas.md")
    assert p == root / "example" / "knowledge" / "icp-personas.md"


def test_product_without_that_file_uses_profile_voice(tmp_path):
    root = _make_profiles(tmp_path)
    # alpha overrides icp but not voice → voice resolves to profile level.
    p = resolve_knowledge_file(root, "example2", "voice.md", product="alpha")
    assert p == root / "example2" / "knowledge" / "voice.md"


def test_returns_profile_path_even_when_missing(tmp_path):
    root = _make_profiles(tmp_path)
    p = resolve_knowledge_file(root, "example2", "does-not-exist.md", product="alpha")
    assert p == root / "example2" / "knowledge" / "does-not-exist.md"
    assert not p.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"filename": "../secrets.md"},
        {"filename": "a/b.md"},
        {"filename": ".."},
        {"filename": "icp.md", "product": "../other"},
        {"filename": "icp.md", "product": ".."},
    ],
)
def test_rejects_traversal_segments(tmp_path, kwargs):
    root = _make_profiles(tmp_path)
    with pytest.raises(ValueError):
        resolve_knowledge_file(root, "example2", **kwargs)


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_resolved_path(tmp_path, capsys):
    root = _make_profiles(tmp_path)
    rc = cli.main(
        [
            "icp-personas.md",
            "--profile",
            "example2",
            "--product",
            "alpha",
            "--profiles-root",
            str(root),
        ]
    )
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == str(root / "example2" / "products" / "alpha" / "icp-personas.md")


def test_cli_fallback_path(tmp_path, capsys):
    root = _make_profiles(tmp_path)
    rc = cli.main(["icp-personas.md", "--profile", "example", "--profiles-root", str(root)])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == str(root / "example" / "knowledge" / "icp-personas.md")


def test_cli_require_exists_missing(tmp_path, capsys):
    root = _make_profiles(tmp_path)
    rc = cli.main(
        ["nope.md", "--profile", "example2", "--profiles-root", str(root), "--require-exists"]
    )
    assert rc == 3
    assert capsys.readouterr().out.strip() == ""


def test_cli_rejects_traversal(tmp_path, capsys):
    root = _make_profiles(tmp_path)
    rc = cli.main(["../secrets.md", "--profile", "example2", "--profiles-root", str(root)])
    assert rc == 2
