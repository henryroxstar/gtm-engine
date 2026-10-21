"""F4 — the dispatch-intent ledger (VPS file store). Test plan F4-1, F4-2, F4-4, F4-7, F4-8, F4-9, F4-10.

The Postgres twin runs the SAME scenarios in tests/backend/test_dispatch_intents_db.py (live dbtest tier).
"""

from __future__ import annotations

import ast
import asyncio
import json
import multiprocessing
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.ledgers import Ledgers
from gtm_core import dispatch_intents as di
from tests.unit.dispatch_intent_scenarios import SCENARIOS, SEQ, keys

REPO = Path(__file__).resolve().parents[2]


def _store(root: Path, profile: str = "example") -> di.FileIntentStore:
    return di.FileIntentStore(Ledgers(SimpleNamespace(content_root=root), profile))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_file_store_scenarios(tmp_path, scenario):
    asyncio.run(scenario(_store(tmp_path)))


# ── keys ─────────────────────────────────────────────────────────────────────


def test_recipient_key_normalisation():
    assert di.recipient_key("  Pat@Acme.Example ") == "pat@acme.example"
    assert di.recipient_key(lead_id=7) == "id:7"
    assert di.recipient_key("a@b.example", lead_id=7) == "a@b.example"  # email wins when both exist
    with pytest.raises(ValueError):
        di.recipient_key(None, None)
    with pytest.raises(ValueError):
        di.recipient_key("   ", None)


# ── the decision function is pure and shared ─────────────────────────────────


def test_decide_is_pure_and_names_the_overlap():
    d = di.decide(
        {"a@x.example": "dispatched", "b@x.example": "released"}, ["a@x.example", "b@x.example"]
    )
    assert not d.ok and d.overlap == ("a@x.example",) and d.code == "already_dispatched"
    assert di.decide({"a@x.example": "unknown"}, ["a@x.example"]).code == "outcome_unknown"
    assert di.decide({}, ["new@x.example"]).ok
    assert di.decide({"a@x.example": "released"}, ["a@x.example"]).ok


def test_refusal_text_says_the_next_step_in_plain_words():
    msg = di.refusal_message("outcome_unknown", SEQ, ("a@x.example",) * 1, count=351, label=None)
    assert msg.startswith(
        "Not sent: I can't tell whether this batch of 351 emails already went out."
    )
    assert "Open the sequence and check, then tell me:" in msg
    pub = di.refusal_message(
        "outcome_unknown",
        "publish:linkedin",
        ("h",),
        count=1,
        label="Hello world, this is the post",
    )
    assert "LinkedIn" in pub and "Hello world, this is the post" in pub


# ── F4-3 durability: a real process kill, not a mock ─────────────────────────


def _claim_then_die(root: str, stage: str) -> None:  # runs in a child process
    import os

    store = _store(Path(root))
    if stage == "before":
        os._exit(9)
    r = asyncio.run(store.claim(SEQ, keys("crash@acme.example"), tool="import"))
    assert r.ok
    if stage == "after_claim":
        os._exit(9)  # killed after the intent, before any provider call or outcome
    asyncio.run(store.record_outcome(r.batch_id, "dispatched"))
    os._exit(9)


@pytest.mark.parametrize("stage", ["before", "after_claim"])
def test_f4_3_real_sigkill_points(tmp_path, stage):
    ctx = multiprocessing.get_context("fork")
    p = ctx.Process(target=_claim_then_die, args=(str(tmp_path), stage))
    p.start()
    p.join()
    assert p.exitcode == 9
    again = asyncio.run(_store(tmp_path).claim(SEQ, keys("crash@acme.example"), tool="import"))
    if stage == "before":
        assert again.ok  # nothing sent, nothing blocked
    else:
        assert not again.ok and again.code == "outcome_unknown"  # blocked until a human resolves it


# ── F4-9 concurrency across real processes on the file store ─────────────────


def _racer(root: str, i: int, out) -> None:
    store = _store(Path(root))
    r = asyncio.run(
        store.claim(SEQ, keys("race@acme.example", f"r{i}@acme.example"), tool="import")
    )
    out.put(r.ok)


def test_f4_9_two_processes_racing_yield_exactly_one_winner(tmp_path):
    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=_racer, args=(str(tmp_path), i, q)) for i in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    wins = [q.get() for _ in procs]
    assert sum(wins) == 1


