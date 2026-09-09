"""Unit tests for the knowledge-index graph layer (tags/related/wikilinks/backlinks) added on top
of the existing canonical/freshness index. Stdlib + pytest only, matching the rest of the suite.

Self-tests prove the checker actually fires (house style, mirrors
tests/contracts/test_layering.py): a synthetic corpus with a real cross-file link must resolve,
a code-fenced ``[[..]]`` token must NOT be treated as a link, and a genuinely broken ref must show
up as ``dangling`` rather than silently vanishing.
"""

from __future__ import annotations

from gtm_core.knowledge_index import build_knowledge_index, build_profile_index, find_in_index


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- frontmatter tags/related ---------------------------------------------------


def test_tags_and_related_parsed_from_frontmatter(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(
        kdir / "company.md",
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n"
        "tags: agent-gateway, nist\nrelated: product, icp-personas\n---\nbody\n",
    )
    _write(kdir / "product.md", "body")
    _write(kdir / "icp-personas.md", "body")

    entry = find_in_index(build_knowledge_index(kdir), "company")
    assert entry.tags == ("agent-gateway", "nist")
    assert entry.related == ("product", "icp-personas")
    assert set(entry.links_out) == {"product", "icp-personas"}


def test_missing_tags_related_default_to_empty(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "company.md", "no frontmatter at all")
    entry = find_in_index(build_knowledge_index(kdir), "company")
    assert entry.tags == ()
    assert entry.related == ()
    assert entry.links_out == ()


# --- wikilink extraction + hygiene ----------------------------------------------


def test_body_wikilink_resolves_to_sibling_file(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "objection-digest.md", "body")
    _write(
        kdir / "voices.md",
        "See [[objection-digest]] for the counter-arguments.",
    )
    entry = find_in_index(build_knowledge_index(kdir), "voices")
    assert "objection-digest" in entry.links_out
    assert entry.dangling == ()


def test_wikilink_inside_fenced_code_is_ignored(tmp_path):
    """Real false-positive found during exploration: products/vta/vta-how-to.md has literal
    `[[staff]]`/`[[bin]]` tokens inside code fences that must never count as a corpus link."""
    kdir = tmp_path / "knowledge"
    _write(
        kdir / "vta-how-to.md",
        "Example:\n```\nconfig[[staff]] = x\nconfig[[bin]] = y\n```\n",
    )
    entry = find_in_index(build_knowledge_index(kdir), "vta-how-to")
    assert entry.links_out == ()
    assert entry.dangling == ()


def test_wikilink_inside_inline_code_is_ignored(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "notes.md", "Use `array[[0]]` indexing syntax here.")
    entry = find_in_index(build_knowledge_index(kdir), "notes")
    assert entry.links_out == ()
    assert entry.dangling == ()


def test_unresolvable_wikilink_is_dangling_not_silently_dropped(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "notes.md", "See [[does-not-exist-anywhere]] for details.")
    entry = find_in_index(build_knowledge_index(kdir), "notes")
    assert entry.links_out == ()
    assert entry.dangling == ("does-not-exist-anywhere",)


def test_memory_node_wikilink_is_neither_link_out_nor_dangling(tmp_path):
    """[[feedback_x]]/[[project_x]]/[[reference_x]] point at the auto-memory system, not the
    knowledge corpus — a deliberate external reference, not a broken link."""
    kdir = tmp_path / "knowledge"
    _write(kdir / "notes.md", "Background: [[feedback_verify_against_canonical_refs]].")
    entry = find_in_index(build_knowledge_index(kdir), "notes")
    assert entry.links_out == ()
    assert entry.dangling == ()


# --- backlinks -------------------------------------------------------------------


def test_backlinks_are_inverted_links_out(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "company.md", "home fact")
    _write(kdir / "hook-matrix.md", "See [[company]] for positioning.")
    _write(kdir / "icp-personas.md", "Also references [[company]].")

    index = build_knowledge_index(kdir)
    company = find_in_index(index, "company")
    assert set(company.links_in) == {"hook-matrix", "icp-personas"}
    assert find_in_index(index, "hook-matrix").links_in == ()


# --- profile-wide graph (knowledge + products) -----------------------------------


def test_profile_index_cross_resolves_knowledge_and_products(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write(profiles_root / "acme" / "knowledge" / "product.md", "home fact")
    _write(
        profiles_root / "acme" / "products" / "widget" / "PRODUCT.md",
        "See [[product]] for the home definition.",
    )
    index = build_profile_index(profiles_root, "acme")
    relpaths = {e.relpath for e in index}
    assert relpaths == {"knowledge/product.md", "products/widget/PRODUCT.md"}
    product_entry = find_in_index(index, "PRODUCT")
    assert "knowledge/product" in product_entry.links_out


def test_content_tree_is_never_scanned(tmp_path):
    """The index/dupcheck graph is a curated-knowledge read-plane; content/ is the generated
    write-plane (gitignored, a separate nested repo per CLAUDE.md) and must never be scanned even
    if a decoy .md file sits at an identical relative depth next to profiles/."""
    profiles_root = tmp_path / "profiles"
    _write(profiles_root / "acme" / "knowledge" / "company.md", "real knowledge")
    # A sibling content/ tree with a same-named account file — must be invisible to the index.
    _write(tmp_path / "content" / "acme" / "accounts" / "widgetco" / "company.md", "generated PII")

    index = build_profile_index(profiles_root, "acme")
    assert {e.relpath for e in index} == {"knowledge/company.md"}


def test_profile_index_rejects_traversal_in_profile_name(tmp_path):
    profiles_root = tmp_path / "profiles"
    profiles_root.mkdir()
    import pytest

    with pytest.raises(ValueError):
        build_profile_index(profiles_root, "../etc")


# --- existing behavior preserved (canonical/freshness/missing-dir) --------------


def test_existing_canonical_and_missing_dir_behavior_unchanged(tmp_path):
    kdir = tmp_path / "knowledge"
    _write(kdir / "company.md", "canonical")
    entry = find_in_index(build_knowledge_index(kdir), "company")
    assert entry.canonical is True
    assert build_knowledge_index(tmp_path / "does-not-exist") == ()
