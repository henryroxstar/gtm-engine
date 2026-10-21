"""Tests for Mission Control video validator (T12, T13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.screen_ui.mc_video.validate import validate_script

ROOT = Path(__file__).resolve().parents[2]


def _valid_base_script() -> dict:
    return {
        "product": "Core Gateway",
        "fps": 25,
        "width": 1920,
        "height": 1080,
        "brief": {
            "placement": "booth",
            "formats": "16:9",
            "maxDuration": 30,
            "sound": "off",
            "audience": "leaders",
            "message": "Verify agent identities",
            "cta": "example.com",
            "claimsOwner": "PMM",
            "confirmed": True,
        },
        "scenes": [
            {
                "type": "hook-constellation",
                "dur": 4.0,
                "line1": "Agents act for you.",
                "line2": "Which ones?",
            },
            {
                "type": "idcard",
                "dur": 4.0,
                "title": "Agent identity on record.",
                "fields": [["Agent", "Finance"], ["Status", "Verified"]],
            },
            {
                "type": "gate",
                "dur": 5.0,
                "title": "Every action checked.",
                "rules": ["Rule 1", "Rule 2"],
                "requests": [{"at": 0.5, "label": "Pay", "pass": [True, True]}],
            },
            {
                "type": "endcard",
                "dur": 5.0,
                "logo": "logos/brand/logo.svg",
                "cta": "Try it now",
                "url": "example.com",
            },
        ],
    }


def test_mc_video_validator_enforces_brief_schema_and_structure():
    """T12: Pre-render validator enforces brief fields, confirmation, and structure."""
    base = _valid_base_script()

    # Clean script passes
    valid, errors, warnings, dur, _ = validate_script(base)
    assert valid is True
    assert errors == []

    # Missing brief field
    bad_brief = _valid_base_script()
    del bad_brief["brief"]["audience"]
    valid, errors, _, _, _ = validate_script(bad_brief)
    assert valid is False
    assert any("audience" in e for e in errors)

    # Unconfirmed brief
    unconfirmed = _valid_base_script()
    unconfirmed["brief"]["confirmed"] = False
    valid, errors, _, _, _ = validate_script(unconfirmed)
    assert valid is False
    assert any("confirmed" in e for e in errors)

    # Malformed block payload (gate requests pass length mismatch)
    bad_gate = _valid_base_script()
    bad_gate["scenes"][2]["requests"] = [{"at": 0.5, "label": "Pay", "pass": [True]}]  # Needs 2
    valid, errors, _, _, _ = validate_script(bad_gate)
    assert valid is False
    assert any("one pass/fail per rule" in e for e in errors)

    # Duration cap exceeded
    long_script = _valid_base_script()
    long_script["brief"]["maxDuration"] = 10
    valid, errors, _, _, _ = validate_script(long_script)
    assert valid is False
    assert any("over the 10.0s cap" in e for e in errors)


def test_reference_examples_pass_validation():
    """T12 part 2: Migrated reference fixtures pass validation cleanly."""
    example_files = list((ROOT / "profiles").glob("*/knowledge/brand/video-examples/*.json"))
    if not example_files:
        pytest.skip(
            "no reference fixtures found under profiles/*/knowledge/brand/video-examples (OSS carve)"
        )
    assert example_files, (
        "Expected at least one reference fixture under profiles/*/knowledge/brand/video-examples"
    )
    for path in sorted(example_files):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        valid, errors, warnings, dur, _ = validate_script(data)
        assert valid is True, f"{path.name} failed validation: {errors}"
        assert dur <= 30.5


def test_mc_video_validator_enforces_six_word_limit_and_plain_language_jargon_gate():
    """T13: Density limit warning/error and plain-language jargon suggestions."""
    # 7-word headline
    script_7_words = _valid_base_script()
    script_7_words["scenes"][1]["title"] = "Delegated authority breaks at the first hop"

    # Advisory mode: valid, but emits warning
    valid, errors, warnings, _, _ = validate_script(script_7_words, strict_density=False)
    assert valid is True
    assert any("title has 7 words" in w for w in warnings)

    # Strict mode: fails validation
    valid, errors, _, _, _ = validate_script(script_7_words, strict_density=True)
    assert valid is False
    assert any("title has 7 words" in e for e in errors)

    # Technical jargon detection
    jargon_script = _valid_base_script()
    jargon_script["scenes"][0]["line1"] = (
        "Zero-trust OAuth JWT with SPIFFE mTLS reduces blast radius"
    )

    valid, errors, warnings, _, suggestions = validate_script(jargon_script)
    assert valid is True  # Jargon is advisory, exit code 0
    terms_flagged = {s["term"].lower() for s in suggestions}
    assert "zero-trust" in terms_flagged
    assert "oauth" in terms_flagged
    assert "jwt" in terms_flagged
    assert "spiffe" in terms_flagged
    assert "mtls" in terms_flagged
    assert "blast radius" in terms_flagged

    # Verify replacement suggestions exist
    for s in suggestions:
        assert s["suggestion"] != ""


def test_mc_video_validator_checks_logo_path_existence(tmp_path: Path):
    """Validator fails when logo or brandLogo points to a missing file path under brand/."""
    prof_dir = tmp_path / "profiles" / "mockcorp" / "knowledge"
    prof_dir.mkdir(parents=True)
    (prof_dir / "BRAND.toml").write_text('[palette]\ncanvas = "#000000"\n', encoding="utf-8")
    existing_logo = prof_dir / "brand" / "logos" / "mock" / "logo.svg"
    existing_logo.parent.mkdir(parents=True)
    existing_logo.write_text("<svg/>", encoding="utf-8")

    # 1. Existing logo path passes
    script_ok = _valid_base_script()
    script_ok["scenes"][3]["logo"] = "logos/mock/logo.svg"
    valid, errors, _, _, _ = validate_script(script_ok, profile="mockcorp", repo_root=tmp_path)
    assert valid is True, f"Expected valid script with existing logo, got: {errors}"

    # 2. Missing logo path fails
    script_bad = _valid_base_script()
    script_bad["scenes"][3]["logo"] = "logos/mock/missing.svg"
    valid, errors, _, _, _ = validate_script(script_bad, profile="mockcorp", repo_root=tmp_path)
    assert valid is False
    assert any("logo file not found at brand/logos/mock/missing.svg" in e for e in errors)
