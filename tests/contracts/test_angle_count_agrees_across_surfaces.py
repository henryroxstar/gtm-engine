"""One registry, four surfaces, and the arithmetic that has to hold between them.

The design this implements names **four** places the angle count appears: the generated matrix
view, ``hook_coverage``'s coverage report, ``messaging unused``, and the dashboard's per-angle
cells. All of them are supposed to derive
from :func:`gtm_core.messaging.registry.load`, and the failure this file exists to catch is the
one where they quietly stop — a matrix that shows eight arguments, a report that counts three,
and an operator who believes whichever screen they opened.

**Three of the four exist today. The fourth does not, and this file does not pretend it does.**
The dashboard's per-angle outcome cells are FR4 work (PRD §6, "``outcomes.jsonl`` rows tagged
``angle:<id>`` → the dashboard's per-angle cells"); nothing in
``gtm_core/email_campaign_dashboard/`` reads the registry yet. Asserting three surfaces and
claiming four would be a lie of exactly the kind this test is supposed to prevent, so instead
:func:`test_the_dashboard_surface_does_not_exist_yet_and_says_so` pins the *absence*: the day a
dashboard module imports ``gtm_core.messaging``, that test goes red and names this file as the
place the fourth number has to be added. A missing surface is an absence somebody has to
notice; this makes it one that announces itself.

**Why the numbers are read the way a caller reads them.** Calling ``registry.load`` four times
and comparing the answer to itself proves only that ``len()`` is deterministic. Every number
below comes off a real caller path instead: the markdown the renderer emits, parsed back with
:func:`gtm_core.hook_coverage.matrix.parse_matrix`; the ``matrix: N cell(s)`` line of the
coverage report, parsed out of the rendered report text; and the JSON three ``messaging``
verbs print. The expectations they are checked against come from the fixture's own TOML
literals below — never from a fifth call to the loader.

**Three of the numbers are legitimately different, and equality would be the wrong assertion.**
The surfaces do not count the same set, and forcing them equal would hide the difference
rather than prove agreement:

* ``check.angles`` and ``messaging unused``'s universe (``referenced`` + ``unused``) count
  **every** angle in ``angles.toml``, whatever its status. ``unused`` has to: an angle nobody
  writes from is precisely what it reports, and a retired one still answers "does a spec
  declare this".
* ``matrix --json``'s ``cells`` counts the **non-retired angles**
  (:func:`gtm_core.messaging.matrix_view.rendered_angles`) — the grid answers "what is on
  offer", and a withdrawn offer is not one.
* The matrix grid and the coverage report count non-retired **cells**, which is a larger
  number whenever an angle declares more than one segment: the grid is drawn once per segment,
  so such an angle occupies one cell in each. One angle, two cells — both right about
  different questions, so :func:`test_a_two_segment_angle_is_one_angle_and_two_cells` pins the
  fan-out instead of forcing the two numbers together.
* ``check.live_angles`` counts only ``status = "live"`` — the angles an operator promoted.

So the asserted relationship is ``live ≤ rendered angles ≤ all`` with ``cells ≥ rendered
angles``, the fixture making both inequalities **strict** (one retired angle, two drafts, one
live), and equality asserted only between surfaces that genuinely count the same set. A fixture
where the numbers coincided would pass while proving nothing — which is exactly what the live
tenant looks like today (53 angles, 53 rendered, 53 cells, 0 live), and why the fixture is
built to differ.

The negative control adds one angle and requires **every** surface to move by exactly one; the
discrimination control (:func:`test_a_surface_that_filters_differently_is_caught`) makes one
surface filter differently on purpose and requires the agreement check to fail. Without the
first, four hardcoded zeros pass; without the second, an assertion that cannot go red.

Every fixture value here is fictional (§R9) — a fictional profile slug, fictional seats,
fictional premises, and no company or person at all. Real tenant facts live only under
``profiles/``, and nothing in this file reads that tree.

The one exception, stated so the sentence above stays true: a premise fixture's ``terms`` name
real agent frameworks (``langgraph``, ``autogen``). Those are ecosystem TECHNOLOGY tokens, not
third-party identity — a premise vocabulary matches on what a prospect's stack contains, and a
fictional framework name would make the fixture describe a world the matcher never meets. The
repo already uses that pair this way at HEAD (``gtm_core/hook_coverage/premise.py`` and five
test files). §R9 is about real PEOPLE and COMPANIES as example data; no such name appears here.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from gtm_core.hook_coverage.coverage import Coverage
from gtm_core.hook_coverage.matrix import parse_matrix
from gtm_core.hook_coverage.render import render as render_coverage
from gtm_core.messaging import cli, matrix_view

# --- the fixture tenant ----------------------------------------------------------------
#
# Two seats, two premises, two claims (one verified, one design-target) and four angles whose
# statuses are deliberately unequal: one live, two draft, one retired. That spread is what
# makes `live < rendered < all` strict, and a test whose three numbers cannot differ cannot
# tell agreement from coincidence.

_PROFILE = "copperline"

_VOCABULARY_TOML = """\
default_persona = "ciso"
segments = ["enterprise", "midmarket", "unspecified"]

