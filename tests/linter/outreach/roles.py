"""Title -> persona -> seat. The ONE implementation, re-exported by
``gtm_core.hook_coverage.config`` and reached that way by ``build_eval_sheet`` / ``cells``.
A second resolver would drift silently: the page would report cells the gate never checked.
"""

from __future__ import annotations

import re
from functools import cache

# --------------------------------------------------------------------------- persona lead pain
#
# voice.md's persona-axis table says what each seat leads on. The recurring failure is firing the
# CISO's attribution pain at every seat, because attribution is what WE find interesting: a
# 2026-08-17 batch sent audit/examiner framing to eight CEOs and a revenue lead to a real CISO.
#
# Seat -> (title cues, stakes vocabulary that belongs to this seat).
# --------------------------------------------------------------------------- persona axis
#
# THE VOCABULARY ITSELF NOW LIVES IN TENANT DATA — :mod:`gtm_core.role_vocabulary`.
#
# It moved on 2026-09-21. Which personas a tenant sells to, which seat each reads, and what
# that seat's stakes sound like are ICP facts, and while they were literals here no tenant
# could change them: measured that day, one profile had 5 of its 7 matrix personas and
# another 4 of its 8 normalising onto nothing, which does not raise — ``hook_coverage``
# books those rows as unassignable and the tenant reads a coverage report showing zero.
#
# What did NOT move is the resolver. :func:`persona_of` / :func:`seat_of` stay here beside
# the rules they police, and every consumer still imports them from this module. Two
# implementations of "which seat is this person" would drift, and the drift would be
# invisible — the page would report cells the gate never checked.
#
# The names below are the DEFAULT vocabulary, re-exported so the boundary tests that pin
# the substring traps (``test_persona_cue_boundaries.py``) keep asserting against the
# shipped default. A profile that ships ``knowledge/role-vocabulary.toml`` overrides it, and
# the functions resolve per call — so read these as "what a tenant gets before it
# customises", never as "what this run is using".
from gtm_core.role_vocabulary import (  # noqa: E402
    DEFAULT_VOCABULARY as _DEFAULT_VOCABULARY,
)
from gtm_core.role_vocabulary import (  # noqa: E402
    RoleVocabulary as RoleVocabulary,
)
from gtm_core.role_vocabulary import (  # noqa: E402
    load as _load_vocabulary,
)

from .text import _cue_re, _matches

# --------------------------------------------------------------------------- vp spellings
#
# PH12 (2026-09-25). The cue lists carry the ABBREVIATION, so "VP of Engineering" resolved to
# ``cto`` while "Senior Vice President of Engineering", "SVP Engineering" and "VP, Engineering"
# resolved to nothing — the same seat, spelled differently. A tenant can paper over it one cue
# at a time in its vocabulary, which is how the gap kept recurring for every other seat.
#
# The fix canonicalises BOTH sides of the positive match the same way: the title, and every
# cue. Canonicalising only the title is wrong — a cue a tenant already spelled out
# ("vice president of engineering") would stop matching the title it was written for.
#
# The ANTI-cues are deliberately NOT canonicalised and see the RAW title: ``evp`` / ``svp`` /
# ``vice president`` are what veto the bare ``president`` exec cue, and an EVP must never be
# re-seated as the exec because its rank was rewritten to ``vp`` before the veto looked.
_VP_RANK_RE = re.compile(r"\b(?:vice[\s-]+president|[sea]vp)\b")
#: "vp, engineering" / "vp - engineering" / "vp of engineering" -> "vp engineering".
_VP_JOIN_RE = re.compile(r"\bvp(?:[\s,:;\-–—]+of)?[\s,:;\-–—]+")


@cache
def _canon(text: str) -> str:
    """Lowercase, fold every vice-president spelling to ``vp``, and drop what joins it to
    the function it heads. Applied to titles and positive cues alike, never to anti-cues."""
    return _VP_JOIN_RE.sub("vp ", _VP_RANK_RE.sub("vp", text.lower()))


@cache
def _canon_cues(cues: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_canon(c) for c in cues)


