"""Brand kit merge contract.

The point of gtm_core.brandkit is that it does NOT behave like resolve_knowledge_file: a product
kit contributes its deltas instead of replacing the company kit wholesale. These tests pin that
difference, plus the traversal guard it inherits.

Fixture tenants are fictional (§R9 — no real company or person outside profiles/ and content/).
"""

from __future__ import annotations

import json
import tomllib

import pytest

from gtm_core.brandkit import (
    brand_kit_paths,
    identity_write_target,
    load_brand_kit,
    lookup,
    main,
    merge_kits,
    set_identity_value,
)

COMPANY = """\
[palette]
canvas = "#0E0E0E"
primary = "#E8E8E8"
rule = "#2A2A2A"

[typography]
display = "Helvetica Neue"
serif_allowed = false
fallback_stack = ["Inter", "system-ui"]

[disclosure]
line = "Made with AI. Posted by a human."
"""

PRODUCT = """\
[palette]
primary = "#A8512F"

[typography]
display = "Fraunces"
serif_allowed = true
fallback_stack = ["ui-sans-serif"]
"""


@pytest.fixture
def profiles_root(tmp_path):
    """A profiles tree with a company kit and one product kit for profile 'acme'."""
    knowledge = tmp_path / "acme" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "BRAND.toml").write_text(COMPANY, encoding="utf-8")
    product = tmp_path / "acme" / "products" / "widget"
    product.mkdir(parents=True)
    (product / "BRAND.toml").write_text(PRODUCT, encoding="utf-8")
    return tmp_path


def test_company_only_when_no_product_requested(profiles_root):
    kit = load_brand_kit(profiles_root, "acme")
    assert kit["palette"]["primary"] == "#E8E8E8"
    assert kit["typography"]["display"] == "Helvetica Neue"


def test_product_overrides_per_key_not_whole_table(profiles_root):
    """The whole reason this module exists: canvas and rule survive a palette override."""
    kit = load_brand_kit(profiles_root, "acme", "widget")
    assert kit["palette"]["primary"] == "#A8512F"  # overridden
    assert kit["palette"]["canvas"] == "#0E0E0E"  # inherited — would be lost by whole-file override
    assert kit["palette"]["rule"] == "#2A2A2A"  # inherited


def test_tables_absent_from_the_product_kit_are_inherited_whole(profiles_root):
    kit = load_brand_kit(profiles_root, "acme", "widget")
    assert kit["disclosure"]["line"] == "Made with AI. Posted by a human."


def test_product_may_invert_a_company_boolean(profiles_root):
    """A product brand can contradict the house rule — deliberately, and that must survive."""
    assert load_brand_kit(profiles_root, "acme")["typography"]["serif_allowed"] is False
    assert load_brand_kit(profiles_root, "acme", "widget")["typography"]["serif_allowed"] is True


def test_lists_are_replaced_not_concatenated(profiles_root):
    kit = load_brand_kit(profiles_root, "acme", "widget")
    assert kit["typography"]["fallback_stack"] == ["ui-sans-serif"]


def test_unknown_product_falls_back_to_company_without_self_merge(profiles_root):
    company, product = brand_kit_paths(profiles_root, "acme", "nonesuch")
    assert product is None, "resolver fell back to the company file; must not merge it with itself"
    assert load_brand_kit(profiles_root, "acme", "nonesuch") == load_brand_kit(
        profiles_root, "acme"
    )


def test_missing_kit_is_empty_not_an_error(tmp_path):
    (tmp_path / "bare" / "knowledge").mkdir(parents=True)
    assert load_brand_kit(tmp_path, "bare") == {}


def test_product_kit_alone_works_without_a_company_kit(tmp_path):
    product = tmp_path / "solo" / "products" / "widget"
    product.mkdir(parents=True)
    (product / "BRAND.toml").write_text(PRODUCT, encoding="utf-8")
    kit = load_brand_kit(tmp_path, "solo", "widget")
    assert kit["palette"]["primary"] == "#A8512F"


