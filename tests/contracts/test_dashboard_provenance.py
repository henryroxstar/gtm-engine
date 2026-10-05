"""F3 — the page says where each number came from, and which sections are read vs typed.

WHY THIS EXISTS (2026-09-30, G2/G8). The page carried no provenance at all: `sequence-stats.json`
is written by an AGENT from skill prose, with a date it types, and one date covered sixteen
sequences though the skill refreshed only those it touched. Nothing on the page distinguished a
figure read from a file on every render from one a person typed last Tuesday.

WHAT THIS FILE HOLDS. The registry of sources and section kinds, the table and its states, the
as-of readers (each reads its source's OWN content, never a file time), and the always-open
group. The section -> source MAPPING is `section_sources.py`, with its perturbation oracle in
tests/contracts/test_dashboard_section_sources.py; `SECTION_KIND` is held to that map there.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import provenance
from gtm_core.email_campaign_dashboard.config import (
    FIGURES_MAX_AGE_DAYS as LIMIT,
)
from gtm_core.email_campaign_dashboard.config import (
    INPUT_GLOBS,
    OPS_GROUPS,
    PROFILE_FILES,
    SECTIONS,
)
from tests.contracts.test_dashboard_ps20_trust import _stats
from tests.test_email_campaign_dashboard import _seed


def _model(tmp_path, *, days_old=1):
    profile = _seed(tmp_path)
    when = (datetime.now(UTC) - timedelta(days=days_old)).date().isoformat()
    _stats(tmp_path, profile, json.dumps({"fetched": when, "sequences": [{"id": "S1", "sent": 1}]}))
    return gd.build_model(profile, tmp_path), when


def _ops(page: str) -> str:
    return page.split('<section id="p-ops"', 1)[1].split("</section>", 1)[0]


def _group(page: str, gid: str) -> str:
    return _ops(page).split(f'data-section="{gid}"', 1)[1].split("</details>", 1)[0]


# --- the registry (T13, T16) -----------------------------------------------------------------


def test_every_source_glob_is_a_real_input():
    """T13 forward. A source row claiming a glob the inventory does not track would tell the
    operator a file is watched when a change to it passes `--check-fresh` silently."""
    tracked = set(INPUT_GLOBS) | set(PROFILE_FILES)
    for key, src in provenance.SOURCES.items():
        for glob in src["globs"]:
            assert glob in tracked, f"source {key!r} claims {glob!r}, which nothing tracks"


def test_every_tracked_input_belongs_to_some_source():
    """T13 reverse, and the direction that actually decays. A new glob added to `INPUT_GLOBS`
    with no source row is a file the page reads and the provenance table does not mention — the
    table then reads as complete while being short, which is worse than being visibly partial."""
    claimed = {g for src in provenance.SOURCES.values() for g in src["globs"]}
    orphans = sorted(set(INPUT_GLOBS) - claimed)
    assert orphans == [], f"tracked inputs no source row accounts for: {orphans}"
    assert sorted(set(PROFILE_FILES) - claimed) == []


def test_the_section_kind_list_covers_the_section_registry_exactly():
    """T16 — both directions, against the REGISTRY rather than a render: a section with nothing
    to show is legitimately absent from the page (`config.SECTIONS` is a subset rule), so a
    render-based check would silently stop covering whatever an empty fixture omits."""
    ids = {sid for tab in SECTIONS.values() for sid in tab}
    assert set(provenance.SECTION_KIND) == ids
    assert set(provenance.SECTION_KIND.values()) <= set(provenance.SECTION_KINDS)


def test_the_benchmarks_section_is_static_and_dates_itself_from_one_constant():
    """T16's tail. Published reference figures are the one thing on this page that is NOT read
    from the tenant's data — they are a researched constant, and a reader has no way to tell that
    from a live figure unless the page says so. §R14: the date comes from the constant."""
    from gtm_core.email_campaign_dashboard.config import BENCHMARKS_RESEARCHED

    assert provenance.SECTION_KIND["benchmarks"] == "static"
    assert provenance.SOURCES["benchmarks"]["as_of"] is not None
    assert BENCHMARKS_RESEARCHED == provenance.SOURCES["benchmarks"]["as_of"]({})


def test_the_sending_figures_are_agent_written_not_live():
    """G2 in one assertion. The figures are typed into a file by an agent following skill prose;
    calling them `live` on this list would be the exact claim the assessment disproved."""
    assert provenance.SECTION_KIND["sequence-table"] == "agent-written"


# --- the table (T14, T15, T17) ---------------------------------------------------------------


def test_one_row_per_source_and_the_figures_row_matches_the_header(tmp_path):
    """T14, and §4.5 surface agreement: the table and the header name ONE date because both read
    `m["figures"]`. Two surfaces re-deriving it is how they came to disagree."""
    m, day = _model(tmp_path)
    page = gd.render_html(m)
    group = _group(page, provenance.GROUP_ID)
    table = group.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert table.count("<tr>") == len(provenance.SOURCES)
    for src in provenance.SOURCES.values():
        assert src["label"] in group
    figures_row = [r for r in table.split("<tr>") if provenance.SOURCES["figures"]["label"] in r]
    assert len(figures_row) == 1
    assert day in figures_row[0]
    assert f"Sending figures from {day}" in page  # the header's own wording


def _everything_present(tmp_path, monkeypatch, *, days_old=1):
    """A profile where EVERY source resolves — the only fixture that can prove the group stays
    closed. Built on `_seed_all_inputs`, which exists for the same reason one tab over: the
    default fixture is missing most inputs, so "not current" is true of it permanently and a
    closed-group assertion against it would be vacuous."""
    from tests.contracts.test_dashboard_reads_are_inventoried import (
        _pin_profiles_root,
        _seed_all_inputs,
    )

    _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)
    base = pc._prospects_dir(profile, tmp_path)
    (base / "evals" / "lanes-state.jsonl").write_text(
        '{"email": "ada@analytical.example", "lane": "personalised"}\n', encoding="utf-8"
    )
    (tmp_path / profile / "history.jsonl").write_text(
        '{"event": "prospect_run", "ts": "2026-08-01T09:00:00Z"}\n', encoding="utf-8"
    )
    when = (datetime.now(UTC) - timedelta(days=days_old)).date().isoformat()
    _stats(tmp_path, profile, json.dumps({"fetched": when, "sequences": [{"id": "S1", "sent": 1}]}))
    return gd.build_model(profile, tmp_path)


def _opening_tag(page: str) -> str:
    return re.search(r'<details[^>]*data-section="sources"[^>]*>', page).group(0)


def test_the_sources_group_is_always_open_even_when_every_source_is_current(tmp_path, monkeypatch):
    """PRD F3: the group is ALWAYS open — "hiding the one table that says what is stale defeats
    it". This used to be conditional (`opens_itself`) and only happened to be open because the
    benchmarks row is permanently `static`.

    The model is forced to the state the old rule closed on: every row `current`, so there is
    nothing "not current" for a conditional to open on. Mutation caught: reinstating a
    condition on `open_` in `views_ops._ops_view` closes the group here.
    """
    m = _everything_present(tmp_path, monkeypatch)
    for row in m["sources"].values():
        row["state"] = "current"
    assert {r["state"] for r in m["sources"].values()} == {"current"}
    page = gd.render_html(m)
    assert _opening_tag(page).endswith(" open>")


def test_the_sources_group_is_open_when_a_source_is_old_too(tmp_path):
    stale, _ = _model(tmp_path, days_old=LIMIT + 1)
    page = gd.render_html(stale)
    assert 'data-section="' + provenance.GROUP_ID + '" open' in page
    assert stale["sources"]["figures"]["state"] == "old"


def test_no_conditional_open_rule_survives_in_the_provenance_module():
    """The rule was deleted, not renamed: nothing may offer a way to close the group again."""
    assert not hasattr(provenance, "opens_itself")


def test_the_group_states_the_limit_once_and_derives_it(tmp_path):
    """§R14 and §6 legibility: the limit is stated where the sources are, from the constant."""
    m, _ = _model(tmp_path)
    group = _group(gd.render_html(m), provenance.GROUP_ID)
    assert f"sending figures are flagged after {LIMIT} days" in group
    assert group.count("flagged after") == 1


def test_the_group_has_a_declared_home_in_ops_groups():
    """A card with no declared home is how the Operator notes tab sprawled one finding at a time
    (PS20 root cause 1). The group is an edit to `OPS_GROUPS`, not a free-floating card."""
    assert provenance.GROUP_ID in {gid for gid, _t, _b in OPS_GROUPS}
    blocks = next(b for gid, _t, b in OPS_GROUPS if gid == provenance.GROUP_ID)
    assert set(blocks) == {"sources-table", "section-kinds"}
    assert set(blocks) <= SECTIONS["ops"]


def test_the_table_header_is_not_a_machine_column(tmp_path):
    """I9 (tests/test_email_campaign_dashboard.py) — "State" is on the banned list: this page is
    written for a reader who does not work the tooling, so the column asks their question. Pinned
    here too, because I9 only fails once the table is on the page and this table is new."""
    m, _ = _model(tmp_path)
    group = _group(gd.render_html(m), provenance.GROUP_ID)
    assert "<th>Can you trust it?</th>" in group
    assert "<th>State</th>" not in group


def test_a_missing_source_reads_missing_and_an_unknown_state_reads_unknown(tmp_path):
    """T17 — both halves of absence. "missing" is a finding (the file the page wants is not
    there); "unknown" is the closed list refusing to render a word it does not know, which is
    what stops a typo or a new state leaking onto the page as if it meant something."""
    m, _ = _model(tmp_path)
    states = {k: v["state"] for k, v in m["sources"].items()}
    assert states["outcomes"] == "missing", "the fixture writes no outcomes.jsonl"
    assert states["figures"] == "current"
    assert states["benchmarks"] == "static"
    assert set(states.values()) <= set(provenance.SOURCE_STATES)

    m["sources"]["outcomes"]["state"] = "wharrgarbl"
    group = _group(gd.render_html(m), provenance.GROUP_ID)
    assert "wharrgarbl" not in group
    assert "unknown" in group


def test_old_figures_read_old_not_missing(tmp_path):
    """The three figure states are different work: refresh them, go set them up, or find out why
    the file cannot be read. One word for all three sends the reader to the wrong one."""
    old, _ = _model(tmp_path, days_old=LIMIT + 1)
    assert old["sources"]["figures"]["state"] == "old"

    profile = _seed(tmp_path)
    pc._pool_dir(profile, tmp_path).joinpath("sequence-stats.json").unlink()
    none = gd.build_model(profile, tmp_path)
    assert none["sources"]["figures"]["state"] == "missing"


# --- no mtime anywhere (T18) -----------------------------------------------------------------


def test_the_page_is_byte_identical_under_wildly_different_mtimes(tmp_path):
    """T18 — REPLACES "patch `Path.stat`", which misses `os.stat`, `os.path.getmtime` and
    `Path.lstat`. Real files, real mtimes, forty years apart: an as-of date read from a
    timestamp instead of from a source's own content would move and the page would differ.

    This is the property that makes a provenance date worth printing. A clone, a `cp -r` or a
    restore rewrites every mtime at once, so an mtime-derived "as of" is confidently wrong for
    the whole tree simultaneously — the same failure `page_inputs` uses digests to avoid.
    """
    import os

    m, _ = _model(tmp_path)
    first = gd.render_html(m)

    root = tmp_path / m["profile"]
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (0, 0))  # 1970
    aged = gd.render_html(gd.build_model(m["profile"], tmp_path))

    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (2_000_000_000, 2_000_000_000))  # 2033
    future = gd.render_html(gd.build_model(m["profile"], tmp_path))

    assert aged == future
    assert _group(aged, provenance.GROUP_ID) == _group(first, provenance.GROUP_ID)


def test_no_source_reader_touches_a_timestamp():
    """T18's structural half — the value check above only convicts a reader whose output HAPPENS
    to differ at the two mtimes it tries. Asserted by AST over the module, with a negative
    control, so a new as-of reader cannot quietly reach for `getmtime`."""
    import ast
    from pathlib import Path as P

    src = P(provenance.__file__).read_text(encoding="utf-8")
    banned = {"getmtime", "st_mtime", "getctime", "stat", "lstat"}
    hits = [
        node.attr
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Attribute) and node.attr in banned
    ]
    assert hits == [], f"provenance reads a timestamp: {hits}"
    assert [
        n.attr
        for n in ast.walk(ast.parse("import os\nx = os.path.getmtime('a')\n"))
        if isinstance(n, ast.Attribute) and n.attr in banned
    ] == ["getmtime"]


# --- as-of readers (F3, audit F-06 / red-team F8) --------------------------------------------
#
# Until 2026-10-02 a source with no reader could never be `old` and said "current" for any file
# that merely existed; the pre-flight row was always "—" because nothing wired the report in.
# Each reader below reads its source's OWN content; none reads a file time.


def _write(tmp_path, rel: str, text: str, profile: str = "acme"):
    path = tmp_path / profile / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _states(tmp_path, m=None):
    return provenance.source_states(m or {}, "acme", tmp_path)


def _row(tmp_path, key, m=None):
    return _states(tmp_path, m)[key]


def _cell(tmp_path, key, m=None) -> str:
    """The rendered "As of" cell of one source's row, from the real table builder."""
    full = {"sources": _states(tmp_path, m)}
    html = provenance.sources_table(full)
    label = provenance.SOURCES[key]["label"]
    row = [r for r in html.split("<tr>") if label in r][0]
    return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)[1]


