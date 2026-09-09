"""The `sent` denominator: per-sequence deltas, per-recipient rows when addresses exist,
and a refusal — never a zero — for a shape nobody recognises. Fictional data only."""

from __future__ import annotations

import json

from gtm_core import sequencer_sends as ss
from gtm_core.wave_gate import read_reports

SEQ = "SEQabc123"


def _stats(delivered=17, replied=1, unsubscribed=1, bounced=0, sid=SEQ):
    """The real 2026-08-31 payload shape, values as the provider types them (strings)."""
    return {
        "fetched": "2026-09-03T07:00:00Z",
        "sequences": [
            {
                "sequenceId": sid,
                "sequenceName": "Run · Generic",
                "prospects": [
                    {
                        "total": "326",
                        "replied": str(replied),
                        "unsubscribed": str(unsubscribed),
                        "contacted": str(delivered),
                        "bounced": str(bounced),
                    }
                ],
                "emails": {
                    "total": delivered,
                    "status": {"delivered": delivered, "replied": replied, "bounced": bounced},
                },
            }
        ],
    }


def _plan(payloads, existing=(), **kw):
    base = {
        "lane_by_seq": {SEQ: "generic"},
        "cell_by_seq": {SEQ: ["enterprise:security:alpha"]},
        "email_to_cell": {},
        "email_to_lane": {},
        "fetched": "2026-09-03",
    }
    base.update(kw)
    return ss.plan_sends(payloads, list(existing), **base)


def test_aggregate_delta_rows_carry_seq_lane_and_a_single_cell():
    rows, notes = _plan([_stats()])
    assert len(rows) == 1
    r = rows[0]
    assert r["outcome"] == "sent" and r["value"] == 17 and r["channel"] == "email"
    assert set(r["tags"]) == {f"seq:{SEQ}", "lane:generic", "cell:enterprise:security:alpha"}
    assert r["meta"]["aggregate"] is True and r["meta"]["sent_key"] == f"{SEQ}:2026-09-03"
    assert r["meta"]["cumulative_delivered"] == 17 and r["meta"]["unsubscribed"] == 1


def test_sends_is_idempotent_on_rerun_and_records_only_the_delta():
    first, _ = _plan([_stats(17)])
    again, notes = _plan([_stats(17)], existing=first)
    assert again == [] and any("already recorded" in n for n in notes)
    later, notes = _plan([_stats(17)], existing=first, fetched="2026-09-10")
    assert later == [] and any("no new sends" in n for n in notes)
    grown, _ = _plan([_stats(25)], existing=first, fetched="2026-09-10")
    assert len(grown) == 1 and grown[0]["value"] == 8, "a cumulative total must never be re-counted"


def test_sends_refuses_unrecognised_payload_shape_rather_than_writing_zero():
    rows, notes = _plan([{"sequences": [{"sequenceId": SEQ, "name": "x"}]}])
    assert rows == [] and any(n.startswith("REFUSED") for n in notes)
    rows, notes = _plan([{"unrelated": True}])
    assert rows == [] and any(n.startswith("REFUSED") for n in notes)
    # Positive control: the real shape is accepted.
    assert _plan([_stats()])[0]


def test_a_sequence_spanning_several_cells_counts_toward_the_lane_not_a_cell():
    rows, notes = _plan([_stats()], cell_by_seq={SEQ: ["a:b:c", "d:e:f"]})
    assert not any(t.startswith("cell:") for t in rows[0]["tags"])
    assert "lane:generic" in rows[0]["tags"] and any("2 cell(s)" in n for n in notes)


def test_per_recipient_rows_when_addresses_exist_and_bounces_are_excluded():
    payload = {
        "sequences": [
            {
                "sequenceId": SEQ,
                "prospects": [
                    {"email": "a@acme.example", "status": "contacted"},
                    {"email": "b@acme.example", "status": "hardBounced"},
                    {"email": "c@acme.example", "status": "delivered"},
                ],
            }
        ]
    }
    rows, _ = _plan(
        [payload],
        email_to_cell={"a@acme.example": "enterprise:security:alpha"},
        email_to_lane={"a@acme.example": "personalised"},
    )
    assert [r["meta"]["prospect_email"] for r in rows] == ["a@acme.example", "c@acme.example"]
    assert (
        "cell:enterprise:security:alpha" in rows[0]["tags"]
        and "lane:personalised" in rows[0]["tags"]
    )
    assert "lane:generic" in rows[1]["tags"], "an unknown address inherits the sequence's lane"
    again, _ = _plan([payload], existing=rows)
    assert again == [], "per-recipient rows are idempotent on <seq>:<email>"


def test_wave_reports_one_per_sequence():
    reps = ss.wave_reports([_stats(17, replied=2, unsubscribed=3)], "2026-09-03")
    assert len(reps) == 1 and reps[0].wave == SEQ and reps[0].sends == 17
    assert reps[0].replies == 2 and reps[0].opt_outs == 3 and reps[0].positive_replies == 0


def test_cli_apply_writes_both_ledgers_and_is_idempotent(tmp_path, monkeypatch, capsys):
    from gtm_core.outcomes import read_outcomes

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps(_stats()), encoding="utf-8")
    assert ss._cli(["--profile", "acme", "--stats", str(stats)]) == 0
    assert read_outcomes(tmp_path, "acme") == [], "dry run writes nothing"
    assert ss._cli(["--profile", "acme", "--stats", str(stats), "--apply"]) == 0
    rows = read_outcomes(tmp_path, "acme")
    assert len(rows) == 1 and rows[0]["value"] == 17 and "lane:unregistered" in rows[0]["tags"]
    reports = read_reports("acme", tmp_path)
    assert len(reports) == 1 and reports[0].sends == 17
    ss._cli(["--profile", "acme", "--stats", str(stats), "--apply"])
    assert len(read_outcomes(tmp_path, "acme")) == 1 and len(read_reports("acme", tmp_path)) == 1


def test_cli_refuses_an_unreadable_payload_with_a_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"sequences": [{"sequenceId": SEQ}]}), encoding="utf-8")
    assert ss._cli(["--profile", "acme", "--stats", str(stats), "--apply"]) == 1
