"""Tests for gtm_core.prospects_dashboard — the status MODEL. It renders nothing.

Until 2026-09-05 this module carried a second renderer over the same model, writing
``prospects/status-standalone.html``. Assertions that pinned that page's wording are gone
with it; what survives is the model, and the ``.pool/status.json`` contract the merged
renderer writes from it. ``lifecycle`` and ``cost_model`` reach no page today — they are
machine-readable-only, which the ``prospect`` skill relies on, so they are asserted there.
"""

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
    stub = pc._prospects_dir(profile, content_root=tmp_path) / "status.html"
    assert "url=../email_campaign_status.html" in stub.read_text(encoding="utf-8")

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

    # The one surviving renderer is what a reader opens, so the assertion follows it there.
    from gtm_core import email_campaign_dashboard as gd

    html = gd.render_html(gd.build_model(profile, tmp_path))
    assert "Email sequences" in html
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


def test_the_lifecycle_model_survives_into_status_json(tmp_path):
    """``lifecycle`` reaches no HTML page — it is a machine-readable-only field.

    It used to render as the standalone page's "Top-priority accounts" card, and that
    page is gone. The field is NOT dead: ``.pool/status.json`` is the whole ``status``
    dict, and the `prospect` skill tells the agent to read metrics from there rather
    than counting CSVs by hand. So the contract to hold is that consolidation writes it,
    not that some heading spells it. A future reader deleting `_lifecycle_funnel` because
    "nothing renders it" would break that skill step; this test is the tripwire.
    """
    profile = "acme"
    _seed_lifecycle(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)  # auto-refreshes the merged renderer

    on_disk = json.loads(
        (pc._pool_dir(profile, content_root=tmp_path) / "status.json").read_text(encoding="utf-8")
    )
    assert on_disk["lifecycle"] == pd.build_status(profile, content_root=tmp_path)["lifecycle"]
    assert on_disk["lifecycle"]["dossier"] == 1
    assert on_disk["cost_model"], "the cost model is part of the same machine-readable contract"


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


def test_the_gate_numbers_match_the_gate_itself(tmp_path):
    """The dashboard must audit the SAME population the enrollment gate audits.

    On 2026-09-01 it audited `.pool/master-list.csv` (the whole backlog) with a
    hand-rolled verdict filter that omitted the suppression check, and published
    17 errors / 30 warnings while `account_integrity --require-verdict send` on
    `ready-to-load.csv` reported 11 / 27. Two disagreeing sources for one question is
    the exact failure this dashboard exists to end, so pin them together.
    """
    import csv as _csv

    from gtm_core import account_integrity as ai
    from gtm_core.prospects_consolidate import ready_to_load_path

    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)

    status = pd.build_status(profile, content_root=tmp_path, profiles_root=tmp_path / "profiles")
    f = status["funnel"]

    # Recompute the same way the CLI does, through the same shared filter.
    ready = ready_to_load_path(profile, content_root=tmp_path)
    with ready.open(newline="", encoding="utf-8") as fh:
        reader = _csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    kept, vstats = ai.filter_by_verdict(rows, "send")
    audit = ai.audit_rows(
        kept,
        profile,
        tmp_path,
        tmp_path / "profiles",
        fieldnames=fieldnames,
    )

    assert f["gate_candidates"] == vstats.kept
    assert f["gate_errors"] == len(audit.errors)
    assert f["gate_warnings"] == len(audit.warnings)


def test_this_module_exports_no_writer(tmp_path):
    """One model, one renderer — enforced by absence, not by convention.

    Two writers used to race for `prospects/status.html`: the sweep left a redirect stub,
    then `python -m gtm_core.prospects_dashboard` replaced it with a full page, so the
    operator's bookmark changed meaning depending on which ran last. The fix in 2026-08
    was to give the second renderer its own filename, which only moved the problem: the
    page it wrote (`status-standalone.html`) was refreshed by nobody and free to disagree
    with the one an operator opens, and the `prospect` skill carried a paragraph warning
    the agent away from a command this repo still shipped.

    Retired 2026-09-05. The invariant is now structural: this module renders nothing and
    writes nothing, so it CANNOT drift from `email_campaign_dashboard` again.

    What this does NOT catch: a writer added under a name none of these patterns match,
    or one added to a module this test does not import. It is an export check, not a
    filesystem sandbox.
    """
    for gone in ("render_html", "render_dashboard", "dashboard_path", "legacy_dashboard_path"):
        assert not hasattr(pd, gone), (
            f"{gone} is back on gtm_core.prospects_dashboard. This module is the status "
            "MODEL; the one renderer is gtm_core.email_campaign_dashboard. Put it there."
        )
    assert not [n for n in dir(pd) if n.startswith(("render", "write"))]

    # And the retired page is not resurrected by a consolidation sweep.
    profile = "acme"
    _seed(tmp_path, profile)
    pc.consolidate(profile, content_root=tmp_path)
    prospects = pc._prospects_dir(profile, content_root=tmp_path)
    assert not (prospects / "status-standalone.html").exists()
    assert "url=../email_campaign_status.html" in (prospects / "status.html").read_text(
        encoding="utf-8"
    )