_ANTI_CUES = _DEFAULT_VOCABULARY.anti_cues
_CEO_TITLE_CUES = _DEFAULT_VOCABULARY.ceo_title_cues
_PERSONA_RULES = _DEFAULT_VOCABULARY.persona_rules
_NON_BUYER_CUES = _DEFAULT_VOCABULARY.non_buyer_cues
SEGMENT_DEFAULT_PERSONA = _DEFAULT_VOCABULARY.default_persona
_SEAT_RULES = _DEFAULT_VOCABULARY.seat_rules
_SECURITY_ONLY = _DEFAULT_VOCABULARY.security_only

#: ``persona -> seat`` for the DEFAULT vocabulary, derived so the two views can never
#: disagree. Per-run callers should use ``_load_vocabulary(profile).persona_to_seat``.
_PERSONA_TO_SEAT: dict[str, str] = _DEFAULT_VOCABULARY.persona_to_seat


def persona_of(header: str, profile: str | None = None) -> str | None:
    """Which matrix persona this recipient's title names, or None if unrecognised.

    The finer of the two views. Fail-quiet: an unrecognised title says nothing about the
    person, and guessing is how a cost argument reaches a security reviewer. Callers are
    expected to COUNT and NAME the None bucket rather than hide it — "this list has no
    FinOps leads" and "we cannot see the FinOps leads in this list" are different facts
    and only one is good news.

    Fail-quiet is about what this function ASSERTS, not about what the caller may then do.
    A ``None`` here is an unknown seat; a caller choosing what to send such a person should
    route it to :data:`SEGMENT_DEFAULT_PERSONA` rather than honouring whatever persona a
    spec happened to declare, which is how a body written for a CISO reached an SME owner.
    """
    low = (header or "").lower()
    canon = _canon(low)
    vocab = _load_vocabulary(profile)
    # An explicit exec TITLE outranks a lower functional cue elsewhere in the same compound
    # title; see ``ceo_title_cues`` for why rank and ownership cues are excluded. The
    # anti-cues still veto, so "Executive Vice President, Engineering" is unaffected.
    if _matches(vocab.ceo_title_cues, low) and not _matches(vocab.anti_cues.get("ceo", ()), low):
        return "ceo"
    for persona, cues in vocab.persona_rules:
        # Positive match on the canonical form; the anti-cue veto below on the RAW title.
        if not _matches(_canon_cues(tuple(cues)), canon):
            continue
        if _matches(vocab.anti_cues.get(persona, ()), low):
            continue
        return persona
    return None


def non_buyer_of(header: str, profile: str | None = None) -> str | None:
    """The cue that marks this title as someone who cannot act on a governance pitch.

    Deliberately SEPARATE from :func:`persona_of`, and deliberately small. ``persona_of``
    returning None means "we cannot see this seat"; this returning a cue means "we can see
    it, and it is not a buyer" — two facts that look identical in a None bucket and lead to
    opposite actions (resolve a better contact vs. do not contact this company at all).

    Only titles that are unambiguously not a decision-maker for THIS product are listed. A
    role that is a support function at a bank but the owner at a ten-person agency (an
    operations manager, a digital-transformation lead) is NOT here: it stays unrecognised,
    because asserting non-buyer on it would drop real SME buyers. Under-claiming here is
    cheap; over-claiming deletes accounts.

    Found by the 2026-09-06 judge pass, which flagged an Administrative Assistant and an
    Executive Assistant as recipients of a runtime-governance argument. Nothing deterministic
    was checking, because ``lint_persona_lead`` cannot fire on a title it does not recognise.
    """
    low = (header or "").lower()
    for cue in _load_vocabulary(profile).non_buyer_cues:
        if _cue_re(cue).search(low):
            return cue
    return None


def seat_of(header: str, profile: str | None = None) -> str | None:
    """Which seat's copy this recipient should receive, or None if unrecognised.

    The coarser view, derived from :func:`persona_of` so the two cannot drift. Returns
    None for a persona that has no seat (``finops``, ``partnership``) exactly as it does
    for an unrecognised title — in both cases nothing is owed and nothing is claimed.
    """
    persona = persona_of(header, profile)
    return _load_vocabulary(profile).persona_to_seat.get(persona) if persona else None
