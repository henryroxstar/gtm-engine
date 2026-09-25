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


def test_classify_confidence_site_published():
    """A generic inbox scraped off the company's own official site — Builder/Startup
    only, and only when the address is actually on the company's own domain.

    Segment gate: a founder's `hello@` inbox plausibly reaches a decision-maker; an
    enterprise `info@` reaches a mailroom, so it stays `medium` (verify, don't
    auto-load) rather than `high` even with the same status string.
    """
    # Builder/Startup + domain matches -> high, straight to ready-to-load.csv
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="Builder",
            email="hello@halden.example",
            company_domain="halden.example",
        )
        == "high"
    )
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="startup",
            email="hello@borea.example",
            company_domain="borea.example",
        )
        == "high"
    )
    # Enterprise (or any other/blank segment), same domain-matched status -> medium,
    # NOT high — the negative control that proves the segment gate actually gates.
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="Enterprise",
            email="info@cirrus.example",
            company_domain="cirrus.example",
        )
        == "medium"
    )
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="",
            email="info@cirrus.example",
            company_domain="cirrus.example",
        )
        == "medium"
    )
    # Domain mismatch -> unknown regardless of segment: the claim "their own site
    # published this" is false on its face, so it fails closed even for a Builder.
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="Builder",
            email="hello@delta.example",
            company_domain="halden.example",
        )
        == "unknown"
    )
    # A subdomain either direction still counts as the company's own domain.
    assert (
        pc.classify_confidence(
            "site-published",
            "",
            segment="Builder",
            email="hello@mail.halden.example",
            company_domain="halden.example",
        )
        == "high"
    )
    # No email/domain supplied at all (a caller that didn't pass the new kwargs) ->
    # can't confirm the domain claim, fails closed to unknown, never crashes.
    assert pc.classify_confidence("site-published", "", segment="Builder") == "unknown"


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
    reproduced on a tenant profile in mid-2026."""
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


def test_signal_column_survives_a_consolidate_sweep(tmp_path):
    """`gtm_core.signal_record.SIGNAL_COLUMN` must be in `MASTER_COLS`, or `_load_master`'s
    hard `{c: r.get(c) for c in MASTER_COLS}` projection silently drops it on the very next
    sweep -- exactly the failure mode this column exists to avoid for the research it holds
    (2026-08-23: the same class of loss `RECORD_COLUMNS`' own header comment warns about)."""
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
                "email": "signal@x.com",
                "first": "Dana",
                "company": "X",
                "email_status": "verified",
                "conf_tier": "unknown",
                "signal_column": "Compliance event (audit, breach)",
            }
        )
        w.writerow(row)

    pc.consolidate(profile, content_root=tmp_path)
    master = pc._load_master(master_path)
    assert master[0]["signal_column"] == "Compliance event (audit, breach)"


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
                "company": "Canopy GBS | SAP Consulting |",
                "email_status": "RocketReach A",
                "conf_tier": "high",
            }
        )
        w.writerow(row)

    pc.consolidate(profile, content_root=tmp_path)
    ready = pc._load_master(pdir / "sequences" / "ready-to-load.csv")
    assert ready[0]["first"] == "Rani"
    assert ready[0]["company"] == "Canopy GBS"


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

    # PS17: neither split is loaded directly by a human, so both live under the hidden
    # `.pool/`, not visibly in `sequences/`.
    pool = pdir / "sequences" / ".pool"
    assert not (pdir / "sequences" / "ready-to-load-signal.csv").exists()
    signal = pc._load_master(pool / "ready-to-load-signal.csv")
    assert [r["email"] for r in signal] == ["a@x.com"]
    with (pool / "ready-to-load-signal.csv").open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["signal_clause"] == "Agent Control Layer launch"

    generic = {r["email"] for r in pc._load_master(pool / "ready-to-load-generic.csv")}
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


def test_split_by_signal_csv_headers_have_no_duplicate_columns(tmp_path, monkeypatch):
    """PS4 defensive check (§R18): `queues.py` builds column lists using `dict.fromkeys`
    to guarantee deduplication. We monkeypatch MASTER_COLS in `queues` to include `signal_clause`
    to prove that deduplication actually fires and prevents duplicate columns in output CSVs,
    with a negative control showing that naive list concatenation fails."""
    from gtm_core.prospects_consolidate import queues

    # Negative control: naive concatenation with duplicate column produces duplicates
    naive_cols = [*queues.MASTER_COLS, "signal_clause", "signal_clause"]
    assert len(naive_cols) != len(set(naive_cols))

    # Monkeypatch queues.MASTER_COLS to contain signal_clause, creating a collision
    # with the explicit "signal_clause" appended in queues.py line 99
    monkeypatch.setattr(queues, "MASTER_COLS", (*queues.MASTER_COLS, "signal_clause"))

    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "GTM_Why_Now"],
        [["Ann", "a@x.com", "Rain", "RocketReach A", "Agent Control Layer launch (2026-06-09)"]],
    )
    pc.consolidate(profile, content_root=tmp_path)
    pc.split_by_signal(profile, content_root=tmp_path)
    pool = pdir / "sequences" / ".pool"
    for name in ("ready-to-load-signal.csv", "ready-to-load-generic.csv"):
        with (pool / name).open(newline="", encoding="utf-8") as fh:
            header = csv.DictReader(fh).fieldnames or []
        assert len(header) == len(set(header)), f"{name}: duplicate columns in {header}"
        assert header.count("signal_clause") == 1


