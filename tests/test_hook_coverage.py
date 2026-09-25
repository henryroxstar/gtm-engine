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
    RowAxis,
    RowCell,
    SignalFit,
    UnknownHookCell,
    _cells_equal,
    _clean_cell,
    _sections,
    _split_row,
    argument_distinctness,
    audit_campaign,
    campaign_packs,
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
    seat_key_of_label,
    seat_of,
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


# --- the row axis (FR2, 2026-09-24) --------------------------------------------------
#
# A matrix row is only useful because a recipient can be put on it. Which resolver does that
# is decided by the file's own header cell, and the two key spaces are never mixed: `ceo` is
# a legal key in BOTH, so a report that resolved row labels as seats and recipients as
# personas would look right and count wrong, with no defect anywhere to find it.

SEAT_GRID_MATRIX = """---
source: generated
---
# Hook matrix — Northwind Systems

## Enterprise

| Signal → / Seat ↓ | shipping-agents × account-event | shipping-agents × public-event |
| --- | --- | --- |
| security | "One trust perimeter across two estates." | — |
| ceo | — | "Win the deal that slipped." |
"""

#: Header cell 0 naming BOTH axis words. Undecidable, so refused — see `RowAxis`.
AMBIGUOUS_GRID_MATRIX = SEAT_GRID_MATRIX.replace("Signal → / Seat ↓", "Signal → / Seat ↓ (persona)")

SEAT_ROWS_MATRIX = """---
source: manual
---
# Hook matrix — Halden Labs

## Product A

| id | Seat | Signal to open on | Hook angle |
|---|---|---|---|
| ha-sec-perimeter | security | Rebuilding auth per framework | "One perimeter, not four." |
"""


def test_a_persona_grid_still_reads_as_a_persona_axis_and_joins_on_personas():
    """**The negative control.** A hand-kept matrix is unchanged by the seat axis existing:
    same shape, same axis, same key for every row label, same key for every recipient."""
    m = parse_matrix(GRID_MATRIX)
    assert m.shape == MatrixShape.GRID
    assert m.row_axis == RowAxis.PERSONA
    assert m.row_key("CISO") == persona_key_of_label("CISO") == "ciso"
    assert m.recipient_key("Chief Information Security Officer") == persona_of(
        "Chief Information Security Officer"
    )
    # The seat resolver would answer differently for the SAME label — which is exactly why
    # the axis has to be read from the file and never guessed.
    assert seat_key_of_label("CISO") == "security" != m.row_key("CISO")


def test_a_seat_grid_reads_as_a_seat_axis_and_joins_recipients_by_seat():
    m = parse_matrix(SEAT_GRID_MATRIX)
    assert m.shape == MatrixShape.GRID
    assert m.row_axis == RowAxis.SEAT
    # Read on the persona axis these rows resolve to NOTHING, which is the failure this
    # dispatch exists to prevent: every recipient booked unassignable, report reads zero.
    assert persona_key_of_label("security") is None
    assert m.row_key("security") == "security"
    assert m.recipient_key("Chief Information Security Officer") == "security"
    assert m.row_key("security") == m.recipient_key("Head of Security")
    assert not m.unmapped_personas()


def test_a_seat_row_written_as_a_title_resolves_through_persona_to_seat():
    """Rung 2 of `seat_key_of_label`: a label that is a title, not a seat name. Uses the one
    persona→seat mapping (`RoleVocabulary.persona_to_seat`), never a second table."""
    assert seat_key_of_label("CISO") == "security"
    assert seat_key_of_label("Chief Technology Officer") == "cto"
    assert seat_key_of_label("Head of Interpretive Dance") is None


def test_a_header_naming_both_axes_is_refused_rather_than_guessed():
    m = parse_matrix(AMBIGUOUS_GRID_MATRIX)
    assert m.shape == MatrixShape.UNSUPPORTED
    assert not m.ok and m.cells == {}
    assert "BOTH" in m.reason and "seat" in m.reason


def test_a_rows_matrix_can_carry_a_seat_column():
    """The axis is read from the same column head the shape came from, for every shape that
    has one — otherwise a file could be detected on one word and parsed off the other."""
    m = parse_matrix(SEAT_ROWS_MATRIX)
    assert m.shape == MatrixShape.ROWS
    assert m.row_axis == RowAxis.SEAT
    assert m.find("security", "Rebuilding auth per framework") is not None


def test_a_sections_matrix_keeps_the_persona_axis():
    """Section headings carry no axis word, and every `sections` matrix in this repo heads
    its sections with a persona. Defaulting elsewhere would silently re-key a live tenant."""
    assert parse_matrix(SECTIONS_MATRIX).row_axis == RowAxis.PERSONA


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
    assert persona_of("Chief Information Officer") == "cio"


def test_cio_and_cloud_architect_are_distinct_personas_on_one_seat():
    """The 2026-09-21 split: 87 titles / 5,262 people in the `architect` seat were CIOs
    and 3 titles / 100 people were architects, so the seat reported technical coverage of
    the ICP's named "Influencer — Security architect" that it did not have.

    The property that makes the split safe is that the SEAT is unchanged — no new copy is
    owed — while the persona axis stops reporting one number for two buyers. Ordering is
    load-bearing in the other direction too: "Chief Information Architect" carries BOTH a
    `chief information` cue and an `architect` cue, and must seat as the architect it is.
    """
    assert persona_of("Chief Information Officer") == "cio"
    assert persona_of("Enterprise Architect") == "cloud-architect"
    assert persona_of("Chief Information Architect") == "cloud-architect"
    assert seat_of("Chief Information Officer") == seat_of("Enterprise Architect") == "architect"


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


# --- resolving the cell from the declared angle (FR3, 2026-09-24) ---------------------
#
# FR2 removed `hook_cell:` from the shipped spec template and made the cell a DERIVATION of
# the declared `angle:`. Nothing here read `angle:`, so every spec written to the new
# template counted as undeclared and `angle-missing` (then named `hook-cell-missing`)
# reported the whole campaign — a coverage report that lies about the artifact the same
# skill just produced.

