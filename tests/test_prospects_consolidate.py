"""Tests for gtm_core.prospects_consolidate — the raw-export sweep + deliverability gate."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import prospects_consolidate as pc


def _write_csv(path, header, rows):
    """Write a source export fixture.

    A row with no first name cannot render "Hi <first>," and is held back by the
    merge-field gate, so a fixture that omits the column would fail every downstream
    ready-to-load assertion for a reason unrelated to what it is testing. Supply a
    placeholder name when the fixture doesn't care about names; tests that DO exercise
    name handling pass their own "First Name" column and are left alone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not any(h.lower() in ("first name", "first", "contact name") for h in header):
        header = ["First Name", *header]
        rows = [["Dana", *r] for r in rows]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _prospects_dir(root, profile):
    return root / profile / "prospects"


def test_classify_confidence_tiers():
    assert pc.classify_confidence("RocketReach A", "") == "high"
    assert pc.classify_confidence("RocketReach A-", "") == "high"
    assert pc.classify_confidence("verified", "") == "high"
    assert pc.classify_confidence("account-folder-verified", "") == "high"
    assert pc.classify_confidence("RocketReach B", "") == "medium"
    assert pc.classify_confidence("found", "") == "medium"
    assert pc.classify_confidence("", "high") == "medium"
    assert pc.classify_confidence("RocketReach F(pattern)", "") == "blocked"
    assert pc.classify_confidence("", "invalid") == "blocked"
    assert pc.classify_confidence("", "") == "unknown"


def test_classify_confidence_apollo_tiers():
    """Regression: an Apollo-sourced row must NOT silently fall to 'unknown' (the
    hidden hold queue).

    Apollo's `email_status` / `contact_email_status` vocabulary is exactly four values —
    `verified`, `unverified`, `likely to engage`, `unavailable` (docs.apollo.io People
    API Search, verified 2026-07-27). The first version of this mapping guessed the
    labels ("apollo likely", "apollo unavailable"), strings Apollo never emits, so THREE
    of the four real statuses still fell through to `unknown`. Assert the real values.
    """
    # verified → high
    assert pc.classify_confidence("verified", "") == "high"
    assert pc.classify_confidence("Apollo Verified", "") == "high"
    # likely to engage → medium (a real, usable email; not verification-grade)
    assert pc.classify_confidence("likely to engage", "") == "medium"
    assert pc.classify_confidence("Likely To Engage", "") == "medium"
    # unverified → medium, NOT high and NOT unknown
    assert pc.classify_confidence("unverified", "") == "medium"
    # unavailable → blocked
    assert pc.classify_confidence("unavailable", "") == "blocked"
    # "unverified" must never be swept up by the `verified` high rule.
    assert pc.classify_confidence("unverified", "") != "high"


def test_alias_mapping_handles_schema_variants(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)

    # GTM_* schema, "Email" column
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        [
            "First Name",
            "Last Name",
            "Email",
            "Job Title",
            "Company Name",
            "Company Domain Name",
            "GTM_Tier",
            "GTM_Score",
            "Email Status",
        ],
        [
            [
                "Ada",
                "Lovelace",
                "ada@analytical.com",
                "CTO",
                "Analytical Engine",
                "analytical.com",
                "A",
                "9",
                "RocketReach A",
            ]
        ],
    )
    # GTM_* prefixed schema, "Contact Email" column, no verification signal at all
    _write_csv(
        pdir / "prospects-20260102-b-hubspot.csv",
        ["Contact Name", "Contact Title", "Contact Email", "Company", "GTM_Tier", "GTM_Score"],
        [["Grace Hopper", "Rear Admiral", "grace@compiler.com", "Compiler Inc", "A", "8"]],
    )

    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["net_new_folded"] == 2

    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    by_email = {r["email"]: r for r in master}
    assert by_email["ada@analytical.com"]["first"] == "Ada"
    assert by_email["ada@analytical.com"]["conf_tier"] == "high"
    assert by_email["grace@compiler.com"]["first"] == "Grace"
    assert by_email["grace@compiler.com"]["last"] == "Hopper"
    assert by_email["grace@compiler.com"]["conf_tier"] == "unknown"


