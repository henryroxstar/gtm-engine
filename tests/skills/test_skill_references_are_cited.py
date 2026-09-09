"""C8-T3 — every file under a skill's `references/` is reachable from that skill.

A reference nobody cites is a reference nobody loads. It reads as part of the skill in review, and
is inert at runtime — which is worse than not having it, because the next author sees the rule
written down and assumes it is enforced.

Generalised deliberately. C3 added `capture-contract.md`, C8 leans on `prompt-recipes.md`, and C9
adds `outlier-mining.md`; a test written for one of them would have to be rewritten for each. This
walks every skill that has a `references/` directory and skips the ones that do not — an orphan
check, not a requirement that every skill carry references (the design `tests/contracts/
test_reference_register.py` already uses).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "plugin" / "skills"

#: Directories under `references/` whose contents are loaded as a set rather than named one by
#: one — a template's own assets, for instance. Empty today; kept so the exemption is explicit
#: rather than a silent hole in the walk.
_BULK_DIRS: frozenset[str] = frozenset()

#: A RATCHET, not an exemption list. It held the ten files that were already uncited when this test
#: was written (2026-09-07, C8), so that a NEW orphan failed immediately while the existing ones
#: stayed visible instead of silently passing. **It is empty, and the intent is that it stays that
#: way**: the check is now unconditional in both directions. Cite the reference or delete it — never
#: add an entry to make this test go green.
#:
#: What an entry here would mean, concretely: the file reads as part of the skill in review and is
#: never loaded at runtime, so a rule written in it is not in force. That is worse than not having
#: the file, because the next author reads the rule and assumes it is enforced.
#:
#: The ten were worked down on 2026-09-07. Seven were live references that had simply lost their
#: citation — `call-prep`'s and `gtm-planning`'s output skeletons, `email-quality`'s judge rubric
#: (whose item names the body already used as vocabulary), `email-sequence`'s three provider
#: adapters (cited only through a `<email_tool>` path template, so no literal filename appeared),
#: and `video-render`'s duration ceilings (cited by `video-script` and by `gtm_core.shots_lint`, but
#: not by its own body). Three were genuinely dead and were deleted: `account-plan`'s 8-section
#: template, superseded by the body's 18 numbered sections; `linkedin-engagers`' field map,
#: duplicating its own body plus the GENERATED `prospect/references/hubspot-csv-map.md`; and
#: `linkedin-reply`'s form scaffolds, whose comment shapes opened on "Strong point on [X]" —
#: precisely the disguised compliment the body spends a page banning.
_KNOWN_UNCITED: dict[str, tuple[str, ...]] = {}


def _reference_files(skill: Path) -> list[Path]:
    refs = skill / "references"
    if not refs.is_dir():
        return []
    return [
        p
        for p in sorted(refs.rglob("*"))
        if p.is_file() and not p.name.startswith(".") and p.parent.name not in _BULK_DIRS
    ]


def _skills_with_references() -> list[Path]:
    return [s for s in sorted(SKILLS.iterdir()) if s.is_dir() and _reference_files(s)]


def _corpus(skill: Path) -> str:
    """Everything in the skill that could name a reference: its body, and its other references."""
    parts = []
    body = skill / "body_template.md"
    if body.is_file():
        parts.append(body.read_text(encoding="utf-8"))
    for ref in _reference_files(skill):
        parts.append(ref.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_there_are_skills_with_references_to_check():
    """A walk that silently matches nothing is a test that passes for the wrong reason."""
    assert _skills_with_references(), "no skill carries a references/ directory — check the walk"


@pytest.mark.parametrize("skill", _skills_with_references(), ids=lambda s: s.name)
def test_every_reference_file_is_named_somewhere_in_its_skill(skill: Path):
    """An uncited reference is inert at runtime while reading as enforced in review."""
    corpus = _corpus(skill)
    known = set(_KNOWN_UNCITED.get(skill.name, ()))
    orphans = {
        str(ref.relative_to(skill / "references"))
        for ref in _reference_files(skill)
        if ref.name not in corpus and str(ref.relative_to(skill / "references")) not in corpus
    }

    new_orphans = sorted(orphans - known)
    assert not new_orphans, (
        f"{skill.name} carries NEW uncited reference file(s): {new_orphans}. A reference nobody "
        "cites is never loaded, and reads as part of the skill anyway — which is worse than not "
        "having it, because the next author assumes the rule is enforced. Cite it from the body, "
        "or delete it."
    )

    # The ratchet only tightens: an entry that is now cited must leave the list, or the list stops
    # describing anything and becomes a permanent excuse.
    fixed = sorted(known - orphans)
    assert not fixed, (
        f"{skill.name}: {fixed} are now cited and must be removed from _KNOWN_UNCITED. A stale "
        "ratchet entry is how a list of known problems turns into a list nobody reads."
    )


def test_the_capture_contract_is_loaded_conditionally_and_says_so():
    """C3's reference is the one CONDITIONAL load in the tree — the body must state the gate,
    or a rendered run reads camera direction it cannot act on."""
    body = (SKILLS / "video-script" / "body_template.md").read_text(encoding="utf-8")
    assert "capture-contract.md" in body
    window = body[body.index("capture-contract.md") - 400 : body.index("capture-contract.md") + 200]
    assert "live_action" in window, (
        "the capture contract is cited without naming the condition it loads under"
    )


def test_the_reference_standard_clause_appears_once_per_prompt_shape():
    """C8's family 2. TWICE in the recipes (one per assembly shape) and once in the body.

    A second copy in either place is the two-authors failure: the shapes then disagree about
    what the standard may cite, and the Element shape's narrower rule is the one that gets lost.
    """
    recipes_path = SKILLS / "video-render" / "references" / "prompt-recipes.md"
    body_path = SKILLS / "video-render" / "body_template.md"
    if not recipes_path.is_file() or not body_path.is_file():
        # video-render is a private (stubbed) skill in the OSS carve — both files are
        # deliberately withheld there, so there is no per-shape prose left to check.
        pytest.skip("video-render's body/references are stubbed out of this tree")

    recipes = recipes_path.read_text(encoding="utf-8")
    assert recipes.count("**Reference standard**") == 2, (
        "the closing clause must appear exactly once per assembly shape"
    )

    body = body_path.read_text(encoding="utf-8")
    assert body.count("REFERENCE STANDARD") == 1, (
        "the body states the rule once and points at the recipes for the per-shape detail"
    )


def test_the_known_uncited_list_names_only_skills_that_exist():
    """A ratchet keyed on a renamed skill silently stops ratcheting."""
    missing = [name for name in _KNOWN_UNCITED if not (SKILLS / name).is_dir()]
    assert not missing, f"_KNOWN_UNCITED names skills that no longer exist: {missing}"


def test_the_references_added_by_this_prd_are_all_cited():
    """The positive control the ratchet could otherwise hide: C3's and C9's new files, and C8's
    prompt recipes, are cited — the ratchet exists for the pre-existing ten and nothing else."""
    for skill, filename in (
        ("video-script", "capture-contract.md"),
        ("video-render", "prompt-recipes.md"),
        ("content-plan", "outlier-mining.md"),
        ("creator-brief", "story-graph.md"),
    ):
        ref = SKILLS / skill / "references" / filename
        if not ref.is_file():
            continue  # not yet landed by its workstream
        body = (SKILLS / skill / "body_template.md").read_text(encoding="utf-8")
        assert filename in body, f"{skill} does not cite its own {filename}"
        assert filename not in _KNOWN_UNCITED.get(skill, ()), (
            f"{filename} was added by this PRD and must never be on the ratchet"
        )
