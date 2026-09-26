from __future__ import annotations

import csv
import json
from pathlib import Path

from gtm_core.prospects_consolidate.cli import _cli
from gtm_core.prospects_consolidate.columns import MASTER_COLS
from gtm_core.prospects_consolidate.dossier import (
    EXCLUSION_REASONS,
    accounts_needing_dossier,
)


def _seed_master(tmp_path: Path, profile: str, rows: list[dict]) -> None:
    pool = tmp_path / profile / "prospects" / "sequences" / ".pool"
    pool.mkdir(parents=True, exist_ok=True)
    with (pool / "master-list.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in MASTER_COLS})


def _row(**kwargs) -> dict:
    base = {
        "company": "Acme Inc",
        "company_domain": "acme.com",
        "email": "alice@acme.com",
        "title": "Chief Technology Officer",
        "seat": "cto",
        "country": "United States",
        "tier": "A",
        "score": "80",
        "heat": "2",
        "why_now": "expanding AI team",
        "cohort": "enterprise",
        "top_intent_score": "85",
        "lane": "generic",
        "verdict": "send",
        "suppression": "",
        "account_id": "acme-1",
    }
    base.update(kwargs)
    return base


def test_fully_eligible_account_is_included(tmp_path: Path) -> None:
    profile = "test-profile"
    _seed_master(tmp_path, profile, [_row(company="Acme", company_domain="acme.example")])

    queue = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=True)
    assert len(queue) == 1
    assert queue[0]["company"] == "Acme"
    assert queue.total_input == 1
    assert queue.counts == dict.fromkeys(EXCLUSION_REASONS, 0)
    assert len(queue) + sum(queue.counts.values()) == queue.total_input


def test_each_named_reason_excludes_account_and_is_counted(tmp_path: Path) -> None:
    profile = "test-profile"
    rows = [
        # 1. no-contact: empty email
        _row(
            company="No Contact Co",
            company_domain="nocontact.example",
            email="",
            account_id="acct-1",
        ),
        # 2. no-seat: empty seat and unresolvable title
        _row(
            company="No Seat Co",
            company_domain="noseat.example",
            email="b@noseat.example",
            seat="",
            title="",
            account_id="acct-2",
        ),
        # 3. no-country: empty country
        _row(
            company="No Country Co",
            company_domain="nocountry.example",
            email="c@nocountry.example",
            country="",
            account_id="acct-3",
        ),
        # 4. out-of-market: country blocked by target markets
        _row(
            company="Out Market Co",
            company_domain="outmarket.example",
            email="d@outmarket.example",
            country="France",
            account_id="acct-4",
        ),
        # 5. suppressed: suppression flag present
        _row(
            company="Suppressed Co",
            company_domain="suppressed.example",
            email="e@suppressed.example",
            suppression="dnc",
            account_id="acct-5",
        ),
        # 6. excluded-lane: routed into excluded lane
        _row(
            company="Excluded Lane Co",
            company_domain="excludedlane.example",
            email="f@excludedlane.example",
            lane="excluded",
            account_id="acct-6",
        ),
        # 7. dropped: verdict is drop
        _row(
            company="Dropped Co",
            company_domain="dropped.example",
            email="g@dropped.example",
            verdict="drop",
            account_id="acct-7",
        ),
    ]
    _seed_master(tmp_path, profile, rows)

    queue = accounts_needing_dossier(
        profile,
        content_root=tmp_path,
        eligible_only=True,
        target_markets=["United States"],
    )
    assert len(queue) == 0
    assert queue.total_input == 7
    assert queue.counts["no-contact"] == 1
    assert queue.counts["no-seat"] == 1
    assert queue.counts["no-country"] == 1
    assert queue.counts["out-of-market"] == 1
    assert queue.counts["suppressed"] == 1
    assert queue.counts["excluded-lane"] == 1
    assert queue.counts["dropped"] == 1
    # Conservation property: included + excluded = input
    assert len(queue) + sum(queue.counts.values()) == queue.total_input


def test_needs_research_stays_in_queue(tmp_path: Path) -> None:
    profile = "test-profile"
    _seed_master(
        tmp_path,
        profile,
        [
            _row(
                company="Needs Research",
                company_domain="needsresearch.example",
                why_now="",
                verdict="needs-research",
                lane_reason="needs-research",
                account_id="acct-nr",
            )
        ],
    )

    queue = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=True)
    assert len(queue) == 1
    assert queue[0]["company"] == "Needs Research"
    assert queue.counts["dropped"] == 0
    assert queue.total_input == 1


def test_multi_row_account_with_one_eligible_row_is_included(tmp_path: Path) -> None:
    profile = "test-profile"
    _seed_master(
        tmp_path,
        profile,
        [
            # First row has no email (no-contact)
            _row(
                company="Hybrid",
                company_domain="hybrid.example",
                email="",
                account_id="acct-hybrid",
            ),
            # Second row is fully eligible
            _row(
                company="Hybrid",
                company_domain="hybrid.example",
                email="valid@hybrid.example",
                account_id="acct-hybrid",
            ),
        ],
    )

    queue = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=True)
    assert len(queue) == 1
    assert queue[0]["company"] == "Hybrid"
    assert queue.total_input == 1
    assert sum(queue.counts.values()) == 0


