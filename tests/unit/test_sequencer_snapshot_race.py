"""Two refreshes at once must both land — the writer's read-merge-write is one locked step.

Real processes, a shared start time, real files. A lost update shows as a missing sequence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

from gtm_core import sequencer_snapshot as ss
from gtm_core.sequence_snapshot_format import body_digest

PROFILE = "acme"
ROUNDS = 6
WRITERS = 3

_CHILD = (
    "import sys, time; t = float(sys.argv[1]); time.sleep(max(0.0, t - time.time()));"
    "from gtm_core.sequencer_snapshot import main; sys.exit(main(sys.argv[2:]))"
)


def _row(sid):
    return {
        "sequenceId": sid,
        "prospects": [{"total": 3, "contacted": 3}],
        "emails": {"status": {"delivered": 3}},
    }


def _payload(tmp_path, name, sid):
    p = tmp_path / "payloads" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({"sequences": [_row(sid)]}), encoding="utf-8")
    return p


def test_concurrent_writers_never_lose_a_sequence(tmp_path):
    (tmp_path / PROFILE).mkdir()
    expected: set[str] = set()
    for rnd in range(ROUNDS):
        start = time.time() + 1.5
        procs = []
        for w in range(WRITERS):
            sid = f"R{rnd}W{w}"
            expected.add(sid)
            f = _payload(tmp_path, f"{sid}.json", sid)
            procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _CHILD,
                        str(start),
                        "--profile",
                        PROFILE,
                        "--content-root",
                        str(tmp_path),
                        "write",
                        "--payload",
                        str(f),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            )
        outs = [(p.communicate(timeout=60)[0], p.returncode) for p in procs]
        assert all(rc == 0 for _, rc in outs), outs
        doc = json.loads(ss.stats_path(PROFILE, tmp_path).read_text(encoding="utf-8"))
        have = {r["sequenceId"] for r in doc["sequences"]}
        assert expected <= have, f"round {rnd}: lost {sorted(expected - have)}"
        assert set(doc["stamps"]) == have


def _wide_row(i, sent):
    row = _row(f"W{i:03d}")
    row["description"] = "x" * 600  # a big file, so a truncate-then-write has a wide window
    row["prospects"][0]["contacted"] = sent
    row["emails"]["status"]["delivered"] = sent
    return row


def test_a_reader_never_sees_a_partial_file_while_writers_replace_it(tmp_path):
    """The write is a rename of a finished temp file, so a reader sees the old file or the new one.

    A plain ``write_text`` truncates in place: a reader that lands between the truncate and the
    last byte reads an empty or cut-off file, which is exactly what the page and ``status`` load.
    """
    (tmp_path / PROFILE).mkdir()
    path = ss.stats_path(PROFILE, tmp_path)

    def refresh(sent):
        f = tmp_path / "payloads" / f"wide-{sent}.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"sequences": [_wide_row(i, sent) for i in range(300)]}))
        ok, lines = ss.write(PROFILE, [f], content_root=tmp_path)
        assert ok, lines

    refresh(1)
    assert path.stat().st_size > 200_000, "the fixture must be big enough to tear"
    stop, torn, reads = threading.Event(), [], []

    def reader():
        while not stop.is_set():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if doc.get("body_sha256") != body_digest(doc):
                    torn.append("a whole file whose body does not match its hash")
            except (ValueError, OSError) as exc:
                torn.append(f"{type(exc).__name__}: {str(exc)[:60]}")
            reads.append(1)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for sent in range(2, 32):
            refresh(sent)
    finally:
        stop.set()
        thread.join(timeout=30)
    assert len(reads) > 30, "the reader must have run alongside the writes"
    assert torn == [], f"{len(torn)} torn read(s) of {len(reads)}: {torn[:2]}"