[[persona]]
name = "ciso"
cues = ["ciso", "chief information security officer", "head of security"]

[[persona]]
name = "platform-lead"
cues = ["head of platform", "vp platform", "platform lead"]

[[seat]]
name = "security"
personas = ["ciso"]
stakes = ["breach", "audit"]

[[seat]]
name = "platform"
personas = ["platform-lead"]
stakes = ["outage", "roadmap"]
"""

_PREMISE_TOML = """\
schema = 1

[premise.multi-framework]
claim = "the reader runs agents on more than one framework"
min_distinct = 2
terms = ["langgraph", "autogen"]

[premise.cross-org-agents]
claim = "the reader's agents cross an organisational boundary"
min_distinct = 1
terms = ["supplier portal"]
"""

_CLAIMS = [
    {
        "id": "audit-signed",
        "group": "observability",
        "status": "verified",
        "statement": "Each audit entry is signed.",
        "source": "knowledge/references/ledger-notes.md:12",
    },
    {
        "id": "mtls",
        "group": "identity",
        "status": "design-target",
        "statement": "Mutual TLS between agent and gateway.",
    },
]

_PROOF = [
    {
        "id": "regulator-note",
        "kind": "anchor",
        "market": "Singapore",
        "figure_kind": "none",
        "statement": "A verifiable identity per agent, tied to an accountable human.",
        "source": "knowledge/guidance/regulator.md:4",
        "binding": False,
    },
]


def _angle(angle_id: str, seat: str, premise: str, opener: str, status: str, claim: str) -> dict:
    return {
        "id": angle_id,
        "seat": seat,
        "premise": premise,
        "claim": claim,
        "proof": "regulator-note",
        "opener_kind": opener,
        "summary": f"One argument for {seat} on {premise}.",
        "status": status,
    }


#: Four angles across two seats and two premises. No ``segments`` key on any of them, so each
#: renders into exactly one grid cell — a multi-segment angle renders one cell PER segment, and
#: mixing that in here would confound the count under test with a fan-out.
_ANGLES = [
    _angle(
        "a1-security-multi-framework",
        "security",
        "multi-framework",
        "account-event",
        "live",
        "audit-signed",
    ),
    _angle(
        "a2-platform-multi-framework",
        "platform",
        "multi-framework",
        "public-event",
        "draft",
        "mtls",
    ),
    _angle(
        "a3-security-cross-org",
        "security",
        "cross-org-agents",
        "account-event",
        "draft",
        "audit-signed",
    ),
    _angle(
        "a4-platform-cross-org",
        "platform",
        "cross-org-agents",
        "account-event",
        "retired",
        "audit-signed",
    ),
]

#: The angle the negative control adds. ``live`` on the verified claim, on a cell none of the
#: four above occupies, so it lands as a new grid cell rather than colliding with one.
_EXTRA_ANGLE = _angle(
    "a5-platform-cross-org-public",
    "platform",
    "cross-org-agents",
    "public-event",
    "live",
    "audit-signed",
)

#: One angle scoped to TWO segments, for the fan-out case. The grid is drawn once per segment,
#: so this angle occupies two cells while remaining one angle.
_TWO_SEGMENT_ANGLE = {
    **_angle(
        "a6-security-multi-framework-public",
        "security",
        "multi-framework",
        "public-event",
        "draft",
        "audit-signed",
    ),
    "segments": ["enterprise", "midmarket"],
}

#: The one angle a fixture spec declares, so ``unused``'s universe is provably
#: ``referenced + unused`` and not just the length of one of them.
_DECLARED_BY_A_SPEC = "a1-security-multi-framework"

_MATRIX_CELLS_RE = re.compile(r"matrix: (?P<n>\d+) cell\(s\)")


# --- writing the fixture ----------------------------------------------------------------


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    return json.dumps(value)


def _render_table(table: str, blocks: list[dict]) -> str:
    out: list[str] = []
    for block in blocks:
        out.append(f"[[{table}]]")
        out.extend(f"{key} = {_toml_value(value)}" for key, value in block.items())
        out.append("")
    return "\n".join(out)


def _tenant(tmp_path: Path, monkeypatch, name: str, angles: list[dict]) -> tuple[Path, Path]:
    """``(profiles_root, specs_dir)`` for one fixture tenant holding ``angles``.

    ``GTM_PROFILES_ROOT`` is pointed at the same tree the CLI is told to read: the role
    vocabulary resolves through the ambient root, and a suite whose CLI and whose vocabulary
    disagreed about which tree they are in would be measuring two tenants. Each tree gets its
    own directory so the vocabulary loader's cache cannot answer one fixture with another's
    seats.
    """
    root = tmp_path / name
    knowledge = root / _PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    for filename, text in {
        "claims.toml": _render_table("claim", _CLAIMS),
        "proof.toml": _render_table("proof", _PROOF),
        "angles.toml": _render_table("angle", angles),
        "role-vocabulary.toml": _VOCABULARY_TOML,
        "premise-vocab.toml": _PREMISE_TOML,
    }.items():
        (knowledge / filename).write_text(text, encoding="utf-8")

    specs = root / "specs"
    specs.mkdir()
    (specs / "spec-security-2026-09-24.md").write_text(
        f"```\nangle: {_DECLARED_BY_A_SPEC}\nsegment: enterprise\n```\n\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(root))
    return root, specs


# --- reading each surface the way a caller reads it ---------------------------------------


def _json_verb(capsys, root: Path, *argv: str) -> dict:
    """Run one ``messaging`` verb and return the JSON it printed. Exit 0 or the test stops."""
    code = cli.main([*argv, "--profile", _PROFILE, "--profiles-root", str(root), "--json"])
    out = capsys.readouterr()
    assert code == 0, f"`messaging {argv[0]}` exited {code}: {out.err}"
    return json.loads(out.out)


def _matrix_grid_cells(root: Path) -> int:
    """Surface 1 — the generated matrix, parsed back out of the markdown it renders.

    Read through :func:`parse_matrix` rather than off the registry, because the grid is the
    artifact a human opens and the parser is what every downstream joiner sees. A hole renders
    ``—`` and parses back as no cell, so this number is the filled cells, not the slots.
    """
    from gtm_core.messaging import registry

    reg = registry.load(_PROFILE, profiles_root=root)
    parsed = parse_matrix(matrix_view.render(reg))
    assert parsed.ok, parsed.reason
    return len(parsed.cells)


def _coverage_report_cells(matrix_path: Path) -> int:
    """Surface 2 — the integer in ``hook_coverage``'s rendered coverage report.

    Deliberately reads the ``hook-matrix.md`` that ``messaging matrix`` WROTE, not the
    in-memory render surface 1 used: two readings of one file prove nothing, while a file the
    write verb left stale is exactly the drift the two surfaces exist to catch. The number is
    scraped from the report text because that line is what an operator reads.
    """
    report = render_coverage(Coverage(profile=_PROFILE, matrix=parse_matrix(matrix_path)))
    match = _MATRIX_CELLS_RE.search(report)
    assert match, f"no `matrix: N cell(s)` line in the coverage report:\n{report}"
    return int(match.group("n"))


def _surfaces(capsys, root: Path, specs: Path) -> dict[str, int]:
    """Every angle count this system can currently be asked for, by the caller's own route."""
    check = _json_verb(capsys, root, "check")
    matrix = _json_verb(capsys, root, "matrix")
    unused = _json_verb(capsys, root, "unused", "--specs", str(specs))
    return {
        "matrix_grid": _matrix_grid_cells(root),
        "coverage_report": _coverage_report_cells(Path(matrix["path"])),
        "matrix_cli": matrix["cells"],
        "unused_universe": len(unused["referenced"]) + len(unused["unused"]),
        "check_angles": check["angles"],
        "check_live": check["live_angles"],
    }


