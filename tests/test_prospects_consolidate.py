"""Tests for gtm_core.prospects_consolidate — the raw-export sweep + deliverability gate."""

from __future__ import annotations

import csv
import json

from gtm_core import prospects_consolidate as pc


def _write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
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
