"""PS20 Phase 2 — the Results tab: before a campaign's first contact, what it will learn; after,
the figures (TP T2.13)."""

import json

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import forecast
from gtm_core.email_campaign_dashboard import format as fmt
from gtm_core.email_campaign_dashboard import views_results as vr
from gtm_core.email_campaign_dashboard.aggregate import _scope_figures
from gtm_core.email_campaign_dashboard.config import SECTIONS
from gtm_core.power import detectable_lift
from tests.contracts.dashboard_page import block, section, section_ids, visible_text
from tests.contracts.test_dashboard_ps20_trust import _ago, _fig, _fixture_10_24_1, _stats
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.contracts.test_dashboard_tenant_prose import _full_fixture
from tests.test_email_campaign_dashboard import _seed_operational

EXPERIMENT = (
    '\n[experiment]\nwhat_it_tells_us = "whether the seat answers"\n'
    'what_it_cant_tell_us = "a reply rate"\n\n[[experiment.hypotheses]]\nid = "H1"\n'
    'claim = "The seat beats the pitch"\nstatus = "can answer"\nverdict = "open"\n'
    'needs = "replies"\n'
)


def _with_experiment(tmp_path, profile):
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    with camp.open("a", encoding="utf-8") as fh:
        fh.write(EXPERIMENT)
    return profile


def _card(html, slug="c1"):
    return block(html, f'data-campaign="{slug}"')


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": "S1", "status": "paused", "sent": 0}],
        [{"id": "S1", "status": "active", "sent": 0}],
        "{broken",  # unknown: the manifest's hypotheses still show; no figures line
    ],
)
def test_before_first_contact_the_card_says_what_the_run_will_learn(tmp_path, rows):
    profile = _with_experiment(tmp_path, _seed_operational(tmp_path))
    _stats(
        tmp_path,
        profile,
        rows if isinstance(rows, str) else {"fetched": _ago(0), "sequences": rows},
    )
    html = vr._results_view(gd.build_model(profile, tmp_path))
    assert set(section_ids(html)) <= SECTIONS["results"]
    card = _card(html)
    text = visible_text(card)
    assert "The seat beats the pitch" in text and "The 1 question we set out to answer" in text
    assert "whether the seat answers" in text and "a reply rate" in text
    assert _fig(card, "goal-emails-c1") is not None  # the goal, in the manifest's own unit
    assert _fig(card, "results-contacted-c1") is None  # no figures line before first contact
    assert section(html, "when-we-know")


def test_after_first_contact_the_figures_replace_the_hypotheses(tmp_path):
    profile = _with_experiment(tmp_path, _fixture_10_24_1(tmp_path))
    card = _card(vr._results_view(gd.build_model(profile, tmp_path)))
    assert (_fig(card, "results-contacted-c1"), _fig(card, "results-replied-c1")) == ("10", "0")
    assert "The seat beats the pitch" not in card
    earlier = (
        "<strong>Earlier run.</strong> An earlier run contacted 24 more people, not counted here."
    )
    assert earlier in card and card.index(earlier) < card.index("<table")


