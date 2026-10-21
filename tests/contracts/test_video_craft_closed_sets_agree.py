"""The asset-class closed list agrees everywhere it is spelled (PRD 2026-10-19 R2a, R9).

Pattern of ``tests/contracts/test_closed_sets_agree.py``: one source (``video_craft.classes``) and
every other surface checked against it. The comparison is a real function, ``disagreements``, that
takes the surfaces as plain data, so the negative controls feed it tmp COPIES of the real files with
one thing changed and watch it go red. Surfaces:

(a) ``schemas/shots.schema.json`` ``asset.class`` enum == ``ASSET_CLASS_NAMES``, same order;
(b) the operator labels in the ``video-storyboard`` Step 2.7 table == the module's labels;
(c) every ``preferred`` and non-empty ``fallback`` names a registered scene or a named lane;
(d) diagram, kinetic_type, ui_mock, data_visual and non-Mission-Control brand_plate prefer an HTML
    scene; every non-empty fallback is a Pillow scene;
(e) brand_plate follows the archetype: Mission Control gets a registered ``mc-`` scene, anyone
    else ``hero-reveal``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("gtm_core.video_craft")

from gtm_core.screen_ui import registry  # noqa: E402
from gtm_core.video_craft import classes as real_classes  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "schemas" / "shots.schema.json"
SKILL = REPO / "plugin" / "skills" / "video-storyboard" / "body_template.md"
CLASSES_PY = Path(real_classes.__file__)

#: Kinds that no screen_ui scene renders; their ``preferred`` is a lane, checked to exist below.
LANES = ("video-render", "video-avatar", "live-action-video")
HTML_FIRST = ("diagram", "kinetic_type", "ui_mock", "data_visual", "brand_plate")


def _lane_exists(name: str) -> bool:
    return (REPO / "plugin" / "skills" / name).is_dir() or (
        REPO / "packs" / "video" / "graphs" / f"{name}.toml"
    ).is_file()


# ── surface loaders (each takes a path, so a tmp copy can stand in) ───────────────────────────


def load_classes(path: Path) -> ModuleType:
    name = f"_classes_copy_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolves the module by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def schema_enum(path: Path) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return list(
        doc["properties"]["shots"]["items"]["properties"]["asset"]["properties"]["class"]["enum"]
    )


def skill_labels(path: Path) -> list[str]:
    """First column of the Step 2.7 table, ``| **Label** | ...``."""
    text = path.read_text(encoding="utf-8")
    start = text.index("## Step 2.7")
    nxt = text.find("\n## ", start + 1)
    section = text[start : nxt if nxt != -1 else len(text)]
    return re.findall(r"^\|\s*\*\*(.+?)\*\*\s*\|", section, re.MULTILINE)


def surfaces(
    *, classes_path: Path = CLASSES_PY, schema_path: Path = SCHEMA, skill_path: Path = SKILL
) -> dict:
    module = load_classes(classes_path)
    return {
        "module_names": list(module.ASSET_CLASS_NAMES),
        "module_labels": [c.label for c in module.ASSET_CLASSES],
        "engines": {c.name: (c.preferred, c.fallback) for c in module.ASSET_CLASSES},
        "preferred_engine": module.preferred_engine,
        "schema_enum": schema_enum(schema_path),
        "skill_labels": skill_labels(skill_path),
        "pillow_scenes": frozenset(registry._SCENES),
        "html_scenes": frozenset(registry._HTML_SCENES),
        "lanes": frozenset(n for n in LANES if _lane_exists(n)),
    }


# ── the comparator ────────────────────────────────────────────────────────────────────────────


def disagreements(s: dict) -> list[str]:
    """Every way the surfaces disagree, in words. Empty means they agree."""
    out: list[str] = []
    if set(s["schema_enum"]) != set(s["module_names"]):
        out.append(f"(a) schema enum {s['schema_enum']} != module list {s['module_names']}")
    elif s["schema_enum"] != s["module_names"]:
        out.append(f"(a-order) schema enum order {s['schema_enum']} != module {s['module_names']}")
    if s["skill_labels"] != s["module_labels"]:
        out.append(f"(b) skill labels {s['skill_labels']} != module labels {s['module_labels']}")
    if len(set(s["module_names"])) != len(s["module_names"]):
        out.append("(a) the module list repeats a name")
    registered = s["pillow_scenes"] | s["html_scenes"]
    for name, (preferred, fallback) in s["engines"].items():
        if preferred not in registered and preferred not in s["lanes"]:
            out.append(f"(c) {name}: preferred {preferred!r} is no registered scene or lane")
        if fallback and fallback not in s["pillow_scenes"]:
            out.append(f"(c) {name}: fallback {fallback!r} is no registered Pillow scene")
    for name in HTML_FIRST:
        if name not in s["engines"]:
            out.append(f"(d) {name} is missing from the module")
            continue
        preferred = s["preferred_engine"](name, archetype=None)
        if preferred not in s["html_scenes"]:
            out.append(f"(d) {name}: preferred {preferred!r} is not an HTML scene")
    if "brand_plate" in s["engines"]:
        mc = s["preferred_engine"]("brand_plate", archetype="mission-control")
        if not (mc.startswith("mc-") and mc in s["pillow_scenes"]):
            out.append(f"(e) Mission Control brand_plate {mc!r} is not a registered mc scene")
        for other in (None, "editorial-light", "custom"):
            got = s["preferred_engine"]("brand_plate", archetype=other)
            if got != "hero-reveal":
                out.append(f"(e) brand_plate under archetype {other!r} is {got!r}, not hero-reveal")
    return out


def test_the_real_surfaces_agree():
    assert [d for d in disagreements(surfaces()) if not d.startswith("(a-order)")] == []


def test_the_schema_enum_is_in_the_module_order():
    assert [d for d in disagreements(surfaces()) if d.startswith("(a-order)")] == []


def test_the_surfaces_are_not_empty():
    """The instrument check: an empty parse on either side would agree vacuously."""
    s = surfaces()
    assert len(s["module_names"]) == len(s["schema_enum"]) == len(s["skill_labels"]) > 0
    assert set(LANES) <= s["lanes"]


# ── negative controls on tmp copies ───────────────────────────────────────────────────────────

_EXTRA_CLASS = """    AssetClass(
        "hologram",
        "Floating hologram",
        "Nothing needed.",
        "",
        "hero-reveal",
        "title-claim",
    ),
)

