"""The unused-hook backlog: every matrix cell no spec declares, ungated by volume.

The matrix held a good argument nobody was arguing and no report said so. Coverage is
measured per PERSONA and gated on `MIN_RECIPIENTS`, so a cell whose persona is addressed by
a DIFFERENT argument — and holds far more than the threshold — reads green while the cell
itself is untouched. That is not a threshold that was set too high; it is a threshold
answering a different question.

The headline control is at the bottom: reintroducing a `MIN_RECIPIENTS` gate must make the
known-unused cell vanish, proving the absence of that gate is load-bearing rather than
incidental.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.hook_coverage.backlog import (
    Backlog,
    BacklogUnreadable,
    _opening,
    backlog,
    render_backlog,
)
from gtm_core.hook_coverage.cli import main

PROFILE = "acme"

#: Invented outright — personas, signals and hook prose all unrelated to any tenant's
#: positioning. `gtm_core/` and `tests/` both ship in the public carve, so a fixture copied
#: from a live matrix would put that tenant's segment names, signal labels and near-verbatim
#: hook copy in a public repo. The de-brand lint cannot see it (no tenant TOKEN appears) and
#: the PII roster cannot see it (it is not a third party's name) — which is exactly why it
#: has to be invented rather than trimmed.
#:
#: Two personas x two signals is all the shape these tests need, plus one hook with two
#: sentences so the first-sentence boundary is exercised.
_MATRIX = """\
# Hook matrix

## Startup

| Signal → / Persona ↓ | Tide chart drifted | Kiln log arrived |
|---|---|---|
| **CTO** | Two tide charts disagree by a full hour at the same berth. And more. | The kiln log is the question already written down. |
| **CISO** | A firing schedule nobody signed leaves the glaze on somebody's word. | Answering in the log beats answering from memory. |
"""

_SPEC = """\
```
Campaign: q4
hook_cell: CISO × Kiln log arrived
```

