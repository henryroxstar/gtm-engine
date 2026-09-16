"""Title cues match WORDS, not substrings — and the None bucket has a documented default.

All fixtures invented (docs/RULES.md R9) — no real recipient appears here.

Two regressions, one root cause, found together on 2026-09-08.

**The bug.** Cues were tested with plain ``in``, so ``cto`` matched inside ``director``.
Every title carrying that word resolved to the CTO persona: on the 1,193-row pool, 146
titles contain "director" and 128 were seated as CTO — a Director of Information Security
(a CISO), a Managing Director (a CEO), a Director of Product Development (a CPO), a
Director of Talent Acquisition (nobody). Nothing reported it, because a confidently wrong
seat and a correct one are indistinguishable downstream. The module had already been bitten
three times by the same class — bare ``cro``, ``coo`` and ``cio`` were each deleted from the
vocabulary with a comment explaining the substring trap — and each time the fix was applied
to the one cue rather than to the matching rule, which is why the fourth one survived.

**The consequence.** A campaign audit read the resulting mis-seating as a TARGETING defect
("15 of 18 recipients do not hold their declared seat") and nearly rewrote the copy for it.
Most of those recipients held perfectly reasonable seats; the classifier was wrong about
them. A measurement instrument that is broken reports a defect in whatever it measures.

The tests below are the class, not the instance: any future cue that is a substring of a
common title word fails here rather than in a campaign.
"""

from __future__ import annotations

import pytest
from outreach_pack_linter import (
    _CEO_TITLE_CUES,
    _NON_BUYER_CUES,
    _PERSONA_RULES,
    _PERSONA_TO_SEAT,
    SEGMENT_DEFAULT_PERSONA,
    non_buyer_of,
    persona_of,
    seat_of,
)

# ------------------------------------------------------------------ the class of bug


#: Ordinary title words that must never be claimed by a cue hiding inside them. Each is a
#: word that really occurs in the pool; ``director`` is the one that actually shipped.
_INNOCENT_WORDS = (
    "director",  # contains "cto" — the one that actually shipped
    "coordinator",  # contains "coo"
    "microsoft",  # contains "cro"
    "principal",  # contains "cip"
    "associate",  # contains "cia"
    "specialist",  # contains "cia"
    "operations",  # contains "era", "ratio"
    "internal",  # contains "intern"
    "international",  # contains "intern"
)


@pytest.mark.parametrize("word", _INNOCENT_WORDS)
def test_no_persona_cue_hides_inside_an_ordinary_title_word(word):
    """A cue that is a strict substring of a common word will fire on every title
    containing that word. That is how `cto` claimed 128 directors."""
    for persona, cues in _PERSONA_RULES:
        for cue in cues:
            if cue != word and cue in word:
                assert persona_of(word) != persona, (
                    f"cue {cue!r} (persona {persona!r}) matches inside the ordinary word "
                    f"{word!r} — match on word boundaries or drop the bare cue, as the "
                    f"module already had to do for 'cro', 'coo' and 'cio'"
                )


def test_the_specific_collision_that_shipped():
    """`cto` inside `director`. Named separately from the class test so a regression says
    which one came back."""
    assert persona_of("Director") is None
    assert persona_of("Sales Director") is None
    assert persona_of("Executive Director") == "founder-operator"
    assert persona_of("Director of Product Development") == "cpo"
    assert persona_of("Director of Information Security") == "ciso"
    assert persona_of("Managing director") == "ceo"
    # ...while the cue it was supposed to match still matches.
    assert persona_of("CTO") == "cto"
    assert persona_of("Chief Technology Officer") == "cto"
    assert persona_of("Director of Engineering") == "cto"


def test_cues_ending_in_punctuation_still_match_what_follows():
    """Boundaries are applied only at alphanumeric edges. A cue like `chief a.i.` ends in a
    dot; demanding a word boundary after it would never match `Chief A.I. Officer`."""
    assert persona_of("Chief A.I. Officer") == "ai-platform"


def test_the_anti_cues_still_bite():
    """`_ANTI_CUES` is matched the same way, so the vice-president guard must survive the
    change — without it the bare `president` cue makes every VP an exec."""
    assert persona_of("Executive Vice President, Engineering") != "ceo"
    assert persona_of("SVP, Platform") != "ceo"
    assert persona_of("President") == "ceo"


# ------------------------------------------------------------------ the default seat


def test_the_default_persona_exists_and_owes_no_new_copy():
    """`founder-operator` is the answer to "what do we send someone whose seat we cannot
    see". It maps to an EXISTING seat on purpose: below a certain headcount there is no
    functional split, and the hook matrix's CEO / Founder row is already that argument."""
    assert SEGMENT_DEFAULT_PERSONA in {p for p, _ in _PERSONA_RULES}
    assert _PERSONA_TO_SEAT[SEGMENT_DEFAULT_PERSONA] == "ceo"
    assert seat_of("Managing Partner") == "ceo"


def test_the_default_is_last_so_it_only_catches_what_named_seats_did_not():
    """Order is load-bearing throughout this vocabulary. A default evaluated early would
    swallow the seats it exists to fall back from."""
    personas = [p for p, _ in _PERSONA_RULES]
    assert personas[-1] == SEGMENT_DEFAULT_PERSONA


