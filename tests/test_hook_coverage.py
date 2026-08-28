"""Tests for :mod:`gtm_core.hook_coverage` — the message-axis measurement.

Every company, person, title and hook below is invented (``docs/RULES.md`` R9): real
prospect data lives only under ``profiles/`` and ``content/``. The matrix fixtures are
miniatures of the four shapes this repo's profiles actually use, not copies of any
tenant's hook text.

The load-bearing test in this file is
:func:`test_shared_phrases_catches_the_monoculture_that_jaccard_scores_as_distinct`.
It encodes the H0 measurement: four specs restating one claim in different domain nouns
score *below* the Jaccard threshold while sharing whole phrases verbatim. That is why
the anti-monoculture gate cannot be built on bag-of-word overlap, and a regression here
would quietly re-open the gap the whole PRD is about.
"""

from __future__ import annotations

from collections import Counter

import pytest

from gtm_core.hook_coverage import (
    JACCARD_MAX,
    MAX_SPECS_PER_CAPABILITY,
    Coverage,
    MatrixShape,
    RowCell,
    SignalFit,
    UnknownHookCell,
    _cells_equal,
    argument_distinctness,
    capability_monotone,
    capability_slug,
    capability_vocab,
    classify_rows,
    declared_capability,
    declared_cell,
    declared_premise,
    derive_row_cell,
    load_premise_vocab,
    parse_matrix,
    persona_coverage,
    persona_key_of_label,
    persona_of,
    premise_unsupported,
    render,
    shared_phrases,
    signal_columns_for_segment,
)

# --- fixtures: the four matrix shapes -----------------------------------------------

GRID_MATRIX = """---
source: manual
---
# Hook matrix — Northwind Systems

## Enterprise

| Signal → / Persona ↓ | M&A / consolidation | Compliance event | 🆕 New-in-role exec |
|---|---|---|---|
| **CISO** | "One trust perimeter across two estates." | "Evidence a regulator can read." | "A control story in your first 90 days." |
| **FinOps lead** | "Cost per agent, not one bill." | "Defensible AI spend." | "Where the AI budget actually goes." |

## Startup

| Signal → / Persona ↓ | Sales-cycle stall | Board AI-risk ask | 🆕 Security hire landed |
|---|---|---|---|
| **CEO / Founder** | "Win the deal that slipped." | "The responsibility narrative." | "Give the new hire a baseline." |
"""

ROWS_MATRIX = """---
source: manual
---
# Hook matrix — Halden Labs

## Product A

| id | Persona | Signal to open on | Hook angle |
|---|---|---|---|
| ha-cto-build | CTO / Founding Engineer | Rebuilding auth per framework | "Buy the layer, keep the roadmap." |
| ha-cpo-embed | CPO | Design partner asking for provenance | "Embed the primitive, ship features." |
"""

SECTIONS_MATRIX = """---
source: manual
---
# Hook matrix — Cascade Clinics

## Pillars
**P1 Trusted Data** · **P2 Autonomous Intake**

## Head of Platform
- **P2 × staffing:** "The hardest hire in the building is the one nobody sees."
- **P1 × denials:** "Bad data is born at intake, not at billing."

## CPO
- **P2 × roadmap:** "Buy the intake layer, keep the roadmap."
"""

UNSUPPORTED_MATRIX = """---
source: manual
---
# Hook matrix — <Company>

| id | angle | payoff promise | formats | status |
|----|-------|----------------|---------|--------|
| example-augmentation | AI that replaces judgment is a bad trade | Time back | carousel | test |
"""


def _spec(front: str = "", body: str = "") -> str:
    """A sequence spec in the shipped shape: fenced Key: value block, then touches."""
    return f"""# Sequence spec — test

```
Campaign:   Test campaign
Profile:    northwind
Sign-off:   Alex
{front}```

## 1. Touches

**Step 1 — Day 1** · Subject: `a subject`
> {body or "Hi {{First Name}}, a plain opening line about agent identity."}

Prose after the quote is ignored.
"""


# --- matrix parsing ------------------------------------------------------------------


def test_grid_matrix_parses_every_persona_by_signal_intersection():
    m = parse_matrix(GRID_MATRIX)
    assert m.shape == MatrixShape.GRID
    assert m.ok
    # 2 personas x 3 signals + 1 persona x 3 signals
    assert len(m.cells) == 9
    assert set(m.personas) == {"CISO", "FinOps lead", "CEO / Founder"}
    # Hook text is kept verbatim, quotes included: it is the tenant's copy, and
    # this module never edits it.
    assert m.find("CISO", "Compliance event").hook == '"Evidence a regulator can read."'


def test_grid_matrix_strips_the_new_in_role_emoji_from_the_signal_label():
    m = parse_matrix(GRID_MATRIX)
    assert "New-in-role exec" in m.signals
    assert not any(s.startswith("🆕") for s in m.signals)


def test_rows_matrix_takes_one_cell_per_row_and_keeps_the_stable_id():
    m = parse_matrix(ROWS_MATRIX)
    assert m.shape == MatrixShape.ROWS
    assert len(m.cells) == 2
    cell = m.find("CTO / Founding Engineer", "Rebuilding auth per framework")
    assert cell.argument_id == "ha-cto-build"


def test_sections_matrix_reads_persona_from_the_heading_and_signal_from_the_bullet():
    m = parse_matrix(SECTIONS_MATRIX)
    assert m.shape == MatrixShape.SECTIONS
    assert len(m.cells) == 3
    assert m.find("Head of Platform", "staffing") is not None
    # "## Pillars" carries no bullets in the label shape, so it is not a persona.
    assert "Pillars" not in m.personas


def test_a_matrix_with_no_persona_or_signal_axis_is_named_unsupported_not_empty():
    m = parse_matrix(UNSUPPORTED_MATRIX)
    assert m.shape == MatrixShape.UNSUPPORTED
    assert not m.ok
    assert m.cells == {}
    # The reason must be carried, so this can never read as "parsed fine, found
    # nothing" -- a gate that passes by finding nothing is the failure being fixed.
    assert "no persona axis" in m.reason


def test_matrix_carries_no_hook_of_its_own():
    """Every hook string in the result came out of the file."""
    m = parse_matrix(GRID_MATRIX)
    for cell in m.cells.values():
        assert cell.hook in GRID_MATRIX


# --- persona vocabulary --------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Chief Information Security Officer", "ciso"),
        ("CISO", "ciso"),
        ("Chief Data Officer", "data-compliance"),
        ("Head of Data Governance", "data-compliance"),
        ("Chief Risk Officer", "compliance"),
        ("Head of AI Platform", "ai-platform"),
        ("Head of Partnerships", "partnership"),
        ("FinOps Lead", "finops"),
        ("Enterprise Architect", "cloud-architect"),
        ("CTO", "cto"),
        ("Chief Product Officer", "cpo"),
        ("Co-Founder & CEO", "ceo"),
    ],
)
def test_persona_of_places_a_recognisable_title(title, expected):
    assert persona_of(title) == expected


