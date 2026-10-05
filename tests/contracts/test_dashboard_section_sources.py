"""The section -> source map is a claim, so it is tested the way a claim is: by perturbation.

For each source, delete its files from a seeded tree, re-render, and assert that every section
whose HTML changed is in the source's mapped set (``changed ⊆ mapped``). The direction means an
over-claiming map passes and an under-claiming one fails. A positive control per globbed source
proves the deletion was seen at all — otherwise a perturbation that touches nothing would pass
every assertion in this file.

What this cannot do, said plainly: the oracle runs on ONE fixture. A section that renders
nothing there is mapped by reading the code and pinned in ``UNWITNESSED``; the test fails if one
starts rendering, which is the cue to witness it. The dossier lookup feeds no rendered section,
and a test pins that, so a consumer appearing forces a mapping.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import page_inputs
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import provenance, section_sources, views_ops
from gtm_core.email_campaign_dashboard.config import (
    NAME_GLOBS,
    OPS_GROUPS,
    SECTIONS,
    input_globs,
)
from tests.contracts.test_dashboard_ps20_trust import _stats
from tests.contracts.test_dashboard_reads_are_inventoried import (
    _pin_profiles_root,
    _seed_all_inputs,
)

ALL_IDS = set().union(*SECTIONS.values())
_VOID = {"br", "img", "input", "meta", "link", "hr"}


class _Sections(HTMLParser):
    """``data-section`` id -> the markup inside it (nested sections count for the outer one)."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str | None]] = []
        self.active: list[str] = []
        self.out: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        text = self.get_starttag_text()
        for sid in self.active:
            self.out[sid].append(text)
        if tag in _VOID:
            return
        sid = dict(attrs).get("data-section")
        self.stack.append((tag, sid))
        if sid:
            self.active.append(sid)
            self.out.setdefault(sid, []).append(text)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        while self.stack:
            t, sid = self.stack.pop()
            if sid:
                self.active = [a for a in self.active if a != sid]
            if t == tag:
                break
        for sid in self.active:
            self.out[sid].append(f"</{tag}>")

    def handle_data(self, data):
        for sid in self.active:
            self.out[sid].append(data)


def _sections(html: str) -> dict[str, str]:
    p = _Sections()
    p.feed(html)
    return {k: "".join(v) for k, v in p.out.items()}


def _render(profile: str, root: Path) -> dict[str, str]:
    return _sections(gd.render_html(gd.build_model(profile, root)))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("section-sources")
    with pytest.MonkeyPatch.context() as mp:
        _pin_profiles_root(tmp, mp)
        profile = _seed_all_inputs(tmp)
        base = pc._prospects_dir(profile, tmp)
        (base / "evals" / "lanes-state.jsonl").write_text(
            '{"email": "ada@analytical.example", "lane": "personalised"}\n'
        )
        (tmp / profile / "history.jsonl").write_text(
            '{"event": "prospect_run", "ts": "2026-08-01T09:00:00Z"}\n'
            '{"event": "capability_asserted", "provider": "demo", "status": "PASS",'
            ' "sequence_id": "S1", "ts": "2026-08-02T09:00:00Z"}\n'
            '{"event": "optout_unreadable", "email": "x@y.example", "ts": "2026-08-03T09:00:00Z"}\n'
        )
        when = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        _stats(tmp, profile, json.dumps({"fetched": when, "sequences": [{"id": "S1", "sent": 1}]}))
        # A campaign whose roster export does NOT follow the `prospects-*-hubspot.csv`
        # convention: the manifest's own `roster_globs` is the only thing that names it.
        camp = base.parent / "plans" / "campaigns" / "c1.campaign.toml"
        camp.write_text(
            'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n'
            'roster_globs = ["roster-*.csv"]\n\n[targets]\nemails = 9\n'
        )
        (base / "roster-demo.csv").write_text(
            "Company,Email,First Name,Job Title,GTM_Segment,GTM_Tier,GTM_Why_Now,Country\n"
            "Zebra Quill Robotics,ada@zebraquill.example,Ada,Head of Platform,enterprise,A,"
            "ships an agent,Singapore\n"
            "Moss Harbor Labs,bo@mossharbor.example,Bo,CTO,startup,B,,Singapore\n"
        )
        yield profile, tmp, _render(profile, tmp)


