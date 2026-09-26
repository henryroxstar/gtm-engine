"""`lanes route` writes state a person's status block is built from — so what it writes must
be mappable, complete, and never quietly destructive.

Covers the 2026-09-21 audit's router-side findings: PSK-028 (every unattended trigger the
router can write is known to the status model), PSK-033 (routing a subset never blanks the
rows it did not route), PSK-034 (a first run routes with no judge records at all), PSK-035
(blank emails never inherit a verdict; the state write is atomic; routing never deletes an archived list).
Fictional fixtures only (§R9).
"""

from __future__ import annotations

import csv
import datetime
import json
import re
from pathlib import Path

import pytest

from gtm_core import lanes, prospect_status
from gtm_core import prospect_status_cli as status_cli
from gtm_core.account_integrity import CompetitorHit
from gtm_core.adjudication import Adjudication
from gtm_core.lanes import decisions as dec
from gtm_core.lanes import model
from gtm_core.lanes.context import RouterContext
from gtm_core.prospects_consolidate.confidence import org_token

AS_OF = datetime.date(2026, 9, 3)
PROFILE = "qa-sandbox"


def _row(**kw) -> dict:
    base = {
        "first": "Rowan",
        "last": "Pike",
        "email": "rowan.pike@northwindrobotics.example",
        "title": "Chief Information Security Officer",
        "company": "Northwind Robotics",
        "company_domain": "northwindrobotics.example",
        "segment": "enterprise",
        "tier": "B",
        "score": "80",
        "why_now": "Northwind Robotics opened an AI governance program covering autonomous agents",
        "signal_observed": "2026-08-20",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
        "account_id": "",
        "suppression": "",
    }
    base.update(kw)
    return base


def _ctx() -> RouterContext:
    return RouterContext(profile=PROFILE, as_of=AS_OF)