@pytest.mark.parametrize(
    "title",
    ["", "Regional Sales Manager", "Chief Happiness Officer", "Barista"],
)
def test_persona_of_is_fail_quiet_on_an_unrecognised_title(title):
    """An unrecognised title says nothing; guessing sends a cost pitch to a reviewer."""
    assert persona_of(title) is None


def test_ciso_wins_over_cloud_architect_because_order_is_load_bearing():
    """ "chief information security" must not be claimed by "chief information officer"."""
    assert persona_of("Chief Information Security Officer") == "ciso"
    assert persona_of("Chief Information Officer") == "cloud-architect"


def test_data_compliance_and_compliance_stay_distinct_personas():
    """Both labels contain "compliance"; ordering is what keeps the matrix row count."""
    assert persona_key_of_label("Data / Compliance Leader") == "data-compliance"
    assert persona_key_of_label("CRO / Compliance") == "compliance"


def test_every_grid_persona_label_normalises_to_a_distinct_key():
    """A collision would silently merge two matrix rows into one persona."""
    m = parse_matrix(GRID_MATRIX)
    keys = [persona_key_of_label(p) for p in m.personas]
    assert None not in keys
    assert len(set(keys)) == len(m.personas)


# --- what a spec declares -------------------------------------------------------------


def test_declared_cell_reads_the_fenced_front_block():
    spec = _spec("hook_cell:  FinOps lead × Compliance event\nargument_id: cost-attribution\n")
    d = declared_cell(spec)
    assert (d.persona, d.signal) == ("FinOps lead", "Compliance event")
    assert d.argument_id == "cost-attribution"


def test_declared_cell_accepts_an_ascii_x_separator():
    d = declared_cell(_spec("hook_cell: CISO x Compliance event\n"))
    assert (d.persona, d.signal) == ("CISO", "Compliance event")


def test_a_spec_declaring_nothing_returns_none_rather_than_raising():
    """The four staged specs predate the field; the baseline must report, not crash."""
    assert declared_cell(_spec()) is None


def test_an_unknown_cell_is_a_hard_error_when_the_matrix_is_supplied():
    m = parse_matrix(GRID_MATRIX)
    with pytest.raises(UnknownHookCell):
        declared_cell(_spec("hook_cell: CISO × Fictional signal\n"), m)
    with pytest.raises(UnknownHookCell):
        declared_cell(_spec("hook_cell: Head of Vibes × Compliance event\n"), m)


def test_a_known_cell_validates_against_the_matrix():
    m = parse_matrix(GRID_MATRIX)
    assert declared_cell(_spec("hook_cell: CISO × Compliance event\n"), m) is not None


# --- distinctness ---------------------------------------------------------------------

#: Four openers that make ONE claim in four vocabularies -- the 2026-08-18 shape.
_ONE_ARGUMENT = {
    "exec.md": (
        "Access logs answer who logged in, not which agent acted or on whose "
        "authority. Tell me if this is already handled."
    ),
    "security.md": (
        "When agents touch regulated records the trail shows the tenant, not which "
        "agent acted. Tell me if this is already handled."
    ),
    "technical.md": (
        "IAM records which service connected; it does not prove which agent acted. "
        "Tell me if this is already handled."
    ),
    "startup.md": (
        "Your first enterprise security review asks which agent took an action. "
        "Tell me if this is already handled."
    ),
}


def test_argument_distinctness_scores_every_pair():
    specs = {n: _spec(body=b) for n, b in _ONE_ARGUMENT.items()}
    pairs = argument_distinctness(specs)
    assert len(pairs) == 6  # 4 choose 2
    assert pairs == sorted(pairs, key=lambda p: -p.jaccard)


def test_shared_phrases_catches_the_monoculture_that_jaccard_scores_as_distinct():
    """The H0 finding, as a regression.

    Four specs restating one claim in four vocabularies score BELOW the Jaccard
    ceiling -- because differing domain nouns push bag-of-word overlap down -- while
    carrying a whole phrase verbatim. Bag-of-words cannot separate "same argument,
    reworded" from "different argument"; a verbatim shared phrase can.
    """
    specs = {n: _spec(body=b) for n, b in _ONE_ARGUMENT.items()}

    pairs = argument_distinctness(specs)
    assert all(not p.same_argument for p in pairs), (
        "Jaccard is expected to MISS this monoculture; if it now fires, the "
        "anti-monoculture gate design can be revisited"
    )
    assert max(p.jaccard for p in pairs) < JACCARD_MAX

    shared = shared_phrases(specs)
    assert shared, "the verbatim shared phrase must be detected"
    assert shared[0].count == len(specs)
    assert "already handled" in shared[0].phrase


def test_shared_phrases_stays_quiet_when_the_openers_genuinely_differ():
    """The negative control: four real arguments share no phrase over the ceiling."""
    specs = {
        "a.md": _spec(body="Cost per agent, not one invoice labelled AI at quarter end."),
        "b.md": _spec(body="Partner agents cross the boundary before anyone verifies them."),
        "c.md": _spec(body="Every framework rebuilds the same policy layer from scratch."),
        "d.md": _spec(body="Your board asked a question your logging cannot answer today."),
    }
    assert shared_phrases(specs) == []


# --- persona coverage -----------------------------------------------------------------


def _counter(**kw):
    from collections import Counter

    return Counter(kw)


def test_persona_coverage_names_populated_personas_no_spec_addresses():
    findings = persona_coverage(
        _counter(ciso=120, finops=80, cpo=5),
        {"a.md": None},
        min_recipients=40,
    )
    assert len(findings) == 1
    assert findings[0].startswith("persona-unaddressed: ")
    assert "ciso (120)" in findings[0] and "finops (80)" in findings[0]
    # Below the threshold, so not a finding.
    assert "cpo" not in findings[0]


def test_persona_coverage_is_quiet_once_a_spec_addresses_each_persona():
    from gtm_core.hook_coverage import DeclaredCell

    findings = persona_coverage(
        _counter(ciso=120),
        {"a.md": DeclaredCell(persona="CISO", signal="Compliance event")},
        min_recipients=40,
    )
    assert findings == []


def test_persona_coverage_aggregates_rather_than_printing_one_finding_per_persona():
    """finding_budget's contract: a rate with exemplars, never six walls."""
    findings = persona_coverage(
        _counter(ciso=99, finops=98, cpo=97, cto=96, ceo=95, compliance=94),
        {"a.md": None},
        min_recipients=40,
    )
    assert len(findings) == 1
    assert "6 of 6 populated persona(s)" in findings[0]
    assert "+3 more" in findings[0]