def test_the_outcomes_row_is_dated_by_its_newest_row(tmp_path):
    _write(
        tmp_path,
        "outcomes.jsonl",
        '{"ts": "2026-08-01T09:00:00Z", "outcome": "reply"}\n'
        '{"ts": "2026-09-14T17:30:00+08:00", "outcome": "meeting"}\n'
        '{"ts": "2026-07-02T00:00:00Z", "outcome": "reply"}\n',
    )
    row = _row(tmp_path, "outcomes")
    assert (row["state"], row["as_of"]) == ("current", "2026-09-14")
    assert _cell(tmp_path, "outcomes") == "2026-09-14"


def test_a_two_year_old_outcomes_file_shows_its_date_and_is_not_invented_old(tmp_path):
    """The PRD defines an age limit for the sending figures ONLY. The table must put the date
    in front of the reader; it may not invent a second limit and call this "old"."""
    _write(tmp_path, "outcomes.jsonl", '{"ts": "2024-01-01T00:00:00Z"}\n')
    row = _row(tmp_path, "outcomes")
    assert row["as_of"] == "2024-01-01" and row["state"] == "current"
    assert provenance.SOURCE_STATES.count("old") == 1 and "old" not in {
        _row(tmp_path, k)["state"] for k in provenance.SOURCES if k != "figures"
    }


