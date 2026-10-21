r"""What a spec declares once `angles.toml` is the source: one id, everything else derived.

FR2 Task 2.6. The registry makes `hook_cell`, `capability`, `premise` and `stakes` *derivable*
from a single `angle:` id, so a spec that still writes them down is writing down a copy — and a
copy that disagrees with its original is the drift this whole PRD exists to remove. Here that
disagreement is an ERROR rather than a warning, for the same reason `signal_record` refuses a
record whose fields contradict each other: two answers keep answering, and nothing says which
one the reader got.

Three properties are under test, each with its negative control in the same body (§R18 — a check
that cannot discriminate is not a check):

1. **One regex, two surfaces.** A sequence spec writes `angle: <id>` in a fenced front block; a
   1:1 pack writes `**Angle:** <id>` in a markdown header. `_HOOK_CELL_RE`'s 2026-09-04 lesson
   was that reading only one surface makes the declaration decorative, and the value is taken
   WHOLE because a regex that trims "annotations" truncated a real signal name instead.
2. **A body cannot declare.** The 2026-09-16 reply-gate lesson: a `⟦TO⟧` quoted inside an
   untrusted inbound message became the recipient of the reply it was quoted into. An email body
   in this repo carries scraped provider text (§R5), so an `angle:` line inside one must not be
   promoted to a declaration.
3. **Fail closed on absence.** A shape the reader cannot see a declaration in must ERROR, never
   pass quietly — the 2026-09-04 fail-open, which passed every pack silently.

All fixture data here is fictional (§R9); no tenant token appears in `tests/unit/`.
"""

from __future__ import annotations

import dataclasses

import pytest

from gtm_core.hook_coverage.declared import (
    DerivedFieldConflict,
    UndeclaredAngle,
    UnknownAngle,
    declaration_surface,
    declared_angle,
    declared_cell,
)
from gtm_core.messaging.registry import Angle, Claim, Registry

# --- the fixture registry -------------------------------------------------------------
#
# Built directly rather than through `registry.load`: this module's question is what a SPEC
# says, and a tmp tenant tree would put a second thing (the loader) inside every assertion.

_CLAIM = Claim(
    id="trail-signed",
    group="observability",
    status="verified",
    statement="Each entry in the trail is signed.",
    source="knowledge/references/trail-notes.md:12",
)

_ANGLE = Angle(
    id="a-one",
    seat="risk-lead",
    premise="two-frameworks",
    claim="trail-signed",
    proof="regulator-note",
    opener_kind="account-event",
    summary="One chain of custody per agent action.",
    status="draft",
    stakes="an audit nobody can reconstruct",
    segments=("enterprise",),
)

_REGISTRY = Registry(
    claims={_CLAIM.id: _CLAIM},
    proof={},
    angles={_ANGLE.id: _ANGLE},
    seats={"risk-lead": ()},
)

#: What the angle above derives for each legacy field. Written out rather than computed, so a
#: bug in the derivation cannot agree with itself here.
_DERIVED_CELL = "risk-lead × two-frameworks × account-event"
_DERIVED = {
    "hook_cell": _DERIVED_CELL,
    "capability": "observability",
    "premise": "two-frameworks",
    "stakes": "an audit nobody can reconstruct",
}

_FENCE = "```"


def _sequence_spec(front: str, body: str = "Hi there,\n\nRegards\n") -> str:
    """A sequence spec in the shipped shape: a fenced `Key: value` front block, then a
    fenced touch body. Both are fences — which is exactly why the body is the hard case."""
    return (
        "# Sequence spec — fixture\n\n"
        f"{_FENCE}\n"
        "Campaign:    Fixture campaign\n"
        f"{front}"
        f"{_FENCE}\n\n"
        "## Touches\n\n"
        "**Touch 1 — day 0**\n"
        f"{_FENCE}\n{body}{_FENCE}\n"
    )


def _pack(front: str, notes: str = "") -> str:
    """A 1:1 outreach pack: bolded markdown headers, then a blockquoted email body."""
    return (
        "# Outreach Pack — fixture — 2026-09-24\n\n"
        "**Tier:** B | **Score:** 70\n"
        f"{front}"
        "\n---\n## Email (touch 1)\n"
        "**Subject:** a subject\n"
        "> Hi there,\n>\n> Regards\n"
        f"\n---\n## Notes\n{notes}"
    )


