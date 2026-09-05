"""Tests for gtm_core.email_campaign_dashboard — the campaign status page.

Assertions deliberately pin the *reader-facing wording*, not just the data. The page's
whole purpose is that someone who has never opened the sequencer can read it, so a label
regressing to tool jargon is a real defect and should fail here.
"""

from __future__ import annotations

import json

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


def test_page_carries_the_three_reader_questions(tmp_path):
    profile = _seed(tmp_path)
    out = gd.render_dashboard(profile, tmp_path, stubs=False)
    page = out.read_text(encoding="utf-8")
    assert out.name == "email_campaign_status.html"
    import html as _h

    for label in ("Who we're emailing", "What we're saying", "What we'll learn"):
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
    """The honest answer to "why are so many unknown?" is that the resolver covers three
    of the tenant's seven seats — not that data is missing."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "gap in our own classifier" in page
    assert "Chief Information Officer" in page  # the actual unplaced title, shown
    assert "seven" in page and "three" in page


def test_persona_axis_and_detection_coverage_are_shown(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Which problem we lead on, per job" in page
    assert "Head of AI / Applied AI" in page  # a seat we do NOT detect
    assert "not detected" in page


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


def test_learning_tab_leads_with_a_hypothesis_and_parameters(tmp_path):
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "The hypothesis" in page
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
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "Nothing has been sent" in page
    assert "0 of 9" in page


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
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert page.index('data-t="status"') < page.index('data-t="who"')
    assert "Email sequences" in page
    assert "Finding new people" in page


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
    assert "Two filters stand between" in page


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


def test_target_is_judged_against_the_closest_icp_comparator(tmp_path):
    """3% is ~1.7x the SaaS-to-enterprise figure. Calling that a conservative floor —
    as the page originally did — inverts the truth."""
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\nreply_rate = 0.03\n",
        encoding="utf-8",
    )
    page = _page(tmp_path, profile)
    assert gd.PRIMARY_BENCHMARK["label"] == "SaaS selling to enterprise"
    assert "1.7x the closest published comparator" in page
    assert "stretch target" in page

    # Matching the comparator exactly reads as neither stretch nor floor.
    (camp / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\nreply_rate = 0.018\n",
        encoding="utf-8",
    )
    page = _page(tmp_path, profile)
    assert "roughly in line with the closest published comparator" in page
    assert "neither a stretch nor a floor" in page


def test_cta_is_described_as_an_offer_not_an_ask(tmp_path):
    """We put an artifact on the table; we do not ask for the reader's time. Calling it an
    "ask" misdescribes the copy — every CTA in the live specs offers something."""
    profile = _seed(tmp_path)
    page = _page(tmp_path, profile)
    assert "One offer" in page
    assert "One ask" not in page


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


def test_trigger_hypothesis_is_stated_with_its_own_limits(tmp_path):
    """A hypothesis the run cannot answer must say so, or it reads as a plan."""
    profile = _seed(tmp_path)
    _with_pool(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "does the trigger predict the reply" in page
    assert "half-answerable at best" in page
    assert "To make it answerable next run" in page
    assert "Trigger type" in page  # counted as a parameter, with 1 level


def test_page_renders_for_a_profile_with_no_prospect_pool(tmp_path):
    """A zero-coverage profile must render, not raise.

    The intent section formats a percentage; doing arithmetic on that formatted string
    crashed on an empty profile, and because consolidate() wraps the render in a
    try/except the only symptom was a page that silently never appeared.
    """
    profile = _seed(tmp_path)  # no latest.json written
    page = _page(tmp_path, profile)
    assert "How well they fit the ideal customer" in page
    assert "half-answerable at best" in page


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
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    head = page.split('id="p-status"')[0]
    assert "Nothing has been sent" not in head
    assert "Re-push" not in head
    assert "Nothing has been sent" in page.split('id="p-ops"')[1]


def test_the_status_panel_still_states_that_nothing_has_gone_out(tmp_path):
    """Moving the banner must not cost the reader the fact. The first tile carries it."""
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    status = page.split('id="p-status"')[1].split("</section>")[0]
    assert "emails sent" in status
    assert "nothing goes out until a person starts it" in status


def test_a_figures_warning_does_stay_above_every_panel(tmp_path):
    """The one banner that keeps page scope: it says the numbers on every panel are suspect,
    so a reader who never opens Operator notes still sees it."""
    profile = _seed_operational(tmp_path)
    pool = pc._pool_dir(profile, tmp_path)
    stats = json.loads((pool / "sequence-stats.json").read_text(encoding="utf-8"))
    stats["sequences"].append({"id": "GHOST", "name": "Ghost", "status": "paused", "sent": 0})
    (pool / "sequence-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    head = _page(tmp_path, profile).split('id="p-status"')[0]
    assert "These numbers may be out of date" in head


def test_a_passing_check_never_reads_as_ready_while_the_loaded_copy_differs(tmp_path):
    """The state stays with the badge it qualifies. Moving it out with the procedure would
    leave a green PASS as the only thing next to copy that is not what would send."""
    profile = _seed_operational(tmp_path)
    page = _page(tmp_path, profile)
    what = page.split('id="p-what"')[1].split("</section>")[0]
    assert "not cleared to start" in what
    assert "PASS" in what


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
    assert "arrive as replies inside the first email" in page


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
    r = roster_model("p", ["*-hubspot.csv"], tmp_path)
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
    assert [x["tier"] for x in roster_model("p", ["*-hubspot.csv"], tmp_path)["rows"]] == ["A", "B"]


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