def test_a_source_whose_file_records_no_date_says_so_and_is_not_called_more_than_present(tmp_path):
    _write(tmp_path, "outcomes.jsonl", '{"outcome": "reply"}\n\n{"outcome": "meeting"}\n')
    row = _row(tmp_path, "outcomes")
    assert row["state"] == "undated" and row["as_of"] is None
    assert _cell(tmp_path, "outcomes") == "no date in the file"
    _write(tmp_path, "outcomes.jsonl", "")
    assert _row(tmp_path, "outcomes")["state"] == "undated"
    assert _cell(tmp_path, "outcomes") == "no date in the file"


def test_a_malformed_timestamp_reads_unknown_and_never_crashes(tmp_path):
    _write(tmp_path, "outcomes.jsonl", '{"ts": "last tuesday"}\n{"ts": "2026-13-45"}\n')
    row = _row(tmp_path, "outcomes")
    assert row["state"] == "unknown" and row["as_of"] is None
    html = provenance.sources_table({"sources": _states(tmp_path)})
    assert "it could not be read" in html
    # a non-JSON line with nothing dated beside it is the same finding, not a traceback
    _write(tmp_path, "outcomes.jsonl", "{broken\n")
    assert _row(tmp_path, "outcomes")["state"] == "unknown"
    # an unreadable ts that is not even text
    _write(tmp_path, "outcomes.jsonl", '{"ts": 20260901}\n')
    assert _row(tmp_path, "outcomes")["state"] == "unknown"


