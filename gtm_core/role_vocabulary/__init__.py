"""Role vocabulary — a tenant's persona / seat / segment vocabulary, as tenant data.

**What this is, and why it moved here.** Which personas a tenant sells to, which seat each
persona reads, what that seat's stakes sound like, and which segments a run may select are
**ICP facts** — the same class as ``icp-personas.md`` or ``icp-scoring.toml``. Until
2026-09-21 they lived as Python literals in ``tests/linter/outreach_pack_linter.py`` and
``gtm_core/merge_hygiene/company.py``, where no tenant could change them. That is a tenant
boundary violation (CLAUDE.md) and it degraded **silently**, which is why it survived:

* one profile — 5 of its 7 matrix personas did not normalise onto the vocabulary
* another — 4 of 8

(Named here only as counts: which tenants those are is tenant data, and §R9 keeps a real
organisation's identity out of code. ``python -m gtm_core.hook_coverage --profile P
--matrix-only`` names them on a live tree.)

An unmapped persona does not raise. ``hook_coverage`` books its rows as *unassignable*, so
the tenant reads a coverage report showing zero and concludes the copy is bad.

**A default is not the problem; an UNOVERRIDABLE default is.** The values below stay as
``DEFAULT_*`` and remain a reasonable generic B2B-SaaS vocabulary (ciso / cto / ceo /
architect / product / ai-platform). A profile that ships no ``role-vocabulary.toml`` gets
exactly them, so nothing changes behaviour on landing. A profile that ships one gets its own.

**One implementation, still.** ``persona_of`` / ``seat_of`` continue to live in
``outreach_pack_linter`` beside the rules they police, and every consumer keeps importing
them from there. This module supplies the *data* those functions read; it does not add a
second resolver. Two implementations of "which seat is this person" would drift, and the
drift would be invisible — the page would report cells the gate never checked.

**Resolution** goes through :func:`gtm_core.paths.resolve_knowledge_file`, so a
``role-vocabulary.toml`` may be overridden per product — or, for the length of one run, by an
experiment overlay (:mod:`gtm_core.experiments`) — exactly like any other knowledge file.
That matters more here than elsewhere: an experiment that targets a new buyer usually needs
new personas before it needs anything else, and without this it would have to edit the live
tenant vocabulary to describe a cohort it is only testing.

**Validation is fail-loud.** A seat naming a persona no rule can produce, a persona claimed
by two seats, or a ``default_persona`` nothing resolves are each a hard error naming the
offending value. The failure this guards is the one that created this module: a targeting
fact that is wrong but not *loud* is one nobody finds.

CLI::

    python -m gtm_core.role_vocabulary --profile P            # summary + validation
    python -m gtm_core.role_vocabulary --profile P --json     # the resolved vocabulary
    python -m gtm_core.role_vocabulary --profile P --check    # exit 2 if invalid
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..paths import clean_env_var, resolve_knowledge_file, resolve_profiles_root
from .defaults import (  # noqa: F401 — re-exported as this package's public surface
    DEFAULT_ANTI_CUES,
    DEFAULT_CEO_TITLE_CUES,
    DEFAULT_NON_BUYER_CUES,
    DEFAULT_PERSONA_RULES,
    DEFAULT_SEAT_FORBIDDEN_PAINS,
    DEFAULT_SEAT_GAIN,
    DEFAULT_SEAT_LEAD_PAIN,
    DEFAULT_SEAT_REGISTER,
    DEFAULT_SEAT_RULES,
    DEFAULT_SEAT_SEGMENTS,
    DEFAULT_SECURITY_ONLY,
    DEFAULT_SEGMENT_PERSONA,
    DEFAULT_SEGMENTS,
    SEAT_REGISTERS,
    UNSPECIFIED_SEGMENT,
)

#: The tenant file this module reads. Resolved product-first, profile-fallback.
VOCABULARY_FILE = "role-vocabulary.toml"


class VocabularyError(ValueError):
    """A role vocabulary that cannot be trusted to answer "which seat is this person".

    Raised at load, never at use. A vocabulary that is wrong in a way this class can see is
    refused outright rather than allowed to mis-seat every recipient it touches: a
    confidently wrong seat and a correct one look identical downstream, which is the whole
    reason this validation exists.
    """


@dataclass(frozen=True)
class RoleVocabulary:
    """One tenant's resolved role vocabulary.

    Field-for-field the data ``persona_of`` / ``seat_of`` / ``lint_persona_lead`` read.
    Frozen, because a vocabulary that can be mutated after load is one where two callers in
    the same process can disagree about who someone is.
    """

    anti_cues: dict[str, tuple[str, ...]]
    ceo_title_cues: tuple[str, ...]
    persona_rules: tuple[tuple[str, tuple[str, ...]], ...]
    non_buyer_cues: tuple[str, ...]
    default_persona: str
    seat_rules: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]
    security_only: tuple[str, ...]
    segments: tuple[str, ...]
    #: The seat's outbound-copy facts, each keyed by seat name (2026-09-24). Kept BESIDE
    #: ``seat_rules`` rather than widened into it: that tuple is what ``persona_of`` /
    #: ``seat_of`` and the linter read on every row, and growing a hot 3-tuple to an 8-tuple
    #: to carry copy would make every consumer of the resolver unpack fields it never uses.
    #: Absent is "" / () everywhere — see :data:`defaults.DEFAULT_SEAT_LEAD_PAIN` for why a
    #: seat's lead pain is never guessed.
    lead_pain: dict[str, str] = field(default_factory=dict)
    gain: dict[str, str] = field(default_factory=dict)
    forbidden_pains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    register: dict[str, str] = field(default_factory=dict)
    seat_segments: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source: str = "built-in default"

    @property
    def persona_to_seat(self) -> dict[str, str]:
        """``persona -> seat``, derived so the two views can never disagree."""
        return {p: seat for seat, personas, _ in self.seat_rules for p in personas}

    @property
    def personas(self) -> frozenset[str]:
        """Every persona a rule can actually produce."""
        return frozenset(name for name, _ in self.persona_rules)

    @property
    def seats(self) -> tuple[str, ...]:
        return tuple(seat for seat, _, _ in self.seat_rules)

    def stakes_for(self, seat: str) -> tuple[str, ...]:
        """The seat's own stakes vocabulary, or ``()`` if it has none."""
        return next((words for name, _, words in self.seat_rules if name == seat), ())

    # The five accessors below mirror :meth:`stakes_for` exactly — an empty answer for a
    # seat that declared nothing, and for a seat that does not exist. Never a fabricated
    # default: a caller has to be able to tell "this tenant has not written this seat's
    # pain yet" from "this is the pain", and only an empty value says the first out loud.

    def lead_pain_for(self, seat: str) -> str:
        """The pain this seat's copy LEADS on, or ``""`` if the tenant declared none."""
        return self.lead_pain.get(seat, "")

    def gain_for(self, seat: str) -> str:
        """The unlock this seat's copy promises, or ``""``."""
        return self.gain.get(seat, "")

    def forbidden_pains_for(self, seat: str) -> tuple[str, ...]:
        """Pains that belong to a DIFFERENT seat and must not be fired at this one."""
        return self.forbidden_pains.get(seat, ())

    def register_for(self, seat: str) -> str:
        """The seat's formality register (see :data:`SEAT_REGISTERS`), or ``""``."""
        return self.register.get(seat, "")

    def segments_for(self, seat: str) -> tuple[str, ...]:
        """The segments this seat's copy is aimed at. ``()`` means "no segment scope"."""
        return self.seat_segments.get(seat, ())

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "default_persona": self.default_persona,
            "segments": list(self.segments),
            "ceo_title_cues": list(self.ceo_title_cues),
            "security_only": list(self.security_only),
            "non_buyer_cues": list(self.non_buyer_cues),
            "anti_cues": {k: list(v) for k, v in self.anti_cues.items()},
            "persona": [{"name": n, "cues": list(c)} for n, c in self.persona_rules],
            "seat": [
                {
                    "name": s,
                    "personas": list(p),
                    "stakes": list(w),
                    # Always emitted, never omitted when empty: `--json` is how an operator
                    # checks what a seat carries, and a key that disappears when unset
                    # cannot be told apart from a key the exporter forgot.
                    "lead_pain": self.lead_pain_for(s),
                    "gain": self.gain_for(s),
                    "forbidden_pains": list(self.forbidden_pains_for(s)),
                    "register": self.register_for(s),
                    "segments": list(self.segments_for(s)),
                }
                for s, p, w in self.seat_rules
            ],
        }