def test_findings_split_on_the_finding_budget_contract():
    """Every finding must read as "rule: detail" so split_rule works unchanged."""
    from gtm_core.finding_budget import split_rule

    findings = persona_coverage(_counter(ciso=120), {"a.md": None}, min_recipients=40)
    rule, detail = split_rule(findings[0])
    assert rule == "persona-unaddressed"
    assert detail


# --- the report -----------------------------------------------------------------------


def test_arguments_counts_one_when_every_spec_shares_a_phrase_verbatim():
    cov = Coverage(declared=dict.fromkeys(_ONE_ARGUMENT))
    cov.shared = shared_phrases({n: _spec(body=b) for n, b in _ONE_ARGUMENT.items()})
    assert cov.arguments == 1


def test_arguments_counts_declared_cells_when_specs_declare_them():
    from gtm_core.hook_coverage import DeclaredCell

    cov = Coverage(
        declared={
            "a.md": DeclaredCell("CISO", "Compliance event", "attribution"),
            "b.md": DeclaredCell("FinOps lead", "M&A / consolidation", "cost"),
        }
    )
    assert cov.arguments == 2


def test_render_names_the_unresolved_bucket_rather_than_hiding_it():
    cov = Coverage(campaign="test", rows=10)
    cov.personas = _counter(ciso=7)
    cov.unresolved = _counter(**{"Chief Vibes Officer": 3})
    out = render(cov)
    assert "unresolved" in out
    assert "Chief Vibes Officer" in out


def test_render_reports_an_unsupported_matrix_by_name():
    cov = Coverage(campaign="test", matrix=parse_matrix(UNSUPPORTED_MATRIX))
    assert "UNSUPPORTED" in render(cov)


# --- the campaign gate, end to end ----------------------------------------------------
#
# audit_campaign is the campaign-scope half of the H2 gate (the per-spec half is
# merge_render_linter's hook-cell-* rules). A gate needs its PASS state pinned as hard as
# its FAIL state: without the negative control below, a bug that made every campaign fail
# would look exactly like the gate working.

_MATRIX_FOR_CAMPAIGN = GRID_MATRIX


def _campaign(
    tmp_path,
    specs: dict[str, tuple[str, str]],
    rows_per_spec: int = 50,
    *,
    segments: dict[str, list[str]] | None = None,
    evidence: dict[str, str] | None = None,
    matrix: str | None = None,
):
    """Build a throwaway profile + content tree and return (profiles_root, content_root).

    ``specs`` maps a spec filename to ``(hook_cell, touch-1 body)``. Every recipient is
    invented (R9).

    ``segments`` and ``evidence`` are optional per-spec columns used by the list-vs-cell
    fit checks. Omitting them writes no such column at all, which is the shape every
    pre-existing test in this file relies on: with no segment column the fit check has
    nothing to compare and must stay silent rather than inventing a mismatch.
    ``segments[name]`` is cycled over the rows, so ``["enterprise", "startup"]`` gives an
    even split and ``["enterprise"] * 7 + ["startup"] * 3`` gives 70/30.
    """
    profiles = tmp_path / "profiles"
    (profiles / "northwind" / "knowledge").mkdir(parents=True)
    (profiles / "northwind" / "knowledge" / "hook-matrix.md").write_text(
        matrix or _MATRIX_FOR_CAMPAIGN, encoding="utf-8"
    )

    seq = tmp_path / "content" / "northwind" / "prospects" / "sequences"
    seq.mkdir(parents=True)

    titles = {"CISO": "Chief Information Security Officer", "FinOps lead": "FinOps Lead"}
    cells = []
    for i, (name, (cell, body)) in enumerate(sorted(specs.items())):
        front = f"hook_cell:   {cell}\nargument_id: arg-{i}\n" if cell else ""
        (seq / name).write_text(_spec(front, body), encoding="utf-8")

        csv_name = f"list-{i}.csv"
        persona = cell.split("×")[0].strip() if cell else "CISO"
        title = titles.get(persona, "Chief Information Security Officer")
        seg_cycle = (segments or {}).get(name)
        row_evidence = (evidence or {}).get(name, "")
        header = "first,last,email,title,company,company_domain,suppression"
        if seg_cycle:
            header += ",segment"
        if row_evidence:
            header += ",signal_evidence"
        lines = [header]
        for n in range(rows_per_spec):
            row = f"Ada,Okonkwo,ada{n}@halden.example,{title},Halden Systems,halden.example,"
            if seg_cycle:
                row += f",{seg_cycle[n % len(seg_cycle)]}"
            if row_evidence:
                row += f",{row_evidence}"
            lines.append(row)
        (seq / csv_name).write_text("\n".join(lines) + "\n", encoding="utf-8")

        cells.append(
            f'[[sequence]]\nid = "seq{i}"\ncsv = "{csv_name}"\nspec = "{name}"\n'
            f'campaign = "test-campaign"\n'
        )
    (seq / "cells.toml").write_text("\n".join(cells), encoding="utf-8")
    return profiles, tmp_path / "content"


def test_a_campaign_with_declared_distinct_cells_passes(tmp_path):
    """The negative control. Two specs, two real cells, two genuinely different claims."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-a.md": (
                "CISO × Compliance event",
                "Regulators now ask which agent took an action, and the login record "
                "cannot answer that question for anybody reviewing the estate.",
            ),
            "spec-b.md": (
                "FinOps lead × M&A / consolidation",
                "Two merged platforms bill artificial intelligence as one line item, so "
                "nobody can price a team against what it actually consumed last quarter.",
            ),
        },
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=2,
        min_recipients=40,
    )
    assert cov.rows == 100
    assert cov.declared_count == 2
    assert cov.arguments == 2
    assert not cov.failed, cov.findings


def test_a_campaign_where_every_spec_declares_nothing_fails(tmp_path):
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-a.md": ("", "Regulators ask which agent acted and the login cannot say."),
            "spec-b.md": ("", "Auditors ask which agent acted and the login cannot say."),
        },
    )
    cov = audit_campaign("northwind", "test-campaign", content_root=content, profiles_root=profiles)
    assert cov.failed
    assert any(f.startswith("hook-cell-missing") for f in cov.findings)


def test_a_campaign_repeating_one_phrase_across_every_spec_fails(tmp_path):
    """The monoculture the PRD was written about, caught by the phrase check."""
    from gtm_core.hook_coverage import audit_campaign

    shared = " Tell me if this is already handled at your end today."
    profiles, content = _campaign(
        tmp_path,
        {
            "spec-a.md": ("CISO × Compliance event", "Regulators want attribution." + shared),
            "spec-b.md": ("FinOps lead × M&A / consolidation", "Finance wants a bill." + shared),
            "spec-c.md": ("CISO × M&A / consolidation", "Two estates, one trail." + shared),
            "spec-d.md": ("FinOps lead × Compliance event", "Defensible spend." + shared),
        },
    )
    cov = audit_campaign("northwind", "test-campaign", content_root=content, profiles_root=profiles)
    assert cov.failed
    assert any(f.startswith("template-share") for f in cov.findings)


# --- list vs declared cell: is the copy aimed at these recipients? -------------------
#
# The gap these close, measured 2026-08-21: four specs declared four valid, distinct
# cells and passed every check in this file, while one of them was written for the
# matrix's STARTUP grid and sent to a list that was 70% enterprise. Nothing above
# compares a spec to its own recipients.

#: A matrix whose section headings carry a parenthetical, as a real tenant's does
#: ("## Enterprise (per ICP value-prop ranking)"). The segment stored on a Cell is the
#: heading verbatim, so the comparison has to find the canonical word inside it.
PARENTHETICAL_MATRIX = """---
source: manual
---
# Hook matrix — Northwind Systems

