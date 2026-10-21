"""A small, fictional profile with every sequence state the outreach log must tell apart.

Used by the outreach-log tests. The Saleshandy figures are written by the REAL snapshot writer
(``gtm_core.sequencer_snapshot.write``) from a payload plus a ``list_sequences`` reply — a
hand-built dict would let a test pass on a snapshot shape the writer never produces (a snapshot
written without the list reply has no ``status`` at all). Fictional identities only (§R9).

Sequences (ids are ten characters, the provider's shape):

=============  ==========================================================================
LiveSeq001     sending; registered TWICE (two lists, one spec); one person is also known
               only to the dispatcher's ledger, one is also in a closed-out file
PausedSeq1     not sending; provider says 3 emailed of 4; two of them are closed out
StagedSeq1     not sending, 0 emailed (loaded, not started)
DeletedSq1     deleted per history; never in the figures; its people include a free-text
               suppression and a company-level one
GoneSend01     deleted per history; its last figures say 2 emailed; one is closed out
AbsentSeq1     registered, but not in the figures at all
=============  ==========================================================================
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from gtm_core import sequencer_snapshot as ss

PROFILE = "acme"
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
FETCHED = "2026-10-08T08:00:00Z"

LIVE, PAUSED, STAGED = "LiveSeq001", "PausedSeq1", "StagedSeq1"
DELETED, GONE, ABSENT = "DeletedSq1", "GoneSend01", "AbsentSeq1"

P = {  # name -> address
    "ada": "ada@lantern.example",
    "ben": "ben@lantern.example",  # unsubscribed-link on the ledger
    "cy": "cy@lantern.example",  # closed out AND on a sending sequence's list
    "dee": "dee@lantern.example",  # on the second list of LiveSeq001
    "pat": "pat@lantern.example",  # known only to the dispatcher's ledger
    "eli": "eli@riverbend.example",  # closed out, no-reply
    "fay": "fay@riverbend.example",  # closed out, replied
    "gus": "gus@riverbend.example",  # bookkeeping reason only
    "hal": "hal@riverbend.example",  # bookkeeping row, THEN a real opt-out row
    "ida": "ida@copperline.example",
    "jo": "jo@eastvale.example",  # free-text suppression reason
    "kim": "kim@competitor.example",  # company-level 'competitor'
    "lee": "lee@northgate.example",  # closed out, in a deleted sequence
    "max": "max@northgate.example",  # not closed out, same deleted sequence
    "ned": "ned@southfield.example",
    "zed": "zed@westbrook.example",  # closed out, lane-label token: in no sequence
    "yan": "yan@westbrook.example",  # closed out, unknown ten-character token
}

SPEC = """# Sequence spec — test

## 4. Variant

**Step 1 — Day 0** · Subject: `a note for {{Company}}`

> Hi {{First Name}},
>
> One line about {{Company}}.

**Step 2 — Day 3** (same thread, no subject)

