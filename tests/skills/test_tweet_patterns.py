"""Tests for gtm_core.tweet_patterns (X tweet-pattern catalog registry).

The final test is the CI drift enforcer — the committed docs/x-tweet-patterns.md must match a
fresh render — mirroring tests/skills/test_knowledge_usage.py::test_committed_usage_doc_in_sync.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from gtm_core import tweet_patterns as tp

REPO = Path(__file__).resolve().parents[2]
HOOK_CRAFT = REPO / "docs" / "hook-craft.md"
VIRALITY_ENGINEERING = REPO / "docs" / "virality-engineering.md"


def _write_registry(tmp_path: Path, *rows: str) -> Path:
    """Write a registry file from one or more dedented [[pattern]] row bodies.

    ``schema_version`` is written exactly once, ahead of every row — a bare `key = value` line
    placed after a `[[pattern]]` header attaches to THAT table under TOML semantics, so naively
    concatenating two copies of a row string (each carrying its own `schema_version` line) puts
    the second one inside the first pattern's table instead of creating a top-level duplicate.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "tweet_patterns.toml"
    body = "schema_version = 1\n\n" + "\n".join(textwrap.dedent(row) for row in rows)
    path.write_text(body, encoding="utf-8")
    return path


_MINIMAL_CORE_ROW = """
    [[pattern]]
    id = "test-pattern"
    name = "Test pattern"
    source = "aop-101 #1"
    formats = ["single"]
    archetype = "named-number"
    trigger = ["T2"]
    fit = "core"
    template = "template text"
    transposition = "transposition text"
    example = "example text"
    """


# --- registry loading + validation ------------------------------------------------------------


def test_registry_loads_and_ids_are_unique_kebab(tmp_path):
    registry = tp.load_registry(_write_registry(tmp_path, _MINIMAL_CORE_ROW))
    assert set(registry) == {"test-pattern"}
    spec = registry["test-pattern"]
    assert spec.formats == ("single",)
    assert spec.trigger == ("T2",)
    assert spec.fit == "core"
    assert spec.guardrail == ""


def test_committed_registry_loads_cleanly_with_over_forty_rows():
    registry = tp.load_registry()
    assert len(registry) >= 40
    for pattern_id in registry:
        assert re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", pattern_id)


def test_empty_registry_rejected(tmp_path):
    with pytest.raises(tp.TweetPatternError, match="at least one"):
        tp.load_registry(_write_registry(tmp_path))


def test_missing_registry_file_rejected(tmp_path):
    with pytest.raises(tp.TweetPatternError, match="not found"):
        tp.load_registry(tmp_path / "nope.toml")


