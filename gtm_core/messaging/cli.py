"""``python -m gtm_core.messaging <verb>`` — read the outbound fact registry; write two files.

Five verbs. Three read; the other two each write exactly one file, by construction:

* ``check``   — does this tenant's registry load, and is ``hook-matrix.md`` still the render
  of it? Exit 0 clean, exit 2 with **one line per defect** naming the file and the id.
* ``matrix``  — regenerate ``hook-matrix.md`` from ``angles.toml``. The sixth ``profiles/``
  writer, and the only one that is not key-scoped: it replaces the file whole, and refuses any
  target it did not itself write (:mod:`gtm_core.messaging.matrix_view`).
* ``angle promote|retire`` — move ONE ``[[angle]]``'s status in ``angles.toml``, with a dated
  history line. The fifth key-scoped ``profiles/`` writer (``docs/RULES.md``); it changes one
  line in place and nothing deletes an angle, ever
  (:mod:`gtm_core.messaging.angle_status`).
* ``resolve`` — for each row of a pool CSV, which angle is offered, or which refusal; plus a
  count for **every** refusal kind, including the ones that did not fire.
* ``unused``  — which angles no spec declares. A spec it could not read is a defect line
  naming the path and exit 2, never a skipped file: dropping one shrinks ``unused`` upward
  and ``unknown`` downward at once, and both directions read as a clean answer.

**Why the counts, and why the zeros.** The failure this reporting exists to catch is a
*silently smaller* personalised lane: a pool that used to resolve 300 angles now resolves 40,
and nothing says so because 260 refusals look exactly like 260 rows nobody got to. Every
member of :data:`gtm_core.messaging.resolve.REFUSALS` is printed with its number whether or
not it fired, so the shape of the refusal pile is a value an operator reads rather than an
absence they have to notice.

**Why there is still no ``--apply``.** There is no flag here to misuse. ``matrix`` and
``angle`` are verbs, not modes: each does one thing, to one path it resolves itself, and there
is no argument that turns a read verb into a write. ``angle`` carries no ``--overlay`` either,
and that absence is structural — an experiment overlay lasts one run by definition, so a
promotion decided under one has nowhere durable to land.
``tests/unit/test_messaging_cli.py`` asserts the flag set over this file's AST, because a
docstring saying "read-only" is the cheapest thing in the file to get wrong.

Stdlib only. This module opens no socket and makes no paid call — all external I/O in this
system is the brain's, through MCP tools (§R6).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from ..paths import PathConfig, _safe_segment, resolve_content_root, resolve_profiles_root
from . import angle_status, matrix_view, registry, resolve

#: The verbs this module offers, stated once so a test can assert the set rather than
#: re-read ``--help``. ``angle`` is one verb with two sub-verbs (``promote`` / ``retire``),
#: because they share a target, a refusal vocabulary and a writer.
VERBS = ("angle", "check", "matrix", "resolve", "unused")

#: Gate-marker delimiters. A company name reaching this output is scraped provider text
#: (§R5) and the brain pastes CLI output into a run header, where markers are read. Applied by
#: :func:`_say` to every line this module prints and to every JSON field that carries untrusted
#: text, and replaced **visibly** rather than stripped, so an operator can see that something
#: was there. Same treatment, same reason, as ``gtm_core.icp_check.cli``.
_MARKER_DELIMS = {"⟦": "[", "⟧": "]"}

#: Failures an operator fixes by editing a file. Each already carries a message naming the
#: file and the next action, so it is printed as one line. Anything NOT here is a bug in this
#: package and keeps its stack trace, because a bug rendered as advice is a bug nobody files.
_OPERATOR_FIXABLE = (
    registry.RegistryError,
    angle_status.AngleStatusError,
    FileNotFoundError,
    UnicodeDecodeError,
)


def _inert(text: object) -> str:
    """Render untrusted text with gate-marker delimiters defanged."""
    out = str(text)
    for bad, safe in _MARKER_DELIMS.items():
        out = out.replace(bad, safe)
    return out


def _inert_all(lines: list[str]) -> list[str]:
    """:func:`_inert` over a list — the JSON channel's half of the rule in :func:`_say`."""
    return [_inert(line) for line in lines]


