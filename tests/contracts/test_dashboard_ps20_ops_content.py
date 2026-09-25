"""PS20 Phase 2 — the cards moving to Operator notes, gated on their own data as they move (PRD
out-of-scope Rule-B list) and counting "1" in the singular."""

import copy
import re

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import views_inbound, views_learn, views_ops, views_what
from gtm_core.email_campaign_dashboard.config import TAB_LABELS
from tests.contracts.dashboard_page import visible_text
from tests.contracts.test_dashboard_ps20_trust import _seed_with_roster
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.contracts.test_dashboard_tenant_prose import _full_fixture
from tests.test_email_campaign_dashboard import SPEC_THREADED, _seed

X = "<script>x</script>"
_BAR = re.compile(
    r'blabel">([^<]*)</div><div class="btrack"><div class="bfill (\w+)" '
    r'style="width:(\d+)%"></div></div><div class="bval">([^<]*)<'
)
# The same three emails, but the last one is the threaded reply: no group ends on a subject.
SPEC_THREADED_TAIL = SPEC_THREADED.replace(
    "**Step 2 — Day 4** (same thread, no subject)",
    "**Step 2 — Day 4** · Subject: `the procurement question`",
).replace(
    "**Step 3 — Day 9** · Subject: `the procurement question`",
    "**Step 3 — Day 9** (same thread, no subject)",
)


def _register(tmp_path, profile, sid, spec):
    cells = pc._prospects_dir(profile, tmp_path) / "sequences" / "cells.toml"
    cells.write_text(
        cells.read_text(encoding="utf-8") + f'\n[[sequence]]\nid = "{sid}"\ntitle = "{sid} group"\n'
        f'csv = "list.csv"\nspec = "{spec}"\n',
        encoding="utf-8",
    )


def _subjects(tmp_path, profile, spec, other=None):
    """The subjects block for one group on ``spec``, plus a second group on ``other``."""
    p = _seed(tmp_path, profile)
    seq = pc._prospects_dir(p, tmp_path) / "sequences"
    (seq / "spec-demo-2026-08-18.md").write_text(spec, encoding="utf-8")
    if other is not None:
        (seq / "spec-other.md").write_text(other, encoding="utf-8")
        _register(tmp_path, p, "S2", "spec-other.md")
    return visible_text(views_what._subjects_block(gd.build_model(p, tmp_path)))


def test_the_shared_subject_note_needs_every_group_to_end_on_it(tmp_path):
    other = SPEC_THREADED.replace("your first security review", "a second opener")
    arc = _subjects(tmp_path, "arc", SPEC_THREADED, other)
    assert "the procurement question is the subject of the final email in every group" in arc
    one = _seed(tmp_path, "one")  # one-touch specs sharing a subject: there is no "final" email
    _register(tmp_path, one, "S2", "spec-demo-2026-08-18.md")
    assert "final email" not in visible_text(
        views_what._subjects_block(gd.build_model(one, tmp_path))
    )
    # One group is not "every group", however many subjects it carries.
    assert "final email" not in _subjects(tmp_path, "solo", SPEC_THREADED)
    # Two groups that end on different subjects share no last subject.
    diff = SPEC_THREADED.replace("the procurement question", "a different close")
    assert "final email" not in _subjects(tmp_path, "diff", SPEC_THREADED, diff)
    # Groups whose final email is a threaded reply have no final subject to name.
    tail = SPEC_THREADED_TAIL.replace("your first security review", "a second opener")
    assert "final email" not in _subjects(tmp_path, "tail", SPEC_THREADED_TAIL, tail)


def test_the_subject_counts_agree_in_number(tmp_path):
    solo = _subjects(tmp_path, "solo", SPEC_THREADED)
    assert "The other 1 arrives as a reply inside the first email's own thread, so it carries" in (
        solo
    )
    arc = _subjects(tmp_path, "arc", SPEC_THREADED, SPEC_THREADED)
    assert "The other 2 arrive as replies inside the first email's own thread, so they carry" in arc
    assert "4 of the emails carry a subject of their own, of which 2 reuse a subject" in arc
    diff = SPEC_THREADED.replace("the procurement question", "a different close")
    assert "of which 1 reuses a subject another group" in _subjects(
        tmp_path, "diff", SPEC_THREADED, diff
    )


