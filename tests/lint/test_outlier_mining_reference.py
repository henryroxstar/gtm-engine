"""C9 — the outlier-mining reference: its prohibition, and its de-branded standing.

A reference that supplies a STRUCTURE derived from other people's work has exactly one line it
must not cross, and the line has to survive an edit by someone who did not write the file. So the
prohibition is asserted here rather than trusted to review — along with the two properties that
make the reference safe to ship in the public carve: it names no real creator or channel, and it
keeps the ratio-not-reach selection rule that is the whole reason the method is evidence rather
than taste.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REF = REPO / "plugin" / "skills" / "content-plan" / "references" / "outlier-mining.md"
BODY = REPO / "plugin" / "skills" / "content-plan" / "body_template.md"


@pytest.fixture(scope="module")
def text() -> str:
    return REF.read_text(encoding="utf-8")


def test_the_reference_exists_and_is_cited_by_its_skill():
    """An uncited reference is never loaded and reads as enforced anyway."""
    assert REF.is_file()
    assert "outlier-mining.md" in BODY.read_text(encoding="utf-8")


def test_the_content_lifting_prohibition_is_present_and_explicit(text: str):
    """The one line this method must not cross, stated in words a reader cannot miss."""
    assert "Lift the structure. Never lift the content." in text, (
        "the prohibition was softened or removed — it is the load-bearing sentence of this file"
    )


def test_the_prohibition_carries_the_reason_that_actually_bites(text: str):
    """ "It is someone else's" is sufficient but not persuasive to someone in a hurry. The second
    reason — that borrowed content makes the structure stop working — is what survives pressure."""
    lowered = text.lower()
    assert "derivative" in lowered or "does not function" in lowered, (
        "the prohibition states only the ethical reason; the practical one is what holds under "
        "deadline"
    )


def test_the_selection_rule_is_ratio_and_not_absolute_reach(text: str):
    """Sorting by views measures audience size, which is already known and cannot be copied."""
    lowered = text.lower()
    assert "ratio" in lowered
    assert "never by absolute reach" in lowered or "not by absolute reach" in lowered


def test_the_output_is_one_named_structure(text: str):
    """A structure that cannot be named in a sentence is a preference wearing a rubric's clothes,
    and — concretely — it cannot be recorded as the brief's `outlier_structure` decision."""
    assert "outlier_structure" in text, "the reference does not say where its output goes"
    assert "named" in text.lower()


def test_the_reference_names_no_real_creator_channel_or_company(text: str):
    """De-branded, like its two sibling rubrics: it ships in the public carve.

    The lint suite's own PII and de-brand gates cover the mechanical half; this asserts the
    property this file is most at risk of losing, since the whole method is about reading other
    people's work.
    """
    assert "Company-neutral" in text, "the neutrality banner its siblings carry is missing"
    assert "@" not in text, "an @handle reached a company-neutral reference"

    # Asserted by SHAPE rather than by naming platforms — writing "<platform>.com/" literally here
    # would put a bare domain in the source surface, which is the very thing `pii_check` refuses
    # (it caught this test's first draft). A link to a specific piece of work is a link whatever
    # the host is, so matching "a domain followed by a path" covers more and names nothing.
    linked = re.findall(r"\b[\w-]+\.(?:com|io|net|tv|co)/\S", text)
    assert not linked, f"the reference links to specific external work: {linked}"
    for scheme in ("http://", "https://"):
        assert scheme not in text, f"{scheme!r} points at a specific external source"


def test_it_declares_how_strong_its_own_claim_is(text: str):
    """A structure from three pieces in a fortnight and one from five across a quarter are
    different strengths of claim, and the difference vanishes once only the sentence survives."""
    lowered = text.lower()
    assert "how many outliers" in lowered and "window" in lowered


def test_it_warns_that_an_outlier_can_beat_its_baseline_for_a_reason_that_does_not_transfer(
    text: str,
):
    """The trap that feels most like rigour: crediting the structure with someone else's traffic."""
    assert "do not transfer" in text.lower()