def _source_files(profile: str, root: Path, key: str) -> list[Path]:
    src = provenance.SOURCES[key]
    if key == "knowledge":
        base = page_inputs._profile_root(profile)
        return [
            base / page_inputs._profile_rel(g)
            for g in src["globs"]
            if (base / page_inputs._profile_rel(g)).is_file()
        ]
    inv_root, _ = input_globs(profile, root)
    return [Path(p) for p in page_inputs._resolve(inv_root, list(src["globs"]))]


def _without(files: list[Path], render):
    held = {f: f.read_bytes() for f in files}
    try:
        for f in files:
            f.unlink()
        return render()
    finally:
        for f, data in held.items():
            f.write_bytes(data)


def _changed(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


# --- the registry ----------------------------------------------------------------------------


def test_the_map_covers_the_section_registry_exactly():
    assert set(section_sources.SECTION_SOURCES) == ALL_IDS


def test_every_mapped_source_is_a_real_source():
    for sid, srcs in section_sources.SECTION_SOURCES.items():
        assert srcs <= set(provenance.SOURCES), f"{sid!r} maps to an unknown source"


def test_every_source_feeds_at_least_one_section():
    for key in provenance.SOURCES:
        assert section_sources.sections_for(key), f"source {key!r} feeds no section"


def test_a_group_is_exactly_the_union_of_its_blocks():
    for gid, _title, blocks in OPS_GROUPS:
        expected = frozenset().union(*(section_sources.SECTION_SOURCES[b] for b in blocks))
        assert section_sources.SECTION_SOURCES[gid] == expected


def test_the_sources_table_depends_on_every_source():
    """The table prints each source's state, so any source changing can change it."""
    assert section_sources.SECTION_SOURCES["sources-table"] == frozenset(provenance.SOURCES)
    assert section_sources.SECTION_SOURCES["section-kinds"] == frozenset()


# --- the reverse direction: a glob -> its source ---------------------------------------------


def test_every_glob_a_source_names_maps_back_to_that_source():
    for key, src in provenance.SOURCES.items():
        for glob in src["globs"]:
            assert section_sources.source_for_glob(glob) == key


def test_a_manifest_roster_glob_with_any_basename_belongs_to_the_pool_source(world):
    profile, tmp, _ = world
    _root, globs = input_globs(profile, tmp)
    assert "prospects/roster-*.csv" in globs, "positive control: the fixture's own roster glob"
    assert section_sources.source_for_glob("prospects/roster-*.csv") is None
    assert section_sources.source_for_glob("prospects/roster-*.csv", ("roster-*.csv",)) == "pool"


def test_the_dossier_lookup_belongs_to_no_source():
    for glob in NAME_GLOBS:
        assert section_sources.source_for_glob(glob) is None


def test_every_tracked_input_resolves_to_a_source_or_is_the_dossier_lookup(world):
    profile, tmp, _ = world
    _root, globs = input_globs(profile, tmp)
    from gtm_core.campaigns_dashboard import _load_manifests

    rosters = tuple(g for m in _load_manifests(profile, tmp) for g in m.get("roster_globs") or [])
    orphans = [g for g in globs if section_sources.source_for_glob(g, rosters) is None]
    assert orphans == [], f"tracked inputs with no source: {orphans}"


# --- the oracle: perturb a source, only its mapped sections may move -------------------------

_GLOBBED = [k for k, v in provenance.SOURCES.items() if v["globs"]]


@pytest.fixture(scope="module")
def observed(world):
    """``{perturbation: sections that changed}`` — every perturbation run once."""
    profile, tmp, base = world
    root, _ = input_globs(profile, tmp)
    out: dict[str, set[str]] = {}
    for key in _GLOBBED:
        files = _source_files(profile, tmp, key)
        assert files, f"positive control: the fixture holds no file for source {key!r}"
        out[key] = _changed(base, _without(files, lambda: _render(profile, tmp)))
    roster = [Path(p) for p in page_inputs._resolve(root, ["prospects/roster-*.csv"])]
    assert roster, "positive control: the manifest's roster export"
    out["roster"] = _changed(base, _without(roster, lambda: _render(profile, tmp)))
    dossiers = [Path(p) for p in page_inputs._resolve(root, list(NAME_GLOBS))]
    assert dossiers, "positive control: the fixture's dossier"
    out["dossiers"] = _changed(base, _without(dossiers, lambda: _render(profile, tmp)))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            views_ops,
            "BENCHMARKS",
            tuple({**b, "low": 99.0} if "low" in b else dict(b) for b in views_ops.BENCHMARKS),
        )
        out["benchmarks-values"] = _changed(base, _render(profile, tmp))
        mp.setattr(provenance, "BENCHMARKS_RESEARCHED", "1999-01-01")
        out["benchmarks-date"] = _changed(base, _render(profile, tmp))
    return out