def test_heat_and_intent_fields_carried_from_hubspot_to_master(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        [
            "First Name",
            "Last Name",
            "Email",
            "Company Name",
            "GTM_Tier",
            "GTM_Score",
            "GTM_Heat",
            "GTM_Top_Intent_Score",
            "GTM_Intent_Topics",
            "GTM_Persona_Tier",
            "GTM_Qualification_Path",
        ],
        [
            [
                "Ada",
                "Lovelace",
                "ada@analytical.com",
                "Analytical Engine",
                "A",
                "9",
                "2",
                "94",
                "autonomous ai:94;agentic ai:85",
                "fs-capital-markets-lending",
                "intent-only-relaxed",
            ]
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)
    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    rec = master[0]
    assert rec["heat"] == "2"
    assert rec["top_intent_score"] == "94"
    assert rec["intent_topics"] == "autonomous ai:94;agentic ai:85"
    assert rec["cohort"] == "fs-capital-markets-lending"
    assert rec["qualification_path"] == "intent-only-relaxed"


def test_bulk_mode_header_shape_also_carries_heat(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    # Bulk-mode export shape: unprefixed headers, no "GTM_" prefix.
    _write_csv(
        pdir / "prospects-20260101-bulk-hubspot.csv",
        ["Email", "Company Name", "GTM_Tier", "GTM_Score", "Heat", "Cohort", "Qualification Path"],
        [
            [
                "bulk@x.com",
                "X Corp",
                "A",
                "53",
                "2",
                "healthcare-life-sciences",
                "intent-only-relaxed",
            ]
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)
    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    rec = master[0]
    assert rec["heat"] == "2"
    assert rec["cohort"] == "healthcare-life-sciences"
    assert rec["qualification_path"] == "intent-only-relaxed"


def test_old_master_list_without_new_columns_still_loads(tmp_path):
    # A legacy 17-column master-list.csv (no heat/intent/cohort columns at all).
    legacy_cols = [
        "first",
        "last",
        "email",
        "title",
        "company",
        "company_domain",
        "city",
        "country",
        "segment",
        "tier",
        "score",
        "conf",
        "email_status",
        "why_now",
        "case_study",
        "src",
        "conf_tier",
    ]
    path = tmp_path / "master-list.csv"
    _write_csv(
        path,
        legacy_cols,
        [
            [
                "Ada",
                "Lovelace",
                "ada@x.com",
                "CTO",
                "X",
                "x.com",
                "",
                "",
                "",
                "A",
                "9",
                "",
                "Verified",
                "",
                "",
                "old.csv",
                "high",
            ]
        ],
    )
    master = pc._load_master(path)
    assert len(master) == 1
    rec = master[0]
    assert rec["email"] == "ada@x.com"
    assert rec["heat"] == ""
    assert rec["top_intent_score"] == ""
    assert rec["intent_topics"] == ""
    assert rec["cohort"] == ""
    assert rec["qualification_path"] == ""


def test_dedupe_keeps_highest_score_on_collision(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    header = ["Email", "Company Name", "GTM_Score"]
    _write_csv(pdir / "prospects-20260101-a-hubspot.csv", header, [["dup@x.com", "X Corp", "4"]])
    _write_csv(pdir / "prospects-20260102-b-hubspot.csv", header, [["dup@x.com", "X Corp", "9"]])

    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["net_new_folded"] == 1
    assert result["net_new_dups_collapsed"] == 1
    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    assert len(master) == 1
    assert master[0]["score"] == "9"


def test_already_present_row_keeps_stale_conf_tier_by_default(tmp_path):
    """Without --reclassify, a corrected source export (Email Status added after the
    fact) never updates a row that's already in the master list — this is the bug
    reproduced on the vtr profile 2026-07-26."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name"],
        [["stale@x.com", "X Corp"]],  # no Email Status column at all -> unknown
    )
    pc.consolidate(profile, content_root=tmp_path)

    # operator corrects the same export in place, adding a verified status
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["stale@x.com", "X Corp", "verified"]],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["already_in_master_skipped"] == 1
    assert result["reclassified_from_source"] == 0
    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    assert master[0]["conf_tier"] == "unknown"
    assert result["ready_to_load"] == 0


def test_reclassify_upgrades_already_present_row_from_newer_source(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name"],
        [["stale@x.com", "X Corp"]],
    )
    pc.consolidate(profile, content_root=tmp_path)

    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["stale@x.com", "X Corp", "verified"]],
    )
    result = pc.consolidate(profile, content_root=tmp_path, reclassify=True)
    assert result["reclassified_from_source"] == 1
    assert result["ready_to_load"] == 1

    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    assert master[0]["conf_tier"] == "high"
    assert master[0]["email_status"] == "verified"


def test_reclassify_never_downgrades_without_explicit_flag(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["good@x.com", "X Corp", "verified"]],
    )
    pc.consolidate(profile, content_root=tmp_path)

    # a later, worse export for the same contact (no verification signal)
    _write_csv(
        pdir / "prospects-20260215-b-hubspot.csv",
        ["Email", "Company Name"],
        [["good@x.com", "X Corp"]],
    )
    result = pc.consolidate(profile, content_root=tmp_path, reclassify=True)
    assert result["reclassified_from_source"] == 0
    master = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    assert master[0]["conf_tier"] == "high"

    result_down = pc.consolidate(
        profile, content_root=tmp_path, reclassify=True, allow_downgrade=True
    )
    # already unknown->unknown vs high->unknown: with allow_downgrade the weaker
    # signal from the newer export is applied
    assert result_down["reclassified_from_source"] == 1
    master_after = pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    assert master_after[0]["conf_tier"] == "unknown"


def test_rebuild_master_recomputes_conf_tier_from_stored_fields(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    master_path = pdir / "sequences" / ".pool" / "master-list.csv"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with master_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pc.MASTER_COLS)
        w.writeheader()
        row = dict.fromkeys(pc.MASTER_COLS, "")
        # first/company are required to clear the merge-field gate and reach ready-to-load.
        row.update(
            {
                "email": "fixed@x.com",
                "first": "Dana",
                "company": "X",
                "email_status": "verified",
                "conf_tier": "unknown",
            }
        )
        w.writerow(row)

    result = pc.consolidate(profile, content_root=tmp_path, rebuild_master=True)
    assert result["rebuilt_master_tier_changes"] == 1
    master = pc._load_master(master_path)
    assert master[0]["conf_tier"] == "high"
    assert result["ready_to_load"] == 1


def test_merge_field_defects_are_repaired_on_ingestion(tmp_path):
    """A scraped headline or a glued honorific is fixed before it reaches the load file."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Email Status"],
        [
            ["Dr.rani", "Sharma", "rani@brightpath.example", "Talently", "RocketReach A"],
            [
                "Arjun",
                "Mehta",
                "arjun@devtrial.example",
                "DevTrial | We Build Tests",
                "RocketReach A",
            ],
            ["Anthony", "Brooke", "a@medipath.example", "MediPath, Inc.", "RocketReach A"],
        ],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["ready_to_load"] == 3
    assert result["merge_field_excluded"] == 0

    ready = {r["email"]: r for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready["rani@brightpath.example"]["first"] == "Rani"
    assert ready["arjun@devtrial.example"]["company"] == "DevTrial"
    assert ready["a@medipath.example"]["company"] == "MediPath"


def test_unrepairable_merge_field_is_held_back_and_logged(tmp_path):
    """An emoji with no recoverable name never reaches ready-to-load — and is auditable."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Email Status"],
        [
            ["Chris", "Renner", "ok@cascade.example", "Cascade", "RocketReach A"],
            ["\U0001f366", "\U0001f366", "bad@x.com", "X", "RocketReach A"],
        ],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["merge_field_excluded"] == 1
    assert result["ready_to_load"] == 1

    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready == {"ok@cascade.example"}

    # Held back, never silently dropped: still in the master list, and logged with a reason.
    master = {r["email"] for r in pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")}
    assert "bad@x.com" in master
    log = (pdir / "sequences" / ".pool" / ".blocked-log.jsonl").read_text(encoding="utf-8")
    assert "merge-field: first-name-empty" in log


def test_master_list_rows_are_repaired_retroactively_on_the_next_sweep(tmp_path):
    """Rows folded in before the gate existed get cleaned on load, not left dirty forever."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    master_path = pdir / "sequences" / ".pool" / "master-list.csv"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with master_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pc.MASTER_COLS)
        w.writeheader()
        row = dict.fromkeys(pc.MASTER_COLS, "")
        row.update(
            {
                "email": "stale@x.com",
                "first": "Dr.rani",
                "last": "Sharma",
                "company": "Canopus GBS | SAP Consulting |",
                "email_status": "RocketReach A",
                "conf_tier": "high",
            }
        )
        w.writerow(row)

    pc.consolidate(profile, content_root=tmp_path)
    ready = pc._load_master(pdir / "sequences" / "ready-to-load.csv")
    assert ready[0]["first"] == "Rani"
    assert ready[0]["company"] == "Canopus GBS"


def test_split_by_signal_separates_usable_triggers_from_the_rest(tmp_path):
    """One list cannot serve both populations: a blank merge tag ships broken copy."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "GTM_Why_Now"],
        [
            ["Ann", "a@x.com", "Rain", "RocketReach A", "Agent Control Layer launch (2026-06-09)"],
            # An intent-topic score is a targeting input, not an event to quote back.
            ["Ben", "b@y.com", "Yco", "RocketReach A", "machine learning (intent score 81)"],
            # Research recording that NO signal was found must never open an email.
            ["Cara", "c@z.com", "Zco", "RocketReach A", "No dated funding round confirmed"],
            ["Dan", "d@w.com", "Wco", "RocketReach A", ""],
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)
    result = pc.split_by_signal(profile, content_root=tmp_path)

    assert result["why_now_populated"] == 3
    assert result["signal_led"] == 1
    assert result["generic"] == 3

    seq = pdir / "sequences"
    signal = pc._load_master(seq / "ready-to-load-signal.csv")
    assert [r["email"] for r in signal] == ["a@x.com"]
    with (seq / "ready-to-load-signal.csv").open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["signal_clause"] == "Agent Control Layer launch (2026-06-09)"

    generic = {r["email"] for r in pc._load_master(seq / "ready-to-load-generic.csv")}
    assert generic == {"b@y.com", "c@z.com", "d@w.com"}


def test_split_by_signal_totals_always_reconcile(tmp_path):
    """Every ready row lands in exactly one of the two lists — none silently dropped."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "GTM_Why_Now"],
        [
            ["Ann", "a@x.com", "Rain", "RocketReach A", "Agent Control Layer launch (2026-06-09)"],
            ["Ben", "b@y.com", "Yco", "RocketReach A", ""],
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)
    result = pc.split_by_signal(profile, content_root=tmp_path)
    assert result["signal_led"] + result["generic"] == result["ready_total"]


def test_dnc_and_sent_are_excluded_from_loadable_but_kept_in_master(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [
            ["ok@x.com", "X Corp", "RocketReach A"],
            ["blocklisted@x.com", "Y Corp", "RocketReach A"],
        ],
    )
    dnc_file = tmp_path / "dnc.json"
    dnc_file.write_text(json.dumps(["blocklisted@x.com"]), encoding="utf-8")

    result = pc.consolidate(profile, content_root=tmp_path, dnc_file=dnc_file)
    assert result["dnc_hits_blocked"] == 1
    assert result["ready_to_load"] == 1  # only ok@x.com

    ready = pc._load_master(pdir / "sequences" / "ready-to-load.csv")
    ready_emails = {r["email"] for r in ready}
    assert ready_emails == {"ok@x.com"}
    # DNC'd contact is never even folded into the master (never re-emitted for
    # a future contact attempt), matching the Saleshandy DNC semantics.
    master_emails = {
        r["email"] for r in pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")
    }
    assert "blocklisted@x.com" not in master_emails


def test_blocked_confidence_excluded_from_ready_and_logged(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["bad@x.com", "Bad Corp", "RocketReach F(pattern)"]],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["blocked_excluded"] == 1
    assert result["ready_to_load"] == 0
    assert result["needs_verification"] == 0

    log_path = pdir / "sequences" / ".pool" / ".blocked-log.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["email"] == "bad@x.com"


def test_default_gate_only_high_confidence_is_ready_to_load(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [
            ["verified@x.com", "V Corp", "RocketReach A"],
            ["maybe@x.com", "M Corp", "RocketReach B"],
            ["unknown@x.com", "U Corp", ""],
        ],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["ready_to_load"] == 1
    assert result["needs_verification"] == 2  # medium + unknown both held back

    ready_emails = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready_emails == {"verified@x.com"}


def test_same_person_two_email_formats_collapses_in_loadable(tmp_path):
    """Robin Kraft resolved as both robin.kraft@ and rkraft@ (same domain) is ONE
    human — the loadable list must not enroll him twice, even though the master
    audit keeps both addresses."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Company Domain Name", "Email Status"],
        [
            [
                "Robin",
                "Kraft",
                "robin.kraft@nimbus.example",
                "Nimbus Labs",
                "nimbus.example",
                "RocketReach A",
            ],
            [
                "Robin",
                "Kraft",
                "rkraft@nimbus.example",
                "Nimbus Labs",
                "nimbus.example",
                "RocketReach A",
            ],
        ],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["person_dups_collapsed"] == 1
    assert result["ready_to_load"] == 1  # one human, not two
    assert result["master_total"] == 2  # audit keeps both addresses


def test_person_already_sent_under_different_address_is_excluded(tmp_path):
    """Sam Ortega sent as sam@vertex.example must not be re-contacted as
    sortega@vertex.example — email-only DNC misses this; the person key catches it."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "sequences" / "sent-pack.csv",
        ["status", "first", "last", "email", "company"],
        [["SENT", "Sam", "Ortega", "sam@vertex.example", "Vertex"]],
    )
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Company Domain Name", "Email Status"],
        [["Sam", "Ortega", "sortega@vertex.example", "Vertex", "vertex.example", "RocketReach A"]],
    )
    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["sent_person_excluded"] == 1
    assert result["ready_to_load"] == 0
    ready = pc._load_master(pc.ready_to_load_path(profile, content_root=tmp_path))
    assert ready == []


def test_verify_batch_shapes_hold_queue_for_import(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "GTM_Score"],
        [
            ["Lo", "Score", "lo@x.com", "X Corp", "4"],
            ["Hi", "Score", "hi@y.com", "Y Corp", "9"],
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)
    batch = pc.next_verification_batch(profile, limit=1, content_root=tmp_path)
    assert len(batch) == 1
    assert batch[0]["Email"] == "hi@y.com"  # highest score first
    assert batch[0]["First Name"] == "Hi"


def test_idempotent_rerun_with_no_new_exports(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name"],
        [["a@x.com", "A Corp"]],
    )

    first = pc.consolidate(profile, content_root=tmp_path)
    second = pc.consolidate(profile, content_root=tmp_path)
    assert first["net_new_folded"] == 1
    assert second["net_new_folded"] == 0
    assert second["master_total"] == first["master_total"]


def test_mid_run_interruption_new_export_folds_in_on_next_sweep(tmp_path):
    """Simulates the actual failure mode this module exists to fix: a run gets
    interrupted after writing its export CSV but before manual consolidation.
    A later, independent sweep must still pick it up."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name"],
        [["a@x.com", "A Corp"]],
    )
    pc.consolidate(profile, content_root=tmp_path)

    # simulate an interrupted second run: only the raw export lands, no consolidation call
    _write_csv(
        pdir / "prospects-20260215-b-hubspot.csv",
        ["Email", "Company Name"],
        [["b@x.com", "B Corp"]],
    )

    status = pc.pool_status(profile, content_root=tmp_path)
    assert status["unconsolidated_in_raw_exports"] == 1

    result = pc.consolidate(profile, content_root=tmp_path)
    assert result["net_new_folded"] == 1
    status_after = pc.pool_status(profile, content_root=tmp_path)
    assert status_after["unconsolidated_in_raw_exports"] == 0


def test_pool_status_ignores_non_email_placeholder_values(tmp_path):
    """A source export with a literal placeholder string (e.g. "unverified") in
    its email column must not be miscounted as a real unconsolidated address."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Contact Email", "Company"],
        [["unverified", "No Email Corp"], ["real@x.com", "Real Corp"]],
    )
    pc.consolidate(profile, content_root=tmp_path)
    status = pc.pool_status(profile, content_root=tmp_path)
    assert status["unconsolidated_in_raw_exports"] == 0


# --------------------------------------------------------------------------- suppression gate
#
# DNC suppression is a COMPLIANCE control, not deliverability hygiene: an opted-out
# address that reaches a sequencer is the one mistake this pipeline treats as
# unrecoverable (docs/email-compliance.md). The provider only
# enforces its own DNC at *send* time, so these tests pin the checks we do upstream —
# at fold time, at export time, and again at drain time.


def _dnc_cache(path, emails=(), domains=(), fetched_at=None):
    """Write a suppression cache in the current (dict) shape."""
    if fetched_at is None:
        fetched_at = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"emails": list(emails), "domains": list(domains), "fetched_at": fetched_at}),
        encoding="utf-8",
    )
    return path