def test_first_contact_is_counted_not_read_off_the_word(tmp_path):
    """T2.13: a PAUSED campaign that has contacted people is past first contact."""
    profile = _fixture_10_24_1(tmp_path)
    rows = [
        {"id": "S1", "status": "paused", "sent": 7, "loaded": 7},
        {"id": "S2", "sent": 3},
        {"id": "S3", "sent": 17},
        {"id": "S4", "sent": 7},
        {"id": "X", "sent": 1},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    assert m["campaigns"]["campaigns"][0]["state"] == "paused"
    assert _fig(_card(vr._results_view(m)), "results-contacted-c1") == "10"


def test_when_we_will_know_is_the_forecasts_sentence(tmp_path):
    m = gd.build_model(_seed_operational(tmp_path), tmp_path)
    sentence, _why = forecast.when_done(m)
    html = vr._results_view(m)
    assert sentence in visible_text(section(html, "when-we-know"))
    assert "Done by" not in html


def test_when_the_forecast_refuses_results_says_why(tmp_path):
    from tests.contracts.test_dashboard_aggregation_refusal import BASE
    from tests.contracts.test_dashboard_aggregation_refusal import _seed as _two

    _two(tmp_path, second=BASE.replace("touches = 2", "touches = 3"))
    m = gd.build_model("acme", tmp_path)
    _sentence, why = forecast.when_done(m)
    assert why in visible_text(section(vr._results_view(m), "when-we-know"))


@pytest.mark.parametrize("checked", [2, 3])
def test_the_small_numbers_line_is_the_readable_difference(tmp_path, checked):
    profile = _full_fixture(tmp_path)
    lint = pc._pool_dir(profile, tmp_path) / "lint-S1.json"
    rec = json.loads(lint.read_text(encoding="utf-8"))
    lint.write_text(json.dumps({**rec, "rows": checked}), encoding="utf-8")
    m = gd.scope_to_campaign(gd.build_model(profile, tmp_path), FULL)
    lifts = [
        x
        for ln in forecast._lanes(m)
        if (x := detectable_lift(ln["people"], m["cells"]["baseline"]))
    ]
    assert bool(lifts) is (checked == 3), (
        "the fixture must take both branches (cf. tenant_prose :350-366)"
    )
    line = section(vr._results_view(m), "small-numbers")
    if lifts:
        assert _fig(line, "smallest-lift") == f"{min(lifts):g}×"
    else:
        assert "No group here is big enough" in visible_text(line)


def test_the_results_tiles_are_the_scopes_outcome_figures(tmp_path):
    fmt._tiles_reset()
    html = vr._results_view(gd.build_model(_fixture_10_24_1(tmp_path), tmp_path))
    assert [t["label"] for t in fmt._tiles_recorded()] == [
        "people contacted",
        "reply rate",
        "replies so far",
    ]
    assert _fig(section(html, "results-figures"), "contacted-current") == "10"


def test_when_we_will_know_shows_while_any_campaign_is_before_first_contact(tmp_path):
    """Gone once every campaign has contacted someone; back when one has not (any, not all)."""
    profile = _fixture_10_24_1(tmp_path)
    assert section(vr._results_view(gd.build_model(profile, tmp_path)), "when-we-know") == ""
    camps = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camps / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["S9"]\n\n[targets]\nemails = 30\n',
        encoding="utf-8",
    )
    html = vr._results_view(gd.build_model(profile, tmp_path))
    assert _fig(_card(html), "results-contacted-c1") == "10"
    assert _fig(_card(html, "c2"), "results-contacted-c2") is None
    assert section(html, "when-we-know")


def test_an_unreadable_snapshot_refuses_the_goal_figure_never_zero(tmp_path):
    profile = _with_experiment(tmp_path, _seed_operational(tmp_path))
    _stats(tmp_path, profile, "{broken")
    card = _card(vr._results_view(gd.build_model(profile, tmp_path)))
    assert _fig(card, "goal-emails-c1").startswith("—")
    assert "sending figures unavailable" in visible_text(card)


def test_an_unreadable_snapshot_claims_neither_before_nor_after(tmp_path):
    """Owner decision (refuse on absence): with the figures unreadable, sending may already be
    under way, so neither the forecast's "if sending starts" date nor the figures line is
    claimed. The page says it cannot tell, in the words the rest of the page uses for an
    unreadable snapshot. The manifest's hypotheses stay: they are its words, not figures."""
    profile = _with_experiment(tmp_path, _seed_operational(tmp_path))
    sentence, _why = forecast.when_done(gd.build_model(profile, tmp_path))
    _stats(tmp_path, profile, "{broken")
    m = gd.build_model(profile, tmp_path)
    why = _scope_figures(m)["contacted"][1]
    html = vr._results_view(m)
    assert sentence and sentence not in html and "If sending starts" not in html
    wwk = visible_text(section(html, "when-we-know"))
    assert f"Can't tell whether sending has started: {why}." in wwk
    text = visible_text(_card(html))
    assert "The seat beats the pitch" in text  # a hypothesis claim, still shown
    assert "whether the seat answers" in text and "a reply rate" in text
    assert _fig(_card(html), "results-contacted-c1") is None