def _expected(angles: list[dict]) -> tuple[int, int, int, int]:
    """``(all, rendered, live, grid_cells)`` straight off the fixture's TOML literals.

    The ground truth is the fixture, never a fifth call to ``registry.load`` — a test that
    derives its expectation from the thing under test cannot fail for the reason it exists.

    ``grid_cells`` is **not** ``rendered``: the grid is drawn once per segment, so an angle
    declaring two segments occupies a cell in each. That is a fan-out of the drawing, not of
    the argument, which is why it is computed here rather than assumed away.
    """
    rendered = [a for a in angles if a["status"] != "retired"]
    return (
        len(angles),
        len(rendered),
        sum(1 for a in angles if a["status"] == "live"),
        sum(max(1, len(a.get("segments") or ())) for a in rendered),
    )


def _assert_agreement(counts: dict[str, int], angles: list[dict]) -> None:
    """The relationship between the surfaces — equality only where the sets are the same.

    Grouped by what each surface COUNTS, because that is the distinction a naive "all four are
    equal" assertion erases. Three different sets are in play and forcing them equal would be a
    test that lies:

    * ``check_angles`` / ``unused_universe`` — every angle in ``angles.toml``, any status.
    * ``matrix_cli`` — the non-retired **angles** (``matrix --json`` calls the field ``cells``,
      but it is ``len(rendered_angles)``: one per angle, whatever its segment scope).
    * ``matrix_grid`` / ``coverage_report`` — the non-retired **cells**, one per
      (segment, seat, signal). Equal to ``matrix_cli`` only while every angle declares at most
      one segment, which is every angle in the live tenant today.
    * ``check_live`` — promoted angles only.
    """
    total, rendered, live, grid_cells = _expected(angles)

    # Every angle, whatever its status: the registry's own census.
    assert counts["check_angles"] == total
    assert counts["unused_universe"] == total

    # What is on offer, counted as angles and as drawn cells.
    assert counts["matrix_cli"] == rendered
    assert counts["matrix_grid"] == grid_cells
    assert counts["coverage_report"] == grid_cells

    # Promoted only.
    assert counts["check_live"] == live

    # And the ordering, which is the part that survives a fixture change: a surface counting
    # fewer offers than the operator promoted, or more angles than the registry holds, is wrong
    # regardless of what the fixture happens to contain. A cell count may exceed an angle count
    # (the segment fan-out) but never the other way round.
    assert counts["check_live"] <= counts["matrix_cli"] <= counts["check_angles"]
    assert counts["matrix_grid"] >= counts["matrix_cli"]