def test_one_unreadable_row_does_not_hide_the_newest_readable_date(tmp_path):
    _write(
        tmp_path,
        "outcomes.jsonl",
        '{"ts": "garbage"}\n{broken\n{"ts": "2026-09-01T00:00:00Z"}\n',
    )
    row = _row(tmp_path, "outcomes")
    assert (row["state"], row["as_of"]) == ("current", "2026-09-01")


def test_the_sorted_list_is_dated_by_its_newest_lane_stamp(tmp_path):
    _write(
        tmp_path,
        "prospects/evals/lanes-state.jsonl",
        '{"email": "a@x.example", "lane": "generic", "stamp": "2026-09-01"}\n'
        '{"email": "b@x.example", "lane": "generic", "stamp": "2026-09-20"}\n'
        '{"email": "c@x.example", "lane": "generic"}\n',
    )
    row = _row(tmp_path, "sorted-list")
    assert (row["state"], row["as_of"]) == ("current", "2026-09-20")


def test_the_ledger_is_dated_by_the_run_historys_newest_row(tmp_path):
    _write(tmp_path, "prospects/latest.json", '{"items": []}')
    assert _cell(tmp_path, "ledger") == "no date in the file"
    _write(
        tmp_path,
        "history.jsonl",
        '{"event": "prospect_run", "ts": "2026-08-01T09:00:00Z"}\n'
        '{"event": "prospect_run", "ts": "2026-09-30T08:15:00Z"}\n',
    )
    row = _row(tmp_path, "ledger")
    assert (row["state"], row["as_of"]) == ("current", "2026-09-30")


