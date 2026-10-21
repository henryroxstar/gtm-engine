"""I2, loader side — a snapshot row with odd types must never crash a render.

WHY THIS EXISTS (red team round 2, 2026-10-02). The sending-figures writer validates the FILE, not
the page that reads it, and the file on disk can also be hand-written or written by an older
build. An integer ``sequenceId`` raised ``TypeError`` in ``health`` and ``model``, a non-string
``sequenceName`` raised ``AttributeError`` in ``views_accounts``, and a counter of 309+ digits
raised ``OverflowError`` in the loader. Any of them poisoned EVERY later refresh of that profile,
because the rollup render is the first thing a refresh runs.

The contract is at the ONE boundary where snapshot rows enter the page
(``prospects_dashboard._normalize_seq``), so every reader — health, model, figure ages, the views,
the campaigns model — gets the same normalised row:

* ``id`` is a string, the SAME string the writer's ``row_id`` produces (one helper, shared);
* ``name`` and ``status`` are strings (anything else reads as empty, never as markup or a crash);
* every counter is a small non-negative ``int``; one that is not a finite number, is negative, or
  has more than ``MAX_COUNTER_DIGITS`` digits is REFUSED — read as 0 for arithmetic, named in the
  row's ``refused`` list, and shown as an em dash where the page lists it.

The fixtures are written straight to ``sequence-stats.json``, not through the writer: the loader
must tolerate whatever is already on disk, whatever the writer accepts today.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard.model import scope_to_campaign
from gtm_core.prospects_dashboard import (
    MAX_COUNTER_DIGITS,
    _normalize_seq,
    load_sequence_snapshot,
)
from gtm_core.sequence_snapshot_format import row_id
from tests.contracts.test_dashboard_ps20_trust import _stats
from tests.test_email_campaign_dashboard import _seed

TODAY = datetime.now(UTC).date().isoformat()

_BAD_IDS = [7, 10**15, 7.5, True, ["x"], {"a": 1}, "x" * 300, "<b>x</b>", "⟦GATE:publish⟧"]
_BAD_NAMES = [5, 1.5, True, ["a"], {"a": 1}, "<img src=x onerror=alert(1)>", "N" * 5000]
_BAD_COUNTS = [
    10**15,
    10**30,
    10**400,
    "9" * 309,
    "1e400",
    float("inf"),
    float("nan"),
    "x",
    True,
    -1,
    1.5,
    "٣",
    ["l"],
    {"d": 1},
]


def _raw(sid, **over):
    row = {
        "sequenceId": sid,
        "sequenceName": "Name",
        "status": "active",
        "prospects": [{"total": 10, "contacted": 4, "replied": 1, "bounced": 0}],
        "emails": {"status": {"delivered": 4, "replied": 1, "bounced": 0}},
    }
    row.update(over)
    return row


def _shapes() -> list[tuple[str, list]]:
    out: list[tuple[str, list]] = []
    for i, bad in enumerate(_BAD_IDS):
        out.append((f"id-{i}", [_raw(bad)]))
    for i, bad in enumerate(_BAD_NAMES):
        out.append((f"name-{i}", [_raw("S1", sequenceName=bad)]))
    for i, bad in enumerate(_BAD_COUNTS):
        out.append((f"count-{i}", [_raw("S1", prospects=[{"total": bad, "contacted": bad}])]))
    for i, bad in enumerate(_BAD_COUNTS[:6]):
        out.append((f"delivered-{i}", [_raw("S1", emails={"status": {"delivered": bad}})]))
    out.append(("id-none", [{"sequenceId": None, "id": None, "prospects": [{"total": 3}]}]))
    out.append(("id-empty-string", [_raw("")]))
    out.append(("name-none", [_raw("S1", sequenceName=None)]))
    out.append(("name-empty", [_raw("S1", sequenceName="")]))
    out.append(("status-list", [_raw("S1", status=["paused"])]))
    out.append(("status-dict", [_raw("S1", status={"a": 1})]))
    for i, bad in enumerate([[5], [[]], "x", {}, 5]):
        out.append((f"prospects-{i}", [_raw("S1", prospects=bad)]))
    out.append(("mixed-id-types", [_raw(7), _raw("7"), _raw("S1"), _raw(7.0), _raw(True)]))
    out.append(("flat-int-id", [{"id": 7, "name": 5, "sent": 10**30, "loaded": 3}]))
    out.append(("flat-list-id", [{"id": ["S1"], "name": ["n"], "sent": "x"}]))
    return out


SHAPES = _shapes()


def _write(tmp_path, rows):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, json.dumps({"fetched": TODAY, "sequences": rows}))
    return profile


def test_there_are_at_least_forty_eight_hostile_shapes():
    """The brief's number, held: a fixture list that quietly shrank would stop covering a site."""
    assert len(SHAPES) >= 48


