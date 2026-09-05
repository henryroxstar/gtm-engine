"""ContentItem and shots schema tests for the optional hook_id field."""

import json
from pathlib import Path

from tests.contracts.minijsonschema import validate

REPO = Path(__file__).resolve().parents[1]
SCHEMAS = REPO / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def _content_item() -> dict:
    return {
        "id": "ci-1",
        "pillar": "AI Trust",
        "story_id": "cluster-001",
        "platform": "linkedin",
        "format": "carousel",
        "status": "planned",
    }


def test_content_item_without_hook_id_is_valid():
    s = _schema("content-item")
    assert validate(_content_item(), s) == []


def test_content_item_with_hook_id_is_valid():
    s = _schema("content-item")
    item = _content_item()
    item["hook_id"] = "example-founder-conversation"
    assert validate(item, s) == []


def test_content_item_rejects_unknown_property():
    s = _schema("content-item")
    item = _content_item()
    item["extra_field"] = 1
    errors = validate(item, s)
    assert any("additional property" in e for e in errors)


def test_shots_without_hook_id_is_valid():
    s = _schema("shots")
    shots = {
        "source_item": "ci-1",
        "total_duration_s": 12.0,
        "style_scaffold": {"look": "clean", "provider_model": "model-x"},
        "shots": [
            {
                "n": 1,
                "duration_s": 12.0,
                "camera": "close-up",
                "motion_prompt": "he leans in",
                "visual": "intro",
            }
        ],
    }
    assert validate(shots, s) == []


def test_shots_with_hook_id_is_valid():
    s = _schema("shots")
    shots = {
        "source_item": "ci-1",
        "hook_id": "example-augmentation",
        "total_duration_s": 12.0,
        "style_scaffold": {"look": "clean", "provider_model": "model-x"},
        "shots": [
            {
                "n": 1,
                "duration_s": 12.0,
                "camera": "close-up",
                "motion_prompt": "he leans in",
                "visual": "intro",
            }
        ],
    }
    assert validate(shots, s) == []


def test_shots_rejects_unknown_property():
    s = _schema("shots")
    shots = {
        "source_item": "ci-1",
        "total_duration_s": 12.0,
        "style_scaffold": {"look": "clean", "provider_model": "model-x"},
        "shots": [
            {
                "n": 1,
                "duration_s": 12.0,
                "camera": "close-up",
                "motion_prompt": "he leans in",
                "visual": "intro",
            }
        ],
        "extra_field": 1,
    }
    errors = validate(shots, s)
    assert any("additional property" in e for e in errors)