#: A miniature of what `gtm_core.messaging.matrix_view.render` now generates: SEAT rows,
#: `premise × opener_kind` columns, one section per segment. Invented (R9).
_SEAT_MATRIX = """---
source: generated
---
# Outreach hook matrix — Northwind Systems

## builder

| Signal → / Seat ↓ | agents-in-path × account-event | agents-in-path × public-event |
| --- | --- | --- |
| ceo | "Prove whose authority the agent carries." | — |
| cto | "One rotation point instead of eighty secrets." | — |
"""


def _registry(angle_id: str = "handoff-evidence-ceo", seat: str = "ceo"):
    """A two-angle fact registry, built in memory. No tenant file is read (R9)."""
    from gtm_core.messaging.registry import Angle, Claim, Proof, Registry

    return Registry(
        claims={
            "handoff-attested": Claim(
                id="handoff-attested",
                group="identity",
                status="verified",
                statement="Each call writes a signed audit entry.",
            )
        },
        proof={
            "market-governance-anchor": Proof(
                id="market-governance-anchor",
                kind="anchor",
                figure_kind="none",
                statement="The market's AI governance guidance names agent accountability.",
                market="SG",
            )
        },
        angles={
            angle_id: Angle(
                id=angle_id,
                seat=seat,
                premise="agents-in-path",
                claim="handoff-attested",
                proof="market-governance-anchor",
                opener_kind="account-event",
                summary="Prove whose authority the agent carries.",
                status="live",
            )
        },
        seats={seat: (angle_id,)},
    )


def test_resolve_declared_cell_derives_the_cell_from_the_declared_angle():
    """The migrated shape: `angle:` only, no `hook_cell:` anywhere in the spec."""
    from gtm_core.hook_coverage import resolve_declared_cell

    reg = _registry()
    m = parse_matrix(_SEAT_MATRIX)
    d = resolve_declared_cell(_spec("angle:      handoff-evidence-ceo\n"), m, reg)
    assert d is not None, "a spec written to the shipped template resolves no cell"
    assert (d.persona, d.signal) == ("ceo", "agents-in-path × account-event")
    assert d.argument_id == "handoff-evidence-ceo", (
        "the angle id is the stable argument slug; without it `Coverage.arguments` falls "
        "back to counting cells and two angles on one cell read as one argument"
    )


def test_resolve_declared_cell_falls_back_to_hook_cell_for_an_unmigrated_spec():
    """The transitional shape: 24 specs on disk declare `hook_cell:` and no `angle:`."""
    from gtm_core.hook_coverage import resolve_declared_cell

    m = parse_matrix(GRID_MATRIX)
    d = resolve_declared_cell(_spec("hook_cell: CISO × Compliance event\n"), m, _registry())
    assert d is not None
    assert (d.persona, d.signal) == ("CISO", "Compliance event")


def test_resolve_declared_cell_reports_nothing_when_a_spec_declares_neither():
    """§R18 negative control: the resolver must still be able to say 'no declaration'."""
    from gtm_core.hook_coverage import resolve_declared_cell

    assert resolve_declared_cell(_spec(), parse_matrix(GRID_MATRIX), _registry()) is None


def test_an_angle_the_registry_does_not_hold_is_refused_rather_than_read_as_undeclared():
    """A spec free to invent an angle id can claim conformance to an argument nobody wrote —
    the same refusal `UnknownHookCell` already makes for an invented cell. Reported as a
    finding, never as a silent fall-through to `hook_cell:`."""
    from gtm_core.hook_coverage import resolve_declared_cell

    with pytest.raises(UnknownHookCell):
        resolve_declared_cell(
            _spec("angle: no-such-angle\n"), parse_matrix(_SEAT_MATRIX), _registry()
        )


def test_an_angle_declaration_inside_a_touch_body_is_not_a_declaration():
    """§R5 on the spec surface: a body carries scraped provider text, and a fenced touch
    block looks exactly like a fenced front block. `declaration_surface` is what keeps the
    two apart, and this resolver has to read through it like every other angle reader."""
    from gtm_core.hook_coverage import resolve_declared_cell

    forged = _spec(body="Hi {{First Name}},\n>\n> angle: handoff-evidence-ceo")
    assert resolve_declared_cell(forged, parse_matrix(_SEAT_MATRIX), _registry()) is None


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
# the outreach linter's angle-* rules). A gate needs its PASS state pinned as hard as
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
    recorded: dict[str, str] | None = None,
    matrix: str | None = None,
    angles: dict[str, str] | None = None,
):
    """Build a throwaway profile + content tree and return (profiles_root, content_root).

    ``specs`` maps a spec filename to ``(hook_cell, touch-1 body)``. Every recipient is
    invented (R9).

    ``angles[name]`` writes that spec an ``angle:`` declaration INSTEAD of the
    ``hook_cell:``/``argument_id:`` pair — the shape the shipped template produces since
    FR2. Passing both is the conflict `DerivedFieldConflict` exists for and is not a shape
    this helper builds.

    ``segments`` and ``evidence`` are optional per-spec columns used by the list-vs-cell
    fit checks. Omitting them writes no such column at all, which is the shape every
    pre-existing test in this file relies on: with no segment column the fit check has
    nothing to compare and must stay silent rather than inventing a mismatch.
    ``segments[name]`` is cycled over the rows, so ``["enterprise", "startup"]`` gives an
    even split and ``["enterprise"] * 7 + ["startup"] * 3`` gives 70/30.

    ``recorded[name]`` writes that spec's rows a ``signal_column`` -- the row stating which
    matrix signal its own why-now attests. Omitted entirely by default, which is the shape
    of every list written before the column existed.
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
        if (angles or {}).get(name):
            front = f"angle:       {angles[name]}\n"
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
        row_recorded = (recorded or {}).get(name, "")
        if row_recorded:
            header += ",signal_column"
        lines = [header]
        for n in range(rows_per_spec):
            row = f"Ada,Okonkwo,ada{n}@halden.example,{title},Halden Systems,halden.example,"
            if seg_cycle:
                row += f",{seg_cycle[n % len(seg_cycle)]}"
            if row_evidence:
                row += f",{row_evidence}"
            if row_recorded:
                row += f",{row_recorded}"
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
    assert any(f.startswith("angle-missing") for f in cov.findings)


# --- the audit reads `angle:`, and still reads `hook_cell:` (FR3, 2026-09-24) ---------


def test_a_campaign_of_angle_declaring_specs_is_not_reported_undeclared(tmp_path):
    """The shipped template writes `angle:` and no `hook_cell:`. Before this, every spec
    written to it was counted as undeclared by the very report the same skill runs."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {
            "spec-ceo.md": ("", "Regulators now ask which agent took an action on a record."),
            "spec-cto.md": ("", "Eight agents across ten services is eighty secrets in memory."),
        },
        angles={"spec-ceo.md": "handoff-evidence-ceo", "spec-cto.md": "handoff-evidence-cto"},
        matrix=_SEAT_MATRIX,
    )
    reg = _registry()
    reg.angles.update(_registry("handoff-evidence-cto", seat="cto").angles)
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        registry=reg,
        min_arguments=2,
        min_recipients=999,
    )
    assert cov.declared_count == 2, cov.findings
    assert cov.arguments == 2
    assert not any(f.startswith("angle-missing") for f in cov.findings), cov.findings