@pytest.mark.parametrize("name,rows", SHAPES, ids=[n for n, _ in SHAPES])
def test_every_shape_on_disk_renders_without_raising(tmp_path, name, rows):
    profile = _write(tmp_path, rows)
    m = gd.build_model(profile, tmp_path)
    page = gd.render_html(m)
    assert "<html" in page
    # a campaign-scoped page reads the same rows through a second path
    scoped = scope_to_campaign(gd.build_model(profile, tmp_path), "c1")
    assert "<html" in gd.render_html(scoped)


@pytest.mark.parametrize("name,rows", SHAPES, ids=[n for n, _ in SHAPES])
def test_every_row_the_loader_hands_over_is_normalised(tmp_path, name, rows):
    profile = _write(tmp_path, rows)
    got = load_sequence_snapshot(profile, tmp_path)["rows"]
    assert len(got) == len(rows)
    for r in got:
        assert isinstance(r["id"], str)
        assert isinstance(r["name"], str) and isinstance(r["status"], str)
        for k, v in r.items():
            if k in ("id", "name", "status", "bounce_source", "refused"):
                continue
            assert isinstance(v, int) and not isinstance(v, bool), (k, v)
            assert 0 <= v < 10**MAX_COUNTER_DIGITS, (k, v)
        assert all(isinstance(f, str) for f in r.get("refused", []))


def test_the_row_id_is_the_writers_row_id_not_a_second_definition(tmp_path):
    """One helper. The writer keys its per-sequence dates and falls by ``row_id``; a loader that
    coerced the id some other way would look every stamp up under a different key."""
    for raw in (_raw(7), _raw("S1"), {"id": 7.5, "sent": 1}, _raw(["x"]), _raw(True)):
        assert _normalize_seq(raw)["id"] == row_id(raw)
    assert _normalize_seq(_raw(7))["id"] == "7"
    assert _normalize_seq({"id": 7, "sent": 1})["id"] == "7"


def test_a_row_with_a_stamped_integer_id_keeps_its_own_date(tmp_path):
    """The reason the id must be the writer's string: a format-2 file stamps ``"7"`` and a loader
    that read the id as ``7`` would find no stamp for it and call the figures undated."""
    profile = _seed(tmp_path)
    rows = [_raw(7)]
    payload = {"fetched": TODAY, "sequences": rows}
    _stats(tmp_path, profile, json.dumps(payload))
    snap = load_sequence_snapshot(profile, tmp_path)
    assert snap["rows"][0]["id"] in snap["file_meta"]["ids"]
    assert snap["file_meta"]["stamps"].get(snap["rows"][0]["id"]) == TODAY


def test_a_non_string_name_reads_as_empty_and_the_table_falls_back_to_the_id(tmp_path):
    profile = _write(tmp_path, [_raw("S9", sequenceName=["a"], status={"a": 1})])
    row = load_sequence_snapshot(profile, tmp_path)["rows"][0]
    assert row["name"] == "" and row["status"] == ""
    assert "S9" in gd.render_html(gd.build_model(profile, tmp_path))