Body.
"""


def _tenant(
    tmp_path,
    *,
    specs: dict[str, str] | None = None,
    cells_toml: str | None = None,
    matrix: str | None = None,
):
    profiles = tmp_path / "profiles" / PROFILE / "knowledge"
    profiles.mkdir(parents=True)
    (profiles / "hook-matrix.md").write_text(matrix or _MATRIX, encoding="utf-8")
    seq = tmp_path / "content" / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    entries = []
    for name, text in (specs or {}).items():
        (seq / name).write_text(text, encoding="utf-8")
        (seq / name.replace(".md", ".csv")).write_text("email\na@x.example\n", encoding="utf-8")
        entries.append(
            f'[[sequence]]\nid = "{name}"\ncsv = "{name.replace(".md", ".csv")}"\n'
            f'spec = "{name}"\ncampaign = "q4"\n'
        )
    (seq / "cells.toml").write_text(
        cells_toml if cells_toml is not None else "\n".join(entries), encoding="utf-8"
    )
    return tmp_path / "content", tmp_path / "profiles"


def _run(tmp_path, **kw) -> Backlog:
    content, profiles = _tenant(tmp_path, **kw)
    return backlog(PROFILE, content_root=content, profiles_root=profiles)


def _coords(b: Backlog) -> set[tuple[str, str]]:
    return {(c.persona, c.signal) for c in b.unused}


# --- what it reports --------------------------------------------------------


def test_a_matrix_no_spec_touches_is_entirely_backlog(tmp_path):
    b = _run(tmp_path)
    assert b.cells_total == 4
    assert len(b.unused) == 4
    assert b.used == 0


def test_a_declared_cell_drops_out_of_the_backlog(tmp_path):
    """The positive control. Without it, a report that always lists everything would pass
    every test above."""
    b = _run(tmp_path, specs={"spec-a.md": _SPEC})
    assert ("CISO", "Kiln log arrived") not in _coords(b)
    assert len(b.unused) == 3
    assert b.used == 1
    assert b.specs_read == 1


def test_a_cell_declared_only_through_an_angle_drops_out_of_the_backlog(tmp_path):
    """FR3. The shipped spec template writes `angle:` and no `hook_cell:`, so a report that
    read only the legacy field would list every migrated spec's cell as backlog and send a
    drafter to write copy that already exists."""
    from gtm_core.messaging.registry import Angle, Claim, Registry

    # The generated-view shape: SEAT rows, `premise × opener_kind` columns, which is exactly
    # what `derived_fields` builds a cell id out of.
    generated = (
        "# Hook matrix\n\n## Startup\n\n"
        "| Signal → / Seat ↓ | kiln-log × account-event | tide-chart × public-event |\n"
        "| --- | --- | --- |\n"
        "| ciso | The kiln log is the question already written down. | — |\n"
        "| cto | Two tide charts disagree by a full hour at the same berth. | — |\n"
    )
    registry = Registry(
        claims={"c1": Claim(id="c1", group="identity", status="verified", statement="s")},
        proof={},
        angles={
            "kiln-log-ciso": Angle(
                id="kiln-log-ciso",
                seat="ciso",
                premise="kiln-log",
                claim="c1",
                proof="p1",
                opener_kind="account-event",
                summary="s",
                status="live",
            )
        },
        seats={"ciso": ("kiln-log-ciso",)},
    )
    spec = "```\nCampaign: q4\nangle: kiln-log-ciso\n```\n\nBody.\n"
    content, profiles = _tenant(tmp_path, specs={"spec-a.md": spec}, matrix=generated)
    b = backlog(PROFILE, content_root=content, profiles_root=profiles, registry=registry)
    assert ("ciso", "kiln-log × account-event") not in _coords(b)
    assert b.used == 1

    # §R18 negative control: without the registry the same spec resolves nothing, so the
    # cell comes back into the backlog. That is what proves the angle read is doing the work
    # here and not some other path marking the cell used.
    b = backlog(PROFILE, content_root=content, profiles_root=profiles, registry=None)
    assert ("ciso", "kiln-log × account-event") in _coords(b)


def test_each_entry_carries_the_argument_not_only_its_coordinates(tmp_path):
    """Carrying the 388-unreadable-warnings lesson: a bare (persona, signal) triple is a
    finding nobody acts on. A drafter must be able to judge a cell without opening the file.
    """
    b = _run(tmp_path)
    cell = next(c for c in b.unused if c.signal == "Tide chart drifted" and c.persona == "CTO")
    assert cell.opening == "Two tide charts disagree by a full hour at the same berth."
    assert "And more" not in cell.opening, "the FIRST sentence, not the whole hook"
    assert cell.opening in render_backlog(b)


def test_the_report_is_sorted_by_segment(tmp_path):
    b = _run(tmp_path)
    segments = [c.segment for c in b.unused]
    assert segments == sorted(segments, key=str.lower)


def test_an_empty_hook_stays_empty_rather_than_becoming_a_placeholder():
    """A cell the tenant left blank is itself worth seeing, so it must not be dressed up."""
    assert _opening("") == ""
    assert _opening("   ") == ""
    assert _opening("No full stop here") == "No full stop here"
    assert _opening("First? Second.") == "First?"


# --- identity: a cell cannot be covered under one spelling and unused under another ---


@pytest.mark.parametrize(
    "declared",
    [
        "CISO x Kiln log arrived",  # ASCII separator
        "CISO × Kiln log arrived",  # multiplication sign
        "  ciso   ×   kiln log arrived  ",  # case + spacing
        "CISO × Kiln log arrived",  # non-breaking space
    ],
)
def test_one_cell_is_one_cell_however_the_spec_spells_it(tmp_path, declared):
    """P2's key is the same normalisation the matrix parser uses. A report that answered
    differently per spelling would be a spelling test wearing a coverage test's name."""
    spec = "```\nCampaign: q4\nhook_cell: " + declared + "\n```\n\nBody.\n"
    b = _run(tmp_path, specs={"spec-a.md": spec})
    assert ("CISO", "Kiln log arrived") not in _coords(b), declared


# --- refuse vs skip ---------------------------------------------------------


def test_a_corrupt_cells_toml_refuses_rather_than_reporting_every_cell_unused(tmp_path):
    """The dangerous failure. `load_cell_map` is fail-closed and returns [] for a malformed
    file — silence is the wrong answer HERE, because no sources means EVERY cell reads as
    unused, which is a maximally wrong report delivered with total confidence.
    """
    with pytest.raises(BacklogUnreadable, match="could not be read"):
        _run(tmp_path, cells_toml="[[sequence]\nid = ")


def test_an_unparseable_matrix_refuses_by_name(tmp_path):
    profiles = tmp_path / "profiles" / PROFILE / "knowledge"
    profiles.mkdir(parents=True)
    (profiles / "hook-matrix.md").write_text("| id | angle |\n|---|---|\n| 1 | x |\n", "utf-8")
    content = tmp_path / "content" / PROFILE / "prospects" / "sequences"
    content.mkdir(parents=True)
    (content / "cells.toml").write_text("", encoding="utf-8")
    with pytest.raises(BacklogUnreadable):
        backlog(PROFILE, content_root=tmp_path / "content", profiles_root=tmp_path / "profiles")


def test_a_missing_cells_toml_is_not_an_error(tmp_path):
    """Absent and corrupt are different. A tenant with no campaigns yet has a backlog of
    everything, which is true and useful; a tenant whose file will not parse has an
    unanswerable question."""
    profiles = tmp_path / "profiles" / PROFILE / "knowledge"
    profiles.mkdir(parents=True)
    (profiles / "hook-matrix.md").write_text(_MATRIX, encoding="utf-8")
    b = backlog(PROFILE, content_root=tmp_path / "content", profiles_root=tmp_path / "profiles")
    assert len(b.unused) == 4
    assert b.specs_read == 0


