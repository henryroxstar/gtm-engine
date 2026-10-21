"""R2.7, R2.8, R1.2, R1.6, R6.3: the plain lines about source lists that the record section shows.

One function, ``signal_first_status.record_lines``, feeds the terminal report and the status page, so
the two cannot word the same fact two ways. The lines are plain words for the operator (no setting
jargon beyond the two setting names, no file names, no counts without what they mean).
"""

from __future__ import annotations

import builtins
import datetime
import json

from gtm_core import signal_first_status as sfs
from gtm_core.signal_obs import switch, unresolved
from unit.test_signal_view import TODAY, world  # noqa: F401 (the fixture)


def _lines(w, **kw):
    return sfs.record_lines(
        w.profile,
        w.product,
        profiles_root=w.profiles_root,
        content_root=w.content_root,
        today=TODAY,
        **kw,
    )


# --- the mode line (R2.8) ---------------------------------------------------------------------


def test_with_the_source_switch_closed_the_line_says_off_and_names_the_setting(world, monkeypatch):  # noqa: F811
    monkeypatch.delenv(switch.SETTING)
    lines = _lines(world)
    assert lines[0].startswith("Source lists: off.")
    assert switch.SETTING in lines[0]
    assert len(lines) == 1


def test_with_the_source_switch_closed_no_observation_path_is_opened(world, monkeypatch):  # noqa: F811
    """K1: the status read must not touch the observation files while lists are off."""
    monkeypatch.delenv(switch.SETTING)
    real_open = builtins.open
    seen: list[str] = []

    def spy(file, *a, **k):
        seen.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    _lines(world)
    assert not [p for p in seen if "observations" in p]


def test_collecting_only_names_the_second_setting(world, monkeypatch):  # noqa: F811
    monkeypatch.delenv(switch.VIEW_SETTING, raising=False)
    first = _lines(world)[0]
    assert first.startswith("Source lists: collecting only.") and switch.VIEW_SETTING in first


def test_routing_open_says_lists_choose_emails(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    assert _lines(world)[0] == "Source lists: used for choosing emails."


# --- the shadow line (R2.7) -------------------------------------------------------------------


def test_the_shadow_line_counts_companies_a_list_would_qualify_while_routing_is_closed(  # noqa: F811
    world,  # noqa: F811
    monkeypatch,
):
    monkeypatch.delenv(switch.VIEW_SETTING, raising=False)
    lines = _lines(world)
    assert any("1 company is on an approved industry list" in ln for ln in lines)


def test_no_shadow_line_once_routing_is_open_because_it_is_applied_not_shadowed(  # noqa: F811
    world,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    assert not any("approved industry list" in ln for ln in _lines(world))


def test_the_shadow_line_never_reaches_the_lede_words(world):  # noqa: F811
    """The lines are record-section lines: nothing here is phrased as a headline count."""
    for ln in _lines(world):
        assert not ln.lower().startswith(("ready", "waiting on you", "sendable"))


# --- the run header sentence (R1.2) -----------------------------------------------------------


def test_an_invalid_registry_gives_one_plain_sentence_and_the_run_goes_on(world):  # noqa: F811
    (world.profiles_root / world.profile / "knowledge" / "signal-sources.toml").write_text(
        "schema = [", encoding="utf-8"
    )
    lines = _lines(world)
    assert lines[0].startswith("Source lists:")
    note = [ln for ln in lines if ln.startswith("Sources are off for this run")]
    assert len(note) == 1 and "signal-sources.toml" in note[0]


def test_a_refused_shard_is_named(world):  # noqa: F811
    (world.obs_dir / "zed-2026-10.jsonl").write_text(
        json.dumps({"schema": 2, "kind": "x"}) + "\n", encoding="utf-8"
    )
    assert any("zed-2026-10.jsonl" in ln and "pull and upgrade" in ln for ln in _lines(world))


# --- names waiting for a decision (R1.6) ------------------------------------------------------


def _queue(w, names, first_seen="2026-09-25"):
    unresolved.record(
        w.obs_dir,
        [
            {
                "product": w.product,
                "source_id": "north-directory",
                "name": n,
                "first_seen": first_seen,
                "candidate_domain": "",
                "reason": "no domain on the page",
                "status": "open",
            }
            for n in names
        ],
    )


def test_names_waiting_for_a_decision_are_counted_in_plain_words(world):  # noqa: F811
    _queue(world, ["Alpha Freight", "Beta Foods", "Gamma Labs"])
    lines = _lines(world)
    waiting = [ln for ln in lines if "waiting" in ln]
    assert len(waiting) == 1 and waiting[0].startswith("3 names found on source lists are waiting")
    assert "which company" in waiting[0]


def test_one_name_waiting_reads_in_the_singular(world):  # noqa: F811
    _queue(world, ["Alpha Freight"])
    assert any(ln.startswith("1 name found on a source list is waiting") for ln in _lines(world))


def test_no_names_waiting_means_no_line(world):  # noqa: F811
    assert not any("waiting" in ln for ln in _lines(world))


def test_a_name_older_than_ninety_days_is_not_counted(world):  # noqa: F811
    _queue(world, ["Old Name"], first_seen="2026-06-01")
    assert not any("waiting" in ln for ln in _lines(world))


def test_the_count_is_not_capped_at_the_sheet_size(world):  # noqa: F811
    _queue(world, [f"Company Number {i}" for i in range(63)])
    assert any(ln.startswith("63 names found on source lists are waiting") for ln in _lines(world))


# --- the outcome table (R6.3) -----------------------------------------------------------------


def _enrol(w, email, cls="source_list", seq="s1"):
    d = w.content_root / w.profile / "prospects"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "enrolments.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "email": email,
                    "sequence_id": seq,
                    "cell_id": "c",
                    "signal_class": cls,
                    "premise_via": "source",
                    "source_id": "north-directory",
                    "dispatched_at": "2026-10-01T00:00:00Z",
                }
            )
            + "\n"
        )