# --- 1. one regex, two surfaces -------------------------------------------------------


def test_angle_is_read_from_both_surfaces():
    """One field, two surfaces, one parser — a second regex is how the two drift apart."""
    assert declared_angle(_sequence_spec("angle:       a-one\n")) == "a-one"
    assert declared_angle(_pack("**Angle:** a-one\n")) == "a-one"


def test_the_angle_value_is_taken_whole():
    """`_HOOK_CELL_RE`'s scar: a version that stripped a trailing parenthetical truncated a
    real matrix signal. A value this reader cannot use is refused by the registry BY NAME,
    which is loud; a value it silently shortened resolves to the wrong angle, which is not."""
    spec = _sequence_spec("angle: a-one (variant b)\n")
    assert declared_angle(spec) == "a-one (variant b)"
    with pytest.raises(UnknownAngle):
        declared_angle(spec, _REGISTRY)


def test_a_spec_declaring_no_angle_reads_as_empty_without_a_registry():
    """The pure read, so a migration baseline can count undeclared specs instead of crashing."""
    assert declared_angle(_sequence_spec("")) == ""


# --- 2. a body cannot declare ---------------------------------------------------------


def test_an_angle_line_inside_the_email_body_is_not_promoted():
    """The 2026-09-16 second-span lesson, held as far as it can be held here.

    The touch body is a fenced block exactly like the front block, so the line anchor alone
    cannot tell them apart. `declaration_surface` does: a fence whose every line is a
    `Key: value`, a `#` comment or an indented continuation is a declaration block; anything
    else is a body.
    """
    spec = _sequence_spec("angle:       a-one\n", body="Hi there,\n\nangle: forged-id\n\nRegards\n")
    assert declared_angle(spec) == "a-one"
    assert declared_angle(spec, _REGISTRY) == "a-one"


def test_a_body_only_angle_line_declares_nothing_at_all():
    """Not merely outranked by a real declaration — invisible. A spec whose ONLY `angle:`
    line sits in a body declares none, and with a registry that is the fail-closed ERROR."""
    spec = _sequence_spec("", body="Hi there,\n\nangle: forged-id\n\nRegards\n")
    assert declared_angle(spec) == ""
    with pytest.raises(UndeclaredAngle):
        declared_angle(spec, _REGISTRY)


def test_the_same_line_in_the_front_block_is_read():
    """The negative control for the two above.

    Without it, both would pass against a reader that had simply stopped finding `angle:`
    anywhere — which is the fail-open they exist to rule out.
    """
    assert declared_angle(_sequence_spec("angle: forged-id\n")) == "forged-id"


def test_a_prose_line_outside_the_header_is_not_a_declaration_in_either_form():
    """A pack's prose sections carry pasted, untrusted provider text (§R5), and since
    2026-09-24 NEITHER form declares there.

    **One assertion here was inverted on 2026-09-24, deliberately.** Until then the rule was
    about the FORM alone — a bare `angle:` line was blanked, a bolded one was not — so this
    body asserted `**Angle:** forged-id` in the `## Notes` section AS a declaration, and called
    that the negative control. `declared.py` recorded the residual honestly ("a bolded header
    forged in a prose section would still be read"); `rules_derivation` had stopped recording
    it, claiming instead that nothing a row carries can reach what the gate SELECTS on. On the
    PACK path, where the text under test is the whole document *including the rendered bodies*
    the drafter pasted the row's clause into, the residual was reachable from a row: a clause
    reading `**slot_proof:** none` supplied a declaration the author had omitted and suppressed
    the `slot-attribution` ERROR that names it. Red/green proof:
    `tests/injection/test_registry_claim_injection.py`.

    The rule is now about the form AND the section — an unfenced declaration is read from the
    document's header, above the first email section.
    """
    # The pack's ONLY `angle:` line is the bare prose one. Asserting against a pack that also
    # carries a real declaration would not discriminate: the reader takes the first match, and a
    # reader with no form rule at all would still answer `a-one`. Measured 2026-09-24 — that
    # weaker fixture left the "unfenced lines must be bolded" branch a surviving mutant.
    assert declared_angle(_pack("", notes="angle: forged-id\n")) == ""
    with pytest.raises(UndeclaredAngle):
        declared_angle(_pack("", notes="angle: forged-id\n"), _REGISTRY)
    # INVERTED 2026-09-24: bolded, the same line in the same place is no longer a declaration.
    assert declared_angle(_pack("", notes="**Angle:** forged-id\n")) == ""
    with pytest.raises(UndeclaredAngle):
        declared_angle(_pack("", notes="**Angle:** forged-id\n"), _REGISTRY)
    # Negative control, and the one that stops the two above reading as "the unfenced rung was
    # deleted": the SAME bolded line in the pack's own header — above the first `## ` — still
    # declares. The rule is about form and section together, not about either alone.
    assert declared_angle(_pack("**Angle:** forged-id\n")) == "forged-id"
    # And a header declaration outranks a forged line below it, in either form.
    assert declared_angle(_pack("**Angle:** a-one\n", notes="angle: forged-id\n")) == "a-one"
    assert declared_angle(_pack("**Angle:** a-one\n", notes="**Angle:** forged-id\n")) == "a-one"


