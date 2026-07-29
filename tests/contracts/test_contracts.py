"""Contract tests (spec §13.2): meta-validate every schema, and validate representative
valid/invalid samples against the live contracts. Stdlib-only via minijsonschema."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from minijsonschema import validate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "schemas"
FIXTURES = REPO / "tests" / "fixtures"

ALL_SCHEMAS = sorted(SCHEMAS.glob("*.schema.json"))


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.sample.json").read_text())


def test_eight_schemas_present():
    # Count guard against accidental schema additions. 12 = 7 core + journey-state +
    # story-cluster (journey engine) + profile-draft (profile onboarding) + community-signal-model
    # (community-signal-analysis dashboard) + syften-filters (per-profile Syften filter
    # partition). Bump when adding one.
    assert len(ALL_SCHEMAS) == 12, [p.name for p in ALL_SCHEMAS]


@pytest.mark.parametrize("path", ALL_SCHEMAS, ids=lambda p: p.name)
def test_schema_is_well_formed(path):
    s = json.loads(path.read_text())
    assert s.get("$schema", "").startswith("https://json-schema.org")
    assert s.get("type") == "object"
    assert "title" in s


# Schemas that intentionally do NOT use the public github.com/gtm-engine/gtm-engine $id
# convention (checked below) — profile-draft.schema.json uses a bare relative id because it's
# resolved locally by the onboarding pipeline, never dereferenced over the network.
_RELATIVE_ID_SCHEMAS = {"profile-draft"}


@pytest.mark.parametrize(
    "path",
    [p for p in ALL_SCHEMAS if p.stem.removesuffix(".schema") not in _RELATIVE_ID_SCHEMAS],
    ids=lambda p: p.name,
)
def test_schema_id_matches_public_convention(path):
    # A schema's $id must resolve to the public gtm-engine repo, not any private/internal
    # host — this was caught drifting to "https://gtm-engine/schemas/..." once already
    # (2026-07-19): the oss-export neutralize step silently papered over it into a
    # differently-shaped, still-wrong URL instead of failing loudly. Fix at the source, not
    # the export script.
    s = json.loads(path.read_text())
    expected = f"https://github.com/gtm-engine/gtm-engine/schemas/{path.name}"
    assert s.get("$id") == expected, f"{path.name}: $id={s.get('$id')!r}, expected {expected!r}"


# ── valid samples (from fixtures + inline) ────────────────────────────────────


def test_news_item_valid_then_invalid():
    s = _schema("news-item")
    assert validate(_fixture("news_item"), s) == []
    bad = _fixture("news_item")
    del bad["title"]  # drop a required field
    assert any("title" in e for e in validate(bad, s))
    bad2 = _fixture("news_item")
    bad2["trending_score"] = "high"  # wrong type
    assert validate(bad2, s)


def test_story_cluster_valid_then_invalid():
    s = _schema("story-cluster")
    assert validate(_fixture("story_cluster"), s) == []
    bad = _fixture("story_cluster")
    bad["score"] = 150  # > maximum 100
    assert validate(bad, s)
    bad2 = _fixture("story_cluster")
    bad2["platform_fit"] = ["tiktok"]  # not in enum
    assert validate(bad2, s)


def test_content_item_valid_then_invalid():
    s = _schema("content-item")
    ok = {
        "id": "ci-1",
        "pillar": "AI Trust",
        "story_id": "cluster-001",
        "platform": "linkedin",
        "format": "carousel",
        "status": "planned",
    }
    assert validate(ok, s) == []
    bad = dict(ok, platform="myspace")  # not in enum
    assert validate(bad, s)
    bad2 = dict(ok)
    bad2["extra"] = 1  # additionalProperties: false
    assert any("additional property" in e for e in validate(bad2, s))


def test_content_item_x_instagram_and_locale():
    # Phase 2: X + Instagram platforms/formats and a non-primary locale are valid ContentItems.
    s = _schema("content-item")
    x_thread = {
        "id": "ci-202625-02",
        "pillar": "Agentic AI",
        "story_id": "cluster-002",
        "platform": "x",
        "format": "thread",
        "status": "planned",
    }
    assert validate(x_thread, s) == []
    ig_reel = dict(x_thread, id="ci-202625-03", platform="instagram", format="reel")
    assert validate(ig_reel, s) == []
    # A localized variant — same story, non-primary locale tag.
    localized = dict(x_thread, id="ci-202625-02-apac", locale="en-IN")
    assert validate(localized, s) == []


def test_run_manifest_valid_then_invalid():
    s = _schema("run-manifest")
    ok = {
        "run_id": "r1",
        "trigger": "telegram",
        "stages": [{"name": "radar", "status": "ok", "outputs": ["digest.md"]}],
    }
    assert validate(ok, s) == []
    # awaiting_approval is a real status agent/pipeline.py persists (Gate 1/2) and
    # agent/web.py searches for — must validate, not just be tolerated at write time.
    gated = {
        "run_id": "r1",
        "trigger": "telegram",
        "stages": [{"name": "plan", "status": "awaiting_approval"}],
    }
    assert validate(gated, s) == []
    # depends_on (E-1, graph-shaped runner) is additive/optional — old manifests without it
    # still validate (the "ok" case above has none), and a manifest that does declare it
    # must also validate.
    graph_shaped = {
        "run_id": "r1",
        "trigger": "telegram",
        "stages": [
            {"name": "radar", "status": "ok"},
            {"name": "plan", "status": "pending", "depends_on": ["radar"]},
        ],
    }
    assert validate(graph_shaped, s) == []
    bad = {
        "run_id": "r1",
        "trigger": "telegram",
        "stages": [{"name": "radar", "status": "exploded"}],
    }  # status not in enum
    assert validate(bad, s)


def test_metric_record_valid_then_invalid():
    s = _schema("metric-record")
    ok = {
        "post_id": "p1",
        "platform": "linkedin",
        "published_at": "2026-06-13T09:00:00Z",
        "impressions": 1000,
        "engagements": 40,
    }
    assert validate(ok, s) == []
    bad = dict(ok, impressions=-5)  # < minimum 0
    assert validate(bad, s)


def test_journey_state_valid_then_invalid():
    s = _schema("journey-state")
    ok = _fixture("journey_state")
    assert validate(ok, s) == []
    # null last_processed_sha is valid (pre-backfill state)
    pre_backfill = {"last_processed_sha": None, "backfill_done": False, "series_spine": []}
    assert validate(pre_backfill, s) == []
    # Missing required field
    bad = {"backfill_done": True}
    assert validate(bad, s)
    # additionalProperties: false
    bad2 = dict(ok, unknown_field="x")
    assert any("additional" in e.lower() for e in validate(bad2, s))