def test_malformed_kit_raises_rather_than_rendering_off_brand(tmp_path):
    knowledge = tmp_path / "broken" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "BRAND.toml").write_text("[palette\ncanvas = ", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_brand_kit(tmp_path, "broken")


@pytest.mark.parametrize("segment", ["../escape", "a/b", "..", ""])
def test_traversal_guard_is_inherited_from_the_resolver(profiles_root, segment):
    with pytest.raises(ValueError):
        load_brand_kit(profiles_root, segment)
    with pytest.raises(ValueError):
        load_brand_kit(profiles_root, "acme", segment)


def test_merge_kits_does_not_mutate_its_inputs():
    company = {"palette": {"primary": "#000"}}
    product = {"palette": {"primary": "#fff"}}
    merge_kits(company, product)
    assert company == {"palette": {"primary": "#000"}}
    assert product == {"palette": {"primary": "#fff"}}


def test_lookup_dotted_key_and_miss():
    kit = {"palette": {"primary": "#A8512F"}}
    assert lookup(kit, "palette.primary") == "#A8512F"
    with pytest.raises(KeyError):
        lookup(kit, "palette.absent")
    with pytest.raises(KeyError):
        lookup(kit, "palette.primary.deeper")


def test_cli_prints_raw_scalar_for_a_key(profiles_root, capsys):
    code = main(
        [
            "--profile",
            "acme",
            "--product",
            "widget",
            "--key",
            "palette.primary",
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == "#A8512F"


def test_cli_prints_whole_kit_as_json(profiles_root, capsys):
    code = main(["--profile", "acme", "--profiles-root", str(profiles_root)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["palette"]["canvas"] == "#0E0E0E"


def test_cli_exit_codes(profiles_root, tmp_path, capsys):
    missing_key = main(
        ["--profile", "acme", "--key", "palette.absent", "--profiles-root", str(profiles_root)]
    )
    assert missing_key == 3
    unsafe = main(["--profile", "../escape", "--profiles-root", str(profiles_root)])
    assert unsafe == 2
    no_kit = main(["--profile", "bare", "--profiles-root", str(tmp_path)])
    assert no_kit == 3


# --- identity write mode (Phase 8, §5.6) ---------------------------------------

IDENTITY_KIT = """\
[meta]
source = "test"

[palette]
primary = "#000000"

[identity]
soul_id = ""
reference_element_ids = []
voice_id = ""
"""


@pytest.fixture
def identity_kit_path(tmp_path):
    knowledge = tmp_path / "acme" / "knowledge"
    knowledge.mkdir(parents=True)
    path = knowledge / "BRAND.toml"
    path.write_text(IDENTITY_KIT, encoding="utf-8")
    return path


def test_identity_write_target_company_vs_product(tmp_path):
    company = identity_write_target(tmp_path, "acme", None)
    assert company == tmp_path / "acme" / "knowledge" / "BRAND.toml"
    product = identity_write_target(tmp_path, "acme", "widget")
    assert product == tmp_path / "acme" / "products" / "widget" / "BRAND.toml"


def test_identity_write_target_ignores_existence(tmp_path):
    """Unlike resolve_knowledge_file, this must NOT fall back to the company path just
    because the product file doesn't exist yet — a write has to be able to create it."""
    target = identity_write_target(tmp_path, "acme", "brand-new-product")
    assert "products" in target.parts
    assert not target.exists()


def test_set_identity_value_replaces_an_existing_key(identity_kit_path):
    set_identity_value(identity_kit_path, "identity.soul_id", "chr_abc123", now="2026-08-15")
    text = identity_kit_path.read_text(encoding="utf-8")
    assert 'soul_id = "chr_abc123"' in text
    assert "# identity-kit 2026-08-15" in text
    # Untouched sections/keys survive byte-for-byte.
    assert '[palette]\nprimary = "#000000"' in text
    kit = load_brand_kit(identity_kit_path.parent.parent.parent, "acme")
    assert kit["identity"]["soul_id"] == "chr_abc123"


def test_set_identity_value_with_note(identity_kit_path):
    set_identity_value(
        identity_kit_path, "identity.consent_note", "own likeness", note="operator confirmed"
    )
    text = identity_kit_path.read_text(encoding="utf-8")
    assert 'consent_note = "own likeness"' in text
    assert ": operator confirmed" in text


def test_set_identity_value_appends_key_absent_from_the_section(identity_kit_path):
    """restyle_preset_id isn't in the fixture's [identity] table at all — must be appended."""
    set_identity_value(identity_kit_path, "identity.restyle_preset_id", "preset_1")
    kit = load_brand_kit(identity_kit_path.parent.parent.parent, "acme")
    assert kit["identity"]["restyle_preset_id"] == "preset_1"
    assert kit["identity"]["soul_id"] == ""  # untouched


def test_set_identity_value_creates_the_identity_section_when_absent(tmp_path):
    knowledge = tmp_path / "acme" / "knowledge"
    knowledge.mkdir(parents=True)
    path = knowledge / "BRAND.toml"
    path.write_text('[palette]\nprimary = "#000"\n', encoding="utf-8")
    set_identity_value(path, "identity.voice_id", "voice_1")
    kit = load_brand_kit(tmp_path, "acme")
    assert kit["identity"]["voice_id"] == "voice_1"
    assert kit["palette"]["primary"] == "#000"  # pre-existing section untouched


def test_set_identity_value_creates_the_file_only_with_create_true(tmp_path):
    target = tmp_path / "acme" / "knowledge" / "BRAND.toml"
    with pytest.raises(FileNotFoundError):
        set_identity_value(target, "identity.soul_id", "chr_1")
    assert not target.exists()

    set_identity_value(target, "identity.soul_id", "chr_1", create=True)
    kit = load_brand_kit(tmp_path, "acme")
    assert kit["identity"]["soul_id"] == "chr_1"
    assert kit["meta"]["source"] == "identity-kit"


def test_set_identity_value_product_delta_then_merged_readback(tmp_path):
    company = tmp_path / "acme" / "knowledge" / "BRAND.toml"
    company.parent.mkdir(parents=True)
    company.write_text(IDENTITY_KIT, encoding="utf-8")

    product_path = identity_write_target(tmp_path, "acme", "widget")
    set_identity_value(product_path, "identity.soul_id", "chr_product_only", create=True)

    # Product-bound merged read sees the delta; company-only read does not.
    assert load_brand_kit(tmp_path, "acme", "widget")["identity"]["soul_id"] == "chr_product_only"
    assert load_brand_kit(tmp_path, "acme")["identity"]["soul_id"] == ""


def test_set_identity_value_comments_elsewhere_are_preserved_byte_for_byte(tmp_path):
    text = (
        "# top-level comment\n"
        "[meta]\n"
        'source = "seed"  # inline note\n'
        "\n"
        "[identity]\n"
        'soul_id = ""  # was empty\n'
    )
    path = tmp_path / "acme" / "knowledge" / "BRAND.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")

    set_identity_value(path, "identity.soul_id", "chr_new", now="2026-08-15")
    new_text = path.read_text(encoding="utf-8")
    assert new_text.startswith("# top-level comment\n")
    assert 'source = "seed"  # inline note' in new_text
    assert 'soul_id = "chr_new"  # identity-kit 2026-08-15' in new_text
    assert "# was empty" not in new_text  # the replaced line's own trailing comment is gone


def test_set_identity_value_list_value(identity_kit_path):
    set_identity_value(identity_kit_path, "identity.reference_element_ids", ["el_1", "el_2"])
    kit = load_brand_kit(identity_kit_path.parent.parent.parent, "acme")
    assert kit["identity"]["reference_element_ids"] == ["el_1", "el_2"]


def test_set_identity_value_accepts_known_voice_engines(identity_kit_path):
    for engine in (
        "seed_audio",
        "elevenlabs",
        "minimax",
        "seed_speech",
        "vibe_voice",
        "cozy_voice",
        "",
    ):
        set_identity_value(identity_kit_path, "identity.voice_engine", engine)
        kit = load_brand_kit(identity_kit_path.parent.parent.parent, "acme")
        assert kit["identity"]["voice_engine"] == engine


def test_set_identity_value_rejects_unknown_voice_engine(identity_kit_path):
    with pytest.raises(ValueError, match="voice_engine"):
        set_identity_value(identity_kit_path, "identity.voice_engine", "gpt-voice")


def test_set_identity_value_rejects_unknown_key(identity_kit_path):
    with pytest.raises(ValueError, match="not writable"):
        set_identity_value(identity_kit_path, "identity.not_a_real_key", "x")


def test_set_identity_value_rejects_malformed_key_shape(identity_kit_path):
    with pytest.raises(ValueError):
        set_identity_value(identity_kit_path, "soul_id", "x")  # missing 'identity.' prefix
    with pytest.raises(ValueError):
        set_identity_value(identity_kit_path, "identity.soul_id.deeper", "x")


def test_set_identity_value_rejects_control_characters(identity_kit_path):
    with pytest.raises(ValueError, match="control characters"):
        set_identity_value(identity_kit_path, "identity.soul_id", "chr_1\nmalicious = true")


def test_set_identity_value_rejects_wrong_type_for_list_key(identity_kit_path):
    with pytest.raises(ValueError, match="list of strings"):
        set_identity_value(identity_kit_path, "identity.reference_element_ids", "not-a-list")


def test_set_identity_value_verify_failure_leaves_file_untouched(identity_kit_path, monkeypatch):
    """If the post-write round-trip doesn't read back the value that was asked for,
    the file on disk must be exactly what it was before the call."""
    import gtm_core.brandkit as bk

    before = identity_kit_path.read_text(encoding="utf-8")
    # Force the verify step to see a different value than what was written.
    monkeypatch.setattr(bk, "lookup", lambda kit, key: "a completely different value")
    with pytest.raises(ValueError, match="verify failed"):
        set_identity_value(identity_kit_path, "identity.soul_id", "chr_1")
    assert identity_kit_path.read_text(encoding="utf-8") == before


def test_cli_set_identity_round_trip(identity_kit_path, capsys):
    profiles_root = identity_kit_path.parent.parent.parent
    code = main(
        [
            "--profile",
            "acme",
            "--set",
            "identity.soul_id",
            "--value",
            "chr_cli",
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert code == 0
    assert "wrote identity.soul_id" in capsys.readouterr().out
    assert load_brand_kit(profiles_root, "acme")["identity"]["soul_id"] == "chr_cli"


def test_cli_set_identity_list_value(identity_kit_path, capsys):
    profiles_root = identity_kit_path.parent.parent.parent
    code = main(
        [
            "--profile",
            "acme",
            "--set",
            "identity.reference_element_ids",
            "--value",
            '["a","b"]',
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert code == 0
    kit = load_brand_kit(profiles_root, "acme")
    assert kit["identity"]["reference_element_ids"] == ["a", "b"]


def test_cli_set_identity_missing_value_exit_2(identity_kit_path, capsys):
    profiles_root = identity_kit_path.parent.parent.parent
    code = main(
        ["--profile", "acme", "--set", "identity.soul_id", "--profiles-root", str(profiles_root)]
    )
    assert code == 2


def test_cli_set_identity_unwritable_key_exit_2(identity_kit_path, capsys):
    profiles_root = identity_kit_path.parent.parent.parent
    code = main(
        [
            "--profile",
            "acme",
            "--set",
            "identity.nope",
            "--value",
            "x",
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert code == 2


def test_cli_set_identity_missing_file_without_create_exit_3(tmp_path, capsys):
    code = main(
        [
            "--profile",
            "brand-new",
            "--set",
            "identity.soul_id",
            "--value",
            "chr_1",
            "--profiles-root",
            str(tmp_path),
        ]
    )
    assert code == 3


def test_cli_set_identity_unsafe_product_segment_exit_2(identity_kit_path, capsys):
    profiles_root = identity_kit_path.parent.parent.parent
    code = main(
        [
            "--profile",
            "acme",
            "--product",
            "../escape",
            "--set",
            "identity.soul_id",
            "--value",
            "chr_1",
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert code == 2