#: What every profile gets until it ships its own file.
DEFAULT_VOCABULARY = RoleVocabulary(
    anti_cues=DEFAULT_ANTI_CUES,
    ceo_title_cues=DEFAULT_CEO_TITLE_CUES,
    persona_rules=DEFAULT_PERSONA_RULES,
    non_buyer_cues=DEFAULT_NON_BUYER_CUES,
    default_persona=DEFAULT_SEGMENT_PERSONA,
    seat_rules=DEFAULT_SEAT_RULES,
    security_only=DEFAULT_SECURITY_ONLY,
    segments=DEFAULT_SEGMENTS,
    # Passed explicitly, though they are empty, so that "the default ships no copy facts" is
    # a visible decision here rather than an omission a reader has to infer.
    lead_pain=DEFAULT_SEAT_LEAD_PAIN,
    gain=DEFAULT_SEAT_GAIN,
    forbidden_pains=DEFAULT_SEAT_FORBIDDEN_PAINS,
    register=DEFAULT_SEAT_REGISTER,
    seat_segments=DEFAULT_SEAT_SEGMENTS,
)


# --- parsing + validation ------------------------------------------------------------


def _str_tuple(raw, field: str, source: str) -> tuple[str, ...]:
    """A list of non-empty strings, or a named error. Never a silently-coerced value."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise VocabularyError(
            f"{source}: `{field}` must be a list of strings, got {type(raw).__name__}"
        )
    out = tuple(x.strip().lower() for x in raw if x and x.strip())
    if len(out) != len(raw):
        raise VocabularyError(
            f"{source}: `{field}` contains an empty entry — a blank cue matches nothing and hides the gap"
        )
    return out


def _parse_personas(raw: dict, source: str) -> list[tuple[str, tuple[str, ...]]]:
    """The ordered persona rules. Order is load-bearing — first match wins outright."""
    persona_rules: list[tuple[str, tuple[str, ...]]] = []
    for i, block in enumerate(raw.get("persona") or []):
        if not isinstance(block, dict) or not block.get("name"):
            raise VocabularyError(f"{source}: [[persona]] #{i + 1} has no `name`")
        name = str(block["name"]).strip().lower()
        cues = _str_tuple(block.get("cues"), f"persona.{name}.cues", source)
        if not cues:
            raise VocabularyError(
                f"{source}: persona `{name}` has no cues — it can never be resolved from a "
                f"title, so every recipient it should cover lands in the None bucket"
            )
        persona_rules.append((name, cues))
    if not persona_rules:
        raise VocabularyError(
            f"{source}: no [[persona]] blocks — a vocabulary with no personas resolves nothing"
        )

    names = [n for n, _ in persona_rules]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise VocabularyError(
            f"{source}: persona(s) declared twice: {dupes}. First match wins, so the second is unreachable"
        )
    return persona_rules


def _str_field(raw, field_name: str, source: str) -> str:
    """One string of prose, or a named error. Absent is ``""``; a list or a number is not.

    Deliberately NOT lowercased, unlike :func:`_str_tuple`. Cues are matched against titles
    and so normalise; a lead pain is a sentence a human reads in an email, and folding its
    case would make every tenant's copy arrive shouting in lower case.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise VocabularyError(
            f"{source}: `{field_name}` must be a string, got {type(raw).__name__}"
        )
    return raw.strip()