def _say(text: object = "", *, err: bool = False) -> None:
    """Print one line of CLI output, defanged. **The one place this module renders text.**

    There are two channels and they need different treatment, which is why "defanged once"
    kept being wrong here:

    * **Text** is defanged at the PRINT — this function. ``_fail`` used to claim that title,
      but it is one of several printers: the ``unused`` report interpolated an angle id, a
      seat and a status straight into ``print`` on the SUCCESS path, and looped its own
      defect lines to stderr without passing through ``_fail`` at all. An id is guarded by
      :func:`gtm_core.paths._safe_segment`, which rejects ``/ \\ .. NUL`` and says nothing
      about ``⟦`` / ``⟧``, so ``a1⟦GATE:publish⟧`` is a **loadable** id — and the brain pastes
      this output into a run header, where markers are read (§R5). Routing every print through
      one function is the point: the seventh printer is defanged by construction rather than
      by the author remembering, and ``tests/unit/test_messaging_cli.py`` fails on a bare
      ``print`` in this file so the next one cannot be added quietly.
    * **JSON** is defanged as DATA, at the point the payload is built. ``json.dumps`` escapes
      U+27E6/U+27E7 by default, but that is a property of ``json.dumps`` and not a control:
      the consumer's ``json.loads`` hands the live marker straight back. So a payload field
      carrying registry or filesystem text goes through :func:`_inert` / :func:`_inert_all`
      before it is serialised, and this function is then a no-op on the escaped result.
    """
    print(_inert(text), file=sys.stderr if err else sys.stdout)


def _profile(args: argparse.Namespace) -> str:
    """The active profile, guarded as a bare segment before it is ever joined to a path.

    ``--profile`` reaches here straight from the command line and becomes a directory name.
    Traversal here is the highest-risk tenant error (CLAUDE.md), and a refusal is reported in
    the same one-line shape as every other defect rather than as a traceback.
    """
    name = args.profile or PathConfig.from_env().default_profile
    try:
        _safe_segment(name, "profile")
    except ValueError as exc:
        raise registry.RegistryError(f"--profile: {exc}") from exc
    return name


def _profiles_root(args: argparse.Namespace) -> Path:
    return (
        Path(args.profiles_root).expanduser().resolve()
        if getattr(args, "profiles_root", None)
        else resolve_profiles_root()
    )


def _load(args: argparse.Namespace) -> tuple[str, registry.Registry]:
    profile = _profile(args)
    root = _profiles_root(args)
    return profile, registry.load(
        profile, profiles_root=root, product=args.product, overlay=args.overlay
    )


def _defects(exc: Exception) -> list[str]:
    """One line per defect. ``RegistryError`` already composes them; anything else is one."""
    return [line for line in str(exc).splitlines() if line.strip()]


def _fail(exc: Exception, as_json: bool) -> int:
    """An expected, operator-fixable failure printed plainly — never a stack trace.

    A traceback buries the file and the id under frames the reader cannot act on. The run
    still exits 2, so nothing downstream reads it as success.

    Every message on this path is composed from registry-derived text — an angle id, a claim's
    status, a path — so the JSON payload is defanged as data here, and the text lines are
    defanged by :func:`_say` like every other line this module prints. This is **a** place a
    refusal is rendered, not the only one: ``unused`` reports its own defects beside a report
    that still stands, which is why the guarantee lives in the printer.
    """
    lines = _inert_all(_defects(exc))
    if as_json:
        _say(json.dumps({"ok": False, "defects": lines}, indent=2))
    else:
        for line in lines:
            _say(f"[messaging] {line}", err=True)
    return 2


# --- check ----------------------------------------------------------------------------