def _pool(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def _env(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    seq = tmp_path / "content" / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    return seq


def _route(*extra: str, csv_path: Path) -> int:
    return lanes.main(
        ["route", "--profile", PROFILE, "--csv", str(csv_path), "--as-of", "2026-09-03", *extra]
    )


# --------------------------------------------------------------------- PSK-028


def test_every_unattended_trigger_is_a_hold_trigger_with_a_question_and_a_status():
    assert set(model.UNATTENDED_TRIGGERS) == {"generic", "repair"}
    for trigger in model.UNATTENDED_TRIGGERS.values():
        assert trigger in model.HOLD_ORDER
        question = model.HOLD_QUESTION[trigger]
        assert model.QUESTION_COPY[question][1].keys() == {"suppress", "generic", "salvage"}
        assert prospect_status.status_of("hold", trigger) == "waiting_on_you"


def test_the_router_composes_no_unattended_trigger_of_its_own():
    """The shared constant is the ONLY spelling: a second `f"unattended-…"` in the router is
    how `unattended-repair` reached a state file the status model had never heard of."""
    src = (Path(lanes.__file__).parent / "router.py").read_text(encoding="utf-8")
    assert not re.search(r"""f["']unattended-""", src)
    assert not re.search(r"""["']unattended-[a-z]""", src)


def test_an_unattended_repair_candidate_is_held_under_the_shared_trigger():
    rec = Adjudication(
        email="rowan.pike@northwindrobotics.example",
        verdict="re-angle",
        score=2,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
    )
    assert lanes.route_row(_row(), _ctx(), [rec]).lane == "repair"
    held = lanes.route_row(_row(), _ctx(), [rec], unattended=True)
    assert (held.lane, held.trigger) == ("hold", model.UNATTENDED_TRIGGERS["repair"])
    assert prospect_status.status_of(held.lane, held.stable_reason) == "waiting_on_you"


# --------------------------------------------------------------------- PSK-034


def test_route_without_records_writes_state_and_status_prints_a_table(
    tmp_path, monkeypatch, capsys
):
    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    assert _route(csv_path=pool) == 0
    out = capsys.readouterr().out
    assert "no judge records" in out.lower()
    state = dec.read_state(dec.state_path(PROFILE))
    assert state["rowan.pike@northwindrobotics.example"]["lane"] == "generic"

    assert status_cli.main(["--profile", PROFILE]) == 0
    block = capsys.readouterr().out
    assert "Nothing to show yet" not in block
    assert re.search(r"Sorted — not yet checked\s+1\b", block)


def test_route_with_a_missing_records_path_exits_2_with_one_line(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    assert _route("--records", str(tmp_path / "judge" / "absent.jsonl"), csv_path=pool) == 2
    err = capsys.readouterr().err
    assert "absent.jsonl" in err and "Traceback" not in err
    assert len(err.strip().splitlines()) == 1
    assert not dec.state_path(PROFILE).exists()


# --------------------------------------------------------------------- PSK-033


def _three_account_pool(seq: Path) -> tuple[Path, list[dict]]:
    rows = [
        _row(),
        _row(
            email="ira.bloom@contosofreight.example",
            first="Ira",
            last="Bloom",
            company="Contoso Freight",
            company_domain="contosofreight.example",
        ),
        _row(
            email="oma.reyes@litwarepay.example",
            first="Oma",
            last="Reyes",
            company="Litware Pay",
            company_domain="litwarepay.example",
        ),
    ]
    return _pool(seq / "ready-to-load.csv", rows), rows


def _mark_contoso_a_direct_competitor(monkeypatch) -> None:
    from gtm_core.lanes import cli as lanes_cli

    real = lanes_cli.load_context

    def fake(profile, *, as_of):
        ctx = real(profile, as_of=as_of)
        ctx.competitors = {
            org_token("contosofreight.example", "Contoso Freight"): CompetitorHit(
                "direct", "direct competitor (fixture)"
            )
        }
        return ctx

    monkeypatch.setattr(lanes_cli, "load_context", fake)


def test_routing_a_subset_keeps_every_other_rows_record(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool, rows = _three_account_pool(seq)
    _mark_contoso_a_direct_competitor(monkeypatch)
    assert _route(csv_path=pool) == 0
    before = dec.read_state(dec.state_path(PROFILE))
    competitor = before["ira.bloom@contosofreight.example"]
    assert (competitor["lane"], competitor["trigger"]) == ("excluded", "competitor-direct")
    capsys.readouterr()

    subset = _pool(tmp_path / "subset.csv", [rows[0]])
    assert _route("--stamp", "2026-09-04", csv_path=subset) == 0
    out = capsys.readouterr().out
    assert "carried forward 2 record(s) not in this CSV" in out

    after = dec.read_state(dec.state_path(PROFILE))
    assert after["ira.bloom@contosofreight.example"] == competitor
    assert after["oma.reyes@litwarepay.example"] == before["oma.reyes@litwarepay.example"]
    assert after["rowan.pike@northwindrobotics.example"]["stamp"] == "2026-09-04"
    # …and the one file a person loads from still says so.
    with pool.open(newline="", encoding="utf-8") as fh:
        lane_by_email = {r["email"]: r["lane"] for r in csv.DictReader(fh)}
    assert lane_by_email["ira.bloom@contosofreight.example"] == "excluded"
    assert lane_by_email["oma.reyes@litwarepay.example"] == "generic"


def test_replace_all_is_the_explicit_way_to_drop_unrouted_records(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool, rows = _three_account_pool(seq)
    assert _route(csv_path=pool) == 0
    subset = _pool(tmp_path / "subset.csv", [rows[0]])
    assert _route("--replace-all", csv_path=subset) == 0
    assert set(dec.read_state(dec.state_path(PROFILE))) == {"rowan.pike@northwindrobotics.example"}


def test_a_record_for_someone_no_longer_in_the_list_is_not_carried_forward(
    tmp_path, monkeypatch, capsys
):
    """Carry-forward protects rows this CSV did not cover — not rows the list has since lost. A
    contact removed from the pool must not keep counting as "Sorted — not yet checked"."""
    seq = _env(tmp_path, monkeypatch)
    pool, rows = _three_account_pool(seq)
    assert _route(csv_path=pool) == 0
    _pool(seq / "ready-to-load.csv", rows[:2])  # the list lost its third person
    capsys.readouterr()
    assert _route(csv_path=_pool(tmp_path / "subset.csv", [rows[0]])) == 0
    out = capsys.readouterr().out
    assert "carried forward 1 record(s) not in this CSV" in out
    assert "oma.reyes@litwarepay.example" not in dec.read_state(dec.state_path(PROFILE))


def test_a_malformed_state_file_stops_the_route_with_one_line(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    state = dec.state_path(PROFILE)
    state.parent.mkdir(parents=True)
    state.write_text('{"email": "x@litwarepay.example", "lane": "gen\n', encoding="utf-8")
    assert _route(csv_path=pool) == 2
    err = capsys.readouterr().err
    assert "lanes-state.jsonl" in err and "Traceback" not in err


# --------------------------------------------------------------------- PSK-035


def test_a_blank_email_row_never_inherits_a_judge_verdict():
    blank_rec = Adjudication(
        email="", verdict="send", score=4, repair_attempt=0, body_hash="h1", touch=1
    )
    result = lanes.route([_row(email="")], [blank_rec], _ctx())
    (routed,) = result.routed
    assert routed.judge_verdict == ""
    assert routed.lane != "personalised"
    assert result.judged == 0


def test_write_state_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "evals" / "lanes-state.jsonl"
    result = lanes.route([_row()], [], _ctx())
    dec.write_state(result, path, "2026-09-03")
    good = path.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dumps", boom)
    with pytest.raises(OSError, match="disk full"):
        dec.write_state(result, path, "2026-09-04")
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == good  # the old state survived a failed write
    assert [p.name for p in path.parent.iterdir()] == ["lanes-state.jsonl"]


def test_state_records_carry_the_account_identity_the_status_block_joins_on(tmp_path):
    path = tmp_path / "lanes-state.jsonl"
    result = lanes.route([_row(account_id="acct-7")], [], _ctx())
    dec.write_state(result, path, "2026-09-03")
    (rec,) = dec.read_state(path).values()
    assert rec["company"] == "Northwind Robotics"
    assert rec["company_domain"] == "northwindrobotics.example"
    assert rec["account_id"] == "acct-7"


def test_routing_never_deletes_an_archived_list(tmp_path):
    """Product-owner decision 2026-09-21: nothing is purged automatically, ever. The archive of
    superseded lists grows until the operator runs the retention command themselves."""
    result = lanes.route([_row()], [], _ctx())
    for day in range(1, 7):
        lanes.write_lanes(result, tmp_path, f"2026-09-{day:02d}")
    superseded = tmp_path / ".pool" / ".superseded"
    kept = sorted(p.name for p in superseded.glob("ready-to-load-generic-*.csv"))
    assert kept == [f"ready-to-load-generic-2026-09-{d:02d}.csv" for d in range(1, 6)]


# --------------------------------------------------------------------- review M4 / H2 (2026-09-21)


def test_an_empty_list_is_not_the_same_as_no_list(tmp_path, monkeypatch):
    """`emails - {""} or None` turned a header-only list into "no list to ask", so every old
    record was carried forward and people removed from the list kept counting as Ready."""
    from gtm_core.lanes import cli as lanes_cli

    seq = _env(tmp_path, monkeypatch)
    pool = seq / "ready-to-load.csv"
    assert lanes_cli._pool_emails(PROFILE) is None  # no file at all
    _pool(pool, [_row()])
    assert lanes_cli._pool_emails(PROFILE) == {"rowan.pike@northwindrobotics.example"}
    pool.write_text(",".join(_row()) + "\n", encoding="utf-8")  # header only
    assert lanes_cli._pool_emails(PROFILE) == set()
    _pool(pool, [_row(email="")])  # rows, but nobody addressable
    assert lanes_cli._pool_emails(PROFILE) == set()


@pytest.mark.parametrize(
    "content",
    [b"", b"first,last,company\nRowan,Pike,Northwind Robotics\n", b"email\n\xff\xfe@x.example\n"],
    ids=["zero-bytes", "no-email-column", "not-utf8"],
)
def test_a_list_that_cannot_be_read_as_addresses_is_no_list(tmp_path, monkeypatch, content):
    """It cannot say who left, so it must not be allowed to say "everyone did"."""
    from gtm_core.lanes import cli as lanes_cli

    seq = _env(tmp_path, monkeypatch)
    (seq / "ready-to-load.csv").write_bytes(content)
    assert lanes_cli._pool_emails(PROFILE) is None


def test_an_emptied_list_carries_nobody_forward(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool, rows = _three_account_pool(seq)
    assert _route(csv_path=pool) == 0
    pool.write_text(",".join(rows[0]) + "\n", encoding="utf-8")  # everyone was removed
    capsys.readouterr()
    assert _route(csv_path=_pool(tmp_path / "subset.csv", [rows[0]])) == 0
    assert "carried forward 0 record(s) not in this CSV" in capsys.readouterr().out
    assert set(dec.read_state(dec.state_path(PROFILE))) == {rows[0]["email"]}


def test_a_missing_list_still_carries_everyone_forward(tmp_path, monkeypatch, capsys):
    """Positive control for the test above: no list is not an empty list."""
    seq = _env(tmp_path, monkeypatch)
    pool, rows = _three_account_pool(seq)
    assert _route(csv_path=pool) == 0
    subset = _pool(tmp_path / "subset.csv", [rows[0]])
    pool.unlink()
    capsys.readouterr()
    assert _route(csv_path=subset) == 0
    assert "carried forward 2 record(s) not in this CSV" in capsys.readouterr().out
    assert len(dec.read_state(dec.state_path(PROFILE))) == 3


def _lock_is_held() -> bool:
    """Whether the state lock is held right now — asked the only way another process could:
    by trying to take it."""
    import fcntl

    dec.state_lock_path(PROFILE).parent.mkdir(parents=True, exist_ok=True)
    with dec.state_lock_path(PROFILE).open("w") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False


def test_route_holds_the_state_lock_from_the_read_to_the_restamp(tmp_path, monkeypatch):
    import importlib

    from gtm_core.lanes import cli as lanes_cli

    consolidate_mod = importlib.import_module("gtm_core.prospects_consolidate.consolidate")

    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    seen: dict[str, bool] = {}

    def spy(name, real):
        def wrapped(*a, **kw):
            seen[name] = _lock_is_held()
            return real(*a, **kw)

        return wrapped

    monkeypatch.setattr(dec, "read_state", spy("read", dec.read_state))
    monkeypatch.setattr(dec, "write_state", spy("write", dec.write_state))
    monkeypatch.setattr(
        consolidate_mod,
        "restamp_ready_to_load",
        spy("restamp", consolidate_mod.restamp_ready_to_load),
    )
    assert _lock_is_held() is False  # the probe can say no (§R18)
    assert lanes_cli.main(["route", "--profile", PROFILE, "--csv", str(pool)]) == 0
    assert seen == {"read": True, "write": True, "restamp": True}
    assert _lock_is_held() is False  # released on the way out


def test_a_refused_route_releases_the_lock_and_a_dry_run_leaves_no_state(tmp_path, monkeypatch):
    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    assert _route("--records", str(tmp_path / "absent.jsonl"), csv_path=pool) == 2
    assert _route("--dry-run", csv_path=pool) == 0
    assert not dec.state_path(PROFILE).exists()
    assert _lock_is_held() is False


def test_the_state_lock_is_not_the_run_level_profile_lock(tmp_path, monkeypatch):
    """A pack run holds `content/<profile>/.lock` for its whole duration and the prospect skill
    runs `lanes route` INSIDE it, from a child process. Taking that same lock here would hang
    (or refuse) every unattended run, so this one is scoped to the state file."""
    from gtm_core.locks import profile_lock
    from gtm_core.paths import resolve_content_root

    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    with profile_lock(resolve_content_root(), PROFILE, blocking=False):
        assert _route(csv_path=pool) == 0
    assert dec.state_lock_path(PROFILE).parent == dec.state_path(PROFILE).parent


def test_a_state_file_that_is_not_text_stops_the_route_with_one_line(tmp_path, monkeypatch, capsys):
    seq = _env(tmp_path, monkeypatch)
    pool = _pool(seq / "ready-to-load.csv", [_row()])
    state = dec.state_path(PROFILE)
    state.parent.mkdir(parents=True)
    state.write_bytes(b'{"email": "x@litwarepay.example", "lane": "generic"}\n\xff\xfe\n')
    before = state.read_bytes()
    assert _route(csv_path=pool) == 2
    err = capsys.readouterr().err
    assert err.startswith("REFUSED: lanes-state.jsonl is not UTF-8 text")
    assert "Traceback" not in err and state.read_bytes() == before


# --------------------------------------------------------------------- W1a: Judge advisory until calibrated (R1.1, R1.2)


def test_r1_1_uncalibrated_judge_re_angle_does_not_route_to_repair(monkeypatch):
    """R1.1: with is_calibrated() == False, a row whose record stamp says calibrated: true
    and whose verdict is re-angle does not route to repair, and its lane equals the lane
    with no judge record (the oracle is an independent route of the same row without the
    judge record). The verdict and defect class remain on the routed row."""
    from gtm_core import eval_calibration

    monkeypatch.setattr(eval_calibration, "is_calibrated", lambda profile: False)

    row = _row(verdict="send")
    rec = Adjudication(
        email=row["email"],
        verdict="re-angle",
        score=2,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        calibrated=True,
        defect_class="fact-creates-problem",
    )
    ctx = _ctx()

    oracle_result = lanes.route([row], [], ctx)
    oracle_lane = oracle_result.routed[0].lane
    assert oracle_lane == "generic"

    result = lanes.route([row], [rec], ctx)
    routed = result.routed[0]
    assert routed.lane != "repair"
    assert routed.lane == "generic"
    assert routed.lane == oracle_lane
    assert routed.judge_verdict == "re-angle"
    assert routed.judge_defect_class == "fact_earns_its_place"


def test_r1_1_uncalibrated_judge_drop_does_not_route_to_repair(monkeypatch):
    """R1.1: with is_calibrated() == False, an argument-scope drop does not route to repair,
    and takes the lane its research verdict earns. Verdict and defect class stay attached."""
    from gtm_core import eval_calibration

    monkeypatch.setattr(eval_calibration, "is_calibrated", lambda profile: False)

    row = _row(verdict="send")
    rec = Adjudication(
        email=row["email"],
        verdict="drop",
        score=1,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        calibrated=True,
        defect_class="fact-creates-no-problem",
    )
    ctx = _ctx()

    oracle_result = lanes.route([row], [], ctx)
    oracle_lane = oracle_result.routed[0].lane
    assert oracle_lane == "generic"

    result = lanes.route([row], [rec], ctx)
    routed = result.routed[0]
    assert routed.lane != "repair"
    assert routed.lane == "generic"
    assert routed.lane == oracle_lane
    assert routed.judge_verdict == "drop"
    assert routed.judge_defect_class == "fact_earns_its_place"


def test_r1_1_calibrated_judge_routes_to_repair_as_today(monkeypatch):
    """R1.1: with is_calibrated() == True, re-angle and argument-scope drop route to repair."""
    from gtm_core import eval_calibration

    monkeypatch.setattr(eval_calibration, "is_calibrated", lambda profile: True)

    row = _row(verdict="send")
    rec_reangle = Adjudication(
        email=row["email"],
        verdict="re-angle",
        score=2,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        calibrated=False,
        defect_class="fact-creates-problem",
    )
    ctx = _ctx()

    result = lanes.route([row], [rec_reangle], ctx)
    routed = result.routed[0]
    assert routed.lane == "repair"
    assert routed.judge_verdict == "re-angle"
    assert routed.judge_defect_class == "fact_earns_its_place"

    rec_drop = Adjudication(
        email=row["email"],
        verdict="drop",
        score=1,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        calibrated=False,
        defect_class="fact-creates-no-problem",
    )
    result_drop = lanes.route([row], [rec_drop], ctx)
    routed_drop = result_drop.routed[0]
    assert routed_drop.lane == "repair"
    assert routed_drop.judge_verdict == "drop"
    assert routed_drop.judge_defect_class == "fact_earns_its_place"


def test_r1_1_is_calibrated_is_called_once_per_route(monkeypatch):
    """R1.1: is_calibrated is evaluated ONCE per route (spy count = 1)."""
    from gtm_core import eval_calibration

    calls = []

    def spy(profile):
        calls.append(profile)
        return False

    monkeypatch.setattr(eval_calibration, "is_calibrated", spy)

    rows = [
        _row(
            email=f"user{i}@northwindrobotics.example",
            company=f"Northwind {i}",
            company_domain="northwindrobotics.example",
        )
        for i in range(5)
    ]
    recs = [
        Adjudication(
            email=r["email"],
            verdict="re-angle",
            score=2,
            repair_attempt=0,
            body_hash="h1",
            touch=1,
        )
        for r in rows
    ]
    ctx = _ctx()

    result = lanes.route(rows, recs, ctx)
    assert len(result.routed) == 5
    assert len(calls) == 1
    assert calls == [PROFILE]


@pytest.mark.parametrize(
    ("env_value", "expected_lane"),
    [
        ("0", "repair"),
        (None, "generic"),
        ("1", "generic"),
        ("wharrgarbl", "generic"),
    ],
)
def test_r1_2_kill_switch_governs_calibration_requirement(monkeypatch, env_value, expected_lane):
    """R1.2: GTM_JUDGE_REPAIR_REQUIRES_CALIBRATION, default on.
    Only the literal "0" turns it off (restoring repair routing regardless of calibration).
    Unset, "1", or any other value means calibration check applies."""
    from gtm_core import eval_calibration

    monkeypatch.setattr(eval_calibration, "is_calibrated", lambda profile: False)

    if env_value is None:
        monkeypatch.delenv("GTM_JUDGE_REPAIR_REQUIRES_CALIBRATION", raising=False)
    else:
        monkeypatch.setenv("GTM_JUDGE_REPAIR_REQUIRES_CALIBRATION", env_value)

    row = _row(verdict="send")
    rec = Adjudication(
        email=row["email"],
        verdict="re-angle",
        score=2,
        repair_attempt=0,
        body_hash="h1",
        touch=1,
        defect_class="fact-creates-problem",
    )
    ctx = _ctx()

    result = lanes.route([row], [rec], ctx)
    assert result.routed[0].lane == expected_lane