# --- the property ------------------------------------------------------------------------


def test_every_existing_surface_agrees_on_the_angle_count(tmp_path, monkeypatch, capsys):
    """Matrix view, coverage report and the three ``messaging`` verbs, on one fixture.

    The fixture's three numbers are deliberately distinct (4 angles, 3 rendered, 1 live), so a
    surface that counted the wrong SET cannot pass by accident — which is the failure a fixture
    of four identical numbers would wave through.
    """
    root, specs = _tenant(tmp_path, monkeypatch, "agree", _ANGLES)
    counts = _surfaces(capsys, root, specs)

    _assert_agreement(counts, _ANGLES)
    # Stated rather than implied: the strictness IS the discrimination.
    assert counts["check_live"] < counts["matrix_grid"] < counts["check_angles"]


def test_the_grid_count_is_filled_cells_and_not_grid_slots(tmp_path, monkeypatch, capsys):
    """Two seats x four columns is eight slots; three angles fill three of them.

    Without this, "the surfaces agree" would still hold for an implementation that counted
    every intersection — and the report answering "how many seats have an argument" would read
    high by exactly the number of holes, the failure FR2's placeholder rule exists to close.
    """
    root, _specs = _tenant(tmp_path, monkeypatch, "holes", _ANGLES)
    from gtm_core.messaging import registry

    text = matrix_view.render(registry.load(_PROFILE, profiles_root=root))
    # Body rows only: the header names the axes, the separator is punctuation, and the
    # preamble prose carries em dashes of its own — counting `—` over the whole document
    # would measure the documentation rather than the grid.
    body: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells[0].startswith("---") or "Signal →" in cells[0]:
            continue
        body.append(cells[1:])

    slots = sum(len(row) for row in body)
    holes = sum(1 for row in body for cell in row if cell == "—")

    assert (len(body), slots) == (2, 8), text  # two seat rows x four premise x opener columns
    assert holes == 5
    assert _matrix_grid_cells(root) == slots - holes == 3


