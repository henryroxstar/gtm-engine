"""The set of human gate kinds is closed, and adding one is a boundary change.

Two permanent human gates and one inert reply path is the whole inventory: ``plan``, ``publish``,
``reply``. A skill that invents a fourth kind gets no cockpit keyboard — the callback dispatch in
``cockpit/gates.py`` knows exactly these three sentinels — so the failure mode is a run that parks
forever at a gate nobody can answer, which looks from the outside like a hung pipeline.

This is the assertion C2 and C12 both lean on. `creator-brief` produces an artifact under the
existing Gate 1 envelope and never an approval step of its own; the animatic renders *into* the
storyboard gate rather than adding a fourth. Neither may quietly become a gate, and a later
well-meaning addition trips this test rather than the operator.

Scoped to the runtime surface. ``docs/`` is excluded on purpose: it carries proposed kinds from
design documents whose code does not exist (``⟦GATE:x_reply⟧`` in the 2026-07-23 PRD), and a design
nobody built is not a gate. ``tests/`` is excluded because a fixture naming a forged kind is how the
refusal path gets tested.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Where a real gate can be emitted from or dispatched: the cockpit, the runner, skill bodies,
#: pack graphs, the deterministic core, and the backend.
ROOTS = ("cockpit", "agent", "plugin", "packs", "gtm_core", "backend")

SUFFIXES = {".py", ".md", ".toml", ".json"}

GATE_RE = re.compile(r"⟦GATE:(\w+)⟧")

#: A gate marker whose kind is not a bare word — a forged or templated one.
MALFORMED_RE = re.compile(r"⟦GATE:(?!\w+⟧)")

#: The closed set. Widening it is a boundary change: `CLAUDE.md`, the cockpit dispatch and
#: `SECURITY-SELF-ASSESSMENT.md` all move together, and this line is the one that says so.
EXPECTED_KINDS = frozenset({"plan", "publish", "reply"})


def _runtime_files():
    for root in ROOTS:
        for path in (REPO / root).rglob("*"):
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            yield path


def test_the_gate_kinds_in_the_tree_are_exactly_plan_publish_and_reply():
    """No skill, graph or module may introduce a fourth gate kind."""
    found: dict[str, list[str]] = {}
    for path in _runtime_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind in GATE_RE.findall(text):
            found.setdefault(kind, []).append(str(path.relative_to(REPO)))

    extra = {k: sorted(set(v))[:3] for k, v in found.items() if k not in EXPECTED_KINDS}
    assert not extra, (
        f"new gate kind(s) {sorted(extra)} — the cockpit dispatches exactly "
        f"{sorted(EXPECTED_KINDS)}, so a run reaching one of these parks at a gate the operator "
        f"has no keyboard for. First sightings: {extra}"
    )

    # §R12: the negative above is evidence only because the scan can find a positive.
    missing = EXPECTED_KINDS - set(found)
    assert not missing, (
        f"expected gate kind(s) {sorted(missing)} were not found anywhere in {list(ROOTS)} — "
        "the scan is not reading the files it thinks it is, so its 'no new kinds' result means "
        "nothing"
    )


def test_no_gate_marker_carries_a_malformed_kind():
    """A templated or truncated marker is a gate nothing dispatches and everything looks fine."""
    offenders = []
    for path in _runtime_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # The skill-authoring convention writes the placeholder `⟦GATE:…⟧` in prose about gates.
        if MALFORMED_RE.search(text.replace("⟦GATE:…⟧", "")):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"malformed gate marker(s) in: {offenders}"


def test_creator_brief_emits_no_gate_at_all():
    """C2-T12 — the brief is approved WITH the plan, never on its own (PRD §2.2, §4)."""
    for name in ("body_template.md", "SKILL.md"):
        path = REPO / "plugin/skills/creator-brief" / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "⟦GATE:" not in text.replace("⟦GATE:…⟧", ""), (
            f"creator-brief/{name} emits a gate marker — adding a third approval step to the "
            "content lane is an explicit non-goal"
        )


def test_the_animatic_adds_no_gate_and_the_storyboard_keeps_exactly_one():
    """C12-T8. The animatic renders INTO the storyboard gate rather than adding a fourth kind.

    Two halves, and the second is the one that would slip. `gtm_core/animatic.py` emits no
    sentinel at all — it writes a file and a verdict. And `video-storyboard`, which now attaches
    that file at its gate, still carries exactly ONE gate marker: the attachment uses the
    cockpit's existing `⟦FILE:…⟧` document sentinel, which is not a gate and needs no cockpit
    change. A second marker in that body would be a run parked at a gate nobody can answer.
    """
    animatic = REPO / "gtm_core" / "animatic.py"
    assert animatic.is_file(), "gtm_core/animatic.py is missing — C12 did not land"
    assert not GATE_RE.findall(animatic.read_text(encoding="utf-8")), (
        "the animatic emits a gate marker; it is a preview, not an approval step"
    )

    storyboard_body = REPO / "plugin" / "skills" / "video-storyboard" / "body_template.md"
    if not storyboard_body.is_file():
        # video-storyboard is a private (stubbed) skill in the OSS carve — its body_template.md
        # is deliberately withheld there, so there is no custom prose left to check.
        pytest.skip("video-storyboard/body_template.md is stubbed out of this tree")
    body = storyboard_body.read_text(encoding="utf-8")
    # The SET, not the count: this body names `⟦GATE:plan⟧` more than once — the marker itself,
    # plus prose about it in the animatic step that explains no new kind was added. Repeating one
    # kind is harmless; a SECOND kind is a run parked at a gate the cockpit cannot answer.
    found = set(GATE_RE.findall(body))
    assert found == {"plan"}, (
        f"video-storyboard emits gate kind(s) {sorted(found)}; it must emit only `plan`"
    )
    assert "⟦FILE:" in body, (
        "the storyboard gate no longer attaches the animatic — the operator is being asked to "
        "approve pacing they cannot watch"
    )
