"""Tests for gtm_core.prospects_dashboard — the self-refreshing status page."""

from __future__ import annotations

import csv
import json

from gtm_core import prospects_consolidate as pc
from gtm_core import prospects_dashboard as pd


def _write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _seed(tmp_path, profile="acme"):
    pdir = tmp_path / profile / "prospects"
    # One high-confidence (ready) and one no-signal (needs-verification) person.
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Company Domain Name", "Email Status"],
        [
            [
                "Ada",
                "Lovelace",
                "ada@analytical.com",
                "Analytical Engine",
                "analytical.com",
                "RocketReach A",
            ],
            ["Grace", "Hopper", "grace@compiler.com", "Compiler Inc", "compiler.com", ""],
        ],
    )
    # Account layer: two companies with no email; one already has a person (Ada).
    _write_csv(
        pdir / "imports" / "bulk-net-new.csv",
        ["business_name", "business_website", "business_domain"],
        [
            ["Analytical Engine", "https://analytical.com", "analytical.com"],  # enriched
            ["Backlog Corp", "https://backlogco.example", "backlogco.example"],  # backlog
        ],
    )
    return pdir


def test_status_model_counts_both_layers(tmp_path):
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path)
    f = status["funnel"]
    a = status["accounts"]

    assert f["master_total"] == 2
    assert f["ready"] == 1  # Ada, RocketReach A
    assert f["needs_verification"] == 1  # Grace, no signal

    assert a["unique_accounts"] == 2
    assert a["enriched_accounts"] == 1  # Analytical Engine has Ada
    assert a["backlog_accounts"] == 1  # Backlog Corp has no person yet


def test_render_writes_html_and_json_with_real_numbers(tmp_path):
    """Since the 2026-08-18 merge, consolidation renders ``gtm.html`` and leaves a
    redirect at ``status.html``. The machine-readable ``status.json`` contract is
    unchanged — it is written from the same model, by the merged renderer."""
    from gtm_core import email_campaign_dashboard as gd

    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)  # auto-refreshes the dashboard

    json_path = pc._pool_dir(profile, content_root=tmp_path) / "status.json"
    assert json_path.exists()
    assert "url=../email_campaign_status.html" in pd.dashboard_path(
        profile, content_root=tmp_path
    ).read_text(encoding="utf-8")

    html_path = gd.dashboard_path(profile, content_root=tmp_path)
    assert html_path.exists()

    html = html_path.read_text(encoding="utf-8")
    assert "Email Campaign Status" in html
    assert "Backlog Corp".lower() not in html.lower()  # names aren't leaked into the page
    assert ">1<" in html or "ready to send" in html  # the ready number renders

    model = json.loads(json_path.read_text(encoding="utf-8"))
    assert model["funnel"]["ready"] == 1
    assert model["accounts"]["backlog_accounts"] == 1


def test_normalize_raw_saleshandy_payload():
    raw = {
        "sequenceId": "seq1",
        "sequenceName": "Test",
        "status": "active",
        "prospects": [
            {
                "total": "30",
                "contacted": "20",
                "upcoming": "8",
                "waiting": "2",
                "open": "12",
                "replied": "3",
                "meetingBooked": "1",
                "interested": "2",
                "meetingBookedDealValue": "5000",
                "interestedDealValue": "1000",
            }
        ],
        "emails": {
            "status": {
                "delivered": "19",
                "opened": "12",
                "replied": "3",
                "hardBounced": "1",
                "softBounced": "0",
                "blockBounced": "0",
            }
        },
    }
    n = pd._normalize_seq(raw)
    assert n["loaded"] == 30
    assert n["sent"] == 20
    assert n["pending"] == 10  # upcoming 8 + waiting 2
    assert n["delivered"] == 19
    assert n["replied"] == 3
    assert n["bounced"] == 1  # hard 1 + soft 0 + block 0
    assert n["meetings"] == 1
    assert n["deal_value"] == 6000  # 5000 + 1000