def test_a_spec_declaring_an_angle_the_registry_lacks_reports_angle_unknown(tmp_path):
    """The id, not just the message. FR3 retired `hook-cell-unknown` into `angle-unknown`
    on the copy-linter surface, and this report kept raising the retired name for a day —
    one fact answering to two ids across two surfaces, which is what
    `rule_lifecycle_report` cannot bucket. The positive control for the rename; the
    negative control is the test above, which asserts the finding is absent."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {"spec-ceo.md": ("", "Regulators now ask which agent took an action on a record.")},
        angles={"spec-ceo.md": "handoff-evidence-invented"},
        matrix=_SEAT_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        registry=_registry(),
        min_recipients=999,
    )
    assert [f for f in cov.findings if f.startswith("angle-unknown")], cov.findings
    assert not [f for f in cov.findings if f.startswith("hook-cell-")], cov.findings


def test_a_campaign_of_hook_cell_specs_is_unchanged_by_the_angle_path(tmp_path):
    """The transitional control. Every spec on disk today declares `hook_cell:` and no
    `angle:`; a rewire that resolved only angles would turn the live report red."""
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
        registry=_registry(),
        min_arguments=2,
        min_recipients=40,
    )
    assert cov.declared_count == 2, cov.findings
    assert not cov.failed, cov.findings


def test_a_campaign_declaring_an_angle_the_registry_lacks_is_a_named_finding(tmp_path):
    """§R18: the angle path must be able to REFUSE, not only to resolve. Without this an
    invented angle id would fall through to 'declares no cell' and read as an unmigrated
    spec — the fail-open `UndeclaredAngle` was written to end."""
    from gtm_core.hook_coverage import audit_campaign

    profiles, content = _campaign(
        tmp_path,
        {"spec-ceo.md": ("", "Regulators now ask which agent took an action on a record.")},
        angles={"spec-ceo.md": "an-angle-nobody-wrote"},
        matrix=_SEAT_MATRIX,
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        registry=_registry(),
        min_recipients=999,
    )
    assert any("an-angle-nobody-wrote" in f for f in cov.findings), cov.findings


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
    """``angle-missing`` already covers it; restating it as a fit failure double-counts."""
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
    """The Forgeworks shape: one product on one platform under a plurality claim."""
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


# --- the resolver rung ladder (2026-09-23) -----------------------------------
#
# `load_premise_vocab` hand-built `<root>/<profile>/knowledge/<file>` while `capability_vocab`
# two functions below it used `resolve_knowledge_file`. So the premise vocabulary reached
# neither the product rung nor the overlay rung: a tenant who wrote a product-level vocabulary
# was silently served the profile-level one, with nothing reporting the substitution. These
# three tests are the discriminating control - point the function back at the hand-built path
# and the first one goes red while the other two stay green.


def test_a_product_level_vocab_wins_over_the_profile_level_one(tmp_path):
    """The rung that did not exist. Two files, same name, different arity."""
    (tmp_path / "acme" / "knowledge").mkdir(parents=True)
    (tmp_path / "acme" / "knowledge" / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 1\nterms = ["agent"]\n',
        encoding="utf-8",
    )
    (tmp_path / "acme" / "products" / "widgets").mkdir(parents=True)
    (tmp_path / "acme" / "products" / "widgets" / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 2\nterms = ["agent", "assistant"]\n',
        encoding="utf-8",
    )
    profile_level = load_premise_vocab("acme", tmp_path)
    product_level = load_premise_vocab("acme", tmp_path, product="widgets")
    assert profile_level["ships-agents"].min_distinct == 1
    assert product_level["ships-agents"].min_distinct == 2, (
        "a product-level premise-vocab.toml did not win - load_premise_vocab is not going "
        "through resolve_knowledge_file, so the product rung is unreachable"
    )


def test_a_product_with_no_vocab_of_its_own_falls_back_to_the_profile(tmp_path):
    """The rung ladder's middle step is a fallback, not a requirement: a product that ships no
    vocabulary of its own inherits the tenant's rather than losing the check entirely."""
    (tmp_path / "acme" / "knowledge").mkdir(parents=True)
    (tmp_path / "acme" / "knowledge" / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 1\nterms = ["agent"]\n',
        encoding="utf-8",
    )
    (tmp_path / "acme" / "products" / "widgets").mkdir(parents=True)
    vocab = load_premise_vocab("acme", tmp_path, product="widgets")
    assert vocab["ships-agents"].min_distinct == 1


def test_a_traversing_product_slug_is_refused_not_resolved(tmp_path):
    """The rung the resolver adds also brings its guard: a product segment is `_safe_segment`ed,
    so a slug is a bare name or it is nothing. Returning `{}` rather than raising keeps this
    consistent with every other unreadable-vocab case in this module."""
    (tmp_path / "acme" / "knowledge").mkdir(parents=True)
    (tmp_path / "acme" / "knowledge" / "premise-vocab.toml").write_text(
        'schema = 1\n[premise.ships-agents]\nmin_distinct = 1\nterms = ["agent"]\n',
        encoding="utf-8",
    )
    assert load_premise_vocab("acme", tmp_path, product="../../etc") == {}


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

    'Riverbend Health Partners' carries a premise term on its letterhead. Matching it would
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
            "company": "Riverbend Health Partners",
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
            "company": "Riverbend Health Partners",
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