def test_the_default_does_not_claim_functional_directors():
    """The first attempt listed a bare `director` cue and swallowed every functional
    director in the pool — the same over-reach as the bug, from the other direction."""
    for title in ("Sales Director", "Director of Talent Acquisition", "Creative Director"):
        assert persona_of(title) != SEGMENT_DEFAULT_PERSONA, f"{title!r} is not an owner-operator"


# ------------------------------------------------------------------ non-buyers


def test_a_non_buyer_is_recognised_as_such_not_merely_unrecognised():
    """ "We cannot see this seat" and "we can see it and it is not a buyer" look identical
    in a None bucket and lead to opposite actions. `lint_persona_lead` cannot fire on a
    title it does not recognise, which is why nothing was checking these."""
    assert non_buyer_of("Administrative Assistant") == "administrative assistant"
    assert non_buyer_of("Executive Assistant to the CEO") == "executive assistant"
    assert non_buyer_of("Technical Recruiter") == "recruiter"
    # Not a non-buyer: unknown, and it must stay unknown.
    assert non_buyer_of("Operations Manager") is None
    assert non_buyer_of("CTO") is None


def test_non_buyer_cues_are_word_bounded_too():
    """`intern` inside `internal`, `international`, `internet` — the same trap, in the
    newest list in the file."""
    assert non_buyer_of("Head of Internal Audit") is None
    assert non_buyer_of("International Sales Lead") is None
    assert non_buyer_of("Intern") == "intern"


def test_the_non_buyer_list_stays_small_and_unambiguous():
    """Under-claiming costs one wasted send; over-claiming deletes a real account. Any
    growth here should be a deliberate decision, not a drift."""
    assert len(_NON_BUYER_CUES) <= 12, (
        "the non-buyer list is growing — each entry silently disqualifies every account "
        "whose only contact holds that title"
    )


# ------------------------------------------------------------------ the compound title


#: (title, persona). Every row is a real shape from the 1,145-title `content/` corpus. The
#: first two are the bug; the rest are what the fix must NOT break — each one is a case some
#: earlier decision in this module exists to get right.
_COMPOUND_TITLES = (
    # The bug: `ceo` is evaluated last, so a lower functional cue in a trailing segment won
    # the whole title and the exec was seated as an engineer.
    ("Founder, chief executive officer, head of ai innovations", "ceo"),
    ("Chief Operating Officer / Chief Compliance Officer", "ceo"),
    ("Chief Operating Officer and Chief AI Officer", "ceo"),
    ("Chief Technology and Strategy Officer, Chief Operating Officer", "ceo"),
    # Seated by its own parenthetical before the fix.
    ("Chief Executive Officer (former CTO, promoted 2026-05-20)", "ceo"),
    # OWNERSHIP is not a seat. 33 pooled rows are this shape and their `cto` seat is right:
    # "founder" says who owns the company, not which copy they are owed.
    ("Co-Founder & CTO", "cto"),
    ("Co-founder / CTO", "cto"),
    ("Founder, CTO", "cto"),
    ("Co-Founder & Chief Product Officer", "cpo"),
    # RANK is not a seat either — a functional chief can also hold the band.
    ("Group CISO Managing Director", "ciso"),
    ("Senior Managing Director, Chief Information Officer", "cloud-architect"),
    # The ordering rules the file already documents, unchanged by the fix.
    ("Senior Vice President, Chief Technology Officer", "cto"),
    ("Executive Vice President, Engineering", None),
    ("Data / Compliance Leader", "data-compliance"),
    ("CRO / Compliance", "compliance"),
    ("Director of Information Security", "ciso"),
    ("Director of Talent Acquisition", None),
    ("Chief Information Security Officer", "ciso"),
    ("Chief Executive Officer", "ceo"),
    ("Managing director", "ceo"),
)


@pytest.mark.parametrize("title,persona", _COMPOUND_TITLES)
def test_a_compound_title_resolves_to_its_senior_functional_seat(title, persona):
    """An exec who also holds a functional title is an exec.

    `_PERSONA_RULES` puts `ceo` LAST so "SVP, Chief Technology Officer" is a CTO — correct,
    and load-bearing. But last also meant a trailing `head of ai` beat a leading `chief
    executive officer`, so the exec spec was convicted by `seat-stakes-missing` on its two
    best rows and the tempting fix was to DROP them from the list. Founders and CEOs at
    smaller companies very often carry compound titles: this demotes exactly the buyers
    worth reaching, and it reads as a copy defect.
    """
    assert persona_of(title) == persona


def test_only_the_exec_TITLE_cues_are_promoted_not_rank_or_ownership():
    """The fix is a claim about which cues name the JOB. Promoting "managing director" or
    "president" would trade one mis-seat for a wider one, and promoting "founder" would
    move 33 technical co-founders out of the seat whose copy they should get."""
    assert set(_CEO_TITLE_CUES).isdisjoint(
        {"founder", "co-founder", "cofounder", "owner", "managing director", "president"}
    )
    # Every promoted cue must still be a cue the `ceo` persona itself claims, or the two
    # lists have drifted and a title can resolve to `ceo` that `_PERSONA_RULES` disowns.
    ceo_cues = next(cues for persona, cues in _PERSONA_RULES if persona == "ceo")
    assert set(_CEO_TITLE_CUES) <= set(ceo_cues)