@dataclass(frozen=True)
class _SeatCopy:
    """The per-seat outbound-copy facts, collected while the seat blocks are read.

    Returned alongside the seat rules rather than folded into them — see the note on
    :attr:`RoleVocabulary.lead_pain` for why the hot 3-tuple stays a 3-tuple.
    """

    lead_pain: dict[str, str]
    gain: dict[str, str]
    forbidden_pains: dict[str, tuple[str, ...]]
    register: dict[str, str]
    segments: dict[str, tuple[str, ...]]


def _parse_seats(
    raw: dict, known: set[str], source: str
) -> tuple[list[tuple[str, tuple[str, ...], tuple[str, ...]]], _SeatCopy]:
    """The seat rules, checked against the personas that actually exist.

    Both persona failures below are relationships between two blocks, which is why they
    cannot be validated where each block is written. The copy fields collected here are
    checked two ways: whatever is checkable WITHIN the block (a type, a closed set) is
    checked here, and the one relationship — a seat's segments against the file's declared
    ``segments`` — is checked in :func:`parse`, where both halves are in hand.
    """
    seat_rules: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    claimed: dict[str, str] = {}
    copy = _SeatCopy({}, {}, {}, {}, {})
    for i, block in enumerate(raw.get("seat") or []):
        if not isinstance(block, dict) or not block.get("name"):
            raise VocabularyError(f"{source}: [[seat]] #{i + 1} has no `name`")
        name = str(block["name"]).strip().lower()
        personas = _str_tuple(block.get("personas"), f"seat.{name}.personas", source)
        stakes = _str_tuple(block.get("stakes"), f"seat.{name}.stakes", source)
        copy.lead_pain[name] = _str_field(block.get("lead_pain"), f"seat.{name}.lead_pain", source)
        copy.gain[name] = _str_field(block.get("gain"), f"seat.{name}.gain", source)
        # A scalar here is the DANGEROUS shape, not merely the wrong one: a linter iterating
        # a bare string would forbid every character of it and match on all of them, so the
        # seat would silently forbid its own copy. `_str_tuple` names the seat in the error.
        copy.forbidden_pains[name] = _str_tuple(
            block.get("forbidden_pains"), f"seat.{name}.forbidden_pains", source
        )
        copy.segments[name] = _str_tuple(block.get("segments"), f"seat.{name}.segments", source)
        register = _str_field(block.get("register"), f"seat.{name}.register", source).lower()
        if register and register not in SEAT_REGISTERS:
            raise VocabularyError(
                f"{source}: seat `{name}` declares register `{register}`, which is not one "
                f"of {sorted(SEAT_REGISTERS)}. A register selects a whole surface of copy, "
                f"so an unrecognised one can only fall back to a register the tenant did "
                f"not choose or render nothing — both invisible at send time"
            )
        copy.register[name] = register
        for p in personas:
            if p not in known:
                raise VocabularyError(
                    f"{source}: seat `{name}` covers persona `{p}`, which no [[persona]] block "
                    f"produces. A seat owed copy for a persona nothing resolves is a copy "
                    f"obligation with no reader"
                )
            if p in claimed:
                raise VocabularyError(
                    f"{source}: persona `{p}` is claimed by both seat `{claimed[p]}` and seat "
                    f"`{name}`. One persona reads one seat's copy; two is ambiguous, and the "
                    f"derived persona->seat map would silently keep whichever came last"
                )
            claimed[p] = name
        seat_rules.append((name, personas, stakes))
    return seat_rules, copy