> Following up, {{First Name}}.
"""


def _list(path: Path, *people: str) -> None:
    head = "email,first,last,company,title,company_domain,city,country,signal_clause\n"
    body = ""
    for key in people:
        email = P[key]
        body += f"{email},{key.title()},Test,{email.split('@')[1].split('.')[0].title()} Co,CTO,{email.split('@')[1]},X,Y,a clause\n"
    path.write_text(head + body, encoding="utf-8")


def _row(sid: str, total: int, contacted: int) -> dict:
    return {
        "sequenceId": sid,
        "sequenceName": f"Name {sid}",
        "prospects": [{"total": str(total), "contacted": str(contacted)}],
        "emails": {"status": {"delivered": contacted}},
    }


def write_snapshot(root: Path, *, fetched: str = FETCHED, now: datetime = NOW, rows=None) -> None:
    """Write the figures through the real writer, with the list reply that stamps each flag."""
    rows = (
        rows
        if rows is not None
        else [
            (LIVE, 5, 1, True),
            (PAUSED, 4, 3, False),
            (STAGED, 1, 0, False),
            (GONE, 2, 2, False),
        ]
    )
    pay = root.parent / "payloads"
    pay.mkdir(exist_ok=True)
    payload = pay / "stats.json"
    payload.write_text(
        json.dumps({"sequences": [_row(s, t, c) for s, t, c, _a in rows]}), encoding="utf-8"
    )
    os.utime(payload, (now.timestamp(), now.timestamp()))
    listing = pay / "list.json"
    listing.write_text(
        json.dumps(
            {
                "message": "ok",
                "payload": [
                    {"id": s, "title": f"Name {s}", "active": a, "steps": [{}, {}]}
                    for s, _t, _c, a in rows
                ],
            }
        ),
        encoding="utf-8",
    )
    ok, lines = ss.write(
        PROFILE, [payload], content_root=root, fetched=fetched, now=now, sequences=[listing]
    )
    assert ok, lines


def make_world(tmp_path: Path, *, snapshot: bool = True) -> tuple[Path, Path]:
    """``(content_root, sequences_dir)`` for the profile described in the module docstring."""
    root = tmp_path / "content"
    seq = root / PROFILE / "prospects" / "sequences"
    (seq / ".pool").mkdir(parents=True)
    _list(seq / "a.csv", "ada", "ben", "cy")
    _list(seq / "a2.csv", "dee")
    _list(seq / "b.csv", "eli", "fay", "gus", "hal")
    _list(seq / "c.csv", "ida")
    _list(seq / "d.csv", "jo", "kim")
    _list(seq / "g.csv", "lee", "max")
    _list(seq / "e.csv", "ned")
    (seq / "spec.md").write_text(SPEC, encoding="utf-8")

    def reg(sid: str, csv: str) -> str:
        return (
            f'[[sequence]]\nid = "{sid}"\ncsv = "{csv}"\nspec = "spec.md"\ntitle = "Title {sid}"\n'
        )

    (seq / "cells.toml").write_text(
        reg(LIVE, "a.csv")
        + reg(LIVE, "a2.csv")
        + reg(PAUSED, "b.csv")
        + reg(STAGED, "c.csv")
        + reg(DELETED, "d.csv")
        + reg(GONE, "g.csv")
        + reg(ABSENT, "e.csv")
        + reg("DRAFT-x", "a.csv"),
        encoding="utf-8",
    )
    (root / PROFILE / "history.jsonl").write_text(
        json.dumps({"event": "sequence_deleted", "sequence_id": DELETED})
        + "\n"
        + json.dumps({"event": "sequence_cleanup", "sequences_deleted": [{"id": GONE}]})
        + "\n",
        encoding="utf-8",
    )
    (seq / "contacted-2026-10-06.csv").write_text(
        "email,name,company_domain,sequences,max_step,outcome,source\n"
        f"{P['eli']},Eli Test,riverbend.example,{PAUSED},1,no-reply,saleshandy\n"
        f"{P['fay']},Fay Test,riverbend.example,{PAUSED},2,replied,saleshandy\n"
        f"{P['cy']},Cy Test,lantern.example,,1,replied,manual\n"
        f"{P['zed']},Zed Test,westbrook.example,manual:generic,1,unsubscribed,manual\n"
        f"{P['yan']},Yan Test,westbrook.example,Unknown001,1,no-reply,manual\n"
        f"{P['lee']},Lee Test,northgate.example,{GONE},1,no-reply,saleshandy\n",
        encoding="utf-8",
    )
    (root / PROFILE / "prospects" / "enrolments.jsonl").write_text(
        json.dumps(
            {"email": P["pat"], "sequence_id": LIVE, "cell_id": "", "dispatched_at": FETCHED}
        )
        + "\n",
        encoding="utf-8",
    )
    (seq / ".pool" / "suppression.csv").write_text(
        "email,name,company_domain,reason,date,note\n"
        f"{P['ben']},,,unsubscribed-link,2026-10-06,\n"
        f"{P['gus']},,,cross-sequence-overlap,2026-10-06,\n"
        f"{P['hal']},,,cross-sequence-overlap,2026-10-06,\n"
        f"{P['hal']},,,dnc-optout,2026-10-07,\n"
        f"{P['jo']},,,Adjudicated unbuyable during verification,2026-08-30,\n"
        ",,competitor.example,competitor,2026-09-01,\n",
        encoding="utf-8",
    )
    pack = root / PROFILE / "accounts" / "nimbus"
    pack.mkdir(parents=True)
    (pack / "prospects-20261001-outreach-nimbus.md").write_text(
        "# Outreach Pack — Nimbus — 2026-10-01\n\n**Tier:** A | **Score:** 9/10\n"
        "**Primary persona:** Jordan Lee — CTO (jordan.lee@example.com, verified)\n\n"
        "## Email (touch 1)\n**Subject:** hello there\n> the body\n",
        encoding="utf-8",
    )
    if snapshot:
        write_snapshot(root)
    return root, seq