# --- a refused figure is shown as a refusal, never as a number ------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        10**15,
        10**30,
        10**400,
        pytest.param(10**5000, id="int-past-the-interpreter-digit-limit"),
        "9" * 309,
        float("inf"),
        float("nan"),
        "1e400",
        "-1",
        True,
    ],
)
def test_an_absurd_or_non_finite_counter_is_refused_not_raised(bad):
    row = _normalize_seq(_raw("S1", prospects=[{"total": 10, "contacted": bad}]))
    assert row["sent"] == 0 and row["refused"] == ["sent"]
    assert row["loaded"] == 10  # the rest of the row is read as before


def test_the_fifteen_digit_boundary_is_exact():
    assert MAX_COUNTER_DIGITS == 15
    ok = _normalize_seq(_raw("S1", prospects=[{"total": 10**15 - 1}]))
    assert ok["loaded"] == 10**15 - 1 and "refused" not in ok
    bad = _normalize_seq(_raw("S1", prospects=[{"total": 10**15}]))
    assert bad["loaded"] == 0 and bad["refused"] == ["loaded"]


@pytest.mark.parametrize("blank", [None, ""])
def test_a_blank_counter_is_absent_not_refused(blank):
    """Unchanged: a figure the sending tool did not send is not an error."""
    row = _normalize_seq(_raw("S1", prospects=[{"total": blank, "contacted": blank}]))
    assert row["loaded"] == 0 and "refused" not in row


def test_the_flat_row_path_refuses_the_same_way():
    row = _normalize_seq({"id": "S1", "sent": 10**30, "loaded": "x", "replied": 2})
    assert row["refused"] == ["loaded", "sent"] and row["replied"] == 2


def test_the_sequence_table_shows_a_refused_figure_as_a_dash_and_says_why(tmp_path):
    profile = _write(
        tmp_path,
        [
            _raw(
                "S1",
                sequenceName="One",
                prospects=[{"total": 10, "contacted": 10**30, "replied": 2}],
            )
        ],
    )
    page = gd.render_html(gd.build_model(profile, tmp_path))
    table = page.split("<h2>Email sequences</h2>", 1)[1].split("</table>", 1)[0]
    rows = {}
    for chunk in table.split("<tr>")[1:]:
        cells = [c.split("</td>")[0] for c in chunk.split("<td")[1:]]
        if cells:
            rows[cells[0].split(">", 1)[1]] = [c.split(">", 1)[1] for c in cells[1:]]
    assert rows["One"][1] == "—", rows  # people contacted
    assert rows["One"][3] == "—", rows  # progress needs both numbers
    assert rows["One"][0] == "10" and rows["One"][2] == "2"  # the readable ones stay
    card = page.split("<h2>Email sequences</h2>", 1)[1].split("</div>", 1)[0]
    assert "not a usable number" in card
    assert 10**30 > 0 and "1,000,000" not in table


def test_a_clean_table_carries_no_refusal_note(tmp_path):
    profile = _write(tmp_path, [_raw("S1")])
    page = gd.render_html(gd.build_model(profile, tmp_path))
    table = page.split("<h2>Email sequences</h2>", 1)[1].split("</table>", 1)[0]
    assert "not a usable number" not in page and "—" not in table.split("<tbody>", 1)[1]


# --- ordering is stable whatever the id types were -------------------------------------------


def test_mixed_id_types_sort_and_reconcile_the_same_way_every_time(tmp_path):
    rows = [_raw(7), _raw("S1"), _raw(3.5), _raw("7"), _raw("A")]
    profile = _write(tmp_path, rows)
    first = gd.build_model(profile, tmp_path)
    second = gd.build_model(profile, tmp_path)
    assert first["reconciliation"] == second["reconciliation"]
    only = first["reconciliation"]["in_snapshot_only"]
    assert only == sorted(only) and all(isinstance(x, str) for x in only)
    assert [r["id"] for r in first["status"]["sequences"]] == ["7", "S1", "3.5", "7", "A"]


