"""Storyboard approval — the recorded half of ``video-storyboard``'s ⟦GATE:plan⟧.

The gate itself is a cockpit button. What was missing is any *durable trace* that it was pressed:
``storyboard.json`` recorded the frames but not the decision, so ``video-render`` had nothing to
check and the whole gate reduced to prose asking the agent to behave. On 2026-08-18 a full
synthetic render ran end to end without the storyboard stage being invoked at all, and nothing
objected until the operator watched the finished video.

So approval is a **write**, and ``render_manifest._validate_storyboard`` refuses a synthetic
render whose storyboard does not carry it. Same shape as the brand kit: never hand-edit the
JSON — go through this CLI, which verifies the file round-trips before it lands.

    uv run python -m gtm_core.storyboard approve <path> --by <who> --at <ISO-8601>
    uv run python -m gtm_core.storyboard status  <path>

``--at`` is required rather than defaulted to "now": the timestamp records when the *operator*
decided, which is not necessarily when this ran.

Lineage (2026-09-06, C1). Reference-image conditioning makes a DELTA-EDIT CHAIN the normal way
to build a storyboard: one hero still, then N frame-compatible derivatives each generated from
the last. That is **one** identity anchor and N job ids, which the drift check below would have
refused — exactly the workflow the reference parameter exists to enable, and the skill body's
own guardrail says not to reach for ``--allow-anchors`` to make something pass. The fix is
lineage rather than a looser cap: a derived entry records ``derived_from: <image_job_id>`` and
the grouping walks to the ROOT. One hero, N derivatives, ``allow_anchors=1`` still holds, and
two independently generated stills are still two anchors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class StoryboardError(ValueError):
    """Raised when a storyboard cannot be read, or an approval would be meaningless."""


def _load(path: Path) -> dict:
    if not path.exists():
        raise StoryboardError(f"no such storyboard: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoryboardError(f"storyboard is unreadable: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise StoryboardError(f"storyboard is not a JSON object: {path}")
    return data


#: How many delta edits deep a chain may run before the operator is told. Reference quality is
#: the ceiling on output quality and it degrades with every edit pass, so a long chain drifts
#: from the hero it claims to descend from. A WARN, never a refusal — re-anchoring from the hero
#: is the remedy and it is the operator's call. ``gtm_core.shots_lint`` imports this constant
#: rather than declaring its own, so the cap has one home.
MAX_EDIT_DEPTH = 4


def _parent_map(entries: list) -> dict[str, str]:
    """``{job_id: parent_job_id}`` for every entry declaring one, anchored or not.

    Built from ALL entries, not only identity-bearing ones: a chain may legitimately pass
    through a frame that carries no ``identity_anchor`` of its own.
    """
    parents: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        job_id = str(entry.get("image_job_id") or "")
        parent = str(entry.get("derived_from") or "")
        if job_id and parent:
            parents[job_id] = parent
    return parents


def _root_of(job_id: str, parents: dict[str, str], known: set[str]) -> tuple[str, int]:
    """Walk ``derived_from`` to the root anchor. Returns ``(root_job_id, edit_depth)``.

    Raises :class:`StoryboardError` on a parent that is not an entry of this storyboard, or on a
    cycle. Both are refusals rather than fallbacks on purpose: this walk is what lets a chain
    pass ``allow_anchors=1``, so an unverifiable parent pointer would be a way to launder two
    real anchors into one — the precise thing the cap exists to prevent. An entry with no
    ``derived_from`` is its own root at depth 0, which is why every storyboard written before
    this field existed keeps its historical anchor count.
    """
    seen = [job_id]
    current = job_id
    while current in parents:
        parent = parents[current]
        if parent in seen:
            raise StoryboardError(
                f"storyboard lineage is circular: {' -> '.join(seen)} -> {parent}. A "
                "`derived_from` chain must end at a hero still, not loop back on itself."
            )
        if parent not in known:
            raise StoryboardError(
                f"entry {current[:8]} declares derived_from={parent[:8]}, which is not an "
                "image_job_id in this storyboard. Lineage is what lets a delta-edit chain count "
                "as ONE identity anchor, so a parent nobody can check is refused rather than "
                "trusted — cite a still that is an entry here, or drop the field."
            )
        seen.append(parent)
        current = parent
    return current, len(seen) - 1


def edit_depths(entries: list) -> dict[str, int]:
    """``{job_id: edit_depth}`` for every entry, derived by walking ``derived_from``.

    Depth is DERIVED here and merely recorded on the entry: a stored number nobody checks is a
    confidently wrong number waiting to happen, so :func:`approve` verifies any declared
    ``edit_depth`` against this and refuses a disagreement.
    """
    parents = _parent_map(entries)
    known = {
        str(e.get("image_job_id") or "")
        for e in entries
        if isinstance(e, dict) and e.get("image_job_id")
    }
    depths: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        job_id = str(entry.get("image_job_id") or "")
        if not job_id:
            continue
        depths[job_id] = _root_of(job_id, parents, known)[1]
    return depths


def anchor_drift(entries: list) -> dict[str, list]:
    """Group a storyboard's entries by ``image_job_id`` — the identity-anchor drift check.

    A trained likeness reaches video as a start frame, and every independent still generation
    re-invents whatever the prompt did not pin: outfit, lighting, room, hair. So N stills for N
    shots means N independently invented wardrobes, which is what "why am I doing wardrobe
    changes?" describes. Verified 2026-08-18: five shots, five distinct ``image_job_id`` values
    (``51a7c697``, ``80e75eb8``, ``14fd65ed``, ``191ba746``, ``2636dede``), five different jackets
    in one 32-second video.

    The discipline is one hero still per look, reused as the start frame for every shot that shares
    that look — which ``video-render``'s own guardrail already stated in prose ("never re-roll the
    identity anchor per shot") while nothing checked it.

    Only entries carrying an ``identity_anchor`` are counted. A screen capture or an abstract
    b-roll frame has no wardrobe, hair or room to re-invent, so its still is not drift — and
    counting it would force a mixed-role storyboard (one hero still + three scene frames) to raise
    ``allow_anchors`` to 4, which would simultaneously re-permit the four *presenter* looks this
    check exists to catch. Scoping to identity-bearing entries keeps the ceiling meaningful.

    An entry carrying ``derived_from`` is grouped under the ROOT of its chain, so a delta-edit
    chain from one hero counts once (2026-09-06). An entry with no ``derived_from`` is its own
    root, which is what keeps N independently generated stills reading as N anchors.

    Returns ``{root_image_job_id: [shot numbers]}``. A caller wanting a *deliberate* second look
    (a real scene/wardrobe change) records it — see :func:`approve`'s ``allow_anchors``.
    """
    parents = _parent_map(entries)
    known = {
        str(e.get("image_job_id") or "")
        for e in entries
        if isinstance(e, dict) and e.get("image_job_id")
    }
    groups: dict[str, list] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not entry.get("identity_anchor"):
            continue
        job_id = str(entry.get("image_job_id") or "")
        if not job_id:
            continue
        root, _ = _root_of(job_id, parents, known)
        groups.setdefault(root, []).append(entry.get("n"))
    return groups


def unaccepted_spoilers(entries: list[dict]) -> list[dict]:
    """Every `spoiler_check` finding on these entries that nobody has accepted.

    An entry with no ``spoiler_check`` block is not counted — this gate is additive and does not
    retroactively block storyboards written before the check existed. `"checked": true` with
    `"findings": []` is a clean result and also passes; the two states are distinguishable on
    disk on purpose, because "checked and clean" and "never checked" must not look the same.
    """
    out: list[dict] = []
    for entry in entries:
        block = entry.get("spoiler_check")
        if not isinstance(block, dict):
            continue
        accepted = {
            str(a.get("term", "")).strip().lower()
            for a in (block.get("accepted") or [])
            if isinstance(a, dict)
        }
        for finding in block.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            if str(finding.get("term", "")).strip().lower() not in accepted:
                out.append(finding)
    return out


def approve(
    path: Path,
    *,
    by: str,
    at: str,
    allow_anchors: int = 1,
    reason: str = "",
) -> dict:
    """Record the operator's ⟦GATE:plan⟧ approval on a storyboard, and verify it round-trips.

    Refuses a storyboard with no entries: approving an empty frame set would authorise a render
    the operator never actually saw.

    Also refuses more distinct identity anchors than ``allow_anchors`` (default 1) — see
    :func:`anchor_drift`. A genuine multi-look storyboard raises the ceiling explicitly and must
    give a ``reason``, so "this video deliberately changes setting" is a recorded decision rather
    than an accident nobody noticed until the render came back.

    Refuses, in the same shape, an entry carrying UNACCEPTED ``spoiler_check`` findings — a reused
    real asset whose visible text names a product the script has not introduced by that timecode
    (:mod:`gtm_core.spoiler_check`). This is here rather than left to prose because prose is what
    failed: a screenshot naming the product 75 seconds early passed every gate, and Step 2.6 of
    the skill telling someone to look is the same kind of instruction that has been skipped
    before. The recorded exception is `gtm_core.spoiler_check --accept ... --storyboard <path>`,
    which names the term, the decider, the date and the reason — there is no count-based override,
    because a spoiler finding has an identity and a count does not preserve it.

    Deliberately ADDITIVE: an entry with no ``spoiler_check`` block at all still approves, so no
    existing storyboard is retroactively blocked. What is refused is a check that ran, found
    something, and was then ignored.

    Returns the re-read storyboard. Any ``edit_depth`` warning is on the returned dict under
    ``edit_depth_warnings`` and is printed by the CLI — a chain past :data:`MAX_EDIT_DEPTH` is
    reported, never refused.
    """
    data = _load(path)
    if not (by or "").strip():
        raise StoryboardError("--by is required: approval must name who decided")
    if not (at or "").strip():
        raise StoryboardError("--at is required: approval must record when the operator decided")
    entries = data.get("entries")
    if not entries:
        raise StoryboardError(
            f"storyboard {path} has no entries — there are no frames to have approved"
        )

    if allow_anchors < 1:
        raise StoryboardError(f"--allow-anchors must be >= 1, got {allow_anchors}")
    # Walks lineage, so it refuses a dangling or circular `derived_from` before anything else
    # reads the anchor count off a chain that cannot be verified.
    depths = edit_depths(entries)
    groups = anchor_drift(entries)
    if len(groups) > allow_anchors:
        detail = "; ".join(
            f"{job_id[:8]} → shot(s) {sorted(n for n in shots if n is not None)}"
            for job_id, shots in groups.items()
        )
        raise StoryboardError(
            f"storyboard {path} has {len(groups)} distinct identity anchors but only "
            f"{allow_anchors} allowed: {detail}. Each independently generated still re-invents "
            "wardrobe, lighting and setting, so this renders as unexplained costume changes "
            "mid-video (documented 2026-08-18: 5 stills, 5 different jackets in 32 seconds). "
            "Generate ONE hero still and reuse it as the start frame for every shot sharing that "
            "look. If the look genuinely changes, re-run with "
            f"--allow-anchors {len(groups)} --reason '<why>'."
        )
    if allow_anchors > 1 and not (reason or "").strip():
        raise StoryboardError(
            "--reason is required with --allow-anchors > 1: a deliberate look change is a decision "
            "worth recording, and without one this flag just silences the drift check"
        )
    if allow_anchors > 1:
        data["anchor_exception_reason"] = reason.strip()
    data["identity_anchor_count"] = len(groups)

    # A declared depth is checked against the walked one. Both existing is fine; disagreeing is
    # not — a recorded number that nobody verifies is how a wrong number survives review.
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        job_id = str(entry.get("image_job_id") or "")
        declared = entry.get("edit_depth")
        if not job_id or declared is None:
            continue
        walked = depths.get(job_id, 0)
        if declared != walked:
            raise StoryboardError(
                f"entry {job_id[:8]} declares edit_depth={declared} but its derived_from chain "
                f"is {walked} edit(s) deep. Depth is derived from lineage, so a disagreement "
                "means one of the two is wrong — fix the chain or drop the field."
            )

    deep = sorted(
        ((job_id, d) for job_id, d in depths.items() if d > MAX_EDIT_DEPTH), key=lambda kv: -kv[1]
    )
    warnings = [
        f"entry {job_id[:8]} is {d} edit(s) from its hero still (cap {MAX_EDIT_DEPTH}). "
        "Reference quality is the ceiling on output quality and it degrades with every pass — "
        "re-anchor from the hero if this frame has drifted."
        for job_id, d in deep
    ]
    data["max_edit_depth"] = max(depths.values(), default=0)

    open_spoilers = unaccepted_spoilers(entries)
    if open_spoilers:
        detail = "; ".join(
            f"shot {f.get('shot_n')} @ {f.get('shot_tc')}: {f.get('term')!r} visible, "
            f"first spoken {f.get('first_spoken_tc') or 'NEVER'}"
            for f in open_spoilers
        )
        raise StoryboardError(
            f"storyboard {path} carries {len(open_spoilers)} unaccepted spoiler finding(s): "
            f"{detail}. A reused real asset is showing a name the script has not said yet. Fix "
            "it in this order: crop the term out (`gtm_core.screen_ui still-push --crop-frac "
            "L,T,R,B`), pick a different existing asset, move the shot after the first verbal "
            "mention, or regenerate. To ship it anyway, accept the finding BY NAME — "
            "`gtm_core.spoiler_check --accept ... --storyboard <this file>` — which records who "
            "decided, when, and why, against the specific term. There is deliberately no "
            "count-based override: unlike identity anchors, a spoiler finding HAS an identity, "
            "and a flag that waves through 'N of them' with one sentence is a worse record than "
            "the one the per-finding path already writes."
        )
    data["unaccepted_spoilers"] = 0
    data["approved"] = True
    data["approved_by"] = by.strip()
    data["approved_at"] = at.strip()
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    verify = _load(path)
    if not verify.get("approved") or verify.get("approved_by") != by.strip():
        raise StoryboardError(f"approval did not round-trip on disk: {path}")
    verify["edit_depth_warnings"] = warnings
    return verify


def status(path: Path) -> dict:
    data = _load(path)
    entries = data.get("entries") or []
    groups = anchor_drift(entries)
    depths = edit_depths(entries)
    return {
        "max_edit_depth": max(depths.values(), default=0),
        "path": str(path),
        "approved": bool(data.get("approved")),
        "approved_by": data.get("approved_by"),
        "approved_at": data.get("approved_at"),
        "entries": len(data.get("entries") or []),
        "identity_anchors": len(groups),
        "anchor_exception_reason": data.get("anchor_exception_reason"),
        "unaccepted_spoilers": len(unaccepted_spoilers(data.get("entries") or [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.storyboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("approve", help="record a ⟦GATE:plan⟧ approval")
    a.add_argument("path", type=Path)
    a.add_argument("--by", required=True, help="who approved (operator handle)")
    a.add_argument("--at", required=True, help="ISO-8601 timestamp of the operator's decision")
    a.add_argument(
        "--allow-anchors",
        type=int,
        default=1,
        help="distinct identity anchors (image_job_ids) permitted; default 1 — one hero still "
        "reused across every shot. Raise it only for a deliberate look change, with --reason",
    )
    a.add_argument(
        "--reason", default="", help="why more than one anchor is correct (required with >1)"
    )
    s = sub.add_parser("status", help="show a storyboard's approval state")
    s.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "approve":
            out = approve(
                args.path,
                by=args.by,
                at=args.at,
                allow_anchors=args.allow_anchors,
                reason=args.reason,
            )
            for warning in out.get("edit_depth_warnings") or []:
                print(f"[storyboard] WARN {warning}", file=sys.stderr)
            print(f"approved {args.path} by {out['approved_by']} at {out['approved_at']}")
        else:
            print(json.dumps(status(args.path), indent=2))
    except StoryboardError as exc:
        print(f"[storyboard] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