# ── append-only, tamper-evident, strict on corruption ────────────────────────


def test_rows_are_chained_and_never_edited(tmp_path):
    store = _store(tmp_path)
    a = asyncio.run(store.claim(SEQ, keys("c@acme.example"), tool="import"))
    asyncio.run(store.record_outcome(a.batch_id, "unknown"))
    asyncio.run(store.resolve(a.batch_id, "not_sent"))
    path = tmp_path / "example" / "dispatch-intents.jsonl"
    before = path.read_text().splitlines()
    asyncio.run(store.claim(SEQ, keys("d@acme.example"), tool="import"))
    after = path.read_text().splitlines()
    assert after[: len(before)] == before  # nothing earlier was rewritten
    rows = [json.loads(line) for line in after]
    assert [r["kind"] for r in rows] == ["claim", "outcome", "resolve", "claim"]
    assert all("prev_sha256" in r for r in rows)


def test_a_corrupt_line_refuses_the_send_rather_than_skipping_it(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.claim(SEQ, keys("c@acme.example"), tool="import"))
    path = tmp_path / "example" / "dispatch-intents.jsonl"
    path.write_text(path.read_text() + "{this is not json\n")
    with pytest.raises(di.IntentLedgerUnreadable):
        asyncio.run(store.claim(SEQ, keys("e@acme.example"), tool="import"))
    with pytest.raises(di.IntentLedgerUnreadable):
        asyncio.run(store.unresolved())


def test_a_torn_final_line_from_a_crash_mid_write_is_cut_not_fatal(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.claim(SEQ, keys("c@acme.example"), tool="import"))
    path = tmp_path / "example" / "dispatch-intents.jsonl"
    path.write_text(path.read_text() + '{"kind": "claim", "batch_id": "tor')  # no newline: torn
    again = asyncio.run(store.claim(SEQ, keys("c@acme.example"), tool="import"))
    assert (
        not again.ok
    )  # the first claim is still honoured; the torn tail did not poison the ledger


def test_f4_10_the_ledger_is_on_the_tenant_write_refusal_list():
    from agent.path_confinement import _LEDGER_NAMES

    assert "dispatch-intents.jsonl" in _LEDGER_NAMES


# ── F4-8 one function, two stores ────────────────────────────────────────────


def _calls(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text())
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) == name or getattr(n.func, "id", None) == name)
    )


def test_f4_8_both_stores_and_both_dispatchers_use_the_single_decision_function():
    assert _calls(REPO / "gtm_core" / "dispatch_intents.py", "decide") >= 1  # the file store
    pg = REPO / "backend" / "services" / "dispatch_intents.py"
    if pg.exists():
        assert _calls(pg, "decide") >= 1  # the Postgres store
    for dispatcher in ("agent/email_dispatch.py", "agent/publish_dispatch.py"):
        assert "claim" in (REPO / dispatcher).read_text(), dispatcher  # both go through store.claim


# ── CLI: dispatch-intents --unresolved / resolve / removed / hold / reconcile ─


def _cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "GTM_CONTENT_ROOT": str(tmp_path)}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.ledger_cli",
            "dispatch-intents",
            "--profile",
            "example",
            *args,
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_unresolved_resolve_sent_recipients_and_removed(tmp_path):
    store = _store(tmp_path)
    emails = [f"p{i:02d}@acme.example" for i in range(10)]
    a = asyncio.run(store.claim(SEQ, keys(*emails), tool="import"))
    asyncio.run(store.record_outcome(a.batch_id, "unknown"))
    out = _cli(tmp_path, "--unresolved")
    assert out.returncode == 0 and a.batch_id in out.stdout and "10" in out.stdout

    sent_file = tmp_path / "sent.txt"
    sent_file.write_text("\n".join(emails[:4]) + "\n")
    r = _cli(tmp_path, "resolve", a.batch_id, "--sent-recipients", str(sent_file))
    assert r.returncode == 0, r.stderr
    assert "4 marked sent" in r.stdout and "6 released" in r.stdout
    assert _cli(tmp_path, "--unresolved").stdout.strip().endswith("none open")

    rr = _cli(tmp_path, "removed", "--sequence", SEQ, "--recipient", emails[0])
    assert rr.returncode == 0
    assert asyncio.run(_store(tmp_path).claim(SEQ, keys(emails[0]), tool="import")).ok