# --- capability groups come from `claims.toml` once a tenant ships one (FR2 Task 2.8) ---
#
# The taxonomy in `product.md` is prose; `claims.toml` is the file the outbound linter, the
# generated matrix and the angle resolver all derive from. Two answers to "which capability
# groups exist" drift, and the drift is invisible because both keep answering. The tests below
# pin the rung order AND the failure posture — a broken `claims.toml` must not resolve to an
# empty vocabulary, because an empty vocabulary turns `capability-unknown` off on every spec in
# the campaign: a gate that passes by finding nothing.

_CLAIMS_TOML = """\
[[claim]]
id = "trail-signed"
group = "observability"
status = "design-target"
statement = "Each entry in the trail is signed."

[[claim]]
id = "per-tool-scope"
group = "credentials-delegation"
status = "design-target"
statement = "A tool's scope is brokered per call."
"""


def _registry_profile(tmp_path, name: str, claims: str | None, product: str | None = None):
    """A tmp ``profiles/`` root holding one profile, and return the root.

    ``registry.load`` is all-or-nothing and requires all three tables to exist, so the two it
    is not being asked about here are written empty rather than omitted.
    """
    kb = tmp_path / name / "demo" / "knowledge"
    kb.mkdir(parents=True)
    if claims is not None:
        (kb / "claims.toml").write_text(claims, encoding="utf-8")
        (kb / "proof.toml").write_text("", encoding="utf-8")
        (kb / "angles.toml").write_text("", encoding="utf-8")
    if product is not None:
        (kb / "product.md").write_text(product, encoding="utf-8")
    return tmp_path / name


def test_capability_vocab_prefers_claims_toml_over_the_product_md_taxonomy(tmp_path):
    """Both files present: the facts win, and the prose is not consulted at all.

    ``product.md`` here declares `identity` — a group `claims.toml` does not have — so an
    implementation that merged the two, or that read the wrong one, cannot pass.
    """
    root = _registry_profile(tmp_path, "both", _CLAIMS_TOML, _TAXONOMY)
    assert set(capability_vocab("demo", root)) == {"observability", "credentials-delegation"}


def test_capability_vocab_without_claims_toml_keeps_todays_behaviour(tmp_path):
    """The negative control that matters: nothing regresses for a profile that has not
    migrated. Same fixture, same assertion, minus `claims.toml`."""
    root = _registry_profile(tmp_path, "unmigrated", None, _TAXONOMY)
    assert set(capability_vocab("demo", root)) == {
        "identity",
        "credentials-delegation",
        "observability",
    }


def test_a_broken_claims_toml_raises_rather_than_emptying_the_vocabulary(tmp_path):
    """A `verified` claim with no source is a registry the loader refuses.

    Asserting the raise is only half of it. The two failure modes worth ruling out are silent:
    returning `{}` (every spec passes `capability-unknown`) and falling back to `product.md`
    (answering from a file the tenant stopped maintaining). Both would look like success here,
    so the test asserts the *call*, not a value.
    """
    from gtm_core.messaging.registry import RegistryError

    broken = _CLAIMS_TOML.replace('status = "design-target"', 'status = "verified"', 1)
    root = _registry_profile(tmp_path, "broken", broken, _TAXONOMY)
    with pytest.raises(RegistryError) as exc:
        capability_vocab("demo", root)
    assert "claims.toml" in str(exc.value) and "trail-signed" in str(exc.value)

    # Negative control: the same fixture with the one defect repaired loads, and returns the
    # claims groups — so the refusal above is about the defect, not about the fixture.
    fixed = broken.replace(
        'statement = "Each entry in the trail is signed."',
        'statement = "Each entry in the trail is signed."\nsource = "knowledge/x.md:1"',
        1,
    )
    ok = _registry_profile(tmp_path, "repaired", fixed, _TAXONOMY)
    assert set(capability_vocab("demo", ok)) == {"observability", "credentials-delegation"}


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


# --- a row that STATES its signal is not a row to guess about -------------------------
#
# Live case, 2026-08-30: a one-row spec declaring `Head of AI Platform × M&A / consolidation`
# passed `cell-row-mismatch` (the equality test) and was warned `0%` unattested by
# `cell-signal-fit` in the same report, because the row's evidence says "acquisition" and the
# label says "M&A". Two checks, one row, opposite answers. Term matching is the documented-weak
# fallback (`_discriminating_terms`); it must not overrule a fact the row recorded.


def _recorded_campaign(tmp_path, recorded: str | None):
    """One spec, one row whose evidence contains NONE of the declared label's terms."""
    return _campaign(
        tmp_path,
        {
            "spec-security.md": (
                "CISO × MCP/A2A/AP2 entering architecture",
                "Once those agents call across an org boundary, a network credential "
                "proves the company and stays silent on which agent is calling today.",
            ),
        },
        rows_per_spec=4,
        segments={"spec-security.md": ["enterprise"]},
        evidence={"spec-security.md": "completed its acquisition of a sensing company"},
        recorded=({"spec-security.md": recorded} if recorded else None),
        matrix=PARENTHETICAL_MATRIX,
    )


SEAT_GRID_FOR_CAMPAIGN = """\
# Hook matrix — Northwind Systems

## Enterprise

| Signal → / Seat ↓ | Agents in the estate | Partner agents |
|---|---|---|
| security | "One trust perimeter across two estates." | "Admit a partner's agents safely." |
| ceo | "Win the deal that slipped." | "The responsibility narrative." |
"""


