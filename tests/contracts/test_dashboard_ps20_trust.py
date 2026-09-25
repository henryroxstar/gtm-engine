import html
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard.aggregate import _scope_figures
from gtm_core.email_campaign_dashboard.filters import row_groups
from gtm_core.prospect_lede import GO_LIVE_WORDS
from tests.test_dashboard_operator_truth import _badge
from tests.test_email_campaign_dashboard import CSV_HEADER, _page, _seed


def _stats(tmp_path, profile, payload):
    pc._pool_dir(profile, tmp_path).joinpath("sequence-stats.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )


def _ago(days):
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip(page):
    return page.split('<section id="p-', 1)[0]


def test_unreadable_snapshot_reads_unknown_not_zero(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, "{broken")
    m = gd.build_model(profile, tmp_path)
    assert (m["warnings"], m["go_live_status"]) == (["unreadable"], "unknown")
    assert m["reconciliation"]["ok"] is True  # skipped, not silently "everything vanished"
    head = _strip(gd.render_html(m))
    assert 'data-warn="unreadable"' in head
    assert "These numbers may be out of date" not in head  # no artifact records-disagree


def test_old_figures_strip_sits_above_the_tabs(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(3), "sequences": [{"id": "S1", "sent": 0}]})
    assert 'data-warn="figures-old"' in _strip(_page(tmp_path, profile))


def test_unparseable_fetched_shows_no_date_not_the_raw_string(tmp_path):
    """A garbage `fetched` must never print as if it were a date — the renderer follows the
    model's own parse (`health.figures_date`), never the raw string's truthiness."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": "yesterday", "sequences": [{"id": "S1", "sent": 0}]})
    page = _page(tmp_path, profile)
    assert "Sending figures carry no date, so treat them as old." in page
    assert "from yesterday" not in page


def test_two_reasons_render_as_one_strip(tmp_path):
    """A reconciliation mismatch AND old figures together are still ONE strip, never two
    separate cards."""
    profile = _seed(tmp_path)
    pool = pc._pool_dir(profile, tmp_path)
    stats = json.loads((pool / "sequence-stats.json").read_text(encoding="utf-8"))
    stats["sequences"].append({"id": "GHOST", "name": "Ghost", "status": "paused", "sent": 0})
    stats["fetched"] = _ago(3)
    (pool / "sequence-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    page = _page(tmp_path, profile)
    assert page.count('class="card warn"') == 1
    assert 'data-warn="records-disagree figures-old"' in page
    strip = page.split('<div class="card warn"', 1)[1].split("</div>", 1)[0]
    assert strip.count("<p>") == 2
    assert "Refresh before trusting anything below." in strip


def test_go_live_badge_uses_the_one_vocabulary(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 2}]})
    page = _page(tmp_path, profile)
    # The lede says this too, so a page-wide substring check alone can't tell the badge
    # apart from the lede — pin the badge ELEMENT and its exact neutral-pill markup.
    assert 'Go-Live Status: <span class="pill">' in page
    assert _badge(page) == html.escape(GO_LIVE_WORDS["started"])
    assert "Nothing staged yet" not in page

    _stats(tmp_path, profile, "{broken")
    assert _badge(_page(tmp_path, profile)) == html.escape(GO_LIVE_WORDS["unknown"])


def test_scope_to_campaign_synthetic_row_status_is_not_the_ledgers(tmp_path):
    """A sequence the campaign's ledger names but the live snapshot has never seen gets a
    synthesized row (PS20) — its status must read as unknown, never the ledger's own
    "paused" (which every staged-but-unlive sequence carries by construction)."""
    profile = _seed(tmp_path)
    m = gd.build_model(profile, tmp_path)
    m["campaigns"]["campaigns"][0]["sequences"].append(
        {"sequence_id": "GHOST2", "status": "paused", "spec": "", "enrolled": 5}
    )
    scoped = gd.scope_to_campaign(m, "c1")
    row = next(r for r in scoped["status"]["sequences"] if r["id"] == "GHOST2")
    assert row["status"] == ""


def test_sum_only_mismatch_gets_counted_twice_not_out_of_date(tmp_path):
    """PS20 T1.2/T1.3, deferred from the Task 4 review — pinned now that Task 6 wires
    `_scope_figures`'s real `sum_ok` into `page_warnings` (model.py no longer hardcodes
    `True`). Two campaigns both claiming sequence S1 double its sent count in the 'current'
    total `_scope_figures` sums over campaigns, while the snapshot itself still agrees with
    the ledger about which sequences exist (a CLEAN reconciliation) — so the strip must say
    a sequence was counted twice, and must NOT claim the live figures and our own records
    disagree, which they don't.
    """
    profile = _seed(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    (camp / "c2.campaign.toml").write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["S1"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 5}]})
    m = gd.build_model(profile, tmp_path)
    assert m["reconciliation"]["ok"] is True
    assert m["warnings"] == ["records-disagree"]
    page = gd.render_html(m)
    strip = _strip(page)
    assert "don't add up" in strip and "counted twice" in strip
    assert "These numbers may be out of date" not in strip
    # Flagged, not fixed: the tile and the ops card still show ONE figure between them.
    tile, card = _fig(page, "contacted-current"), _fig(page, "ops-contacted")
    assert tile is not None and tile == card


def test_clean_page_with_contacts_has_no_warning_strip(tmp_path):
    """The positive control for the sum-only check above: one campaign, one sequence, a
    fresh and readable snapshot with real sends — nothing here disagrees or double-counts,
    so `sum_ok` must be True and the strip must not appear at all."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 2}]})
    assert gd.build_model(profile, tmp_path)["warnings"] == []


