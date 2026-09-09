"""`python -m gtm_core.elements <verb>` — the ONLY writer of an element.

Skills are markdown executed by the brain and cannot import Python, so every operation a body
needs is a verb here. The important consequence is not convenience: it is that no skill ever runs
``Edit``/``Write`` on a file under ``brand/``, so every write is schema-checked, round-trip
verified, and dated by the same code path.

Exit codes: 0 done; 1 unreadable input; 2 a refusal (the element, the consent route, or a path).
"""

from __future__ import annotations

import argparse
import json
import shutil
import tomllib
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..confine import ConfinementError
from ..paths import resolve_content_root, resolve_profiles_root
from . import store
from .legacy_import import plan_import
from .model import Element, ElementError, Pose, validate_pose_name
from .readme import render_readme

__all__ = ["main"]

_CONTACT_SHEET = "contact-sheet.jpg"
_README = "README.md"


def _profiles_root(args) -> Path:
    """Where the brand kits live. An explicit ``--profiles-root`` wins.

    The flag exists for the same reason ``--content-root`` does: so a caller (a test, an
    onboarding staging run) can bind BOTH tenant roots explicitly instead of reaching into module
    state. A `from ... import` of the resolver would make that binding un-overridable and push
    every caller into patching a private copy, which is how a mock stops testing the real path.
    """
    return args.profiles_root if args.profiles_root is not None else resolve_profiles_root()


def _consent_note(args) -> str:
    """The tenant's configured consent note, or ``""`` — read through brandkit, never guessed."""
    from ..brandkit import load_brand_kit, lookup

    # Every way of NOT having a note reads the same here: no kit, an unreadable one, or a kit
    # with no such key. `lookup` raises KeyError for the last of those, and treating that as a
    # crash rather than as "no consent recorded" would fail the define with a traceback instead
    # of the refusal that names `identity-kit`.
    try:
        kit = load_brand_kit(_profiles_root(args), args.profile, args.product)
        return str(lookup(kit, "identity.consent_note") or "").strip()
    except (KeyError, OSError, ValueError):
        return ""


# ── verbs ─────────────────────────────────────────────────────────────────────────────────────


def _cmd_list(args) -> int:
    slugs = store.list_slugs(args.profile, content_root=args.content_root)
    print(json.dumps({"profile": args.profile, "elements": slugs}, indent=2))
    return 0