def test_a_seat_matrix_counts_recipients_by_seat_not_by_persona(tmp_path):
    """On a seat matrix, "which rows hold recipients" is a SEAT question.

    Two lists: one of security seats, one of a persona the vocabulary recognises and
    deliberately gives no seat (a resolver-only persona). Three things must follow, and each
    is a place the old persona-keyed answer would be confidently wrong:

    1. ``axis_counts`` is keyed by the matrix's rows, so it can be compared to a row label at
       all. Keyed by persona it would hold ``ciso``/``finops`` — neither of which is a row in
       this matrix — and every one of them would read as an unaddressed gap.
    2. ``persona-unaddressed`` therefore stays silent: the one populated row IS argued.
    3. A recipient with a persona but no seat is counted as *unplaceable*, not as placed.
       Reading that off the persona bucket would say 0 and lose 50 rows from the one line
       that totals what no cell can hold.
    """
    profiles, content = _campaign(
        tmp_path,
        {
            "spec-sec.md": (
                "security × Agents in the estate",
                "Regulators now ask which agent took an action, and the login record "
                "cannot answer that question for anybody reviewing the estate.",
            ),
            "spec-fin.md": (
                "FinOps lead × Partner agents",
                "Spend per agent is invisible when eight of them share one key, so the "
                "bill arrives as a single number nobody can allocate to a team.",
            ),
        },
        matrix=SEAT_GRID_FOR_CAMPAIGN,
    )
    cov = _audit(profiles, content)
    assert cov.matrix.row_axis == RowAxis.SEAT
    assert dict(cov.axis_counts) == {"security": 50}, dict(cov.axis_counts)
    assert dict(cov.personas) == {"ciso": 50, "finops": 50}, "the persona view is still reported"
    assert cov.unresolved_axis_rows == 50, "the seatless list is 50 rows no row can hold"
    assert not [f for f in cov.findings if f.startswith("persona-unaddressed")], cov.findings
    assert "unresolved seat" in render(cov), "the report must name the axis it counted on"


def _audit(profiles, content):
    from gtm_core.hook_coverage import audit_campaign

    return audit_campaign(
        "northwind",
        "test-campaign",
        content_root=content,
        profiles_root=profiles,
        min_arguments=1,
    )


def test_a_recorded_signal_column_is_decided_by_equality_not_by_term_matching(tmp_path):
    cov = _audit(*_recorded_campaign(tmp_path, "MCP/A2A/AP2 entering architecture"))
    fit = cov.signal_fits["spec-security.md"]
    assert fit.recorded == 4 and fit.inferred == 0
    assert fit.share == 1.0, "the rows state the declared signal; equality decides them"
    assert not [w for w in cov.warnings if w.startswith("cell-signal-fit")]
    assert "recorded" in render(cov), "the table must say the number came from recorded rows"


def test_without_the_recorded_column_the_same_rows_fall_back_to_the_heuristic(tmp_path):
    """The negative control, and the migration guarantee in one: identical evidence, no
    recorded column, and the pre-2026-08-30 behaviour is reproduced exactly."""
    cov = _audit(*_recorded_campaign(tmp_path, None))
    fit = cov.signal_fits["spec-security.md"]
    assert fit.recorded == 0 and fit.inferred == 4
    assert fit.share == 0.0, "'acquisition' matches no term in the MCP label"
    assert [w for w in cov.warnings if w.startswith("cell-signal-fit")]


def test_a_recorded_signal_that_disagrees_is_an_error_not_a_duplicate_warning(tmp_path):
    """Division of labour: a row recording the WRONG signal is `cell-row-mismatch` (ERROR).
    `cell-signal-fit` must not also fire — one defect, one finding."""
    cov = _audit(*_recorded_campaign(tmp_path, "M&A / consolidation"))
    fit = cov.signal_fits["spec-security.md"]
    assert fit.recorded == 4 and fit.attested == 0
    assert not [w for w in cov.warnings if w.startswith("cell-signal-fit")]
    assert [f for f in cov.findings if f.startswith("cell-row-mismatch")], cov.findings


# --- the committed corpus itself (mirrors test_knowledge_meta's corpus gate) ----------
#
# Every other test in this file runs on an invented miniature (R9). These two run on the
# REAL matrices, because the ways a matrix edit breaks are invisible to a fixture:
# `_parse_grid_section` uses `zip(..., strict=False)`, so widening a header without
# widening every persona row drops cells silently; and `persona_of` is a closed
# vocabulary, so a new row label matching no cue makes `audit_campaign` fail. Neither was
# asserted anywhere against a file a tenant actually ships — before these, adding a bad
# row or column to a live profile failed exactly zero tests.
#
# They assert PROPERTIES, not counts: a tenant may hold any number of cells, and the four
# shapes are all legitimate — grid, sections and rows are each in use, and the starter hook
# bank the profile template ships is correctly called unsupported by `_detect_shape`, because
# a bank of angles cannot name a persona x signal cell. No tenant name, hook text or persona
# label is reproduced here: this file ships in the OSS carve and the roster does not.


#: A KNOWN, pre-existing persona-vocabulary gap, recorded as a budget rather than hidden.
#: `_PERSONA_RULES` (tests/linter/outreach_pack_linter.py) is a single GLOBAL tuple shaped
#: around one tenant's B2B-infra roles — there is no per-profile parameterisation — so a
#: tenant whose buyers are clinical roles, or independent-operator roles, has personas that
#: can never normalise, and `hook_coverage` reports persona-unmapped for them today.
#: Fixing it means making the vocabulary tenant-scoped, which is its own change.
#:
#: Kept as an aggregate budget (no tenant is named here — this file ships in the OSS carve,
#: where the tenant roster does not). Measured 2026-09-01: 8 unmapped labels across the
#: committed profiles, 0 of them in the grid the vocabulary was written for. The budget
#: RATCHETS: closing a gap without lowering this number fails, and a new one fails too.
#:
#: Raised 8 -> 9 on 2026-09-08, and the extra entry is a gap that was always there. Cues
#: used to be matched as bare substrings, so `cto` matched inside the word `director` and
#: every title carrying it resolved to the CTO persona — including one clinical row label
#: naming a director of operations, which therefore looked mapped while being mapped to
#: the wrong seat entirely. Word-boundary matching (`outreach_pack_linter._cue_re`) removed
#: the false mapping and the real gap surfaced underneath it. A budget that a bug was
#: holding down was never measuring what it claimed to.
_KNOWN_UNMAPPED_BUDGET = 9