def test_an_earlier_run_alone_is_not_first_contact(tmp_path):
    """Current 0, earlier N: still BEFORE — an archived run's sends are not this run's."""
    profile = _fixture_10_24_1(tmp_path)
    rows = [
        {"id": "S1", "sent": 0, "loaded": 7},
        {"id": "S2", "sent": 0, "loaded": 5},
        {"id": "S3", "sent": 17},
        {"id": "S4", "sent": 7},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    earlier = sum(r["sent"] for r in rows if r["id"] in {"S3", "S4"})  # the retired run's ids
    html = vr._results_view(gd.build_model(profile, tmp_path))
    card = _card(html)
    assert _fig(card, "results-contacted-c1") is None
    line = f"An earlier run contacted {earlier} more people, not counted here."
    assert line in visible_text(card)
    assert section(html, "when-we-know")


def test_the_small_numbers_line_reads_the_largest_group(monkeypatch):
    """Several groups: the line names the SMALLEST detectable lift (the largest group's)."""
    base, sizes = 0.03, [5, 40, 1]
    monkeypatch.setattr(vr, "_lanes", lambda m: [{"people": n} for n in sizes])
    lifts = {n: detectable_lift(n, base) for n in sizes}
    assert lifts[40] < lifts[5] and lifts[1] is None  # the fixture separates min from max
    line = vr._small_numbers({"cells": {"baseline": base}})
    assert _fig(line, "smallest-lift") == f"{lifts[40]:g}×"


def test_no_groups_no_small_numbers_line(monkeypatch):
    monkeypatch.setattr(vr, "_lanes", lambda m: [])
    assert vr._small_numbers({"cells": {"baseline": 0.03}}) == ""


X = "<script>alert(1)</script>"


def test_manifest_text_on_results_renders_escaped(tmp_path):
    profile = _seed_operational(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    camp.write_text(
        f'slug = "c1"\ntitle = {json.dumps("T" + X)}\nsequences = ["S1"]\n\n'
        "[targets]\nemails = 9\n\n"
        f"[experiment]\nwhat_it_tells_us = {json.dumps('W' + X)}\n"
        f"what_it_cant_tell_us = {json.dumps('C' + X)}\n\n"
        f"[[experiment.will_learn]]\nquestion = {json.dumps('Q' + X)}\n"
        f"how = {json.dumps('H' + X)}\n",
        encoding="utf-8",
    )
    html = vr._results_view(gd.build_model(profile, tmp_path))
    assert "<script>" not in html
    for tag in "TWCQH":
        assert f"{tag}&lt;script&gt;alert(1)" in html, tag


@pytest.mark.parametrize(
    ("sent", "replied", "words"),
    [(1, 1, "1 person contacted · 1 reply"), (2, 0, "2 people contacted · 0 replies")],
)
def test_the_figures_line_says_one_person_and_one_reply(tmp_path, sent, replied, words):
    profile = _fixture_10_24_1(tmp_path)
    rows = [{"id": "S1", "sent": sent, "replied": replied, "loaded": 7}, {"id": "S2", "sent": 0}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    assert words in visible_text(_card(vr._results_view(gd.build_model(profile, tmp_path))))


def test_an_unreadable_snapshot_refuses_the_rate_tile_too(tmp_path):
    profile = _seed_operational(tmp_path)
    _stats(tmp_path, profile, "{broken")
    html = vr._results_view(gd.build_model(profile, tmp_path))
    tiles = visible_text(section(html, "results-figures"))
    assert "sending figures unavailable" in tiles and "nothing sent yet" not in tiles


def _two_rates(tmp_path, second):
    from tests.contracts.test_dashboard_aggregation_refusal import BASE
    from tests.contracts.test_dashboard_aggregation_refusal import _seed as _two

    _two(tmp_path, second=second(BASE.replace("reply_rate = 0.02", "reply_rate = 0.06")))
    m = gd.build_model("acme", tmp_path)
    return _scope_figures(m), visible_text(section(vr._results_view(m), "results-figures"))


def test_the_rate_tile_says_why_it_has_no_single_target(tmp_path):
    fig, tiles = _two_rates(tmp_path, lambda b: b.replace("prospects = 300\n", ""))
    target, why = fig["target_rate"]
    assert target is None and why
    assert f"no single target — {why}" in tiles


def test_a_blended_rate_target_says_it_is_weighted_by_prospects(tmp_path):
    fig, tiles = _two_rates(tmp_path, lambda b: b)
    assert fig["rates_differ"] and fig["target_rate"][1] is None
    assert f"target {fig['target_rate'][0]:.1%} (weighted by prospects)" in tiles


def test_a_bare_manifest_says_it_has_no_goals_and_no_hypotheses(tmp_path):
    profile = _seed_operational(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    camp.write_text('slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n', encoding="utf-8")
    card = visible_text(_card(vr._results_view(gd.build_model(profile, tmp_path))))
    assert "No goals recorded for this campaign." in card
    assert "This campaign's manifest states no hypotheses." in card


def test_every_results_section_renders_and_the_questions_open(tmp_path):
    """All six declared sections, exactly: one a rewrite drops (``can-answer``,
    ``learnings``) is a missing block, not a smaller subset."""
    profile = _seed_operational(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c1.campaign.toml"
    with camp.open("a", encoding="utf-8") as fh:
        fh.write(
            '\n[experiment]\n[[experiment.will_learn]]\nquestion = "Does the seat answer"\n'
            'how = "count replies"\n'
        )
    html = vr._results_view(gd.build_model(profile, tmp_path))
    assert sorted(section_ids(html)) == sorted(SECTIONS["results"])
    assert "Does the seat answer" in visible_text(section(html, "learnings"))


def test_t3_2_optouts_are_people(tmp_path):
    """PS20 T3.2: optouts are people, deduplicated by key, matched against dnc_added."""
    from gtm_core.prospects_import import _ledgers

    profile = _fixture_10_24_1(tmp_path)
    led = _ledgers(profile, tmp_path)
    events = [
        {"event": "optout_detected", "email": "Ada@Acme.Example", "ts": "2026-09-25"},
        {"event": "optout_detected", "email": "ada@acme.example", "ts": "2026-09-25"},
        {"event": "optout_detected", "email": "ada@acme.example", "ts": "2026-09-25"},
        {"event": "optout_detected", "email": "bob@acme.example", "ts": "2026-09-25"},
        {"event": "dnc_added", "email": "ada@acme.example", "ts": "2026-09-25"},
        {"event": "optout_unreadable", "email": "unreadable@acme.example", "ts": "2026-09-25"},
        {"event": "optout_detected", "email": "", "ts": "2026-09-25"},
    ]
    for ev in events:
        led.append_history(ev)

    m = gd.build_model(profile, tmp_path)
    card = _card(vr._results_view(m))
    assert "<span class='pill risk' data-risk='opted-out'>2</span>" in card
    assert "2 people asked not to be contacted; 1 of 2 on the do-not-contact list" in visible_text(
        card
    )
    assert "unattributable" in card


def test_t3_4_results_live_view_table(tmp_path):
    """PS20 T3.4: Live view per-sequence table shows outcome labels, meetings tagged,
    pilots not tracked, and no opens/clicks."""
    profile = _fixture_10_24_1(tmp_path)
    seq_payload = {
        "sequenceId": "S1",
        "sequenceName": "Demo Sequence",
        "status": "active",
        "prospects": [
            {
                "contacted": 10,
                "replied": 2,
                "interested": 1,
                "notNow": 1,
                "notInterested": 0,
                "unsubscribed": 0,
                "outOfOffice": 0,
                "meetingBooked": 1,
                "total": 50,
            }
        ],
        "emails": {
            "status": {
                "delivered": 100,
                "bounced": 0,
            }
        },
    }
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [seq_payload]})
    m = gd.build_model(profile, tmp_path)
    card = _card(vr._results_view(m))

    assert "Demo Sequence" in card
    assert "Meetings <span class='note'>(tagged in the sending tool)</span>" in card
    assert "not tracked" in card
    assert "Open" not in card and "open" not in card
    assert "Click" not in card and "click" not in card


def test_t3_5_surface_agreement_across_tabs(tmp_path):
    """PS20 T3.5: outcomes and labels across overview, results, and ops agree."""
    profile = _fixture_10_24_1(tmp_path)
    seq_payload = {
        "sequenceId": "S1",
        "sequenceName": "Demo",
        "status": "active",
        "prospects": [
            {
                "contacted": 10,
                "replied": 3,
                "interested": 2,
                "notNow": 1,
                "notInterested": 0,
                "unsubscribed": 0,
                "outOfOffice": 0,
                "meetingBooked": 1,
                "total": 50,
            }
        ],
        "emails": {"status": {"delivered": 100, "bounced": 0}},
    }
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [seq_payload]})
    m = gd.build_model(profile, tmp_path)
    fig = _scope_figures(m)
    assert fig["contacted"][0]["current"] == 10
    assert fig["replied"][0] == 3
    assert fig["meetings"][0] == 1
    assert fig["labels"][0]["interested"] == 2
    assert fig["labels"][0]["not_now"] == 1

    card = _card(vr._results_view(m))
    assert _fig(card, "results-contacted-c1") == "10"
    assert _fig(card, "results-replied-c1") == "3"


@pytest.mark.parametrize(
    ("bounced", "delivered", "source", "expected_fragment"),
    [
        (29, 971, "emails", "2.9%"),  # no pill
        (30, 970, "emails", "3.0%"),  # exactly 3.0% -> no pill
        (31, 969, "emails", "data-risk='bounce-rate'>3.1%"),  # pill strictly above 3%
        (0, 0, "emails", "—"),  # no denominator -> em dash
        (0, 100, "emails", "0.0%"),  # genuine 0 with emails source
        (5, 95, "prospects", "not available"),  # fallback source -> not available
        (5, 95, None, "not available"),  # flat dict / no source -> not available
    ],
)
def test_t3_6_bounce_rate_rendering_cases(tmp_path, bounced, delivered, source, expected_fragment):
    """PS20 T3.6: bounce rate cases in sequence results table."""
    profile = _fixture_10_24_1(tmp_path)
    if source == "emails":
        seq_payload = {
            "sequenceId": "S1",
            "sequenceName": "Demo",
            "prospects": [{"contacted": 10, "replied": 0}],
            "emails": {"status": {"bounced": bounced, "delivered": delivered}},
        }
    elif source == "prospects":
        seq_payload = {
            "sequenceId": "S1",
            "sequenceName": "Demo",
            "prospects": [{"contacted": 10, "replied": 0, "bounced": bounced}],
        }
    else:
        # flat dict path
        seq_payload = {
            "id": "S1",
            "name": "Demo",
            "sent": 10,
            "bounced": bounced,
            "delivered": delivered,
        }
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [seq_payload]})
    m = gd.build_model(profile, tmp_path)
    card = _card(vr._results_view(m))
    assert expected_fragment in card
    if expected_fragment in ("2.9%", "3.0%"):
        assert "data-risk='bounce-rate'" not in card