def test_cli_resolve_rejects_an_unknown_batch_and_a_bad_combination(tmp_path):
    assert _cli(tmp_path, "resolve", "nope", "--sent").returncode != 0
    store = _store(tmp_path)
    a = asyncio.run(store.claim(SEQ, keys("z@acme.example"), tool="import"))
    asyncio.run(store.record_outcome(a.batch_id, "unknown"))
    assert _cli(tmp_path, "resolve", a.batch_id).returncode != 0  # must say which resolution
    assert _cli(tmp_path, "resolve", a.batch_id, "--sent", "--not-sent").returncode != 0


def test_f4_12_reconcile_after_a_restore_lists_sends_the_ledger_forgot_and_holds_dispatch(tmp_path):
    store = _store(tmp_path)
    known = asyncio.run(store.claim(SEQ, keys("known@acme.example"), tool="import"))
    asyncio.run(store.record_outcome(known.batch_id, "dispatched"))
    assert _cli(tmp_path, "--restored").returncode == 0
    held = asyncio.run(_store(tmp_path).claim(SEQ, keys("new@acme.example"), tool="import"))
    assert not held.ok and held.code == "on_hold"

    export = tmp_path / "sequencer.csv"
    export.write_text("email\nknown@acme.example\nForgot@Acme.Example\n")
    listing = _cli(tmp_path, "--reconcile", "--sequence", SEQ, "--sequencer-export", str(export))
    assert listing.returncode == 0
    assert "forgot@acme.example" in listing.stdout and "known@acme.example" not in listing.stdout
    assert (
        asyncio.run(_store(tmp_path).claim(SEQ, keys("new@acme.example"), tool="import")).code
        == "on_hold"
    )

    rec = _cli(
        tmp_path, "--reconcile", "--sequence", SEQ, "--sequencer-export", str(export), "--record"
    )
    assert rec.returncode == 0
    # recorded as sent -> a repeat of the forgotten recipient is now refused; the hold is cleared
    after = asyncio.run(
        _store(tmp_path).claim(SEQ, keys("forgot@acme.example", "new@acme.example"), tool="import")
    )
    assert (
        not after.ok
        and after.code == "already_dispatched"
        and after.overlap == ("forgot@acme.example",)
    )
    assert asyncio.run(_store(tmp_path).claim(SEQ, keys("new@acme.example"), tool="import")).ok


# ── who settled it (invariant-review finding): the resolving act is the one that re-enables a send ────────


def test_resolving_records_who_and_leaves_a_history_event(tmp_path):
    store = _store(tmp_path)
    c = asyncio.run(store.claim(SEQ, keys("z@acme.example"), tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    asyncio.run(store.resolve(c.batch_id, "not_sent", actor="user:u-123"))
    rows = [
        json.loads(x)
        for x in (tmp_path / "example" / "dispatch-intents.jsonl").read_text().splitlines()
    ]
    res = [r for r in rows if r["kind"] == "resolve"][0]
    assert res["by"] == "user:u-123"
    hist = [
        json.loads(x) for x in (tmp_path / "example" / "history.jsonl").read_text().splitlines()
    ]
    ev = [h for h in hist if h["event"] == "dispatch_resolved"][0]
    assert (
        ev["batch_id"] == c.batch_id and ev["resolution"] == "not_sent" and ev["by"] == "user:u-123"
    )


def test_marking_removed_records_who(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.mark_removed(SEQ, keys("gone@acme.example"), actor="cli"))
    rows = [
        json.loads(x)
        for x in (tmp_path / "example" / "dispatch-intents.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["kind"] == "removed" and rows[-1]["by"] == "cli"


def test_the_cli_stamps_itself_as_the_actor(tmp_path):
    store = _store(tmp_path)
    c = asyncio.run(store.claim(SEQ, keys("q@acme.example"), tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    assert _cli(tmp_path, "resolve", c.batch_id, "--not-sent").returncode == 0
    rows = [
        json.loads(x)
        for x in (tmp_path / "example" / "dispatch-intents.jsonl").read_text().splitlines()
    ]
    assert [r for r in rows if r["kind"] == "resolve"][0]["by"] == "cli"
