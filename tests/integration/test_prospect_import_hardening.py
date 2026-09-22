"""`prospects_import finalize` / `stage-standard` treat `items.json` as what it is: LLM output.

The file is written by the brain (§R5 — structured output is untrusted), and everything it
says lands in the ledger of record and in the CSV a human loads into a sequencer. These tests
pin the properties an audit proved missing by execution: no research conclusion is invented
for a field the caller left out, a row the scorer refused never becomes sendable, a thin
re-discovery never erases research, a bad value is a loud one-line refusal that writes
nothing, and neither `--source-run` nor an item's `id` can name a path.

All fixtures are fictional (§R9); every write is confined to tmp_path.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from gtm_core import prospects_import as pi
from gtm_core import prospects_state as ps
from gtm_core.signal_record import check_record

PROFILE = "qa-sandbox"

RESEARCHED = {
    "company": "Contoso Freight",
    "domain": "contosofreight.example",
    "segment": "enterprise",
    "market": "Singapore",
    "score": 9,
    "tier": "A",
    "heat": 2,
    "intent_feeds": ["vibe-topic"],
    "new_in_role": True,
    "contact_name": "Rowan Pike",
    "contact_title": "Founder",
    "contact_email": "rowan.pike@contosofreight.example",
    "email_status": "verified",
    "why_now": "Contoso Freight opened its agent platform to partner organisations.",
    "signal_source_url": "https://contosofreight.example/news/partner-agents",
    "signal_observed": "2026-09-10",
    "signal_evidence": "Contoso Freight opened its agent platform to partner organisations.",
    "signal_subject": "Contoso Freight",
    "signal_agent_kind": "ai",
    "category_relation": "prospect",
    "verdict": "send",
    "lane": "personalised",
}


#: A row carrying a SCORE must name the rubric that produced it, or `finalize` refuses before
#: writing anything (`prospects_import.require_rubric_provenance`).
RUBRIC = {"rubric_source": "knowledge/fixture.md#rubric", "rubric_version": "2026-01-01"}


def _finalize(root: Path, items, run: str = "run-1", **kw) -> dict:
    """Call `finalize`, supplying rubric provenance to any scored row that omits it.

    Injected in the helper rather than written into two dozen inline fixtures, and NOT by
    disabling the gate: every test in this file is about some other property — CSV render
    failure, the state merge, do-not-contact, name variants — and would otherwise be blocked by
    a refusal it is not testing. The provenance gate has its own suite
    (`tests/unit/test_scorecard_provenance.py`); a row that deliberately omits the rubric there
    is the point, and here it is noise. A row with no score is left alone, and a row that names
    its own rubric keeps it.
    """
    supplied = [
        {**RUBRIC, **item}
        if isinstance(item, dict) and "score" in item and not item.get("rubric_source")
        else item
        for item in items
    ]
    return pi.finalize(PROFILE, supplied, run, content_root=root, **kw)


def _ledger(root: Path) -> list[dict]:
    return ps.load_latest(PROFILE, content_root=root)["items"]


def _csv_rows(summary: dict) -> list[dict]:
    with Path(summary["hubspot_csv"]).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _written(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# --- PSK-012: no fabricated research conclusions -------------------------------------- #

FAIL_CLOSED = ("verdict", "category_relation", "signal_subject", "signal_agent_kind")


def test_a_field_the_researcher_left_out_stays_blank():
    item = pi.build_standard_item(
        {"company": "Northwind Robotics", "score": 9, "why_now": "Shipped an MCP agent gateway."}
    )
    assert {f: item[f] for f in FAIL_CLOSED} == dict.fromkeys(FAIL_CLOSED, "")
    assert (item["verdict_reason"], item["lane"], item["lane_reason"]) == ("", "", "")


def test_the_record_gate_still_blocks_an_item_nobody_researched():
    """The property the blanks exist for: a defaulted record used to sail through the gate."""
    item = pi.build_standard_item(
        {"company": "Northwind Robotics", "score": 9, "why_now": "Shipped an MCP agent gateway."}
    )
    codes = {f.rule for f in check_record(item) if f.level == "block"}
    assert {
        "verdict-missing",
        "relation-unresolved",
        "signal-subject-missing",
        "agent-kind-unresolved",
    } <= codes


def test_agent_kind_is_never_inferred_from_the_clause():
    item = pi.build_standard_item(
        {"company": "Fabrikam Staffing", "why_now": "Hiring customer service agents for a centre."}
    )
    assert item["signal_agent_kind"] == ""


def test_supplied_vocabulary_is_normalised_not_rejected_for_its_case():
    item = pi.build_standard_item(
        {
            "company": "Northwind Robotics",
            "verdict": " SEND ",
            "lane": "Personalised",
            "signal_agent_kind": "AI",
            "category_relation": "Prospect",
        }
    )
    assert (item["verdict"], item["lane"]) == ("send", "personalised")
    assert (item["signal_agent_kind"], item["category_relation"]) == ("ai", "prospect")


def test_a_lane_is_never_derived_from_the_verdict():
    """`lanes route` is the only thing that stamps a lane."""
    assert (
        pi.build_standard_item({"company": "Northwind Robotics", "verdict": "send"})["lane"] == ""
    )
    kept = pi.build_standard_item({"company": "Northwind Robotics", "lane": "repair"})
    assert kept["lane"] == "repair"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verdict", "maybe"),
        ("lane", "fast"),
        ("signal_agent_kind", "robot"),
        ("category_relation", "customer"),
    ],
)
def test_a_value_outside_the_vocabulary_names_its_row_and_field_and_writes_nothing(
    tmp_path, field, value
):
    items = [{"company": "Northwind Robotics"}, {"company": "Contoso Freight", field: value}]
    with pytest.raises(ValueError, match=rf"row 2 .*{field}"):
        _finalize(tmp_path, items)
    assert _written(tmp_path) == []


# --- PSK-011: a refused row is recorded, never exported ------------------------------- #


def test_a_row_the_scorer_dropped_is_a_recorded_refusal_and_never_reaches_the_csv(tmp_path):
    items = [
        {"company": "Fabrikam Staffing", "score": 2, "tier": "drop", "contact_name": "Sam Low"},
        {"company": "Northwind Robotics", "score": 8, "tier": "A", "contact_name": "Avery Quill"},
    ]
    summary = _finalize(tmp_path, items, standard=True)
    assert summary["refused"] == 1
    assert [r["Company Name"] for r in _csv_rows(summary)] == ["Northwind Robotics"]
    dropped = next(i for i in _ledger(tmp_path) if i["company"] == "Fabrikam Staffing")
    assert (dropped["verdict"], dropped["verdict_reason"], dropped["lane"]) == (
        "drop",
        "below publish threshold",
        "excluded",
    )


def test_a_researchers_drop_keeps_its_own_reason_and_is_refused_the_same_way(tmp_path):
    items = [
        {
            "company": "Fabrikam Staffing",
            "tier": "A",
            "verdict": "Drop",
            "verdict_reason": "the agents in the clause are people",
        }
    ]
    summary = _finalize(tmp_path, items, standard=True)
    assert (summary["refused"], _csv_rows(summary)) == (1, [])
    dropped = _ledger(tmp_path)[0]
    assert dropped["verdict_reason"] == "the agents in the clause are people"
    assert dropped["lane"] == "excluded"


def test_a_scorer_drop_outranks_a_send_the_same_item_asserts(tmp_path):
    item = {
        "company": "Fabrikam Staffing",
        "tier": "DROP",
        "verdict": "send",
        "verdict_reason": "strong fit",
    }
    summary = _finalize(tmp_path, [item])
    assert summary["refused"] == 1
    dropped = _ledger(tmp_path)[0]
    # The send's reason explained the send; it must not be left captioning the drop.
    assert (dropped["verdict"], dropped["verdict_reason"]) == ("drop", "below publish threshold")


def test_a_scorer_drop_on_a_known_account_is_recorded_not_ignored(tmp_path):
    """The one machine-written verdict, and only in the fail-closed direction: a refusal
    that left the old `send` standing would leave the account's earlier rows sendable."""
    _finalize(tmp_path, [dict(RESEARCHED)], standard=True)
    _finalize(tmp_path, [{"company": "Contoso Freight", "score": 2, "tier": "drop"}], run="run-2")
    (account,) = _ledger(tmp_path)
    assert (account["verdict"], account["lane"]) == ("drop", "excluded")
    assert account["verdict_reason"] == "below publish threshold"
    assert account["why_now"] == RESEARCHED["why_now"]  # the research itself is kept


