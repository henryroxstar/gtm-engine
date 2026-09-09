"""Where an element lives on disk, and the only code that writes one.

Layout, under the resolved content root:

    content/<profile>/brand/<slug>/element.toml
    content/<profile>/brand/<slug>/poses/<file>
    content/<profile>/brand/<slug>/README.md          (generated)
    content/<profile>/brand/<slug>/contact-sheet.jpg  (generated)
    content/<profile>/brand/deleted.jsonl             (append-only)

Every segment passes through :func:`gtm_core.paths._safe_segment`, and every pose path is
resolved through :mod:`gtm_core.confine` before a byte is read — this store holds reference
imagery that a render path ships to a third party, so a path here is an exfiltration primitive
before it is a convenience.

Why a hand-rolled TOML writer
------------------------------
The stdlib reads TOML and does not write it, and the one other writer in this repo
(:mod:`gtm_core.brandkit`) is a targeted line editor for a file a human owns — the wrong tool for
a file this module owns outright. So this serialises a fixed, closed schema. The safety property
is not the serialiser's cleverness, it is that :func:`write` **parses its own output and compares
it to the input before touching disk**: a value this writer cannot round-trip raises rather than
being written half-escaped. Then the file lands through ``os.replace``, so a reader never sees a
partial element.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..confine import confined_source_file
from ..fsio import atomic_write_text
from ..paths import _safe_segment, resolve_content_root
from .model import Element, ElementError, Pose

__all__ = [
    "ELEMENT_FILENAME",
    "element_dir",
    "element_path",
    "poses_dir",
    "brand_root",
    "load",
    "write",
    "list_slugs",
    "resolve_pose_files",
    "record_deletion",
    "render_toml",
]

ELEMENT_FILENAME = "element.toml"
_BRAND_DIRNAME = "brand"
_POSES_DIRNAME = "poses"
_DELETED_LEDGER = "deleted.jsonl"

#: Anything a TOML basic string cannot carry literally. Refused rather than escaped: an element
#: description with a raw control character is a paste accident, not a requirement.
#: Everything but TAB. The first version let \n and \r through, and a TOML basic string cannot
#: carry either literally — so a `use` with a pasted newline rendered as invalid TOML and the
#: round-trip parse raised a raw TOMLDecodeError instead of the ElementError this module promises.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def brand_root(profile: str, *, content_root: Path | None = None) -> Path:
    """``content/<profile>/brand/`` — the parent every element sits under."""
    root = content_root if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / _BRAND_DIRNAME


def element_dir(profile: str, slug: str, *, content_root: Path | None = None) -> Path:
    return brand_root(profile, content_root=content_root) / _safe_segment(slug, "element slug")


def element_path(profile: str, slug: str, *, content_root: Path | None = None) -> Path:
    return element_dir(profile, slug, content_root=content_root) / ELEMENT_FILENAME


def poses_dir(profile: str, slug: str, *, content_root: Path | None = None) -> Path:
    return element_dir(profile, slug, content_root=content_root) / _POSES_DIRNAME


# ── serialisation ─────────────────────────────────────────────────────────────────────────────


def _s(value: str) -> str:
    """A TOML basic string, or a refusal."""
    if _CONTROL_CHAR_RE.search(value):
        raise ElementError(f"value {value!r} carries a control character and will not be written")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list(values: list[str]) -> str:
    return "[" + ", ".join(_s(v) for v in values) + "]"


def _as_dict(element: Element) -> dict:
    """The exact document :func:`render_toml` claims to produce — the round-trip's left side."""
    doc: dict = {
        "slug": element.slug,
        "kind": element.kind,
        "name": element.name,
        "draft": element.draft,
        "constraints": list(element.constraints),
        "avoid": list(element.avoid),
        "updated": element.updated,
    }
    if element.depicts:
        doc["depicts"] = element.depicts
    if element.consent_ref:
        doc["consent_ref"] = element.consent_ref
    if element.provider_handles:
        doc["provider_handles"] = dict(element.provider_handles)
    # Only when non-empty: `render_toml` emits `[[poses]]` tables, and zero tables parses back
    # with no `poses` key at all. This dict has to describe what the serialiser ACTUALLY writes,
    # or the round-trip check fails on a legitimate element instead of on a broken one.
    if element.poses:
        doc["poses"] = [
            {
                "name": p.name,
                "file": p.file,
                "ratio": p.ratio,
                "use": p.use,
                "animatable": p.animatable,
            }
            for p in element.poses
        ]
    return doc