def _cli_check(args: argparse.Namespace) -> int:
    try:
        profile, reg = _load(args)
        # Resolved with the SAME overlay the registry was loaded with, so an experiment run
        # compares the overlay's matrix against the overlay's angles rather than reporting
        # drift that is really just two different files.
        state = matrix_view.matrix_state(
            reg,
            matrix_view.matrix_path(
                _profiles_root(args), profile, product=args.product, overlay=args.overlay
            ),
        )
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc, args.json)

    # Only a STALE matrix is a defect. An ABSENT or HAND_AUTHORED one is a tenant that has
    # not adopted the generated view — most profiles in this repo — and failing them here
    # would make `check` red for everyone who has not migrated, which is how a gate gets
    # switched off. Both states are reported; neither fails.
    if state == matrix_view.STALE:
        return _fail(
            registry.RegistryError(
                f"{matrix_view.MATRIX_FILE}: out of date — it no longer matches "
                f"{registry.ANGLES_FILE}; regenerate with "
                f"`python -m gtm_core.messaging matrix --profile {profile}`"
            ),
            args.json,
        )

    live = len(reg.live_angles())
    if args.json:
        _say(
            json.dumps(
                {
                    "ok": True,
                    "profile": profile,
                    "claims": len(reg.claims),
                    "proof": len(reg.proof),
                    "angles": reg.angle_count,
                    "live_angles": live,
                    "seats": len(reg.seats),
                    "matrix": state,
                    "defects": [],
                },
                indent=2,
            )
        )
        return 0

    _say(f"messaging check — {profile}")
    _say(
        f"  claims {len(reg.claims)} · proof {len(reg.proof)} · "
        f"angles {reg.angle_count} (live {live}) · seats {len(reg.seats)}"
    )
    _say(f"  {registry.ANGLES_FILE} → {matrix_view.MATRIX_FILE}: {state}")
    _say("  OK — every claim, proof and angle resolves.")
    return 0


# --- matrix ---------------------------------------------------------------------------


def _cli_matrix(args: argparse.Namespace) -> int:
    """Regenerate ``hook-matrix.md`` — the sixth ``profiles/`` writer (``docs/RULES.md``).

    The one write here that is **not** key-scoped: it replaces the whole file. That is bounded
    by the banner check in :mod:`gtm_core.messaging.matrix_view` and by the target being a
    derived view, never by narrowing what it touches.

    An overlay is refused rather than rendered. ``--overlay`` legitimately changes what
    ``check`` and ``resolve`` READ; letting it change what this verb WRITES would persist one
    morning's experiment into the file every later run reads as the tenant's own. An overlay
    is an argument precisely so it cannot become ambient (CLAUDE.md), and a written file is
    as ambient as it gets.
    """
    if args.overlay:
        return _fail(
            registry.RegistryError(
                f"{matrix_view.MATRIX_FILE}: `--overlay {_inert(args.overlay)}` — an overlay is a "
                "run-scoped experiment and is never written into tenant data; rerun without it"
            ),
            args.json,
        )
    try:
        profile, reg = _load(args)
        target = matrix_view.matrix_path(_profiles_root(args), profile, product=args.product)
        state = matrix_view.write_matrix(reg, target)
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc, args.json)

    cells = len(matrix_view.rendered_angles(reg))
    if args.json:
        _say(
            json.dumps(
                {
                    "profile": profile,
                    "path": str(target),
                    "state": state,
                    "cells": cells,
                    "seats": len(reg.seats),
                },
                indent=2,
            )
        )
        return 0

    _say(f"messaging matrix — {profile}")
    _say(f"  {state}: {target}")
    _say(f"  {cells} cell(s) from {registry.ANGLES_FILE} · {len(reg.seats)} seat(s)")
    _say("  Generated file. Edit angles.toml and rerun; an edit here is lost.")
    return 0


# --- resolve --------------------------------------------------------------------------