def test_split_by_signal_supersedes_a_pre_ps17_visible_stamp(tmp_path):
    """PS17 moved `ready-to-load-signal.csv`/`ready-to-load-generic.csv` from visibly in
    `sequences/` to hidden under `sequences/.pool/`, but only for the destination this run
    writes to — a copy already sitting VISIBLY in `sequences/` from before that change
    shipped is never touched by the new write, and is orphaned on disk forever. The next
    `split_by_signal` run must find that stale visible copy and archive it, exactly as
    `lanes.router.write_lanes` already does for its own lane CSVs."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    seq = pdir / "sequences"
    seq.mkdir(parents=True)
    legacy_signal = seq / "ready-to-load-signal.csv"
    legacy_signal.write_text("email\nstale@old.example\n", encoding="utf-8")
    legacy_generic = seq / "ready-to-load-generic.csv"
    legacy_generic.write_text("email\nstale2@old.example\n", encoding="utf-8")

    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "GTM_Why_Now"],
        [["Ann", "a@x.com", "Rain", "RocketReach A", "Agent Control Layer launch (2026-06-09)"]],
    )
    pc.consolidate(profile, content_root=tmp_path)
    pc.split_by_signal(profile, content_root=tmp_path)

    # The stale visible copies are gone from `sequences/` — moved, never deleted — and the
    # fresh run wrote the new pair at `.pool/`.
    assert not legacy_signal.exists()
    assert not legacy_generic.exists()
    pool = seq / ".pool"
    assert (pool / "ready-to-load-signal.csv").is_file()
    assert (pool / "ready-to-load-generic.csv").is_file()
    superseded = pool / ".superseded"
    assert (superseded / "ready-to-load-signal.csv").read_text(encoding="utf-8") == (
        "email\nstale@old.example\n"
    )
    assert (superseded / "ready-to-load-generic.csv").read_text(encoding="utf-8") == (
        "email\nstale2@old.example\n"
    )


def test_stamp_lanes_prefers_reason_over_trigger_and_blanks_rows_missing_from_state(
    tmp_path,
):
    """PS2 + PS5, first test coverage for `_stamp_lanes` itself.

    A row whose email is no longer in `lanes-state.jsonl` — dropped from the pool, or from
    an earlier route the state file no longer covers — must get a BLANK `lane`/`lane_reason`
    rather than keep whatever it last had; a stale stamp reads as "still routed this way",
    which is worse than an admittedly-unrouted row.
    """
    from gtm_core.lanes.decisions import state_path
    from gtm_core.prospects_consolidate.consolidate import _stamp_lanes

    profile = "acme"
    state_file = state_path(profile, tmp_path)
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {
                    "email": "a@x.example",
                    "lane": "personalised",
                    "trigger": "",
                    "reason": "researcher-send",
                },
                {
                    "email": "b@y.example",
                    "lane": "hold",
                    "trigger": "tier-a-generic",
                    "reason": "tier-a-generic",
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {"email": "a@x.example", "lane": "", "lane_reason": ""},
        {"email": "b@y.example", "lane": "", "lane_reason": ""},
        # Stale: routed `generic` on a previous sweep, but no longer in the state file.
        {"email": "c@z.example", "lane": "generic", "lane_reason": "generic:old-trigger"},
    ]
    stamped = _stamp_lanes(rows, profile, tmp_path)
    assert stamped == 2
    by_email = {r["email"]: r for r in rows}
    assert by_email["a@x.example"]["lane"] == "personalised"
    assert by_email["a@x.example"]["lane_reason"] == "researcher-send"
    assert by_email["b@y.example"]["lane"] == "hold"
    assert by_email["b@y.example"]["lane_reason"] == "tier-a-generic"
    assert by_email["c@z.example"]["lane"] == ""
    assert by_email["c@z.example"]["lane_reason"] == ""


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
                company="A Fernway Capital",
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
    assert cand["company"] == "A Fernway Capital"
    assert cand["canonical_slug"] == "a-fernway-capital"
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
    has, matched = pc.account_has_dossier(
        profile, "Acme Bank", "acmebank.com", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_account_has_dossier_true_for_prospecting_brief(tmp_path):
    profile = "acme"
    accounts = tmp_path / profile / "accounts" / "acme-bank"
    accounts.mkdir(parents=True)
    (accounts / "prospecting-brief-acme-bank-2026-07-24.docx").write_text("x")
    has, matched = pc.account_has_dossier(
        profile, "Acme Bank", "acmebank.com", content_root=tmp_path
    )
    assert has is True
    assert matched == "acme-bank"


# --- account_has_dossier: abbreviating-domain + geo-qualifier fallback -------
#
# Until 2026-08-25 the fuzzy fallback compared a DOMAIN-derived token against a
# NAME-derived folder token, so it was structurally inert for every account whose
# domain abbreviates its name (nc.example for Northwind Chartered, ...): those two tokens
# can never be equal. The docstring promised the fallback prevented a duplicate folder;
# for this whole class it never ran. The negative controls below matter as much as the
# positive ones — this primitive backs account_integrity's `no-dossier` ERROR gate, so
# a match that is too loose masks genuinely missing research before a sequence load.


def _seed_dossier(tmp_path, profile, folder, filename="account-dossier-x-2026-08-12.docx"):
    d = tmp_path / profile / "accounts" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("x")
    return d


def test_account_has_dossier_matches_legacy_folder_when_domain_abbreviates_name(tmp_path):
    """The reported repro: org_token("nc.example", ...) is `sc`, the folder tokenises to
    `northwindcharteredsingapore`, and the two can never compare equal."""
    _seed_dossier(tmp_path, "acme", "northwind-chartered-singapore")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is True
    assert matched == "northwind-chartered-singapore"


def test_account_has_dossier_matches_abbreviating_domain_without_geo_suffix(tmp_path):
    """`-plc` is stripped by the org-token normaliser, so this needs only the
    name-derived comparison — not the geographic-qualifier one."""
    _seed_dossier(tmp_path, "acme", "northwind-chartered-plc")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is True
    assert matched == "northwind-chartered-plc"


def test_account_has_dossier_matches_when_company_carries_the_geo_qualifier(tmp_path):
    """Mirror direction: the qualifier is on the company name, not the folder."""
    _seed_dossier(tmp_path, "acme", "northwind-chartered")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered Singapore", "nc.example", content_root=tmp_path
    )
    assert has is True
    assert matched == "northwind-chartered"


def test_account_has_dossier_matches_two_word_geo_qualifier(tmp_path):
    _seed_dossier(tmp_path, "acme", "northwind-chartered-hong-kong")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is True
    assert matched == "northwind-chartered-hong-kong"


def test_account_has_dossier_prefers_exact_token_over_geo_qualified_folder(tmp_path):
    _seed_dossier(tmp_path, "acme", "northwind-chartered-singapore")
    _seed_dossier(tmp_path, "acme", "northwind-chartered-ltd")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is True
    assert matched == "northwind-chartered-ltd"  # `ltd` is suffix-stripped -> exact token


# --- negative controls ------------------------------------------------------


def test_account_has_dossier_does_not_match_unrelated_company_sharing_a_domain_stem(tmp_path):
    """Two genuinely different companies must not collide."""
    _seed_dossier(tmp_path, "acme", "vertex-robotics")
    has, matched = pc.account_has_dossier(
        "acme", "Vertex Analytics", "vertex.example", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_account_has_dossier_does_not_match_a_separate_business_unit(tmp_path):
    """NC Ventures is a distinct entity with its own buyers; its dossier must not be
    accepted as research for the parent bank."""
    _seed_dossier(tmp_path, "acme", "nc-ventures-by-northwind-chartered")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_account_has_dossier_does_not_match_across_two_regional_accounts(tmp_path):
    """A qualifier is tolerated on ONE side only — base-vs-base would collapse every
    regional account of a brand into one."""
    _seed_dossier(tmp_path, "acme", "northwind-chartered-malaysia")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered Singapore", "nc.example", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_account_has_dossier_does_not_strip_a_name_merely_ending_in_qualifier_letters(tmp_path):
    """`_drop_geo_suffix` splits on word boundaries, so "Nexus" is not read as
    "Nex" + "us" (which would match an unrelated `nex` folder)."""
    _seed_dossier(tmp_path, "acme", "nex")
    has, matched = pc.account_has_dossier("acme", "Nexus", "nexus.example", content_root=tmp_path)
    assert has is False
    assert matched == ""


def test_account_has_dossier_geo_match_still_requires_an_actual_dossier_file(tmp_path):
    """The looser name match must not weaken the file check the gate depends on."""
    d = tmp_path / "acme" / "accounts" / "northwind-chartered-singapore"
    d.mkdir(parents=True)
    (d / "prospects-20260101-outreach-northwind-chartered-singapore.md").write_text("x")
    has, matched = pc.account_has_dossier(
        "acme", "Northwind Chartered", "nc.example", content_root=tmp_path
    )
    assert has is False
    assert matched == ""


def test_drop_geo_suffix_leaves_unqualified_and_bare_names_alone():
    assert pc._drop_geo_suffix("northwind-chartered-singapore") == "northwind chartered"
    assert pc._drop_geo_suffix("northwind-chartered-hong-kong") == "northwind chartered"
    assert pc._drop_geo_suffix("northwind-chartered") == "northwind-chartered"
    assert pc._drop_geo_suffix("Nexus") == "Nexus"
    assert pc._drop_geo_suffix("Undersea") == "Undersea"
    assert pc._drop_geo_suffix("Singapore") == "Singapore"  # never strips the whole name


def test_accounts_needing_dossier_excludes_abbreviating_domain_account(tmp_path):
    """End-to-end: the sweep must stop re-generating a second folder for these."""
    profile = "acme"
    _seed_master(
        tmp_path,
        profile,
        [
            _master_row(
                company="Northwind Chartered",
                company_domain="nc.example",
                tier="A",
                email="a@nc.example",
            )
        ],
    )
    _seed_dossier(tmp_path, profile, "northwind-chartered-singapore")
    assert pc.accounts_needing_dossier(profile, content_root=tmp_path) == []


# --- the ledger survives a rebuild, structurally (B3/B6) --------------------- #
# `suppression apply` wrote two columns that the very next sweep's projection
# dropped, so keeping exclusions alive depended on a human re-running a command
# after every consolidate. On 2026-08-11 nobody did and 55 were wiped in an hour.


def _pool(root, profile):
    return _prospects_dir(root, profile) / "sequences" / ".pool"


def _ledger(tmp_path, profile, rows):
    from gtm_core import prospect_paths

    p = prospect_paths.suppression_ledger(profile, content_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = ["email", "name", "company_domain", "reason", "date", "note"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def test_suppression_columns_survive_the_projection(tmp_path):
    assert "suppression" in pc.MASTER_COLS and "suppression_date" in pc.MASTER_COLS


def test_master_cols_stay_prefix_stable(tmp_path):
    """New columns are APPENDED — anything reading by index must not shift."""
    assert pc.MASTER_COLS[:6] == ["first", "last", "email", "title", "company", "company_domain"]


def test_consolidate_stamps_suppression_from_the_ledger(tmp_path):
    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "verified"]],
    )
    _ledger(
        tmp_path,
        "acme",
        [{"email": "dana@northwind.example", "reason": "dnc-optout", "date": "2026-08-11"}],
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["ledger_suppressions_marked"] == 1
    assert res["suppressed_excluded"] == 1
    assert res["ready_to_load"] == 0, "a suppressed person reached the send list"


def test_a_rebuild_does_not_lose_the_suppression(tmp_path):
    """The regression, end to end: consolidate twice, still suppressed."""
    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "verified"]],
    )
    _ledger(tmp_path, "acme", [{"email": "dana@northwind.example", "reason": "dnc-optout"}])
    pc.consolidate("acme", content_root=tmp_path)
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["suppressed_excluded"] == 1, "the rebuild returned a suppressed person to the pool"
    master = list(csv.DictReader((_pool(tmp_path, "acme") / "master-list.csv").open()))
    assert master[0]["suppression"] == "dnc-optout"


def test_a_person_suppressed_under_another_address_is_still_excluded(tmp_path):
    """The person key reaches into the build, not just the standalone gate."""
    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Company Domain Name", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    _ledger(
        tmp_path,
        "acme",
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
            }
        ],
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["suppressed_excluded"] == 1


def test_a_disqualified_account_does_not_return_to_the_send_list(tmp_path):
    """`status: disqualified` was decoration — the build read no lifecycle status at all."""
    import json as _json

    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Company Domain Name", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    latest = _prospects_dir(tmp_path, "acme") / "latest.json"
    latest.write_text(
        _json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [
                    {
                        "company": "Northwind",
                        "domain": "northwind.example",
                        "status": "disqualified",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["disqualified_excluded"] == 1
    assert res["ready_to_load"] == 0


def test_an_active_account_is_unaffected_by_the_status_filter(tmp_path):
    """Positive control: the filter must not empty a healthy list."""
    import json as _json

    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Company Domain Name", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]],
    )
    latest = _prospects_dir(tmp_path, "acme") / "latest.json"
    latest.write_text(
        _json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [{"company": "Northwind", "domain": "northwind.example", "status": "new"}],
            }
        ),
        encoding="utf-8",
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["disqualified_excluded"] == 0
    assert res["ready_to_load"] == 1


def test_a_missing_latest_json_is_not_an_error(tmp_path):
    _write_csv(
        _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "verified"]],
    )
    res = pc.consolidate("acme", content_root=tmp_path)
    assert res["disqualified_excluded"] == 0


def test_judge_columns_survive_the_projection(tmp_path):
    """D2: the judged CSV was an orphan — verdicts were written into a file the
    enrollment gate never read, because consolidate regenerates the one it does."""
    from gtm_core.signal_record import JUDGE_COLUMNS

    for col in JUDGE_COLUMNS:
        assert col in pc.MASTER_COLS


def test_a_judge_verdict_reaches_ready_to_load(tmp_path):
    src = _prospects_dir(tmp_path, "acme") / "prospects-20260827-hubspot.csv"
    _write_csv(
        src,
        ["First Name", "Last Name", "Email", "Company", "Email Status"],
        [["Dana", "Vance", "dana@northwind.example", "Northwind", "verified"]],
    )
    pc.consolidate("acme", content_root=tmp_path)

    # A judge pass writes into the master list, the way write-verdicts would.
    master = _pool(tmp_path, "acme") / "master-list.csv"
    rows = list(csv.DictReader(master.open()))
    rows[0]["judge_verdict"] = "drop"
    rows[0]["judge_calibrated"] = "true"
    with master.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=pc.MASTER_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    pc.consolidate("acme", content_root=tmp_path)
    ready = list(csv.DictReader(pc.ready_to_load_path("acme", content_root=tmp_path).open()))
    assert ready and ready[0]["judge_verdict"] == "drop", (
        "the judge's verdict did not survive the rebuild into the file the gate reads"
    )
    assert ready[0]["judge_calibrated"] == "true"


# --- the account's research record must reach the row the gate reads -------------
#
# `account_integrity --require-verdict send` filters ROWS in ready-to-load.csv, but the
# researcher writes the record onto the ACCOUNT in latest.json. Until this propagation
# existed the join stamped `account_id` and nothing else, so a fully-researched batch
# still produced "kept 0/N rows with verdict='send'" — judgement made in one place,
# enforced from another, with nothing carrying it across. Same defect class as D2.


def _latest_with_record(tmp_path, *, status="new", **record):
    import json as _json

    item = {
        "company": "Northwind",
        "domain": "northwind.example",
        "status": status,
        "account_id": "a-0000000001",
    }
    item.update(record)
    (_prospects_dir(tmp_path, "acme") / "latest.json").write_text(
        _json.dumps({"kind": "prospects", "profile": "acme", "items": [item]}),
        encoding="utf-8",
    )


_FULL_RECORD = {
    "signal_source_url": "https://northwind.example/newsroom/agents",
    "signal_observed": "2026-08-01",
    "signal_evidence": "Northwind now runs autonomous agents across its claims workflow.",
    "signal_subject": "Northwind",
    "signal_agent_kind": "ai",
    "category_relation": "prospect",
    "verdict": "send",
    "verdict_reason": "",
    "signal_column": "Compliance event (audit, breach)",
}


def _one_row(tmp_path, name="prospects-20260827-hubspot.csv", extra_cols=None, extra_vals=None):
    cols = ["First Name", "Last Name", "Email", "Company", "Company Domain Name", "Email Status"]
    vals = ["Dana", "Vance", "dana@northwind.example", "Northwind", "northwind.example", "verified"]
    if extra_cols:
        cols += extra_cols
        vals += extra_vals
    _write_csv(_prospects_dir(tmp_path, "acme") / name, cols, vals and [vals] or [])


def _ready_rows(tmp_path):
    import csv as _csv

    path = _prospects_dir(tmp_path, "acme") / "sequences" / "ready-to-load.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return list(_csv.DictReader(f))


def test_account_record_reaches_the_row_the_gate_reads(tmp_path):
    """The whole point: a researched account makes its rows carry verdict + provenance."""
    _one_row(tmp_path)
    _latest_with_record(tmp_path, **_FULL_RECORD)

    res = pc.consolidate("acme", content_root=tmp_path)

    assert res["records_joined"] == 1
    (row,) = _ready_rows(tmp_path)
    for col, expected in _FULL_RECORD.items():
        assert row[col] == expected, col


def test_row_level_record_wins_over_the_account(tmp_path):
    """A record written into the export is more specific; the account must not clobber it."""
    _one_row(
        tmp_path,
        extra_cols=["GTM_Verdict", "GTM_Signal_Subject"],
        extra_vals=["drop", "Northwind Holdings (parent)"],
    )
    _latest_with_record(tmp_path, **_FULL_RECORD)

    pc.consolidate("acme", content_root=tmp_path)

    (row,) = _ready_rows(tmp_path)
    assert row["verdict"] == "drop"
    assert row["signal_subject"] == "Northwind Holdings (parent)"
    # ...while the fields the row did NOT carry are still filled from the account.
    assert row["signal_source_url"] == _FULL_RECORD["signal_source_url"]


def test_a_corrected_account_clause_brings_its_own_provenance(tmp_path):
    """The clause and the source that evidences it move together, or not at all.

    Re-research writes a better fact onto the ACCOUNT. The row must not end up holding the
    new clause beside the old clause's source — a row whose cited page does not support its
    own claim is the `signal-evidence-unsupported` defect these columns exist to catch. Seen
    2026-08-29 while building this: Pace's row took a clause about a named customer selecting
    them while still citing the funding article the previous clause came from.
    """
    _one_row(
        tmp_path,
        extra_cols=["GTM_Why_Now", "GTM_Signal_Source_Url", "GTM_Signal_Observed"],
        extra_vals=["stale raise clause", "https://old.example/funding", "2026-01-02"],
    )
    _latest_with_record(
        tmp_path,
        **{
            **_FULL_RECORD,
            "why_now": "Northwind names Contoso as its agent partner",
            "signal_source_url": "https://new.example/partner",
            "signal_observed": "2026-06-01",
        },
    )

    pc.consolidate("acme", content_root=tmp_path)
    (row,) = _ready_rows(tmp_path)

    assert row["why_now"] == "Northwind names Contoso as its agent partner"
    assert row["signal_source_url"] == "https://new.example/partner"
    assert row["signal_observed"] == "2026-06-01"


def test_a_corrected_account_clause_brings_the_judgements_that_describe_it(tmp_path):
    """Subject, agent kind and relation describe the CLAUSE, so they follow it.

    Row-wins protects a row's own, more specific reading of the clause it carries. Once the
    account's clause has replaced the row's, that reading describes a sentence the row no
    longer holds. Seen 2026-09-25: re-research promoted corrected records onto their
    accounts, the pool took each new clause and kept the old subject beside it, the gate
    blocked those rows on `signal-subject-mismatch`, and an adjacent vendor's row still read
    `prospect` — the direction that passes silently.
    """
    _one_row(
        tmp_path,
        extra_cols=[
            "GTM_Why_Now",
            "GTM_Signal_Subject",
            "GTM_Signal_Agent_Kind",
            "GTM_Category_Relation",
        ],
        extra_vals=["stale raise clause", "Northwind Capital (lead investor)", "none", "prospect"],
    )
    _latest_with_record(
        tmp_path,
        **{
            **_FULL_RECORD,
            "why_now": "Northwind names Contoso as its agent partner",
            "category_relation": "adjacent",
        },
    )

    pc.consolidate("acme", content_root=tmp_path)
    (row,) = _ready_rows(tmp_path)

    assert row["why_now"] == "Northwind names Contoso as its agent partner"
    assert row["signal_subject"] == "Northwind"
    assert row["signal_agent_kind"] == "ai"
    assert row["category_relation"] == "adjacent"


def test_stale_provenance_is_repaired_even_when_the_clause_already_matches(tmp_path):
    """Negative control for the LATCH bug, and it has to be THIS scenario.

    The obvious control — sweep twice and assert the clause still wins — is inert: a latched
    implementation copies clause AND provenance together on the first pass, so the second pass
    has nothing left to get wrong and the test passes either way. Verified inert 2026-08-29
    before this replaced it.

    The state that actually distinguishes them is a row whose clause ALREADY matches the
    account while its provenance does not — exactly what a pool looks like after a clause-only
    correction shipped ahead of the provenance one. A latched rule ("only act when the clause
    differs") sees no difference and leaves the row citing a page that does not support it.
    """
    _one_row(
        tmp_path,
        extra_cols=["GTM_Why_Now", "GTM_Signal_Source_Url", "GTM_Signal_Observed"],
        extra_vals=[
            "Northwind names Contoso as its agent partner",  # clause already correct
            "https://old.example/funding",  # provenance still from the OLD clause
            "2026-01-02",
        ],
    )
    _latest_with_record(
        tmp_path,
        **{
            **_FULL_RECORD,
            "why_now": "Northwind names Contoso as its agent partner",
            "signal_source_url": "https://new.example/partner",
            "signal_observed": "2026-06-01",
        },
    )

    pc.consolidate("acme", content_root=tmp_path)
    (row,) = _ready_rows(tmp_path)

    assert row["signal_source_url"] == "https://new.example/partner"
    assert row["signal_observed"] == "2026-06-01"


def test_orphan_row_gets_no_record(tmp_path):
    """Negative control: a fix that fills every row regardless of the join must fail here."""
    _one_row(tmp_path)
    _latest_with_record(tmp_path, **_FULL_RECORD)
    # Same export, but the account in latest.json is a different company entirely.
    import json as _json

    (_prospects_dir(tmp_path, "acme") / "latest.json").write_text(
        _json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [
                    dict(
                        {
                            "company": "Southwind",
                            "domain": "southwind.example",
                            "status": "new",
                            "account_id": "a-0000000002",
                        },
                        **_FULL_RECORD,
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    res = pc.consolidate("acme", content_root=tmp_path)

    assert res["records_joined"] == 0
    (row,) = _ready_rows(tmp_path)
    assert row["verdict"] == ""
    assert row["signal_source_url"] == ""


def test_judge_columns_are_never_propagated_from_the_account(tmp_path):
    """`verdict` is the researcher's; `judge_*` is the judge's. The join must not blur them."""
    _one_row(tmp_path)
    _latest_with_record(
        tmp_path,
        judge_verdict="drop",
        judge_verdict_reason="account-level judge value that must not travel",
        judge_calibrated="true",
        **_FULL_RECORD,
    )

    pc.consolidate("acme", content_root=tmp_path)

    (row,) = _ready_rows(tmp_path)
    assert row["verdict"] == "send"
    assert row["judge_verdict"] == ""
    assert row["judge_verdict_reason"] == ""
    assert row["judge_calibrated"] == ""


def test_record_survives_a_second_sweep(tmp_path):
    """Re-derived from latest.json each sweep, so a rebuild can never drop it."""
    _one_row(tmp_path)
    _latest_with_record(tmp_path, **_FULL_RECORD)

    first = pc.consolidate("acme", content_root=tmp_path)
    second = pc.consolidate("acme", content_root=tmp_path)

    (row,) = _ready_rows(tmp_path)
    assert row["verdict"] == "send"
    # `records_joined` counts rows this sweep NEWLY filled — the same "what changed"
    # semantics as its siblings `accounts_joined` and `pool_row_ids_stamped`. The record
    # persisting with nothing left to fill is the success case, so 0 is correct here and
    # a non-zero count on every sweep would mean the fill was not sticking.
    assert first["records_joined"] == 1
    assert second["records_joined"] == 0


def test_a_corrected_record_propagates_on_the_next_sweep(tmp_path):
    """Fill-only must not mean write-once: a field the row still lacks arrives later."""
    _one_row(tmp_path)
    partial = {k: v for k, v in _FULL_RECORD.items() if k != "signal_evidence"}
    _latest_with_record(tmp_path, **partial)
    pc.consolidate("acme", content_root=tmp_path)
    assert _ready_rows(tmp_path)[0]["signal_evidence"] == ""

    # The researcher goes back and adds the missing verbatim span.
    _latest_with_record(tmp_path, **_FULL_RECORD)
    res = pc.consolidate("acme", content_root=tmp_path)

    assert res["records_joined"] == 1
    assert _ready_rows(tmp_path)[0]["signal_evidence"] == _FULL_RECORD["signal_evidence"]


def test_a_corrected_account_segment_tier_score_propagates_on_the_next_sweep(tmp_path):
    """Re-scoring an account already in the pool must reach `ready-to-load.csv`.

    Before segment/tier/score joined `_INHERITED_RECORD_COLUMNS`, an already-present row
    (the `already_present` branch in `consolidate`) only got its `conf_tier` reclassified —
    the stale segment/tier/score from whichever hubspot export first introduced the email
    sat in the pool forever, with no documented path (not `finalize`, not
    `signal_backfill --promote`, not a later `consolidate`) to correct it even after
    `latest.json`'s own account record was fixed.
    """
    _one_row(
        tmp_path,
        extra_cols=["GTM_Segment", "GTM_Tier", "GTM_Score"],
        extra_vals=["startup", "C", "40"],
    )
    pc.consolidate("acme", content_root=tmp_path)
    stale = _ready_rows(tmp_path)[0]
    assert stale["segment"] == "startup"
    assert stale["tier"] == "C"
    assert stale["score"] == "40"

    # The account is re-gated and re-scored under a different rubric; the correction lands
    # on the account record in latest.json, same as prospects_import.finalize/upsert_latest.
    _latest_with_record(tmp_path, segment="builder", tier="A", score="90", **_FULL_RECORD)
    res = pc.consolidate("acme", content_root=tmp_path)

    corrected = _ready_rows(tmp_path)[0]
    assert corrected["segment"] == "builder"
    assert corrected["tier"] == "A"
    assert corrected["score"] == "90"
    assert res["records_joined"] == 1


# --- the hand-send list -------------------------------------------------------
#
# A merge-blocked row is not an unsendable row. A role inbox with no first name cannot
# render "Hi {{First Name}}," so no SEQUENCER will take it, and a person sends it instead.
# That person is downstream of every load-time gate — `account_integrity` filters a list on
# the way INTO a sequencer, so a list that never goes in is never filtered.
#
# On 2026-09-04 the hand-send list was therefore assembled by hand from the pool: 8 role
# inboxes of which 5 carried a Gate-B `drop` verdict, i.e. accounts research had already
# refused. Nothing was wrong with the gate; the gate was never in the path. The only control
# was the operator remembering which 3 of the 8 rows to copy.


def _no_first_name_export(pdir, rows):
    """Write an export whose rows have a BLANK first name — the merge-field gate's trigger.

    `_write_csv` helpfully injects a placeholder first name when the header omits one, which
    would make every row here sequenceable and quietly test nothing. Supplying the column
    explicitly, empty, is what puts these rows on the hand-send path.
    """
    _write_csv(
        pdir / "prospects-20260101-inbox-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "Verdict"],
        rows,
    )


def test_hand_send_list_carries_only_rows_that_may_be_sent(tmp_path):
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _no_first_name_export(
        pdir,
        [
            ["", "hello@northgate.example", "Northgate Labs", "verified", "re-angle"],
            ["", "team@brightpath.example", "Brightpath", "verified", "send"],
            ["", "info@quaymark.example", "Quaymark", "verified", ""],
            # Refused by research. This is the row the 2026-09-04 list shipped five of.
            ["", "hi@stonebridge.example", "Stonebridge", "verified", "drop"],
        ],
    )
    result = pc.consolidate(profile, content_root=tmp_path)

    assert result["merge_field_excluded"] == 4, "fixture is not exercising the hand-send path"
    assert result["hand_send"] == 3
    assert result["hand_send_refused"] == 1

    rows = pc._load_master(pdir / "sequences" / "hand-send.csv")
    assert {r["email"] for r in rows} == {
        "hello@northgate.example",
        "team@brightpath.example",
        "info@quaymark.example",
    }
    assert all(r["verdict"] != "drop" for r in rows)


def test_a_hand_send_row_never_reaches_the_sequencer_load_file(tmp_path):
    """The two lists are disjoint by construction. A row appearing in both would be sent
    twice — once by the sequencer and once by the person working the hand list."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _no_first_name_export(pdir, [["", "team@brightpath.example", "Brightpath", "verified", "send"]])
    _write_csv(
        pdir / "prospects-20260102-named-hubspot.csv",
        ["First Name", "Email", "Company Name", "Email Status", "Verdict"],
        [["Robin", "robin@northgate.example", "Northgate Labs", "verified", "send"]],
    )
    pc.consolidate(profile, content_root=tmp_path)

    ready = {r["email"] for r in pc._load_master(pdir / "sequences" / "ready-to-load.csv")}
    hand = {r["email"] for r in pc._load_master(pdir / "sequences" / "hand-send.csv")}
    assert ready == {"robin@northgate.example"}
    assert hand == {"team@brightpath.example"}
    assert not (ready & hand)


def test_the_hand_send_filter_is_the_same_rule_the_enrollment_gate_applies(tmp_path):
    """One rule, one home (`gtm_core.lane_verdicts`). Two callers applying their own idea
    of "may this be sent" is how the two drifted apart in the first place — the gate had
    the rule and the sweep could not import it."""
    from gtm_core.account_integrity import LANE_VERDICTS as gate_rule
    from gtm_core.lane_verdicts import LANE_VERDICTS as owner

    assert gate_rule is owner
    assert "drop" not in set().union(*owner.values()), (
        "`drop` became admissible somewhere — it must enrol in no lane at all"
    )


def test_an_empty_hand_send_list_is_still_written(tmp_path):
    """Never-had-any and filtered-to-none must not look like a missing file. A stale list
    left on disk from a previous sweep is exactly the artifact this replaced."""
    profile = "acme"
    pdir = _prospects_dir(tmp_path, profile)
    _no_first_name_export(pdir, [["", "hi@stonebridge.example", "Stonebridge", "verified", "drop"]])
    result = pc.consolidate(profile, content_root=tmp_path)

    assert result["hand_send"] == 0 and result["hand_send_refused"] == 1
    assert (pdir / "sequences" / "hand-send.csv").exists()
    assert pc._load_master(pdir / "sequences" / "hand-send.csv") == []


# ------------------------------------------------------ the header-variant accessor


def test_column_value_prefers_the_newest_prefix_then_falls_back():
    """One reader for `_ALIASES`, so no module has to hardcode a spelling.

    `email_campaign_dashboard.roster` hardcoded `GTM_*`, so an export written under an
    earlier column prefix rendered a whole campaign's roster as blank tier / blank score /
    no signal, silently.
    """
    from gtm_core.prospects_consolidate import column_value

    assert column_value({"GTM_Segment": "Builder"}, "segment") == "Builder"
    assert column_value({"OLD_Segment": "builder"}, "segment") == "builder"
    assert column_value({"segment": "startup"}, "segment") == "startup"
    # Newest prefix wins when a row somehow carries both.
    assert column_value({"GTM_Tier": "A", "OLD_Tier": "B"}, "tier") == "A"
    # An empty newer value must not shadow a populated older one.
    assert column_value({"GTM_Tier": "  ", "OLD_Tier": "B"}, "tier") == "B"


def test_column_value_is_fail_quiet_on_an_unaliased_field():
    """Callers depend on "" rather than a raise — the same contract `_get` always had."""
    from gtm_core.prospects_consolidate import column_value

    assert column_value({"x": "y"}, "no_such_field") == ""
    assert column_value({}, "segment") == ""


def test_a_column_written_under_an_older_prefix_still_resolves():
    """Reachability, not membership — and the distinction is the whole point.

    Some live exports were written before the current column prefix. The engine must read
    them, and it must do so WITHOUT naming the old prefix: a prefix is one tenant's spelling
    of its own export history, so a literal in ``gtm_core`` is tenant data in company-
    agnostic code and the release carve refuses it outright. So the accessor matches the
    column's shape, and this test asserts the value comes back rather than asserting a
    particular string sits in ``_ALIASES``.

    The prefixes below are arbitrary on purpose. If the assertion only held for the one real
    legacy prefix, the implementation would be an enumeration wearing a regex's clothes.
    """
    from gtm_core.prospects_consolidate import column_value
    from gtm_core.prospects_consolidate.columns import _ALIASES

    for prefix in ("LEGACY", "OLD", "V1"):
        assert column_value({f"{prefix}_Segment": "builder"}, "segment") == "builder"
        assert column_value({f"{prefix}_Tier": "A"}, "tier") == "A"
        assert column_value({f"{prefix}_Why_Now": "shipped"}, "why_now") == "shipped"
        assert column_value({f"{prefix}_Persona_Tier": "Champion"}, "cohort") == "Champion"

    # And no prefix is named in the engine: the alias map carries only the current one.
    named = {
        a.split("_", 1)[0] for v in _ALIASES.values() for a in v if "_" in a and a[0].isupper()
    }
    assert named <= {
        "GTM",
        "First",
        "Last",
        "Job",
        "Contact",
        "Company",
        "Email",
        "HQ",
        "Why",
        "Case",
        "Top",
        "Intent",
        "Lead",
        "Signal",
        "Category",
        "Qualification",
        "Persona",
    }, (
        f"an unexpected column prefix is hardcoded in _ALIASES: {sorted(named)}. A tenant's "
        "own prefix belongs in its data, not in company-agnostic code."
    )


def test_a_current_spelling_is_never_shadowed_by_the_shape_fallback():
    """The fallback runs only after every explicit alias came back empty."""
    from gtm_core.prospects_consolidate import column_value

    assert column_value({"GTM_Tier": "A", "OLD_Tier": "B"}, "tier") == "A"
    assert column_value({"Tier": "A", "OLD_Tier": "B"}, "tier") == "A"
    # A field with no underscore-bearing alias cannot match by shape at all.
    assert column_value({"OLD_Conf": "9"}, "conf") == ""


def test_load_master_lowercases_a_capitalised_segment_and_keeps_an_unknown_one(tmp_path):
    """Storage is lowercase; 46 of 622 pooled rows read `Startup`/`Enterprise` on 2026-09-24.
    Case-only repair: a value `SEGMENTS` does not know passes through as written."""
    from gtm_core.prospects_consolidate.columns import MASTER_COLS
    from gtm_core.prospects_consolidate.io import _load_master

    path = tmp_path / "master-list.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MASTER_COLS)
        w.writeheader()
        for email, segment in (
            ("a@x.example", "Startup"),
            ("b@x.example", "Enterprise"),
            ("c@x.example", "builder"),
            ("d@x.example", "Weird"),
        ):
            w.writerow(
                {
                    "email": email,
                    "segment": segment,
                    "first": "Avery",
                    "last": "Quill",
                    "company": "Copperline",
                    "title": "CTO",
                }
            )
    assert {r["email"]: r["segment"] for r in _load_master(path)} == {
        "a@x.example": "startup",
        "b@x.example": "enterprise",
        "c@x.example": "builder",
        "d@x.example": "Weird",
    }


def test_stamp_lanes_owns_the_judge_columns(tmp_path):
    """The judge's columns come from the state record the last route wrote, and are BLANK
    when it attached none — never carried from the previous master. On 2026-09-24 the pool
    carried 283 `re-angle` / 220 `drop` scored against August specs nobody would send,
    while the state file, routed without records, held no verdict at all."""
    from gtm_core.lanes.decisions import state_path
    from gtm_core.prospects_consolidate.consolidate import _stamp_lanes

    profile = "acme"
    state_file = state_path(profile, tmp_path)
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        json.dumps(
            {
                "email": "a@x.example",
                "lane": "personalised",
                "reason": "researcher-send",
                "judge_verdict": "send",
                "judge_defect_class": "",
                "judge_note": "clean",
                "judge_calibrated": "false",
            }
        )
        + "\n"
        + json.dumps({"email": "b@y.example", "lane": "generic", "reason": "no-signal"})
        + "\n",
        encoding="utf-8",
    )
    stale = {
        "judge_verdict": "drop",
        "judge_verdict_reason": "an August spec",
        "judge_defect_class": "vague_frame",
        "judge_calibrated": "true",
    }
    rows = [
        {"email": "a@x.example", **stale},
        {"email": "b@y.example", **stale},  # routed, no record attached
        {"email": "c@z.example", **stale},  # not in the state file at all
    ]
    _stamp_lanes(rows, profile, tmp_path)
    by_email = {r["email"]: r for r in rows}
    a = by_email["a@x.example"]
    assert (a["judge_verdict"], a["judge_verdict_reason"], a["judge_defect_class"]) == (
        "send",
        "clean",
        "",
    )
    assert a["judge_calibrated"] == "false"
    for email in ("b@y.example", "c@z.example"):
        assert all(by_email[email][col] == "" for col in stale), email