def _parse_anti_cues(raw: dict, known: set[str], source: str) -> dict[str, tuple[str, ...]]:
    """Per-persona veto cues, checked against the personas that exist.

    A veto keyed on a persona nothing produces never fires, and reads in the file as
    protection that is not there — the most expensive kind of wrong, because it is the kind
    someone relies on.
    """
    anti_raw = raw.get("anti_cues") or {}
    if not isinstance(anti_raw, dict):
        raise VocabularyError(f"{source}: `anti_cues` must be a table of persona -> list of cues")
    anti: dict[str, tuple[str, ...]] = {}
    for persona, cues in anti_raw.items():
        key = str(persona).strip().lower()
        if key not in known:
            raise VocabularyError(
                f"{source}: anti_cues names persona `{key}`, which no [[persona]] block "
                f"produces — the veto would never fire and reads as protection that is not there"
            )
        anti[key] = _str_tuple(cues, f"anti_cues.{key}", source)
    return anti


def parse(raw: dict, source: str) -> RoleVocabulary:
    """Build a vocabulary from parsed TOML, validating it as a whole.

    Whole-file validation rather than per-field: nearly every rule here is a relationship
    BETWEEN two blocks — a seat and the personas it claims, a default and the rules that
    produce it, a veto and its target — and a per-field check cannot see any of them. That
    is the same reason ``tests/lint/test_profile_targeting_invariants.py`` exists one layer
    up, and the same reason it is worth the extra indirection to keep each relationship
    checked in exactly one place.
    """
    persona_rules = _parse_personas(raw, source)
    known = {n for n, _ in persona_rules}
    seat_rules, seat_copy = _parse_seats(raw, known, source)
    anti = _parse_anti_cues(raw, known, source)

    default_persona = str(raw.get("default_persona") or "").strip().lower()
    if not default_persona:
        raise VocabularyError(
            f"{source}: `default_persona` is required — it is where a recipient goes when no cue matches"
        )
    if default_persona not in known:
        raise VocabularyError(
            f"{source}: `default_persona` is `{default_persona}`, which no [[persona]] block "
            f"produces. The default exists so the None bucket stops being handed whatever a "
            f"spec declared; one that resolves to nothing restores exactly that bug"
        )

    segments = _str_tuple(raw.get("segments"), "segments", source) or DEFAULT_SEGMENTS
    if UNSPECIFIED_SEGMENT not in segments:
        raise VocabularyError(
            f"{source}: `segments` must include `{UNSPECIFIED_SEGMENT}` — it is the kept value "
            f"for a row nobody classified, and without it such a row normalises to itself and "
            f"is booked as unassignable"
        )

    # A seat's segment scope against the file's own segment vocabulary. This is the third
    # relationship BETWEEN blocks in this file, and it is checked here for the same reason
    # as the other two: `_parse_seats` has not seen `segments` yet, and `segments` has no
    # idea which seats exist.
    for seat, scope in seat_copy.segments.items():
        for seg in scope:
            if seg == UNSPECIFIED_SEGMENT:
                raise VocabularyError(
                    f"{source}: seat `{seat}` is scoped to `{UNSPECIFIED_SEGMENT}`, which is "
                    f"the kept value for a row nobody classified and is never selected into "
                    f"a run mix. Copy aimed there would read as targeted and reach no one"
                )
            if seg not in segments:
                raise VocabularyError(
                    f"{source}: seat `{seat}` is scoped to segment `{seg}`, which `segments` "
                    f"does not declare. The run mix can never select it, so the seat reads "
                    f"as covered while none of its copy is ever sent"
                )

    return RoleVocabulary(
        anti_cues=anti,
        ceo_title_cues=_str_tuple(raw.get("ceo_title_cues"), "ceo_title_cues", source),
        persona_rules=tuple(persona_rules),
        non_buyer_cues=_str_tuple(raw.get("non_buyer_cues"), "non_buyer_cues", source),
        default_persona=default_persona,
        seat_rules=tuple(seat_rules),
        security_only=_str_tuple(raw.get("security_only"), "security_only", source),
        segments=segments,
        lead_pain=seat_copy.lead_pain,
        gain=seat_copy.gain,
        forbidden_pains=seat_copy.forbidden_pains,
        register=seat_copy.register,
        seat_segments=seat_copy.segments,
        source=source,
    )