def test_a_full_bulk_mode_item_is_refused_too(tmp_path):
    full = {**RESEARCHED, "id": "contoso-freight", "status": "new", "tier": "drop"}
    summary = _finalize(tmp_path, [full])
    assert (summary["refused"], _csv_rows(summary)) == (1, [])


# --- PSK-013: a thin re-discovery never erases research ------------------------------- #


def test_a_minimal_rediscovery_changes_only_what_it_supplies(tmp_path):
    _finalize(tmp_path, [dict(RESEARCHED)], standard=True)
    before = _ledger(tmp_path)[0]
    _finalize(
        tmp_path,
        [{"company": "Contoso Freight", "score": 7, "contact_name": "Dana Holt"}],
        run="run-2",
        standard=True,
    )
    after = _ledger(tmp_path)[0]
    changed = {k for k in before if before[k] != after.get(k)}
    assert changed == {"score", "tier", "contact_name"}, changed
    assert (after["verdict"], after["verdict_reason"]) == ("send", "")


def test_two_personas_at_one_company_share_an_account_and_both_reach_the_csv(tmp_path):
    second = {
        "company": "Contoso Freight",
        "contact_name": "Dana Holt",
        "contact_title": "CTO",
        "contact_email": "dana.holt@contosofreight.example",
    }
    summary = _finalize(tmp_path, [dict(RESEARCHED), second], standard=True)
    assert [r["Email"] for r in _csv_rows(summary)] == [
        "rowan.pike@contosofreight.example",
        "dana.holt@contosofreight.example",
    ]
    (account,) = _ledger(tmp_path)
    assert account["why_now"] == RESEARCHED["why_now"]
    assert account["domain"] == "contosofreight.example"
    assert account["verdict"] == "send"


