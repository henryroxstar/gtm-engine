"""schemas/shots.schema.json — the writer (video-script) / reader (video-render) contract for
long-form shot lists (Phase E).

Validates both skill bodies' own worked examples against the schema — a drift between what
video-script documents writing and what the schema declares would otherwise only surface when a
real shot list broke video-render at runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.contracts.minijsonschema import is_valid

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "schemas" / "shots.schema.json"
SCRIPT_BODY = REPO / "plugin" / "skills" / "video-script" / "body_template.md"
RENDER_BODY = REPO / "plugin" / "skills" / "video-render" / "body_template.md"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _extract_json_fence(text: str, *, near: str) -> dict:
    """First ```json fenced block appearing after the given anchor string."""
    idx = text.index(near)
    m = re.search(r"```json\n(.*?)```", text[idx:], re.DOTALL)
    assert m, f"no ```json fence found after {near!r}"
    return json.loads(m.group(1))


def test_schema_file_is_valid_json():
    _schema()  # must not raise


def test_video_script_worked_example_validates_against_the_schema():
    if not SCRIPT_BODY.exists():
        pytest.skip("video-script body_template.md not present (paid-tier stub)")
    body = SCRIPT_BODY.read_text(encoding="utf-8")
    example = _extract_json_fence(body, near="Step 1.5")
    assert is_valid(example, _schema()), "video-script's own worked shots.json example is invalid"


def test_worked_example_includes_a_presenter_and_a_non_presenter_shot():
    """The example must exercise BOTH branches role gates — a presenter shot (identity anchor +
    lip-sync applies) and a broll/screen shot (both skipped) — or the doc could silently drift to
    only ever showing the historical shape."""
    if not SCRIPT_BODY.exists():
        pytest.skip("video-script body_template.md not present (paid-tier stub)")
    body = SCRIPT_BODY.read_text(encoding="utf-8")
    example = _extract_json_fence(body, near="Step 1.5")
    roles = {s.get("role", "presenter") for s in example["shots"]}
    assert "presenter" in roles
    assert roles & {"broll", "screen"}


def test_schema_role_defaults_to_presenter_and_is_optional():
    schema = _schema()
    role_schema = schema["properties"]["shots"]["items"]["properties"]["role"]
    assert role_schema["default"] == "presenter"
    assert "role" not in schema["properties"]["shots"]["items"]["required"]


def test_schema_role_enum_is_exactly_the_three_documented_values():
    schema = _schema()
    role_schema = schema["properties"]["shots"]["items"]["properties"]["role"]
    assert set(role_schema["enum"]) == {"presenter", "broll", "screen"}


def test_a_shot_missing_role_still_validates_backward_compatibly():
    """Every shot written before this field existed has no role key at all — must keep validating."""
    minimal = {
        "source_item": "item-1",
        "total_duration_s": 8,
        "style_scaffold": {"look": "x", "provider_model": "m"},
        "shots": [
            {
                "n": 1,
                "duration_s": 8,
                "camera": "static",
                "motion_prompt": "she looks up",
                "visual": "office",
            }
        ],
    }
    assert is_valid(minimal, _schema())


def test_an_unknown_role_value_is_rejected():
    bad = {
        "source_item": "item-1",
        "total_duration_s": 8,
        "style_scaffold": {"look": "x", "provider_model": "m"},
        "shots": [
            {
                "n": 1,
                "duration_s": 8,
                "camera": "static",
                "motion_prompt": "she looks up",
                "visual": "office",
                "role": "narrator",
            }
        ],
    }
    assert not is_valid(bad, _schema())


def test_prompt_layer_fields_are_optional_strings():
    """Phase 18 (2026-08-17 gap assessment): the engineered-prompt layer's fields — per-shot
    lighting/lens/wardrobe/stability/sfx and the scaffold's negative clause — are additive and
    optional, so every shot list written before they existed keeps validating."""
    schema = _schema()
    shot_props = schema["properties"]["shots"]["items"]["properties"]
    for name in ("lighting", "lens", "wardrobe", "stability", "sfx"):
        assert shot_props[name]["type"] == "string"
        assert name not in schema["properties"]["shots"]["items"]["required"]
    scaffold = schema["properties"]["style_scaffold"]
    # Widened 2026-09-03 to the two shapes the committed corpus actually uses — a comma-joined
    # string, and the array of nouns the three 2026-08/09 films write. Both typed; the array's
    # items are pinned to strings, so this is a second typed shape rather than a loosening.
    # tests/contracts/test_shots_schema_conformance.py asserts both are accepted and that an
    # untyped shape (a number, an object, an array of objects) is still refused.
    assert scaffold["properties"]["negative"]["type"] == ["string", "array"]
    assert scaffold["properties"]["negative"]["items"]["type"] == "string"
    assert "negative" not in scaffold["required"]


def test_a_shot_carrying_the_prompt_layer_fields_validates():
    doc = {
        "source_item": "item-1",
        "total_duration_s": 8,
        "style_scaffold": {
            "look": "editorial monochrome, fine grain",
            "provider_model": "m",
            "negative": "text artifacts, logos, extra fingers",
        },
        "shots": [
            {
                "n": 1,
                "duration_s": 8,
                "camera": "static, slow push-in",
                "motion_prompt": "she sets down the mug, then turns toward the window",
                "visual": "empty office at dusk",
                "lighting": "single soft key from camera left",
                "lens": "shallow depth of field",
                "wardrobe": "dark crew-neck",
                "stability": "wardrobe and framing stay exactly as the start frame",
                "sfx": "SFX: room tone",
            }
        ],
    }
    assert is_valid(doc, _schema())