def test_all_flag_restores_old_list(tmp_path: Path) -> None:
    profile = "test-profile"
    rows = [
        _row(
            company="Good",
            company_domain="good.example",
            email="good@good.example",
            account_id="acct-good",
        ),
        _row(company="Bad One", company_domain="bad1.example", email="", account_id="acct-bad1"),
        _row(
            company="Bad Two", company_domain="bad2.example", verdict="drop", account_id="acct-bad2"
        ),
    ]
    _seed_master(tmp_path, profile, rows)

    # eligible_only=True excludes 2 bad accounts
    queue_eligible = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=True)
    assert len(queue_eligible) == 1
    assert queue_eligible[0]["company"] == "Good"
    assert queue_eligible.total_input == 3
    assert queue_eligible.counts["no-contact"] == 1
    assert queue_eligible.counts["dropped"] == 1

    # eligible_only=False restores all accounts
    queue_all = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=False)
    assert len(queue_all) == 3
    assert [a["company"] for a in queue_all] == ["Good", "Bad One", "Bad Two"]
    assert queue_all.total_input == 3
    assert sum(queue_all.counts.values()) == 0


def test_ordering_heat_desc_score_desc_id_asc(tmp_path: Path) -> None:
    profile = "test-profile"
    rows = [
        _row(
            company="Alpha",
            company_domain="alpha.example",
            heat="1",
            score="95",
            account_id="id-001",
        ),
        _row(
            company="Bravo",
            company_domain="bravo.example",
            heat="3",
            score="70",
            account_id="id-002",
        ),
        _row(
            company="Charlie",
            company_domain="charlie.example",
            heat="2",
            score="90",
            account_id="id-003",
        ),
        _row(
            company="Delta",
            company_domain="delta.example",
            heat="2",
            score="90",
            account_id="id-004",
        ),
        _row(
            company="Echo", company_domain="echo.example", heat="2", score="80", account_id="id-005"
        ),
    ]
    _seed_master(tmp_path, profile, rows)

    # Order without limit:
    # 1. Bravo (heat 3, score 70)
    # 2. Charlie (heat 2, score 90, id-003)
    # 3. Delta (heat 2, score 90, id-004)
    # 4. Echo (heat 2, score 80)
    # 5. Alpha (heat 1, score 95)
    queue = accounts_needing_dossier(profile, content_root=tmp_path, eligible_only=True)
    expected_order = ["Bravo", "Charlie", "Delta", "Echo", "Alpha"]
    assert [a["company"] for a in queue] == expected_order

    # With limit=3:
    queue_limited = accounts_needing_dossier(
        profile, content_root=tmp_path, eligible_only=True, limit=3
    )
    assert [a["company"] for a in queue_limited] == ["Bravo", "Charlie", "Delta"]


def test_wave_stamp_present_on_records(tmp_path: Path) -> None:
    profile = "test-profile"
    _seed_master(tmp_path, profile, [_row(company="Wave Co", company_domain="waveco.example")])

    # With wave specified
    queue = accounts_needing_dossier(
        profile,
        content_root=tmp_path,
        eligible_only=True,
        wave="wave-2026-09-25",
    )
    assert len(queue) == 1
    assert queue[0]["researched_for_wave"] == "wave-2026-09-25"

    # Without wave specified
    queue_no_wave = accounts_needing_dossier(
        profile,
        content_root=tmp_path,
        eligible_only=True,
    )
    assert len(queue_no_wave) == 1
    assert queue_no_wave[0]["researched_for_wave"] == ""


def test_cli_accounts_needing_dossier(tmp_path: Path, monkeypatch, capsys) -> None:
    profile = "cli-test"
    rows = [
        _row(
            company="Eligible 1",
            company_domain="e1.example",
            heat="3",
            score="80",
            account_id="e-1",
        ),
        _row(
            company="Eligible 2",
            company_domain="e2.example",
            heat="2",
            score="90",
            account_id="e-2",
        ),
        _row(
            company="Ineligible Drop",
            company_domain="drop.example",
            verdict="drop",
            account_id="drop-1",
        ),
    ]
    _seed_master(tmp_path, profile, rows)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))

    # Test 1: CLI default (--eligible-only) with --wave and --limit
    rc = _cli(
        ["accounts-needing-dossier", "--profile", profile, "--wave", "wave-cli", "--limit", "1"]
    )
    assert rc == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["company"] == "Eligible 1"
    assert data[0]["researched_for_wave"] == "wave-cli"
    assert "research queue:" in err
    assert "1 dropped" in err

    # Test 2: CLI --all restores old list (including dropped)
    rc = _cli(["accounts-needing-dossier", "--profile", profile, "--all"])
    assert rc == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert len(data) == 3
    assert {d["company"] for d in data} == {"Eligible 1", "Eligible 2", "Ineligible Drop"}

    # Test 3: CLI --counts outputs structured report
    rc = _cli(["accounts-needing-dossier", "--profile", profile, "--counts"])
    assert rc == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["total_input"] == 3
    assert data["included"] == 2
    assert data["excluded"] == 1
    assert data["counts"]["dropped"] == 1
    assert len(data["accounts"]) == 2