def test_the_checks_row_is_dated_by_the_reports_own_ran_at(tmp_path):
    from gtm_core.prospect_readiness import Readiness

    _write(tmp_path, "preflight/latest.json", "{}")
    ok = Readiness(state="ok", ran_at="2026-09-20T10:00:00Z")
    row = _row(tmp_path, "checks", {"readiness": ok})
    assert (row["state"], row["as_of"]) == ("current", "2026-09-20")
    # no report loaded, or one with no run time: no date, said so
    assert _cell(tmp_path, "checks", {"readiness": None}) == "no date in the file"
    assert (
        _cell(tmp_path, "checks", {"readiness": Readiness(state="none")}) == "no date in the file"
    )
    # a ran_at that is text but not a date is unknown, never a crash and never a copied string
    bad = Readiness(state="stale", ran_at="whenever")
    assert _row(tmp_path, "checks", {"readiness": bad})["state"] == "unknown"
    # a report that exists and cannot be read is unknown too
    assert _row(tmp_path, "checks", {"readiness": Readiness(state="unreadable")})["state"] == (
        "unknown"
    )


def test_the_checks_date_reaches_the_model_through_the_real_report(tmp_path, monkeypatch):
    """The wiring, not the reader: `page_extras` used to pass ``readiness: None`` so the row was
    "—" for every tenant. Written by hand the way `preflight_report.write_report` lays it out."""
    from gtm_core.prospect_readiness import FATES
    from tests.contracts.test_dashboard_reads_are_inventoried import (
        _pin_profiles_root,
        _seed_all_inputs,
    )

    _pin_profiles_root(tmp_path, monkeypatch)
    profile = _seed_all_inputs(tmp_path)
    block = {"rows": 0, "fates": dict.fromkeys(FATES, 0), "lanes": [], "inputs": {}}
    (tmp_path / profile / "preflight" / "latest.json").write_text(
        json.dumps({"ran_at": "2026-09-18T07:00:00Z", "readiness": block}), encoding="utf-8"
    )
    m = gd.build_model(profile, tmp_path)
    assert m["readiness"].ran_at == "2026-09-18T07:00:00Z"
    assert m["sources"]["checks"]["as_of"] == "2026-09-18"
    assert "2026-09-18" in provenance.sources_table(m)


def test_a_review_sheet_whose_dated_name_is_not_a_date_is_unknown(tmp_path):
    _write(tmp_path, "prospects/evals/hold-2026-13-45.csv", "email\n")
    from gtm_core.email_campaign_dashboard.health import review_sheet

    m = {"review_sheet": review_sheet("acme", tmp_path)}
    assert m["review_sheet"]["stamp"] == "2026-13-45"
    assert _row(tmp_path, "review", m)["state"] == "unknown"
    (tmp_path / "acme" / "prospects" / "evals" / "hold-2026-13-45.csv").unlink()
    _write(tmp_path, "prospects/evals/hold-2026-09-22.csv", "email\n")
    m = {"review_sheet": review_sheet("acme", tmp_path)}
    assert _row(tmp_path, "review", m)["as_of"] == "2026-09-22"


def test_a_missing_source_has_no_date_and_the_readers_are_not_asked(tmp_path):
    row = _row(tmp_path, "outcomes")
    assert (row["state"], row["as_of"]) == ("missing", None)
    assert _cell(tmp_path, "outcomes") == "—"


def test_no_row_says_current_without_a_date(tmp_path, monkeypatch):
    """The honesty rule as a property of the rendered table, on the fixture where every source
    resolves: a row that says "current" carries a date; one that records none says so in BOTH
    cells — "no date in the file" under As of and "present" under the state — because "current"
    beside "no date" reads as a freshness the page never checked (red team r12)."""
    m = _everything_present(tmp_path, monkeypatch)
    group = _group(gd.render_html(m), provenance.GROUP_ID)
    tbody = group.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    rows = [re.findall(r"<td[^>]*>(.*?)</td>", r, re.S) for r in tbody.split("<tr>") if "<td" in r]
    current = [c for c in rows if c[2] == "current"]
    assert current, "fixture shows no current row, so the rule would be vacuous"
    for _label, as_of, _state in current:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of), f"'current' with no date: {as_of!r}"
    undated = [c for c in rows if c[2].startswith("present")]
    assert undated, "fixture shows no undated row, so the other half would be vacuous"
    for _label, as_of, _state in undated:
        assert as_of == "no date in the file", as_of


