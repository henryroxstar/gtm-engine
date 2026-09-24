"""A `[[seat]]` carries the outbound-copy facts, and refuses the ones it cannot trust.

Task 1.2 of the outbound fact registry. The registry's slot 3 is "the seat's own lead
pain"; until now that fact lived in prose tables scattered across five knowledge files,
each free to drift from the others. Moving it onto the seat block makes it *one* fact with
*one* home — but only if the block refuses the shapes that would make it silently wrong.

**Every refusal here carries its negative control** (``docs/RULES.md`` §R18). A test that
feeds a parser garbage and asserts it raises passes just as happily against a parser that
raises on everything, so each case below is a PAIR: a document that must be refused, and a
sibling differing only in the field under test that must load.

Fixtures are abstract (``owner`` / ``operator``, ``exec`` / ``ops``) and name no company,
person, email or domain — there is nothing here for §R9 to be about. Real seats and real
pains live only under the tenant's own tree, and the tenant-data contract that reads it
lives in ``tests/contracts/`` — a fictional-fixture file and a tenant-data file are
different things, and keeping them apart is what lets this one stay carve-clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.role_vocabulary import (
    DEFAULT_VOCABULARY,
    SEAT_REGISTERS,
    VocabularyError,
    parse,
)

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "profiles"

#: The minimal document every case below starts from. It declares the copy fields on one
#: seat and omits them entirely on the other, so "present" and "absent" are both exercised
#: by the same fixture rather than by two that could drift apart.
GOOD: dict = {
    "default_persona": "owner",
    "segments": ["enterprise", "startup", "unspecified"],
    "persona": [
        {"name": "owner", "cues": ["owner", "proprietor"]},
        {"name": "operator", "cues": ["operator", "manager"]},
    ],
    "seat": [
        {
            "name": "exec",
            "personas": ["owner"],
            "stakes": ["margin"],
            "lead_pain": "the board asks for a number nobody can produce",
            "gain": "one number, produced the same way every quarter",
            "forbidden_pains": ["queue depth", "on-call pages"],
            "register": "executive",
            "segments": ["enterprise"],
        },
        {"name": "ops", "personas": ["operator"], "stakes": ["throughput"]},
    ],
}


def _doc(**overrides) -> dict:
    """GOOD with fields replaced — the control and the case share everything else."""
    out = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in GOOD.items()}
    out.update(overrides)
    return out


def _seats(**exec_overrides) -> list[dict]:
    """The GOOD seat list with the ``exec`` block's copy fields patched.

    Returned as a fresh list every call: the case and its control must not share a mutable
    block, or the second parse would see the first one's edit.
    """
    first = dict(GOOD["seat"][0])
    first.update(exec_overrides)
    return [first, dict(GOOD["seat"][1])]


# --- the control ----------------------------------------------------------------------


def test_the_baseline_document_parses() -> None:
    """If this fails, every "must raise" test below stops meaning anything."""
    vocab = parse(GOOD, "test")
    assert vocab.seats == ("exec", "ops")


# --- lead_pain / gain / register / segments, parsed ------------------------------------


def test_seat_lead_pain_is_parsed() -> None:
    """A declared lead pain is readable; an undeclared one is "" and never a guess.

    The empty string matters as much as the value. A seat with no lead pain is a seat the
    registry cannot write slot 3 for, and the caller has to be able to see that — a
    fabricated or inherited default would make an unwritten fact look written.
    """
    vocab = parse(GOOD, "test")
    assert vocab.lead_pain_for("exec") == "the board asks for a number nobody can produce"
    assert vocab.lead_pain_for("ops") == ""
    assert vocab.lead_pain_for("no-such-seat") == ""
    assert vocab.gain_for("exec") == "one number, produced the same way every quarter"
    assert vocab.gain_for("ops") == ""
    assert vocab.register_for("exec") == "executive"
    assert vocab.register_for("ops") == ""
    assert vocab.forbidden_pains_for("exec") == ("queue depth", "on-call pages")
    assert vocab.forbidden_pains_for("ops") == ()
    assert vocab.segments_for("exec") == ("enterprise",)
    assert vocab.segments_for("ops") == ()

    # NEGATIVE CONTROL for the whole change: the pre-existing stakes axis is untouched on a
    # seat that carries ONLY stakes. If adding five fields had shifted the tuple the seat
    # rules are built from, this is where it would show.
    assert vocab.stakes_for("ops") == ("throughput",)
    assert vocab.stakes_for("exec") == ("margin",)
    assert vocab.seat_rules == (
        ("exec", ("owner",), ("margin",)),
        ("ops", ("operator",), ("throughput",)),
    )


def test_the_shipped_default_carries_no_seat_copy_facts() -> None:
    """The no-op property: a tenant that ships no file gets exactly today's behaviour.

    These fields are tenant COPY, not a generic B2B vocabulary. A shipped default lead pain
    would be put in a stranger's mouth by every profile that never opted in — the precise
    failure ``defaults.py`` exists to prevent one layer down.
    """
    for seat in DEFAULT_VOCABULARY.seats:
        assert DEFAULT_VOCABULARY.lead_pain_for(seat) == ""
        assert DEFAULT_VOCABULARY.gain_for(seat) == ""
        assert DEFAULT_VOCABULARY.register_for(seat) == ""
        assert DEFAULT_VOCABULARY.forbidden_pains_for(seat) == ()
        assert DEFAULT_VOCABULARY.segments_for(seat) == ()


def test_to_dict_carries_the_new_fields() -> None:
    """``--json`` is how an operator reads the resolved vocabulary; a field it omits is a
    field nobody can check without reading Python."""
    exported = parse(GOOD, "test").to_dict()
    block = next(s for s in exported["seat"] if s["name"] == "exec")
    assert block["lead_pain"] == "the board asks for a number nobody can produce"
    assert block["gain"] == "one number, produced the same way every quarter"
    assert block["forbidden_pains"] == ["queue depth", "on-call pages"]
    assert block["register"] == "executive"
    assert block["segments"] == ["enterprise"]
    # control: the seat that declares none exports the empty shapes, not a missing key
    bare = next(s for s in exported["seat"] if s["name"] == "ops")
    assert bare["lead_pain"] == "" and bare["forbidden_pains"] == []


# --- refusals, each with its near-miss control ------------------------------------------


def test_forbidden_pains_must_be_a_list_of_strings() -> None:
    """A scalar here is the dangerous shape, not merely the wrong one: a linter iterating a
    string would forbid every single CHARACTER of it, and match on all of them."""
    with pytest.raises(VocabularyError, match="exec"):
        parse(_doc(seat=_seats(forbidden_pains="queue depth")), "test")
    # control: the same document with the value in a list
    assert parse(_doc(seat=_seats(forbidden_pains=["queue depth"])), "test").forbidden_pains_for(
        "exec"
    ) == ("queue depth",)


def test_lead_pain_must_be_a_string() -> None:
    with pytest.raises(VocabularyError, match="lead_pain"):
        parse(_doc(seat=_seats(lead_pain=["a", "b"])), "test")
    assert (
        parse(_doc(seat=_seats(lead_pain="one pain")), "test").lead_pain_for("exec") == "one pain"
    )


def test_register_outside_closed_set_refuses() -> None:
    """The register selects a whole surface of copy. An unrecognised one cannot be rendered,
    and the only alternatives to refusing are to fall back silently to a register the
    tenant did not ask for, or to render nothing — both invisible at send time."""
    with pytest.raises(VocabularyError, match="exec"):
        parse(_doc(seat=_seats(register="casual")), "test")
    # control: the same document with a member of the closed set
    assert parse(_doc(seat=_seats(register="standard")), "test").register_for("exec") == "standard"
    assert SEAT_REGISTERS == frozenset({"standard", "technical", "executive"})


def test_seat_segments_must_exist() -> None:
    """A seat scoped to a segment the file does not declare is copy aimed at nobody: the
    run's segment mix can never select it, so the seat reads as covered and is not."""
    with pytest.raises(VocabularyError, match="mid-market"):
        parse(_doc(seat=_seats(segments=["mid-market"])), "test")
    # control: a segment the file DOES declare
    assert parse(_doc(seat=_seats(segments=["startup"])), "test").segments_for("exec") == (
        "startup",
    )


def test_a_seat_scoped_to_the_unclassified_bucket_refuses() -> None:
    """``unspecified`` is the kept value for a row nobody classified — never a run mix a
    seat can be aimed at. Scoping copy to it would make the unclassified bucket look like a
    targeted segment."""
    with pytest.raises(VocabularyError, match="unspecified"):
        parse(_doc(seat=_seats(segments=["unspecified"])), "test")
    assert parse(_doc(seat=_seats(segments=["enterprise"])), "test").segments_for("exec") == (
        "enterprise",
    )
