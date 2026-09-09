"""C11 — the element library (`gtm_core.elements`).

PRD test ids C11-T1 (round-trip), C11-T2 (traversal refused before any write), C11-T3 (the pose
vocabulary), C11-T4 (resolve order and confinement), C11-T7 (the consent route) and C11-T9
(deletion is local and refuses while referenced).

Everything runs against a `--content-root` under `tmp_path`, so no test can see, or create, a
real tenant tree.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.confine import ConfinementError
from gtm_core.elements import store
from gtm_core.elements.model import (
    IDENTITY_POSES_CHARACTER,
    IDENTITY_POSES_OTHER,
    Element,
    ElementError,
    Pose,
    validate_pose_name,
)
from gtm_core.elements.readme import render_readme


def _object(**overrides) -> Element:
    base = {
        "slug": "brass-dial",
        "kind": "object",
        "name": "Brass dial",
        "constraints": ["always on walnut"],
        "avoid": ["plastic props"],
        "poses": [
            Pose("wide", "01.png", ratio="16:9", use="establish the bench"),
            Pose("detail", "02.png", ratio="1:1", use="texture of the engraving"),
        ],
    }
    base.update(overrides)
    return Element(**base)


# ── C11-T1: round-trip ────────────────────────────────────────────────────────────────────────


def test_an_element_round_trips_every_field_it_declares(tmp_path):
    """A store that loses a field on the way to disk is a store nobody can trust with a `use`."""
    element = _object(
        provider_handles={"higgsfield_element_id": "elem-abc", "registered_on": "2026-09-07"}
    )
    store.write(element, "probe", content_root=tmp_path)
    back = store.load("probe", "brass-dial", content_root=tmp_path)

    assert back.name == element.name
    assert back.constraints == element.constraints
    assert back.avoid == element.avoid
    assert back.provider_handles == element.provider_handles
    assert [(p.name, p.file, p.ratio, p.use, p.animatable) for p in back.poses] == [
        (p.name, p.file, p.ratio, p.use, p.animatable) for p in element.poses
    ]
    assert back.updated, "the write must date itself — an undated element is unreviewable"


def test_a_value_the_writer_cannot_represent_raises_instead_of_landing_half_escaped(tmp_path):
    """The round-trip check is the safety property, not the serialiser's cleverness."""
    with pytest.raises(ElementError, match="control character"):
        store.write(_object(name="Brass\x07dial"), "probe", content_root=tmp_path)
    assert not store.element_path("probe", "brass-dial", content_root=tmp_path).exists()


@pytest.mark.parametrize("name", ['a "quoted" name', "a \\backslash\\ name", "a — dash — name"])
def test_awkward_but_legal_strings_survive_the_round_trip(tmp_path, name):
    """Positive control for the refusal above: escaping works, it is only control chars we refuse."""
    store.write(_object(name=name), "probe", content_root=tmp_path)
    assert store.load("probe", "brass-dial", content_root=tmp_path).name == name


# ── C11-T2: traversal ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "..", "."])
def test_a_traversing_slug_is_refused_before_anything_is_created(tmp_path, bad):
    """The tenant boundary's highest-risk error. Asserted on the directory, not just the raise."""
    with pytest.raises(ValueError):
        store.write(_object(slug=bad), "probe", content_root=tmp_path)
    assert list(tmp_path.iterdir()) == [], "a refused write still created something"


@pytest.mark.parametrize("bad", ["../x.png", "sub/x.png", "..\\x.png"])
def test_a_pose_file_with_a_separator_is_refused(tmp_path, bad):
    """A pose file is a bare name inside poses/ — a separator here is a traversal, not a path."""
    with pytest.raises(ElementError, match="bare filename"):
        store.write(_object(poses=[Pose("wide", bad)]), "probe", content_root=tmp_path)


def test_a_normal_slug_and_filename_are_accepted(tmp_path):
    """Positive control for both refusals above."""
    assert store.write(_object(), "probe", content_root=tmp_path).is_file()


# ── C11-T3: the pose vocabulary ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pose", sorted(IDENTITY_POSES_CHARACTER))
def test_every_character_identity_pose_is_accepted(pose):
    assert validate_pose_name(pose, kind="character") == pose


