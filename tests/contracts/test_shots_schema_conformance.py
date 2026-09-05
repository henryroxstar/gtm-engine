"""``schemas/shots.schema.json`` is ENFORCED, and enforced against real documents.

2026-09-03: the schema declared itself the ``video-script`` → ``video-render`` contract and
nothing ran it — not CI, not the skills, not ``gtm_core.shots_lint``. Measured, 11 of the 14
committed shot lists failed it. The dominant cause was ``additionalProperties: false`` at the top
and per-shot levels meeting real files that legitimately carry production keys, and the one place
it was noticed worked AROUND it: a render record still carries three keys with a note that they
"were never schema-validated".

Two things had to change together, and both are pinned here.

1. **The schema had to permit what real files carry** — via a reserved ``production`` namespace
   at three levels, NOT by opening ``additionalProperties``. That distinction is the whole design:
   a production fact gets a declared home, while a misspelt ``motion_promt`` is still refused as
   the typo it is. :func:`test_additional_properties_stays_closed_at_every_level` is the pin that
   stops a later "just make it pass" from flipping the door open instead.
2. **Something had to actually run it.** ``gtm_core.shots_lint`` validates by default (``--no-schema``
   opts out), and this module validates every committed fixture in both directions.

Sibling of ``tests/skills/test_shots_schema.py``, which pins the schema against the SKILL bodies'
worked examples and against the identity-binding linter's vocabulary. This module pins it against
the corpus on disk and against the readers in ``gtm_core``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.minischema import validate
from gtm_core.shots_lint.schema import SCHEMA_PATH, load_schema, schema_errors

REPO = Path(__file__).resolve().parents[2]

#: Tripwire fixtures deliberately built BROKEN — the corpus proving each lint rule fires. They
#: must fail the schema too; a "broken" fixture that quietly conforms has stopped testing anything.
#: Listed explicitly rather than matched by filename, because intent is not inferable from a name.
INVALID_FIXTURES = {
    "broken-top.shots.json",
    "identity-bindings-bad.shots.json",
    "live-action.shots.json",
    "narration-bad.shots.json",
    "not-an-object.shots.json",
    "shots-bad.shots.json",
}


def _fixtures() -> list[Path]:
    return sorted((REPO / "tests" / "tripwire" / "shots_lint").glob("*.shots.json"))


def test_the_fixture_corpus_is_not_empty():
    """Guards the two parametrised tests below: an empty glob passes them both vacuously."""
    assert len(_fixtures()) >= 5


@pytest.mark.parametrize(
    "path", [p for p in _fixtures() if p.name not in INVALID_FIXTURES], ids=lambda p: p.name
)
def test_every_valid_fixture_conforms_to_the_schema(path: Path):
    errors = schema_errors(json.loads(path.read_text(encoding="utf-8")))
    assert not errors, f"{path.name} no longer conforms to {SCHEMA_PATH.name}:\n  " + "\n  ".join(
        errors
    )


@pytest.mark.parametrize(
    "path", [p for p in _fixtures() if p.name in INVALID_FIXTURES], ids=lambda p: p.name
)
def test_every_deliberately_broken_fixture_is_refused(path: Path):
    """The other direction. Without this the suite would pass with a schema that accepts anything —
    which is materially what the old one did, since nothing ran it at all."""
    assert schema_errors(json.loads(path.read_text(encoding="utf-8")))


def test_the_invalid_fixture_list_names_only_files_that_exist():
    """A renamed fixture must not silently drop out of the negative half of the corpus."""
    assert INVALID_FIXTURES <= {p.name for p in _fixtures()}


# --- the design: a namespace, not an open door ----------------------------------------------


def _levels() -> dict[str, dict]:
    schema = load_schema()
    return {
        "top level": schema,
        "style_scaffold": schema["properties"]["style_scaffold"],
        "shot": schema["properties"]["shots"]["items"],
    }


@pytest.mark.parametrize("level", sorted(_levels()))
def test_additional_properties_stays_closed_at_every_level(level: str):
    """The relaxation was a NAMESPACE. Flipping this to ``true`` would "fix" the same failures
    while giving up the only thing the schema still buys: that ``motion_promt`` is a typo."""
    assert _levels()[level].get("additionalProperties") is False


@pytest.mark.parametrize("level", sorted(_levels()))
def test_every_level_reserves_the_production_namespace(level: str):
    prod = _levels()[level]["properties"].get("production")
    assert prod is not None, f"{level} has no reserved `production` key"
    assert prod["type"] == "object"
    assert prod["additionalProperties"] is True, "the namespace is deliberately unconstrained"


def test_a_production_namespace_absorbs_arbitrary_keys_the_schema_never_named():
    doc = _valid_shotlist(
        production={"status": "delivered", "score_pass_2026_08_30_v5": {"note": "n/a"}},
    )
    doc["style_scaffold"]["production"] = {"palette": {"canvas": "#0B1A2E"}}
    doc["shots"][0]["production"] = {"lane": "screen-ui", "planned_duration_s": 4.0, "tc": "0:00"}
    assert not schema_errors(doc)


def test_a_misspelt_field_is_still_refused_at_every_level():
    """The property the namespace was designed to preserve, asserted at all three levels at once."""
    for mutate in (
        lambda d: d.update(total_duraton_s=4),
        lambda d: d["style_scaffold"].update(negatve="logos"),
        lambda d: d["shots"][0].update(motion_promt="the cup tips"),
    ):
        doc = _valid_shotlist()
        mutate(doc)
        assert schema_errors(doc), "a typo outside `production` must not be smuggled in"


# --- the schema must declare what the READERS actually read ---------------------------------


def test_the_fields_video_finish_reads_off_a_shot_are_declared():
    """``id`` and ``file`` were load-bearing in ``burn_captions`` and absent from the schema.

    A field a reader reads is contract, whatever anyone wrote down — so it is declared, never
    filed under ``production``. Read out of the source rather than restated, so deleting or
    renaming either read fails here instead of silently re-opening the gap.
    """
    source = (REPO / "gtm_core" / "video_finish" / "burn.py").read_text(encoding="utf-8")
    declared = load_schema()["properties"]["shots"]["items"]["properties"]
    for field in ("id", "file"):
        assert f'shot.get("{field}")' in source, f"burn.py no longer reads shot[{field!r}]"
        assert declared[field]["type"] == "string"


def test_shot_order_may_be_carried_by_n_or_by_id():
    """Practice converged on the stable ``id`` label; ``n`` is redundant with the array index and
    is no longer required. Both shapes, and both together, must validate."""
    for shot_key in ({"n": 1}, {"id": "s01"}, {"n": 1, "id": "s01"}):
        doc = _valid_shotlist()
        doc["shots"][0].update(shot_key)
        assert not schema_errors(doc), f"{shot_key} must validate"


def test_n_is_still_typed_when_present():
    """Dropping a field from ``required`` must not stop it being checked when it is written."""
    doc = _valid_shotlist()
    doc["shots"][0]["n"] = "1"
    assert schema_errors(doc)


def test_scaffold_negative_accepts_both_committed_shapes_and_refuses_others():
    for good in ("text artifacts, logos", ["text artifacts", "logos"]):
        doc = _valid_shotlist()
        doc["style_scaffold"]["negative"] = good
        assert not schema_errors(doc), f"{good!r} is in the committed corpus"
    for bad in (7, {"exclude": "logos"}, [{"noun": "logos"}]):
        doc = _valid_shotlist()
        doc["style_scaffold"]["negative"] = bad
        assert schema_errors(doc), f"{bad!r} is not a typed shape and must be refused"


# --- the validator itself --------------------------------------------------------------------


def test_the_test_tree_and_gtm_core_share_one_validator():
    """``tests/contracts/minijsonschema`` is a re-export, not a second copy that can drift."""
    import tests.contracts.minijsonschema as shim

    assert shim.validate is validate


def test_the_schema_the_cli_loads_is_the_one_in_schemas():
    assert SCHEMA_PATH == REPO / "schemas" / "shots.schema.json"
    assert SCHEMA_PATH.is_file()


def _valid_shotlist(**extra) -> dict:
    """A minimal conformant shot list, so each test asserts only the thing it names."""
    doc = {
        "source_item": "item-1",
        "total_duration_s": 4,
        "deliverable_ratios": ["9:16"],
        "style_scaffold": {"look": "clean grain", "provider_model": "model-1"},
        "shots": [
            {
                "id": "s01",
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