# ------------------------------------------ PS20 Task 7: one figure source, derived sentences
#
# Every assertion below reads a MARKED element (`data-figure`), never a page-wide substring:
# benchmark prose ("replies by emails sent"), CSS and tag-split phrases make a page-wide check
# pass or fail by accident.


def _fig(page, name):
    hit = re.search(rf'data-figure="{re.escape(name)}"[^>]*>(.*?)<', page, re.S)
    return hit.group(1).strip() if hit else None


def _figs(page, name):
    """Every element carrying this mark. A figure shown twice must say the same thing twice."""
    marks = re.findall(rf'data-figure="{re.escape(name)}"[^>]*>(.*?)<', page, re.S)
    return [v.strip() for v in marks]


def _tile(page, name):
    """The headline tile whose value carries this mark, cut at the next tile."""
    at = page.index(f'data-figure="{name}"')
    start = page.rindex('<div class="stat" ', 0, at)
    end = page.find('<div class="stat" ', at)
    return page[start : end if end != -1 else None]


def _ops_card(page):
    """The ops card, from its opening tag to its own close — it nests no div."""
    at = page.index('data-figure="ops-heading"')
    return page[page.rindex('<div class="card', 0, at) : page.index("</div>", at)]


def _tile_labelled(page, label):
    """The headline tile with this label, cut at the next tile — for a tile with no mark."""
    at = page.index(f'<div class="stat-label">{label}</div>')
    start = page.rindex('<div class="stat" ', 0, at)
    end = page.find('<div class="stat" ', at)
    return page[start : end if end != -1 else None]


def _stat_value(tile):
    """What a tile shows as its value, tags stripped."""
    value = re.search(r'<div class="stat-value">(.*?)</div>', tile, re.S).group(1)
    return re.sub(r"<[^>]+>", "", value).strip()


def _goal_card_html(page, slug):
    """One campaign's goal card, from its opening tag to the end of its table."""
    at = page.index(f'data-figure="goal-emails-{slug}"')
    return page[page.rindex('<div class="card"', 0, at) : page.index("</table>", at)]


def _manifest(tmp_path, profile, current, archived, *, roster=False):
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    globs = 'roster_globs = ["mine-hubspot.csv"]\n' if roster else ""
    (camp / "c1.campaign.toml").write_text(
        f'slug = "c1"\ntitle = "Campaign One"\nsequences = {json.dumps(current)}\n'
        f"archived_sequences = {json.dumps(archived)}\n{globs}\n[targets]\nemails = 213\n",
        encoding="utf-8",
    )


