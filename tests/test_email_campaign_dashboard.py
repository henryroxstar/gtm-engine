"""Tests for gtm_core.email_campaign_dashboard — the campaign status page.

Assertions deliberately pin the *reader-facing wording*, not just the data. The page's
whole purpose is that someone who has never opened the sequencer can read it, so a label
regressing to tool jargon is a real defect and should fail here.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc

CSV_HEADER = "first,last,email,title,company,company_domain,city,country,segment,tier,signal_clause,why_now,case_study,src,suppression,suppression_date\n"

SPEC = """# Sequence spec — demo

## 3. The copy

**Step 1 — Day 1** · Subject: `your first security review`

> Hi {{First Name}},
>
> {{Why Now}}.
>
> My read: the first enterprise security review will ask how {{Company}} proves which agent acted.
>
> Henry
"""


def _rows():
    return (
        "Ada,Byte,ada@acme.example,CISO,Acme,acme.example,SG,Singapore,enterprise,A,"
        "Acme launches an agent platform,,,,,\n"
        "Bo,Nyte,bo@acme.example,CTO,Borogove,boro.example,NY,United States,enterprise,A,"
        "Borogove runs an agent that files claims,,,,,\n"
        "Cy,Pher,cy@acme.example,Chief Information Officer,Cog,cog.example,NY,United States,"
        "enterprise,A,Cog raised a Series B,,,,,\n"
    )


def _seed(tmp_path, profile="acme", *, lint=True):
    seq = pc._prospects_dir(profile, tmp_path) / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    (seq / "list.csv").write_text(CSV_HEADER + _rows(), encoding="utf-8")
    (seq / "spec-demo-2026-08-18.md").write_text(SPEC, encoding="utf-8")
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "S1"\ntitle = "Demo"\ncsv = "list.csv"\n'
        'spec = "spec-demo-2026-08-18.md"\n',
        encoding="utf-8",
    )
    pool = pc._pool_dir(profile, tmp_path)
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "sequence-stats.json").write_text(
        json.dumps({"sequences": [{"id": "S1", "name": "Demo", "status": "paused", "sent": 0}]}),
        encoding="utf-8",
    )
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    if lint:
        (pool / "lint-S1.json").write_text(
            json.dumps(
                {
                    "sequence_id": "S1",
                    "verdict": "PASS",
                    "rows": 3,
                    "touches": 1,
                    "renders": 3,
                    "errors": 0,
                    "warnings": 1,
                    "by_rule": {"specificity": {"WARN": 1}},
                    "checks_run": {
                        "specificity": {"category": "substance", "protects": "generic filler"},
                        "persona-lead-mismatch": {
                            "category": "relevance",
                            "protects": "wrong seat's pain",
                        },
                        "subject-length": {
                            "category": "deliverability",
                            "protects": "truncated subject",
                        },
                    },
                    "ran_at": "2026-08-18T13:00:00Z",
                }
            ),
            encoding="utf-8",
        )
    return profile


def _page(tmp_path, profile="acme"):
    return gd.render_html(gd.build_model(profile, tmp_path))


def test_title_names_the_product(tmp_path):
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nproduct = "Acme Gateway"\n'
        'sequences = ["S1"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    page = _page(tmp_path, profile)
    assert "<title>Email Campaign Status — Acme Gateway</title>" in page
    assert "<h1>Email Campaign Status — Acme Gateway</h1>" in page


def test_title_falls_back_to_the_profile_without_a_product(tmp_path):
    """A tenant that never declared a product gets a usable title, not a dangling dash."""
    profile = _seed(tmp_path)
    assert f"Email Campaign Status — {profile}" in _page(tmp_path, profile)


def test_page_carries_the_five_tab_labels(tmp_path):
    profile = _seed(tmp_path)
    out = gd.render_dashboard(profile, tmp_path, stubs=False)
    page = out.read_text(encoding="utf-8")
    assert out.name == "email_campaign_status.html"
    import html as _h

    expected = ("Overview", "Accounts", "Emails", "Results", "Operator notes")
    assert tuple(label for _tid, label in gd.TABS) == expected
    for _tid, label in gd.TABS:
        assert f">{_h.escape(label)}</button>" in page


def test_no_unglossed_tool_jargon_in_reader_facing_copy(tmp_path):
    """The page is for someone who does not work the tooling. These words leaking into a
    heading or stat label is the exact failure the rewrite fixed."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    import re

    for tag in re.findall(r"<h1[^>]*>(.*?)</h1>|<h2[^>]*>(.*?)</h2>", page, re.S):
        text = re.sub(r"<[^>]+>", "", "".join(tag)).lower()
        for word in ("sequencer", "enrolled", "merge tag", "variant", "cell"):
            assert word not in text, f"jargon {word!r} in heading {text!r}"
    for label in re.findall(r'<div class="stat-label">(.*?)</div>', page):
        assert "enrol" not in label.lower()