def test_counts_of_one_are_singular(tmp_path):
    text = visible_text(views_what._subjects_block(gd.build_model(_seed(tmp_path), tmp_path)))
    assert "1 different subject line across" in text
    assert "1 of the emails carries a subject of its own" in text
    tally = {"rows": 2, "dest:prospect:re-target": 1, "dest:spec:re-argue": 1, "revised": 1}
    notes = visible_text(
        views_what._judge_notes({"roster": {"judge_tally": tally, "judge_source": "q.jsonl"}})
    )
    assert (
        "1 of the 2 is a targeting defect" in notes and "1 of the 2 carries copy revised" in notes
    )
    assert "The judge scored 2 rows:" in notes and "; 1 routes to a rewrite" in notes
    one = {"rows": 1, "dest:spec:re-argue": 1}
    notes = visible_text(
        views_what._judge_notes({"roster": {"judge_tally": one, "judge_source": "q.jsonl"}})
    )
    assert "The judge scored 1 row:" in notes
    many = {"rows": 3, "dest:prospect:re-target": 1, "dest:spec:re-argue": 2, "revised": 2}
    notes = visible_text(
        views_what._judge_notes({"roster": {"judge_tally": many, "judge_source": "q.jsonl"}})
    )
    assert "; 2 route to a rewrite" in notes and "2 of the 3 carry copy revised" in notes
    assert views_what._judge_notes({"roster": {}}) == ""  # no rows: no card, not an empty one


def test_the_scoped_lift_card_counts_one_lane_in_the_singular(tmp_path, monkeypatch):
    """It read "across 1 lanes" on a one-lane campaign too small to power any comparison."""
    monkeypatch.setattr(views_learn, "detectable_lift", lambda *_a: None)
    m = gd.scope_to_campaign(gd.build_model(_seed(tmp_path), tmp_path), "c1")
    text = visible_text(views_learn._lift_block(m))
    assert "across 1 lane this campaign cannot" in text
    people = sum(ln["people"] for ln in views_learn._lanes(m))
    noun = "recipient" if people == 1 else "recipients"
    assert f"At {people} scheduled {noun} across" in text
    monkeypatch.setattr(views_learn, "_lanes", lambda _m: [{"people": 1}])
    assert "At 1 scheduled recipient across" in visible_text(views_learn._lift_block(m))


def test_the_capability_summary_is_shown_plainly(tmp_path):
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    card = views_inbound._capability_card(m)
    tech = re.compile(r"""class=["'][^"']*\btech\b""")
    assert "Compliance preflight" in card and not tech.search(card)
    assert m["inbound"]["capability_rows"] and not tech.search(views_inbound._inbound_card(m))
    # The contract's own lines sit inside the card, not only its heading.
    text = visible_text(card)
    assert "saleshandy capability contract last asserted" in text
    assert "saleshandy/stop_on_reply" in text
    assert "No DNC sync has ever run" in visible_text(views_inbound._inbound_card(m))
    recon = {"dnc": {"event": "dnc_reconciled", "ts": "2026-09-20T00:00:00Z", "findings": []}}
    card = views_inbound._inbound_card({"inbound": recon})
    assert "DNC mirror reconciled 2026-09-20" in visible_text(card) and not tech.search(card)


def _bench(tmp_path, monkeypatch, fig):
    monkeypatch.setattr(views_ops, "_scope_figures", lambda _m: fig)
    monkeypatch.setattr(views_ops, "sending_tiles", lambda _m, _f: {"contacted": 200, "replied": 5})
    card = views_ops._benchmarks_block(gd.build_model(_seed(tmp_path), tmp_path))
    return card, _BAR.findall(card)


def test_the_benchmarks_card_reads_its_own_modules_benchmarks(tmp_path, monkeypatch):
    moved = tuple(dict(b, label=f"moved {b['label']}") for b in views_ops.BENCHMARKS)
    monkeypatch.setattr(views_ops, "BENCHMARKS", moved)
    card = views_ops._benchmarks_block(gd.build_model(_seed(tmp_path), tmp_path))
    assert "<h2>Reply rate</h2>" in card and "moved all industries, average" in card
    assert all(label.startswith("moved ") for label, *_ in _BAR.findall(card)[1:])
    assert "a three-email sequence" in card
    # A renamed figure refuses the gap's two ends rather than crashing the page.
    assert "a figure it compares is no longer in the table below" in card


