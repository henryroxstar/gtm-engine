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


# --------------------------------------------------- the story spine (S3, 2026-09-07)
# `brief` is `additionalProperties: false`, so these three fields are a schema change rather
# than a convention. Each test below pairs its denial with the positive control that proves the
# validator reached the field at all (§R12).


def test_content_item_accepts_all_three_story_spine_fields():
    """S3-T1. The spine is representable: core_value, opposite and protagonist all validate."""
    s = _schema("content-item")
    item = _content_item()
    item["brief"] = {
        "core_value": "perseverance",
        "opposite": "giving up",
        "protagonist": "a security lead who cannot attribute an agent's action",
    }
    assert validate(item, s) == [], (
        "the three story-spine brief fields must validate — content-plan Step 1 writes them"
    )


def test_content_item_accepts_a_brief_carrying_none_of_them():
    """S3-T1. The fields are optional: a brief written before S3 still validates unchanged."""
    s = _schema("content-item")
    item = _content_item()
    item["brief"] = {"angle": "trust is an infra problem", "audience": "CISO / security lead"}
    assert validate(item, s) == [], "the spine fields are optional; a pre-S3 brief must still pass"


def test_content_item_rejects_a_misspelled_story_spine_field():
    """S3-T1. `core_values` is refused — the typo cannot pass as steering nobody reads."""
    s = _schema("content-item")
    item = _content_item()
    item["brief"] = {"core_values": "perseverance"}
    errors = validate(item, s)
    assert any("additional property" in e for e in errors), (
        f"brief.core_values must be refused by additionalProperties: false, got {errors}"
    )


def test_the_misspelling_check_would_have_passed_the_correct_spelling():
    """S3-T1. Positive control: the refusal above is about the NAME, not about brief itself."""
    s = _schema("content-item")
    item = _content_item()
    item["brief"] = {"core_value": "perseverance"}
    assert validate(item, s) == [], (
        "the correctly spelled field must validate, or the misspelling test proves nothing"
    )


def test_no_existing_fixture_item_gained_an_error_from_the_story_spine():
    """S3-T3. Optional means optional: S3 added no error to any ContentItem fixture in the tree.

    The PRD asks that "every existing fixture item still validates". None of them did, before or
    after — the tripwire plans are deliberately minimal and have always been short of `story_id`
    and `status`. The property that IS true, and is the one S3 could have broken, is that the three
    new fields introduced no NEW error: no fixture item is refused for anything about its `brief`.
    """
    s = _schema("content-item")
    plans = sorted((REPO / "tests").rglob("plans/*.json"))
    seen_with_brief = 0
    offenders: list[str] = []
    for path in plans:
        items = json.loads(path.read_text())
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if "brief" in item:
                seen_with_brief += 1
            for err in validate(item, s):
                if "brief" in err:
                    offenders.append(f"{path.relative_to(REPO)}:{item.get('id')} — {err}")
    assert not offenders, f"the story-spine fields refused an existing fixture item: {offenders}"
    assert seen_with_brief >= 1, (
        "the walk found no fixture item carrying a `brief` at all — a clean result here would "
        "mean nothing, because the fields under test are inside `brief` (§R12)"
    )


def test_the_fixture_walk_would_catch_a_bad_brief_key():
    """S3-T3. Positive control: the walk's silence is evidence only because it can speak."""
    s = _schema("content-item")
    plans = sorted((REPO / "tests").rglob("plans/*.json"))
    item = next(
        i
        for path in plans
        for i in json.loads(path.read_text())
        if isinstance(i, dict) and "brief" in i
    )
    poisoned = {**item, "brief": {**item["brief"], "core_values": "perseverance"}}
    assert any("brief" in e for e in validate(poisoned, s)), (
        "a misspelled brief key on a real fixture produced no brief-shaped error — the walk above "
        "cannot detect the regression it exists to detect"
    )


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


# ------------------------------------------- the reconciled hand-authored drift (2026-09-07)
# Six names were written into real plan items under `content/*/plans/` and refused by
# `additionalProperties: false`. Each was reconciled by DROPPING it rather than by widening the
# schema, and the schema records that decision only by omission — an absent property is invisible,
# so the reasoning would be re-litigated the next time an item fails to validate. It is pinned here
# instead. Every name below has an owner that already holds the same fact:
#
#   markets              -> `locale` + the two-clock rule (one item per variant, content-plan
#                           Step 2); the per-beat caption split belongs to the script's own
#                           "Market variants" section.
#   notes                -> the artifact it annotates, plus git history. The instance that
#                           prompted this pointed at a script section that does not exist and
#                           described a blocker the script had already superseded.
#   brief.operator_directed -> `history.jsonl`, which is written by code. A hand-typed assertion
#                           that an item was authorised is not an audit record.
#   brief.audience_detail -> `brief.audience`, whose description already says "a segment name from
#                           icp-personas.md". Two fields on one axis let a reader pick the wrong one.
#   brief.self_demonstrating -> `BRAND.toml [disclosure].line`, enforced verbatim by
#                           `validate_disclosure`. The instance restated that line and was ALREADY
#                           stale against it, which is the whole argument against restating it.
#   brief.sources        -> `research_ref` and the script's `## Verification log`. `brief` is
#                           pre-generation steering, not evidence.
#
# Widening the schema for any of these is a decision, not a fix — this test is what makes it one.

_DROPPED_TOP_LEVEL = ("markets", "notes")
_DROPPED_BRIEF = ("operator_directed", "audience_detail", "self_demonstrating", "sources")


def test_content_item_still_refuses_the_dropped_hand_authored_fields():
    """The six reconciled names stay unrepresentable, at both levels they were written at."""
    s = _schema("content-item")
    for name in _DROPPED_TOP_LEVEL:
        item = {**_content_item(), name: "x"}
        assert any("additional property" in e for e in validate(item, s)), (
            f"{name!r} was reconciled by dropping it, not by adding it to the schema — if it now "
            "validates, revisit the decision in this test's comment rather than deleting the test"
        )
    for name in _DROPPED_BRIEF:
        item = {**_content_item(), "brief": {name: "x"}}
        errors = validate(item, s)
        assert any("brief" in e and "additional property" in e for e in errors), (
            f"brief.{name!r} was reconciled by dropping it, not by adding it to the schema — if it "
            "now validates, revisit the decision in this test's comment rather than deleting it"
        )


def test_the_dropped_field_check_passes_the_fields_that_replaced_them():
    """§R12 positive control: the refusals above are about those NAMES, not about the levels.

    Each dropped name's surviving owner is exercised here, so a schema change that broke `brief`
    or the item root wholesale would fail loudly instead of making the test above pass vacuously.
    """
    s = _schema("content-item")
    item = {
        **_content_item(),
        "locale": "en",
        "research_ref": "content/t/research/2026-09-07-x.md",
        "brief": {"audience": "The Founder Building a Personal Brand", "angle": "a"},
    }
    assert validate(item, s) == [], (
        "the owners that absorbed the dropped fields must validate, or the refusals above prove "
        "nothing about the names"
    )