def _read_pool(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _cli_resolve(args: argparse.Namespace) -> int:
    """Per-row angle or refusal, plus a count for every refusal kind. Writes nothing."""
    try:
        profile, reg = _load(args)
        rows = _read_pool(Path(args.csv))
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc, args.json)

    root = _profiles_root(args)
    counts = dict.fromkeys(sorted(resolve.REFUSALS), 0)
    reported: list[dict] = []
    for i, row in enumerate(rows, start=1):
        # Resolved through the module, not a `from .resolve import angle_for` binding: the
        # one-resolver property is provable that way (a test patching `resolve.angle_for`
        # is seen here) rather than assumed.
        result = resolve.angle_for(
            row,
            reg,
            profile=profile,
            profiles_root=root,
            product=args.product,
            overlay=args.overlay,
        )
        if result.refusal:
            counts[result.refusal] = counts.get(result.refusal, 0) + 1
        reported.append(
            {
                "row": i,
                "company": _inert(row.get("company") or ""),
                "seat": result.seat,
                # `market` is the row's own `country` column after
                # `email_compliance.normalize_market`, which is a PASS-THROUGH for a value it
                # does not recognise — it lowercases, drops a `(...)` annotation and collapses
                # whitespace, then returns the raw string. So this field is untrusted provider
                # text (§R5) exactly as `company` is, and is defanged at the same one place.
                # `seat` is not: it is a key from the tenant's own closed seat vocabulary.
                "market": _inert(result.market),
                # Same shape as `market`: `clean_segment` passes an unrecognised value
                # through as written, so this is the row's own column, defanged here.
                "segment": _inert(result.segment),
                "attestation": _inert(result.attestation),
                # Angle ids are registry text and carry the same marker risk as a company
                # name; `None` stays `None` so "no angle" is not reported as the string.
                "angle": _inert(result.angle_id) if result.angle_id else result.angle_id,
                "refusal": result.refusal,
                "alternatives": _inert_all(list(result.alternatives)),
            }
        )

    resolved = sum(1 for r in reported if r["angle"])
    if args.json:
        _say(
            json.dumps(
                {
                    "profile": profile,
                    "csv": str(args.csv),
                    "rows": len(rows),
                    "resolved": resolved,
                    "refused": len(rows) - resolved,
                    "counts": counts,
                    "resolutions": reported,
                },
                indent=2,
            )
        )
        return 0

    _say(f"messaging resolve — {profile} · {len(rows)} row(s) · {Path(args.csv).name}")
    for r in reported:
        verdict = f"{r['angle']} [{r['attestation']}]" if r["angle"] else f"refused: {r['refusal']}"
        extra = f"  (also fit: {', '.join(r['alternatives'])})" if r["alternatives"] else ""
        _say(
            f"  {r['row']:>4}  {r['company'] or '<no company>'} · "
            f"{r['seat'] or '<no seat>'} · {r['segment'] or '<no segment>'} · "
            f"{r['market'] or '<no market>'} → {verdict}{extra}"
        )
    _say()
    _say(f"  resolved:  {resolved}")
    _say(f"  refused:   {len(rows) - resolved}")
    # Every kind, including the zeros: a refusal kind that is missing from the report and a
    # refusal kind that did not fire look identical, and only one of them is good news.
    for kind, n in sorted(counts.items()):
        _say(f"    {kind:<22} {n}")
    _say()
    _say("  This verb writes nothing. Nothing was staged, enrolled or sent.")
    return 0


# --- unused ---------------------------------------------------------------------------


def _specs_dir(args: argparse.Namespace, profile: str) -> Path:
    if getattr(args, "specs", None):
        return Path(args.specs).expanduser()
    root = (
        Path(args.content_root).expanduser().resolve()
        if getattr(args, "content_root", None)
        else resolve_content_root()
    )
    return root / _safe_segment(profile, "profile") / "prospects" / "sequences"