# --- the CLI ----------------------------------------------------------------


def test_the_cli_refuses_a_recipient_threshold(tmp_path, monkeypatch, capsys):
    """Refuse rather than ignore: a run that passed the flag and got a report computed
    without it would reasonably believe the threshold had been applied."""
    content, profiles = _tenant(tmp_path)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles))
    assert main(["--profile", PROFILE, "--backlog", "--min-recipients", "5"]) == 2
    assert "takes no recipient threshold" in capsys.readouterr().err


def test_the_cli_is_a_report_and_never_a_refusal(tmp_path, monkeypatch, capsys):
    """Exit 0 with a full backlog. A backlog that can fail a run becomes a reason to stop
    running it."""
    content, profiles = _tenant(tmp_path)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles))
    assert main(["--profile", PROFILE, "--backlog"]) == 0
    assert "HOOK BACKLOG" in capsys.readouterr().out


def test_the_cli_and_the_function_report_the_same_number(tmp_path, monkeypatch, capsys):
    """One function, two surfaces (§4.5). If a second surface ever renders this count it
    must come from here, not from a second walk of the matrix."""
    content, profiles = _tenant(tmp_path, specs={"spec-a.md": _SPEC})
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles))
    main(["--profile", PROFILE, "--backlog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    direct = backlog(PROFILE, content_root=content, profiles_root=profiles)
    assert payload["cells_total"] == direct.cells_total
    assert len(payload["unused"]) == len(direct.unused)
    assert payload["used"] == direct.used


def test_the_json_carries_the_full_list_not_an_excerpt(tmp_path, monkeypatch, capsys):
    """The rendered text may summarise; the machine-readable form may not."""
    content, profiles = _tenant(tmp_path)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles))
    main(["--profile", PROFILE, "--backlog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["unused"]) == payload["cells_total"]
    assert all(c["opening"] for c in payload["unused"])


# --- the headline control, against the LIVE matrix --------------------------

REPO = Path(__file__).resolve().parents[2]


def _live_profile() -> str:
    """A profile in this checkout with both a parseable matrix and a cells.toml, discovered
    rather than named (the de-brand release gate forbids a tenant token in tests/)."""
    for path in sorted(REPO.glob("profiles/*/knowledge/hook-matrix.md")):
        name = path.parent.parent.name
        if not (REPO / "content" / name / "prospects" / "sequences" / "cells.toml").is_file():
            continue
        try:
            if backlog(name).cells_total:
                return name
        except BacklogUnreadable:  # pragma: no cover
            continue
    pytest.skip("no tenant matrix + cells.toml in this checkout")


def test_the_live_matrix_holds_several_cells_behind_one_row_label():
    """**The negative control for the whole feature**, stated as the defect's SHAPE.

    The cell that motivated this sat unused for three weeks while every existing report read
    green, because its persona was addressed by a DIFFERENT argument and held far more than
    `MIN_RECIPIENTS`. The shape is: **one row label owns several coordinates, so crediting
    the row credits all of them.** A row-level check cannot see the difference; this report
    can, because it has no volume gate and reports coordinates rather than rows.

    **Why this is no longer phrased as "a used cell and an unused cell share a persona."**
    That phrasing bound the control to the tenant's *spec corpus* as well as to its matrix:
    it could only hold while some spec's `hook_cell` named a coordinate the matrix defines.
    FR2 (2026-09-24) re-axed the matrix onto seats and premise x opener_kind, and the legacy
    `hook_cell` strings name the retired persona x signal coordinates, so the used side is
    empty until `declared` learns to read `angle:` — a *different* change. Keeping the old
    phrasing would have made this report's control red for a reason that is not about this
    report, and the only ways to clear it would have been to weaken it or to wait. The shape
    it exists to catch is a property of the MATRIX, so it is asserted against the matrix.

    It still discriminates, in both directions: a matrix of one coordinate per row (nothing
    for a row-level check to hide) fails, and so does a report that collapsed coordinates
    onto rows — `hidden` would be empty either way. It is strictly stronger than the old
    form on the one axis that matters, because it holds whatever the spec corpus is doing.

    Deliberately not asserted on the tenant's own row and signal labels. `tests/` ships in
    the public carve, so pinning this to a grid coordinate would put that tenant's taxonomy in
    a public repo — which neither the de-brand lint (no tenant token) nor the PII roster (not a
    third party's name) can see.
    """
    b = backlog(_live_profile())
    assert b.unused, "the live matrix has no unused cell at all — nothing to control against"
    per_row: dict[str, list] = {}
    for cell in b.unused:
        per_row.setdefault(cell.persona, []).append(cell)
    # Every coordinate a row-level check would credit off one sibling. The first of each row
    # is the one that "covers" the row; every later one is hidden behind it.
    hidden = [c for cells in per_row.values() if len(cells) > 1 for c in cells[1:]]
    assert hidden, (
        f"no row label in the live matrix owns more than one unused coordinate "
        f"({len(b.unused)}/{b.cells_total} unused across {len(per_row)} row label(s)), so "
        "there is nothing a row-level check could hide and this report is redundant here. "
        "Either the matrix is one coordinate per row, or the report has collapsed "
        "coordinates onto rows."
    )
    assert all(c.opening for c in hidden), (
        "these must arrive with their argument, not just coordinates — a drafter has to be "
        "able to judge one without opening the matrix"
    )


def test_the_live_backlog_accounts_for_every_matrix_cell():
    """Conservation: every cell in the live matrix is classified, none silently dropped.

    The other half of what the old control covered. It read the live matrix a second time to
    compute a used set, which meant a cell that fell out of `backlog()` entirely — the silent
    failure — could still leave that assertion green.

    Asserted against an INDEPENDENT parse of the same file, never against `b.used` alone:
    that property is derived (`cells_total - unused - unmapped`) and would hold for any
    numbers at all. What is checked is that each cell the parser produces is reported exactly
    once or is genuinely argued — so a cell appended to both lists, or dropped from both, or
    a report reading a different file, is red.
    """
    b = backlog(_live_profile())
    assert b.specs_read, "no specs read — the used side would be trivially empty"
    cells = list(_live_matrix_cells())
    assert b.cells_total == len(cells), "the report parsed a different matrix than this did"
    reported = {(c.segment, c.persona, c.signal) for c in [*b.unused, *b.unmapped]}
    assert len(reported) == len(b.unused) + len(b.unmapped), "a coordinate reported twice"
    argued = [c for c in cells if (c.segment, c.persona, c.signal) not in reported]
    assert len(argued) == b.used, (
        f"{len(cells)} cell(s) in the matrix, {len(reported)} reported and {b.used} counted "
        "as argued — a cell that is in neither bucket has silently left the backlog"
    )


def _live_matrix_cells():
    from gtm_core.hook_coverage.matrix import parse_matrix
    from gtm_core.paths import resolve_knowledge_file, resolve_profiles_root

    profile = _live_profile()
    return parse_matrix(
        resolve_knowledge_file(resolve_profiles_root(), profile, "hook-matrix.md"),
        profile=profile,
    ).cells.values()


def test_reintroducing_a_recipient_gate_would_hide_the_cell(tmp_path):
    """**The discriminating control**: prove the ABSENCE of a volume gate is load-bearing.

    Built on a fixture whose `CTO` persona is richly addressed — 60 recipients,
    well over `MIN_RECIPIENTS`, by a spec arguing a DIFFERENT cell. That is exactly the
    live shape: the persona-level check is satisfied, so it reports green, while one of
    that persona's cells is untouched.

    Two assertions, and the pair is the control. `backlog()` lists the untouched cell
    BECAUSE it applies no threshold; re-applying the persona-level rule on top of its
    output makes the cell vanish. If the first ever fails, `backlog()` has grown the gate
    and can no longer see the defect it was built for.
    """
    from gtm_core.hook_coverage.config import MIN_RECIPIENTS

    spec = "```\nCampaign: q4\nhook_cell: CTO × Kiln log arrived\n```\n\nBody.\n"
    content, profiles = _tenant(tmp_path, specs={"spec-a.md": spec})
    # 60 recipients on that persona: the volume that made it look covered.
    recipients = {"CTO": MIN_RECIPIENTS + 20}
    (content / PROFILE / "prospects" / "sequences" / "spec-a.csv").write_text(
        "email,title\n" + "".join(f"p{i}@x.example,CTO\n" for i in range(recipients["CTO"])),
        encoding="utf-8",
    )

    b = backlog(PROFILE, content_root=content, profiles_root=profiles)
    untouched = [c for c in b.unused if c.persona == "CTO" and c.signal == "Tide chart drifted"]
    assert untouched, (
        "backlog() did not report a cell whose persona is addressed in volume by another "
        "argument — it has acquired a recipient threshold, and with it the blind spot"
    )

    # Now the gate, applied to the same output: a cell is only a finding when its persona
    # holds FEWER than MIN_RECIPIENTS unaddressed rows. This persona holds many, addressed.
    gated = [c for c in b.unused if recipients.get(c.persona, 0) < MIN_RECIPIENTS]
    assert not [c for c in gated if c.persona == "CTO"], (
        "the simulated volume gate did not hide the cell, so this control proves nothing "
        "about why the real report has no gate — fix the simulation, not the module"
    )
    assert len(gated) < len(b.unused), "the gate must actually remove something"