## Enterprise (per ICP value-prop ranking)

| Signal → / Persona ↓ | M&A / consolidation | MCP/A2A/AP2 entering architecture |
|---|---|---|
| **CISO** | "One trust perimeter across two estates." | "Name the agent, not the key." |
| **FinOps lead** | "Cost per agent, not one bill." | "Price the protocol traffic." |

## Startup (per ICP value props)

| Signal → / Persona ↓ | Sales-cycle stall | MCP / A2A in build |
|---|---|---|
| **CEO / Founder** | "Win the deal that slipped." | "Buy the layer, keep the roadmap." |
"""


def test_signal_terms_prefers_an_acronym_over_the_connective_words_beside_it():
    """``MCP/A2A/AP2 entering architecture`` must not be scored on "architecture".

    The first implementation ignored casing and derived ``{build}`` for the startup MCP
    column, then reported 7/61 attestation from rows that never mentioned a protocol.
    An UPPERCASE token in the tenant's own label names the thing; a lowercase one is
    English that matches any sentence.
    """
    from gtm_core.hook_coverage import parse_matrix, signal_terms

    terms = signal_terms(parse_matrix(PARENTHETICAL_MATRIX))
    assert terms["MCP/A2A/AP2 entering architecture"] == frozenset({"mcp", "a2a", "ap2"})
    assert "architecture" not in terms["MCP/A2A/AP2 entering architecture"]
    assert "entering" not in terms["MCP/A2A/AP2 entering architecture"]


def test_signal_terms_keeps_an_acronym_two_grids_share():
    """``mcp`` belongs to both the enterprise and the startup column, and to both sets.

    Distinctiveness is scoped to the segment grid: a matrix-wide uniqueness rule deletes
    the shared acronym from both columns and leaves only the generic remainder, which is
    how the first version lost ``mcp``/``a2a`` entirely.
    """
    from gtm_core.hook_coverage import parse_matrix, signal_terms

    terms = signal_terms(parse_matrix(PARENTHETICAL_MATRIX))
    assert "mcp" in terms["MCP/A2A/AP2 entering architecture"]
    assert "mcp" in terms["MCP / A2A in build"]


def test_signal_terms_falls_back_to_words_when_a_label_has_no_acronym():
    from gtm_core.hook_coverage import parse_matrix, signal_terms

    terms = signal_terms(parse_matrix(PARENTHETICAL_MATRIX))
    assert "consolidation" in terms["M&A / consolidation"]


def test_a_startup_cell_on_a_seventy_percent_enterprise_list_is_a_finding(tmp_path):
    """The regression test for the defect that shipped.

    The cell exists, the copy matches the cell, the argument is distinct — and the
    recipients are in the other grid. Every other check in this module passes here.
    """
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-builder.md": (
                "CEO / Founder × Sales-cycle stall",
                "Each framework holds credentials its own way, so a policy change has "
                "to be made in every wrapper and they drift apart quietly over time.",
            ),
        },
        rows_per_spec=61,
        segments={"spec-builder.md": ["enterprise"] * 7 + ["startup"] * 3},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    fit = cov.segment_fits["spec-builder.md"]
    assert fit.declared_segment == "startup"
    assert fit.rows == 61
    assert fit.share < 0.5
    assert cov.failed
    assert any(f.startswith("cell-segment-fit") for f in cov.findings)


def test_a_list_matching_its_declared_grid_passes(tmp_path):
    """The negative control: without it, a check that always fires looks like it works."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-architect.md": (
                "CISO × M&A / consolidation",
                "Two merged estates keep separate logs, so no single view says which "
                "agent acted on whose authority across the whole chain of custody.",
            ),
        },
        rows_per_spec=18,
        segments={"spec-architect.md": ["enterprise"]},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    assert cov.segment_fits["spec-architect.md"].share == 1.0
    assert not any(f.startswith("cell-segment-fit") for f in cov.findings)


def test_segment_counts_merge_case_variants_instead_of_overwriting_them(tmp_path):
    """``Enterprise`` and ``enterprise`` are one bucket, and their counts SUM.

    Built as a dict comprehension this silently kept whichever spelling came last,
    reporting a 70%-enterprise list as 60% startup — the check inverting the very
    defect it exists to catch. Both spellings are live in the tenant's stored CSVs.
    """
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-mixed.md": (
                "CISO × M&A / consolidation",
                "Two merged estates keep separate logs, so no single view says which "
                "agent acted on whose authority across the whole chain of custody.",
            ),
        },
        rows_per_spec=40,
        # 30 enterprise (20 titlecase + 10 lowercase) and 10 startup.
        segments={"spec-mixed.md": ["Enterprise", "Enterprise", "enterprise", "startup"]},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    fit = cov.segment_fits["spec-mixed.md"]
    assert fit.counts["enterprise"] == 30, fit.counts
    assert fit.counts["startup"] == 10, fit.counts
    assert set(fit.counts) == {"enterprise", "startup"}
    assert fit.rows == 40


def test_an_unattested_signal_warns_and_does_not_fail_the_run(tmp_path):
    """``cell-signal-fit`` is advisory: it infers a signal from free text.

    A miss can mean "the evidence does not say" rather than "the aim is wrong", so it
    reports and never gates — unlike segment, where both sides are enumerated values.
    """
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-security.md": (
                "CISO × MCP/A2A/AP2 entering architecture",
                "Once those agents call across an org boundary, a network credential "
                "proves the company and stays silent on which agent is calling today.",
            ),
        },
        rows_per_spec=57,
        segments={"spec-security.md": ["enterprise"]},
        evidence={"spec-security.md": "opened a second regional support centre"},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    fit = cov.signal_fits["spec-security.md"]
    assert fit.attested == 0
    assert not cov.failed, cov.findings
    assert any(w.startswith("cell-signal-fit") for w in cov.warnings)
    assert "cell-signal-fit" in render(cov)


