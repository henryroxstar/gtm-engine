"""The outbound fact registry — claims, proof, angles, seats — and its refusals.

**What this is.** A tenant's outbound argument stated as *facts* rather than as copy: what
the product does and how sure we are (``claims.toml``), what attests a number or an
obligation (``proof.toml``), and which of those a given seat is offered (``angles.toml``).
The seats themselves are **not** stored here — they are ICP data and already live in
``role-vocabulary.toml``; this module reads them through
:mod:`gtm_core.role_vocabulary`, because two answers to "which seats exist" would drift and
the drift would be invisible.

**Why fail-closed.** Every defect this module refuses is one that reads as *fine* downstream.
A claim marked ``verified`` with no source, a figure whose ``figure_kind`` is a typo, an
angle citing a claim id that no longer exists: each of them renders a confident sentence,
and a confident wrong sentence and a correct one look identical in an inbox. So
:func:`load` is all-or-nothing. It never drops the block it cannot parse and returns the
rest, because the observable result of that is a *silently smaller* personalised lane —
the same class of failure as a coverage report showing zero for a persona nothing
resolves.

**Why all defects, then one raise.** A loader that stops at the first defect turns a
ten-minute data fix into ten edit-run cycles, which is how a tenant ends up with the
check disabled. Every defect is collected and rendered as **one line naming the file and
the id**, never a traceback: the reader of this message is an operator editing TOML, not
a Python developer reading a stack.

This module is stdlib-only and read-only. It opens no socket, makes no paid call, and
writes nothing — ``profiles/`` stays read-only at runtime (CLAUDE.md).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..paths import _safe_segment, resolve_knowledge_file, resolve_profiles_root
from ..role_vocabulary import VocabularyError
from ..role_vocabulary import load as load_vocabulary

#: The three tenant files this module owns. ``role-vocabulary.toml`` and
#: ``premise-vocab.toml`` are read through their existing loaders, never re-parsed here.
CLAIMS_FILE = "claims.toml"
PROOF_FILE = "proof.toml"
ANGLES_FILE = "angles.toml"

#: How sure we are of a claim. ``verified`` is the only status a body may assert as fact;
#: the other two exist so a roadmap item can be *recorded* rather than quietly promoted.
CLAIM_STATUSES = frozenset({"verified", "conditional", "design-target"})

#: What a number in a proof is. ``disputed`` is deliberately representable: a figure we
#: cannot stand behind has to be nameable, or it comes back as a "memory" in six weeks.
FIGURE_KINDS = frozenset({"measured", "illustrative", "disputed", "none"})

#: What a proof is evidence *of* — an external obligation, a measurement, or a result.
PROOF_KINDS = frozenset({"anchor", "stat", "outcome"})

#: Whether an angle opens on something that happened at the account or in public.
OPENER_KINDS = frozenset({"account-event", "public-event"})

#: The one claim status a body may draft FROM, and therefore the only one a ``live`` angle
#: may cite. Named rather than spelled out at each use: the writer
#: (:mod:`gtm_core.messaging.angle_status`) refuses the *move* to ``live`` and this module
#: refuses the *state*, and two spellings of one word is how one of the two drifts.
VERIFIED = "verified"

#: An angle's lifecycle. ``retired`` and not deleted — a retired angle is evidence about
#: what was tried, and deleting it invites the next session to re-derive it.
#:
#: ``draft`` is the only status an angle on a ``conditional`` or ``design-target`` claim may
#: hold, which is what ``draft`` is *for* — a recorded argument waiting on a verification.
ANGLE_STATUSES = frozenset({"draft", "live", "retired"})

#: Frozen key allowlists. A typo'd field (``statment``) is dropped without complaint by
#: every TOML reader in existence, so the only place it can be caught is here.
_CLAIM_KEYS = frozenset({"id", "group", "status", "statement", "source", "do_not_say", "notes"})
_CLAIM_REQUIRED = ("id", "group", "status", "statement")

_PROOF_KEYS = frozenset(
    {"id", "kind", "market", "figure_kind", "statement", "source", "binding", "notes"}
)
_PROOF_REQUIRED = ("id", "kind", "figure_kind", "statement")

_ANGLE_KEYS = frozenset(
    {
        "id",
        "seat",
        "premise",
        "claim",
        "proof",
        "opener_kind",
        "stakes",
        "summary",
        "status",
        "segments",
        "notes",
    }
)
_ANGLE_REQUIRED = (
    "id",
    "seat",
    "premise",
    "claim",
    "proof",
    "opener_kind",
    "summary",
    "status",
)


class RegistryError(ValueError):
    """A registry that cannot be trusted to answer "what may this email say".

    The message is one line per defect, each naming the file and the id, and nothing
    else. It is read by whoever is editing the TOML.
    """


@dataclass(frozen=True)
class Claim:
    """One thing the product does, and how sure we are of it."""

    id: str
    group: str
    status: str
    statement: str
    source: str = ""
    do_not_say: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Proof:
    """One external obligation, measurement or result a claim can lean on."""

    id: str
    kind: str
    figure_kind: str
    statement: str
    market: str = ""
    source: str = ""
    binding: bool = False
    notes: str = ""


@dataclass(frozen=True)
class Angle:
    """One offer to one seat: a premise, a claim, a proof, and the cell text."""

    id: str
    seat: str
    premise: str
    claim: str
    proof: str
    opener_kind: str
    summary: str
    status: str
    stakes: str = ""
    segments: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class Registry:
    """One tenant's resolved outbound facts.

    Frozen, and every dict keyed by the **lowercased** id: two callers in one process
    disagreeing about whether ``Audit-Signed`` and ``audit-signed`` are the same claim is
    exactly the ambiguity :func:`load` refuses at the door.
    """

    claims: dict[str, Claim]
    proof: dict[str, Proof]
    angles: dict[str, Angle]
    seats: dict[str, tuple[str, ...]]

    @property
    def angle_count(self) -> int:
        return len(self.angles)

    def live_angles(self) -> tuple[Angle, ...]:
        """The angles an operator has promoted, in id order so two callers agree."""
        return tuple(a for _, a in sorted(self.angles.items()) if a.status == "live")


# --- parsing -------------------------------------------------------------------------


def _read_table(path: Path, table: str, errors: list[str]) -> list[dict]:
    """The raw blocks of one file, or ``[]`` plus a recorded defect.

    A **missing** file is a defect, not an empty table. A tenant onboarded without
    ``angles.toml`` would otherwise resolve zero angles and read as covered.
    """
    label = path.name
    if not path.is_file():
        errors.append(f"{label}: missing — every tenant declares this table, even empty")
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"{label}: unreadable — {exc}")
        return []
    blocks = raw.get(table) or []
    if not isinstance(blocks, list):
        errors.append(f"{label}: `{table}` must be a list of [[{table}]] blocks")
        return []
    return blocks


def _shaped(
    blocks: list[dict],
    label: str,
    table: str,
    allowed: frozenset[str],
    required: tuple[str, ...],
    errors: list[str],
) -> list[tuple[str, dict]]:
    """``(lowercased id, block)`` for every block whose shape is usable.

    Shape is checked before content so a defect can be reported against an id. A block
    with no usable id is reported by position instead — the only case where the operator
    has to count blocks, and the only case where there is nothing else to name it by.
    """
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for i, block in enumerate(blocks, start=1):
        where = f"[[{table}]] #{i}"
        if not isinstance(block, dict):
            errors.append(f"{label}: {where} — not a table")
            continue

        unknown = sorted(set(block) - allowed)
        raw_id = block.get("id")
        name = str(raw_id).strip().lower() if isinstance(raw_id, str) and raw_id.strip() else ""
        if name:
            try:
                _safe_segment(name, f"{table} id")
            except ValueError as exc:
                errors.append(f"{label}: {where} — {exc}")
                name = ""
        where = name or where

        for key in unknown:
            errors.append(f"{label}: {where} — unknown key `{key}`")
        for key in required:
            if block.get(key) in (None, ""):
                errors.append(f"{label}: {where} — missing required key `{key}`")
        if not name:
            continue
        if name in seen:
            errors.append(f"{label}: {name} — declared twice (ids are case-insensitive)")
            continue
        seen.add(name)
        if all(block.get(key) not in (None, "") for key in required):
            out.append((name, block))
    return out


def _text(block: dict, key: str) -> str:
    value = block.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _str_tuple(
    block: dict,
    key: str,
    label: str,
    where: str,
    errors: list[str],
    *,
    lower: bool = False,
) -> tuple[str, ...]:
    """A list of non-empty strings, or a recorded defect.

    ``lower`` is opt-in rather than the default: ``do_not_say`` is prose an operator wrote and
    case is part of it, while a ``segments`` entry is a KEY joined against
    ``role-vocabulary.toml``, which folds its own with ``_str_tuple``. Two spellings of one
    segment do not refuse anywhere — they split a matrix grid in half and each side reads full.
    """
    raw = block.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        errors.append(f"{label}: {where} — `{key}` must be a list of strings")
        return ()
    values = (x.strip() for x in raw)
    return tuple((x.lower() if lower else x) for x in values if x)


def _in_set(
    block: dict, key: str, allowed: frozenset[str], label: str, where: str, errors: list[str]
) -> str:
    """A closed-set field, or a defect naming the field, the value and the alternatives."""
    value = _text(block, key).lower()
    if value not in allowed:
        errors.append(
            f"{label}: {where} — `{key}` is `{value}`, not one of {'/'.join(sorted(allowed))}"
        )
    return value


def _parse_claims(blocks: list[tuple[str, dict]], errors: list[str]) -> dict[str, Claim]:
    out: dict[str, Claim] = {}
    for name, block in blocks:
        status = _in_set(block, "status", CLAIM_STATUSES, CLAIMS_FILE, name, errors)
        source = _text(block, "source")
        # A `verified` status is a promise the reader can go and check. Without a source
        # there is nothing to check, and the status is an assertion about an assertion.
        if status == "verified" and not source:
            errors.append(f"{CLAIMS_FILE}: {name} — `verified` with no `source`")
        out[name] = Claim(
            id=name,
            group=_text(block, "group"),
            status=status,
            statement=_text(block, "statement"),
            source=source,
            do_not_say=_str_tuple(block, "do_not_say", CLAIMS_FILE, name, errors),
            notes=_text(block, "notes"),
        )
    return out


def _parse_proof(blocks: list[tuple[str, dict]], errors: list[str]) -> dict[str, Proof]:
    out: dict[str, Proof] = {}
    for name, block in blocks:
        binding = block.get("binding", False)
        if not isinstance(binding, bool):
            errors.append(f"{PROOF_FILE}: {name} — `binding` must be true or false")
            binding = False
        out[name] = Proof(
            id=name,
            kind=_in_set(block, "kind", PROOF_KINDS, PROOF_FILE, name, errors),
            figure_kind=_in_set(block, "figure_kind", FIGURE_KINDS, PROOF_FILE, name, errors),
            statement=_text(block, "statement"),
            market=_text(block, "market"),
            source=_text(block, "source"),
            binding=binding,
            notes=_text(block, "notes"),
        )
    return out


def _parse_angles(blocks: list[tuple[str, dict]], errors: list[str]) -> dict[str, Angle]:
    out: dict[str, Angle] = {}
    for name, block in blocks:
        out[name] = Angle(
            id=name,
            seat=_text(block, "seat").lower(),
            premise=_text(block, "premise").lower(),
            claim=_text(block, "claim").lower(),
            proof=_text(block, "proof").lower(),
            opener_kind=_in_set(block, "opener_kind", OPENER_KINDS, ANGLES_FILE, name, errors),
            summary=_text(block, "summary"),
            status=_in_set(block, "status", ANGLE_STATUSES, ANGLES_FILE, name, errors),
            stakes=_text(block, "stakes"),
            segments=_str_tuple(block, "segments", ANGLES_FILE, name, errors, lower=True),
            notes=_text(block, "notes"),
        )
    return out


def _check_references(
    angles: dict[str, Angle],
    claims: dict[str, Claim],
    proof: dict[str, Proof],
    seats: dict[str, tuple[str, ...]],
    premises: set[str],
    segments: set[str],
    errors: list[str],
) -> None:
    """Five references per angle, each checked against the table that owns it — and one rule
    about what a reference is allowed to SAY.

    These are relationships BETWEEN files, so they cannot be checked where either side is
    written. A dangling reference does not raise downstream — it renders an empty slot,
    which is a generic email with a confident shape.

    ``segments`` is the fifth and was the one with no home check, while the template taught
    ``segments = ["Enterprise"]`` — exactly the shape ``role_vocabulary``'s seat-side refusal
    exists to catch. It is a list rather than a scalar, so it is checked beside the four
    rather than with them.

    The sixth (2026-09-24) is not a reference check at all: a ``live`` angle may only cite a
    ``verified`` claim. Both this repo's PRD and its plan described that state as
    *unrepresentable*, and it was not — it was only refused by
    :func:`gtm_core.messaging.angle_status.promote`, the writer. A writer that refuses is
    worth nothing against a file a human edits in an editor, so the loader refuses the state
    as well: one stops it being created, the other stops it being read. Conditional on
    ``status`` on purpose — an angle on a ``conditional`` or ``design-target`` claim is
    legitimate while it is ``draft``, and refusing the claim alone would delete the tenant's
    whole backlog of arguments waiting on a verification.
    """
    homes = (
        ("claim", claims, CLAIMS_FILE),
        ("proof", proof, PROOF_FILE),
        ("seat", seats, "role-vocabulary.toml"),
        ("premise", premises, "premise-vocab.toml"),
    )
    for name, angle in sorted(angles.items()):
        for field, known, home in homes:
            value = getattr(angle, field)
            if value and value not in known:
                errors.append(f"{ANGLES_FILE}: {name} — `{field}` `{value}` is not in {home}")
        for segment in angle.segments:
            if segment not in segments:
                errors.append(
                    f"{ANGLES_FILE}: {name} — `segments` `{segment}` is not in role-vocabulary.toml"
                )
        # `claims.get`, not `claims[...]`: a DANGLING claim is already reported above, and
        # reporting it twice under a second heading sends the operator looking for a second
        # edit that does not exist.
        claim = claims.get(angle.claim)
        if angle.status == "live" and claim is not None and claim.status != VERIFIED:
            errors.append(
                f"{ANGLES_FILE}: {name} — `live` on claim `{claim.id}`, which is "
                f"`{claim.status}` and not `{VERIFIED}`"
            )


def _load_premises(
    profile: str, profiles_root: Path, product: str | None, overlay: str | None
) -> set[str]:
    """The tenant's premise ids, via the one existing parser.

    Imported inside the function on purpose: :mod:`gtm_core.hook_coverage.config` puts
    ``tests/linter`` on ``sys.path`` at import time, and the registry's own import graph
    stays clear of a dev-only tree. A tenant that ships no ``premise-vocab.toml`` simply
    declares no premises — that loader's existing opt-in convention, not a second one.
    """
    from ..hook_coverage.premise import load_premise_vocab

    return set(load_premise_vocab(profile, profiles_root, product, overlay))


def load(
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> Registry:
    """This run's registry, or :class:`RegistryError` listing every defect.

    Each file resolves through :func:`gtm_core.paths.resolve_knowledge_file` — overlay,
    then product, then profile — so a registry may be overridden per product exactly like
    any other knowledge file.
    """
    root = profiles_root or resolve_profiles_root()
    errors: list[str] = []

    def _path(filename: str) -> Path:
        return resolve_knowledge_file(root, profile, filename, product=product, overlay=overlay)

    claims = _parse_claims(
        _shaped(
            _read_table(_path(CLAIMS_FILE), "claim", errors),
            CLAIMS_FILE,
            "claim",
            _CLAIM_KEYS,
            _CLAIM_REQUIRED,
            errors,
        ),
        errors,
    )
    proof = _parse_proof(
        _shaped(
            _read_table(_path(PROOF_FILE), "proof", errors),
            PROOF_FILE,
            "proof",
            _PROOF_KEYS,
            _PROOF_REQUIRED,
            errors,
        ),
        errors,
    )
    angles = _parse_angles(
        _shaped(
            _read_table(_path(ANGLES_FILE), "angle", errors),
            ANGLES_FILE,
            "angle",
            _ANGLE_KEYS,
            _ANGLE_REQUIRED,
            errors,
        ),
        errors,
    )

    seats: dict[str, tuple[str, ...]] = {}
    segments: set[str] = set()
    try:
        vocabulary = load_vocabulary(profile, root, product, overlay)
        seats = {seat: vocabulary.stakes_for(seat) for seat in vocabulary.seats}
        segments = set(vocabulary.segments)
    except VocabularyError as exc:
        errors.append(f"role-vocabulary.toml: {exc}")

    _check_references(
        angles,
        claims,
        proof,
        seats,
        _load_premises(profile, root, product, overlay),
        segments,
        errors,
    )

    if errors:
        raise RegistryError("\n".join(errors))
    return Registry(claims=claims, proof=proof, angles=angles, seats=seats)