BY_NAME"""


def _copy(src: Path, tmp_path: Path) -> Path:
    dst = tmp_path / src.name
    shutil.copyfile(src, dst)
    return dst


def _edit(path: Path, old: str, new: str) -> Path:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"negative control could not find {old!r} in {path.name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def test_a_class_added_to_the_module_only_is_red(tmp_path):
    copy = _edit(_copy(CLASSES_PY, tmp_path), ")\n\nBY_NAME", _EXTRA_CLASS)
    found = disagreements(surfaces(classes_path=copy))
    assert any(f.startswith("(a)") for f in found)
    assert any(f.startswith("(b)") for f in found)


def test_a_class_added_to_the_schema_only_is_red(tmp_path):
    copy = _copy(SCHEMA, tmp_path)
    doc = json.loads(copy.read_text(encoding="utf-8"))
    doc["properties"]["shots"]["items"]["properties"]["asset"]["properties"]["class"][
        "enum"
    ].append("hologram")
    copy.write_text(json.dumps(doc), encoding="utf-8")
    assert any(f.startswith("(a)") for f in disagreements(surfaces(schema_path=copy)))


def test_a_reordered_schema_enum_is_red(tmp_path):
    copy = _copy(SCHEMA, tmp_path)
    doc = json.loads(copy.read_text(encoding="utf-8"))
    enum = doc["properties"]["shots"]["items"]["properties"]["asset"]["properties"]["class"]["enum"]
    enum[:] = list(reversed(real_classes.ASSET_CLASS_NAMES))
    copy.write_text(json.dumps(doc), encoding="utf-8")
    assert any(f.startswith("(a-order)") for f in disagreements(surfaces(schema_path=copy)))


def test_a_label_changed_in_the_skill_only_is_red(tmp_path):
    copy = _edit(_copy(SKILL, tmp_path), "| **Animated diagram** |", "| **Moving diagram** |")
    assert any(f.startswith("(b)") for f in disagreements(surfaces(skill_path=copy)))


def test_a_preferred_engine_on_a_pillow_only_scene_is_red(tmp_path):
    pillow_only = sorted(set(registry._SCENES) - set(registry._HTML_SCENES))[0]
    copy = _edit(_copy(CLASSES_PY, tmp_path), '"diagram-reveal",', f'"{pillow_only}",')
    assert any(f.startswith("(d) diagram") for f in disagreements(surfaces(classes_path=copy)))


def test_an_unregistered_scene_is_red(tmp_path):
    copy = _edit(_copy(CLASSES_PY, tmp_path), '"kinetic-type",', '"scene-nobody-registered",')
    found = disagreements(surfaces(classes_path=copy))
    assert any(f.startswith("(c) kinetic_type") for f in found)


def test_an_unregistered_fallback_is_red(tmp_path):
    copy = _edit(_copy(CLASSES_PY, tmp_path), '"checkpoint-flow",', '"fallback-nobody-registered",')
    assert any(f.startswith("(c) diagram") for f in disagreements(surfaces(classes_path=copy)))


def test_a_brand_plate_that_ignores_the_archetype_is_red(tmp_path):
    copy = _edit(
        _copy(CLASSES_PY, tmp_path),
        'MISSION_CONTROL_BRAND_PLATE = "mc-endcard"',
        'MISSION_CONTROL_BRAND_PLATE = "hero-reveal"',
    )
    assert any(f.startswith("(e)") for f in disagreements(surfaces(classes_path=copy)))


def test_the_copy_loader_reads_the_real_module_faithfully(tmp_path):
    """Positive control for the loader: an unedited copy agrees, so a red above is the edit."""
    assert disagreements(surfaces(classes_path=_copy(CLASSES_PY, tmp_path))) == disagreements(
        surfaces()
    )