def test_error_message_names_the_registry_path(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(tp.TweetPatternError, match=re.escape(str(missing))):
        tp.load_registry(missing)


def test_unknown_key_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace(
        'example = "example text"', 'example = "example text"\n    bogus_key = "x"'
    )
    with pytest.raises(tp.TweetPatternError, match="unknown key"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_duplicate_id_rejected(tmp_path):
    with pytest.raises(tp.TweetPatternError, match="duplicate pattern id"):
        tp.load_registry(_write_registry(tmp_path, _MINIMAL_CORE_ROW, _MINIMAL_CORE_ROW))


def test_missing_required_field_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('name = "Test pattern"\n    ', "")
    with pytest.raises(tp.TweetPatternError, match="missing required field"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_bad_kebab_id_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('id = "test-pattern"', 'id = "Test_Pattern"')
    with pytest.raises(tp.TweetPatternError, match="kebab-case"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_unknown_format_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('formats = ["single"]', 'formats = ["carousel"]')
    with pytest.raises(tp.TweetPatternError, match="unknown format"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_empty_formats_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('formats = ["single"]', "formats = []")
    with pytest.raises(tp.TweetPatternError, match="non-empty"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_unknown_trigger_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('trigger = ["T2"]', 'trigger = ["T9"]')
    with pytest.raises(tp.TweetPatternError, match="unknown trigger"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_empty_trigger_list_is_allowed(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('trigger = ["T2"]', "trigger = []")
    registry = tp.load_registry(_write_registry(tmp_path, body))
    assert registry["test-pattern"].trigger == ()


def test_unknown_archetype_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('archetype = "named-number"', 'archetype = "made-up"')
    with pytest.raises(tp.TweetPatternError, match="unknown archetype"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_unknown_fit_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('fit = "core"', 'fit = "banned"')
    with pytest.raises(tp.TweetPatternError, match="fit must be one of"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_conditional_requires_guardrail(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('fit = "core"', 'fit = "conditional"')
    with pytest.raises(tp.TweetPatternError, match="requires a non-empty guardrail"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_conditional_with_guardrail_accepted(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('fit = "core"', 'fit = "conditional"')
    body = body.replace('example = "example text"', 'example = "example text"\n    guardrail = "g"')
    registry = tp.load_registry(_write_registry(tmp_path, body))
    assert registry["test-pattern"].guardrail == "g"


def test_core_forbids_guardrail(tmp_path):
    body = _MINIMAL_CORE_ROW.replace(
        'example = "example text"', 'example = "example text"\n    guardrail = "g"'
    )
    with pytest.raises(tp.TweetPatternError, match="must not declare a guardrail"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_single_format_example_fits_280(tmp_path):
    long_example = "x" * 281
    body = _MINIMAL_CORE_ROW.replace('example = "example text"', f'example = "{long_example}"')
    with pytest.raises(tp.TweetPatternError, match="exceeds 280"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_thread_only_format_allows_over_280(tmp_path):
    long_example = "x" * 281
    body = _MINIMAL_CORE_ROW.replace('formats = ["single"]', 'formats = ["thread"]')
    body = body.replace('example = "example text"', f'example = "{long_example}"')
    registry = tp.load_registry(_write_registry(tmp_path, body))
    assert len(registry["test-pattern"].example) == 281


def test_blank_example_rejected(tmp_path):
    body = _MINIMAL_CORE_ROW.replace('example = "example text"', 'example = "   "')
    with pytest.raises(tp.TweetPatternError, match="must not be blank"):
        tp.load_registry(_write_registry(tmp_path, body))


def test_pattern_ids_and_get(tmp_path):
    path = _write_registry(tmp_path, _MINIMAL_CORE_ROW)
    assert tp.pattern_ids(path) == frozenset({"test-pattern"})
    spec = tp.get("test-pattern", path)
    assert spec.name == "Test pattern"
    with pytest.raises(tp.TweetPatternError, match="unknown pattern_id"):
        tp.get("nonexistent", path)


def test_registry_cache_is_scoped_per_path(tmp_path):
    """Two distinct tmp registries must not bleed into each other's cache."""
    path_a = _write_registry(tmp_path / "a", _MINIMAL_CORE_ROW)
    other = _MINIMAL_CORE_ROW.replace('id = "test-pattern"', 'id = "other-pattern"')
    (tmp_path / "b").mkdir()
    path_b = _write_registry(tmp_path / "b", other)
    assert tp.pattern_ids(path_a) == frozenset({"test-pattern"})
    assert tp.pattern_ids(path_b) == frozenset({"other-pattern"})


# --- taxonomy cross-check ----------------------------------------------------------------------


def test_taxonomy_matches_source_docs():
    """The 9 hook-craft archetype slugs and T1-T6 triggers this module hardcodes must still
    match the source docs. Fragile to doc reformatting on purpose — better to fail loudly here
    than let two docs that claim one shared vocabulary quietly diverge."""

    def _slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    hook_craft_text = HOOK_CRAFT.read_text(encoding="utf-8")
    # Scope to the "## Archetypes" section only — "## Workflow" further down also has numbered
    # bold list items (its own steps), which would otherwise inflate this count.
    section_match = re.search(
        r"^## Archetypes\n(.*?)^## ", hook_craft_text, re.MULTILINE | re.DOTALL
    )
    assert section_match, f"no '## Archetypes' section found in {HOOK_CRAFT}"
    archetype_names = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", section_match.group(1), re.MULTILINE)
    assert len(archetype_names) == 10, (
        f"expected 10 numbered archetypes in {HOOK_CRAFT}, found {len(archetype_names)}"
    )
    doc_slugs = {_slug(name) for name in archetype_names}
    assert doc_slugs == tp.HOOK_CRAFT_ARCHETYPES

    virality_text = VIRALITY_ENGINEERING.read_text(encoding="utf-8")
    trigger_numbers = set(re.findall(r"\*\*T([1-6])\s*·", virality_text))
    assert trigger_numbers == {"1", "2", "3", "4", "5", "6"}
    assert {f"T{n}" for n in trigger_numbers} == tp.VALID_TRIGGERS


def test_committed_registry_uses_only_declared_taxonomy():
    """Every archetype/trigger the committed registry uses is one of the validated values —
    redundant with load_registry() itself succeeding, but states the invariant directly."""
    registry = tp.load_registry()
    for spec in registry.values():
        assert spec.archetype in tp.VALID_ARCHETYPES
        assert set(spec.trigger) <= tp.VALID_TRIGGERS
        assert set(spec.formats) <= tp.VALID_FORMATS


# --- render determinism + CI drift enforcer -----------------------------------------------------


def test_render_is_deterministic(tmp_path):
    path = _write_registry(tmp_path, _MINIMAL_CORE_ROW)
    assert tp.render(path) == tp.render(path)


def test_render_includes_not_in_catalog_section(tmp_path):
    path = _write_registry(tmp_path, _MINIMAL_CORE_ROW)
    rendered = tp.render(path)
    assert "Not in this catalog" in rendered
    assert "GENERATED — DO NOT EDIT" in rendered


def test_committed_catalog_doc_in_sync():
    """docs/x-tweet-patterns.md must match a fresh render.
    Regenerate with: uv run python -m gtm_core.tweet_patterns generate"""
    assert tp.check(), (
        "docs/x-tweet-patterns.md is stale — run: uv run python -m gtm_core.tweet_patterns generate"
    )


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_check_exits_zero_when_in_sync():
    assert tp.main(["check"]) == 0


def test_cli_list_filters_by_fit_and_format(capsys):
    rc = tp.main(["list", "--fit", "conditional", "--format", "single"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert lines
    for line in lines:
        pattern_id, fit, formats, _name = line.split("\t")
        assert fit == "conditional"
        assert "single" in formats.split(",")


def test_cli_show_unknown_id_exits_nonzero(capsys):
    rc = tp.main(["show", "not-a-real-pattern-id"])
    assert rc == 1
    assert "unknown pattern_id" in capsys.readouterr().err
