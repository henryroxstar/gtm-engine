"""The prospect skill decides the run's product before it does anything that can spend.

The skill is an executable procedure (CLAUDE.md, "Reading a skill is not running it"), so what it
*says first* is the control. These are text-order and text-content assertions over the generated
``SKILL.md``; the behaviour they describe is proven by ``test_run_scope.py`` and
``tests/contracts/test_scope_threaded.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2] / "plugin" / "skills"
TEXT = (SKILLS / "prospect" / "SKILL.md").read_text(encoding="utf-8")

#: Anything that can spend money or reach an external system, or run before the scope is known.
_BEFORE_SCOPE = (
    "gtm_core.preflight_report",
    "gtm_core.preflight ",
    "gtm_core.run_state",
    "gtm_core.prospect_guards",
    "fetch-entities",
    "rocketreach_lookup",
    "firecrawl",
    "gtm_core.web_sweep",
    "gtm_core.prospects status",
)


def test_the_first_tool_call_is_run_scope_resolve():
    first = TEXT.index("gtm_core.run_scope resolve")
    for marker in _BEFORE_SCOPE:
        at = TEXT.find(marker)
        assert at == -1 or first < at, f"{marker!r} appears before `run_scope resolve`"


def test_the_omit_it_sentence_is_gone_and_the_choice_is_asked_not_defaulted():
    assert "omit it for profile-wide work" not in TEXT
    assert "Unattended: never ask and never default" in TEXT
    assert "product-required" in TEXT


def test_ask_question_names_the_product_choice_as_its_one_exception():
    hits = [m.start() for m in re.finditer(r"The one named exception is Step 0", TEXT)]
    assert len(hits) == 2  # the restriction is stated twice, and both carry it
    assert "choosing the run's product" in TEXT[hits[0] : hits[0] + 120]


def test_every_command_that_reads_or_writes_scoped_state_carries_the_product():
    """Each of these modules refuses a dropped product on a company with a second product, so a
    citation without ``--product`` is a command that stops the run."""
    pattern = re.compile(
        r"python3?\s+-m\s+gtm_core\.(?:run_state|prospects\s+status|lanes\s+route"
        r"|prospects_import\s+finalize|prospects_state\s+mutate|web_sweep\s+normalize"
        r"|signal_backfill|scorecard\s+score|prospects\s+icp\s+check|eval_writeback\s+(?:plan|apply))"
        r"[^\n]*"
    )
    # a concrete command names its profile; prose that merely mentions a module does not
    cites = [c for c in pattern.findall(TEXT) if "--profile" in c]
    assert cites, "no scoped command is cited at all"
    for cite in cites:
        line_end = TEXT.index(cite) + len(cite)
        window = TEXT[TEXT.index(cite) : line_end + 200]
        assert "--product" in window, f"cited without --product: {cite[:100]}"


def test_email_skills_thread_the_product_too():
    for skill in ("email-sequence", "draft-outreach", "email-quality"):
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "[--product <slug>]" in text, f"{skill} never passes the run's product"


def test_no_skill_hand_reads_a_file_the_run_scope_owns():
    """A hard-coded ``profiles/<active>/knowledge/<file>`` is the default product's file whatever the
    run's product is. These are read through ``resolve_knowledge`` or the registry, which know the
    product; the voice, ban and other company-wide files stay on their own paths."""
    names = (
        "claims|proof|angles|premise-vocab|web-sweep|hook-matrix|icp-personas|case-studies|product"
    )
    # the name must START the filename (outreach-case-studies.txt is a linter list, not this file)
    owned = re.compile(rf"profiles/<active>/knowledge/(?:\{{[a-z,-]*\}}|(?:{names})(?![a-z-]))")
    for skill in ("prospect", "email-sequence", "draft-outreach", "email-quality"):
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        assert not owned.findall(text), f"{skill} hand-reads {owned.findall(text)}"


def test_the_linter_pack_command_carries_the_product_wherever_it_is_cited():
    """``outreach_linter pack`` loads the registry, which refuses a dropped product on a company with
    a second product, so a cite without it is a command that stops the run (fresh audit, F9)."""
    for skill in ("draft-outreach", "email-sequence", "email-quality"):
        text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
        for m in re.finditer(r"outreach_linter\.py\s+pack\b", text):
            window = text[m.start() : m.start() + 400]
            assert "--product" in window, f"{skill}: `outreach_linter pack` cited without --product"
