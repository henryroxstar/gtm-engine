"""What an element IS — the closed vocabularies, and the shape a pose set has to satisfy.

An **element** is a named, reusable thing a render can be pointed at: a character, an
environment, an object, a product, a wardrobe item. It is a small pile of stills plus the rules
for using them, living in the tenant's own content tree.

Why the pose names are a closed vocabulary
-------------------------------------------
A pose set exists so a later render can ask for *the one it needs* — the head turned left, the
wide establishing angle — without a human re-reading sixteen filenames. That only works if the
names mean the same thing across elements, so the identity poses are enumerated and anything
outside them is refused. The open tail is deliberate and namespaced: ``expression:<name>``,
``angle:<name>`` and ``beat:<slug>`` let an element carry poses this vocabulary never anticipated,
while still saying WHICH KIND of extra it is. A ``beat:`` pose must declare its ``use``, because a
frame made for one moment of one video is the pose most likely to be reused wrongly.

``depicts`` is not decoration
------------------------------
A character element either depicts a fictional person or a real one, and the difference decides
whether a consent note is required before it can exist at all (EU AI Act Art. 50 sits downstream
of that, at the publish gate). There is no default: an unstated ``depicts`` is refused, because
the safe answer and the common answer are not the same one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ElementError",
    "Element",
    "Pose",
    "KINDS",
    "DEPICTS",
    "IDENTITY_POSES_CHARACTER",
    "IDENTITY_POSES_OTHER",
    "POSE_NAMESPACES",
    "validate_pose_name",
]


class ElementError(ValueError):
    """The element, or one of its poses, is not a shape this library will store."""


#: What an element can be. A `character` is the only kind that can depict a person, and so the
#: only kind the consent route applies to.
KINDS: frozenset[str] = frozenset({"character", "environment", "object", "product", "wardrobe"})

#: Required on a `character`. No default — see the module docstring.
DEPICTS: frozenset[str] = frozenset({"fictional", "real_person"})

#: The identity poses of a character: enough to re-establish the same person from any angle.
IDENTITY_POSES_CHARACTER: frozenset[str] = frozenset(
    {"full_body_front", "full_body_angle", "head_straight", "head_left", "head_right"}
)

#: The identity poses of everything else. An object needs far less: what it is, and one detail
#: close enough to carry texture.
IDENTITY_POSES_OTHER: frozenset[str] = frozenset({"wide", "detail"})

#: Namespaced open tails. The prefix says which KIND of extra pose this is, so an unfamiliar name
#: is still legible; `beat:` additionally requires a `use`, being the most reusable-wrongly kind.
POSE_NAMESPACES: tuple[str, ...] = ("expression:", "angle:", "beat:")

#: Aspect ratios a pose may declare. `animatable` is proposed from this, never inferred silently.
_RATIOS: frozenset[str] = frozenset({"9:16", "16:9", "1:1", "4:5", "5:4", "4:3", "3:4"})


def _identity_poses(kind: str) -> frozenset[str]:
    return IDENTITY_POSES_CHARACTER if kind == "character" else IDENTITY_POSES_OTHER


def validate_pose_name(name: str, *, kind: str) -> str:
    """Return ``name`` if this element kind may carry a pose by that name, else raise."""
    if not name or "\x00" in name or name.strip() != name:
        raise ElementError(f"pose name {name!r} is empty, padded, or carries a NUL")
    if name in _identity_poses(kind):
        return name
    for prefix in POSE_NAMESPACES:
        if name.startswith(prefix):
            tail = name[len(prefix) :]
            if not tail:
                raise ElementError(f"pose name {name!r} has an empty {prefix!r} tail")
            if "/" in tail or "\\" in tail or ".." in tail:
                raise ElementError(f"pose name {name!r} carries a path separator in its tail")
            return name
    raise ElementError(
        f"pose name {name!r} is not an identity pose for a {kind!r} element "
        f"({sorted(_identity_poses(kind))}) and carries none of the open-tail prefixes "
        f"{list(POSE_NAMESPACES)}. A pose set is only useful if a name means the same thing "
        "across elements, so an unrecognised bare name is refused rather than stored."
    )


@dataclass(frozen=True)
class Pose:
    """One still, and what it is for.

    ``use`` is what turns a pile of images into a library: it is the sentence a later render
    reads to decide whether THIS is the frame it wants. Required on a ``beat:`` pose, because a
    frame cut for one moment carries assumptions the filename does not.
    """

    name: str
    file: str
    ratio: str = ""
    use: str = ""
    animatable: bool = False

    def validate(self, *, kind: str) -> None:
        validate_pose_name(self.name, kind=kind)
        if not self.file or "/" in self.file or "\\" in self.file or ".." in self.file:
            raise ElementError(
                f"pose {self.name!r} file {self.file!r} must be a bare filename inside the "
                "element's own poses/ directory — a separator here is a traversal, not a path"
            )
        if self.ratio and self.ratio not in _RATIOS:
            raise ElementError(f"pose {self.name!r} ratio {self.ratio!r} not in {sorted(_RATIOS)}")
        if self.name.startswith("beat:") and not self.use.strip():
            raise ElementError(
                f"pose {self.name!r} is a beat pose and declares no `use`. A frame made for one "
                "moment of one video is the pose most likely to be reused wrongly; say what it "
                "is for, or give it an identity-pose name instead."
            )


@dataclass(frozen=True)
class Element:
    """A named reusable subject plus its pose set, its constraints, and its provider handles."""

    slug: str
    kind: str
    name: str
    depicts: str = ""
    draft: bool = False
    constraints: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    poses: list[Pose] = field(default_factory=list)
    provider_handles: dict[str, str] = field(default_factory=dict)
    consent_ref: str = ""
    updated: str = ""

    def validate(self) -> None:
        """Raise unless this element is storable AND usable. A draft is storable, not usable."""
        if self.kind not in KINDS:
            raise ElementError(f"kind {self.kind!r} is not one of {sorted(KINDS)}")
        if not self.name.strip():
            raise ElementError("an element needs a human name, not just a slug")
        if self.kind == "character":
            if self.depicts not in DEPICTS:
                raise ElementError(
                    f"a character element must declare `depicts` as one of {sorted(DEPICTS)}, got "
                    f"{self.depicts!r}. There is no default: whether this is a real person decides "
                    "whether a consent note is required before the element may exist."
                )
        elif self.depicts:
            raise ElementError(f"`depicts` is meaningful only on a character, not a {self.kind!r}")

        seen: set[str] = set()
        for pose in self.poses:
            pose.validate(kind=self.kind)
            if pose.name in seen:
                raise ElementError(f"pose {pose.name!r} is declared twice")
            seen.add(pose.name)

    def validate_usable(self) -> None:
        """Everything :meth:`validate` checks, plus the reasons a DRAFT is not yet a library entry."""
        self.validate()
        if self.draft:
            raise ElementError(
                f"element {self.slug!r} is still a draft — an imported pose set whose `use` "
                "strings are placeholders. Fill them in (`set-pose --use ...`) and clear the flag; "
                "a pose nobody has described is a pose the next render picks wrongly."
            )
        if not self.poses:
            raise ElementError(f"element {self.slug!r} has no poses, so nothing can resolve it")