def _fixture_10_24_1(tmp_path):
    """The founding incident's shape: 10 people contacted on the current sequences, 24 on a
    retired run's, 1 on a sequence no campaign lists — 35 in the snapshot's own total."""
    profile = _seed(tmp_path)
    _manifest(tmp_path, profile, ["S1", "S2"], ["S3", "S4"])
    rows = [
        {"id": "S1", "sent": 7, "loaded": 7},
        {"id": "S2", "sent": 3, "loaded": 5},
        {"id": "S3", "sent": 17},
        {"id": "S4", "sent": 7},
        {"id": "X", "sent": 1},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    return profile


ROSTER_HEAD = (
    "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,"
    "GTM_Tier,GTM_Segment,Country/Region,GTM_Why_Now,GTM_Signal_Source_URL,GTM_Verdict\n"
)


def _seed_with_roster(tmp_path, profile="acme"):
    """`_seed` plus a two-row campaign roster. Shared with Task 9's renderer-opens-nothing spy.

    * The two rows differ on Country/Region AND GTM_Segment, so the filter bar has a second
      value to offer and renders.
    * `ada@acme.example` is on `list.csv`, S1's recipient list, so a worklist row renders as
      staged. Without a roster no worklist row renders at all, and an absence assertion over
      it passes on any code.
    * A `capability_asserted` history row, so the inbound-health block resolves its rows.

    Every name is fictional (`gtm_core.fictionalize`).
    """
    profile = _seed(tmp_path, profile)
    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "mine-hubspot.csv").write_text(
        ROSTER_HEAD
        + "Ada,Byte,ada@acme.example,Acme,acme.example,verified,A,Builder,Singapore,,,send\n"
        + "Riley,Halvorsen,quinn@summitline.example,Riverbend Logistics,copperline.example,"
        "verified,B,Startup,United States,,,send\n",
        encoding="utf-8",
    )
    (pros.parent / "plans" / "campaigns" / "c1.campaign.toml").write_text(
        'slug = "c1"\ntitle = "Campaign One"\nsequences = ["S1"]\n'
        'roster_globs = ["mine-hubspot.csv"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )
    asserted = {
        "event": "capability_asserted",
        "skill": "email-compliance",
        "provider": "saleshandy",
        "sequence_id": "S1",
        "status": "PASS",
        "attested": [],
        "detail": [],
        "ts": "2026-09-20T00:00:00Z",
    }
    (pros.parent / "history.jsonl").write_text(json.dumps(asserted) + "\n", encoding="utf-8")
    return profile


def test_one_answer_to_has_anything_gone_out(tmp_path):
    page = _page(tmp_path, _fixture_10_24_1(tmp_path))
    assert _fig(page, "contacted-current") == "10"
    assert _fig(page, "ops-contacted") == "10"  # T1.2 founding incident: every row read 35 here
    assert _fig(page, "contacted-earlier") == "24"
    assert _fig(page, "planned-emails") == "213"
    assert _fig(page, "ops-heading") == "11 people contacted so far"  # current 10 + not linked 1
    goal = _fig(page, "goal-emails-c1")
    assert goal is not None and "%" not in goal  # the mark exists; no percentage
    assert "currently sending" not in page
    # T1.3: current > 0, so no site may say it, in any case (the goal card's typed
    # "…because nothing has been sent yet" is lower-case).
    assert "nothing has been sent" not in page.lower()
    assert _fig(page, "seq-tally") == "2 started"  # every snapshot row would read "5 started"
    # The goal, the tile and the ops card are one figure wherever it is shown.
    assert set(_figs(page, "planned-emails")) == {"213"}
    assert set(_figs(page, "contacted-current")) == {"10"}


def test_started_reads_started(tmp_path):
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    assert m["go_live_status"] == "started"
    assert any("started, people have been contacted" in line for line in m["lede"])


def test_nothing_sent_in_the_current_campaigns_names_the_earlier_run(tmp_path):
    """T1.3 — current 0, earlier > 0: the heading says so, and the card carries the earlier
    run's people rather than hiding them or folding them into this campaign."""
    profile = _seed(tmp_path)
    _manifest(tmp_path, profile, ["S1"], ["S3"])
    rows = [{"id": "S1", "sent": 0, "loaded": 4}, {"id": "S3", "sent": 17}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    page = _page(tmp_path, profile)
    card = _ops_card(page)
    assert _fig(card, "ops-heading") == "Nothing sent in the current campaigns"
    assert (_fig(card, "ops-contacted"), _fig(card, "contacted-earlier")) == ("0", "17")
    goal = _goal_card_html(page, "c1")
    assert "where it actually is: nothing sent in the current campaigns." in goal
    assert "An earlier run contacted 17 more people, not counted here." in goal


def test_unreadable_heading(tmp_path):
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, "{broken")
    assert _fig(_page(tmp_path, profile), "ops-heading") == "Sending figures unavailable"


def test_progress_is_people_over_people(tmp_path):
    profile = _seed(tmp_path)
    rows = [{"id": "S1", "sent": 4, "loaded": 4}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    page = _page(tmp_path, profile)
    assert _fig(page, "progress-S1") == "100%"
    table = page.split("<h2>Email sequences</h2>", 1)[1].split("</div>", 1)[0]
    assert "Progress is people contacted against people loaded" in table


def test_worklist_never_says_nothing_sent(tmp_path):
    # a ROSTER fixture with a row on list.csv, so a worklist row actually renders; without a
    # roster no row renders and the assert would pass on today's code too
    page = _page(tmp_path, _seed_with_roster(tmp_path))
    assert "On the recipient list for" in page  # the row the clause used to follow renders
    assert "Nothing sent." not in page


def test_enrolled_is_the_current_campaigns_loaded_not_every_row(tmp_path):
    """T1.2 — "enrolled" (the drafted-email tile) and the loaded tile both read Σ current
    `actuals.loaded`. The all-rows sum would add a retired run's row and an unlinked one."""
    profile = _seed_with_roster(tmp_path)
    _manifest(tmp_path, profile, ["S1", "S2"], ["S3"], roster=True)
    rows = [
        {"id": "S1", "sent": 7, "loaded": 7},
        {"id": "S2", "sent": 3, "loaded": 5},
        {"id": "S3", "sent": 17, "loaded": 17},
        {"id": "X", "sent": 1, "loaded": 1},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    current = sum(c["actuals"]["loaded"] for c in m["campaigns"]["campaigns"])
    every_row = sum(s["loaded"] for s in m["status"]["sequences"])
    assert (current, every_row) == (12, 30), "the fixture must tell the two sums apart"
    # Two marks: "enrolled" in the roster's drafted-email tile, then the loaded tile.
    assert _figs(gd.render_html(m), "loaded") == ["12", "12"]


def test_units_people_with_people_emails_with_emails(tmp_path):
    """T1.4 — the tile and the ops card count PEOPLE and state the goal in EMAILS, never as
    one fraction; the goal card's emails row has no percentage, its replies row keeps one."""
    profile = _fixture_10_24_1(tmp_path)
    camp = pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns"
    body = (camp / "c1.campaign.toml").read_text(encoding="utf-8")
    (camp / "c1.campaign.toml").write_text(body + "replies = 4\n", encoding="utf-8")
    pool = pc._pool_dir(profile, tmp_path) / "sequence-stats.json"
    stats = json.loads(pool.read_text(encoding="utf-8"))
    stats["sequences"][0]["replied"] = 2
    pool.write_text(json.dumps(stats), encoding="utf-8")
    page = _page(tmp_path, profile)

    tile = _tile(page, "contacted-current")
    assert '<div class="stat-label">people contacted</div>' in tile
    assert _fig(tile, "planned-emails") == "213" and "goal: " in tile and " emails" in tile
    assert "emails sent" not in tile

    card = _ops_card(page)
    assert "people contacted; the plan is" in card
    assert "planned emails" not in card and "emails sent" not in card

    goals = _goal_card_html(page, "c1")
    assert _fig(page, "goal-emails-c1") == "10 people contacted"
    assert re.search(r"<td>replies</td>.*?<td[^>]*>50%</td>", goals, re.S)


def test_a_started_campaign_is_not_told_nothing_went_out_or_to_press_send(tmp_path):
    """T1.10 — with a `started` campaign the ops card says neither "Nothing has been sent"
    nor "press send" / "not cleared to start": the note follows the figures."""
    m = gd.build_model(_fixture_10_24_1(tmp_path), tmp_path)
    assert m["campaigns"]["campaigns"][0]["state"] == "started"
    card = _ops_card(gd.render_html(m))
    for said in ("Nothing has been sent", "press send", "not cleared to start"):
        assert said not in card, said


def test_the_press_send_note_needs_a_readable_zero_and_a_waiting_campaign(tmp_path):
    """T1.3 — positive control for the test above: readable snapshot, nobody contacted, the
    campaign paused and its copy unchanged since the check, so the note asks a person to
    start it. Under an unreadable snapshot the same page says nothing of the kind."""
    profile = _seed(tmp_path, lint=False)
    card = _ops_card(_page(tmp_path, profile))
    assert _fig(card, "ops-heading") == "Nothing has been sent"
    assert "a person still has to press send" in card

    _stats(tmp_path, profile, "{broken")
    card = _ops_card(_page(tmp_path, profile))
    assert _fig(card, "ops-heading") == "Sending figures unavailable"
    assert "press send" not in card and _fig(card, "ops-contacted") is None


def test_scoped_tally_never_reads_the_ledgers_paused_and_the_lede_says_profile_wide(tmp_path):
    """T1.10 — on a campaign page a sequence synthesized from the ledger carries status "",
    so its tally word is `staged`, never the ledger's by-construction "paused"; and the lede,
    composed before scoping, is labelled profile-wide."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": [{"id": "S1", "sent": 0}]})
    m = gd.build_model(profile, tmp_path)
    m["campaigns"]["campaigns"][0]["sequences"].append(
        {"sequence_id": "GHOST2", "status": "paused", "spec": "", "enrolled": 5}
    )
    page = gd.render_html(gd.scope_to_campaign(m, "c1"))
    assert _fig(page, "seq-tally") == "2 staged"
    lede = re.search(r'<div class="card lede[^"]*"[^>]*>(.*?)</div>', page, re.S).group(1)
    assert "Profile-wide, not this campaign." in lede
    assert "Where things stand" not in lede  # the tab it sits on, so no pointer to it


@pytest.mark.parametrize("status,sent", [("paused", 10), ("active", 0)])
def test_no_press_send_unless_nobody_is_contacted_and_a_campaign_waits(tmp_path, status, sent):
    """T1.3 — each condition on its own. (a) A PAUSED sequence that has contacted 10 fails
    "nobody contacted"; (b) an ACTIVE one that has contacted nobody fails "a campaign is
    staged or paused". Copy unchanged since the check, so the note would say "press send"."""
    profile = _seed(tmp_path, lint=False)
    rows = [{"id": "S1", "status": status, "sent": sent}]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    assert m["campaigns"]["campaigns"][0]["state"] == status
    card = _ops_card(gd.render_html(m))
    assert "press send" not in card and "not cleared to start" not in card


def test_the_press_send_note_needs_a_readable_snapshot_hand_built():
    """T1.3 on a hand-built model: a paused campaign and zero rows, but the snapshot could
    not be read, so there is no zero to act on and no note. Readable, the same model gets it."""
    from gtm_core.email_campaign_dashboard.views_learn import _ops_view

    camp = {"state": "paused", "targets": {"emails": 10}, "sequences": [{"sequence_id": "S1"}]}
    m = {
        "campaigns": {"campaigns": [camp]},
        "status": {"sequences": [], "snapshot": {"unreadable": True}},
        "messages": [],
    }
    card = _ops_card(_ops_view(m))
    assert _fig(card, "ops-heading") == "Sending figures unavailable"
    assert "press send" not in card
    m["status"]["snapshot"]["unreadable"] = False  # positive control
    assert "a person still has to press send" in _ops_card(_ops_view(m))


def test_unreadable_figures_render_as_dashes_never_none_or_zero(tmp_path):
    """T1.11 on Task 7's tiles: under a broken stats file every sending figure refuses. The
    headline tiles and the goal's "so far" cell read "—" (never "None", never a zero), and
    the reply-rate line says why rather than "nothing sent yet"."""
    profile = _seed(tmp_path)
    _stats(tmp_path, profile, "{broken")
    page = _page(tmp_path, profile)
    for label in (
        "people contacted",
        "reply rate",
        "people loaded and waiting",
        "replies so far",
        "sequences set up",
    ):
        assert _stat_value(_tile_labelled(page, label)) == "—", label
    goal = _fig(page, "goal-emails-c1")
    assert goal.split()[0] == "—" and "None" not in goal
    rate = _tile_labelled(page, "reply rate")
    assert "sending figures unavailable" in rate and "nothing sent yet" not in rate


def test_the_replies_tile_is_the_current_campaigns_replied(tmp_path):
    """T1.2 — "replies so far" is `_scope_figures`' replied: the current sequences' own
    replies, not every snapshot row's (a retired run and an unlinked row replied too)."""
    profile = _seed(tmp_path)
    _manifest(tmp_path, profile, ["S1"], ["S3"])
    rows = [
        {"id": "S1", "sent": 7, "replied": 2},
        {"id": "S3", "sent": 17, "replied": 1},
        {"id": "X", "sent": 1, "replied": 1},
    ]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    replied = _scope_figures(m)["replied"][0]
    every_row = sum(s["replied"] for s in m["status"]["sequences"])
    assert (replied, every_row) == (2, 4), "the fixture must tell the two sums apart"
    tile = _tile_labelled(gd.render_html(m), "replies so far")
    assert _stat_value(tile) == str(replied)


# ------------------------------- PS20 Task 9: no [ACTION REQUIRED]; the renderer opens nothing


def _maintenance(page):
    """The Maintenance card on the Operator notes panel, cut at its own close; "" if none."""
    ops = page.split('id="p-ops"', 1)[1].split("</section>", 1)[0]
    at = ops.find("<h2>Maintenance</h2>")
    return "" if at == -1 else ops[at : ops.index("</div>", at)]


def _eval_round(tmp_path, profile, sheet="`send_it:` ___\n`send_it:` Y\n"):
    """A labeler page and its markdown sheet — no fixture created one before T1.6, which is
    how the banner survived PS15. One blank row and one pre-filled by default."""
    ev = pc._prospects_dir(profile, tmp_path) / "evals"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "labeler-2026-09-22-cold.html").write_text("<html></html>")
    (ev / "sheet-2026-09-22.md").write_text(sheet)
    return ev


def test_labeler_is_a_maintenance_line_not_a_banner(tmp_path):
    profile = _seed(tmp_path)
    _eval_round(tmp_path, profile)
    page = _page(tmp_path, profile)
    assert "[ACTION REQUIRED]" not in page
    ops = page.split('id="p-ops"', 1)[1]
    assert "labeler-2026-09-22" in ops and "Maintenance" in ops
    assert "delete-candidate" not in page.split('id="p-ops"', 1)[0]
    # The same counts the banner carried, now in the Maintenance card itself.
    card = _maintenance(page)
    assert 'href="prospects/evals/labeler-2026-09-22-cold.html"' in card
    assert "1 pre-filled for you to correct and 1 blank" in card


def test_an_unlabelled_round_with_nothing_pre_filled_says_so(tmp_path):
    profile = _seed(tmp_path)
    _eval_round(tmp_path, profile, sheet="`send_it:` ___\n`send_it:` ___\n")
    assert "(none pre-filled, 2 blank)" in _maintenance(_page(tmp_path, profile))


def test_skipped_snapshot_rows_are_a_maintenance_line(tmp_path):
    """PRD P1.11: non-dict rows in the sending tool's figures are counted, and the count is
    shown in Maintenance. A clean snapshot (the negative control) shows no card at all."""
    profile = _seed(tmp_path)
    rows = [{"id": "S1", "status": "paused", "sent": 0}, "not a row", 7]
    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows})
    m = gd.build_model(profile, tmp_path)
    assert m["status"]["snapshot"]["skipped"] == 2
    card = _maintenance(gd.render_html(m))
    assert "2 row(s) in the sending tool's figures file could not be read" in card

    _stats(tmp_path, profile, {"fetched": _ago(0), "sequences": rows[:1]})
    assert _maintenance(_page(tmp_path, profile)) == ""