def test_the_benchmark_bars_draw_the_scopes_rate_and_its_targets(tmp_path, monkeypatch):
    one = {"target_rate": (0.03, "set"), "rates_differ": False, "rates": []}
    card, bars = _bench(tmp_path, monkeypatch, one)
    text = visible_text(card)
    assert "Our 3.0% target is drawn beside every published figure" in text
    strict = next(b["low"] for b in views_ops.BENCHMARKS if b["basis"] == "per email sent")
    usual = next(b["high"] for b in views_ops.BENCHMARKS if b["label"] == "all industries, average")
    assert f"between {strict:.2%} and {usual:.1%} — methodology" in text
    label, tone, width, val = bars[0]
    assert label.endswith(", so far") and tone == "ta"
    assert val == f"{5 / 200:.1%}" and width == f"{5 / 200 * 100 / 0.11:.0f}"
    assert ("our target", "tb", f"{3 / 0.11:.0f}", "3.0%") in bars
    rates = [("c1", 0.02, 1), ("c2", 0.04, 1)]
    _card, bars = _bench(
        tmp_path,
        monkeypatch,
        {"target_rate": (0.03, "blend"), "rates_differ": True, "rates": rates},
    )
    labels = [b[0] for b in bars]
    assert "target · c1" in labels and "target · c2" in labels and "our target" not in labels


def test_finding_new_people_is_scoped_out_of_a_campaign_page_once(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    assert "<h2>Finding new people</h2>" in views_ops._runs_block(m)
    m["campaign_scope"] = "c1"
    scoped = views_ops._runs_block(m)
    assert scoped.count("Finding new people") == 1 and "Not shown on a scoped page" in scoped


def test_the_runs_notice_points_at_account_notes_only_where_they_render(tmp_path):
    m = gd.scope_to_campaign(gd.build_model(_full_fixture(tmp_path), tmp_path), FULL)
    runs = visible_text(views_ops._runs_block(m))
    assert "2 accounts came from is named in the account notes above (1 run export its" in runs
    assert views_ops._roster_notes(m)
    one = copy.deepcopy(m)
    one["roster"]["accounts"] = 1
    assert "1 account came from" in visible_text(views_ops._runs_block(one))
    none = copy.deepcopy(m)
    none["roster"]["sources"] = []
    assert "declares no run export" in visible_text(views_ops._runs_block(none))
    # A roster only some in-scope campaigns declare renders no account notes, so no pointer.
    gap = copy.deepcopy(m)
    gap["campaigns"]["campaigns"].append({"slug": "silent"})
    assert views_ops._roster_notes(gap) == ""
    text = visible_text(views_ops._runs_block(gap))
    assert "account notes" not in text and text.endswith("fed this roster.")
    empty = copy.deepcopy(m)
    empty["roster"]["rows"] = []
    assert views_ops._roster_notes(empty) == ""
    assert "account notes" not in visible_text(views_ops._runs_block(empty))


def test_the_roster_notes_carry_the_judge_split_and_the_seat_fit(tmp_path):
    rollup = gd.build_model(_full_fixture(tmp_path), tmp_path)
    m = gd.scope_to_campaign(copy.deepcopy(rollup), FULL)
    notes = visible_text(views_ops._roster_notes(m))
    assert "Where the 2 judged rows go" in notes
    assert "do not hold the seat their own spec declares" in notes
    assert "Filed in retarget-queue-20260819.jsonl" in notes
    assert f"a technical column on {TAB_LABELS['accounts']}" in notes
    assert "Two different verdicts" not in notes
    # "Not shown here" is true on a campaign page only: the rollup shows the pool.
    assert "deliberately not shown here" in notes
    assert "deliberately not shown here" not in visible_text(views_ops._roster_notes(rollup))


def test_operator_notes_escape_every_recorded_value(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    touch = m["messages"][0]["copy"][0]
    msg = dict(m["messages"][0], copy=[dict(touch, subject="a"), dict(touch, subject=X)])
    m["messages"] = [msg, dict(msg, title="other")]
    subjects = views_what._subjects_block(m)
    assert "final email" in visible_text(subjects)
    inbound = {
        "dnc": {"event": X, "ts": X, "reason": X},
        "capability": {"provider": X, "ts": X, "sequence_id": X, "status": X},
    }
    tally = {
        "rows": 1,
        f"verdict:{X}": 1,
        f"dest:{X}": 1,
        f"class:prospect:re-target|{X}": 1,
        "dest:prospect:re-target": 1,
        "backend": X,
    }
    rm = gd.scope_to_campaign(gd.build_model(_full_fixture(tmp_path / "f"), tmp_path / "f"), FULL)
    rm["roster"]["tiers"] = [(X, 1)]
    rm["roster"]["verdicts"] = [(X, 1)]
    blocks = [
        subjects,
        views_inbound._inbound_card({"inbound": inbound}),
        views_inbound._capability_card({"inbound": inbound}),
        views_what._judge_notes({"roster": {"judge_tally": tally, "judge_source": X}}),
        views_ops._roster_notes(rm),
    ]
    for html in blocks:
        assert "<script>" not in html and "&lt;script&gt;" in html