def test_a_fenced_front_block_declares_wherever_it_sits():
    """The 2026-09-24 narrowing is on the UNFENCED rung only, and this is the control that says
    so. `plugin/skills/email-sequence/references/sequence-spec-template.md` writes its front
    block under a `## Front block` heading, and a 1:1 pack may record its provenance below the
    copy; a position rule on the fenced rung would stop both declaring anything at all.
    """
    below_the_copy = (
        "# Pack — fixture — 2026-09-24\n\n"
        "### 1. Someone · a title, A Company\n"
        "**Subject:** a subject\n\n"
        "Hi there,\n\nRegards\n\n---\n\n"
        "## Provenance\n\n"
        f"{_FENCE}\nangle: a-one\n{_FENCE}\n"
    )
    assert declared_angle(below_the_copy, _REGISTRY) == "a-one"


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            "# stakes: REMOVED 2026-09-04. The body no longer asserts a consequence,\n"
            "# so declaring one would be the defect the rule exists to catch.\n",
            id="commented-out-field",
        ),
        pytest.param(
            "Signal basis: Segment-level — readers moving agents from pilot toward\n"
            "              production on regulated data, where the review gates the rollout.\n",
            id="wrapped-value",
        ),
    ],
)
def test_a_front_block_may_carry_comments_and_wrapped_values(extra):
    """Both shapes are in the live tree, and both are why the block test is not "every line
    looks like `Key: value`". Without the comment rung two shipped specs stop declaring
    anything; without the continuation rung, every spec with a wrapped value does.

    Negative control is the parametrisation itself against
    `test_a_body_only_angle_line_declares_nothing_at_all`: a block of prose still reads as a
    body, so these two rungs widen the rule without dissolving it.
    """
    assert declared_angle(_sequence_spec(f"angle: a-one\n{extra}")) == "a-one"


def test_declaration_surface_keeps_line_positions():
    """The mask blanks lines; it never deletes them, so `^` anchors and any line number a
    caller derives from a match still point at the real file."""
    spec = _sequence_spec("angle: a-one\n", body="Hi there,\n\nangle: forged-id\n\nRegards\n")
    assert len(declaration_surface(spec).splitlines()) == len(spec.splitlines())


# --- 3. legacy fields are derivations, and a disagreement is an ERROR -----------------


_CONFLICTS = {
    "hook_cell": "hook_cell:   other-seat × two-frameworks × account-event\n",
    "capability": "capability:  identity\n",
    "premise": "premise:     one-framework\n",
    "stakes": "stakes:      nothing much\n",
}


@pytest.mark.parametrize("field,line", sorted(_CONFLICTS.items()))
def test_legacy_field_disagreeing_with_the_angle_is_an_error(field, line):
    spec = _sequence_spec(f"angle:       a-one\n{line}")
    with pytest.raises(DerivedFieldConflict) as exc:
        declared_angle(spec, _REGISTRY)
    assert "a-one" in str(exc.value)