def _declared_angles(specs: Path, defects: list[str]) -> set[str]:
    """Every angle id a spec under ``specs`` declares, lowercased, plus any file it could not read.

    A skipped spec is a *silently smaller* answer in both directions: every angle it declared
    is reported as ``unused``, and every id it named that the registry lacks drops out of
    ``unknown``. So an unreadable file is recorded as a defect naming its path, the way
    :func:`gtm_core.messaging.registry._read_table` records an unreadable table — the run still
    reports what it could read, and still exits 2 so nothing downstream takes it for clean.
    """
    # The ONE reader of the `angle:` field, imported rather than copied: this module carried its
    # own `_ANGLE_RE` until FR2 Task 2.6, and two readers of one field drift while both keep
    # answering. Imported inside the function for the reason `matrix_view._canonical` gives —
    # `hook_coverage.config` puts `tests/linter` on `sys.path` at import time.
    #
    # What this census does NOT reuse is `declared.declared_angle`, which reads over
    # `declaration_surface` and so ignores an `angle:` line in an email body. `unused` asks a
    # looser question — does any spec MENTION this angle — and over-answering it only moves an id
    # from `unused` into `referenced`/`unknown`, both printed. Scoped would still be better; left
    # to the FR3 linter rewire, and recorded here so it is not mistaken for agreement.
    from ..hook_coverage.declared import _ANGLE_RE

    out: set[str] = set()
    if not specs.is_dir():
        return out
    for path in sorted(specs.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            defects.append(f"{path}: unreadable — {exc}")
            continue
        # No `.strip()`: the pattern's own `\s*` bounds already eat the surrounding
        # whitespace, and a second guard that can never fire reads as a rule while being
        # untestable — the shape §R18 calls an equivalent mutant and says to delete.
        out.update(m.group("value").lower() for m in _ANGLE_RE.finditer(text))
    return out


def _cli_unused(args: argparse.Namespace) -> int:
    try:
        profile, reg = _load(args)
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc, args.json)

    specs = _specs_dir(args, profile)
    defects: list[str] = []
    declared = _declared_angles(specs, defects)
    referenced = sorted(declared & set(reg.angles))
    unused = sorted(set(reg.angles) - declared)
    # A spec naming an angle the registry does not have is a dangling reference, and it
    # reads downstream as an empty slot rather than as an error. Reported, never ignored.
    unknown = sorted(declared - set(reg.angles))

    if args.json:
        _say(
            json.dumps(
                {
                    "profile": profile,
                    "specs": _inert(specs),
                    # Defanged as DATA, not merely as printed bytes: an id and a spec path are
                    # both untrusted text (§R5), and a skill that `json.loads` this payload
                    # gets `json.dumps`' escaping undone for it. See :func:`_say`.
                    "referenced": _inert_all(referenced),
                    "unused": _inert_all(unused),
                    "unknown": _inert_all(unknown),
                    "defects": _inert_all(defects),
                },
                indent=2,
            )
        )
        return 2 if defects else 0

    _say(f"messaging unused — {profile} · specs under {specs}")
    _say(f"  unused ({len(unused)} of {reg.angle_count}):")
    for angle_id in unused:
        angle = reg.angles[angle_id]
        _say(f"    {angle_id} · {angle.seat} · {angle.premise} · {angle.status}")
    if unknown:
        _say(f"  declared by a spec but not in {registry.ANGLES_FILE}:")
        for angle_id in unknown:
            _say(f"    {angle_id}")
    # Printed after the report, not instead of it: the answer for the specs that WERE read is
    # still the operator's, and the defect says which file is missing from it. Through `_say`
    # like every other line here — this loop used to reach stderr on its own, which is how a
    # marker in a spec's FILENAME stayed live in output the brain pastes into a run header.
    for line in defects:
        _say(f"[messaging] {line}", err=True)
    return 2 if defects else 0


# --- angle promote | retire -------------------------------------------------------------


def _cli_angle(args: argparse.Namespace) -> int:
    """Move ONE angle's status. The fifth key-scoped ``profiles/`` writer.

    The verb does no deciding of its own: every refusal — no such angle, an unparseable
    evidence cell, a claim that is not ``verified`` — belongs to
    :mod:`gtm_core.messaging.angle_status`, so the library and the command line cannot
    disagree about what is allowed.
    """
    try:
        profile = _profile(args)
        root = _profiles_root(args)
        move = angle_status.promote if args.angle_cmd == "promote" else angle_status.retire
        path = move(
            profile,
            angle_id=args.id,
            evidence=args.evidence,
            profiles_root=root,
            product=args.product,
        )
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc, args.json)

    moved = angle_status.LIVE if args.angle_cmd == "promote" else angle_status.RETIRED
    if args.json:
        _say(json.dumps({"ok": True, "angle": _inert(args.id), "status": moved, "path": str(path)}))
        return 0
    _say(f"messaging angle {args.angle_cmd} — {profile}")
    _say(f"  {args.id} → {moved}  ({path})")
    _say("  The angle is still in the file; only its status moved.")
    return 0