# --- the loader tolerates a file it cannot parse -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1" + "0" * 5000,
        "[" * 100000 + "]" * 100000,
        '{"sequences": [{"sequenceId": ' + "9" * 5000 + "}]}",
    ],
    ids=["int-over-the-digit-limit", "nesting-too-deep", "id-over-the-digit-limit"],
)
def test_a_file_the_json_parser_itself_refuses_reads_unreadable_not_a_traceback(tmp_path, text):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, text)
    snap = load_sequence_snapshot(profile, tmp_path)
    assert snap["unreadable"] is True and snap["rows"] == []
    assert "<html" in gd.render_html(gd.build_model(profile, tmp_path))


def test_the_reconciliation_sorts_ids_of_mixed_types_from_the_ledger_side_too():
    """The ledger and the manifests are hand-written, so a numeric id can sit beside a text one
    there as well. The snapshot side is normalised at the loader; this side is sorted by text."""
    from gtm_core.email_campaign_dashboard.model import reconcile_snapshot

    got = reconcile_snapshot(
        {"campaigns": [{"sequences": [{"sequence_id": 7}, {"sequence_id": "S1"}], "archived": []}]},
        {"sequences": [{"id": "A"}]},
    )
    assert got["ok"] is False
    assert got["in_snapshot_only"] == ["A"] and got["in_ledger_only"] == [7, "S1"]
    from gtm_core.email_campaign_dashboard.health import reconciliation_detail

    assert "missing from the live figures: 7, S1" in reconciliation_detail(got)


# --- the red team's generator, run against whole files on disk ----------------------------------

_FUZZ_IDS = ["S1", "S2", "abc-9", "A" * 200, 7, 123456789, 10**15, "7", "<b>x</b>", "é", "a\tb"]
_FUZZ_COUNTS = [None, "", 0, 1, 5, 40, 10**9, 10**20, 10**100, 10**400, "12", True, -1, 1.5, "x"]
_FUZZ_NAMES = ["n", "<img src=x>", "", None, 5, ["a"], {"a": 1}, "N" * 500]
_FUZZ_EXTRA = [None, "x", [], {}, 5, [1, 2], {"a": {"b": [1]}}]


def _fuzz_rows(rng) -> list[dict]:
    """Rows shaped like ``p2_writer_fuzz.py``'s (the generator that found three crash sites in
    nine of 48 accepted files), weighted toward hostile values."""
    rows = []
    for _ in range(rng.randint(1, 4)):
        row = {"sequenceId": rng.choice(_FUZZ_IDS), "sequenceName": rng.choice(_FUZZ_NAMES)}
        count = lambda: rng.choice(_FUZZ_COUNTS)  # noqa: E731 -- a one-line draw, used four times
        shape = rng.choice(["both", "prospects", "status", "none", "p-nolist"])
        if shape in ("both", "prospects"):
            row["prospects"] = [
                {"total": count(), "contacted": count(), "replied": count(), "bounced": count()}
            ]
        if shape in ("both", "status"):
            row["emails"] = {"status": {"delivered": count(), "bounced": count()}}
        if shape == "p-nolist":
            row["prospects"] = rng.choice([{}, "x", 5, [], [5], [[]]])
        for key in rng.sample(["status", "steps", "extra"], rng.randint(0, 2)):
            row[key] = rng.choice(_FUZZ_EXTRA)
        rows.append(row)
    return rows


@pytest.mark.parametrize("seed", [5, 9, 21])
def test_a_seeded_hostile_file_fuzz_renders_every_file(tmp_path, seed):
    import random

    rng = random.Random(seed)
    for trial in range(15):
        root = tmp_path / f"t{trial}"
        root.mkdir()
        rows = _fuzz_rows(rng)
        profile = _write(root, rows)
        got = load_sequence_snapshot(profile, root)["rows"]
        assert len(got) == len(rows), rows
        page = gd.render_html(gd.build_model(profile, root))
        assert "<html" in page, rows