# --- resolution ----------------------------------------------------------------------


def vocabulary_path(
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> Path:
    """Where this run's vocabulary lives — overlay, then product, then profile."""
    root = profiles_root or resolve_profiles_root()
    return resolve_knowledge_file(root, profile, VOCABULARY_FILE, product=product, overlay=overlay)


@lru_cache(maxsize=32)
def _load_cached(
    profile: str, profiles_root: str, product: str | None, overlay: str | None
) -> RoleVocabulary:
    path = vocabulary_path(profile, Path(profiles_root), product, overlay)
    if not path.is_file():
        return DEFAULT_VOCABULARY
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise VocabularyError(f"{path}: not valid TOML — {exc}") from exc
    return parse(raw, str(path))


def load(
    profile: str | None = None,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> RoleVocabulary:
    """The vocabulary for ``profile``, or the built-in default when it ships no file.

    ``profile=None`` resolves the session's bound profile from the environment
    (``ACTIVE_PROFILE`` / ``GTM_PROFILE``), which ``agent/mcp_config.py`` injects into every
    subprocess. A missing or unreadable profile is NOT an error here: it yields the default,
    because this module's job is to answer "which seat is this person", and refusing to
    answer at all would take down every caller for a tenant that simply never customised.
    A file that EXISTS but is wrong is a different case and does raise (see :func:`parse`).
    """
    name = (
        profile or clean_env_var("ACTIVE_PROFILE") or clean_env_var("GTM_PROFILE") or ""
    ).strip()
    if not name:
        return DEFAULT_VOCABULARY
    root = profiles_root or resolve_profiles_root()
    try:
        return _load_cached(name, str(root), product, overlay)
    except (OSError, ValueError) as exc:
        if isinstance(exc, VocabularyError):
            raise
        return DEFAULT_VOCABULARY


def clear_cache() -> None:
    """Drop the memoised vocabularies. For tests that write a file then re-resolve."""
    _load_cached.cache_clear()


# --- CLI -----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.role_vocabulary",
        description="Resolve, summarise and validate a profile's role vocabulary.",
    )
    ap.add_argument(
        "--profile", default=None, help="profile slug (default: ACTIVE_PROFILE/GTM_PROFILE)"
    )
    ap.add_argument("--product", default=None, help="product slug for a product-level override")
    ap.add_argument("--profiles-root", default=None)
    ap.add_argument("--json", action="store_true", help="print the resolved vocabulary as JSON")
    ap.add_argument(
        "--check", action="store_true", help="validate only; exit 2 on a bad vocabulary"
    )
    args = ap.parse_args(argv)

    root = Path(args.profiles_root) if args.profiles_root else None
    try:
        vocab = load(args.profile, root, args.product)
    except VocabularyError as exc:
        print(f"invalid role vocabulary: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(vocab.to_dict(), indent=2))
        return 0
    print(f"source:    {vocab.source}")
    print(f"personas:  {len(vocab.persona_rules)} — {', '.join(sorted(vocab.personas))}")
    print(f"seats:     {len(vocab.seat_rules)} — {', '.join(vocab.seats)}")
    seatless = sorted(vocab.personas - set(vocab.persona_to_seat))
    if seatless:
        print(f"seatless:  {', '.join(seatless)} (resolve as a persona, owed no copy)")
    print(f"default:   {vocab.default_persona}")
    print(f"segments:  {', '.join(vocab.segments)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI plumbing
    raise SystemExit(main())
