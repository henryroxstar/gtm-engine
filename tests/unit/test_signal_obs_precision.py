"""Measured precision: graded members can retire a source whose stated precision they contradict.

Fictional tenant ``realshape``. The grading file mirrors the shape of a real screen: a ``?`` row is
ungraded and is neither a hit nor a miss, and the identity of a row is ``(source, organisation)``.
"""

from __future__ import annotations

import datetime

from gtm_core.signal_obs import cli, due, precision, registry
from unit.conftest import World

TODAY = datetime.date(2026, 10, 1)
HEADER = "source,organisation,agentic_grade (A/B/C/?),cross_org_agents\n"


def _grades(
    world: World, rows: list[tuple[str, str, str]], name="source-agentic-screen-2026-10-01"
):
    folder = world.content_root / world.profile / "prospects"
    folder.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{s},{o},{g},unknown\n" for s, o, g in rows)
    (folder / f"{name}.csv").write_text(HEADER + body, encoding="utf-8")


def _rows(hits: int, misses: int, source="north-directory"):
    rows = [(source, f"Org {i} Ltd", "A") for i in range(hits)]
    return rows + [(source, f"Org {hits + i} Ltd", "B") for i in range(misses)]


def test_tally_counts_a_and_leaves_unknown_grades_out(signal_world):
    _grades(signal_world, _rows(8, 10) + [("north-directory", "Org Q Ltd", "?")])
    got = precision.measure("realshape", signal_world.content_root)
    assert got.by_source["north-directory"] == precision.Tally(8, 18)
    assert got.problems == []


def test_an_organisation_graded_twice_alike_counts_once(signal_world):
    _grades(
        signal_world, [("north-directory", "Org  One", "A"), ("north-directory", "org one", "A")]
    )
    assert precision.measure("realshape", signal_world.content_root).by_source[
        "north-directory"
    ] == precision.Tally(1, 1)


def test_conflicting_grades_leave_the_source_untallied_and_named(signal_world):
    _grades(signal_world, _rows(5, 10) + [("north-directory", "Org 0 Ltd", "C")])
    got = precision.measure("realshape", signal_world.content_root)
    assert "north-directory" not in got.by_source
    assert any(
        "graded more than one way" in p and "Org 0 Ltd".casefold() in p for p in got.problems
    )


def test_a_file_without_a_grade_column_is_not_read_and_says_so(signal_world):
    folder = signal_world.content_root / "realshape" / "prospects"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source-agentic-screen-x.csv").write_text(
        "source,organisation\na,b\n", encoding="utf-8"
    )
    got = precision.measure("realshape", signal_world.content_root)
    assert got.by_source == {} and "no agentic_grade column" in got.problems[0]


def test_a_file_that_fails_halfway_counts_for_nothing(signal_world):
    folder = signal_world.content_root / "realshape" / "prospects"
    folder.mkdir(parents=True, exist_ok=True)
    good = "".join(f"north-directory,Org {i} Ltd,B,unknown\n" for i in range(2000))
    (folder / "source-agentic-screen-x.csv").write_bytes(
        (HEADER + good).encode() + b"north-directory,Org \xff Ltd,A,unknown\n"
    )
    _grades(signal_world, _rows(9, 1), name="source-agentic-screen-y")
    got = precision.measure("realshape", signal_world.content_root)
    assert got.by_source["north-directory"] == precision.Tally(9, 10)
    assert any("source-agentic-screen-x.csv could not be read" in p for p in got.problems)


def test_a_stated_prior_that_the_graded_members_contradict_makes_the_source_inert(signal_world):
    _grades(signal_world, _rows(3, 9))
    reg = registry.load_registry(
        "realshape", "alpha", profiles_root=signal_world.profiles_root,
        today=TODAY, premises=set(), content_root=signal_world.content_root,
    )  # fmt: skip
    why = registry.inert_reason(reg.sources[0], TODAY)
    assert why and "3/12" in why and "8/10" in why
    assert reg.active == ()


def test_too_few_graded_members_do_not_override_the_prior(signal_world):
    _grades(signal_world, _rows(1, 8))
    reg = registry.load_registry(
        "realshape", "alpha", profiles_root=signal_world.profiles_root,
        today=TODAY, premises=set(), content_root=signal_world.content_root,
    )  # fmt: skip
    assert registry.inert_reason(reg.sources[0], TODAY) is None


def test_graded_members_that_agree_leave_the_source_live(signal_world):
    _grades(signal_world, _rows(9, 3))
    reg = registry.load_registry(
        "realshape", "alpha", profiles_root=signal_world.profiles_root,
        today=TODAY, premises=set(), content_root=signal_world.content_root,
    )  # fmt: skip
    assert [s.id for s in reg.active] == ["north-directory"]


def test_the_command_prints_the_divergence_and_exits_clean(signal_world, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(signal_world.content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(signal_world.profiles_root))
    _grades(signal_world, _rows(3, 9))
    assert cli.main(["precision", "--profile", "realshape", "--product", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "north-directory: stated 8/10; graded 3/12 (25%), below 70%" in out


def test_the_command_exits_nonzero_when_a_graded_id_is_not_in_the_registry(
    signal_world, monkeypatch, capsys
):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(signal_world.content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(signal_world.profiles_root))
    _grades(signal_world, _rows(3, 9) + _rows(2, 2, source="elsewhere"))
    assert cli.main(["precision", "--profile", "realshape", "--product", "alpha"]) == 2
    assert "elsewhere: 2/4 graded, but no source has this id" in capsys.readouterr().out


def test_due_names_a_grading_file_that_was_dropped(signal_world, capsys):
    folder = signal_world.content_root / "realshape" / "prospects"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "source-agentic-screen-x.csv").write_text("source,organisation\na,b\n", "utf-8")
    report = due.due_sources(
        "realshape", "alpha", content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root, today=TODAY,
    )  # fmt: skip
    due.print_report(report)
    assert "grading: source-agentic-screen-x.csv has no agentic_grade column" in (
        capsys.readouterr().out
    )


def test_the_command_exits_nonzero_when_the_grades_cannot_be_trusted(
    signal_world, monkeypatch, capsys
):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(signal_world.content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(signal_world.profiles_root))
    _grades(signal_world, _rows(2, 1) + [("north-directory", "Org 0 Ltd", "C")])
    assert cli.main(["precision", "--profile", "realshape", "--product", "alpha"]) == 2
    assert "Problem:" in capsys.readouterr().out