def test_a_staged_file_carries_no_default_that_could_overwrite_research(tmp_path, monkeypatch):
    """`stage-standard` then `finalize` is a documented path; it must merge like `--standard`."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _finalize(tmp_path, [dict(RESEARCHED)], standard=True)
    before = _ledger(tmp_path)[0]
    minimal, staged = tmp_path / "minimal.json", tmp_path / "staged.json"
    minimal.write_text(json.dumps([{"company": "Contoso Freight"}]), encoding="utf-8")
    assert pi._cli(["stage-standard", "--items", str(minimal), "--out", str(staged)]) == 0
    args = ["finalize", "--profile", PROFILE, "--items", str(staged), "--source-run", "run-2"]
    assert pi._cli(args) == 0
    assert _ledger(tmp_path)[0] == {**before}


def test_name_variants_of_one_company_are_one_account(tmp_path):
    _finalize(tmp_path, [{"company": "Northwind Robotics, Inc.", "score": 8}], standard=True)
    _finalize(tmp_path, [{"company": "Northwind Robotics", "score": 9}], run="run-2", standard=True)
    (account,) = _ledger(tmp_path)
    assert account["score"] == 9


# --- PSK-022: nothing the caller supplies can name a path ----------------------------- #


def test_source_run_cannot_name_a_path(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    with pytest.raises(ValueError, match="source_run"):
        _finalize(root, [dict(RESEARCHED)], run="r1/../../../ESCAPED")
    assert _written(tmp_path) == []


@pytest.mark.parametrize(
    ("profile", "run", "names"),
    [(PROFILE, "r1/../../../ESCAPED", "source_run"), ("../ESCAPED", "run-1", "profile")],
)
def test_ingest_refuses_a_profile_or_source_run_that_names_a_path(tmp_path, profile, run, names):
    """`ingest` writes its candidates file BEFORE it reaches any guarded ledger path."""
    export = tmp_path / "export.csv"
    export.write_text("business_name\nNorthwind Robotics\n", encoding="utf-8")
    root = tmp_path / "content"
    root.mkdir()
    with pytest.raises(ValueError, match=names):
        pi.ingest(export, profile, run, content_root=root)
    assert _written(tmp_path) == ["export.csv"]


@pytest.mark.parametrize("hostile", ["../../../ESCAPED", "a/b", "Northwind Robotics", 7])
def test_an_item_supplied_id_is_rederived_never_trusted(hostile):
    item = pi.build_standard_item({"company": "Northwind Robotics", "id": hostile})
    assert item["id"] == "northwind-robotics"


# --- §R5: coercion of untrusted values ------------------------------------------------ #


@pytest.mark.parametrize(
    ("field", "given", "expected"),
    [
        ("intent_feeds", "vibe-topic", ["vibe-topic"]),
        ("intent_feeds", ["vibe-topic", " "], ["vibe-topic"]),
        ("new_in_role", "false", False),
        ("new_in_role", "No", False),
        ("new_in_role", "true", True),
        ("new_in_role", 1, True),
        ("score", "8.5", 8.5),
        ("score", 7.5, 7.5),
        ("score", "9", 9),
        ("score", 8.0, 8),
        ("heat", "2", 2),
        ("segment", "Enterprise", "enterprise"),
        ("tier", "b", "B"),
    ],
)
def test_a_loosely_typed_value_is_coerced_to_what_it_means(field, given, expected):
    item = pi.build_standard_item({"company": "Northwind Robotics", field: given})
    assert item[field] == expected
    assert type(item[field]) is type(expected)


def test_fit_score_is_read_when_no_score_is_given():
    assert pi.build_standard_item({"company": "Northwind Robotics", "fit_score": "8"})["score"] == 8


BAD_ITEMS = [
    pytest.param(["just a string"], "row 1", id="non-object-item"),
    pytest.param({"company": "Northwind Robotics"}, "array", id="items-not-an-array"),
    pytest.param(
        [{"company": "Northwind Robotics", "heat": "high"}], "row 1 .*heat", id="heat-word"
    ),
    pytest.param([{"company": "Northwind Robotics", "heat": None}], "row 1 .*heat", id="heat-null"),
    pytest.param(
        [{"company": "Northwind Robotics", "segment": 7}], "row 1 .*segment", id="segment"
    ),
    pytest.param([{"company": "Northwind Robotics", "score": "9/10"}], "row 1 .*score", id="score"),
    pytest.param(
        [{"company": "Northwind Robotics", "score": True}], "row 1 .*score", id="score-bool"
    ),
    pytest.param(
        [{"company": "Northwind Robotics", "new_in_role": "soon"}], "new_in_role", id="flag"
    ),
    pytest.param(
        [{"company": "Northwind Robotics", "intent_feeds": [3]}], "intent_feeds", id="feeds"
    ),
    pytest.param(
        [{"company": "Northwind Robotics"}, {"score": 9}], "row 2.*company", id="no-company"
    ),
    pytest.param([{"company": "   "}], "row 1.*company", id="blank-company"),
]


@pytest.mark.parametrize("verb", ["finalize", "stage-standard"])
@pytest.mark.parametrize(("payload", "names"), BAD_ITEMS)
def test_a_malformed_item_is_one_clean_refusal_that_writes_nothing(
    tmp_path, monkeypatch, capsys, verb, payload, names
):
    import re

    root = tmp_path / "content"
    root.mkdir()
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    src = tmp_path / "items.json"
    # Same reason as `_finalize`: every case here pins the message for ONE malformed field, and a
    # missing rubric would refuse first and hide it. The provenance refusal has its own case in
    # tests/unit/test_scorecard_provenance.py.
    scored = (
        [
            {**RUBRIC, **item} if isinstance(item, dict) and "score" in item else item
            for item in payload
        ]
        if isinstance(payload, list)
        else payload  # the not-an-array case: it must reach the CLI exactly as written
    )
    src.write_text(json.dumps(scored), encoding="utf-8")
    args = {
        "finalize": ["--profile", PROFILE, "--source-run", "run-1", "--standard"],
        "stage-standard": ["--out", str(root / "staged.json")],
    }[verb]

    code = pi._cli([verb, "--items", str(src), *args])

    err = capsys.readouterr().err
    assert code != 0
    assert err.startswith("ERROR: ") and len(err.strip().splitlines()) == 1, err
    assert re.search(names, err), err
    assert _written(root) == []


def test_an_unscored_row_is_given_no_tier() -> None:
    """A tier is a claim that the row was scored; "B" on an unscored row reads as publishable."""
    from gtm_core.prospects_item import new_account_defaults

    assert new_account_defaults({"company": "Northwind Robotics"})["tier"] == ""
    assert new_account_defaults({"company": "Northwind Robotics", "score": 9})["tier"] == "A"
    assert new_account_defaults({"company": "Northwind Robotics", "score": 0})["tier"] == "B"


# --- the CSV row belongs to the ACCOUNT the item merged into --------------------------- #

LEGAL_NAME = {**RESEARCHED, "company": "Contoso Freight, Inc."}

THIN_VARIANT = {
    # A later run names the account by its cleaned name: no domain, a new verified contact.
    "company": "Contoso Freight",
    "score": 9,
    "tier": "A",
    "contact_name": "Ines Vale",
    "contact_title": "COO",
    "contact_email": "ines.vale@contosofreight.example",
    "email_status": "verified",
}


def test_an_exported_row_carries_the_ledger_accounts_name_and_domain(tmp_path):
    """The item's own name + no domain is a row consolidate's exact-key join cannot place."""
    _finalize(tmp_path, [dict(LEGAL_NAME)], standard=True)
    summary = _finalize(tmp_path, [dict(THIN_VARIANT)], run="run-2", standard=True)
    (row,) = _csv_rows(summary)
    assert row["Email"] == "ines.vale@contosofreight.example"
    assert (row["First Name"], row["Job Title"]) == ("Ines", "COO")
    assert row["Company Name"] == "Contoso Freight, Inc."
    assert row["Company Domain Name"] == "contosofreight.example"
    assert summary["excluded_retired"] == 0


@pytest.mark.parametrize("status", [*sorted(ps.RETIRED_STATUSES), "replied"])
def test_a_contact_at_a_retired_account_is_never_exported_under_a_name_variant(
    tmp_path, capsys, status
):
    _finalize(tmp_path, [dict(LEGAL_NAME)], standard=True)
    ps.set_status(PROFILE, {"d:contosofreight.example": status}, content_root=tmp_path)
    capsys.readouterr()

    summary = _finalize(tmp_path, [dict(THIN_VARIANT)], run="run-2", standard=True)

    assert (summary["added"], summary["updated"]) == (0, 1)
    assert _csv_rows(summary) == []
    assert (summary["excluded_retired"], summary["refused"]) == (1, 0)
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "Contoso Freight, Inc." in err[0] and status in err[0], err
    assert "ines.vale" not in err[0], "the refusal names the account, not the person"
    (account,) = _ledger(tmp_path)
    assert account["status"] == status


def test_a_contact_at_an_account_the_researcher_dropped_is_never_exported(tmp_path, capsys):
    dropped = {**LEGAL_NAME, "verdict": "drop", "verdict_reason": "a competitor"}
    assert _finalize(tmp_path, [dropped], standard=True)["refused"] == 1

    summary = _finalize(tmp_path, [dict(THIN_VARIANT)], run="run-2", standard=True)

    assert _csv_rows(summary) == []
    assert (summary["excluded_retired"], summary["refused"]) == (1, 0)
    assert "drop" in capsys.readouterr().err
    assert _ledger(tmp_path)[0]["verdict"] == "drop"


def test_a_live_account_in_the_same_batch_is_still_exported(tmp_path):
    _finalize(tmp_path, [dict(LEGAL_NAME)], standard=True)
    ps.set_status(PROFILE, {"d:contosofreight.example": "do-not-contact"}, content_root=tmp_path)
    other = {
        "company": "Northwind Robotics",
        "domain": "northwind.example",
        "score": 8,
        "contact_name": "Avery Quill",
        "contact_email": "avery.quill@northwind.example",
    }
    summary = _finalize(tmp_path, [dict(THIN_VARIANT), other], run="run-2", standard=True)
    assert [r["Email"] for r in _csv_rows(summary)] == ["avery.quill@northwind.example"]
    assert summary["excluded_retired"] == 1


def test_two_companies_that_share_a_stem_are_two_accounts_and_two_honest_rows(tmp_path):
    holdings = {
        "company": "Zephyrine Holdings",
        "domain": "zephyrine-holdings.example",
        "score": 9,
        "contact_name": "Ada Quill",
        "contact_email": "ada.quill@zephyrine-holdings.example",
        "why_now": "raised a round",
        "verdict": "send",
    }
    group = {
        "company": "Zephyrine Group",
        "score": 6,
        "contact_name": "Bo Marsh",
        "contact_email": "bo.marsh@zephyrine-group.example",
        "why_now": "opened an office",
    }
    summary = _finalize(tmp_path, [holdings, group], standard=True)
    assert (summary["added"], summary["updated"]) == (2, 0)
    rows = {r["Company Name"]: r for r in _csv_rows(summary)}
    assert rows["Zephyrine Holdings"]["Email"] == "ada.quill@zephyrine-holdings.example"
    assert rows["Zephyrine Holdings"]["GTM_Why_Now"] == "raised a round"
    assert rows["Zephyrine Group"]["Company Domain Name"] == ""


# --- a field only the CSV reads is still read before anything is written --------------- #


def test_a_number_where_the_csv_wants_text_is_coerced_and_exported(tmp_path):
    item = {
        "company": "Quillon Labs",
        "domain": "quillon.example",
        "score": 9,
        "contact_email": "mina.ash@quillon.example",
        "employees_range": 500,
        "city": 7,
        "intent_topics": "agentic ai",
    }
    summary = _finalize(tmp_path, [item], standard=True)
    (row,) = _csv_rows(summary)
    assert row["Number of Employees"] == "500"
    assert (row["City"], row["GTM_Intent_Topics"]) == ("7", "agentic ai")
    (account,) = _ledger(tmp_path)
    assert account["employees_range"] == "500"
    assert account["intent_topics"] == [{"topic": "agentic ai"}]


def test_a_topic_to_score_mapping_is_read_as_scored_topics(tmp_path):
    item = {"company": "Quillon Labs", "score": 9, "intent_topics": {"mlops": 72, "agents": 86}}
    (row,) = _csv_rows(_finalize(tmp_path, [item], standard=True))
    assert row["GTM_Intent_Topics"] == "agents:86;mlops:72"


RIDER_GARBAGE = [
    pytest.param({"employees_range": [500]}, "row 2 .*employees_range", id="range-list"),
    pytest.param({"contact_email": {"to": "x"}}, "row 2 .*contact_email", id="email-dict"),
    pytest.param({"city": True}, "row 2 .*city", id="city-bool"),
    pytest.param({"employees_number": ["many"]}, "row 2 .*employees_number", id="number-list"),
    pytest.param({"intent_topics": 7}, "row 2 .*intent_topics", id="topics-number"),
    pytest.param({"intent_topics": [7]}, "row 2 .*intent_topics", id="topics-list-of-number"),
    pytest.param(
        {"intent_topics": [{"topic": "mlops", "score": "high"}]},
        "row 2 .*intent_topics",
        id="topic-score",
    ),
]


@pytest.mark.parametrize(("extra", "names"), RIDER_GARBAGE)
def test_garbage_in_a_csv_only_field_is_refused_before_the_ledger_is_touched(
    tmp_path, extra, names
):
    _finalize(tmp_path, [dict(RESEARCHED)], standard=True)
    before = _written(tmp_path), (tmp_path / PROFILE / "prospects" / "latest.json").read_bytes()
    bad = {"company": "Quillon Labs", "domain": "quillon.example", "score": 9, **extra}

    with pytest.raises(ValueError, match=names):
        _finalize(tmp_path, [dict(THIN_VARIANT), bad], run="run-2", standard=True)

    after = _written(tmp_path), (tmp_path / PROFILE / "prospects" / "latest.json").read_bytes()
    assert after == before


def test_a_row_the_csv_cannot_render_stops_the_run_before_the_ledger_write(tmp_path, monkeypatch):
    """The backstop behind the type checks: rows are built BEFORE the merge is committed, so
    a defect in the writer can no longer leave a ledger that ran ahead of its export."""
    _finalize(tmp_path, [dict(RESEARCHED)], standard=True)
    ledger = tmp_path / PROFILE / "prospects" / "latest.json"
    before = ledger.read_bytes()

    def broken(*_a, **_kw):
        raise AttributeError("'int' object has no attribute 'replace'")

    monkeypatch.setattr(pi, "_hubspot_row", broken)
    with pytest.raises(ValueError, match="row 1 .*Quillon Labs"):
        _finalize(tmp_path, [{"company": "Quillon Labs", "score": 9}], run="run-2", standard=True)
    assert ledger.read_bytes() == before
    assert not (ledger.parent / "prospects-run-2-hubspot.csv").exists()
