"""Contract: every speaker in the collector's taxonomy has a home section in the brief template.

This test exists because of a real defect. `vendor-voice` was added to
`gtm_core/voc/collect.py` as a first-class speaker by the market-intelligence rescope
and tagged onto real sources — but
`references/brief-template.md` was never given a section to render it in. The result: a
whole speaker's material (competitor movement) was collected, correctly labelled, and then
had nowhere to go. It surfaced only as unread vendor-side rows in an appendix, and the gap
went unnoticed for weeks because no gate could see it.

`skill_codegen_sync.sh` cannot catch this: it proves SKILL.md matches its manifest, not that
the template covers the taxonomy. So the check lives here.

Contract:
adding a speaker to the code REQUIRES giving it a section in the template. `mixed` is
deliberately exempt — it is not a speaker but an instruction to split at read time.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path

import pytest

from gtm_core.voc import collect as voc

REPO = Path(__file__).resolve().parents[2]
BRIEF_TEMPLATE = (
    REPO / "plugin" / "skills" / "market-intelligence" / "references" / "brief-template.md"
)

# market-intelligence is `oss = "private"` (gtm_core/gating.toml) — the OSS carve stubs its
# references out, so the two BRIEF_TEMPLATE tests have nothing to check in that distribution.
_template_stubbed = pytest.mark.skipif(
    not BRIEF_TEMPLATE.exists(),
    reason="market-intelligence brief-template.md not present (paid-tier stub)",
)

# `mixed` holds two speakers at once and is split at read time per the source note, so it
# never owns a section of its own. Every other speaker must be renderable somewhere.
EXEMPT_SPEAKERS = frozenset({voc.MIXED})


def _speakers() -> set[str]:
    """Every speaker constant the collector can stamp onto a source."""
    return {
        voc.CUSTOMER_VOICE,
        voc.BD_FOCUS,
        voc.EXPERT_LENS,
        voc.STANDARDS_VOICE,
        voc.VENDOR_VOICE,
        voc.OWN_VOICE,
        voc.REGULATOR_VOICE,
        voc.ACCOUNT_EVENT,
        voc.MIXED,
    }


def _section_headings() -> list[str]:
    """The template's `## ` headings, which carry the ⟨speaker⟩ annotations."""
    text = BRIEF_TEMPLATE.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("## ")]


def test_speaker_constants_match_the_manifest_vocabulary():
    """The manifest's `speakers` map must document exactly the speakers the code can stamp.

    A speaker that exists in code but not in the manifest is invisible to the brief's
    reading key; one in the manifest but not in code is a phantom.
    """
    # Build a real manifest against an empty tree — the speakers map is static.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 7, 29))
    manifest_speakers = set(manifest["speakers"])
    assert manifest_speakers == _speakers(), (
        f"manifest speakers {sorted(manifest_speakers)} != code speakers {sorted(_speakers())}"
    )


@_template_stubbed
def test_every_speaker_has_a_section_in_the_brief_template():
    """THE regression guard: a speaker with no section is material collected and then dropped."""
    headings = "\n".join(_section_headings())
    missing = [
        speaker for speaker in sorted(_speakers() - EXEMPT_SPEAKERS) if speaker not in headings
    ]
    assert not missing, (
        f"speakers with no section in brief-template.md: {missing}. "
        "Adding a speaker to gtm_core/voc/collect.py requires giving it a home section — "
        "otherwise its material is collected, labelled, and silently dropped (this is exactly "
        "what happened to vendor-voice)."
    )


@_template_stubbed
def test_breadth_eligibility_is_stated_where_a_non_demand_speaker_renders():
    """Every non-demand speaker's section must say, in its own heading, that it is not demand.

    The brief's whole premise is that a reader can tell demand from context at a glance. A
    section that renders `vendor-voice` or `regulator-voice` without saying so in the heading
    invites exactly the conflation the spine forbids.
    """
    non_demand = sorted(_speakers() - EXEMPT_SPEAKERS - {voc.CUSTOMER_VOICE})
    offenders: list[str] = []
    for speaker in non_demand:
        for heading in _section_headings():
            if speaker not in heading:
                continue
            # The ⟨…⟩ annotation must disclaim demand for this speaker's section.
            annotation = re.search(r"⟨(.+?)⟩", heading)
            text = (annotation.group(1) if annotation else heading).lower()
            if (
                "not demand" not in text
                and "never demand" not in text
                and "not a signal" not in text
            ):
                offenders.append(heading.strip())
    assert not offenders, (
        f"non-demand speaker sections must disclaim demand in the heading annotation: {offenders}"
    )


def test_customer_voice_is_the_only_breadth_eligible_speaker():
    """Belt-and-braces on the invariant the whole brief rests on, asserted at contract level."""
    eligible = {s for s in _speakers() if voc.counts_toward_breadth(s)}
    assert eligible == {voc.CUSTOMER_VOICE}, eligible