def test_funnel_buckets_are_explained_not_just_counted(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Good to email now" in page
    assert "Address not confirmed" in page
    # The question that prompted this: is it research, qualification, or compliance?
    assert "not a research or qualification gap" in page
    assert "Off limits" in page


def test_country_split_is_shown(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Where they are" in page
    assert "United States" in page
    assert "Singapore" in page


def test_unknown_seats_are_explained_as_a_classifier_gap(tmp_path):
    """The honest answer to "why are so many unknown?" is that the resolver recognises only
    the seats it names — not that data is missing. The count it prints is `SEAT_COVERAGE`'s:
    the typed "seven … three" sat over a list of six (PS20 P1.7)."""
    profile = _seed(tmp_path)
    listing = pc._prospects_dir(profile, tmp_path) / "sequences" / "list.csv"
    with listing.open("a", encoding="utf-8") as fh:
        fh.write(
            "Quinn,Tide,quinn@tidewater.example,Chief Happiness Officer,Tidewater,"
            "tidewater.example,NY,United States,enterprise,A,Tidewater opened an office,,,,,\n"
        )
    page = _page(tmp_path, profile)
    assert "gap in our own classifier" in page
    # The actual unplaced title, named from the data. The typed "Chief Information Officer"
    # this replaced was not unplaced at all: the resolver has seated a CIO since 2026-08-20.
    assert "The largest group below is Chief Happiness Officer (1)." in page
    assert f'data-figure="seats-recognised">{len(gd.SEAT_COVERAGE)}<' in page


def test_every_subject_line_is_listed_with_reach(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Every subject line in the campaign" in page
    assert "your first security review" in page


def test_signal_shape_split_is_surfaced(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "What the opening line is about" in page
    assert "something happened (event)" in page
    assert "what they already do (capability)" in page


def test_quality_shows_all_checks_run_not_only_those_that_fired(tmp_path):
    """Showing only fired rules made a clean run look like a thin one."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "3 different checks" in page  # the catalogue size, not the 1 that fired
    assert "rendered emails" in page
    assert "deliverability" in page  # a category with no findings still listed


def test_missing_quality_record_reads_as_unchecked_not_clean(tmp_path):
    profile = _seed(tmp_path, lint=False)
    assert "No quality record on file" in _page(tmp_path, profile)


def test_learning_tab_shows_the_parameters_and_the_grid(tmp_path):
    """The hard-coded hypothesis card that led this tab is retired (PS20 P1.7 Rule B): a
    tenant's hypotheses render from its own manifest, under "Full experiment notes"."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "What varies, and by how much" in page
    assert "possible combinations" in page
    assert "The experiment, drawn" in page


def test_reconciliation_fires_when_snapshot_and_ledger_disagree(tmp_path):
    profile = _seed(tmp_path)
    model = gd.build_model(profile, tmp_path)
    assert model["reconciliation"]["ok"] is True
    assert "may be out of date" not in gd.render_html(model)

    (pc._pool_dir(profile, tmp_path) / "sequence-stats.json").write_text(
        json.dumps({"sequences": [{"id": "GHOST", "name": "?", "status": "paused"}]}),
        encoding="utf-8",
    )
    model = gd.build_model(profile, tmp_path)
    assert model["reconciliation"]["ok"] is False
    assert model["reconciliation"]["in_ledger_only"] == ["S1"]
    assert "may be out of date" in gd.render_html(model)


def test_not_sending_banner_while_everything_is_paused(tmp_path):
    """Asserted on the ops card itself, through its marked figures (PS20 T1.3). This used to
    pass only because the send tile also printed "0 of 9"."""
    from tests.contracts.test_dashboard_ps20_trust import _fig, _ops_card

    profile = _seed(tmp_path)
    card = _ops_card(_page(tmp_path, profile))
    assert _fig(card, "ops-heading") == "Nothing has been sent"
    assert _fig(card, "ops-contacted") == "0"
    assert _fig(card, "planned-emails") == "9"
    # Readable snapshot, nobody contacted, the campaign paused, and its copy unverified
    # since the check: the note says it is not cleared to start.
    assert "not cleared to start" in card


def test_stubs_redirect_every_retired_page(tmp_path):
    profile = _seed(tmp_path)
    gd.render_dashboard(profile, tmp_path, stubs=True)
    base = pc._prospects_dir(profile, tmp_path).parent
    assert "url=email_campaign_status.html" in (base / "campaigns.html").read_text("utf-8")
    assert "url=email_campaign_status.html" in (base / "gtm.html").read_text("utf-8")
    assert "url=../email_campaign_status.html" in (base / "prospects" / "status.html").read_text(
        "utf-8"
    )


def test_stubs_can_be_declined(tmp_path):
    profile = _seed(tmp_path)
    base = pc._prospects_dir(profile, tmp_path).parent
    (base / "campaigns.html").write_text("ORIGINAL", encoding="utf-8")
    gd.render_dashboard(profile, tmp_path, stubs=False)
    assert (base / "campaigns.html").read_text(encoding="utf-8") == "ORIGINAL"


def test_status_tab_is_first_and_shows_sending_and_prospecting(tmp_path):
    """ "Where things stand" is the landing question: is it sending, and is the pipeline
    that feeds it still running? Neither had a home on any page before."""
    from tests.contracts.dashboard_page import panel

    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert '<button class="tab on" data-t="overview">' in page
    assert page.index('data-t="overview"') < page.index('data-t="accounts"')
    ops = panel(page, "ops")
    assert "Email sequences" in ops
    assert "Finding new people" in ops


# ----------------------------------------------------------------- PS14: the status tiles


def _write_lane_state(tmp_path, profile, rows):
    evals = pc._prospects_dir(profile, tmp_path) / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    with (evals / "lanes-state.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_status_tiles_render_a_dash_when_the_router_has_never_run(tmp_path):
    """No `lanes-state.jsonl` at all must read as an honest refusal per tile — the same
    "—" plus a reason shape the sending-ceiling tile already uses — never a misleading zero
    that would read as "nothing is waiting" when the truth is "nobody has looked yet".
    Includes positive and negative controls (§R18)."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    # Derived from `prospect_status.LABELS`, never retyped: this page and the terminal
    # report read the same dict, and a copy here would let the two drift apart silently —
    # which is exactly how "Ready to send" survived on one surface after the other was
    # corrected (2026-09-23, P6 item 3).
    from gtm_core.prospect_status import LABELS

    for status in (
        "waiting_on_you",
        "ready_to_send",
        "being_fixed",
        "in_sending_tool",
        "not_emailing",
    ):
        assert LABELS[status] in page
    assert "run your prospecting first" in page
    assert "Needs an address" in page

    # Proof that tiles render "—" and NOT "0":
    # exactly 5 lane tiles must render "—" as their stat-value
    assert page.count('<div class="stat-value">—</div>') >= 5

    # Negative control (§R18): once lanes-state exists, the 5 lane tiles do NOT render "—"
    _write_lane_state(
        tmp_path,
        profile,
        [{"email": "ada@analytical.example", "lane": "personalised", "reason": "researcher-send"}],
    )
    page_with_state = _page(tmp_path, profile)
    assert "These 5 sum to" in page_with_state
    assert page_with_state.count('<div class="stat-value">—</div>') < 5


def test_status_tiles_sum_to_the_derived_total_and_hold_out_the_unmapped(tmp_path):
    """The five tiles must sum to a number DERIVED from the model, never typed (§R14), and
    a `(lane, reason)` pair `status_of` does not recognise must not blank the whole page."""
    profile = _seed(tmp_path)
    _write_lane_state(
        tmp_path,
        profile,
        [
            {"email": "a@x.example", "lane": "hold", "reason": "tier-a-generic"},
            {"email": "b@x.example", "lane": "personalised", "reason": "personalised"},
            {"email": "c@x.example", "lane": "repair", "reason": "repair"},
            {"email": "d@x.example", "lane": "excluded", "reason": "already-enrolled"},
            {"email": "e@x.example", "lane": "excluded", "reason": "optout"},
            # unmapped on purpose — a trigger `status_of` has never seen.
            {"email": "z@x.example", "lane": "hold", "reason": "not-a-real-trigger"},
        ],
    )
    model = gd.build_model(profile, tmp_path)
    assert model["prospect_status"]["total"] == 5
    assert model["prospect_status"]["unmapped"] == 1
    page = gd.render_html(model)
    assert "These 5 sum to <strong>5</strong>" in page
    # The terminal prints the unmapped record as an "Unrecognised" row inside ITS total, so
    # the page names the same label and the same whole-list figure rather than a private
    # "1 more row(s)" sentence that made the two totals disagree (5 here, 6 there).
    assert '<div class="stat-value">1</div><div class="stat-label">Unrecognised</div>' in page
    assert "with the 1 unrecognised, <strong>6</strong> people in the current list" in page


def test_needs_address_is_kept_out_of_the_five_tile_total(tmp_path):
    """A different, larger population — the account ledger, not the current routed list —
    and the page must say so rather than implying it is folded into the five-tile sum."""
    profile = _seed(tmp_path)
    _write_lane_state(
        tmp_path,
        profile,
        [{"email": "a@x.example", "lane": "personalised", "reason": "personalised"}],
    )
    (pc._prospects_dir(profile, tmp_path) / "latest.json").write_text(
        json.dumps(
            {
                "kind": "prospects",
                "profile": profile,
                "items": [
                    {
                        "account_id": "1",
                        "contact_name": "Ada Lovelace",
                        "contact_email": "",
                        "status": "new",
                    },
                    # Retired — a named contact with no address here does not need one.
                    {
                        "account_id": "2",
                        "contact_name": "Bo Nyte",
                        "contact_email": "",
                        "status": "disqualified",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    model = gd.build_model(profile, tmp_path)
    assert model["prospect_status"]["total"] == 1
    assert model["prospect_status"]["needs_address"] == 1
    page = gd.render_html(model)
    assert "A different population" in page
    assert "not part of the 1 above" in page


def test_status_column_appears_on_the_account_table_and_marks_seat_kind_technical(
    tmp_path,
):
    """PS14's Status column, end to end: joined by email onto the account table,
    with the existing machine columns moved behind the technical-detail toggle."""
    from tests.contracts.dashboard_page import panel

    profile = _seed(tmp_path)
    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "mine-hubspot.csv").write_text(
        "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"
        "Ada,L,ada@analytical.example,Analytical Engine,analytical.example,verified,A\n",
        encoding="utf-8",
    )
    (pros.parent / "plans" / "campaigns" / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n'
        'roster_globs = ["mine-hubspot.csv"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    _write_lane_state(
        tmp_path,
        profile,
        [{"email": "ada@analytical.example", "lane": "personalised", "reason": "personalised"}],
    )
    page = _page(tmp_path, profile)
    assert page.count("<th>Status</th>") == 1
    assert "<th>Status</th>" in panel(page, "accounts")
    from gtm_core.prospect_status import LABELS

    assert LABELS["ready_to_send"] in page
    assert "Ready to send" not in page, (
        "the routed count must not render under a name that can be read as finished — the "
        "checks that decide whether a row may go have not run at this point"
    )
    assert '<th class="tech">Tier</th>' in page
    assert '<th class="tech">Research verdict</th>' in page


def test_i9_machine_columns_hidden_or_relabeled_in_page_headers(tmp_path):
    """PS-R I9: Eight machine columns must not be default-visible as table headers:
    forecast Lane -> Audience, views_status State -> Status, Counted as -> How it is counted,
    views_who Seat (class="tech"), Signal -> Buying signal, Route -> Qualification path,
    views_what Flagged here -> Issues found, Blocking -> Blocking issues.
    """
    import re

    profile = _seed(tmp_path)
    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "mine-hubspot.csv").write_text(
        "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"
        "Ada,L,ada@analytical.example,Analytical Engine,analytical.example,verified,A\n",
        encoding="utf-8",
    )
    (pros.parent / "plans" / "campaigns" / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n'
        'roster_globs = ["mine-hubspot.csv"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    page = _page(tmp_path, profile)

    th_matches = re.findall(r"<th(\s+[^>]*)?>(.*?)</th>", page, re.DOTALL | re.IGNORECASE)
    default_visible_headers = [
        re.sub(r"<[^>]+>", "", text).strip()
        for attrs, text in th_matches
        if 'class="tech"' not in (attrs or "") and "class='tech'" not in (attrs or "")
    ]
    tech_headers = [
        re.sub(r"<[^>]+>", "", text).strip()
        for attrs, text in th_matches
        if 'class="tech"' in (attrs or "") or "class='tech'" in (attrs or "")
    ]

    banned_default = [
        "Lane",
        "Counted as",
        "State",
        "Seat",
        "Signal",
        "Route",
        "Flagged here",
        "Blocking",
    ]
    for col in banned_default:
        assert col not in default_visible_headers, (
            f"Machine column {col!r} is default-visible in table headers: {default_visible_headers}"
        )

    assert tech_headers == ["Tier", "How it was verified", "Research verdict"]


def test_i10_status_tiles_scope_note_on_campaign_page_and_provenance(tmp_path):
    """PS-R I10: On a campaign-scoped page, status card must render _pool_scope_note,
    and status tiles must declare their provenance token (status:<key>) resolved by resolve_src."""
    from gtm_core.email_campaign_dashboard import format as fmt
    from tests.contracts.test_dashboard_tile_provenance import check_tiles

    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n'
        'roster_globs = ["mine-hubspot.csv"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    _write_lane_state(
        tmp_path,
        profile,
        [{"email": "ada@analytical.example", "lane": "personalised", "reason": "personalised"}],
    )

    model = gd.build_model(profile, tmp_path)
    scoped = gd.scope_to_campaign(model, "c1")
    page = gd.render_html(scoped)

    assert "Profile-wide, not" in page
    assert "The prospect pool is shared" in page

    status_tiles = [t for t in fmt._tiles_recorded() if str(t.get("src", "")).startswith("status:")]
    assert len(status_tiles) >= 5, "Status tiles must declare status:<key> provenance"
    check_tiles(scoped, status_tiles)


@pytest.mark.parametrize("bad", ["not json", "null", "42"])
def test_a_malformed_lane_state_line_is_refused_never_quietly_skipped(tmp_path, bad):
    """Until 2026-09-21 these lines were skipped, so every lane-derived figure on the page came
    out short with nothing saying so — while the terminal block refused the same file."""
    from gtm_core.email_campaign_dashboard.cli import _cli
    from gtm_core.email_campaign_dashboard.model import (
        LaneStateUnreadable,
        prospect_status_model,
    )

    profile = _seed(tmp_path)
    state_file = tmp_path / profile / "prospects" / "evals" / "lanes-state.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        '{"email": "good@example.com", "lane": "personalised", "reason": "personalised"}\n'
        f"{bad}\n",
        encoding="utf-8",
    )
    with pytest.raises(LaneStateUnreadable):
        prospect_status_model(profile, tmp_path)
    page = tmp_path / profile / "email_campaign_status.html"
    before = page.read_text(encoding="utf-8") if page.exists() else None
    assert _cli(["--profile", profile, "--content-root", str(tmp_path), "--scope", "all"]) == 2
    after = page.read_text(encoding="utf-8") if page.exists() else None
    assert after == before, "a page must not be rewritten from a list that could not be read"


def test_m16_unmapped_status(tmp_path):
    """M16: an unmapped row is recorded as 'unmapped' in by_email and rendered as
    '<span class="pill warn" data-warn="unmapped">status unmapped</span>' — a warn that says
    why (PS20 P1.5)."""
    from gtm_core.email_campaign_dashboard.format import _row_status
    from gtm_core.email_campaign_dashboard.model import prospect_status_model

    profile = _seed(tmp_path)
    state_file = tmp_path / profile / "prospects" / "evals" / "lanes-state.jsonl"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        '{"email": "good@example.com", "lane": "personalised", "reason": "personalised"}\n'
        "\n"
        '{"email": "unmapped@example.com", "lane": "hold", "reason": "not-a-real-trigger"}\n',
        encoding="utf-8",
    )
    ps = prospect_status_model(profile, tmp_path)
    assert ps["available"] is True
    assert ps["unmapped"] == 1
    assert ps["by_email"]["good@example.com"] == "ready_to_send"
    assert ps["by_email"]["unmapped@example.com"] == "unmapped"

    m = {"prospect_status": ps}
    assert (
        _row_status(m, "unmapped@example.com")
        == '<span class="pill warn" data-warn="unmapped">status unmapped</span>'
    )
    from gtm_core.prospect_status import LABELS

    assert _row_status(m, "good@example.com") == LABELS["ready_to_send"]
    assert _row_status(m, "good@example.com") == "Sorted — not yet checked", (
        "the per-row cell is where this surface carries the routed-is-not-checked "
        "correction; it has no count to put a checked figure beside"
    )
    assert _row_status(m, "other@example.com") == '<span class="muted">not yet routed</span>'


def test_m17_ops_view_drifted_deduplication_by_sequence_id():
    """M17: views_ready._readiness_blocks deduplicates messages by sequence_id."""
    from gtm_core.email_campaign_dashboard.views_ready import _readiness_blocks

    m = {
        "campaigns": {
            "campaigns": [{"state": "staged", "targets": {"emails": 10}, "sequences": []}]
        },
        "status": {"sequences": []},
        "messages": [
            {"sequence_id": "seq1", "lint": {"drift": ["body changed"]}},
            {"sequence_id": "seq1", "lint": {"drift": ["body changed"]}},  # duplicate registration
            {"sequence_id": "seq2", "lint": {"drift": []}},
        ],
    }
    blocks = _readiness_blocks(m)
    assert "<strong>1 of 2 sequences</strong> were revised" in blocks["re-push"]


def test_m18_render_dashboard_syncs_tech_toggle_on_load(tmp_path):
    """M18: render.py calls syncTech on page load to restore checkbox state on reload."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "syncTech()" in page
    assert "techToggle.addEventListener('change', syncTech)" in page


def test_m19_prospect_status_card_no_state_diff_note(tmp_path):
    """M19: When available is False, the Needs-an-address card notes 'not part of the routed list'."""
    from gtm_core.email_campaign_dashboard.views_overview import _needs_address_block

    m = {
        "prospect_status": {
            "available": False,
            "counts": {},
            "total": 0,
            "unmapped": 0,
            "needs_address": 5,
        },
        "campaigns": {"campaigns": []},
    }
    html = _needs_address_block(m)
    assert "A different population — not part of the routed list." in html
    assert "not part of the 0 above" not in html


def test_good_to_email_says_what_it_does_not_mean(tmp_path):
    """The bucket guarantees deliverability, market and suppression — NOT that the account
    was ICP-scored, that intent was found, that a dossier exists, or that copy was drafted.
    Claiming otherwise would be a confident lie on a CRO-facing page."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "does NOT mean" in page
    for claim in ("scored against the ICP", "buying-intent signals", "dossier", "drafted"):
        assert claim in page


def test_off_limits_names_out_of_market_as_a_reason(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "outside the jurisdictions" in page


def test_market_split_counts_out_of_market_and_unknown_country(tmp_path, monkeypatch):
    from gtm_core import email_campaign_dashboard as m

    monkeypatch.setattr(
        "gtm_core.prospects_consolidate._resolve_market_gate",
        lambda profile, strict: type(
            "G", (), {"markets": ["Singapore"], "blocks": staticmethod(lambda c: c != "Singapore")}
        )(),
    )
    pool = pc._pool_dir("acme", tmp_path)
    pool.mkdir(parents=True, exist_ok=True)
    (pool / "master-list.csv").write_text(
        "email,country\na@x.example,Singapore\nb@x.example,Brazil\nc@x.example,\n",
        encoding="utf-8",
    )
    out = m.market_split("acme", tmp_path)
    assert out["out_of_market"] == 1
    # A row with no country is NOT blocked — the gate can only block a country it can read,
    # so these stay mailable and must be reported separately rather than folded in.
    assert out["unknown_country"] == 1


def test_prospecting_runs_are_read_newest_first(tmp_path):
    from gtm_core import email_campaign_dashboard as m

    profile = _seed(tmp_path)
    hist = tmp_path / profile / "history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(
        json.dumps(
            {"event": "prospect_run", "run_id": "old", "total": 5, "ts": "2026-01-01T00:00:00Z"}
        )
        + "\n"
        + json.dumps(
            {
                "event": "prospect_enrich",
                "run_id": "new",
                "contacts": 9,
                "ts": "2026-03-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    runs = m.prospecting_runs(profile, tmp_path)
    assert [r["run_id"] for r in runs] == ["new", "old"]
    assert runs[0]["kind"] == "enrichment" and runs[0]["found"] == 9


def test_goals_are_stated_on_qualified_people_not_everyone_loaded(tmp_path):
    """ "Unclear" is not a soft pass — list_fit treats an unread title as not-proven, so
    those rows must be excluded from the headline number rather than quietly counted."""
    profile = _seed(tmp_path)
    model = gd.build_model(profile, tmp_path)
    sup = model["supply"]
    assert sup["qualified"] + sup["unclear"] + sup["not_qualified"] == sup["total"]
    # The number goals are set on is role-fit AND not suppressed — never the raw
    # qualified count, which credits the campaign with people already ruled unmailable.
    assert sup["qualified_sendable"] <= sup["qualified"]
    assert sup["sendable"] + sup["suppressed"] == sup["total"]
    page = gd.render_html(model)
    assert "people we will actually email" in page
    assert "How many actually count" in page
    assert "job title unread" in page
    assert "sit the opening-line check" in page and "and the job-title check" in page


def test_reply_rate_benchmarks_are_sourced_and_caveated(tmp_path):
    """Every band must carry a link, a stated denominator, and the three warnings —
    a benchmark quoted without them is worse than none."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Reply rate" in page
    for bm in gd.BENCHMARKS:
        assert bm["url"].startswith("https://")
        assert bm["basis"]
        assert bm["label"] in page
        assert bm["url"] in page
    assert "denominator is not standardised" in page
    assert "cold-email vendor" in page
    # The link-building figure is excluded on purpose and the page says why.
    assert "category error" in page
    assert "8.5%" in page


def test_the_target_sits_beside_every_benchmark_with_no_fit_claim(tmp_path):
    """Which published figure fits a campaign's audience is a claim about the tenant's market,
    and engine code makes none (PS20 P1.7 Rule B). The page named one benchmark "the
    published figure closest to who this campaign actually writes to" and judged the target
    against it; now the target is drawn beside every sourced figure and the reader compares."""
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\nreply_rate = 0.03\n",
        encoding="utf-8",
    )
    page = _page(tmp_path, profile)
    card = page.split("<h2>Reply rate</h2>", 1)[1].split("</details>", 1)[0]
    assert '<div class="blabel">our target</div>' in card and "3.0%" in card
    for bm in gd.BENCHMARKS:
        assert f'<div class="blabel">{bm["label"]}</div>' in card
    for claim in ("closest to who", "closest published comparator", "stretch", "floor"):
        assert claim not in card, claim


def test_reply_target_comes_from_the_manifest_not_a_hardcoded_number(tmp_path):
    """Deriving the rate from two rounded integers reported 3.1% for a goal set at 3.0%."""
    from gtm_core import email_campaign_dashboard as m

    assert m._rate_of({"reply_rate": 0.03, "replies": 13, "prospects": 424}) == 0.03
    # Falls back to replies/prospects only when no rate is declared.
    assert round(m._rate_of({"replies": 13, "prospects": 424}), 4) == 0.0307
    assert m._rate_of({}) == 0.0

    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\nreply_rate = 0.03\nreplies = 1\nprospects = 3\n",
        encoding="utf-8",
    )
    assert "target 3.0%" in _page(tmp_path, profile)


def _with_pool(tmp_path, profile):
    (pc._prospects_dir(profile, tmp_path) / "latest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "domain": "acme.example",
                        "score": 40,
                        "intent_feeds": ["vibe-topic"],
                        "intent_topics": [{"topic": "agentic ai", "score": 70}],
                        "qualification_path": "intent-only-relaxed",
                        "new_in_role": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_icp_score_section_leads_with_coverage(tmp_path):
    profile = _seed(tmp_path)
    _with_pool(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "How well they fit the ideal customer" in page
    assert "We can only answer this for" in page
    # Untraceable people are "no score", never a low score.
    assert "it is no score" in page
    assert "statement about the employer" in page


def test_absent_intent_feeds_are_shown_as_absent(tmp_path):
    profile = _seed(tmp_path)
    _with_pool(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "What buying-intent signals we actually hold" in page
    assert "RocketReach hiring signal" in page  # a feed with zero coverage, still listed
    assert "not on this list" in page
    assert "New in role" in page or "new in role" in page.lower()


def test_topic_distribution_and_qualification_route_are_shown(tmp_path):
    profile = _seed(tmp_path)
    _with_pool(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "Which topics they are researching" in page
    assert "agentic ai" in page
    assert "How each one qualified" in page
    assert "intent-only-relaxed" in page


def test_page_renders_for_a_profile_with_no_prospect_pool(tmp_path):
    """A zero-coverage profile must render, not raise.

    The intent section formats a percentage; doing arithmetic on that formatted string
    crashed on an empty profile, and because consolidate() wraps the render in a
    try/except the only symptom was a page that silently never appeared.
    """
    profile = _seed(tmp_path)  # no latest.json written
    page = _page(tmp_path, profile)
    assert "How well they fit the ideal customer" in page


# --------------------------------------------------------------------------- operator split
# The page has two readers with opposite needs: the one who reads it for the numbers, and the
# one who presses the buttons. Everything below pins that split — the mechanics are reachable,
# but they never displace the figures, and the *state* they qualify never leaves the badge.

SPEC_THREADED = """# Sequence spec — demo

## 3. The copy

**Step 1 — Day 1** · Subject: `your first security review`

> Hi {{First Name}},
>
> {{Why Now}}.
>
> My read: the first enterprise security review will ask how {{Company}} proves which agent acted.
>
> Henry

**Step 2 — Day 4** (same thread, no subject)

> Hi {{First Name}},
>
> One thing I left out of the note below.
>
> Worth the two page version?
>
> Henry

**Step 3 — Day 9** · Subject: `the procurement question`

> Hi {{First Name}},
>
> Closing the loop on this.
>
> Henry
"""

WINDOW = """
[window]
daily_cap = 90
mailboxes = 9
touches = 3
capacity_note = "Nine mailboxes are already warmed"
caveat = "assumes no pauses and no holidays"
"""


def _seed_operational(tmp_path, profile="acme", *, staged="2026-08-17T09:00:00Z"):
    """A seed with the two things the default fixture has no opinion on: a sending window,
    and a record of when the sequence was last pushed."""
    _seed(tmp_path, profile)
    seq = pc._prospects_dir(profile, tmp_path) / "sequences"
    (seq / "spec-demo-2026-08-18.md").write_text(SPEC_THREADED, encoding="utf-8")
    base = pc._prospects_dir(profile, tmp_path).parent
    (base / "plans" / "campaigns" / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\n" + WINDOW,
        encoding="utf-8",
    )
    (base / "history.jsonl").write_text(
        json.dumps(
            {
                "event": "sequence_staged",
                "sequence_id": "S1",
                "campaign": "c1",
                "ts": staged,
                "enrolled": 3,
                "touches": 3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return profile


def test_operator_notes_is_a_panel_of_its_own(tmp_path):
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    import html as _h

    assert f">{_h.escape('Operator notes')}</button>" in page
    assert 'id="p-ops"' in page


def test_repush_detail_lives_in_operator_notes_not_at_the_top(tmp_path):
    """The mechanics moved; they did not vanish. The top of the page is for the numbers."""
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "Re-push before anyone starts a sequence" in page
    assert "The reviewed copy is not what is loaded" not in page
    ops = page.split('id="p-ops"')[1].split("</section>")[0]
    assert "Re-push before anyone starts a sequence" in ops
    assert "not cleared to start" in ops


def test_run_state_is_not_a_banner_over_every_panel(tmp_path):
    """A page-level banner renders above the tabs, so it shows on all five. Only a warning
    about the figures themselves earns that; "nothing has been sent" is a fact about the run,
    and repeating it over Who / What / Learn is noise the reader cannot dismiss."""
    from tests.contracts.dashboard_page import panel

    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    head = page.split('<section id="p-', 1)[0]
    assert "Nothing has been sent" not in head
    assert "Re-push" not in head
    assert "Nothing has been sent" in panel(page, "ops")


def test_the_overview_and_results_still_state_that_nothing_has_gone_out(tmp_path):
    """Moving the banner must not cost the reader the fact. The first tile carries it, as a
    count of PEOPLE contacted read off its marked figure (PS20 T1.4) — "emails sent" also
    appears in the benchmark notes, so a substring check here passed by accident."""
    from tests.contracts.dashboard_page import panel
    from tests.contracts.test_dashboard_ps20_trust import _fig, _tile

    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    overview = panel(page, "overview")
    results = panel(page, "results")
    assert _fig(overview, "campaign-contacted-c1") == "0"
    assert _fig(results, "contacted-current") == "0"
    assert '<div class="stat-label">people contacted</div>' in _tile(results, "contacted-current")


def test_a_figures_warning_does_stay_above_every_panel(tmp_path):
    """The one banner that keeps page scope: it says the numbers on every panel are suspect,
    so a reader who never opens Operator notes still sees it."""
    profile = _seed_operational(tmp_path)
    pool = pc._pool_dir(profile, tmp_path)
    stats = json.loads((pool / "sequence-stats.json").read_text(encoding="utf-8"))
    stats["sequences"].append({"id": "GHOST", "name": "Ghost", "status": "paused", "sent": 0})
    (pool / "sequence-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    head = _page(tmp_path, profile).split('<section id="p-')[0]
    assert "These numbers may be out of date" in head


def test_a_passing_check_never_reads_as_ready_while_the_loaded_copy_differs(tmp_path):
    """The state stays with the badge it qualifies. Moving it out with the procedure would
    leave a green PASS as the only thing next to copy that is not what would send."""
    from tests.contracts.dashboard_page import panel

    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    emails = panel(page, "emails")
    assert "not cleared to start" in emails
    assert "PASS" in emails


def _fingerprint_the_lint_record(tmp_path, profile="acme"):
    """Point the quality record at the files it read and stamp their digests, so the
    freshness check can actually clear. Without this a record is only ever *unproven*."""
    import hashlib

    seq = pc._prospects_dir(profile, tmp_path) / "sequences"
    pool = pc._pool_dir(profile, tmp_path)
    rec = json.loads((pool / "lint-S1.json").read_text(encoding="utf-8"))
    for key, digest_key, name in (
        ("spec", "spec_sha256", "spec-demo-2026-08-18.md"),
        ("csv", "csv_sha256", "list.csv"),
    ):
        src = seq / name
        rec[key] = str(src)
        rec[digest_key] = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    (pool / "lint-S1.json").write_text(json.dumps(rec), encoding="utf-8")


def test_nothing_outstanding_when_the_loaded_copy_is_the_checked_copy(tmp_path):
    """No drift must not render as an empty panel — silence there reads as "no notes",
    which is the same shape as "never checked"."""
    profile = _seed_operational(tmp_path, staged="2026-08-20T09:00:00Z")
    _fingerprint_the_lint_record(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "Nothing outstanding" in page
    assert "Re-push before anyone starts a sequence" not in page


def test_an_unfingerprinted_record_is_drift_not_silence(tmp_path):
    """A record that predates fingerprinting cannot prove it describes what is loaded.
    Unproven and proven-current must not render the same."""
    profile = _seed_operational(tmp_path, staged="2026-08-20T09:00:00Z")
    page = _page(tmp_path, profile)
    assert "Re-push before anyone starts a sequence" in page
    assert "predates fingerprinting" in page


def test_forecast_is_arithmetic_over_the_list_that_was_checked(tmp_path):
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "How long it runs, once it starts" in page
    # 3 people x 3 emails = 9 emails; at 90/day that is one day of sending.
    assert "the list that was checked, once it is loaded" in page
    assert ">9</td>" in page or ">9<" in page


def test_forecast_adds_the_sequence_tail_not_just_the_sending_days(tmp_path):
    """The run is not over when the last email is *started*. Day 1 to day 9 is 8 calendar
    days, ~6 working days, and the last person enrolled still has to live through it."""
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "1 + 6" in page
    assert "7 working days" in page


def test_ceiling_is_shown_with_the_arithmetic_that_sets_it(tmp_path):
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "9 mailboxes at 10 a day each" in page
    assert "The list is not the constraint" in page
    assert "30 new people a working day" in page


def test_only_one_duration_is_stated_on_the_page(tmp_path):
    """Two durations computed on different denominators is how "about five weeks" outlives
    the list it was measured on."""
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "the full run takes" not in page


def test_a_same_thread_reply_is_still_counted_as_an_email(tmp_path):
    """Step 2 carries no subject line. Parsing only subject-bearing steps silently listed
    two emails per sequence while every count beside it said three."""
    profile = _seed_operational(tmp_path)
    model = gd.build_model(profile, tmp_path)
    steps = [c["step"] for c in model["messages"][0]["copy"]]
    assert steps == [1, 2, 3]
    page = gd.render_html(model)
    assert "reply inside the first email&#x27;s thread" in page or "no new subject" in page


def test_a_threaded_reply_is_not_counted_as_a_subject_line(tmp_path):
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    assert "carry a subject of their own" in page
    # One threaded email in this fixture, so the sentence is singular (PS20 2.6a).
    assert "arrives as a reply inside the first email" in page


# --- the 1:1 pack lane -----------------------------------------------------------------
# Invisible to this page until 2026-09-04: a pack is in no `cells.toml`, has no sequence id
# and enrols nobody, so campaigns / cells / outcomes all returned nothing for it and the page
# silently described the merge lane only. Fixtures are fictional (§R9).


def _pack(root, account, name, *, capability="identity", rules="2026-09-04"):
    d = root / "p" / "accounts" / account
    d.mkdir(parents=True, exist_ok=True)
    body = f"# Outreach Pack: {account.title()} (2026-09-04)\n\nRules-Version: {rules}\n\n"
    if capability:
        body += f"**Capability:** {capability}\n"
    (d / name).write_text(body, encoding="utf-8")


def test_packs_model_reads_the_capability_each_pack_declares(tmp_path):
    from gtm_core.email_campaign_dashboard.model import packs_model

    (tmp_path / "p" / "prospects").mkdir(parents=True)
    _pack(tmp_path, "halden", "prospects-20260904-outreach-halden.md", capability="identity")
    _pack(tmp_path, "borea", "prospects-20260904-outreach-borea.md", capability="payments")
    m = packs_model("p", tmp_path)
    assert {r["account"] for r in m["packs"]} == {"halden", "borea"}
    assert dict(m["spread"]) == {"identity": 1, "payments": 1}
    assert m["undeclared"] == 0


def test_a_pack_predating_the_field_is_not_counted_undeclared(tmp_path):
    """930 historical packs have no `capability:` because the field did not exist. Counting
    them as undeclared would report a wrong finding with total confidence."""
    from gtm_core.email_campaign_dashboard.model import packs_model

    (tmp_path / "p" / "prospects").mkdir(parents=True)
    _pack(tmp_path, "halden", "prospects-20260719-outreach-halden.md", capability="")
    _pack(tmp_path, "borea", "prospects-20260904-outreach-borea.md", capability="payments")
    m = packs_model("p", tmp_path)
    assert [r["account"] for r in m["packs"]] == ["borea"], "only the capability era is listed"
    assert m["undeclared"] == 0
    assert m["legacy"] == 1


def test_a_pack_in_the_capability_era_with_no_declaration_is_undeclared(tmp_path):
    from gtm_core.email_campaign_dashboard.model import packs_model

    (tmp_path / "p" / "prospects").mkdir(parents=True)
    _pack(tmp_path, "halden", "prospects-20260904-outreach-halden.md", capability="")
    m = packs_model("p", tmp_path)
    assert m["undeclared"] == 1 and m["legacy"] == 0


def test_one_capability_parser_serves_the_page_and_the_coverage_audit(tmp_path):
    """Two regexes for one declaration would drift apart silently."""
    from gtm_core.email_campaign_dashboard import model as dash
    from gtm_core.hook_coverage.declared import _CAPABILITY_RE

    src = dash.packs_model.__doc__ or ""
    assert "hook_coverage" in src, "the shared parser must stay documented as shared"
    assert _CAPABILITY_RE.search("**Capability:** payments").group("value").strip() == "payments"


def test_no_accounts_dir_yields_no_packs_not_an_error(tmp_path):
    from gtm_core.email_campaign_dashboard.model import packs_model

    (tmp_path / "p" / "prospects").mkdir(parents=True)
    assert packs_model("p", tmp_path)["packs"] == []


# --- campaign scoping ------------------------------------------------------------------
# Until 2026-09-04 this page only rolled up the whole profile, so a run with one staged
# sequence and six 1:1 packs rendered headline tiles reading "0 of 990 emails / 51 people /
# 3 sequences" — every figure belonging to a DIFFERENT campaign.


def _model():
    return {
        "profile": "p",
        "status": {
            "sequences": [
                {"id": "AAA", "loaded": 300, "sent": 17},
                {"id": "BBB", "loaded": 51, "sent": 0},
            ],
            "funnel": {"master_total": 1193},
        },
        "campaigns": {
            "campaigns": [
                {"slug": "other-20260718", "sequences": [{"sequence_id": "AAA"}], "archived": []},
                {
                    "slug": "mine-20260904",
                    "sequences": [
                        {"sequence_id": "BBB", "enrolled": 51, "status": "paused", "spec": "x.md"}
                    ],
                    "archived": [],
                },
            ],
            "unlinked_sequences": [{"sequence_id": "ZZZ"}],
        },
        "messages": [{"sequence_id": "AAA"}, {"sequence_id": "BBB"}],
        "packs": {
            "packs": [
                {"account": "a", "date": "20260904", "capability": "identity", "rules_version": ""},
                {"account": "b", "date": "20260719", "capability": "", "rules_version": ""},
            ],
            "spread": [("identity", 1)],
            "undeclared": 1,
            "legacy": 0,
            "since": "20260904",
        },
    }


def test_scoping_drops_every_other_campaigns_numbers():
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "mine-20260904")
    assert [s["id"] for s in m["status"]["sequences"]] == ["BBB"]
    assert [c["slug"] for c in m["campaigns"]["campaigns"]] == ["mine-20260904"]
    assert [x["sequence_id"] for x in m["messages"]] == ["BBB"]
    assert m["campaigns"]["unlinked_sequences"] == []
    assert m["campaign_scope"] == "mine-20260904"


def test_scoping_keeps_only_this_campaigns_packs():
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "mine-20260904")
    assert [r["account"] for r in m["packs"]["packs"]] == ["a"]
    assert m["packs"]["undeclared"] == 0, "the other campaign's undeclared pack is not ours"


def test_a_ledger_sequence_absent_from_the_snapshot_still_counts():
    """The live snapshot predated staging, so filtering it to this campaign filtered it to
    nothing and the tiles read 0 of 0 for a sequence with 4 people in it."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = _model()
    m["status"]["sequences"] = [{"id": "AAA", "loaded": 300, "sent": 17}]  # BBB missing
    m = scope_to_campaign(m, "mine-20260904")
    (seq,) = m["status"]["sequences"]
    assert seq["id"] == "BBB" and seq["loaded"] == 51 and seq["from_ledger"] is True
    assert seq["sent"] == 0, "the ledger asserts enrolment, never sends"


def test_the_pool_wide_funnel_is_not_relabelled_as_one_campaigns():
    """A prospect pool is shared. Scoping it would trade one wrong number for another."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "mine-20260904")
    assert m["status"]["funnel"]["master_total"] == 1193


def test_an_unknown_campaign_does_not_silently_return_the_rollup():
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "no-such-campaign-20260101")
    assert "campaign_scope" not in m, "render_dashboard raises on this rather than mislabelling"


def test_a_scoped_page_labels_every_pool_wide_block():
    """The pool is shared, so it is not scoped — but rendering it unlabelled under a
    campaign's title is how "731 people, goals are set on this number" appeared on a page
    whose goals are 4 and 8. `scope_to_campaign`'s docstring promised this note from the
    start and nothing rendered it."""
    from gtm_core.email_campaign_dashboard.format import _pool_scope_note

    assert _pool_scope_note({}, "The pool") == "", "no note on the profile-wide page"
    note = _pool_scope_note({"campaign_scope": "mine-20260904"}, "The pool")
    assert "Profile-wide, not this campaign" in note and "mine-20260904" in note


def test_packs_from_another_campaign_are_counted_not_dropped():
    """A pack that exists and appears in no total is how a lane goes missing."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = _model()
    m["packs"]["packs"].append(
        {"account": "c", "date": "20260905", "capability": "payments", "rules_version": ""}
    )
    out = scope_to_campaign(m, "mine-20260904")
    assert [r["account"] for r in out["packs"]["packs"]] == ["a"]
    assert out["packs"]["other_campaigns"] == 2, "the 20260719 and 20260905 packs"


# --- the campaign's own roster ----------------------------------------------------------
# Fictional fixtures (§R9).

_HDR = "Company Name,First Name,Email,Job Title,GTM_Tier,GTM_Verdict,GTM_Why_Now,GTM_Signal_Source_URL\n"


def _exports(tmp_path, *files):
    d = tmp_path / "p" / "prospects"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files:
        (d / name).write_text(_HDR + body, encoding="utf-8")
    return d


def _src(*globs, campaign="c1", product="Brightpath Health"):
    """A `RosterSource` for the direct `roster_model` tests.

    The signature took a bare glob list until 2026-09-10. It takes sources now because a row
    has to know which campaign contributed it — a bare list cannot say, and a row that cannot
    say is unfilterable by campaign or product.
    """
    from gtm_core.email_campaign_dashboard.roster import RosterSource

    return RosterSource(campaign, product, tuple(globs))


def test_roster_folds_by_company_keeping_the_richest_row(tmp_path):
    """A discovery pass then an enrichment pass. Taking the first would report the
    pre-enrichment snapshot: on 2026-09-04 that was 18 accounts with 2 addresses, against 26
    with 23 once all four exports were folded."""
    from gtm_core.email_campaign_dashboard.model import roster_model

    _exports(
        tmp_path,
        ("a-hubspot.csv", "Halden,,,CTO,B,,,\nBorea,,,CEO,A,,,\n"),
        ("b-hubspot.csv", "Halden,Dana,dana@halden.example,CTO,B,send,shipped a thing,https://x\n"),
    )
    r = roster_model("p", [_src("*-hubspot.csv")], tmp_path)
    assert r["accounts"] == 2, "folded by company, not summed"
    assert r["contact_verified"] == 1 and r["named_seat"] == 1
    assert r["signal"] == 1 and r["signal_sourced"] == 1
    assert dict(r["tiers"]) == {"B": 1, "A": 1}


def test_roster_is_empty_when_the_campaign_declares_none(tmp_path):
    """A campaign with no declared roster must not silently borrow the pool."""
    from gtm_core.email_campaign_dashboard.model import roster_model

    _exports(tmp_path, ("a-hubspot.csv", "Halden,,,CTO,B,,,\n"))
    assert roster_model("p", [], tmp_path)["accounts"] == 0


def test_tier_a_sorts_first_in_the_rendered_rows(tmp_path):
    from gtm_core.email_campaign_dashboard.model import roster_model

    _exports(tmp_path, ("a-hubspot.csv", "Zeta,,,CTO,B,,,\nAlpha,,,CEO,A,,,\n"))
    rows = roster_model("p", [_src("*-hubspot.csv")], tmp_path)["rows"]
    assert [x["tier"] for x in rows] == ["A", "B"]
    # Every row inherits its source's identity — the whole point of RosterSource.
    assert {x["campaign"] for x in rows} == {"c1"}
    assert {x["product"] for x in rows} == {"Brightpath Health"}


def test_a_pool_wide_block_is_removed_on_a_scoped_page_not_labelled():
    """'0 different subject lines across 859 people' is two wrong numbers wearing a caveat."""
    from gtm_core.email_campaign_dashboard.format import _scoped_out

    assert _scoped_out({}, "Card", "why") == "", "the profile-wide page keeps the block"
    out = _scoped_out({"campaign_scope": "c-20260904"}, "Card", "because reasons")
    # "scoped page", not "campaign page": since --scope arrived a page can be about the
    # open set or several named campaigns, and "a campaign page" would be the singular
    # mislabelling this note exists to prevent, applied to the note itself.
    assert "Not shown on a scoped page" in out and "because reasons" in out


def test_several_campaigns_scope_to_their_union_not_the_rollup():
    """A programme is a set of campaigns; the honest page is their union, never the profile
    rollup, which also sweeps in campaigns nobody asked about."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "mine-20260904,other-20260718")
    assert {c["slug"] for c in m["campaigns"]["campaigns"]} == {"mine-20260904", "other-20260718"}
    assert {s["id"] for s in m["status"]["sequences"]} == {"AAA", "BBB"}


def test_one_unknown_slug_refuses_the_whole_selection():
    """Dropping the unknown one silently would render a narrower page than was asked for."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "mine-20260904,no-such-20260101")
    assert "campaign_scope" not in m


def test_a_campaign_with_no_date_suffix_claims_no_packs():
    """Packs are claimed by date. Leaving the list intact showed one campaign six packs
    written for another."""
    from gtm_core.email_campaign_dashboard.model import scope_to_campaign

    m = scope_to_campaign(_model(), "other-20260718")
    assert m["packs"]["packs"] == [], "the 20260719 pack is one day off — not this campaign's"
    m2 = _model()
    m2["campaigns"]["campaigns"][0]["slug"] = "undated"
    m2 = scope_to_campaign(m2, "undated")
    assert m2["packs"]["packs"] == [] and m2["packs"]["other_campaigns"] == 2


# --- the four defects fixed on 2026-09-06 ----------------------------------------------
#
# Every spec on disk writes the blockquote ADJACENT to its `**Step N — Day D**` header; the
# fixtures above are the only place in the world with a blank line between them. The old
# `_spec_copy` regex required that blank line, so it returned nothing for every real campaign
# and the forecast card reported a day-0/day-5 sequence as "2 emails over 0 days … 1 working
# day". Tests that only ever saw the fixture could not fail.

SPEC_ADJACENT = """# Sequence spec — adjacent

**Step 1 — Day 0** · Subject: `the shared login`
> Hi {{First Name}},
>
> One line of argument.
>
> Henry

**Step 2 — Day 5** (new thread) · Subject: `the one-pager`
> Hi {{First Name}},
>
> Closing the loop.
>
> Henry
"""


def test_spec_copy_reads_a_blockquote_that_touches_its_header(tmp_path):
    from gtm_core.email_campaign_dashboard.model import _spec_copy

    spec = tmp_path / "s.md"
    spec.write_text(SPEC_ADJACENT, encoding="utf-8")
    copy = _spec_copy(spec)
    assert [(c["step"], c["day"]) for c in copy] == [(1, 0), (2, 5)], (
        "a spec written the way every real spec is written must not parse as zero touches"
    )
    assert copy[0]["subject"] == "the shared login"
    assert copy[1]["subject"] == "the one-pager", "a subject after a parenthetical still counts"


def test_the_forecast_covers_hand_sent_lanes_not_only_the_sequencer(tmp_path):
    """Three quarters of this campaign's traffic never touches the sequencer, and the lane
    that decides the finish date is the one with the longest ladder — not the automated one."""
    from gtm_core.email_campaign_dashboard.forecast import _lanes

    m = {
        "messages": [{"sequence_id": "S1", "spec": "named.md", "copy": [{"day": 0}, {"day": 5}]}],
        "campaigns": {
            "campaigns": [
                {
                    "sequences": [{"sequence_id": "S1", "enrolled": 4}],
                    "targets": {"prospects": 4},
                }
            ]
        },
        "samples": {
            "rendered": [{"spec": "named", "to": f"n{i}@x.com"} for i in range(10)]
            + [{"spec": "inbox", "to": f"i{i}@x.com"} for i in range(8)],
            "touches": [
                {"spec": "named", "day": 0},
                {"spec": "named", "day": 5},
                {"spec": "inbox", "day": 0},
                {"spec": "inbox", "day": 5},
            ],
            "packs": [{"to": f"p{i}@x.com", "mail_days": [2, 7, 12]} for i in range(5)]
            # The same address as a role-inbox row: one pack, one lane, counted once.
            + [{"to": "i0@x.com", "mail_days": [0, 5]}],
        },
    }
    lanes = _lanes(m)
    assert [ln["people"] for ln in lanes] == [4, 8, 5], (
        "the sequencer's 4, the 8 role inboxes, and 5 packs — the sixth pack is its own inbox row"
    )
    assert lanes[-1]["span"] == 10, "the pack ladder, not the sequence, sets the finish date"
    assert lanes[-1]["emails"] == 15, "the LinkedIn touch is not an email and is not counted"


def test_one_sequence_reachable_from_four_registrations_is_counted_once(tmp_path):
    """`cells.toml` can register the same physical sequence more than once — a second
    segment variant, a second campaign's row pointing at the address the first already
    claimed — and each registration became its own entry in `model.py`'s `messages`. Before
    the dedup fix, `reviewed` summed every registration's `lint.rows` and the per-lane loop
    appended one lane per registration, so a sequence reachable from four sources counted
    its reviewed rows and its enrolled people four times over."""
    from gtm_core.email_campaign_dashboard.forecast import _lanes

    one_registration = {
        "sequence_id": "S1",
        "spec": "named.md",
        "copy": [{"day": 0}, {"day": 5}],
        "lint": {"rows": 4},
    }
    m = {
        # Four `[[sequence]]` rows in `cells.toml`, all pointing at the same sequence.
        "messages": [dict(one_registration) for _ in range(4)],
        "campaigns": {
            "campaigns": [
                {
                    "sequences": [{"sequence_id": "S1", "enrolled": 4}],
                    "targets": {"prospects": 4},
                }
            ]
        },
        "samples": {"rendered": [], "touches": [], "packs": []},
    }
    lanes = _lanes(m)
    assert len(lanes) == 1, "one physical sequence must produce one lane row, not four"
    assert lanes[0]["people"] == 4, (
        "the sequence's own reviewed/enrolled count, not four registrations' worth of it"
    )
    assert sum(ln["people"] for ln in lanes) == 4, (
        "the same sequence must not be counted once per registration in the final total"
    )


def test_prospecting_runs_are_scoped_out_of_a_campaign_page(tmp_path):
    """A run export carries no campaign tag, so the profile's discovery history cannot be
    rendered under one campaign's heading — six of the eight runs listed predated it."""
    from gtm_core.email_campaign_dashboard.views_ops import _runs_block

    profile = _seed_operational(tmp_path)
    m = gd.build_model(profile, tmp_path)
    wide = _runs_block(m)
    assert "<h2>Finding new people</h2>" in wide, "the rollup page keeps it — it is its question"

    m["campaign_scope"] = "c1"
    scoped = _runs_block(m)
    assert scoped.count("Finding new people") == 1, "once, not twice"
    assert "Not shown on a scoped page" in scoped.split("Finding new people")[1][:200]


def test_the_learn_panel_leads_with_the_campaigns_own_questions():
    from gtm_core.email_campaign_dashboard.views_results import _headline_learnings

    out = _headline_learnings(
        {
            "campaigns": {
                "campaigns": [
                    {
                        "experiment": {
                            "will_learn": [
                                {"question": "Can we replicate it?", "how": "the funnel"},
                                {"question": "Which SEAT?", "how": "the judge"},
                            ]
                        }
                    }
                ]
            }
        }
    )
    assert "1. Can we replicate it?" in out and "2. Which SEAT?" in out


def test_the_judge_queue_folds_by_address_and_the_worst_verdict_wins(tmp_path):
    """One inbox is both a role-inbox row and a 1:1 pack, so it is scored twice. Reporting
    the friendlier of the two would tell a reader a row is workable while a sibling row
    says it is held."""
    import json as _json

    from gtm_core.email_campaign_dashboard.sources import judge_queue

    evals = pc._prospects_dir("acme", tmp_path) / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    (evals / "retarget-queue-20260905.jsonl").write_text(
        "\n".join(
            _json.dumps(r)
            for r in (
                {"email": "a@x.com", "verdict": "re-angle", "destination": "prospect:re-target"},
                {"email": "b@x.com", "verdict": "re-angle", "destination": "spec:re-argue"},
                {"email": "b@x.com", "verdict": "drop", "destination": "spec:re-argue"},
            )
        ),
        encoding="utf-8",
    )
    q = judge_queue("acme", tmp_path)
    assert q["__tally__"]["rows"] == 3, "the tally counts ROWS; the fold counts addresses"
    assert q["a@x.com"]["action"] == "find a different seat"
    assert q["b@x.com"]["verdict"] == "drop" and q["b@x.com"]["also"] is True


def test_the_drafted_tile_counts_bodies_not_sequencer_rows():
    """Drafting and loading are different steps, and the tile used to report the second.

    `packs + sum(sequences.loaded)` reported "10 emails drafted" for a campaign whose own body
    text said all 23 contactable accounts had one — because `loaded` is a sequencer fact (10
    named-seat rows composed, 4 enrolled) and the role-inbox lane is in no sequence at all, so
    a sum over sequences is structurally blind to it.
    """
    from gtm_core.email_campaign_dashboard.forecast import drafted_to

    m = {
        "samples": {
            # 10 named-seat rows over two touches, 8 role inboxes over two, 6 packs — and one
            # pack written to an address that is also a role-inbox row.
            "rendered": [{"to": f"n{i}@x.com"} for i in range(10) for _t in (1, 2)]
            + [{"to": f"i{i}@x.com"} for i in range(8) for _t in (1, 2)],
            "packs": [{"to": f"p{i}@x.com"} for i in range(5)] + [{"to": "i0@x.com"}],
        }
    }
    assert len(drafted_to(m)) == 23, "an address that is both a pack and a merge row counts once"
    assert len(drafted_to({})) == 0, "no samples is zero, not a crash"


def test_the_drafted_tile_agrees_with_the_contactable_count(tmp_path):
    """The tile and the prose on the same page must not disagree about one number."""
    from gtm_core.email_campaign_dashboard.forecast import drafted_to

    # The property that matters is stated in the unit test; this guards the SUB-LINE arithmetic,
    # which has to add up to the headline for a reader who checks it.
    s = {
        "rendered": [{"to": "a@x.com"}, {"to": "b@x.com"}],
        "packs": [{"to": "b@x.com"}, {"to": "c@x.com"}],
    }
    merge = len({r["to"] for r in s["rendered"]})
    packs = len(s["packs"])
    assert len(drafted_to({"samples": s})) == 3
    assert merge + packs == 4, "the sub-line's two parts overlap; the headline must dedupe them"


def test_seat_fit_counts_against_the_declared_persona_not_by_hand():
    """A hand-written "7 of the 18" understated the real figure by half.

    The generic lane is signal-free, not seat-free: its spec declares a `hook_cell` whose left
    half is a persona and its body argues that persona's stakes, so a recipient who is not that
    seat is a targeting defect on this lane too. Counting it by reading titles is what a
    classifier is for.
    """
    from gtm_core.email_campaign_dashboard.sources import seat_fit

    m = {
        "samples": {"rendered": [{"to": f"r{i}@x.com"} for i in range(4)]},
        "roster": {
            "rows": [
                {"email": "r0@x.com", "seat": "Founder and CEO"},
                {"email": "r1@x.com", "seat": "Operations Manager"},
                {"email": "r2@x.com", "seat": "Head of Business Development"},
                {"email": "r3@x.com", "seat": ""},
            ]
        },
        "messages": [],  # no spec on disk -> no declared persona
    }
    # With nothing declared, every resolvable title counts as matched rather than as a defect:
    # a lane that declares no seat cannot be missing one.
    f = seat_fit(m)
    assert f["total"] == 4
    assert f["declared"] == []
    assert f["unresolved"] == 2, "Operations Manager and the blank title resolve to no persona"
    assert f["no_title"] == 1, "a role inbox is counted apart from a title with no matrix row"
    assert f["matched"] + f["elsewhere"] + f["unresolved"] == f["total"], "the split is a partition"


def test_seat_fit_is_empty_rather_than_zero_without_samples():
    """No rendered bodies is 'not measured', which must not render as 'all seats fit'."""
    from gtm_core.email_campaign_dashboard.sources import seat_fit

    assert seat_fit({}) == {}
    assert seat_fit({"samples": {"rendered": []}}) == {}


def test_seat_fit_resolves_persona_against_the_tenant_vocabulary(tmp_path, monkeypatch):
    """PH13: ``seat_fit`` must hand its ``profile`` through to ``persona_of`` — "Kiln Warden"
    is a title only a tenant's own ``role-vocabulary.toml`` can place. Without the profile
    it is booked unresolved; with it, it resolves and (with no declared persona on this
    lane) counts as matched."""
    from gtm_core.email_campaign_dashboard.sources import seat_fit
    from gtm_core.role_vocabulary import clear_cache

    profile = "acme"
    profiles_root = tmp_path / "profiles"
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (profiles_root / profile / "knowledge" / "role-vocabulary.toml").write_text(
        """\
default_persona = "kiln-warden"
segments = ["enterprise", "unspecified"]
security_only = []
non_buyer_cues = []
ceo_title_cues = []

[[persona]]
name = "kiln-warden"
cues = ["kiln warden"]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles_root))
    clear_cache()

    m = {
        "samples": {"rendered": [{"to": "r0@x.com"}]},
        "roster": {"rows": [{"email": "r0@x.com", "seat": "Kiln Warden"}]},
        "messages": [],
    }

    f_default = seat_fit(m)
    assert f_default["unresolved"] == 1, "control: the default vocabulary must not know this title"

    f_tenant = seat_fit(m, profile)
    assert f_tenant["matched"] == 1, f"tenant persona not resolved — {f_tenant}"
    assert f_tenant["unresolved"] == 0