def test_an_attested_signal_is_quiet(tmp_path):
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-security.md": (
                "CISO × MCP/A2A/AP2 entering architecture",
                "Once those agents call across an org boundary, a network credential "
                "proves the company and stays silent on which agent is calling today.",
            ),
        },
        rows_per_spec=57,
        segments={"spec-security.md": ["enterprise"]},
        evidence={"spec-security.md": "shipped an MCP server for partner integrations"},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    assert cov.signal_fits["spec-security.md"].share == 1.0
    # Scoped to this test's own subject since 2026-08-21: `cell-row-unrecorded` is a second,
    # unrelated advisory that every pre-`hook_cell` list now carries, and asserting on the
    # whole warnings list would make this test fail for a reason it is not about.
    assert not [w for w in cov.warnings if w.startswith("cell-signal-fit")]


def test_a_spec_declaring_no_cell_reports_no_fit_rather_than_a_second_failure(tmp_path):
    """``hook-cell-missing`` already covers it; restating it as a fit failure double-counts."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {"spec-a.md": ("", "Regulators ask which agent acted and the login cannot say.")},
        rows_per_spec=40,
        segments={"spec-a.md": ["enterprise"]},
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign("northwind", "test-campaign", content_root=content, profiles_root=profiles)
    assert cov.segment_fits == {}
    assert cov.signal_fits == {}
    assert not any(f.startswith("cell-segment-fit") for f in cov.findings)


def test_a_list_with_no_segment_column_is_not_scored_as_a_mismatch(tmp_path):
    """Fail-quiet on absence, not fail-closed: an absent optional column is not evidence.

    Every pre-existing campaign test in this file writes no segment column, and none of
    them should acquire a fit finding.
    """
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-a.md": (
                "CISO × M&A / consolidation",
                "Two merged estates keep separate logs, so no single view says which "
                "agent acted on whose authority across the whole chain of custody.",
            )
        },
        rows_per_spec=40,
        matrix=PARENTHETICAL_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )
    assert cov.segment_fits["spec-a.md"].rows == 0
    assert not any(f.startswith("cell-segment-fit") for f in cov.findings)


# --- premise fit (2026-08-21) -------------------------------------------------
#
# The dominant class in the operator's own review and the one nothing checked: the fact is
# true, on-topic, about the right company and specific — and it does not ESTABLISH what the
# next paragraph claims. Every fixture below is invented; the SHAPES are the real ones
# ("one product on one platform" under a body claiming a cross-platform pain).


def _vocab(tmp_path, body: str) -> dict:
    folder = tmp_path / "acme" / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "premise-vocab.toml").write_text(body, encoding="utf-8")
    return load_premise_vocab("acme", tmp_path)


_VOCAB = """
schema = 1
[premise.multi-framework]
claim = "runs agents on more than one framework"
min_distinct = 2
terms = ["langgraph", "autogen", "crewai", "bedrock"]
[premise.ships-agents]
claim = "ships agents at all"
min_distinct = 1
terms = ["agent", "assistant"]
"""


def test_a_premise_needing_two_is_unmet_by_one(tmp_path):
    """The Autodesk shape: one product on one platform under a plurality claim."""
    v = _vocab(tmp_path, _VOCAB)
    rows = [{"email": "a@x.example", "signal_evidence": "Launched a new tool built on Bedrock."}]
    hits = premise_unsupported(rows, v["multi-framework"])
    assert len(hits) == 1
    assert hits[0].attested == ("bedrock",)
    assert "needs 2 distinct" in hits[0].detail


def test_the_same_row_meets_a_premise_that_needs_one(tmp_path):
    """Positive control on the arity itself — the row is not simply 'bad'.

    Without this, the test above is equally consistent with a matcher that never matches.
    """
    v = _vocab(tmp_path, _VOCAB)
    rows = [{"email": "a@x.example", "signal_evidence": "Launched an agent built on Bedrock."}]
    assert premise_unsupported(rows, v["ships-agents"]) == []


def test_two_distinct_terms_satisfy_it(tmp_path):
    v = _vocab(tmp_path, _VOCAB)
    rows = [
        {
            "email": "a@x.example",
            "signal_evidence": "Runs LangGraph for research and CrewAI for support.",
        }
    ]
    assert premise_unsupported(rows, v["multi-framework"]) == []


def test_the_same_term_twice_is_still_one_term(tmp_path):
    """Arity counts DISTINCT terms. 'Bedrock ... Bedrock' is one framework mentioned twice,
    and counting occurrences would let a single-framework row pass a plurality premise —
    which is the exact defect this rule exists to catch."""
    v = _vocab(tmp_path, _VOCAB)
    rows = [{"email": "a@x.example", "signal_evidence": "Bedrock here, Bedrock there."}]
    assert len(premise_unsupported(rows, v["multi-framework"])) == 1


def test_terms_match_on_word_boundaries(tmp_path):
    """'bedrock' must not fire inside 'bedrocked' — a substring match would inflate every
    attestation count and quietly make the gate pass."""
    v = _vocab(tmp_path, _VOCAB)
    rows = [
        {"email": "a@x.example", "signal_evidence": "A bedrocked foundation of autogenous ore."}
    ]
    hits = premise_unsupported(rows, v["multi-framework"])
    assert len(hits) == 1
    assert hits[0].attested == ()


def test_evidence_is_read_across_all_three_record_fields(tmp_path):
    """A row gets every chance to attest before it fails: a false ERROR deletes a good row."""
    v = _vocab(tmp_path, _VOCAB)
    rows = [
        {
            "email": "a@x.example",
            "signal_evidence": "Runs LangGraph in production.",
            "why_now": "Also evaluating CrewAI for support workflows.",
        }
    ]
    assert premise_unsupported(rows, v["multi-framework"]) == []


def test_a_profile_with_no_vocab_file_disables_the_check(tmp_path):
    assert load_premise_vocab("acme", tmp_path) == {}


def test_declared_premise_reads_the_front_block():
    spec = "```\nCampaign: X\nhook_cell:   CISO x MCP\npremise:     multi-framework\n```"
    assert declared_premise(spec) == "multi-framework"


def test_declared_premise_is_empty_when_absent():
    assert declared_premise("```\nCampaign: X\nhook_cell: CISO x MCP\n```") == ""


def test_cells_equal_tolerates_the_separator():
    """A recorded cell and a declared cell are written by different producers; failing on
    punctuation is how a real gate becomes a switched-off one."""
    assert _cells_equal("CISO × MCP / A2A", "CISO x MCP / A2A")
    assert _cells_equal("CISO  x  MCP", "ciso x mcp")
    assert not _cells_equal("CISO x MCP", "CTO x MCP")


def test_a_company_named_partners_cannot_attest_on_its_own_name(tmp_path):
    """The rule must not commit the error it exists to catch.

    'Ardent Health Partners' carries a premise term on its letterhead. Matching it would
    mean the EVIDENCE carries the word while the FACT does not — relevance mistaken for
    entailment, by the check whose whole job is to tell those apart.
    """
    v = _vocab(
        tmp_path,
        'schema = 1\n[premise.cross-org]\nclaim = "agents cross a boundary"\n'
        'min_distinct = 1\nterms = ["partner", "partners"]\n',
    )
    rows = [
        {
            "email": "a@x.example",
            "company": "Ardent Health Partners",
            "signal_evidence": "Will expand its virtual nursing capabilities this year.",
        }
    ]
    hits = premise_unsupported(rows, v["cross-org"])
    assert len(hits) == 1, "the company's own name must not attest the premise"


def test_the_same_row_attests_when_the_FACT_carries_the_term(tmp_path):
    """Positive control: stripping the name must not make the rule blind.

    Without this, the test above is equally consistent with a check that now matches nothing.
    """
    v = _vocab(
        tmp_path,
        'schema = 1\n[premise.cross-org]\nclaim = "agents cross a boundary"\n'
        'min_distinct = 1\nterms = ["partner", "partners"]\n',
    )
    rows = [
        {
            "email": "a@x.example",
            "company": "Ardent Health Partners",
            "signal_evidence": "Announced an enterprise deal with a virtual-care partner.",
            "why_now": "spans 2,000+ rooms",
        }
    ]
    assert premise_unsupported(rows, v["cross-org"]) == []


def test_a_signal_whose_terms_are_all_list_wide_is_reported_unmeasurable():
    """The false PASS found 2026-08-23 on a real per-cell draw.

    `signal_terms` derives terms from the matrix column label. That works for an
    acronym-shaped label and fails for ordinary English: `Hiring for "agent platform" / AI
    CoE` reduces to agent/coe/hiring/platform, and in an agent-identity prospect list `agent`
    and `platform` are everywhere. Eight of nine rows "attested" the hiring signal on those
    two words alone while not one of them, read as prose, was about hiring or a centre of
    excellence — a healthy-looking attestation number that was pure noise.

    The score is deliberately NOT corrected. Subtracting noise terms was tried and zeroed a
    fixture where 57 rows genuinely all attested `mcp`; a real 100% and a spurious one cannot
    be separated by frequency alone without a background corpus. So the number stands and the
    warning says not to trust it."""
    fit = SignalFit(
        spec="spec-hiring.md",
        signal='Hiring for "agent platform" / AI CoE',
        rows=9,
        attested=8,
        terms=("agent", "coe", "hiring", "platform"),
        noise_terms=("agent", "platform"),
        noise_only=8,
        hits=Counter({"platform": 6, "agent": 4, "coe": 1}),
    )
    assert fit.noise_dominated
    assert fit.share > 0.8, "the misleading score is preserved, not silently zeroed"


def test_a_signal_with_one_real_term_is_not_reported_unmeasurable():
    """The negative control. One surviving discriminating term is enough to measure a cell,
    and flagging it would make the warning noise in its own right."""
    fit = SignalFit(
        spec="spec-mcp.md",
        signal="MCP/A2A/AP2 entering architecture",
        rows=57,
        attested=57,
        terms=("mcp", "a2a"),
        noise_terms=("mcp",),
        noise_only=0,
        hits=Counter({"mcp": 57, "a2a": 12}),
    )
    assert not fit.noise_dominated


def test_one_lucky_clean_hit_does_not_vouch_for_the_rest():
    """The second inert definition, pinned. One row of nine matching `coe` must not certify
    the eight that matched only list-wide words — counting rows is the whole point."""
    fit = SignalFit(
        spec="spec-hiring.md",
        signal="Hiring",
        rows=9,
        attested=9,
        terms=("agent", "coe"),
        noise_terms=("agent",),
        noise_only=8,
        hits=Counter({"agent": 9, "coe": 1}),
    )
    assert fit.noise_dominated


# --- row-level signal: derive_row_cell / classify_rows / signal_columns_for_segment ----
#
# The row-side counterpart to `declared_cell`. `HOOK_CELL_COLUMN` already exists with an
# ERROR gate (`cell-row-mismatch`) and is measured 2026-08-23 as unpopulated on every live
# row (0/207) -- these tests are the ones that decide whether the derivation actually
# closes that gap, or just moves the same silent-miss failure one function over.

SIGNAL_GRID_MATRIX = """---
source: manual
---
# Hook matrix — Fenwick Systems