def test_a_two_segment_angle_is_one_angle_and_two_cells(tmp_path, monkeypatch, capsys):
    """The one place the surfaces legitimately DISAGREE, pinned so nobody "fixes" it blind.

    ``matrix --json`` prints a field called ``cells`` whose value is
    ``len(rendered_angles)`` — an **angle** count. The grid it just wrote draws every segment
    as its own table, so an angle scoped to two segments appears in two cells. Both numbers are
    right about different questions ("how many arguments are on offer" vs "how many cells does
    the matrix hold"), and forcing them equal would make one of them wrong.

    They coincide in the live tenant today because no angle there declares more than one
    segment (measured 2026-09-24: 53 angles, 53 cells). That is a property of the data, not of
    the code, so it is asserted here on a fixture that breaks it rather than assumed forever.
    """
    angles = [*_ANGLES, _TWO_SEGMENT_ANGLE]
    root, specs = _tenant(tmp_path, monkeypatch, "fan-out", angles)
    counts = _surfaces(capsys, root, specs)

    _assert_agreement(counts, angles)
    # Stated as numbers as well as through the helper: one angle added, two cells appeared.
    assert counts["matrix_cli"] == 4
    assert counts["matrix_grid"] == counts["coverage_report"] == 5
    assert counts["check_angles"] == counts["unused_universe"] == 5


def test_adding_an_angle_moves_every_surface_together(tmp_path, monkeypatch, capsys):
    """The negative control. Without it, four hardcoded zeros pass this file.

    One angle added, ``live``, on a cell nothing else occupies. Every surface must move by
    exactly one — including ``check_live``, because the added angle is promoted.
    """
    root, specs = _tenant(tmp_path, monkeypatch, "before", _ANGLES)
    before = _surfaces(capsys, root, specs)

    grown = [*_ANGLES, _EXTRA_ANGLE]
    root2, specs2 = _tenant(tmp_path, monkeypatch, "after", grown)
    after = _surfaces(capsys, root2, specs2)

    _assert_agreement(after, grown)
    assert set(before) == set(after)
    for surface, value in after.items():
        assert value == before[surface] + 1, f"{surface} did not move: {before} -> {after}"


def test_a_surface_that_filters_differently_is_caught(tmp_path, monkeypatch, capsys):
    """The discrimination control (§R18): break one surface and the check must go red.

    ``rendered_angles`` is narrowed to ``live`` only — the single most plausible drift, since
    "which statuses belong on the grid" is a judgement somebody could change in one module
    without touching the other three. The matrix then shows one cell where the registry holds
    three non-retired angles, and :func:`_assert_agreement` must refuse it.
    """
    root, specs = _tenant(tmp_path, monkeypatch, "drift", _ANGLES)
    monkeypatch.setattr(
        matrix_view,
        "rendered_angles",
        lambda reg: tuple(a for _, a in sorted(reg.angles.items()) if a.status == "live"),
    )

    counts = _surfaces(capsys, root, specs)

    assert counts["matrix_grid"] == 1
    assert counts["check_angles"] == 4
    with pytest.raises(AssertionError):
        _assert_agreement(counts, _ANGLES)


# --- the fourth surface, which does not exist yet -----------------------------------------


def test_the_dashboard_surface_does_not_exist_yet_and_says_so():
    """PRD §4B names four surfaces; this file asserts three, and pins why.

    The dashboard's per-angle outcome cells are FR4 (PRD §6): ``outcomes.jsonl`` rows tagged
    ``angle:<id>`` rendered by ``email_campaign_dashboard``. No module in that package reads
    the registry today, so there is no fourth number to compare and inventing one would be
    worse than admitting it.

    This asserts the ABSENCE rather than documenting it, so the gap cannot widen quietly: the
    day a dashboard module imports ``gtm_core.messaging``, this goes red and names the file
    that has to carry the fourth surface. Scoped to import statements on purpose — the word
    "angle" appears all over that package as the ``re-angle`` judge verdict, which is an
    unrelated concept and would make a text search cry wolf.
    """
    spec = importlib.util.find_spec("gtm_core.email_campaign_dashboard")
    assert spec is not None and spec.submodule_search_locations
    package = Path(next(iter(spec.submodule_search_locations)))

    importers = sorted(
        path.name
        for path in package.rglob("*.py")
        if re.search(
            r"^\s*(?:from\s+[\w.]*messaging|import\s+[\w.]*messaging)",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    assert not importers, (
        f"{importers} now read the outbound fact registry, so the dashboard's per-angle count "
        "is a real surface. Add it to `_surfaces()` and `_assert_agreement()` in this file — "
        "PRD 2026-09-24 §4B requires all FOUR numbers to agree, and three of four agreeing is "
        "how the fourth drifts."
    )