def _cmd_show(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    print(json.dumps(store._as_dict(element), indent=2))
    return 0


def _cmd_define(args) -> int:
    path = store.element_path(args.profile, args.slug, content_root=args.content_root)
    if path.is_file():
        raise ElementError(
            f"element {args.slug!r} already exists at {path}. `define` creates; use `set` to "
            "change one that exists, so an accidental re-define cannot silently drop a pose set."
        )
    if args.kind == "character" and args.depicts == "real_person":
        note = _consent_note(args)
        if not note:
            raise ElementError(
                "this element depicts a REAL PERSON and the profile's brand kit records no "
                "`identity.consent_note`. A likeness the tenant cannot show consent for is not "
                "storable here. Run `identity-kit` to capture consent first — the note is what "
                "this element will cite. (A `fictional` character needs none.)"
            )
    element = Element(
        slug=args.slug,
        kind=args.kind,
        name=args.name,
        depicts=args.depicts,
        consent_ref=(
            f"identity.consent_note@{date.today().isoformat()}"
            if args.depicts == "real_person"
            else ""
        ),
    )
    written = store.write(element, args.profile, content_root=args.content_root)
    store.poses_dir(args.profile, args.slug, content_root=args.content_root).mkdir(
        parents=True, exist_ok=True
    )
    print(json.dumps({"defined": args.slug, "path": str(written)}, indent=2))
    return 0


def _cmd_set(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    changes: dict = {}
    if args.name:
        changes["name"] = args.name
    if args.constraints is not None:
        changes["constraints"] = args.constraints
    if args.avoid is not None:
        changes["avoid"] = args.avoid
    if args.handle:
        handles = dict(element.provider_handles)
        for pair in args.handle:
            key, _, value = pair.partition("=")
            if not key or not value:
                raise ElementError(f"--handle takes key=value, got {pair!r}")
            handles[key] = value
        changes["provider_handles"] = handles
    if args.clear_draft:
        changes["draft"] = False
    written = store.write(replace(element, **changes), args.profile, content_root=args.content_root)
    print(json.dumps({"updated": args.slug, "path": str(written)}, indent=2))
    return 0


def _cmd_add_pose(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    validate_pose_name(args.pose, kind=element.kind)
    if any(p.name == args.pose for p in element.poses):
        raise ElementError(f"pose {args.pose!r} already exists — use `set-pose` to change it")
    pose = Pose(
        name=args.pose, file=args.file, ratio=args.ratio, use=args.use, animatable=args.animatable
    )
    written = store.write(
        replace(element, poses=[*element.poses, pose]),
        args.profile,
        content_root=args.content_root,
    )
    print(json.dumps({"added": args.pose, "path": str(written)}, indent=2))
    return 0


def _cmd_set_pose(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    poses, hit = [], False
    for pose in element.poses:
        if pose.name != args.pose:
            poses.append(pose)
            continue
        hit = True
        poses.append(
            Pose(
                name=args.rename or pose.name,
                file=args.file or pose.file,
                ratio=args.ratio if args.ratio is not None else pose.ratio,
                use=args.use if args.use is not None else pose.use,
                animatable=pose.animatable if args.animatable is None else args.animatable,
            )
        )
    if not hit:
        raise ElementError(f"element {args.slug!r} has no pose named {args.pose!r}")
    written = store.write(
        replace(element, poses=poses), args.profile, content_root=args.content_root
    )
    print(json.dumps({"updated_pose": args.pose, "path": str(written)}, indent=2))
    return 0


def _cmd_rm_pose(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    poses = [p for p in element.poses if p.name != args.pose]
    if len(poses) == len(element.poses):
        raise ElementError(f"element {args.slug!r} has no pose named {args.pose!r}")
    written = store.write(
        replace(element, poses=poses), args.profile, content_root=args.content_root
    )
    print(json.dumps({"removed_pose": args.pose, "path": str(written)}, indent=2))
    return 0


def _cmd_delete(args) -> int:
    from .refs import references_to

    users = references_to(args.profile, args.slug, content_root=args.content_root)
    if users and not args.force:
        raise ElementError(
            f"element {args.slug!r} is still named by {len(users)} file(s): {users}. Deleting it "
            "turns a resolvable reference into a silent nothing at render time. Re-point those "
            "files, or pass --force --reason '<why>' to record the decision."
        )
    if args.force and not (args.reason or "").strip():
        raise ElementError("--force needs --reason: an unexplained forced delete is unreviewable")

    directory = store.element_dir(args.profile, args.slug, content_root=args.content_root)
    if not directory.is_dir():
        raise ElementError(f"no element {args.slug!r} to delete")
    # Local only. This never calls a provider: a registered element id may be referenced by a
    # render nobody in this tree knows about, so revoking it is the operator's call on the
    # provider's own surface, not a side effect of tidying a folder.
    shutil.rmtree(directory)
    ledger = store.record_deletion(
        args.profile,
        args.slug,
        args.reason or "no longer referenced",
        content_root=args.content_root,
    )
    print(
        json.dumps(
            {"deleted": args.slug, "ledger": str(ledger), "was_referenced_by": users}, indent=2
        )
    )
    return 0


def _cmd_resolve(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    element.validate_usable()
    if args.for_ == "higgsfield":
        handle = element.provider_handles.get("higgsfield_element_id", "")
        print(
            json.dumps(
                {
                    "slug": args.slug,
                    "higgsfield_element_id": handle or None,
                    "note": ""
                    if handle
                    else "local-only: this element has no provider handle, "
                    "so render it from its pose files instead",
                },
                indent=2,
            )
        )
        return 0
    paths = store.resolve_pose_files(element, args.profile, content_root=args.content_root)
    print(json.dumps({"slug": args.slug, "reference_images": [str(p) for p in paths]}, indent=2))
    return 0


def _cmd_contact_sheet(args) -> int:
    from ..cover_frame import sibling_sheet

    element = store.load(args.profile, args.slug, content_root=args.content_root)
    if not element.poses:
        raise ElementError(f"element {args.slug!r} has no poses to sheet")
    paths = store.resolve_pose_files(element, args.profile, content_root=args.content_root)
    dst = (
        store.element_dir(args.profile, args.slug, content_root=args.content_root) / _CONTACT_SHEET
    )
    print(json.dumps(sibling_sheet(paths, dst, columns=args.columns), indent=2))
    return 0


def _cmd_render_readme(args) -> int:
    element = store.load(args.profile, args.slug, content_root=args.content_root)
    dst = store.element_dir(args.profile, args.slug, content_root=args.content_root) / _README
    dst.write_text(render_readme(element), encoding="utf-8")
    print(json.dumps({"wrote": str(dst)}, indent=2))
    return 0


def _cmd_sync_brandkit(args) -> int:
    from ..brandkit import identity_write_target, set_identity_value

    handles, skipped = [], []
    for slug in store.list_slugs(args.profile, content_root=args.content_root):
        element = store.load(args.profile, slug, content_root=args.content_root)
        handle = element.provider_handles.get("higgsfield_element_id", "")
        (handles.append(handle) if handle else skipped.append(slug))

    if not handles and not args.allow_empty:
        raise ElementError(
            f"no element under profile {args.profile!r} carries a higgsfield_element_id, so this "
            "would clear identity.reference_element_ids entirely. That is almost never what a "
            "sync means — pass --allow-empty if it is."
        )
    target = identity_write_target(_profiles_root(args), args.profile, args.product)

    # A no-op sync must be a no-op ON DISK. `set_identity_value` is a targeted line edit that
    # replaces the whole line, comment included, with its own dated one — so re-writing an
    # already-correct value silently downgrades a comment a human wrote. Not hypothetical: a live
    # product kit was found carrying this key with several sentences of provenance on the same
    # line (where the Element was seeded from, why an Element and not a Soul, where the pose
    # library lives), and a sync that "changed nothing" would have replaced all of it with a
    # one-line stamp.
    #
    # Compared against the TARGET FILE's own value, never the merged kit: a merge can inherit
    # this key from the company kit while the product file being written holds nothing, and
    # skipping on an inherited value would leave the override unwritten.
    current = None
    if target.is_file():
        try:
            current = (
                tomllib.loads(target.read_text(encoding="utf-8"))
                .get("identity", {})
                .get("reference_element_ids")
            )
        except (OSError, tomllib.TOMLDecodeError):
            current = None

    if current == handles:
        print(
            json.dumps(
                {
                    "kit": str(target),
                    "reference_element_ids": handles,
                    "skipped_local_only": skipped,
                    "wrote": False,
                    "note": "already correct — left untouched, so any comment on that line "
                    "survives",
                },
                indent=2,
            )
        )
        return 0

    set_identity_value(
        target, "identity.reference_element_ids", handles, note="elements sync", create=True
    )
    print(
        json.dumps(
            {
                "kit": str(target),
                "reference_element_ids": handles,
                "skipped_local_only": skipped,
                "wrote": True,
            },
            indent=2,
        )
    )
    return 0


def _cmd_import_legacy(args) -> int:
    # The source folder must sit inside the content root too. Without this the confinement branch
    # in plan_import was dead code and any file on disk could be copied into the tenant tree as a
    # "pose" that `resolve` would later hand to a provider. Found in review.
    root = args.content_root if args.content_root is not None else resolve_content_root()
    element, files = plan_import(
        args.src,
        slug=args.slug,
        kind=args.kind,
        name=args.name,
        depicts=args.depicts,
        content_root=root,
    )
    if store.element_path(args.profile, args.slug, content_root=args.content_root).is_file():
        raise ElementError(f"element {args.slug!r} already exists — import into a fresh slug")

    dest = store.poses_dir(args.profile, args.slug, content_root=args.content_root)
    dest.mkdir(parents=True, exist_ok=True)
    for src in files:
        shutil.copy2(src, dest / src.name)
    written = store.write(element, args.profile, content_root=args.content_root)
    print(
        json.dumps(
            {
                "imported": args.slug,
                "path": str(written),
                "poses": len(element.poses),
                "draft": True,
                "next": 'every pose carries `use = "TODO"`. Fill them with `set-pose --pose '
                "<name> --use '<what it is for>'`, then `set --clear-draft`; until then "
                "`resolve` refuses, because a pose nobody described is one the next "
                "render picks wrongly.",
            },
            indent=2,
        )
    )
    return 0


# ── parser ────────────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.elements")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--product", default=None, help="product slug, for brand-kit resolution")
    parser.add_argument("--content-root", type=Path, default=None, help="override the content root")
    parser.add_argument(
        "--profiles-root", type=Path, default=None, help="override the profiles root"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="element slugs under this profile")

    p = sub.add_parser("show", help="one element as JSON")
    p.add_argument("slug")

    p = sub.add_parser("define", help="create a new element")
    p.add_argument("slug")
    p.add_argument("--kind", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--depicts", default="", help="fictional | real_person (characters only)")

    p = sub.add_parser("set", help="change an element's fields")
    p.add_argument("slug")
    p.add_argument("--name", default="")
    p.add_argument("--constraints", nargs="*", default=None, help="what must ALWAYS be true")
    p.add_argument("--avoid", nargs="*", default=None, help="what must NEVER appear")
    p.add_argument("--handle", action="append", default=[], help="key=value provider handle")
    p.add_argument("--clear-draft", action="store_true")

    p = sub.add_parser("add-pose", help="attach a still")
    p.add_argument("slug")
    p.add_argument("--pose", required=True)
    p.add_argument("--file", required=True, help="bare filename inside poses/")
    p.add_argument("--ratio", default="")
    p.add_argument("--use", default="")
    p.add_argument("--animatable", action="store_true")

    p = sub.add_parser("set-pose", help="change one pose")
    p.add_argument("slug")
    p.add_argument("--pose", required=True)
    p.add_argument("--rename", default="")
    p.add_argument("--file", default="")
    p.add_argument("--ratio", default=None)
    p.add_argument("--use", default=None)
    p.add_argument("--animatable", type=lambda v: v.lower() in {"1", "true", "yes"}, default=None)

    p = sub.add_parser("rm-pose", help="detach a still")
    p.add_argument("slug")
    p.add_argument("--pose", required=True)

    p = sub.add_parser("delete", help="remove an element locally (never touches the provider)")
    p.add_argument("slug")
    p.add_argument("--force", action="store_true")
    p.add_argument("--reason", default="")

    p = sub.add_parser("resolve", help="what a render should be handed for this element")
    p.add_argument("slug")
    p.add_argument("--for", dest="for_", choices=("headless", "higgsfield"), default="headless")

    p = sub.add_parser("contact-sheet", help="tile the pose set into one image")
    p.add_argument("slug")
    p.add_argument("--columns", type=int, default=4)

    p = sub.add_parser("render-readme", help="regenerate the element's README")
    p.add_argument("slug")

    p = sub.add_parser(
        "sync-brandkit", help="write registered handles to identity.reference_element_ids"
    )
    p.add_argument("--allow-empty", action="store_true")

    p = sub.add_parser("import-legacy", help="import a hand-made poses/ folder as a draft")
    p.add_argument("src", type=Path)
    p.add_argument("--slug", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--depicts", default="")
    return parser


_VERBS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "define": _cmd_define,
    "set": _cmd_set,
    "add-pose": _cmd_add_pose,
    "set-pose": _cmd_set_pose,
    "rm-pose": _cmd_rm_pose,
    "delete": _cmd_delete,
    "resolve": _cmd_resolve,
    "contact-sheet": _cmd_contact_sheet,
    "render-readme": _cmd_render_readme,
    "sync-brandkit": _cmd_sync_brandkit,
    "import-legacy": _cmd_import_legacy,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _VERBS[args.cmd](args)
    except (ValueError, ConfinementError) as exc:  # ElementError, _safe_segment, brandkit guards
        print(f"[elements] {exc}")
        return 2
    except OSError as exc:
        print(f"[elements] {exc}")
        return 1