def test_an_unknown_shot_field_is_still_rejected():
    """additionalProperties stays false — growth happens by schema change, never by smuggling."""
    bad = {
        "source_item": "item-1",
        "total_duration_s": 8,
        "style_scaffold": {"look": "x", "provider_model": "m"},
        "shots": [
            {
                "n": 1,
                "duration_s": 8,
                "camera": "static",
                "motion_prompt": "she looks up",
                "visual": "office",
                "mood": "tense",
            }
        ],
    }
    assert not is_valid(bad, _schema())


def test_render_body_documents_the_role_gate_on_both_anchor_and_lip_sync():
    if not RENDER_BODY.exists():
        pytest.skip("video-render body_template.md not present (paid-tier stub)")
    body = RENDER_BODY.read_text(encoding="utf-8")
    assert "role" in body
    assert "broll" in body and "screen" in body
    assert "skip" in body.lower()
    assert "identity anchor" in body
    assert "lip-sync" in body or "lip sync" in body


def test_render_manifest_references_the_shots_schema():
    if not RENDER_BODY.exists():
        pytest.skip("video-render body_template.md not present (paid-tier stub)")
    body = RENDER_BODY.read_text(encoding="utf-8")
    assert "shots.schema.json" in body or "role" in body


def test_script_body_references_the_shots_schema():
    if not SCRIPT_BODY.exists():
        pytest.skip("video-script body_template.md not present (paid-tier stub)")
    body = SCRIPT_BODY.read_text(encoding="utf-8")
    assert "shots.schema.json" in body


# --- identity_bindings: the schema and the enforced gate must agree -------------------------
#
# 2026-09-03: they did not. `video-script` emitted `identity_bindings: {"element": "<uuid>"}`;
# the schema declared every value an object with HeyGen-avatar fields only and no element id at
# all, so jsonschema refused the file; `shots_lint` reported ok:true because it never looked.
# A schema nobody enforces is dead weight — these tests are the pin that keeps the two in step.


def _bindings_schema() -> dict:
    return _schema()["properties"]["identity_bindings"]


def test_identity_binding_handle_vocabulary_matches_the_linter():
    """Set EQUALITY between the schema's handle fields and the set ``shots_lint`` requires.

    Both directions fail loudly: a handle added to the schema that the linter will not accept as
    identifying a binding, and a handle the linter accepts that the schema never declared.
    """
    from gtm_core.shots_lint import _IDENTITY_HANDLE_FIELDS

    declared = set(_bindings_schema()["additionalProperties"]["properties"])
    descriptive = {"engine", "source_px", "consent_basis"}
    assert declared - descriptive == set(_IDENTITY_HANDLE_FIELDS)


def test_schema_reserves_the_same_notes_key_the_linter_exempts():
    from gtm_core.shots_lint import _BINDINGS_NOTES_KEY

    assert set(_bindings_schema().get("properties", {})) == {_BINDINGS_NOTES_KEY}


def test_bare_id_string_binding_is_refused_by_both_schema_and_linter():
    """The exact 2026-09-03 shape, asserted against BOTH gates in one test."""
    from gtm_core import shots_lint

    doc = _valid_shotlist(identity_bindings={"element": "1c51b99c-7b37-4439-b252-9877eec64e6e"})
    assert not is_valid(doc, _schema())
    errors, _ = shots_lint.lint_shotlist(doc)
    assert any("is not a binding object" in e for e in errors)


def test_object_binding_with_an_element_id_passes_both():
    from gtm_core import shots_lint

    doc = _valid_shotlist(
        identity_bindings={
            "bao": {
                "element_id": "1c51b99c-7b37-4439-b252-9877eec64e6e",
                "engine": "higgsfield nano_banana_pro",
                "source_px": "1080x1920 portrait",
            },
            "notes": {"why_not_a_soul": "a drawn character depicts no real person"},
        }
    )
    assert is_valid(doc, _schema()), "the object form the linter demands must validate"
    errors, _ = shots_lint.lint_shotlist(doc)
    assert not [e for e in errors if e.startswith("identity_bindings")]


def test_binding_naming_no_handle_warns_but_does_not_block():
    """The schema cannot express "at least one of these" in our subset; the linter owns it.

    Advisory only: a subject composited locally from stills has no provider-side handle to
    record, which is legitimate. What it costs is reproducibility, so it is surfaced not refused.
    """
    from gtm_core import shots_lint

    doc = _valid_shotlist(identity_bindings={"bao": {"engine": "higgsfield", "source_px": "1x1"}})
    assert is_valid(doc, _schema())
    errors, warnings = shots_lint.lint_shotlist(doc)
    assert not [e for e in errors if e.startswith("identity_bindings")]
    assert any("names no identity handle" in w for w in warnings)


def _valid_shotlist(**extra) -> dict:
    """A minimal shot list that trips no OTHER rule, so a test asserts only what it names."""
    doc = {
        "source_item": "item-1",
        "total_duration_s": 4,
        "deliverable_ratios": ["9:16"],
        "style_scaffold": {"look": "clean grain", "provider_model": "model-1"},
        "shots": [
            {
                "n": 1,
                "duration_s": 4,
                "camera": "static shot",
                "motion_prompt": "the paper cup tips and rights itself on the table",
                "visual": "a paper cup on a table",
                "role": "broll",
                "audio_bed": "room tone",
            }
        ],
    }
    doc.update(extra)
    return doc