def _live_matrices():
    from gtm_core.paths import resolve_profiles_root

    root = resolve_profiles_root()
    for path in sorted(root.glob("*/knowledge/hook-matrix.md")):
        yield path.parent.parent.name, path


@pytest.mark.private_tree  # carve ships profiles/_template only; its hook bank is not a matrix
def test_every_parsing_hook_matrix_maps_every_persona_onto_the_role_vocabulary():
    """A row label matching no `persona_of` cue is a `persona-unmapped` finding that makes
    the `hook_coverage` CLI exit 1 — the CLI the email-sequence skill runs before staging.

    Scoped to matrices that parse: a tenant shipping the starter hook bank has no persona
    axis yet, which is a different asset rather than a broken one.
    """
    checked = 0
    unmapped: list[str] = []
    for profile, path in _live_matrices():
        m = parse_matrix(path)
        if not m.ok:
            continue
        assert m.cells, f"{profile}: matrix reports ok but parsed to zero cells"
        unmapped.extend(f"{profile}:{label}" for label in m.unmapped_personas())
        checked += 1
    assert checked, "no parsing hook-matrix.md found — this test would pass vacuously"
    assert len(unmapped) <= _KNOWN_UNMAPPED_BUDGET, (
        f"{len(unmapped)} persona label(s) normalise onto no role in `_PERSONA_RULES`, over "
        f"the known budget of {_KNOWN_UNMAPPED_BUDGET}: {sorted(unmapped)}. Recipients could "
        "never be attributed to them — add a cue there in the same change as the row."
    )
    assert len(unmapped) == _KNOWN_UNMAPPED_BUDGET, (
        f"the known persona gap shrank to {len(unmapped)} — lower _KNOWN_UNMAPPED_BUDGET to "
        "match, so the gate keeps its teeth."
    )


@pytest.mark.private_tree  # carve ships profiles/_template only; its hook bank is not a matrix
def test_a_hand_kept_matrix_resolves_exactly_as_it_did_before_the_seat_axis():
    """**The live negative control for the axis fork.** No tenant's numbers move because a
    second key space now exists.

    Every committed matrix whose header names no seat is read on the persona axis, and every
    one of its row labels resolves through the same function the module used before
    2026-09-24. Paired with the assertion that a seat-axis matrix IS committed: a control
    over a fork nobody takes proves nothing (§R18).
    """
    checked = 0
    axes = {}
    for profile, path in _live_matrices():
        m = parse_matrix(path, profile=profile)
        axes[profile] = m.row_axis
        if not m.ok or m.row_axis != RowAxis.PERSONA:
            continue
        for label in m.personas:
            assert m.row_key(label) == persona_key_of_label(label, profile), (
                f"{profile}: row label {label!r} now resolves differently on the persona "
                "axis — a hand-kept matrix must be byte-for-byte unaffected by the fork"
            )
        checked += 1
    assert checked, "no persona-axis matrix committed — this control would pass vacuously"
    assert RowAxis.SEAT in axes.values(), (
        "no seat-axis matrix is committed, so this control is comparing the persona path "
        f"against itself and would pass with the fork deleted: {axes}"
    )


#: Written-out blanks, restated here on purpose rather than imported from
#: ``gtm_core.hook_coverage.matrix``. This test compares the PARSE against the SOURCE;
#: importing the parser's own placeholder set would make that comparison compare a function
#: with itself, and a widening of that set — the change that would start swallowing real
#: hooks — would be invisible here (§R18).
_WRITTEN_BLANKS = {"", "\u2014", "\u2013", "-", "--", "n/a", "na", "tbd", "?"}


def _grid_tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """``[(segment, column heads, data rows)]`` — the raw markdown, before any parsing."""
    out = []
    for heading, body in _sections(text):
        header: list[str] | None = None
        rows: list[list[str]] = []
        for line in body.splitlines():
            row = _split_row(line)
            if row is None:
                continue
            if header is None:
                header = row
            else:
                rows.append(row)
        if header and rows:
            out.append((heading or "default", [_clean_cell(c) for c in header[1:]], rows))
    return out


@pytest.mark.private_tree  # carve ships profiles/_template only; its hook bank is not a matrix
def test_every_committed_grid_matrix_keeps_every_hole_visible():
    """Every grid coordinate reaches the parser, and a hole arrives AS a hole.

    **This replaces a 100%-density assertion, and the reasoning matters.** Until 2026-09-24
    a hole in a grid was a defect, so "every row-label x signal pair is filled" was both the
    editorial contract and a proxy for the mechanical one. FR2 ended it on both sides.
    ``hook-matrix.md`` is now generated from ``angles.toml``, and a seat with no angle for a
    column renders ``—`` *deliberately* — omit the row and the gap is invisible to the human
    reading the file (``gtm_core/messaging/matrix_view.py``). The same change taught
    ``parse_matrix`` to read ``—`` as no cell, which applies to hand-kept matrices too. So
    parsed density is no longer a property ANY matrix in this repo can hold, generated or
    hand-kept; asserting it would be asserting something nothing can satisfy, which is a
    different thing from exempting one tenant from it.

    What was load-bearing underneath it survives intact and is what is asserted here — the
    mechanical failure the old docstring named: ``zip(signals, row[1:], strict=False)`` drops
    a half-widened row with **no diagnostic**, so a row that lost a column reads as a smaller
    grid rather than an error and every coordinate past the break silently ceases to exist.
    Two independent assertions, both against the raw markdown:

    1. **Rectangular** — every data row carries exactly as many cells as the header. This is
       the one ``zip(strict=False)`` cannot report.
    2. **The parse drops exactly the written blanks** — every coordinate the parser did not
       produce is a source cell that says nothing (:data:`_WRITTEN_BLANKS`), and every
       coordinate it did produce had something there. A hook swallowed by a widened
       placeholder rule, or a hole promoted to coverage, fails here.
    """
    checked = 0
    for profile, path in _live_matrices():
        text = path.read_text(encoding="utf-8")
        m = parse_matrix(text)
        if m.shape != MatrixShape.GRID:
            continue
        for segment, columns, rows in _grid_tables(text):
            for row in rows:
                assert len(row) - 1 == len(columns), (
                    f"{profile}/{segment}: row {_clean_cell(row[0])!r} carries "
                    f"{len(row) - 1} cell(s) for {len(columns)} column(s). "
                    "`zip(strict=False)` drops the difference with no diagnostic \u2014 widen "
                    "the header AND every row together."
                )
            source_filled = {
                (_clean_cell(row[0]), col)
                for row in rows
                if _clean_cell(row[0])
                for col, cell in zip(columns, row[1:], strict=True)
                if col and _clean_cell(cell).lower() not in _WRITTEN_BLANKS
            }
            parsed = {
                (c.persona, c.signal)
                for c in m.cells.values()
                if (c.segment or "default") == segment
            }
            assert parsed == source_filled, (
                f"{profile}/{segment}: the parse and the file disagree about which "
                f"coordinates carry a hook \u2014 only in the file "
                f"{sorted(source_filled - parsed)[:3]}, only in the parse "
                f"{sorted(parsed - source_filled)[:3]}. A hole that parses as coverage reads "
                "the report high by exactly the number of holes."
            )
        checked += 1
    assert checked, "no grid-shaped hook-matrix.md found \u2014 this test would pass vacuously"