def test_render_html_opens_no_file(tmp_path, monkeypatch):
    import builtins
    from pathlib import Path

    # A roster fixture: two roster rows in two countries (so the filter bar renders and
    # `script_block` would read its assets), one row on list.csv (so the worklist would read
    # it), and a `capability_asserted` history row (so the inbound-health block would read
    # sequencers.toml). Plus every file `render._review_sheet`/`_eval_labeler` used to read.
    profile = _seed_with_roster(tmp_path)
    ev = _eval_round(tmp_path, profile)
    (ev / "hold-2026-09-22.csv").write_text("email\nada@acme.example\n")
    seen = []
    real_open, real_path_open = builtins.open, Path.open
    monkeypatch.setattr(
        builtins, "open", lambda f, *a, **k: seen.append(str(f)) or real_open(f, *a, **k)
    )
    monkeypatch.setattr(
        Path, "open", lambda p, *a, **k: seen.append(str(p)) or real_path_open(p, *a, **k)
    )
    m = gd.build_model(profile, tmp_path)
    # Positive control: the MODEL phase opens every file the renderer used to open for itself.
    # A thinned fixture (no list row, no capability row, no eval files) fails here, loudly,
    # rather than letting `seen == []` below pass vacuously.
    opened = {Path(s).name for s in seen}
    for name in ("list.csv", "sequencers.toml", "sheet-2026-09-22.md", "hold-2026-09-22.csv"):
        assert name in opened, f"positive control: the model never opened {name}"
    seen.clear()
    page = gd.render_html(m)
    assert seen == []
    # The filter bar rendered, so `script_block` ran — the path that read its two assets.
    assert 'id="filterbar"' in page


