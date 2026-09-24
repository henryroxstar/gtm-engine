"""Contract: every SHIPPED template that declares an ``angle:`` satisfies the linter reading it.

A template is the one artifact whose defects are copied rather than fixed. On 2026-09-24 FR2 put
``angle:`` into ``plugin/skills/email-sequence/references/sequence-spec-template.md`` and FR3 added
``slot-attribution``, which raises one ERROR per missing ``slot_<id>:`` the moment an angle
resolves — so a spec written *exactly* to the shipped template failed the gate the same skill runs,
five times over, before an author had typed a word. The linter's own control
(``outreach.cli._GOOD_SPEC``) carried all five slots; the shipped template did not, and nothing
compared the two.

**Why this file is parameterised over discovery rather than over one path.** That fix was applied
to the sequence template alone, and this file named it alone — so the identical defect sat
untouched in ``draft-outreach/body_template.md`` (the front block it tells a drafter to emit) and
in ``prospect/references/output-templates.md`` (the Tier-A pack header), both of which had just
been given ``--profile``, which is exactly what turns these rules on. A test that names the one
template it was written for cannot see the next one. So the specimens are **discovered**: every
``plugin/skills/**/*.md``, and every document displayed inside one of those files, that declares an
``angle:`` on the declaration surface is a specimen, and the next template to declare one is
covered the day it lands.

**Where the discrimination lives, now that the fixture is bound mechanically.** The earlier version
hand-wrote the template's four placeholder strings so the cross-check half of ``slot-attribution``
(claim / pain / proof must equal what the angle derives) could not pass vacuously. A discovered
specimen has no such table to write, so the three derivable slots are rewritten to the invented
angle's own ids before the positive control runs — and the discrimination moves to three direct
controls that need no table at all: deleting any one slot line must be caught
(:func:`test_deleting_any_one_slot_line_from_a_template_is_caught`), pointing a slot at an id the
angle does not derive must be caught
(:func:`test_a_slot_pointing_at_the_wrong_id_is_caught_not_merely_counted`), and the three
derivable slots must ship as ``<placeholder>`` text rather than as a literal id
(:func:`test_the_three_derivable_slots_ship_as_placeholders_not_literals`), which is what the
rewrite would otherwise be free to mask.

Fixtures are invented (docs/RULES.md R9): the angle below is built in memory and no tenant file is
read.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "plugin" / "skills"

sys.path.insert(0, str(REPO / "tests" / "linter"))

from outreach.rules_derivation import SLOT_IDS, lint_slot_attribution  # noqa: E402

#: The three templates this contract was written against, as a FLOOR on discovery and never as its
#: source. Parameterising over an empty list is a green run, so a discovery that silently stopped
#: matching — a renamed reference, a changed fence convention — would read exactly like a clean
#: pass. Naming them here is the §R18 anti-vacuity control; adding a fourth template does not
#: belong in this tuple, because discovery is what covers it.
_KNOWN_TEMPLATES = (
    "plugin/skills/email-sequence/references/sequence-spec-template.md",
    "plugin/skills/draft-outreach/body_template.md",
    "plugin/skills/prospect/references/output-templates.md",
)

#: A fence of FOUR-or-more backticks is this repo's convention for *displaying* a whole document
#: (``output-templates.md`` wraps each pack shape in one). The document inside is what an author
#: ends up with on disk, and it is the thing the linter will read — so it is a specimen in its own
#: right. Its own three-backtick fences belong to that document and are left alone.
_DISPLAY_OPEN_RE = re.compile(r"^(?P<fence>`{4,}|~{4,})")


def _slot_line_re(slot: str) -> re.Pattern[str]:
    """``slot_claim: audit-signed`` or ``**slot_claim:** audit-signed``.

    Written here rather than imported from the rule, so the two readers are independent: if the
    rule's own regex narrowed, this one would keep matching and the positive control below —
    which runs the real rule — is what would go red. One reader would hide that.
    """
    return re.compile(rf"^\**slot_{slot}\**:\**\s*(?P<value>.*?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Specimen:
    """One document a template hands an author, and where it came from."""

    path: Path
    where: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.path.relative_to(REPO)}::{self.where}"


def _displayed_documents(text: str):
    """Each document displayed inside ``text``, as ``(line number, document)``."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        opened = _DISPLAY_OPEN_RE.match(lines[i])
        if not opened:
            i += 1
            continue
        fence = opened.group("fence")
        closes = re.compile(rf"^{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        j = i + 1
        while j < len(lines) and not closes.match(lines[j]):
            j += 1
        yield i + 1, "\n".join(lines[i + 1 : j]) + "\n"
        i = j + 1


def _discover() -> list[Specimen]:
    """Every shipped template document that declares an ``angle:``.

    The predicate is ``declared_angle`` itself — the same read ``lint_angle`` performs — so a
    declaration written somewhere the surface blanks (an indented fence inside a list item, a
    prose section, a touch body) is not discovered, and the floor test below is what turns that
    into a failure instead of into a quiet absence.
    """
    from gtm_core.hook_coverage.declared import declared_angle

    found: list[Specimen] = []
    for path in sorted(SKILLS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        candidates = [("whole file", text)]
        candidates += [
            (f"displayed document at line {n}", doc) for n, doc in _displayed_documents(text)
        ]
        found += [Specimen(path, where, doc) for where, doc in candidates if declared_angle(doc)]
    return found


SPECIMENS = _discover()
_IDS = [s.id for s in SPECIMENS]

#: The invented angle every specimen is checked against. Its ids are deliberately unlike anything
#: in a tenant's registry: this file must never read `profiles/`, or the fixture becomes a second,
#: stale copy of the thing under test.
_CLAIM = "fixture-claim-id"
_SEAT = "fixture-seat-id"
_PROOF = "fixture-proof-id"


def _angle():
    from gtm_core.messaging.registry import Angle

    return Angle(
        id="fixture-angle-id",
        seat=_SEAT,
        premise="agents-in-path",
        claim=_CLAIM,
        proof=_PROOF,
        opener_kind="account-event",
        summary="Prove whose authority the agent carries.",
        status="live",
    )


#: The three slots the angle DERIVES, and what the invented angle derives for each.
_DERIVABLE = (("claim", _CLAIM), ("pain", _SEAT), ("proof", _PROOF))

#: The two that are presence-only by construction: ``slot_signal`` is the ROW's researched fact,
#: which varies per recipient, and ``slot_hedge`` is the tenant's own cue table. Neither is a
#: registry id, so neither is cross-checked — and neither may ship as a `<placeholder>` either,
#: because their source is fixed and an author has nothing to resolve.
_PRESENCE_ONLY = ("signal", "hedge")


def _bound_to_the_fixture(spec: str) -> str:
    """``spec`` with its three derivable slots rewritten to the invented angle's ids.

    A template writes those three as ``<claim-id the angle derives>`` — prose telling the author
    to take the value from the angle rather than to type one. Rewriting them is what lets one
    fixture check every template; it is safe only because
    :func:`test_the_three_derivable_slots_ship_as_placeholders_not_literals` proves there was a
    placeholder there and not a literal id this would have quietly corrected.
    """
    for slot, value in _DERIVABLE:
        spec = _slot_line_re(slot).sub(
            lambda m, v=value: f"{m.group(0)[: m.start('value') - m.start(0)]}{v}", spec
        )
    return spec


def test_discovery_covers_every_template_this_contract_was_written_against():
    """The floor. Without it every parameterised test below is vacuous on an empty list."""
    assert SPECIMENS, "discovered no shipped template that declares an `angle:`"
    covered = {str(s.path.relative_to(REPO)) for s in SPECIMENS}
    missing = [p for p in _KNOWN_TEMPLATES if p not in covered]
    assert not missing, (
        f"{missing} declares no `angle:` that `declared_angle` can read — either the declaration "
        f"is written somewhere `declaration_surface` blanks (an indented fence, a prose section, "
        f"a touch body), or it is gone. Discovered: {sorted(covered)}"
    )


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
@pytest.mark.parametrize("slot", SLOT_IDS)
def test_every_shipped_template_declares_every_body_slot(spec, slot):
    """Parameterised over ``SLOT_IDS``, never over five names typed out here, so a sixth slot
    added to the linter fails this immediately instead of shipping undeclared."""
    from gtm_core.hook_coverage.declared import declaration_surface

    assert _slot_line_re(slot).search(declaration_surface(spec.text)), (
        f"{spec.id} declares no `slot_{slot}:` on the declaration surface: every document written "
        f"to it fails `slot-attribution` with one ERROR per missing slot the moment its `angle:` "
        f"resolves"
    )


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
def test_every_shipped_template_passes_slot_attribution(spec):
    """The positive control: the template's own bytes, through the real rule."""
    violations = lint_slot_attribution(_bound_to_the_fixture(spec.text), _angle())
    assert not violations, [str(v) for v in violations]


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
@pytest.mark.parametrize("slot", SLOT_IDS)
def test_deleting_any_one_slot_line_from_a_template_is_caught(spec, slot):
    """§R18 negative control, one per slot per template. Without it,
    ``lint_slot_attribution`` returning ``[]`` whenever no angle resolved would read exactly like
    a clean pass — which is precisely how a template shipped a state its own linter refuses."""
    blinded = _slot_line_re(slot).sub("", _bound_to_the_fixture(spec.text))
    hits = [v for v in lint_slot_attribution(blinded, _angle()) if slot in v.detail]
    assert hits, f"removing `slot_{slot}:` from {spec.id} raised nothing"


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
def test_a_slot_pointing_at_the_wrong_id_is_caught_not_merely_counted(spec):
    """Presence alone would be a declaration nothing checks. The three derivable slots are
    cross-checked against the angle, so this proves the contract above is testing the values and
    not just the line count."""
    swapped = _slot_line_re("claim").sub(
        lambda m: f"{m.group(0)[: m.start('value') - m.start(0)]}some-other-claim",
        _bound_to_the_fixture(spec.text),
    )
    hits = [v for v in lint_slot_attribution(swapped, _angle()) if "some-other-claim" in v.detail]
    assert hits, f"{spec.id}: a slot citing an id the angle does not derive was accepted"


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
def test_the_three_derivable_slots_ship_as_placeholders_not_literals(spec):
    """A template's claim / pain / proof slot tells the author to take the value from the angle.
    A literal id there is a second declaration of a derived fact — the drift the registry exists
    to remove — and it is also what would make :func:`_bound_to_the_fixture` above a mask rather
    than a binding. ``none`` is the sanctioned no-anchor value and is accepted."""
    from gtm_core.hook_coverage.declared import declaration_surface

    surface = declaration_surface(spec.text)
    typed = {}
    for slot, _ in _DERIVABLE:
        m = _slot_line_re(slot).search(surface)
        value = m.group("value") if m else ""
        if value.lower() != "none" and not ("<" in value and ">" in value):
            typed[slot] = value
    assert not typed, (
        f"{spec.id} types a literal into {sorted(typed)} ({typed}) — the angle derives those "
        f"three, so a template writes a `<placeholder>` naming what to resolve, or `none` on the "
        f"no-anchor offer shape"
    )


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
@pytest.mark.parametrize("slot", _PRESENCE_ONLY)
def test_the_presence_only_slots_name_a_fixed_source(spec, slot):
    """The mirror of the test above, and what pins WHICH two slots are presence-only. The row's
    researched fact and the tenant's cue table are the same source for every spec ever written to
    this template, so a `<placeholder>` here would be asking an author to invent an answer the
    rule will then accept unchecked."""
    from gtm_core.hook_coverage.declared import declaration_surface

    m = _slot_line_re(slot).search(declaration_surface(spec.text))
    value = m.group("value") if m else ""
    assert value and "<" not in value, (
        f"{spec.id} writes `slot_{slot}: {value}` — this slot is presence-only because its source "
        f"is fixed, so the template names that source outright rather than leaving a placeholder"
    )


@pytest.mark.parametrize("spec", SPECIMENS, ids=_IDS)
def test_no_template_reintroduces_a_field_the_angle_derives(spec):
    """FR2 made ``hook_cell``, ``capability``, ``premise`` and ``stakes`` DERIVATIONS of the
    angle. A template that declares one as well gives one fact two declarations, which
    ``DerivedFieldConflict`` then refuses — so the template would once again ship a state its own
    loader rejects. Read with the loader's OWN regexes, over the raw document exactly as
    ``declared._conflicts`` reads them, so this cannot pass on a spelling the loader would."""
    from gtm_core.hook_coverage import declared

    readers = {
        "hook_cell": declared._HOOK_CELL_RE,
        "capability": declared._CAPABILITY_RE,
        "premise": declared._PREMISE_RE,
        "stakes": declared._STAKES_RE,
        "argument_id": declared._ARGUMENT_ID_RE,
    }
    found = {
        field: m.group(0).strip()
        for field, reader in readers.items()
        if (m := reader.search(spec.text))
    }
    assert not found, (
        f"{spec.id} still declares {sorted(found)} ({found}) — the angle derives all of these, "
        f"and a declared value that disagrees is `angle-conflict` (ERROR), not a second opinion"
    )