def test_t1_suppressed_address_reaches_no_loadable_file_and_is_counted(tmp_path):
    """T1 — a DNC'd address must land in neither loadable file, and be counted rather
    than silently vanishing."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [
            ["keep@x.com", "X Corp", "RocketReach A"],
            ["optedout@y.com", "Y Corp", "RocketReach A"],  # would be ready-tier
            ["optedout2@z.com", "Z Corp", "found"],  # would be hold-queue tier
        ],
    )
    dnc = _dnc_cache(tmp_path / "dnc.json", emails=["optedout@y.com", "optedout2@z.com"])

    result = pc.consolidate(profile, content_root=tmp_path, dnc_file=dnc)

    assert result["dnc_hits_blocked"] == 2
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    hold = {
        r["email"] for r in pc._load_master(pdir / "sequences" / ".pool" / "needs-verification.csv")
    }
    assert ready == {"keep@x.com"}
    assert "optedout@y.com" not in ready and "optedout@y.com" not in hold
    assert "optedout2@z.com" not in ready and "optedout2@z.com" not in hold


def test_t2_missing_cache_hard_fails_under_require_dnc(tmp_path):
    """T2 — the fail-open is closed. Consolidating with no suppression list produces
    output indistinguishable from a correct run, so it must be unreachable."""
    profile = "acme"
    _write_csv(
        _prospects_dir(tmp_path, profile) / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["a@x.com", "X Corp", "RocketReach A"]],
    )
    missing = tmp_path / "nope.json"

    with pytest.raises(ValueError, match="missing"):
        pc.consolidate(profile, content_root=tmp_path, dnc_file=missing, require_dnc=True)

    # ...and the permissive default still works, for interactive use.
    assert pc.consolidate(profile, content_root=tmp_path, dnc_file=missing)["ready_to_load"] == 1


def test_t3_stale_or_undatable_cache_hard_fails_under_require_dnc(tmp_path):
    """T3 — a cache too old to trust, or one that cannot be dated at all, is refused."""
    profile = "acme"
    _write_csv(
        _prospects_dir(tmp_path, profile) / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["a@x.com", "X Corp", "RocketReach A"]],
    )

    stale = _dnc_cache(
        tmp_path / "stale.json",
        emails=["x@y.com"],
        fetched_at=(datetime.now(UTC) - timedelta(hours=72)).isoformat(),
    )
    with pytest.raises(ValueError, match="stale"):
        pc.consolidate(profile, content_root=tmp_path, dnc_file=stale, require_dnc=True)

    # A legacy bare-list cache carries no timestamp — trustworthy for interactive use,
    # never for an unattended run.
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(["x@y.com"]), encoding="utf-8")
    with pytest.raises(ValueError, match="fetched_at"):
        pc.consolidate(profile, content_root=tmp_path, dnc_file=legacy, require_dnc=True)

    empty = _dnc_cache(tmp_path / "empty.json")
    with pytest.raises(ValueError, match="empty"):
        pc.consolidate(profile, content_root=tmp_path, dnc_file=empty, require_dnc=True)


def test_t4_domain_entries_suppress_every_address_at_that_domain(tmp_path):
    """T4 — Saleshandy DNC entries may be domain-typed. There are none today, so this
    must be proven correct BEFORE the first one appears in production."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Company Domain Name", "Email Status"],
        [
            ["ceo@blocked.com", "Blocked Co", "blocked.com", "RocketReach A"],
            ["cto@blocked.com", "Blocked Co", "blocked.com", "RocketReach A"],
            # Suppressed via the row's company_domain even though the mail domain differs
            ["exec@mail.other.com", "Blocked Co", "www.blocked.com", "RocketReach A"],
            ["fine@allowed.com", "Allowed Co", "allowed.com", "RocketReach A"],
        ],
    )
    dnc = _dnc_cache(tmp_path / "dnc.json", domains=["blocked.com"])

    result = pc.consolidate(profile, content_root=tmp_path, dnc_file=dnc)

    assert result["dnc_hits_blocked"] == 3
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready == {"fine@allowed.com"}