# --- 1:1 outreach packs (added 2026-09-04) ------------------------------------------
#
# `argument-monotone` read `cells.toml`, which is the SEQUENCE row set. A Tier-A 1:1 pack
# is never in it, so the campaign-level cap was structurally blind to the artifact it most
# needed to see: on 2026-09-04 five packs in one campaign all argued `identity` while every
# per-file linter gate passed at zero errors. That was a scope gap from the field's
# introduction (2026-08-24), not a regression.


def _packs(tmp_path, packs: dict[str, str], date: str = "20260904"):
    """Write ``{account-slug: capability}`` as 1:1 packs under a throwaway content root."""
    profiles = tmp_path / "profiles"
    (profiles / "northwind" / "knowledge").mkdir(parents=True)
    (profiles / "northwind" / "knowledge" / "hook-matrix.md").write_text(
        _MATRIX_FOR_CAMPAIGN, encoding="utf-8"
    )
    (profiles / "northwind" / "knowledge" / "product.md").write_text(
        "## Capability taxonomy\n\n"
        "| Group | Failure |\n|---|---|\n"
        "| Identity | the action cannot be attributed |\n"
        "| Observability | no evidence artifact |\n"
        "| Payments | cannot be metered |\n",
        encoding="utf-8",
    )
    seq = tmp_path / "content" / "northwind" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "cells.toml").write_text("", encoding="utf-8")
    accounts = tmp_path / "content" / "northwind" / "accounts"
    for slug, capability in packs.items():
        (accounts / slug).mkdir(parents=True)
        (accounts / slug / f"prospects-{date}-outreach-{slug}.md").write_text(
            f"# Outreach Pack — {slug}\n\nRules-Version: 2026-09-04\n\n"
            f"**Capability:** {capability}\n\n> Hi Dana,\n>\n> body.\n",
            encoding="utf-8",
        )
    return profiles, tmp_path / "content"


def test_packs_are_invisible_without_the_flag(tmp_path):
    """The default must not silently widen a report about staged copy to include packs."""
    profiles, content = _packs(tmp_path, {"acme": "identity", "borea": "identity"})
    cov = audit_campaign(
        "northwind", "test-campaign-20260904", content_root=content, profiles_root=profiles
    )
    assert cov.packs == set()
    assert cov.specs == 0


def test_three_packs_arguing_one_capability_trip_the_cap(tmp_path):
    """The measurement this whole change exists for."""
    profiles, content = _packs(
        tmp_path,
        {"acme": "identity", "borea": "identity", "cirrus": "identity", "delta": "payments"},
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign-20260904",
        content_root=content,
        profiles_root=profiles,
        include_packs=True,
    )
    assert len(cov.packs) == 4
    monotone = [f for f in cov.findings if f.startswith("argument-monotone")]
    assert monotone, f"cap did not fire: {cov.findings}"
    assert "'identity'" in monotone[0]
    assert cov.failed


def test_packs_under_the_cap_pass(tmp_path):
    """Negative control — two per group is the cap, not one."""
    profiles, content = _packs(
        tmp_path, {"acme": "identity", "borea": "identity", "cirrus": "observability"}
    )
    cov = audit_campaign(
        "northwind",
        "test-campaign-20260904",
        content_root=content,
        profiles_root=profiles,
        include_packs=True,
    )
    assert not [f for f in cov.findings if f.startswith("argument-monotone")]


def test_a_campaign_slug_with_no_date_globs_nothing(tmp_path):
    """Fail closed: auditing another campaign's packs is worse than auditing none."""
    profiles, content = _packs(tmp_path, {"acme": "identity"})
    assert campaign_packs("northwind", "no-date-here", content_root=content) == []


def test_angle_missing_now_convicts_a_pack(tmp_path):
    """Inverted 2026-09-04. Packs were exempt because `draft-outreach` did not ask for a hook
    cell, which made the exemption the honest reading. It now does (Compose step 1), and six
    Tier-A packs had shipped in one campaign never having opened the matrix — so a pack with no
    declared cell is the same finding as a spec with none."""
    profiles, content = _packs(tmp_path, {"acme": "identity"})
    cov = audit_campaign(
        "northwind",
        "test-campaign-20260904",
        content_root=content,
        profiles_root=profiles,
        include_packs=True,
    )
    assert [f for f in cov.findings if f.startswith("angle-missing")]


def test_a_pack_declaring_its_cell_clears_the_finding(tmp_path):
    """The pack surface is `**Hook cell:**`; until 2026-09-04 the regex read only `hook_cell:`,
    so a declared cell was decorative and the audit passed on nothing."""
    from gtm_core.hook_coverage.declared import _HOOK_CELL_RE

    m = _HOOK_CELL_RE.search("**Hook cell:** CEO / Founder × Some signal")
    assert m and m.group("value") == "CEO / Founder × Some signal"
    assert _HOOK_CELL_RE.search("hook_cell: CEO / Founder × Some signal")
    # Taken WHOLE: a real matrix signal contains parentheses, and a first version of this regex
    # stripped a trailing one — meaning to drop a "(Builder grid)" annotation — and truncated
    # "Compliance event (audit, breach)" instead.
    keep = _HOOK_CELL_RE.search("hook_cell: CISO × Compliance event (audit, breach)")
    assert keep.group("value") == "CISO × Compliance event (audit, breach)"