## Enterprise

| Signal → / Persona ↓ | Compliance event (audit, breach) | Hiring for "agent platform" / AI CoE | MCP/A2A/AP2 entering architecture |
|---|---|---|---|
| **CISO** | "Evidence a regulator can read." | "A team before the tooling." | "Protocol-native from day one." |
| **FinOps lead** | "Defensible AI spend." | "Cost per new hire's stack." | "Who pays when agents call agents." |

## Startup

| Signal → / Persona ↓ | MCP / A2A in build | Board / investor AI-risk ask |
|---|---|---|
| **CEO / Founder** | "Buy the protocol layer." | "The responsibility narrative." |
"""


def test_signal_columns_for_segment_returns_only_that_grids_labels():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    ent = signal_columns_for_segment(m, "Enterprise")
    assert set(ent) == {
        "Compliance event (audit, breach)",
        'Hiring for "agent platform" / AI CoE',
        "MCP/A2A/AP2 entering architecture",
    }
    startup = signal_columns_for_segment(m, "startup")  # lowercase input, still resolves
    assert set(startup) == {"MCP / A2A in build", "Board / investor AI-risk ask"}


def test_derive_row_cell_resolves_a_matching_row():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="Chief Information Security Officer",
        segment="Enterprise",
        signal_column="Compliance event (audit, breach)",
    )
    assert rc.cell is not None
    assert rc.cell.persona == "CISO"
    assert rc.cell.signal == "Compliance event (audit, breach)"
    assert rc.reason == ""


def test_derive_row_cell_survives_segment_case_variants():
    """Live lists carry `Startup`/`startup`, `Enterprise`/`enterprise` interchangeably
    (measured across all four 2026-08-20 send lists) -- the resolver must not silently
    fail on the lowercase spelling."""
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    upper = derive_row_cell(
        m,
        email="a@x.example",
        title="CISO",
        segment="Enterprise",
        signal_column="Compliance event (audit, breach)",
    )
    lower = derive_row_cell(
        m,
        email="a@x.example",
        title="CISO",
        segment="enterprise",
        signal_column="Compliance event (audit, breach)",
    )
    assert upper.cell is not None
    assert lower.cell is not None
    assert upper.cell.key == lower.cell.key


def test_derive_row_cell_survives_extra_whitespace_and_case_in_the_signal_value():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="CISO",
        segment="Enterprise",
        signal_column="  compliance event (audit, breach)  ",
    )
    assert rc.cell is not None
    assert rc.cell.signal == "Compliance event (audit, breach)"


def test_signal_columns_are_scoped_to_the_rows_own_grid():
    """An enterprise row cannot attest a Startup-only signal, even though the resolver
    would otherwise recognise the words -- this is the check `cell-segment-fit` proved
    necessary at the spec level (builder 2026-08-21, a valid cell aimed at 70% the wrong
    grid); the row-level equivalent must refuse the cross-grid match too."""
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="CISO",
        segment="Enterprise",
        signal_column="MCP / A2A in build",  # exists, but only in the Startup grid,
        # and CISO holds no cell there at all
    )
    assert rc.cell is None
    assert "ciso" in rc.reason


#: A matrix where the SAME persona (CTO) sits in both grids but each grid pairs it with a
#: DIFFERENT signal -- the fixture for the "this persona x signal exists, but in the other
#: grid" branch specifically, distinct from "this persona has no such signal anywhere".
SAME_PERSONA_BOTH_GRIDS_MATRIX = """---
source: manual
---
# Hook matrix — Arden Robotics