def test_t6_verify_batch_rechecks_suppression_before_import(tmp_path):
    """T6 — the hold queue on disk is a snapshot. Someone can opt out between the sweep
    that wrote it and the drain that imports it, so the batch is re-checked. This is the
    last point before a suppressed address is handed to a third party."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Email Status"],
        [
            ["Ada", "Lovelace", "ada@x.com", "X Corp", "found"],
            ["Alan", "Turing", "alan@y.com", "Y Corp", "found"],
        ],
    )
    empty_dnc = _dnc_cache(tmp_path / "d0.json", emails=["nobody@nowhere.com"])
    pc.consolidate(profile, content_root=tmp_path, dnc_file=empty_dnc)
    assert len(pc.next_verification_batch(profile, content_root=tmp_path, dnc_file=empty_dnc)) == 2

    # Alan opts out AFTER the sweep — the stale hold-queue file still lists him.
    later = _dnc_cache(tmp_path / "d1.json", emails=["alan@y.com"])
    batch = pc.next_verification_batch(profile, content_root=tmp_path, dnc_file=later)
    assert [r["Email"] for r in batch] == ["ada@x.com"]

    with pytest.raises(ValueError, match="missing"):
        pc.next_verification_batch(
            profile, content_root=tmp_path, dnc_file=tmp_path / "gone.json", require_dnc=True
        )


def test_t7_canary_is_filtered_end_to_end(tmp_path):
    """T7 — the durable control. A known canary kept on the live DNC list is seeded into
    a fixture export; if it ever reaches a loadable file or a drain batch, suppression
    has silently regressed. T1-T6 prove it works once; this proves it still works later."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    canary = "dnc-canary@example.invalid"
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Email Status"],
        [
            ["Canary", "Bird", canary, "Canary Corp", "RocketReach A"],
            ["Real", "Person", "real@x.com", "X Corp", "found"],
        ],
    )
    dnc = _dnc_cache(tmp_path / "dnc.json", emails=[canary])

    pc.consolidate(profile, content_root=tmp_path, dnc_file=dnc, require_dnc=True)

    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    hold = {
        r["email"] for r in pc._load_master(pdir / "sequences" / ".pool" / "needs-verification.csv")
    }
    batch = pc.next_verification_batch(profile, content_root=tmp_path, dnc_file=dnc)
    assert canary not in ready
    assert canary not in hold
    assert canary not in {r["Email"] for r in batch}


