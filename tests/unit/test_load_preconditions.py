"""A5/A6 — `gtm_core.load_preconditions`: what must be on record before anyone is loaded.

The sequencer can be loaded three ways the engine controls: the approved dispatcher
(`agent/email_dispatch.py`), a Claude Code session calling a hosted connector, and an agent in
another harness. Each used to rely on whoever was driving to have run the compliance check and
to have written the history row. These are the rules the first two now share, read from
`history.jsonl` and `cells.toml` and nowhere else.

Every fixture is fictional (§R9): `example.test` addresses, made-up ids and campaign names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import load_preconditions as lp
from gtm_core.copy_words import COPY_DIGEST_ALGO, copy_digests

PROFILE = "acme"
SEQ = "seqAAAA1111"

COPY = [
    {"step_id": "st-1", "variants": [{"subject": "Quick question", "content": "<p>Hello</p>"}]},
    {"step_id": "st-2", "variants": [{"subject": "", "content": "<p>Following up</p>"}]},
]


def _write_history(root: Path, rows: list[dict | str]) -> Path:
    path = root / PROFILE / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def _pre(sequence_id=SEQ, status="PASS", **extra) -> dict:
    return {
        "event": "capability_asserted",
        "provider": "saleshandy",
        "sequence_id": sequence_id,
        "status": status,
        **extra,
    }


def _staged(sequence_id=SEQ, **extra) -> dict:
    return {"event": "sequence_staged", "sequence_id": sequence_id, **extra}


# ── reading the history: refuse, never skip ───────────────────────────────────


def test_a_missing_history_file_is_an_empty_history(tmp_path):
    assert lp.read_history(tmp_path, PROFILE) == []


def test_blank_lines_are_ignored(tmp_path):
    _write_history(tmp_path, [_pre(), "", "   "])
    assert len(lp.read_history(tmp_path, PROFILE)) == 1


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"a string"', "42"])
def test_a_line_that_is_not_a_json_object_refuses_the_whole_read(tmp_path, bad):
    _write_history(tmp_path, [_pre(), bad])
    with pytest.raises(lp.HistoryUnreadable):
        lp.read_history(tmp_path, PROFILE)


def test_non_utf8_bytes_refuse_the_read(tmp_path):
    path = _write_history(tmp_path, [_pre()])
    path.write_bytes(b'{"event": "x"}\n\xff\xfe\n')
    with pytest.raises(lp.HistoryUnreadable):
        lp.read_history(tmp_path, PROFILE)


@pytest.mark.parametrize("profile", ["", "../x", "a/b"])
def test_an_unsafe_profile_is_refused_before_any_path_is_built(tmp_path, profile):
    with pytest.raises(ValueError):
        lp.read_history(tmp_path, profile)


# ── the preflight rule: FAIL or absent refuses ────────────────────────────────


def test_no_recorded_check_refuses_in_plain_words():
    r = lp.preflight_refusal([], SEQ)
    assert r is not None
    text = r.render()
    assert text.startswith("I haven't loaded anyone.")
    assert SEQ in text and "compliance check" in text
    assert "That's because" in text and "You can" in text
    assert "--sequence-id" in (r.technical or "")


def test_a_check_recorded_for_another_sequence_does_not_count():
    assert lp.preflight_refusal([_pre("seqOTHER")], SEQ) is not None


def test_a_sequence_id_is_matched_exactly_not_by_prefix():
    assert lp.preflight_refusal([_pre("seqAAAA11112")], SEQ) is not None
    assert lp.preflight_refusal([_pre("seqAAAA1111")], SEQ) is None


@pytest.mark.parametrize("status", ["PASS", "WARN", "pass", " Warn "])
def test_a_passing_or_warning_check_lets_the_load_proceed(status):
    assert lp.preflight_refusal([_pre(status=status)], SEQ) is None


@pytest.mark.parametrize("status", ["FAIL", "fail", "", None, "MAYBE", "OK", 0, ["PASS"]])
def test_only_a_known_granting_word_grants(status):
    """Absence defaults to refuse: the granting words are a closed list, so a word nobody
    anticipated blocks instead of passing."""
    r = lp.preflight_refusal([_pre(status=status)], SEQ)
    assert r is not None


def test_a_row_with_no_status_key_at_all_refuses():
    row = _pre()
    del row["status"]
    assert lp.preflight_refusal([row], SEQ) is not None


def test_the_latest_check_wins_in_both_directions():
    assert lp.preflight_refusal([_pre(status="FAIL"), _pre(status="PASS")], SEQ) is None
    assert lp.preflight_refusal([_pre(status="PASS"), _pre(status="FAIL")], SEQ) is not None


def test_a_row_that_is_not_a_capability_assertion_never_counts():
    assert (
        lp.preflight_refusal(
            [{"event": "sequence_staged", "sequence_id": SEQ, "status": "PASS"}], SEQ
        )
        is not None
    )


def test_a_failed_wider_preflight_refuses_even_when_the_capability_part_passed():
    """The CLI says DO NOT LOAD when ANY check fails (address, opt-out, market). The row
    carries that verdict in `overall`; a PASS capability status must not outvote it."""
    rows = [_pre(status="PASS", overall="FAIL", failed_checks=["optout"])]
    r = lp.preflight_refusal(rows, SEQ)
    assert r is not None
    assert "optout" in r.render()


def test_a_failed_check_names_what_failed_when_it_is_recorded():
    r = lp.preflight_refusal(
        [_pre(status="FAIL", detail=["FAIL [BLOCKS] saleshandy/stop_on_reply: not read"])], SEQ
    )
    assert r is not None and "stop_on_reply" in r.render()


def test_an_empty_or_missing_sequence_id_never_matches_a_row_without_one():
    """A check run without --sequence-id is recorded with none; it binds to no sequence."""
    assert lp.preflight_refusal([_pre(sequence_id=None)], "") is not None
    assert lp.preflight_refusal([_pre(sequence_id=None)], SEQ) is not None


# ── resolving a step id to the sequence it belongs to (the hosted import has no sequence id) ──


def test_a_step_id_resolves_through_the_step_ids_a_check_recorded():
    rows = [_pre(step_ids=["st-1", "st-2"])]
    assert lp.sequences_for_step(rows, "st-2") == {SEQ}
    assert lp.sequences_for_step(rows, "st-9") == set()


def test_a_step_listed_under_two_sequences_is_ambiguous_not_guessed():
    rows = [_pre(SEQ, step_ids=["st-1"]), _pre("seqBBBB2222", step_ids=["st-1"])]
    assert lp.sequences_for_step(rows, "st-1") == {SEQ, "seqBBBB2222"}


def test_a_malformed_step_ids_value_resolves_nothing():
    assert lp.sequences_for_step([_pre(step_ids="st-1")], "st-1") == set()
    assert lp.sequences_for_step([_pre(step_ids=[1, None])], "st-1") == set()


# ── the staged-copy rule ──────────────────────────────────────────────────────


def _staged_with_digests(steps=COPY, **extra) -> dict:
    return _staged(step_sha256=copy_digests(steps), step_sha256_algo=COPY_DIGEST_ALGO, **extra)


def test_copy_that_matches_what_was_staged_proceeds():
    assert lp.staged_copy_refusal([_staged_with_digests()], SEQ, COPY) is None


def test_a_changed_step_after_staging_refuses_and_names_the_step():
    changed = json.loads(json.dumps(COPY))
    changed[1]["variants"][0]["content"] = "<p>Following up, with a new ask</p>"
    r = lp.staged_copy_refusal([_staged_with_digests()], SEQ, changed)
    assert r is not None
    assert "step 2" in r.render()
    assert r.render().startswith("I haven't loaded anyone.")


def test_a_different_number_of_steps_refuses():
    assert lp.staged_copy_refusal([_staged_with_digests()], SEQ, COPY[:1]) is not None


def test_html_rewrapping_is_not_a_change():
    rewrapped = json.loads(json.dumps(COPY))
    rewrapped[0]["variants"][0]["content"] = "<div> <p>Hello</p> </div>"
    assert lp.staged_copy_refusal([_staged_with_digests()], SEQ, rewrapped) is None


def test_the_latest_staging_row_is_the_one_compared():
    old = json.loads(json.dumps(COPY))
    old[0]["variants"][0]["content"] = "<p>An earlier draft</p>"
    rows = [_staged_with_digests(old), _staged_with_digests(COPY)]
    assert lp.staged_copy_refusal(rows, SEQ, COPY) is None
    assert lp.staged_copy_refusal(list(reversed(rows)), SEQ, COPY) is not None


def test_no_staging_row_or_no_digest_in_a_form_i_can_check_is_not_a_mismatch():
    """Digests written before this algorithm existed cannot be compared; refusing on them
    would stop every sequence staged the old way, so they are reported by the caller, not
    treated as a difference."""
    assert lp.staged_copy_refusal([], SEQ, COPY) is None
    assert lp.staged_copy_refusal([_staged()], SEQ, COPY) is None
    legacy = _staged(step_sha256=["c5029744044e63b8", "322dc5eb145b4f67"])
    assert lp.staged_copy_refusal([legacy], SEQ, COPY) is None
    other_algo = _staged(step_sha256=["0" * 16, "0" * 16], step_sha256_algo="someone-elses")
    assert lp.staged_copy_refusal([other_algo], SEQ, COPY) is None


@pytest.mark.parametrize("bad", ["abc", [1, 2], [], None, {"a": 1}, ["xx"] * 2])
def test_a_named_algorithm_with_an_unusable_list_refuses(bad):
    """If the row says it was written by THIS algorithm, a list that cannot be read is a
    broken record, not a missing one."""
    row = _staged(step_sha256=bad, step_sha256_algo=COPY_DIGEST_ALGO)
    assert lp.staged_copy_refusal([row], SEQ, COPY) is not None


def test_another_sequences_digests_are_not_compared():
    row = _staged_with_digests(sequence_id="seqOTHER")
    row["sequence_id"] = "seqOTHER"
    assert lp.staged_copy_refusal([row], SEQ, COPY) is None


def test_a_draft_whose_steps_cannot_be_digested_refuses_when_a_digest_is_on_record():
    r = lp.staged_copy_refusal([_staged_with_digests()], SEQ, [{"step_id": "x"}])
    assert r is not None


# ── the pilot rule ────────────────────────────────────────────────────────────


def _cells(root: Path, entries: list[dict], *, csvs: dict[str, list[str]] | None = None) -> None:
    seq_dir = root / PROFILE / "prospects" / "sequences"
    seq_dir.mkdir(parents=True, exist_ok=True)
    blocks = []
    for e in entries:
        lines = ["[[sequence]]"]
        for k, v in e.items():
            lines.append(f"{k} = {json.dumps(v)}")
        blocks.append("\n".join(lines))
    (seq_dir / "cells.toml").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    for name, emails in (csvs or {}).items():
        (seq_dir / name).write_text(
            "email,first\n" + "\n".join(f"{e},X" for e in emails) + "\n", encoding="utf-8"
        )


def _draft(n=2, **extra) -> dict:
    return {
        "tool": "import_prospects_to_sequence",
        "sequence_id": SEQ,
        "step_id": "st-1",
        "steps": COPY,
        "prospect_list": [{"Email": f"p{i}@example.test", "First Name": "P"} for i in range(n)],
        **extra,
    }


def test_no_pilot_field_anywhere_changes_nothing_and_reads_no_file(tmp_path):
    """The default is unchanged behaviour: with no pilot declared the rule must not even look
    for the files it would otherwise need (no cells.toml here, and none is required)."""
    assert lp.pilot_refusal(tmp_path, PROFILE, _draft(500), []) is None


def test_a_cells_file_with_no_pilot_field_changes_nothing(tmp_path):
    _cells(tmp_path, [{"id": SEQ, "csv": "a.csv", "spec": "spec-x.md", "campaign": "camp-1"}])
    assert lp.pilot_refusal(tmp_path, PROFILE, _draft(500), []) is None


def test_a_draft_inside_its_pilot_proceeds(tmp_path):
    assert (
        lp.pilot_refusal(tmp_path, PROFILE, _draft(3, pilot_size=3, campaign="camp-1"), []) is None
    )


def test_a_draft_over_its_pilot_refuses_until_the_read_is_recorded(tmp_path):
    draft = _draft(4, pilot_size=3, campaign="camp-1")
    r = lp.pilot_refusal(tmp_path, PROFILE, draft, [])
    assert r is not None
    text = r.render()
    assert text.startswith("I haven't loaded anyone.")
    assert "camp-1" in text and "3" in text and "4" in text
    ok = {"event": "pilot_read_ok", "campaign": "camp-1"}
    assert lp.pilot_refusal(tmp_path, PROFILE, draft, [ok]) is None


def test_a_read_recorded_for_another_campaign_does_not_release_this_one(tmp_path):
    draft = _draft(4, pilot_size=3, campaign="camp-1")
    other = {"event": "pilot_read_ok", "campaign": "camp-2"}
    assert lp.pilot_refusal(tmp_path, PROFILE, draft, [other]) is not None


def test_only_a_pilot_read_ok_event_releases_it(tmp_path):
    draft = _draft(4, pilot_size=3, campaign="camp-1")
    for event in ("pilot_read", "pilot_read_failed", "enrolled", ""):
        rows = [{"event": event, "campaign": "camp-1"}]
        assert lp.pilot_refusal(tmp_path, PROFILE, draft, rows) is not None, event


def test_the_pilot_size_can_live_in_cells_toml_and_the_campaign_total_is_a_union_not_a_sum(
    tmp_path,
):
    """3 already registered in the campaign's other list + this draft's own list (registered at
    card approval, overlapping on one person) = 4 distinct people, not 5."""
    _cells(
        tmp_path,
        [
            {
                "id": "seqBBBB2222",
                "csv": "b.csv",
                "spec": "s.md",
                "campaign": "camp-1",
                "pilot_size": 4,
            },
            {"id": SEQ, "csv": "a.csv", "spec": "s.md", "campaign": "camp-1"},
        ],
        csvs={
            "b.csv": ["p0@example.test", "x1@example.test", "x2@example.test"],
            "a.csv": ["p0@example.test", "p1@example.test"],
        },
    )
    # p0, p1, x1, x2: four distinct people, exactly the pilot.
    assert lp.pilot_refusal(tmp_path, PROFILE, _draft(2), []) is None
    # one more distinct person tips it over.
    assert lp.pilot_refusal(tmp_path, PROFILE, _draft(3), []) is not None


def test_a_draft_override_can_only_tighten_never_loosen_the_registered_pilot(tmp_path):
    _cells(
        tmp_path,
        [{"id": SEQ, "csv": "a.csv", "spec": "s.md", "campaign": "camp-1", "pilot_size": 2}],
        csvs={"a.csv": ["p0@example.test", "p1@example.test", "p2@example.test"]},
    )
    assert lp.pilot_refusal(tmp_path, PROFILE, _draft(3, pilot_size=50), []) is not None


def test_the_campaign_is_found_from_cells_toml_when_the_draft_names_none(tmp_path):
    _cells(
        tmp_path,
        [{"id": SEQ, "csv": "a.csv", "spec": "s.md", "campaign": "camp-1", "pilot_size": 1}],
        csvs={"a.csv": ["p0@example.test", "p1@example.test"]},
    )
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(2), [])
    assert r is not None and "camp-1" in r.render()


def test_the_campaign_is_found_from_the_staging_row_as_a_last_resort(tmp_path):
    rows = [_staged(campaign="camp-9")]
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(3, pilot_size=2), rows)
    assert r is not None and "camp-9" in r.render()


def test_with_no_campaign_anywhere_the_sequence_is_the_pilot_unit(tmp_path):
    draft = _draft(3, pilot_size=2)
    r = lp.pilot_refusal(tmp_path, PROFILE, draft, [])
    assert r is not None and SEQ in r.render()
    assert (
        lp.pilot_refusal(tmp_path, PROFILE, draft, [{"event": "pilot_read_ok", "sequence_id": SEQ}])
        is None
    )


def test_a_lead_id_draft_counts_its_lead_ids(tmp_path):
    draft = {
        "tool": "add_leads_to_sequence",
        "sequence_id": SEQ,
        "lead_ids": [1, 2, 3],
        "pilot_size": 2,
    }
    assert lp.pilot_refusal(tmp_path, PROFILE, draft, []) is not None
    draft["pilot_size"] = 3
    assert lp.pilot_refusal(tmp_path, PROFILE, draft, []) is None


@pytest.mark.parametrize("bad", [0, -1, "5", "x", True, 2.5, [], {}])
def test_a_pilot_size_that_is_not_a_positive_whole_number_refuses(tmp_path, bad):
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(1, pilot_size=bad), [])
    assert r is not None
    assert "pilot" in r.render()


def test_a_corrupt_cells_file_refuses_rather_than_reading_as_no_pilot(tmp_path):
    seq_dir = tmp_path / PROFILE / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)
    (seq_dir / "cells.toml").write_text("[[sequence]\nid = ", encoding="utf-8")
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(2), [])
    assert r is not None
    assert "cells" in r.render()


def test_a_registered_list_i_cannot_read_refuses_when_a_pilot_applies(tmp_path):
    _cells(
        tmp_path,
        [{"id": SEQ, "csv": "missing.csv", "spec": "s.md", "campaign": "camp-1", "pilot_size": 5}],
    )
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(1), [])
    assert r is not None
    assert "missing.csv" in r.render()


def test_a_traversal_shaped_list_name_is_refused_not_followed(tmp_path):
    _cells(
        tmp_path,
        [
            {
                "id": SEQ,
                "csv": "../../../etc/passwd",
                "spec": "s.md",
                "campaign": "c",
                "pilot_size": 5,
            }
        ],
    )
    r = lp.pilot_refusal(tmp_path, PROFILE, _draft(1), [])
    assert r is not None


# ── cells.toml keeps a pilot size across a re-registration ───────────────────


def test_register_sequence_preserves_and_accepts_pilot_size(tmp_path):
    from gtm_core import cells

    cells.register_sequence(
        PROFILE, SEQ, "a.csv", "s.md", campaign="camp-1", pilot_size=7, content_root=tmp_path
    )
    # a later re-registration (what send-cards apply does) must not drop it
    cells.register_sequence(PROFILE, SEQ, "a.csv", "s.md", title="t", content_root=tmp_path)
    text = cells.cells_map_path(PROFILE, tmp_path).read_text(encoding="utf-8")
    assert "pilot_size = 7" in text
    # and it round-trips through the strict reader the dispatcher uses
    entries = lp.read_cells(tmp_path, PROFILE)
    assert entries[0]["pilot_size"] == 7


def test_register_sequence_without_a_pilot_writes_the_same_bytes_as_before(tmp_path):
    from gtm_core import cells

    cells.register_sequence(PROFILE, SEQ, "a.csv", "s.md", campaign="c", content_root=tmp_path)
    text = cells.cells_map_path(PROFILE, tmp_path).read_text(encoding="utf-8")
    assert "pilot_size" not in text


# ── the single entry point the dispatcher calls ───────────────────────────────


def test_load_refusal_runs_the_rules_in_order_and_returns_the_first(tmp_path):
    draft = _draft(2)
    assert lp.load_refusal(PROFILE, tmp_path, draft).render().startswith("I haven't loaded anyone.")
    _write_history(tmp_path, [_pre(status="FAIL")])
    assert "failed" in lp.load_refusal(PROFILE, tmp_path, draft).render()
    _write_history(tmp_path, [_pre(status="PASS"), _staged_with_digests()])
    assert lp.load_refusal(PROFILE, tmp_path, draft) is None


def test_load_refusal_refuses_on_an_unreadable_history(tmp_path):
    _write_history(tmp_path, [_pre(), "{broken"])
    r = lp.load_refusal(PROFILE, tmp_path, _draft(2))
    assert r is not None
    assert "history" in r.render()


def test_load_refusal_refuses_when_there_is_no_profile(tmp_path):
    assert lp.load_refusal("", tmp_path, _draft(2)) is not None


def test_load_refusal_checks_the_pilot_after_the_preflight(tmp_path):
    _write_history(tmp_path, [_pre()])
    r = lp.load_refusal(PROFILE, tmp_path, _draft(4, pilot_size=3, campaign="camp-1"))
    assert r is not None and "pilot" in r.render()
    _write_history(tmp_path, [_pre(), {"event": "pilot_read_ok", "campaign": "camp-1"}])
    assert lp.load_refusal(PROFILE, tmp_path, _draft(4, pilot_size=3, campaign="camp-1")) is None


def test_a_draft_without_a_sequence_id_refuses(tmp_path):
    draft = _draft(2)
    del draft["sequence_id"]
    assert lp.load_refusal(PROFILE, tmp_path, draft) is not None


# ── the module is importable by a hook's interpreter ──────────────────────────


def test_the_module_imports_on_the_system_python_a_hook_may_use():
    """`/usr/bin/python3` on macOS is 3.9. The guard that reads history for a Claude Code hook
    runs under whatever interpreter it finds, so the read path must not need 3.11+ at import
    (tomllib is imported inside the one function that reads cells.toml)."""
    import shutil
    import subprocess

    py = shutil.which("python3.9") or "/usr/bin/python3"
    if not Path(py).exists():
        pytest.skip("no older system python to prove it on")
    version = subprocess.run(
        [py, "-c", "import sys; print(sys.version_info[:2] < (3, 11))"],
        capture_output=True,
        text=True,
        check=False,
    )
    if version.stdout.strip() != "True":
        pytest.skip("the system python here is new enough that this proves nothing")
    repo = Path(lp.__file__).resolve().parents[1]
    proc = subprocess.run(  # noqa: S603
        [py, "-c", "import gtm_core.load_preconditions as m; print(m.GRANTING_STATUSES)"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
        env={"PYTHONPATH": str(repo), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