def test_sequence_stats_json_powers_performance_card(tmp_path):
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    stats = {
        "fetched": "2026-07-23",
        "sequences": [
            {
                "sequenceId": "seqX",
                "sequenceName": "Live One",
                "status": "active",
                "prospects": [
                    {
                        "total": "10",
                        "contacted": "10",
                        "open": "5",
                        "replied": "2",
                        "meetingBooked": "1",
                    }
                ],
                "emails": {"status": {"delivered": "10", "opened": "5", "replied": "2"}},
            }
        ],
    }
    (pc._pool_dir(profile, content_root=tmp_path) / "sequence-stats.json").write_text(
        json.dumps(stats), encoding="utf-8"
    )
    status = pd.build_status(profile, content_root=tmp_path)
    assert len(status["sequences"]) == 1
    assert status["sequences"][0]["replied"] == 2
    assert status["sequences"][0]["meetings"] == 1

    html = pd.render_html(status)
    assert "Live sequencer performance" in html
    assert "reply rate" in html


def test_sequence_state_is_optional(tmp_path):
    """The page renders whether or not the skill layer dropped sequence-state.json."""
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)
    status = pd.build_status(profile, content_root=tmp_path)
    assert status["sequence"] is None  # no file dropped -> graceful absence

    seq_file = pc._pool_dir(profile, content_root=tmp_path) / "sequence-state.json"
    seq_file.parent.mkdir(parents=True, exist_ok=True)
    seq_file.write_text(
        json.dumps({"id": "abc", "status": "paused", "loaded": 5}), encoding="utf-8"
    )
    status = pd.build_status(profile, content_root=tmp_path)
    assert status["sequence"]["loaded"] == 5


# --- lifecycle funnel --------------------------------------------------------


def _seed_lifecycle(tmp_path, profile="acme"):
    pdir = tmp_path / profile / "prospects"
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        [
            "First Name",
            "Last Name",
            "Email",
            "Company Name",
            "Company Domain Name",
            "GTM_Tier",
            "GTM_Score",
        ],
        [
            ["Dana", "Dossiered", "dana@dossiered.com", "Dossiered Co", "dossiered.com", "A", "50"],
            ["Nora", "Nodossier", "nora@nodossier.com", "Nodossier Co", "nodossier.com", "A", "45"],
            ["Bob", "Tierb", "bob@tierbco.com", "Tier B Co", "tierbco.com", "B", "10"],
        ],
    )
    # Backlog: two accounts already ICP-scored but not yet enriched (not in master-list).
    _write_csv(
        pdir / "sequences" / ".pool" / "enrichment-queue.csv",
        [
            "business_id",
            "company",
            "domain",
            "country",
            "segment",
            "industry",
            "employees_range",
            "revenue_range",
            "top_intent_score",
            "intent_topics",
            "cohort",
            "score",
        ],
        [
            [
                "b1",
                "Queued One",
                "queuedone.com",
                "United States",
                "startup",
                "",
                "",
                "",
                "0",
                "",
                "",
                "20",
            ],
            [
                "b2",
                "Queued Two",
                "queuedtwo.com",
                "United States",
                "startup",
                "",
                "",
                "",
                "0",
                "",
                "",
                "15",
            ],
        ],
    )
    # Dossiered Co has a dossier + an outreach draft; Nodossier Co has neither.
    dossiered_dir = tmp_path / profile / "accounts" / "dossiered-co"
    dossiered_dir.mkdir(parents=True)
    (dossiered_dir / "account-dossier-dossiered-co-2026-01-01.docx").write_text("x")
    (dossiered_dir / "prospects-20260101-outreach-dossiered-co.md").write_text(
        "# Outreach Pack — Dossiered Co — 2026-01-01\n\nDraft body.\n"
    )
    return pdir