def render_toml(element: Element) -> str:
    """Serialise ``element``. Pure — takes no filesystem and returns text."""
    lines = [
        "# Generated by `python -m gtm_core.elements`. Edit through the CLI, not by hand:",
        "# every write here is verified to round-trip before it reaches disk.",
        "",
        f"slug = {_s(element.slug)}",
        f"kind = {_s(element.kind)}",
        f"name = {_s(element.name)}",
    ]
    if element.depicts:
        lines.append(f"depicts = {_s(element.depicts)}")
    lines += [
        f"draft = {'true' if element.draft else 'false'}",
        f"constraints = {_list(element.constraints)}",
        f"avoid = {_list(element.avoid)}",
        f"updated = {_s(element.updated)}",
    ]
    if element.consent_ref:
        lines.append(f"consent_ref = {_s(element.consent_ref)}")
    if element.provider_handles:
        lines += ["", "[provider_handles]"]
        lines += [f"{k} = {_s(v)}" for k, v in element.provider_handles.items()]
    for pose in element.poses:
        lines += [
            "",
            "[[poses]]",
            f"name = {_s(pose.name)}",
            f"file = {_s(pose.file)}",
            f"ratio = {_s(pose.ratio)}",
            f"use = {_s(pose.use)}",
            f"animatable = {'true' if pose.animatable else 'false'}",
        ]
    return "\n".join(lines) + "\n"


def _from_doc(doc: dict) -> Element:
    poses = [
        Pose(
            name=str(p.get("name", "")),
            file=str(p.get("file", "")),
            ratio=str(p.get("ratio", "")),
            use=str(p.get("use", "")),
            animatable=bool(p.get("animatable", False)),
        )
        for p in doc.get("poses", [])
        if isinstance(p, dict)
    ]
    return Element(
        slug=str(doc.get("slug", "")),
        kind=str(doc.get("kind", "")),
        name=str(doc.get("name", "")),
        depicts=str(doc.get("depicts", "")),
        draft=bool(doc.get("draft", False)),
        constraints=[str(c) for c in doc.get("constraints", [])],
        avoid=[str(a) for a in doc.get("avoid", [])],
        poses=poses,
        provider_handles={str(k): str(v) for k, v in (doc.get("provider_handles") or {}).items()},
        consent_ref=str(doc.get("consent_ref", "")),
        updated=str(doc.get("updated", "")),
    )


# ── read / write ──────────────────────────────────────────────────────────────────────────────


def load(profile: str, slug: str, *, content_root: Path | None = None) -> Element:
    path = element_path(profile, slug, content_root=content_root)
    if not path.is_file():
        raise ElementError(
            f"no element {slug!r} under {brand_root(profile, content_root=content_root)}"
        )
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ElementError(f"element {slug!r} is unreadable: {exc}") from exc
    return _from_doc(doc)


def write(
    element: Element, profile: str, *, content_root: Path | None = None, today: date | None = None
) -> Path:
    """Validate, serialise, verify the round-trip, then land the file atomically.

    The order matters. Validation first, so a malformed element never reaches the serialiser; the
    round-trip check second, so a value this writer cannot represent raises instead of being
    written half-escaped; ``os.replace`` last, so a reader never observes a partial element.
    """
    element.validate()
    stamped = replace(element, updated=(today or date.today()).isoformat())
    text = render_toml(stamped)

    # A parse failure here is OUR bug (the serialiser emitted something tomllib rejects), and
    # it must surface as the refusal this function promises, never as a traceback.
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ElementError(
            f"element {element.slug!r} serialised to invalid TOML ({exc}). Nothing was written."
        ) from exc
    if parsed != _as_dict(stamped):
        raise ElementError(
            f"element {element.slug!r} did not round-trip: the TOML this writer produced parses "
            "back to a different document. Nothing was written. This is the check that stops a "
            "half-escaped value becoming a silently-wrong element file."
        )

    path = element_path(profile, element.slug, content_root=content_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The shared atomic writer, not a private copy: it exists because four modules had each
    # hand-rolled tmp-then-os.replace, and it adds the fsync and unique temp name this lacked.
    atomic_write_text(path, text)
    return path


def list_slugs(profile: str, *, content_root: Path | None = None) -> list[str]:
    root = brand_root(profile, content_root=content_root)
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / ELEMENT_FILENAME).is_file())


def resolve_pose_files(
    element: Element, profile: str, *, content_root: Path | None = None
) -> list[Path]:
    """Absolute pose paths **in declaration order**, each confined to the content root.

    Order is the contract, exactly as it is for a shot's ``reference_images``: position 1 is what
    a prompt means by "the first reference". Sorting here would silently re-point every prompt.
    """
    root = (content_root if content_root is not None else resolve_content_root()).resolve()
    base = poses_dir(profile, element.slug, content_root=content_root)
    return [confined_source_file(base / p.file, content_root=root) for p in element.poses]


def record_deletion(
    profile: str,
    slug: str,
    reason: str,
    *,
    content_root: Path | None = None,
    today: date | None = None,
) -> Path:
    """Append to the brand tree's deletion ledger. Never calls a provider — see the CLI."""
    ledger = brand_root(profile, content_root=content_root) / _DELETED_LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    row = {"slug": slug, "reason": reason, "deleted_on": (today or date.today()).isoformat()}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return ledger