## Enterprise

| Signal → / Persona ↓ | Compliance event (audit, breach) |
|---|---|
| **CTO** | "A control story in your first 90 days." |

## Startup

| Signal → / Persona ↓ | MCP / A2A in build |
|---|---|
| **CTO** | "Buy the protocol layer." |
"""


def test_a_persona_and_signal_that_exist_but_only_in_the_other_grid_names_the_grid():
    """CTO x "MCP / A2A in build" is a real cell -- just not in the Enterprise grid this
    row sits in. That is a different, more specific fact than "no such signal at all",
    and the reason string must say so (this is the branch
    `test_signal_columns_are_scoped_to_the_rows_own_grid` above is NOT exercising)."""
    m = parse_matrix(SAME_PERSONA_BOTH_GRIDS_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="CTO",
        segment="Enterprise",
        signal_column="MCP / A2A in build",
    )
    assert rc.cell is None
    assert "not in the" in rc.reason
    assert "enterprise" in rc.reason


def test_two_similar_mcp_labels_across_grids_do_not_cross_resolve():
    """`MCP/A2A/AP2 entering architecture` (enterprise) and `MCP / A2A in build` (startup)
    share vocabulary but are different columns -- a startup CEO recording the enterprise
    label must not resolve, because that persona has no cell in that grid at all."""
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="CEO / Founder",
        segment="Startup",
        signal_column="MCP/A2A/AP2 entering architecture",
    )
    assert rc.cell is None
    assert "no" in rc.reason and "cell" in rc.reason


def test_derive_row_cell_reports_unresolved_persona_not_a_bare_none():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="Staff Accountant",
        segment="Enterprise",
        signal_column="Compliance event (audit, breach)",
    )
    assert rc.cell is None
    assert rc.persona_key is None
    assert rc.reason  # never a silent/empty reason


def test_derive_row_cell_reports_a_blank_signal_rather_than_guessing():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rc = derive_row_cell(
        m,
        email="a@x.example",
        title="CISO",
        segment="Enterprise",
        signal_column="",
    )
    assert rc.cell is None
    assert "signal_column" in rc.reason


def test_derive_row_cell_round_trips_every_cell_in_a_multi_grid_matrix():
    """Exhaustive: every cell the matrix declares must be re-derivable from its own
    persona label used as a title, its own segment, and its own signal label. This is
    the test that would catch a punctuation or normalisation class nobody thought to
    write a narrower test for -- the exact failure mode `_cells_equal`'s own docstring
    warns about ("the check fails on punctuation and gets switched off")."""
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    assert len(m.cells) == 8  # 2x3 + 1x2 -- sanity on the fixture itself
    for cell in m.cells.values():
        rc = derive_row_cell(
            m,
            email="a@x.example",
            title=cell.persona,
            segment=cell.segment,
            signal_column=cell.signal,
        )
        assert rc.cell is not None, f"round-trip failed for {cell.key}: {rc.reason}"
        assert rc.cell.key == cell.key


def test_every_matrix_persona_label_has_a_unique_key_within_its_segment():
    """If two persona labels in the SAME grid collapsed to one `persona_of` key,
    `derive_row_cell` would see more than one candidate and (correctly) refuse to guess
    -- but that refusal would then fire on every real row for that persona. This test
    catches the ambiguity at the matrix level, before it ever reaches a row."""
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    by_segment: dict[str, dict[str, str]] = {}
    for cell in m.cells.values():
        seen = by_segment.setdefault(cell.segment, {})
        key = persona_key_of_label(cell.persona)
        if key is None:
            continue
        if key in seen and seen[key] != cell.persona:
            pytest.fail(
                f"{cell.segment!r} grid: {seen[key]!r} and {cell.persona!r} both resolve "
                f"to persona key {key!r}"
            )
        seen[key] = cell.persona


def test_classify_rows_skips_suppressed_rows():
    m = parse_matrix(SIGNAL_GRID_MATRIX)
    rows = [
        {
            "email": "live@x.example",
            "title": "CISO",
            "segment": "Enterprise",
            "signal_column": "Compliance event (audit, breach)",
            "suppression": "",
        },
        {
            "email": "dead@x.example",
            "title": "CISO",
            "segment": "Enterprise",
            "signal_column": "Compliance event (audit, breach)",
            "suppression": "bounced",
        },
    ]
    out = classify_rows(m, rows)
    assert len(out) == 1
    assert out[0].email == "live@x.example"


def test_row_cell_is_a_frozen_dataclass_and_reason_is_always_a_string():
    rc = RowCell("a@x.example", None, "enterprise", "", None, reason="no signal")
    assert isinstance(rc, RowCell)
    with pytest.raises(Exception):
        rc.email = "b@x.example"  # frozen


# --- capability: the argument-monotone gate --------------------------------------------
#
# Why these exist: on 2026-08-23 seven drafted specs each passed the per-email gate at zero
# errors while six of the seven made the same argument. Every rule in the merge-render
# linter judges one spec against its own rows, so none of them could see it. This is the
# campaign-level counterpart, and the tests below are written so that a gate which stopped
# firing would fail rather than pass quietly.

_TAXONOMY = """\
# Product

## Capability taxonomy (7 groups)

