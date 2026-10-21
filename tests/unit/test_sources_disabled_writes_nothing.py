"""K1: with the source-list switch closed, every `signal_obs` command does nothing and says why."""

from __future__ import annotations

import pytest

from gtm_core.signal_obs import cli
from unit.conftest import page

SETTING = "GTM_SIGNAL_SOURCES_ENABLED"
COMMANDS = [
    ["check"],
    ["due"],
    ["due", "--write-manifest", "--run-id", "k1"],
    ["extract", "--source", "north-directory"],
    ["extract", "--source", "north-directory", "--rebaseline"],
    ["review"],
    ["review", "--apply", "missing-sheet.csv"],
    ["repair", "--shard", "amy.r1-2026-10.jsonl", "--apply"],
]


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def tenant(signal_world, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(signal_world.content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(signal_world.profiles_root))
    signal_world.capture(page("Northwind Traders"), "2026-10-01T08:00:00+00:00")
    return signal_world


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a))
@pytest.mark.parametrize("value", [None, "", "0", "false", "maybe", "  "])
def test_a_closed_switch_exits_zero_names_the_setting_and_touches_nothing(
    tenant, monkeypatch, capsys, argv, value
):
    if value is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, value)
    before = _snapshot(tenant.content_root) | _snapshot(tenant.profiles_root.parent)
    code = cli.main([argv[0], "--profile", "realshape", "--product", "alpha", *argv[1:]])
    out = capsys.readouterr().out
    assert code == 0 and SETTING in out
    assert _snapshot(tenant.content_root) | _snapshot(tenant.profiles_root.parent) == before


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_an_open_switch_lets_the_command_run(tenant, monkeypatch, capsys, value):
    monkeypatch.setenv(SETTING, value)
    assert cli.main(["check", "--profile", "realshape", "--product", "alpha"]) == 0
    assert "North members list" in capsys.readouterr().out


def test_library_entry_points_hold_the_switch_too_not_only_the_command_line(tenant, monkeypatch):
    """Anything that reaches the code without the CLI (a skill, a script) is held as well."""
    import datetime

    from gtm_core.signal_obs import due, extract, repair, review
    from gtm_core.signal_obs.switch import SwitchClosed

    monkeypatch.delenv(SETTING, raising=False)
    before = _snapshot(tenant.content_root)
    kw = {"content_root": tenant.content_root, "profiles_root": tenant.profiles_root}
    today = datetime.date(2026, 10, 1)
    rep = extract.run_extract("realshape", "north-directory", product="alpha", today=today, **kw)
    assert rep.status == "disabled" and SETTING in rep.reason
    for call in (
        lambda: review.pending("realshape", "alpha", today=today, **kw),
        lambda: review.apply_sheet("realshape", "", product="alpha", apply=True, today=today, **kw),
        lambda: due.write_due_manifest("realshape", "alpha", run_id="k", today=today, **kw),
        lambda: repair.run_repair("realshape", "alpha", "amy.r1-2026-10.jsonl", apply=True, **kw),
    ):
        with pytest.raises(SwitchClosed):
            call()
    assert _snapshot(tenant.content_root) == before


def test_the_write_primitives_refuse_on_their_own_while_the_switch_is_closed(tmp_path, monkeypatch):
    from gtm_core.signal_obs import observations, state, unresolved
    from gtm_core.signal_obs.switch import SwitchClosed

    monkeypatch.delenv(SETTING, raising=False)
    entry = {"product": "alpha", "source_id": "s", "name": "Northwind Traders"}
    for call in (
        lambda: observations.append_lines(tmp_path / "x.jsonl", "{}\n"),
        lambda: unresolved.record(tmp_path, [entry]),
        lambda: unresolved.dismiss(tmp_path, entry),
        lambda: state.save_state(tmp_path, "alpha", "s", {"status": "ok", "members": {}}),
        lambda: observations.repair(tmp_path, "amy.r1-2026-10.jsonl", apply=True),
    ):
        with pytest.raises(SwitchClosed):
            call()
    assert list(tmp_path.iterdir()) == []