@pytest.mark.parametrize("pose", sorted(IDENTITY_POSES_OTHER))
def test_every_non_character_identity_pose_is_accepted(pose):
    assert validate_pose_name(pose, kind="object") == pose


@pytest.mark.parametrize("pose", ["expression:grin", "angle:low", "beat:the-hand-off"])
def test_the_namespaced_open_tails_are_accepted(pose):
    """The tail is open so an element can carry a pose this vocabulary never anticipated."""
    assert validate_pose_name(pose, kind="character") == pose


@pytest.mark.parametrize("pose", ["sideways", "head-left", "HEAD_LEFT", "wide"])
def test_an_unrecognised_bare_pose_name_is_refused_on_a_character(pose):
    """A pose set is only useful if a name means the same thing across elements."""
    with pytest.raises(ElementError, match="not an identity pose"):
        validate_pose_name(pose, kind="character")


def test_a_beat_pose_without_a_use_is_refused(tmp_path):
    """A frame cut for one moment is the pose most likely to be reused wrongly."""
    with pytest.raises(ElementError, match="declares no `use`"):
        store.write(
            _object(poses=[Pose("beat:the-hand-off", "01.png")]), "probe", content_root=tmp_path
        )
    # Positive control: the same pose WITH a use is stored.
    store.write(
        _object(
            poses=[Pose("beat:the-hand-off", "01.png", use="the moment the dial changes hands")]
        ),
        "probe",
        content_root=tmp_path,
    )


def test_a_pose_declared_twice_is_refused(tmp_path):
    """Two poses with one name means `resolve` silently picks whichever came first."""
    with pytest.raises(ElementError, match="declared twice"):
        store.write(
            _object(poses=[Pose("wide", "01.png"), Pose("wide", "02.png")]),
            "probe",
            content_root=tmp_path,
        )


# ── C11-T4: resolve order and confinement ─────────────────────────────────────────────────────


def _with_pose_files(tmp_path, element: Element) -> Element:
    store.write(element, "probe", content_root=tmp_path)
    poses = store.poses_dir("probe", element.slug, content_root=tmp_path)
    poses.mkdir(parents=True, exist_ok=True)
    for pose in element.poses:
        (poses / pose.file).write_bytes(b"\x89PNG\r\n\x1a\n")
    return element


def test_resolve_returns_pose_files_in_declaration_order_not_sorted(tmp_path):
    """Order is the contract: position 1 is what a prompt means by "the first reference"."""
    element = _with_pose_files(
        tmp_path,
        _object(poses=[Pose("detail", "zz.png", use="u"), Pose("wide", "aa.png", use="u")]),
    )
    names = [p.name for p in store.resolve_pose_files(element, "probe", content_root=tmp_path)]
    assert names == ["zz.png", "aa.png"], "resolve sorted the poses and re-pointed every prompt"


def test_reordering_the_element_reorders_what_resolve_returns(tmp_path):
    """The order is data the operator controls, not an accident of the filesystem."""
    element = _with_pose_files(tmp_path, _object())
    flipped = Element(
        **{
            **{f: getattr(element, f) for f in element.__dataclass_fields__},
            "poses": list(reversed(element.poses)),
        }
    )
    store.write(flipped, "probe", content_root=tmp_path)
    reloaded = store.load("probe", "brass-dial", content_root=tmp_path)
    names = [p.name for p in store.resolve_pose_files(reloaded, "probe", content_root=tmp_path)]
    assert names == ["02.png", "01.png"]


def test_a_pose_file_symlinked_out_of_the_content_root_is_refused(tmp_path):
    """`Path.resolve()` follows symlinks, so the check is about where the BYTES live."""
    outside = tmp_path.parent / "outside-secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    element = _with_pose_files(tmp_path, _object(poses=[Pose("wide", "01.png", use="u")]))
    target = store.poses_dir("probe", "brass-dial", content_root=tmp_path) / "01.png"
    target.unlink()
    target.symlink_to(outside)

    with pytest.raises(ConfinementError):
        store.resolve_pose_files(element, "probe", content_root=tmp_path)