def test_the_fixture_renders_deterministically_and_widely(world):
    profile, tmp, base = world
    again = _render(profile, tmp)
    assert _changed(base, again) == set(), "two renders of one tree differ: the oracle is noise"
    assert len(base) >= 45, "the fixture stopped rendering most of the page"


@pytest.mark.parametrize("key", _GLOBBED)
def test_deleting_a_source_moves_only_the_sections_mapped_to_it(observed, key):
    changed = observed[key]
    assert changed, f"positive control: removing {key!r} changed nothing, the oracle is blind"
    stray = set(changed) - section_sources.sections_for(key)
    assert stray == set(), f"{key!r} moved sections the map does not give it: {sorted(stray)}"


def test_the_roster_export_the_manifest_names_moves_only_pool_mapped_sections(observed):
    assert observed["roster"]
    stray = {s for s in observed["roster"] if "pool" not in section_sources.SECTION_SOURCES[s]}
    assert stray == set(), sorted(stray)


def test_a_dossier_appearing_changes_no_rendered_section(observed):
    """The dossier lookup is a name-only read with no consumer in a section. If one is added
    this fails, and the fix is a source row — not deleting this test."""
    assert observed["dossiers"] == set()


def test_changing_the_benchmarks_moves_only_sections_mapped_to_them(observed):
    assert observed["benchmarks-values"], "positive control: altering the values moved nothing"
    assert observed["benchmarks-date"], "positive control: altering the date moved nothing"
    moved = observed["benchmarks-values"] | observed["benchmarks-date"]
    stray = {s for s in moved if "benchmarks" not in section_sources.SECTION_SOURCES[s]}
    assert stray == set(), sorted(stray)


def test_the_unwitnessed_sections_are_exactly_those_no_perturbation_ever_showed(world, observed):
    """A section that is absent on the fixture AND moves under no perturbation cannot be
    checked here; its mapping is read from the code. If one gets witnessed, shrink the set."""
    _profile, _tmp, base = world
    seen = set(base).union(*observed.values())
    assert section_sources.UNWITNESSED == ALL_IDS - seen


# --- the kind column agrees with the map -----------------------------------------------------


def test_a_section_kind_agrees_with_what_the_section_reads():
    """A typed source is one a person or an agent wrote: the sending snapshot (an agent types its
    date) and the outcomes file ("recorded by hand", as its own source row says). ONE typed
    source is enough to make a section's number as old as someone's last entry, so a section
    whose map holds one is `agent-written` — never `live`. The provenance cards only PRINT
    states and are exempt."""
    typed = set(provenance.TYPED_SOURCES)
    for sid, kind in provenance.SECTION_KIND.items():
        srcs = section_sources.SECTION_SOURCES[sid]
        if kind == "agent-written":
            assert typed & srcs, f"{sid!r} is agent-written but reads no typed source"
        elif kind == "static":
            assert "benchmarks" in srcs or not srcs, f"{sid!r} is static but reads no constant"
        elif sid not in (provenance.GROUP_ID, "sources-table"):
            assert not typed & srcs, f"{sid!r} is live but reads a typed source: {typed & srcs}"