def test_t8_suppressed_address_is_never_re_exported(tmp_path):
    """T8 — CAN-SPAM: once someone opts out you may not sell or transfer their address
    "even in the form of a mailing list". A row folded into the master BEFORE the
    suppression existed must drop out of the loadable exports on the next sweep."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Email Status"],
        [["later@x.com", "X Corp", "RocketReach A"]],
    )
    before = _dnc_cache(tmp_path / "d0.json", emails=["unrelated@q.com"])
    first = pc.consolidate(profile, content_root=tmp_path, dnc_file=before)
    assert first["ready_to_load"] == 1  # already in the master list

    # They opt out. The address is still in master-list.csv (audit trail) but must not
    # survive into any file we would hand to a sequencer.
    after = _dnc_cache(tmp_path / "d1.json", emails=["later@x.com"])
    second = pc.consolidate(profile, content_root=tmp_path, dnc_file=after)

    assert second["ready_to_load"] == 0
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert "later@x.com" not in ready


# --------------------------------------------------------------------------- market gate


def _market_export(tmp_path, profile="acme"):
    """Three high-confidence rows: one US, one UK, one with no country."""
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Email", "Company Name", "Country/Region", "Email Status"],
        [
            ["Ann", "us@x.com", "X Corp", "United States", "RocketReach A"],
            ["Ben", "uk@y.com", "Y Ltd", "United Kingdom", "RocketReach A"],
            ["Cara", "unknown@z.com", "Z Inc", "", "RocketReach A"],
        ],
    )
    return pdir


def test_out_of_market_lead_never_reaches_ready_to_load(tmp_path):
    pdir = _market_export(tmp_path)
    result = pc.consolidate(
        "acme", content_root=tmp_path, target_markets=["United States", "Singapore"]
    )
    assert result["out_of_market_excluded"] == 1
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready == {"us@x.com", "unknown@z.com"}


def test_out_of_market_lead_is_kept_in_the_master_list(tmp_path):
    """A load gate, not a delete — the row survives so a market change can re-admit it."""
    pdir = _market_export(tmp_path)
    pc.consolidate("acme", content_root=tmp_path, target_markets=["United States"])
    master = {r["email"] for r in pc._load_master(pdir / "sequences" / ".pool" / "master-list.csv")}
    assert "uk@y.com" in master


def test_unknown_country_is_counted_but_loadable_by_default(tmp_path):
    _market_export(tmp_path)
    result = pc.consolidate("acme", content_root=tmp_path, target_markets=["United States"])
    assert result["unknown_country"] == 1
    assert result["out_of_market_excluded"] == 1  # the UK row only


def test_strict_market_also_drops_unknown_country(tmp_path):
    pdir = _market_export(tmp_path)
    result = pc.consolidate(
        "acme", content_root=tmp_path, target_markets=["United States"], strict_market=True
    )
    assert result["out_of_market_excluded"] == 2  # UK + unknown
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert ready == {"us@x.com"}


def test_market_aliases_and_annotations_are_normalized(tmp_path):
    pdir = _prospects_dir(tmp_path, "acme")
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Country/Region", "Email Status"],
        [["a@x.com", "X", "USA", "RocketReach A"], ["b@y.com", "Y", "SG", "RocketReach A"]],
    )
    result = pc.consolidate(
        "acme", content_root=tmp_path, target_markets=["United States (primary)", "Singapore"]
    )
    assert result["out_of_market_excluded"] == 0
    assert result["ready_to_load"] == 2


def test_out_of_market_row_is_dropped_from_the_hold_queue_too(tmp_path):
    """Verifying an out-of-market lead would push their PII to the sequencer for nothing."""
    pdir = _prospects_dir(tmp_path, "acme")
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["Email", "Company Name", "Country/Region", "Email Status"],
        [["uk@y.com", "Y Ltd", "United Kingdom", "RocketReach B"]],  # medium = hold queue
    )
    result = pc.consolidate("acme", content_root=tmp_path, target_markets=["United States"])
    assert result["needs_verification"] == 0
    assert result["out_of_market_excluded"] == 1


def test_unreadable_profile_leaves_the_gate_off_rather_than_emptying_the_pool(tmp_path, capsys):
    """No PROFILE.md → gate off + a loud stderr note; the email_compliance preflight fails closed."""
    pdir = _market_export(tmp_path)
    result = pc.consolidate("acme", content_root=tmp_path)  # no target_markets passed
    assert result["market_gate"] == "off"
    assert result["out_of_market_excluded"] == 0
    assert "market gate off" in capsys.readouterr().err
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert "uk@y.com" in ready


def test_market_gate_blocks_is_case_and_whitespace_insensitive():
    gate = pc.MarketGate(["United States", "Singapore"])
    assert not gate.blocks("  singapore ")
    assert not gate.blocks("US")
    assert gate.blocks("France")
    assert not gate.blocks("")  # unknown passes when not strict
    assert pc.MarketGate(["United States"], strict=True).blocks("")


def test_empty_market_gate_is_falsy_and_filters_nothing():
    assert not pc.MarketGate()
    assert not pc.MarketGate([])


def test_global_market_is_a_wildcard_that_blocks_nothing():
    gate = pc.MarketGate(["United States", "Global"])
    assert not gate.blocks("France")
    assert not gate.blocks("United Arab Emirates")
    assert not gate.blocks("")


def test_global_wildcard_is_case_insensitive():
    assert not pc.MarketGate(["GLOBAL"]).blocks("Anywhere")


def test_out_of_market_lead_passes_with_global_wildcard_end_to_end(tmp_path):
    pdir = _market_export(tmp_path)
    result = pc.consolidate(
        "acme", content_root=tmp_path, target_markets=["United States", "Global"]
    )
    assert result["out_of_market_excluded"] == 0
    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    assert "uk@y.com" in ready


# --- tier_a_needing_dossier / _account_has_dossier --------------------------


def _seed_master(tmp_path, profile, rows):
    """Write master-list.csv directly (bypassing consolidate) for dossier-sweep tests."""
    pool = tmp_path / profile / "prospects" / "sequences" / ".pool"
    pool.mkdir(parents=True, exist_ok=True)
    with (pool / "master-list.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pc.MASTER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in pc.MASTER_COLS})


def _master_row(**over):
    row = dict.fromkeys(pc.MASTER_COLS, "")
    row.update(over)
    return row


def test_tier_a_needing_dossier_excludes_non_tier_a(tmp_path):
    profile = "acme"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="Acme Bank", company_domain="acmebank.com", tier="A", email="a@acmebank.com"
            ),
            _master_row(
                company="Widget Co", company_domain="widget.com", tier="B", email="b@widget.com"
            ),
        ],
    )
    out = pc.tier_a_needing_dossier(profile, content_root=tmp_path)
    assert [c["company"] for c in out] == ["Acme Bank"]


def test_tier_a_needing_dossier_excludes_account_with_canonical_dossier(tmp_path):
    profile = "acme"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="Acme Bank", company_domain="acmebank.com", tier="A", email="a@acmebank.com"
            )
        ],
    )
    accounts = tmp_path / profile / "accounts" / "acme-bank"
    accounts.mkdir(parents=True)
    (accounts / "account-dossier-acme-bank-2026-07-01.docx").write_text("x")

    out = pc.tier_a_needing_dossier(profile, content_root=tmp_path)
    assert out == []


def test_tier_a_needing_dossier_excludes_account_via_legacy_folder_fuzzy_match(tmp_path):
    profile = "acme"
    # canonical slug of "Datacore.AI" is "datacoreai", but the real legacy folder is "datacore-ai"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="Datacore.AI", company_domain="", tier="A", email="a@datacore.example"
            )
        ],
    )
    legacy = tmp_path / profile / "accounts" / "datacore-ai"
    legacy.mkdir(parents=True)
    (legacy / "account-dossier-datacore-ai-2026-07-03.docx").write_text("x")

    out = pc.tier_a_needing_dossier(profile, content_root=tmp_path)
    assert out == []


def test_tier_a_needing_dossier_includes_account_with_no_dossier(tmp_path):
    profile = "acme"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="A Better Place",
                company_domain="seniorpath.example",
                tier="A",
                email="lead@seniorpath.example",
                why_now="regulatory deadline",
                cohort="healthcare-life-sciences",
                top_intent_score="80",
            )
        ],
    )
    out = pc.tier_a_needing_dossier(profile, content_root=tmp_path)
    assert len(out) == 1
    cand = out[0]
    assert cand["company"] == "A Better Place"
    assert cand["canonical_slug"] == "a-better-place"
    assert cand["existing_legacy_folder"] is None
    assert cand["why_now"] == "regulatory deadline"
    assert cand["cohort"] == "healthcare-life-sciences"
    assert cand["top_intent_score"] == "80"


def test_tier_a_needing_dossier_dedupes_multiple_people_same_account(tmp_path):
    profile = "acme"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="Acme Bank", company_domain="acmebank.com", tier="A", email="a@acmebank.com"
            ),
            _master_row(
                company="Acme Bank", company_domain="acmebank.com", tier="A", email="b@acmebank.com"
            ),
        ],
    )
    out = pc.tier_a_needing_dossier(profile, content_root=tmp_path)
    assert len(out) == 1


def test_account_has_dossier_false_when_folder_has_no_dossier_file(tmp_path):
    profile = "acme"
    accounts = tmp_path / profile / "accounts" / "acme-bank"
    accounts.mkdir(parents=True)
    (accounts / "prospects-20260101-outreach-acme-bank.md").write_text("x")  # not a dossier file
    has, matched = pc._account_has_dossier(
        profile, "Acme Bank", "acmebank.com", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_account_has_dossier_true_for_prospecting_brief(tmp_path):
    profile = "acme"
    accounts = tmp_path / profile / "accounts" / "acme-bank"
    accounts.mkdir(parents=True)
    (accounts / "prospecting-brief-acme-bank-2026-07-24.docx").write_text("x")
    has, matched = pc._account_has_dossier(
        profile, "Acme Bank", "acmebank.com", content_root=tmp_path
    )
    assert has is True
    assert matched == "acme-bank"