def test_an_element_under_one_profile_is_invisible_to_another(tmp_path):
    """Never read one profile's content while bound to another (CLAUDE.md's tenant rule)."""
    store.write(_object(), "profile-a", content_root=tmp_path)
    assert store.list_slugs("profile-a", content_root=tmp_path) == ["brass-dial"]
    assert store.list_slugs("profile-b", content_root=tmp_path) == []
    with pytest.raises(ElementError, match="no element"):
        store.load("profile-b", "brass-dial", content_root=tmp_path)


# ── C11-T7: the consent route (the model half; the CLI half is in test_elements_cli) ──────────


def test_a_character_must_declare_what_it_depicts(tmp_path):
    """No default: the safe answer and the common answer are not the same one."""
    with pytest.raises(ElementError, match="must declare `depicts`"):
        store.write(
            Element(
                slug="pip", kind="character", name="Pip", poses=[Pose("head_straight", "01.png")]
            ),
            "probe",
            content_root=tmp_path,
        )


def test_depicts_is_meaningless_on_a_non_character(tmp_path):
    """A brass dial does not depict a person, and a field that reads as though it might is worse
    than no field: it invites the consent question to be answered on the wrong object."""
    with pytest.raises(ElementError, match="meaningful only on a character"):
        store.write(_object(depicts="fictional"), "probe", content_root=tmp_path)


# ── drafts ────────────────────────────────────────────────────────────────────────────────────


def test_a_draft_is_storable_but_not_usable(tmp_path):
    """The import lands a shape; a human lands the meaning. `resolve` waits for the second."""
    draft = _object(draft=True, poses=[Pose("wide", "01.png", use="TODO")])
    store.write(draft, "probe", content_root=tmp_path)  # storable
    with pytest.raises(ElementError, match="still a draft"):
        store.load("probe", "brass-dial", content_root=tmp_path).validate_usable()


def test_an_element_with_no_poses_cannot_resolve(tmp_path):
    """Positive control: an element is a pose set, not a name."""
    store.write(_object(poses=[]), "probe", content_root=tmp_path)
    with pytest.raises(ElementError, match="no poses"):
        store.load("probe", "brass-dial", content_root=tmp_path).validate_usable()


# ── the README is derived ─────────────────────────────────────────────────────────────────────


def test_the_readme_is_generated_and_says_so(tmp_path):
    element = _object()
    text = render_readme(element)
    assert "Generated" in text.split("\n", 4)[2], "the header must warn that hand edits are lost"
    for constraint in element.constraints + element.avoid:
        assert constraint in text
    for pose in element.poses:
        assert pose.use in text and pose.name in text


def test_rendering_the_readme_twice_is_byte_identical(tmp_path):
    """Derived means derived: a README that drifts between renders is a second source of truth."""
    element = _object()
    assert render_readme(element) == render_readme(element)


# ── the deletion ledger ───────────────────────────────────────────────────────────────────────


def test_a_deletion_is_recorded_with_its_reason(tmp_path):
    ledger = store.record_deletion(
        "probe", "brass-dial", "replaced by the milled version", content_root=tmp_path
    )
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["slug"] == "brass-dial"
    assert row["reason"] == "replaced by the milled version"
    assert row["deleted_on"], "an undated deletion cannot be reviewed against anything"


@pytest.mark.parametrize("bad", ["line one\nline two", "carriage\rreturn"])
def test_a_newline_in_a_string_is_refused_as_an_element_error_not_a_traceback(tmp_path, bad):
    """Review finding: \\n and \\r slipped past the control-char filter, `render_toml` emitted
    invalid TOML, and the round-trip parse raised a raw TOMLDecodeError instead of the refusal
    this store promises. Nothing may be written either way."""
    with pytest.raises(ElementError):
        store.write(_object(name=bad), "probe", content_root=tmp_path)
    assert not store.element_path("probe", "brass-dial", content_root=tmp_path).exists()


def test_a_tab_is_still_a_legal_character():
    """Positive control: TOML basic strings may carry a literal tab, and so may a `use`."""
    import tomllib

    from gtm_core.elements.store import render_toml

    text = render_toml(_object(name="two\twords"))
    assert tomllib.loads(text)["name"] == "two\twords"