def test_one_parser_reads_both_the_spec_and_the_pack_surface():
    """A second regex is how the two surfaces drift apart."""
    assert declared_capability("capability: identity") == "identity"
    assert declared_capability("**Capability:** identity") == "identity"
    assert declared_capability("**Capability:** Credentials & delegation") == (
        "credentials-delegation"
    )


def test_a_premise_may_declare_whether_it_attests_a_boundary(tmp_path):
    """``attests_boundary`` is optional and three-valued: absent is "not said" — the value
    every vocabulary written before 2026-09-24 carries, and the one that arms nothing."""
    v = _vocab(tmp_path, _VOCAB + "attests_boundary = false\n")
    assert v["ships-agents"].attests_boundary is False
    assert v["multi-framework"].attests_boundary is None


_TWO_GRID_SEAT_MATRIX = """---
source: generated
---
# Outreach hook matrix — Northwind Systems

## builder

| Signal → / Seat ↓ | agents-in-path × account-event |
| --- | --- |
| ceo | "Prove whose authority the agent carries." |

## startup

| Signal → / Seat ↓ | agents-in-path × account-event |
| --- | --- |
| ceo | "Trust as a feature of the demo." |
"""


def test_resolve_declared_cell_prefers_the_grid_the_angle_declares():
    """An angle declares its grid; a seat with a twin cell in another grid must not be
    measured against the twin. Measured 2026-09-24 on a live campaign: six specs whose
    seat also had a builder-grid cell were reported MISAIMED at 0% builder on lists that
    were 100% in the angle's own segment.
    """
    from collections import Counter
    from dataclasses import replace

    from gtm_core.hook_coverage import resolve_declared_cell
    from gtm_core.hook_coverage.fit import segment_fit

    m = parse_matrix(_TWO_GRID_SEAT_MATRIX)
    reg = _registry()
    reg.angles["handoff-evidence-ceo"] = replace(
        reg.angles["handoff-evidence-ceo"], segments=("startup",)
    )
    d = resolve_declared_cell(_spec("angle:      handoff-evidence-ceo\n"), m, reg)
    assert d is not None
    assert d.segment == "startup"
    fit = segment_fit("spec.md", d, m, Counter({"startup": 8}))
    assert fit is not None
    assert fit.declared_segment == "startup"
    assert fit.counts["startup"] == 8

    # Negative control: an angle that declares NO grid keeps the segment-insensitive read
    # and lands on the first grid the matrix holds the pair under.
    plain = resolve_declared_cell(_spec("angle:      handoff-evidence-ceo\n"), m, _registry())
    assert plain is not None
    assert plain.segment == ""
    assert segment_fit("spec.md", plain, m, Counter({"startup": 8})).declared_segment == "builder"


def test_capability_monotone_counts_per_capability_and_seat():
    """2026-09-24: an angle-tagged campaign runs one capability at every seat that has an
    angle for it. The same group at three different seats is three arguments and stays
    silent; at one seat it is one argument in three costumes and fires, naming the seat.
    Without ``seats`` the count stays per capability — the pre-change behaviour, kept as the
    negative control so a rule that went silent everywhere would be caught here."""
    caps = {f"spec-{i}.md": "identity" for i in range(MAX_SPECS_PER_CAPABILITY + 1)}
    spread = {spec: f"seat-{n}" for n, spec in enumerate(caps)}
    assert capability_monotone(caps, seats=spread) == []
    same = dict.fromkeys(caps, "cto")
    findings = capability_monotone(caps, seats=same)
    assert len(findings) == 1
    assert "identity" in findings[0]
    assert "'cto'" in findings[0]
    assert len(capability_monotone(caps)) == 1


_VOCAB_ATTESTED = """
schema = 1
[premise.regulated-entity]
claim = "operates under a named regulator"
min_distinct = 1
terms = ["bank"]
industry_terms = ["commercial banking", "health care"]
[premise.seat-remit]
claim = "the seat owns the standing problem"
attested_by_seat = true
terms = []
"""


def test_an_industry_term_attests_a_premise_from_the_industry_column_alone(tmp_path):
    """A commercial bank is a regulated entity whether or not a sentence of research says so.
    The industry column is a classification, so its terms fire there and nowhere else."""
    v = _vocab(tmp_path, _VOCAB_ATTESTED)
    premise = v["regulated-entity"]
    bank = {
        "email": "a@x.example",
        "signal_evidence": "Opened an office.",
        "industry": "Commercial Banking",
    }
    assert premise_unsupported([bank], premise) == []
    assert premise.hits_for(bank) == {"commercial banking"}
    # Negative controls: the same words in free text do not count as an industry term, and a
    # sector the list does not name attests nothing.
    free_text = {
        "email": "b@x.example",
        "signal_evidence": "commercial banking is changing",
        "industry": "",
    }
    assert premise_unsupported([free_text], premise) != []
    other = {"email": "c@x.example", "signal_evidence": "", "industry": "Software Publishers"}
    assert premise_unsupported([other], premise) != []


def test_a_seat_attested_premise_asks_nothing_of_the_record(tmp_path):
    """`attested_by_seat = true` loads as arity 0 with no terms, so every row attests it —
    and only a literal `true` declares it; a term-less premise otherwise stays unloaded."""
    v = _vocab(tmp_path, _VOCAB_ATTESTED)
    premise = v["seat-remit"]
    assert premise.attested_by_seat and premise.min_distinct == 0
    assert premise_unsupported([{"email": "a@x.example"}], premise) == []
    dropped = _vocab(
        tmp_path, 'schema = 1\n[premise.empty]\nattested_by_seat = "yes"\nterms = []\n'
    )
    assert "empty" not in dropped