| Group | What's in it |
|---|---|
| **Identity** | DID-per-agent |
| **Credentials & delegation** | secrets brokered at runtime |
| **Observability** | OTEL spans |
"""


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Credentials & delegation", "credentials-delegation"),
        ("Traffic management", "traffic-management"),
        ("Identity", "identity"),
        ("  Protocol & proxy  ", "protocol-proxy"),
        ("credentials_delegation", "credentials-delegation"),
        ("", ""),
    ],
)
def test_capability_slug_normalises_label_and_slug_onto_one_key(label, expected):
    assert capability_slug(label) == expected


def test_capability_vocab_reads_the_taxonomy_out_of_product_md(tmp_path):
    kb = tmp_path / "profiles" / "demo" / "knowledge"
    kb.mkdir(parents=True)
    (kb / "product.md").write_text(_TAXONOMY, encoding="utf-8")
    vocab = capability_vocab("demo", tmp_path / "profiles")
    assert set(vocab) == {"identity", "credentials-delegation", "observability"}
    # The label is kept for display; the header row is not a group.
    assert vocab["credentials-delegation"] == "Credentials & delegation"
    assert "group" not in vocab


def test_capability_vocab_is_empty_when_the_profile_has_no_taxonomy(tmp_path):
    kb = tmp_path / "profiles" / "demo" / "knowledge"
    kb.mkdir(parents=True)
    (kb / "product.md").write_text("# Product\n\nNo taxonomy here.\n", encoding="utf-8")
    # Empty turns the check OFF rather than failing every spec: a tenant that never wrote
    # a taxonomy has not declared a violation.
    assert capability_vocab("demo", tmp_path / "profiles") == {}


def test_declared_capability_reads_the_front_block_and_normalises_it():
    spec = "```\nargument_id: x\ncapability:  Credentials & delegation\n```\n"
    assert declared_capability(spec) == "credentials-delegation"


def test_a_spec_declaring_no_capability_returns_empty_not_an_error():
    assert declared_capability("```\nargument_id: x\n```\n") == ""


def test_an_unknown_capability_is_an_error_when_the_vocabulary_is_supplied():
    vocab = {"identity": "Identity"}
    with pytest.raises(ValueError, match="capability taxonomy"):
        declared_capability("```\ncapability: teleportation\n```\n", vocab)


def test_a_typo_cannot_buy_a_spec_an_extra_slot_under_the_cap():
    # The whole reason the field is validated: `credentials_delegation` and
    # `credentials-delegation` read as one group and count as two without normalisation.
    vocab = {"credentials-delegation": "Credentials & delegation"}
    assert declared_capability("```\ncapability: credentials_delegation\n```\n", vocab) == (
        "credentials-delegation"
    )


def test_capability_monotone_fires_above_the_cap():
    caps = {f"spec-{i}.md": "identity" for i in range(MAX_SPECS_PER_CAPABILITY + 1)}
    findings = capability_monotone(caps)
    assert len(findings) == 1
    assert findings[0].startswith("argument-monotone:")
    assert "identity" in findings[0]
    # The finding names the specs, so the reader knows which one to re-angle.
    assert "spec-0.md" in findings[0]


def test_capability_monotone_is_silent_at_the_cap():
    # Positive control for the test above: one fewer spec and the same call must go quiet,
    # so a rule that fired unconditionally would be caught here rather than look correct.
    caps = {f"spec-{i}.md": "identity" for i in range(MAX_SPECS_PER_CAPABILITY)}
    assert capability_monotone(caps) == []


def test_capability_monotone_counts_groups_independently():
    caps = {
        "a.md": "identity",
        "b.md": "identity",
        "c.md": "identity",
        "d.md": "payments",
        "e.md": "payments",
    }
    findings = capability_monotone(caps)
    assert len(findings) == 1
    assert "identity" in findings[0] and "payments" not in findings[0]


def test_undeclared_specs_are_not_counted_as_a_group():
    # An unmigrated spec is not a violation. If blanks were counted, every campaign written
    # before the field existed would trip the cap on its first run.
    caps = {"a.md": "", "b.md": "", "c.md": "", "d.md": "identity"}
    assert capability_monotone(caps) == []


def test_the_2026_08_23_pilot_shape_is_exactly_what_the_gate_catches():
    # All seven drafted specs argued Identity. Six shared the records-vs-proves SENTENCE and
    # the seventh reached the same group by a different route (a build-vs-buy standards
    # argument), which is precisely why the gate counts the declared capability rather than
    # the prose: at the sentence level this set looks like 6, at the argument level it is 7.
    caps = {f"identity-{i}.md": "identity" for i in range(7)}
    findings = capability_monotone(caps)
    assert len(findings) == 1
    assert "7 specs argue capability 'identity'" in findings[0]


# --- the mandated opener is scaffold, not a shared argument -----------------------------


def _opener_spec(argument: str, cta: str = "Would the write-up on that be useful?") -> str:
    """A spec whose touch carries the mandated opener plus one distinct argument line.

    ``cta`` is a parameter because beat 5 requires the ask to rotate across a campaign: four
    specs closing on one verbatim sentence is a real ``template-share`` hit, not scaffold.
    """
    return _spec(body=("Hi {{First Name}},\n>\n> {{Why Now}}.\n>\n> " + argument + "\n>\n> " + cta))


def test_the_mandated_opener_is_not_reported_as_a_shared_phrase():
    # Every spec carries `Hi {{First Name}},` + a bare `{{Why Now}}.` because beat 1 requires
    # it. Before this exclusion the phrase "hi first name why now ." fired on every campaign
    # written CORRECTLY, and a finding that is always true is one readers scroll past.
    specs = {
        f"s{i}.md": _opener_spec(a, cta)
        for i, (a, cta) in enumerate(
            [
                (
                    "The credential behind it outlives the integration that needed it.",
                    "Should I send the walkthrough of how brokering is wired?",
                ),
                (
                    "Nothing emits a per action record, so the answer is assembled by hand.",
                    "Is that already scoped, or still handled per request?",
                ),
                (
                    "An allowlist places the company, never the call that arrived.",
                    "Would the reference architecture on that edge be useful?",
                ),
                (
                    "A key authorises the call and carries no budget at all.",
                    "Would it help if I sent the note on bounding spend?",
                ),
            ]
        )
    }
    assert shared_phrases(specs) == []


def test_shared_copy_outside_the_opener_still_fires():
    # Positive control for the test above. Same four specs, but now they share an authored
    # sentence — if the exclusion were too broad this would also come back empty.
    shared = "The credential behind it outlives the integration that needed it."
    specs = {f"s{i}.md": _opener_spec(shared) for i in range(4)}
    found = shared_phrases(specs)
    assert found, "a genuinely shared argument line must still be reported"
    assert found[0].count == 4
    assert "outlives the integration" in found[0].phrase


def test_a_line_with_one_authored_word_beside_a_merge_tag_still_counts():
    # The exclusion is per line and requires the line to carry NO authored word. A real
    # sentence that happens to contain a merge tag must never be exempted.
    specs = {
        f"s{i}.md": _spec(
            body=(
                "Hi {{First Name}},\n>\n> {{Why Now}}.\n>\n> "
                "Every partner {{Company}} adds brings one more credential to rotate."
                "\n>\n> Would the write-up on that be useful?"
            )
        )
        for i in range(4)
    }
    found = shared_phrases(specs)
    assert found and any("credential" in p.phrase for p in found)