def test_every_typed_source_is_a_source_and_says_it_is_typed():
    assert set(provenance.TYPED_SOURCES) <= set(provenance.SOURCES)
    assert "by hand" in provenance.SOURCES["outcomes"]["label"]


# --- the two entries the mutation pass found unpinned (BM1, BM2) ------------------------------
#
# `holding-up` -> ("outcomes",) and `ready-to-send` -> () both survived: the oracle's direction is
# changed ⊆ mapped, so a map that LOSES a source it should have still passes whenever the
# perturbation moved nothing in that section. Both sections are UNWITNESSED on the fixture (they
# render nothing there), so their sets are read from the code and pinned by CONTENT here.


def test_holding_up_is_mapped_to_what_it_reads_not_to_the_outcomes_file():
    """`_holding_up` judges the checks, the sequence copy and the campaign plans. Mutation caught:
    replacing the entry with ("outcomes",) — a section that is `live` by kind would then claim
    the hand-recorded file as its only source."""
    assert section_sources.SECTION_SOURCES["holding-up"] == {"checks", "copy", "plans"}
    assert "outcomes" not in section_sources.SECTION_SOURCES["holding-up"]


def test_ready_to_send_is_mapped_to_the_ledger_the_pool_and_the_sorted_list():
    """The list of accounts that can go is the ledger joined to the sorted list and the pool.
    Mutation caught: an empty entry, which would read "this section depends on nothing" on the
    page that says what each section depends on."""
    assert section_sources.SECTION_SOURCES["ready-to-send"] == {"ledger", "pool", "sorted-list"}
    assert section_sources.SECTION_SOURCES["ready-to-send"] != frozenset()


def test_a_tenant_whose_only_pool_files_are_roster_globs_still_has_a_pool(tmp_path, monkeypatch):
    """The roster globs a campaign manifest declares are the pool's own files; a tenant that
    keeps its list ONLY there must not read "missing" in the provenance table."""
    from tests.contracts.test_dashboard_reads_are_inventoried import _pin_profiles_root

    _pin_profiles_root(tmp_path, monkeypatch)
    profile = "acme"
    (tmp_path / profile / "plans" / "campaigns").mkdir(parents=True)
    (tmp_path / profile / "plans" / "campaigns" / "q.campaign.toml").write_text(
        'slug = "q"\nroster_globs = ["rosters/*.csv"]\n'
    )
    (tmp_path / profile / "prospects" / "rosters").mkdir(parents=True)
    (tmp_path / profile / "prospects" / "rosters" / "r1.csv").write_text("email\nada@x.example\n")
    states = provenance.source_states({}, profile, tmp_path)
    assert states["pool"]["state"] == "undated"  # there, and it records no date
    (tmp_path / profile / "prospects" / "rosters" / "r1.csv").unlink()
    assert provenance.source_states({}, profile, tmp_path)["pool"]["state"] == "missing"


def test_sections_for_is_the_exact_inverse_of_the_map():
    assert section_sources.sections_for("outcomes") == {
        sid for sid, srcs in section_sources.SECTION_SOURCES.items() if "outcomes" in srcs
    }
    assert "voice-of-market" in section_sources.sections_for("outcomes")
    assert "lede" not in section_sources.sections_for("outcomes")


def test_a_dossier_alone_is_not_a_pool(tmp_path, monkeypatch):
    """The dossier lookup is tracked by name and feeds no section, so it must not be counted as
    a roster: a tenant with only a dossier has no pool."""
    from gtm_core.email_campaign_dashboard.config import NAME_GLOBS
    from tests.contracts.test_dashboard_reads_are_inventoried import _pin_profiles_root

    _pin_profiles_root(tmp_path, monkeypatch)
    profile = "acme"
    glob = NAME_GLOBS[0]
    path = tmp_path / profile / glob.replace("*", "alpha-co", 1).replace("*", "x")
    path.parent.mkdir(parents=True)
    path.write_text("# dossier\n")
    assert provenance.source_states({}, profile, tmp_path)["pool"]["state"] == "missing"
