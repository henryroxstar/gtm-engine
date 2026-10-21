"""R1.5 concurrency: two writers on one shard never interleave; a cut-off line is named and repairable."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from gtm_core.signal_obs import observations as obs

pytestmark = pytest.mark.usefixtures("switch_open")

_WRITER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from gtm_core.signal_obs import observations as obs
    directory, tag = Path(sys.argv[1]), sys.argv[2]
    for i in range(1000):
        rec = obs.make_observation(
            kind="job_post", product="alpha", source_id="s-" + tag,
            source_url="https://members.example.test/list", capture_sha256="c" * 64,
            account_key=f"acct{i}.example.test", observed="2026-10-01",
            observed_basis="first_seen_in_capture", role="buyer", premise_at_write="",
            writer="amy.run1" + tag,
        )
        obs.append(directory, "shared-2026-10.jsonl", [rec])
    """
)


def test_two_processes_append_a_thousand_each_and_every_line_is_whole(tmp_path):
    d = tmp_path / "observations"
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(d), tag], stderr=subprocess.PIPE)
        for tag in ("a", "b")
    ]
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()
    lines = (d / "shared-2026-10.jsonl").read_text().splitlines()
    assert len(lines) == 2000
    parsed = [json.loads(line) for line in lines]
    assert {r["source_id"] for r in parsed} == {"s-a", "s-b"}
    got = obs.read_all(d, product="alpha")
    assert got.problems == [] and len(got.observations) == 2000


def _seed(d, n=3):
    recs = [
        obs.make_observation(
            kind="source_member",
            product="alpha",
            source_id="north-directory",
            source_url="https://members.example.test/list",
            capture_sha256="c" * 64,
            account_key=f"acct{i}.example.test",
            observed="2026-10-01",
            observed_basis="first_seen_in_capture",
            role="buyer",
            premise_at_write="",
            writer="amy.run1",
        )
        for i in range(n)
    ]
    return obs.append(d, "amy.run1-2026-10.jsonl", recs)


def test_a_cut_off_last_line_refuses_that_shard_and_names_it(tmp_path):
    d = tmp_path / "observations"
    shard = _seed(d)
    with shard.open("ab") as f:
        f.write(b'{"schema": 1, "kind": "source_me')
    got = obs.read_all(d, product="alpha")
    assert got.refused_shards == [shard.name]
    assert got.observations == []
    assert "repair" in got.problems[0]["why"]


def test_repair_is_plan_first_and_truncates_only_the_partial_last_line(tmp_path):
    d = tmp_path / "observations"
    shard = _seed(d)
    whole = shard.read_bytes()
    shard.write_bytes(whole + b'{"schema": 1, "kind": "source_me')
    plan = obs.repair(d, shard.name, apply=False)
    assert plan.drop_bytes == len(b'{"schema": 1, "kind": "source_me')
    assert shard.read_bytes() != whole  # the plan alone wrote nothing
    done = obs.repair(d, shard.name, apply=True)
    assert done.applied and shard.read_bytes() == whole
    assert len(obs.read_all(d, product="alpha").observations) == 3


def test_repair_refuses_damage_that_is_not_a_cut_off_tail(tmp_path):
    d = tmp_path / "observations"
    shard = _seed(d)
    lines = shard.read_text().splitlines()
    shard.write_text(lines[0] + "\n{not json}\n" + lines[2] + "\n")
    before = shard.read_bytes()
    plan = obs.repair(d, shard.name, apply=True)
    assert plan.applied is False and "not a cut-off" in plan.reason
    assert shard.read_bytes() == before


def test_eight_writers_queueing_the_same_names_queue_each_one_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from gtm_core.signal_obs import unresolved

    entries = [
        {"product": "alpha", "source_id": "s", "name": f"Member {n:03d} Holdings"}
        for n in range(60)
    ]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: unresolved.record(tmp_path, entries), range(8)))
    assert len(unresolved.read(tmp_path)) == 60
