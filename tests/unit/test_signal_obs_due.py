"""`due`: which sources need a fresh capture, and the manifest that lets a run fetch only those."""

from __future__ import annotations

import datetime
import json

from gtm_core import capture_manifest
from gtm_core.signal_obs import due
from unit.conftest import SOURCE_URL, page

TODAY = datetime.date(2026, 10, 1)


def _due(w, **kw):
    return due.due_sources(
        w.profile,
        w.product,
        content_root=w.content_root,
        profiles_root=w.profiles_root,
        today=TODAY,
        **kw,
    )


def test_a_source_never_captured_is_due(signal_world):
    report = _due(signal_world)
    assert [(s.id, why) for s, why in report.due] == [("north-directory", "never captured")]


def test_cadence_decides_between_fresh_and_stale(signal_world):
    signal_world.capture(page("Northwind Traders"), "2026-09-11T08:00:00+00:00")  # 20 days ago
    assert _due(signal_world).due == []
    signal_world.capture(page("Northwind Traders") + "x", "2026-08-29T08:00:00+00:00")
    assert _due(signal_world).due == []  # the newest capture counts, not an older one
    signal_world.registry()  # cadence 30
    stale = due.due_sources(
        signal_world.profile,
        signal_world.product,
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
        today=datetime.date(2026, 10, 12),
    )
    assert [s.id for s, _ in stale.due] == ["north-directory"]


def test_an_inert_source_is_never_due_and_says_why(signal_world):
    signal_world.registry(precision="6/10")
    report = _due(signal_world)
    assert report.due == []
    assert report.inert[0][0].id == "north-directory" and "precision" in report.inert[0][1]


def test_write_manifest_lists_exactly_the_due_urls_with_the_pinned_options(signal_world):
    path = due.write_due_manifest(
        signal_world.profile,
        signal_world.product,
        run_id="run-7",
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
        today=TODAY,
    )
    data = json.loads(path.read_text())
    assert data["urls"] == [SOURCE_URL] and data["cap"] == 1
    assert (
        data["options"]["formats"] == ["markdown"] and data["options"]["onlyMainContent"] is False
    )
    assert path == signal_world.sources_dir / "manifest-run-7.json"
    assert capture_manifest.load_manifest(path).urls == [SOURCE_URL]


def test_nothing_due_writes_no_manifest(signal_world):
    signal_world.capture(page("Northwind Traders"), "2026-09-30T08:00:00+00:00")
    path = due.write_due_manifest(
        signal_world.profile,
        signal_world.product,
        run_id="run-8",
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
        today=TODAY,
    )
    assert path is None
    assert not list(signal_world.sources_dir.glob("manifest-*.json"))


def test_the_report_names_the_registry_file_it_read(signal_world, capsys):
    due.print_report(_due(signal_world))
    assert "signal-sources.toml" in capsys.readouterr().out


# --- the cap counts pages (PRD §4A; verification audit 2026-10-02, Critical 5(d)) ---------------
# The manifest used to take every due URL, so 50 due sources meant 50 paid scrapes with nothing
# deferred. A run now takes at most a page budget, and the rest wait for the next run.

from types import SimpleNamespace  # noqa: E402


def _fake_report(n):
    sources = [SimpleNamespace(url=f"https://r{i}.example.test/") for i in range(n)]
    return due.DueReport(registry=None, due=[(s, "never captured") for s in sources])


def test_a_run_takes_at_most_the_page_budget_and_defers_the_rest_in_order():
    chosen, deferred = due.pages_for_run(_fake_report(25), 10)
    assert [s.url for s, _ in chosen] == [f"https://r{i}.example.test/" for i in range(10)]
    assert len(deferred) == 15 and deferred[0][0].url == "https://r10.example.test/"


def test_the_default_page_budget_is_ten():
    chosen, deferred = due.pages_for_run(_fake_report(25), None)
    assert len(chosen) == 10 and len(deferred) == 15


def test_fewer_due_than_the_budget_defers_nothing():
    chosen, deferred = due.pages_for_run(_fake_report(4), 10)
    assert len(chosen) == 4 and deferred == []


def test_the_manifest_lists_the_budget_and_its_cap_equals_the_pages_listed(
    signal_world, monkeypatch
):
    monkeypatch.setattr(due, "due_sources", lambda *a, **k: _fake_report(25))
    path = due.write_due_manifest(
        signal_world.profile,
        signal_world.product,
        run_id="run-9",
        content_root=signal_world.content_root,
        profiles_root=signal_world.profiles_root,
        today=TODAY,
        max_pages=6,
    )
    data = json.loads(path.read_text())
    assert len(data["urls"]) == 6 and data["cap"] == 6


def test_the_page_budget_comes_from_the_profile_settings(signal_world):
    d = signal_world.content_root / signal_world.profile
    (d / "settings.json").write_text(
        json.dumps({"signal_monitor_max_captures": 3}), encoding="utf-8"
    )
    assert due.max_pages_setting(signal_world.profile, signal_world.content_root) == 3


import pytest  # noqa: E402


@pytest.mark.parametrize("bad", ["ten", 0, -4, True, None, 2.5, 100000, [1]])
def test_a_missing_or_nonsense_page_budget_falls_back_to_the_default(signal_world, bad):
    d = signal_world.content_root / signal_world.profile
    (d / "settings.json").write_text(
        json.dumps({"signal_monitor_max_captures": bad}), encoding="utf-8"
    )
    assert (
        due.max_pages_setting(signal_world.profile, signal_world.content_root)
        == due.DEFAULT_MAX_PAGES
    )


def test_no_settings_file_means_the_default(tmp_path):
    assert due.max_pages_setting("nobody", tmp_path) == due.DEFAULT_MAX_PAGES