def test_lifecycle_funnel_counts(tmp_path):
    profile = "acme"
    _seed_lifecycle(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path)
    lc = status["lifecycle"]

    assert lc["enriched"] == 3  # Dana, Nora, Bob all have a name+email
    assert lc["scored"] == 3 + 2  # + the 2 still-queued, not-yet-enriched accounts
    assert lc["tier_a"] == 2  # Dana (Dossiered Co) + Nora (Nodossier Co); Bob is Tier B
    assert lc["dossier"] == 1  # only Dossiered Co has a dossier file
    assert lc["drafted"] == 1  # only Dossiered Co has an outreach draft


def test_lifecycle_card_renders_with_relabeled_email_confidence(tmp_path):
    profile = "acme"
    _seed_lifecycle(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path)
    html = pd.render_html(status)

    assert "Top-priority accounts" in html  # the Tier-A card, sales-stage naming
    assert "Email quality" in html  # the relabeled card heading (was "Email confidence")
    assert ">verified<" in html
    assert ">needs a check<" in html
    assert "ready to load" not in html.lower()  # old bare label string is gone
    assert "need verification" not in html.lower()
    assert "not tracked per-account yet" in html  # honest Loaded/Sent gap note

    # sample-file links for the Dossiered Co account (has both a dossier and a draft)
    assert "Browse the underlying files" in html
    assert "Dossiered Co" in html
    assert 'href="../accounts/dossiered-co/"' in html


def _write_latest(pdir, items):
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "latest.json").write_text(
        json.dumps({"kind": "prospects", "profile": "acme", "items": items}), encoding="utf-8"
    )


def test_discovery_counted_when_run_emitted_no_imports_batch(tmp_path):
    """A prospect run that resolved people directly writes latest.json but never an
    ``imports/`` batch. Counting only ``imports/`` reported that run's discovery as 0 —
    an impossible funnel (0 identified -> 12 qualified) on a pipeline that had run.
    """
    profile = "acme"
    pdir = tmp_path / profile / "prospects"
    _write_csv(
        pdir / "prospects-20260101-a-hubspot.csv",
        ["First Name", "Last Name", "Email", "Company Name", "Company Domain Name", "Email Status"],
        [["Ada", "Lovelace", "ada@analytical.com", "Analytical Engine", "analytical.com", "A"]],
    )
    # 3 scored accounts; only Analytical Engine ever got a contact resolved.
    _write_latest(
        pdir,
        [
            {"company": "Analytical Engine", "domain": "analytical.com", "tier": "A", "score": 16},
            {"company": "Difference Co", "domain": "difference.com", "tier": "A", "score": 15},
            {"company": "Tabulator Ltd", "domain": "tabulator.com", "tier": "B", "score": 8},
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)

    lc = pd.build_status(profile, content_root=tmp_path)["lifecycle"]

    assert lc["identified"] == 3  # all 3, not 0 — no imports/ batch exists
    assert lc["scored"] == 3
    assert lc["enriched"] == 1  # only Ada has a name+email
    assert lc["tier_a"] == 2  # both Tier-A accounts, incl. the un-enriched one
    assert lc["identified"] >= lc["scored"] >= lc["enriched"]


def test_identified_unions_imports_and_latest_without_double_counting(tmp_path):
    """Both discovery paths in play: the shared account must count once, and an
    account known only to ``imports/`` must still be counted."""
    profile = "acme"
    pdir = _seed(tmp_path, profile)  # imports/: Analytical Engine + Backlog Corp
    _write_latest(
        pdir,
        [
            {"company": "Analytical Engine", "domain": "analytical.com", "tier": "A", "score": 16},
            {"company": "Latest Only Inc", "domain": "latestonly.com", "tier": "B", "score": 9},
        ],
    )
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path)
    lc, a = status["lifecycle"], status["accounts"]

    # Analytical Engine + Backlog Corp + Latest Only Inc + Compiler Inc (master-only).
    assert lc["identified"] == 4
    assert a["unique_accounts"] == 2  # the imports/-scoped backlog keys stay narrow
    assert a["backlog_accounts"] == 1