# --- plumbing -------------------------------------------------------------------------


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--profile", default=None, help="profile slug (default: ACTIVE_PROFILE/GTM_PROFILE)"
    )
    sp.add_argument("--product", default=None, help="product slug for a product-level override")
    sp.add_argument("--overlay", default=None, help="experiment overlay slug")
    sp.add_argument("--profiles-root", default=None)
    sp.add_argument("--json", action="store_true", help="print the result as JSON")


def _add_angle_common(sp: argparse.ArgumentParser) -> None:
    """The write verbs' options — deliberately :func:`_add_common` MINUS ``--overlay``.

    Written out rather than composed from ``_add_common``, because "same as the read verbs
    except one" is exactly the shape that silently regrows the excluded flag.
    """
    sp.add_argument(
        "--profile", default=None, help="profile slug (default: ACTIVE_PROFILE/GTM_PROFILE)"
    )
    sp.add_argument("--product", default=None, help="product slug for a product-level override")
    sp.add_argument("--profiles-root", default=None)
    sp.add_argument("--json", action="store_true", help="print the result as JSON")
    sp.add_argument("--id", required=True, metavar="ANGLE_ID", help="the angle to move")


def build_parser() -> argparse.ArgumentParser:
    """The parser, separately from running it, so a test can read the verbs it registers.

    Asserting against :data:`VERBS` alone would prove only that a constant was not edited;
    the question worth answering is which subcommands argparse actually accepts.
    """
    p = argparse.ArgumentParser(
        prog="python -m gtm_core.messaging",
        description="Read a tenant's outbound fact registry. Read-only: no verb here writes.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    cp = sub.add_parser("check", help="validate the registry; exit 2 on a bad one")
    _add_common(cp)

    mp = sub.add_parser("matrix", help=f"regenerate {matrix_view.MATRIX_FILE} from angles.toml")
    _add_common(mp)

    rp = sub.add_parser("resolve", help="per-row angle or refusal for a pool CSV")
    _add_common(rp)
    rp.add_argument("--csv", required=True, help="the pool CSV to resolve, row by row")
    rp.add_argument(
        "--dry-run",
        action="store_true",
        help="accepted and always true — this verb never writes; the flag exists so a skill "
        "or an operator can say so out loud, and there is no way to turn it off",
    )

    up = sub.add_parser("unused", help="angles no spec declares")
    _add_common(up)
    up.add_argument("--specs", default=None, help="directory of specs (default: the pool's own)")
    up.add_argument("--content-root", default=None)

    # `angle` carries NO `--overlay`: an overlay lasts one run, so a status decided under one
    # has nowhere durable to land. Unrepresentable beats checked.
    ap = sub.add_parser("angle", help="move one angle's status in angles.toml")
    asub = ap.add_subparsers(dest="angle_cmd", required=True)
    for name, evidence_required in (("promote", True), ("retire", False)):
        vp = asub.add_parser(
            name,
            help=(
                "draft/retired → live; needs the dashboard cell that earned it"
                if evidence_required
                else "→ retired; the angle stays in the file"
            ),
        )
        _add_angle_common(vp)
        vp.add_argument(
            "--evidence",
            required=evidence_required,
            default=None,
            metavar="CELL",
            help="the angle's outcome cell as counts, e.g. 'sent=42 replies=5'. Parsed as "
            "integers against the ledger's own count names — a cell it cannot read is "
            "refused, never coerced, because those figures derive from reply text (§R5)",
        )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "check":
        return _cli_check(args)
    if args.cmd == "matrix":
        return _cli_matrix(args)
    if args.cmd == "resolve":
        return _cli_resolve(args)
    if args.cmd == "unused":
        return _cli_unused(args)
    if args.cmd == "angle":
        return _cli_angle(args)
    return 2  # pragma: no cover - argparse's `required=True` on subparsers makes this unreachable


if __name__ == "__main__":  # pragma: no cover - CLI plumbing
    raise SystemExit(main())