def test_a_campaign_page_stages_nobody_from_another_campaigns_list(tmp_path):
    """Scope safety. List membership rides on each message (`list_rows`), and
    `scope_to_campaign` filters the messages, so a campaign page reads only ITS lists. A
    profile-wide `{email: …}` map would survive scoping and file a person under "staged" on
    this page, beside another campaign's sequence id, because they sit on that one's list."""
    profile = _seed_with_roster(tmp_path)
    seq = pc._prospects_dir(profile, tmp_path) / "sequences"
    # Riley is on c1's roster, and on c2's recipient list (S2) only.
    (seq / "other.csv").write_text(
        CSV_HEADER + "Riley,Halvorsen,quinn@summitline.example,CTO,Riverbend Logistics,"
        "copperline.example,Austin,United States,startup,B,,,,,,\n",
        encoding="utf-8",
    )
    with (seq / "cells.toml").open("a", encoding="utf-8") as fh:
        fh.write('\n[[sequence]]\nid = "S2"\ncsv = "other.csv"\nspec = "spec-demo-2026-08-18.md"\n')
    (
        pc._prospects_dir(profile, tmp_path).parent / "plans" / "campaigns" / "c2.campaign.toml"
    ).write_text(
        'slug = "c2"\ntitle = "Campaign Two"\nsequences = ["S2"]\n\n[targets]\nemails = 9\n',
        encoding="utf-8",
    )

    def riley(m):
        return next(r["i"] for r in m["roster"]["rows"] if r["email"] == "quinn@summitline.example")

    m = gd.build_model(profile, tmp_path)
    # Positive control: on the rollup, S2's list is in scope, so Riley IS staged there.
    assert row_groups(m)[riley(m)] == "staged"
    scoped = gd.scope_to_campaign(m, "c1")
    assert row_groups(scoped)[riley(scoped)] != "staged"
    worklist = gd.render_html(scoped).split('id="p-worklist"', 1)[1].split("</section>", 1)[0]
    assert "quinn@summitline.example" in worklist  # the row renders, just not as staged
    assert "S2" not in worklist
