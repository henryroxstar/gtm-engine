from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .matrix import Matrix, UnknownHookCell, _clean_cell

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    # Type-only, never at runtime: `hook_coverage.config` puts `tests/linter` on `sys.path` at
    # import time, and `gtm_core.messaging` is deliberately clear of that dev-only tree
    # (`messaging.registry._load_premises` takes the same care in the other direction). The two
    # functions that need a `Registry` at runtime are handed one by their caller.
    from ..messaging.registry import Angle, Registry

# --- what a spec declares -----------------------------------------------------------

# Two surfaces, one field — the same split `_CAPABILITY_RE` already carries below. A sequence
# spec writes `hook_cell:` in a fenced front block; a 1:1 pack writes `**Hook cell:**` in a
# markdown header. Until 2026-09-04 only the first form was read, so a pack could declare a cell
# and the audit would see nothing and pass — the declaration was decorative.
#
# The value is taken WHOLE. A first version stripped a trailing parenthetical, meaning to drop a
# "(Builder grid)" annotation, and silently truncated the real matrix signal
# "Compliance event (audit, breach)" — a signal name may contain parentheses and nothing tells
# the two apart. Annotations belong outside the field, not in a regex's guesswork.
_HOOK_CELL_RE = re.compile(
    r"^\**hook[_ ]cell\**:\**\s*(?P<value>.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ARGUMENT_ID_RE = re.compile(r"^argument_id:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_PREMISE_RE = re.compile(r"^premise:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_STAKES_RE = re.compile(r"^stakes:\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# Which signal CATEGORY this spec's beat 2 was written to depend on (2026-09-22, EC7). Beat 2
# has to read as wrong — not merely generic — under a fact from a different category, and no
# single fixed sentence can do that for a list mixing several. Declaring the category is what
# makes "is beat 2 load-bearing?" a checkable question instead of a style note. Same one-line
# front-block read as every field above; a second parser is how two fields drift apart.
_SIGNAL_COLUMN_RE = re.compile(
    r"^\**signal[_ ]column\**:\**\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
# Sequence specs declare `capability: identity` in a fenced front block; 1:1 outreach
# packs declare `**Capability:** identity` in a markdown header. One field, two
# surfaces, one parser -- a second regex is how the two drift apart.
_CAPABILITY_RE = re.compile(
    r"^\**capability\**:\**\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
# Which angle a spec declares (FR2). Same two surfaces and the same whole-value read as
# `_HOOK_CELL_RE` above, and deliberately the SAME pattern the `messaging unused` verb used to
# carry privately: that constant is now imported from here, because two readers of one field
# drift and the drift is invisible while both keep answering.
#
# Unlike every field above, this one is searched over `declaration_surface` rather than over the
# raw spec — see that function for what it excludes and why.
_ANGLE_RE = re.compile(r"^\**angle\**:\**\s*(?P<value>.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# --- where a declaration may be written ----------------------------------------------
#
# `agent/publish.py` reads every non-`⟦POST⟧` field from OUTSIDE the post span, because on
# 2026-09-16 a `⟦TO⟧` quoted inside an untrusted inbound message became the recipient of the
# reply it was quoted into. A spec here has the same shape: its email bodies carry scraped
# provider text (§R5), and in a sequence spec a touch body is a fenced block that looks exactly
# like the fenced `Key: value` front block a declaration is written in.
#
# So a fenced "front block" is defined by what the block CONTAINS, not by where it sits, and an
# unfenced one by what it contains AND where it sits. Measured over the 111 `hook_cell`
# declarations in this tree on 2026-09-24, the rule below admits all 111 and excludes every body:
#
#   * inside a fence  -> a declaration only if EVERY non-blank line of that fence is a
#     ``Key: value`` line, a ``#`` comment, or an indented continuation of the line above.
#     Position-free: the sequence-spec template writes its front block under a ``##`` heading,
#     and a 1:1 pack may record its provenance below the copy, so a fenced block declares
#     wherever it sits;
#   * outside a fence -> a declaration only in the bolded ``**Angle:** id`` form the 1:1 packs
#     actually use, AND only in the document's HEADER: above the first email section. A bare
#     line pasted into a prose section is not one, and neither is a bolded one.
#
# The header rung is the 2026-09-24 §R5 narrowing, and it is the half that had teeth. On the PACK
# path the text handed to the derivation rules is the whole document *including the already-
# rendered bodies*, into which the drafter pasted the row's researched clause — so a clause whose
# line read ``**slot_proof:** none`` supplied a declaration the author had omitted and SUPPRESSED
# the `slot-attribution` ERROR that names it, and a ``**Angle:**`` in a body outranked a
# declaration written lower down. Both are row-derived text reaching a field the gate SELECTS on,
# which is the one direction `rules_derivation`'s one-directional guarantee says cannot happen.
# `tests/injection/test_registry_claim_injection.py` holds the red/green proof of both.
#
# What this still does NOT give you, recorded rather than claimed: a bolded field line forged
# into the HEADER block itself would be read. The header is author-written front matter, but one
# of its fields (`**Why-now:**` in the Tier-A pack template) carries the row's researched clause,
# so a multi-line paste there is the one remaining way row text reaches this surface. That is
# weaker than the publish gate's property, which has one delimited span to read outside of; a
# spec has no such span.
_FENCE_RE = re.compile(r"^\s*(?:`{3,}|~{3,})")
_FIELD_LINE_RE = re.compile(r"^\**[A-Za-z][A-Za-z0-9 _/&-]*\**:")
_CONTINUATION_RE = re.compile(r"^\s+\S")
_COMMENT_RE = re.compile(r"^\s*#")
# Where a document stops being its own header and starts being copy, in the three pack shapes
# `parse.detect_format` knows. A sub-heading opens the email section in both the `### N.`
# (tier-a-manual) and `## Email (touch 1)` (prospect-pack) shapes; the un-sectioned
# draft-outreach shape has no heading at all, and there `**Subject:**` is the first line of the
# email — which is already the split `parse.parse_draft_outreach_pack` reads its `To:` above.
# Evaluated on unfenced lines only, so a `Subject:` inside a fenced touch body cannot end it.
_HEADER_END_RE = re.compile(r"^(?:#{2,6}\s|\**subject\**:)", re.IGNORECASE)


def _is_front_block(lines: list[str]) -> bool:
    """True when a fenced block reads as a ``Key: value`` declaration block, not as a body."""
    body = [line for line in lines if line.strip()]
    return bool(body) and all(
        _FIELD_LINE_RE.match(line) or _CONTINUATION_RE.match(line) or _COMMENT_RE.match(line)
        for line in body
    )


def declaration_surface(spec_text: str) -> str:
    """``spec_text`` with every line a declaration may NOT be written on blanked.

    Blanked, never deleted: the result has the same number of lines as the input, so ``^``
    anchors still behave and any line number a caller derives from a match still points at the
    real file.

    An unclosed fence is treated as a body. A spec that opens a fence and never closes it is
    malformed, and the fail-closed reading turns that into an "declares no angle" ERROR rather
    than into a declaration read out of whatever the fence swallowed.

    Unfenced lines are kept only while the document is still in its header — see
    ``_HEADER_END_RE`` above for the boundary and for what that narrowing is worth. A fenced
    front block is read wherever it sits, header or not.
    """
    out: list[str] = []
    block: list[str] = []
    fenced = False
    header = True
    for line in (spec_text or "").splitlines():
        if _FENCE_RE.match(line):
            if fenced:
                out.extend(block if _is_front_block(block) else [""] * len(block))
                block = []
            out.append("")
            fenced = not fenced
            continue
        if fenced:
            block.append(line)
            continue
        if header and _HEADER_END_RE.match(line):
            header = False
        out.append(line if header and line.startswith("**") else "")
    out.extend([""] * len(block))
    # The trailing ``"\n"`` is what keeps the line count equal to the input's: a blanked last
    # line joins to a trailing newline, and ``splitlines`` would swallow it, so the mask would
    # silently shorten a spec that ends inside a body.
    return "\n".join(out) + "\n" if out else ""


class AngleDeclarationError(ValueError):
    """A spec whose ``angle:`` declaration cannot be trusted to name one argument."""


class UndeclaredAngle(AngleDeclarationError):
    """A spec declares no angle at all.

    An ERROR and not a shrug, unlike :func:`declared_cell`'s ``None``. On 2026-09-04 a shape the
    ``hook_cell`` regex could not read passed silently, so every 1:1 pack declared nothing and
    the audit agreed with it — a gate that passes by finding nothing. Once ``angles.toml`` is the
    source there is no migration left to be halfway through, so absence fails closed.
    """


class UnknownAngle(AngleDeclarationError):
    """A spec names an angle id the registry does not hold.

    The sibling of :class:`UnknownHookCell`: the registry is the vocabulary, and a spec free to
    invent an id can declare conformance to an argument nobody wrote.
    """


class DerivedFieldConflict(AngleDeclarationError):
    """A spec's legacy field contradicts what its angle derives.

    One declared id, everything else derived. A legacy ``hook_cell`` / ``capability`` /
    ``premise`` / ``stakes`` is now a *copy* of a registry fact, and a copy that disagrees with
    its original is the drift the fact registry exists to remove — so it is an ERROR rather than
    a warning, and it names both readings so the operator can see which one is stale.
    """


@dataclass(frozen=True)
class DeclaredCell:
    persona: str
    signal: str
    argument_id: str = ""
    raw: str = ""


def declared_cell(spec_text: str, matrix: Matrix | None = None) -> DeclaredCell | None:
    """The matrix cell a spec claims to implement, or None if it declares none.

    Read with one regex per field over the spec's fenced ``Key: value`` front block —
    the same single-line read ``merge_render_linter`` already uses for ``Sign-off``,
    so the field costs no new parser.

    Pass ``matrix`` to *validate* the declaration: an unknown persona or signal raises
    :class:`UnknownHookCell`, because a spec free to invent its own coordinates can
    declare conformance to a cell that does not exist. Called without a matrix this is
    a pure read, which is what lets the H0 baseline report "0 of 4 specs declare a
    cell" instead of crashing on the four specs that predate the field.
    """
    m = _HOOK_CELL_RE.search(spec_text or "")
    if not m:
        return None
    raw = m.group("value").strip()
    persona, sep, signal = raw.partition("×") if "×" in raw else raw.partition(" x ")
    if not sep:
        persona, signal = raw, ""
    persona, signal = _clean_cell(persona), _clean_cell(signal)
    am = _ARGUMENT_ID_RE.search(spec_text or "")
    declared = DeclaredCell(
        persona=persona,
        signal=signal,
        argument_id=(am.group("value").strip() if am else ""),
        raw=raw,
    )
    if matrix is not None and matrix.ok and matrix.find(persona, signal) is None:
        raise UnknownHookCell(
            f"hook_cell {raw!r} is not a cell in the matrix "
            f"({len(matrix.cells)} cells across {len(matrix.personas)} personas)"
        )
    return declared


# --- one declared id, everything else derived ----------------------------------------


def derived_fields(angle: Angle, registry: Registry) -> dict[str, str]:
    """What ``angle`` derives for each field a spec used to declare by hand.

    ``hook_cell`` is built in the shape :func:`gtm_core.messaging.matrix_view.render` puts in
    the grid — seat as the row, ``premise × opener_kind`` as the column — so the derived cell is
    the cell the matrix actually holds rather than a second spelling of it. :func:`declared_cell`
    splits on the FIRST ``×``, which is what makes that three-part string parse back into the
    same (persona, signal) pair.

    ``capability`` is the claim's ``group``; the seven groups in a tenant's ``claims.toml`` are
    the same seven names the capability taxonomy already used, so nothing is renamed here.
    """
    from .premise import capability_slug

    claim = registry.claims.get(angle.claim)
    return {
        "hook_cell": f"{angle.seat} × {angle.premise} × {angle.opener_kind}",
        "capability": capability_slug(claim.group) if claim else "",
        "premise": angle.premise,
        "stakes": angle.stakes,
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _cell_disagrees(spec_text: str, derived: str) -> str:
    """What the spec's ``hook_cell`` says, when it disagrees with ``derived``; else ``""``.

    Compared through :func:`declared_cell`'s own split rather than as strings. A string compare
    would report ``x`` for ``×`` and a re-spaced cell as conflicts — the tolerance the existing
    reader already grants is the tolerance this check must grant, or the gate fires on spelling
    and the operator learns to ignore it.
    """
    declared = declared_cell(spec_text)
    want = declared_cell(f"hook_cell: {derived}")
    if declared is None or want is None:
        return ""
    same = (_norm(declared.persona), _norm(declared.signal)) == (
        _norm(want.persona),
        _norm(want.signal),
    )
    return "" if same else declared.raw


def _conflicts(spec_text: str, angle: Angle, registry: Registry) -> list[str]:
    """Every legacy field that disagrees with ``angle``, one line each.

    The legacy fields are read with the module's OWN regexes over the raw spec — the same single
    reader every other caller in this repo uses — rather than over
    :func:`declaration_surface`. Two readings of one field is the drift this module keeps warning
    about, and the asymmetry is safe in this direction: a forged legacy line can only *add* a
    refusal, never suppress one, so an injection into a body costs a loud error and never a
    quiet pass.
    """
    from .premise import capability_slug

    derived = derived_fields(angle, registry)
    said: list[tuple[str, str]] = []

    cell = _cell_disagrees(spec_text, derived["hook_cell"])
    if cell:
        said.append(("hook_cell", cell))

    for field, pattern in (("premise", _PREMISE_RE), ("stakes", _STAKES_RE)):
        m = pattern.search(spec_text or "")
        if m and _norm(m.group("value")) != _norm(derived[field]):
            said.append((field, m.group("value").strip()))

    m = _CAPABILITY_RE.search(spec_text or "")
    if m and capability_slug(m.group("value")) != derived["capability"]:
        said.append(("capability", m.group("value").strip()))

    return [
        f"`{field}` says {value!r}; angle `{angle.id}` derives {derived[field]!r}"
        for field, value in said
    ]


def load_registry_or_reason(
    profile: str,
    profiles_root: Path | None = None,
    *,
    overlay: str | None = None,
) -> tuple[Registry | None, str]:
    """``(registry, "")``, or ``(None, <one-line reason>)`` when it will not load.

    One loader for both reports. ``audit_campaign`` files the reason as a warning on the
    coverage report and ``backlog`` prints it to stderr, but neither may *decide* whether a
    registry loaded — two answers to that question is how one report resolves a spec's angle
    and the other calls the same spec undeclared.

    A profile with no registry at all raises the same ``RegistryError`` (``load`` treats a
    missing ``claims.toml`` as a defect rather than an absence), so a pre-FR1 tenant lands
    here too and is told the same thing. Deliberate: the operator action is identical either
    way, and a second, quieter branch for "you have not started yet" is how a broken registry
    gets mistaken for an unmigrated one.
    """
    from ..messaging.registry import RegistryError
    from ..messaging.registry import load as _load

    try:
        return _load(profile, profiles_root, overlay=overlay), ""
    except RegistryError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "unreadable"
        return None, (
            f"registry-unreadable: {profile} — angles.toml/claims.toml/proof.toml did not "
            f"load ({first}), so a spec's cell can only be read off a legacy `hook_cell:`. "
            f"Diagnose with `python -m gtm_core.messaging check --profile {profile}`."
        )


def resolve_declared_cell(
    spec_text: str,
    matrix: Matrix | None = None,
    registry: Registry | None = None,
) -> DeclaredCell | None:
    """The matrix cell a spec implements, resolved the way the spec actually declares it.

    **Angle first, ``hook_cell:`` second.** FR2 removed ``hook_cell:`` from the shipped
    template and made the cell a DERIVATION of the declared ``angle:``. Nothing read
    ``angle:`` here, so every spec written to the new template resolved no cell at all and
    the campaign coverage report counted it undeclared — a report that lies about the
    artifact the same skill had just produced, which is worse than the duplicated field it
    replaced. The fallback stays because 24 specs on disk declare ``hook_cell:`` and no
    ``angle:``; it goes when they migrate, not before.

    The two are never merged. A spec that declares an ``angle:`` is read ONLY through the
    registry, so a stale ``hook_cell:`` beside it cannot win — that disagreement is
    :class:`DerivedFieldConflict`'s finding to make, at the point where a spec is
    validated, and not a second opinion for this resolver to have.

    Raises :class:`UnknownHookCell` when the declared angle is not in the registry, for the
    reason an invented cell raises it: a spec free to name its own argument can claim
    conformance to one nobody wrote. Falling through to ``hook_cell:`` there would report
    an invented angle as an unmigrated spec — the fail-open :class:`UndeclaredAngle` exists
    to end.
    """
    angle_id = declared_angle(spec_text)
    if registry is None or not angle_id:
        return declared_cell(spec_text, matrix)
    angle = registry.angles.get(angle_id)
    if angle is None:
        raise UnknownHookCell(
            f"angle {angle_id!r} is not in angles.toml ({len(registry.angles)} angles), so "
            f"the cell it derives cannot be resolved; "
            f"`python -m gtm_core.messaging unused` lists them"
        )
    cell = declared_cell(f"hook_cell: {derived_fields(angle, registry)['hook_cell']}", matrix)
    # The angle id IS the stable argument slug (`argument_id` was superseded, not renamed),
    # and `Coverage.arguments` keys on it — without this, two angles that derive one cell
    # would be counted as one argument.
    return None if cell is None else replace(cell, argument_id=angle.id)


def declared_angle(spec_text: str, registry: Registry | None = None) -> str:
    """The angle id a spec declares, lowercased, or ``""`` when it declares none.

    Read over :func:`declaration_surface`, so an ``angle:`` line inside an email body is not a
    declaration — the spec-side form of the property ``agent/publish.py`` holds by reading every
    field outside the ``⟦POST⟧`` span.

    Pass ``registry`` to *validate*, exactly as :func:`declared_cell` takes a ``matrix``. Three
    refusals, each with its own type: :class:`UndeclaredAngle` when the spec names none,
    :class:`UnknownAngle` when it names an id the registry lacks, and
    :class:`DerivedFieldConflict` when a legacy field contradicts what the angle derives.

    Called without a registry this is a pure read, which is what lets a migration baseline count
    the specs that predate the field instead of crashing on them.
    """
    m = _ANGLE_RE.search(declaration_surface(spec_text))
    value = m.group("value").strip().lower() if m else ""
    if registry is None:
        return value
    if not value:
        raise UndeclaredAngle(
            "no `angle:` declared — the angle is the one field a spec still writes down, "
            f"and the registry holds {len(registry.angles)} of them"
        )
    angle = registry.angles.get(value)
    if angle is None:
        raise UnknownAngle(
            f"angle {value!r} is not in angles.toml ({len(registry.angles)} angles); "
            "`python -m gtm_core.messaging unused` lists them"
        )
    conflicts = _conflicts(spec_text, angle, registry)
    if conflicts:
        raise DerivedFieldConflict("\n".join(conflicts))
    return value