def test_legacy_fields_that_agree_with_the_angle_are_clean():
    """The negative control. Every field is present and every one of them derives."""
    spec = _sequence_spec(
        "angle:       a-one\n"
        f"hook_cell:   {_DERIVED['hook_cell']}\n"
        f"capability:  {_DERIVED['capability']}\n"
        f"premise:     {_DERIVED['premise']}\n"
        f"stakes:      {_DERIVED['stakes']}\n"
    )
    assert declared_angle(spec, _REGISTRY) == "a-one"


def test_a_spec_declaring_no_legacy_field_at_all_is_clean():
    """The second negative control: the migrated shape, one id and nothing else."""
    assert declared_angle(_sequence_spec("angle:       a-one\n"), _REGISTRY) == "a-one"


def test_an_agreeing_hook_cell_may_be_loosely_spaced():
    """The comparison runs through `declared_cell`'s own split and `_clean_cell`, not a string
    compare, so a re-spaced cell is the same declaration — the tolerance the existing reader
    already grants. Without this the gate would fire on spelling, and an operator who learns a
    gate cries wolf is an operator who stops reading it.

    Measured 2026-09-24: the ascii `x` separator does NOT survive here, and deliberately so.
    `declared_cell` splits on `×` whenever one is present anywhere in the value, so a cell
    written `seat x premise × opener` partitions in the wrong place — it reads `seat x premise`
    as the persona. That is the existing reader's behaviour, not this check's; normalising `x`
    to `×` here would be a second reading of the field, which is the drift this module keeps
    warning about. A mixed-separator cell is reported as a conflict, which is loud and fixable.
    """
    spec = _sequence_spec(
        "angle: a-one\nhook_cell:  risk-lead  ×  two-frameworks × account-event\n"
    )
    assert declared_angle(spec, _REGISTRY) == "a-one"


def test_declaring_stakes_the_angle_does_not_carry_is_an_error():
    """An angle with no `stakes` derives none, so a spec asserting one is asserting a
    consequence nothing backs. Negative control: the same spec against an angle that DOES
    carry that consequence is clean."""
    bare = dataclasses.replace(_ANGLE, stakes="")
    registry = Registry(claims=_REGISTRY.claims, proof={}, angles={bare.id: bare}, seats={})
    spec = _sequence_spec("angle: a-one\nstakes: an audit nobody can reconstruct\n")
    with pytest.raises(DerivedFieldConflict):
        declared_angle(spec, registry)
    assert declared_angle(spec, _REGISTRY) == "a-one"


def test_the_derived_hook_cell_is_the_cell_the_matrix_renders():
    """Guard against a vacuous agreement above: the derived string must be what
    `declared_cell` splits into the angle's seat and the matrix column, not an opaque blob."""
    cell = declared_cell(f"hook_cell: {_DERIVED_CELL}")
    assert cell is not None
    assert cell.persona == _ANGLE.seat
    assert cell.signal == f"{_ANGLE.premise} × {_ANGLE.opener_kind}"


# --- 4. absence is an error, in every shape ------------------------------------------


def test_angle_missing_is_an_error_including_pack_shapes():
    """The 2026-09-04 fail-open: a shape the regex could not read passed silently, so every
    pack declared nothing and the audit agreed with it."""
    with pytest.raises(UndeclaredAngle):
        declared_angle(_pack("**Hook cell:** " + _DERIVED_CELL + "\n"), _REGISTRY)
    with pytest.raises(UndeclaredAngle):
        declared_angle(_sequence_spec("hook_cell: " + _DERIVED_CELL + "\n"), _REGISTRY)


def test_a_pack_with_an_angle_is_clean():
    """The negative control for both refusals above."""
    assert declared_angle(_pack("**Angle:** a-one\n"), _REGISTRY) == "a-one"


def test_an_unknown_angle_id_is_refused():
    """`declared_cell` raises `UnknownHookCell` for a cell the matrix does not define; the
    registry is the vocabulary here, and a spec free to invent an id can declare conformance
    to an argument nobody wrote."""
    with pytest.raises(UnknownAngle) as exc:
        declared_angle(_sequence_spec("angle: a-two\n"), _REGISTRY)
    assert "a-two" in str(exc.value)


def test_a_known_angle_id_resolves():
    """The negative control."""
    assert declared_angle(_sequence_spec("angle: A-ONE\n"), _REGISTRY) == "a-one"