def test_with_nobody_tracked_there_is_no_outcome_section(world):  # noqa: F811
    assert not any("enrolled" in ln for ln in _lines(world))


def test_tracked_people_get_the_plain_table_and_the_record_interval(world):  # noqa: F811
    _enrol(world, "a@x.test")
    _enrol(world, "b@x.test", cls="")
    lines = _lines(world)
    text = "\n".join(lines)
    assert "Replies by what each person was enrolled on:" in text
    assert "on a published list: 1 enrolled" in text
    assert "too few to read (n < 100)" in text
    assert "95% Beta(1,1) interval" in text  # the interval lives in the record section only


def test_the_outcome_lines_are_the_ones_the_report_prints(world):  # noqa: F811
    from gtm_core import signal_outcomes as so

    _enrol(world, "a@x.test")
    rep = so.report(world.content_root, world.profile)
    for ln in so.plain_lines(rep) + so.record_lines(rep):
        assert ln in _lines(world)


# --- the product scope ------------------------------------------------------------------------


def test_an_ambiguous_product_gets_the_mode_line_and_a_plain_request_to_name_one(  # noqa: F811
    world,  # noqa: F811
    monkeypatch,
):
    from gtm_core import run_scope

    def boom(*a, **k):
        raise run_scope.ScopeError("product-ambiguous", "two products here")

    monkeypatch.setattr(run_scope, "require", boom)
    lines = _lines(world)
    assert lines[0].startswith("Source lists: collecting only")
    assert any("Name a product" in ln for ln in lines)


def test_the_status_lines_write_nothing(world, tmp_path):  # noqa: F811
    _queue(world, ["Alpha Freight"])
    before = sorted(
        (p.name, p.stat().st_mtime_ns) for p in world.content_root.rglob("*") if p.is_file()
    )
    _lines(world)
    after = sorted(
        (p.name, p.stat().st_mtime_ns) for p in world.content_root.rglob("*") if p.is_file()
    )
    assert before == after


def test_the_operator_vocabulary_lint_passes_on_every_line(world):  # noqa: F811
    from tests.lint import operator_vocabulary as ov

    _queue(world, ["Alpha Freight"])
    _enrol(world, "a@x.test")
    text = "\n".join(_lines(world))
    assert ov._scan("record_lines", text) == []


def test_the_lines_follow_the_run_date_not_the_wall_clock(world):  # noqa: F811
    """A name first seen 2026-09-25 is waiting on 2026-10-01 and has aged out (90 days) by 2026-12-30."""
    _queue(world, ["Alpha Freight"], first_seen="2026-09-25")

    def waiting(day):
        lines = sfs.record_lines(
            world.profile,
            world.product,
            profiles_root=world.profiles_root,
            content_root=world.content_root,
            today=day,
        )
        return any("waiting" in ln for ln in lines)

    assert waiting(datetime.date(2026, 10, 1)) is True
    assert waiting(datetime.date(2026, 12, 30)) is False