def test_hand_recorded_only_sections_are_agent_written_not_live():
    """Audit F8: the outcomes row says "recorded by hand" while the two sections that read ONLY
    that file were labelled `live` ("re-read from your own data"). One definition of "typed"
    (`TYPED_SOURCES`) now serves both lists."""
    assert "outcomes" in provenance.TYPED_SOURCES and "figures" in provenance.TYPED_SOURCES
    for sid in ("voice-of-market", "sentiment-triage"):
        assert provenance.SECTION_KIND[sid] == "agent-written", sid


# --- the state word is as honest as the date (r12) and the figures' own states (B17) ---------


def test_the_source_states_list_names_undated_and_nothing_invents_an_age_limit():
    assert "undated" in provenance.SOURCE_STATES
    assert provenance.SOURCE_STATES.count("old") == 1  # only the figures can be old


def test_a_file_that_merely_exists_is_present_not_current(tmp_path):
    """r12. `current` means "nothing to do" and, beside a date, "checked". A source that records
    no date has neither, so its state says only that it is there."""
    _write(tmp_path, "plans/campaigns/q.campaign.toml", 'slug = "q"\n')
    row = _row(tmp_path, "plans")
    assert (row["state"], row["as_of"]) == ("undated", None)
    html = provenance.sources_table({"sources": _states(tmp_path)})
    plans = [r for r in html.split("<tr>") if provenance.SOURCES["plans"]["label"] in r][0]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", plans, re.S)
    assert cells[1] == "no date in the file" and cells[2].startswith("present")
    assert "current" not in cells[2]


def test_a_dated_source_still_says_current(tmp_path):
    _write(tmp_path, "outcomes.jsonl", '{"ts": "2026-09-14T00:00:00Z"}\n')
    assert _row(tmp_path, "outcomes")["state"] == "current"


def test_the_undated_state_is_never_old_so_no_age_limit_is_invented(tmp_path):
    _write(tmp_path, "outcomes.jsonl", '{"outcome": "reply"}\n')
    assert _row(tmp_path, "outcomes")["state"] == "undated"
    assert _row(tmp_path, "outcomes")["state"] != "old"


@pytest.mark.parametrize(
    "fig,want",
    [
        ({"state": "none", "over_limit": False}, "missing"),
        # `over_limit` is deliberately False: the verdict must come from the state, so a body that
        # fell through to the age test would say "current" here (mutant B17).
        ({"state": "unreadable", "over_limit": False}, "unknown"),
        ({"state": "unreadable", "over_limit": True}, "unknown"),
        ({"state": "undated", "over_limit": True}, "old"),
        ({"state": "future", "over_limit": True}, "old"),
        ({"state": "dated", "over_limit": True, "date": "2026-09-01"}, "old"),
        ({"state": "dated", "over_limit": False, "date": "2026-10-01"}, "current"),
        ({}, "unknown"),
        ({"state": "wharrgarbl", "over_limit": False}, "unknown"),
    ],
    ids=[
        "none",
        "unreadable-under",
        "unreadable-over",
        "undated",
        "future",
        "dated-over",
        "dated-under",
        "no-figures-at-all",
        "a-state-nobody-defined",
    ],
)
def test_the_figures_state_word_comes_from_the_resolved_state(fig, want):
    assert provenance._figures_state({"figures": fig}) == want


def test_unreadable_figures_read_unknown_in_the_table_under_a_header_that_says_so(tmp_path):
    """B17 end to end. The strip/header says the sending tool's figures could not be read; the
    sources table, one scroll away, must not call the same file anything but unknown."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, "{not json")
    m = gd.build_model(profile, tmp_path)
    assert m["figures"]["state"] == "unreadable"
    assert m["sources"]["figures"]["state"] == "unknown"
    page = gd.render_html(m)
    assert re.search(r"couldn(?:&#x27;|&#39;|')t be read", page), "the header no longer says so"
    group = _group(page, provenance.GROUP_ID)
    row = [r for r in group.split("<tr>") if provenance.SOURCES["figures"]["label"] in r][0]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    assert cells[1] == "—" and cells[2] == "unknown — it could not be read"
    assert "current" not in cells[2]
