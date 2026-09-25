"""PS20 Phase 2 — the Accounts tab: one account table, the filter, and the only `.tech`
(TP T2.4, T2.6, T2.10, T2.12)."""

import json
import re

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import filters
from gtm_core.email_campaign_dashboard import format as fmt
from gtm_core.email_campaign_dashboard import views_accounts as va
from tests.contracts.dashboard_page import classes, elements, section, section_ids, visible_text
from tests.contracts.test_dashboard_colour_reasons import SLUG as EVERY
from tests.contracts.test_dashboard_colour_reasons import _seed_every_site
from tests.contracts.test_dashboard_ps20_trust import _seed_with_roster
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.contracts.test_dashboard_tenant_prose import _full_fixture
from tests.lint import operator_vocabulary as ov
from tests.test_email_campaign_dashboard import _seed

VISIBLE = ["Account", "Contact", "Title", "Email", "Status", "What is left to do"]
TECH = ["Tier", "How it was verified", "Research verdict"]


def _view(tmp_path, build, slug=None):
    m = gd.build_model(build(tmp_path), tmp_path)
    return va._accounts_view(gd.scope_to_campaign(m, slug) if slug else m)


def _rows(html):
    return {
        re.search(r"<td><strong>([^<]+)</strong>", r).group(1): r
        for r in re.findall(r"<tr data-row=.*?</tr>", html, re.S)
    }


def _dropped(tmp_path):
    """`_seed_with_roster`, with the second account's research call a `drop`."""
    profile = _seed_with_roster(tmp_path)
    roster = pc._prospects_dir(profile, tmp_path) / "mine-hubspot.csv"
    text = roster.read_text(encoding="utf-8")
    roster.write_text(
        text.replace("Startup,United States,,,send", "Startup,United States,,,drop"),
        encoding="utf-8",
    )
    return profile


def test_one_account_table_with_the_prds_columns(tmp_path):
    html = _view(tmp_path, _seed_with_roster)
    assert section_ids(html) == ["filter", "account-tiles", "account-table"]
    table = section(html, "account-table")
    assert html.count("<table") == 1
    heads = re.findall(r'<th( class="tech")?>([^<]+)</th>', table)
    assert [h for tech, h in heads if not tech] == VISIBLE
    assert [h for tech, h in heads if tech] == TECH
    assert 'colspan="9"' in table


def test_the_contact_is_a_person_and_the_title_is_their_job(tmp_path):
    row = _rows(_view(tmp_path, _full_fixture, FULL))["Tidewater"]
    assert "Dana Nakamura" in row and "CISO" in row


def test_the_email_judges_next_step_is_the_rows_last_clause(tmp_path):
    rows = _rows(_view(tmp_path, _full_fixture, FULL))
    assert "Email judge: find a different seat." in rows["Tidewater"]
    assert "Email judge: rewrite the argument." in rows["Summitline"]


def test_the_filter_the_toggle_and_the_technical_columns_live_here(tmp_path):
    html = _view(tmp_path, _seed_with_roster)
    filt = section(html, "filter")
    assert (
        'id="filterbar"' in filt and 'id="tech-toggle"' in filt and 'id="filter-tripwire"' in filt
    )
    tech = [t for _p, t, a, _anc in elements(html) if "tech" in classes(a)]
    in_table = [
        t for _p, t, a, _anc in elements(section(html, "account-table")) if "tech" in classes(a)
    ]
    # `.tech` is only ever inside the one table: its three columns, and a staged row's
    # sequence id (Decision 13).
    assert tech == in_table and set(tech) == {"th", "td", "span"}


def _staged_row(html):
    return _rows(html)["Acme"]  # ada@acme.example is on S1's list (`_seed_with_roster`)


def test_a_staged_row_names_its_sequence_by_title_and_keeps_the_id_technical(tmp_path):
    """Decision 13 / Q3: PS14 keeps sequence ids out of operator-facing lines."""
    html = _view(tmp_path, _seed_with_roster)
    shown = visible_text(section(html, "account-table"), skip=frozenset({"tech"}))
    assert "On the recipient list for Demo." in shown  # S1's registered title
    assert not re.search(r"\bS1\b", shown)
    # Control: the id is still on the row, behind the toggle, so the assert above can fail.
    assert re.search(r'<span class="tech">[^<]*\bS1\b', _staged_row(html))
    assert re.search(r"\bS1\b", visible_text(section(html, "account-table")))


def test_a_sequence_with_no_title_falls_back_to_its_own_name_then_its_id(tmp_path):
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    # The fixture's title and the sending tool's name are both "Demo"; split them, so the
    # order of the two sources is what decides the sentence.
    m["status"]["sequences"][0]["name"] = "Provider Name"
    shown = visible_text(_staged_row(va._accounts_view(m)), skip=frozenset({"tech"}))
    assert "On the recipient list for Demo." in shown  # the registered title wins
    for msg in m["messages"]:
        msg.pop("title", None)  # no registered title: the sending tool's own name is next
    shown = visible_text(_staged_row(va._accounts_view(m)), skip=frozenset({"tech"}))
    assert "On the recipient list for Provider Name." in shown
    for r in m["status"]["sequences"]:
        r.pop("name", None)  # no name anywhere: the id is the only name the sequence has
    shown = visible_text(_staged_row(va._accounts_view(m)), skip=frozenset({"tech"}))
    assert "On the recipient list for S1." in shown


def test_a_row_on_two_lists_names_both_by_title(tmp_path):
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    m["messages"].append({**m["messages"][0], "sequence_id": "S2", "title": "Second"})
    lists = sum(
        any(r["email"] == "ada@acme.example" for r in msg["list_rows"]) for msg in m["messages"]
    )
    row = _staged_row(va._accounts_view(m))
    shown = visible_text(row, skip=frozenset({"tech"}))
    assert f"On {lists} recipient lists: Demo, Second." in shown
    assert not re.search(r"\bS[12]\b", shown)
    assert re.search(r"\bS1, S2\b", visible_text(row))  # control: both ids, behind the toggle


def test_every_roster_field_and_sequence_name_is_escaped(tmp_path):
    bad = "<script>x</script>"
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    for msg in m["messages"]:
        msg.pop("title", None)  # the provider's name is what the staged sentence shows
        msg["sequence_id"] = bad + "id"
    for sq in m["status"]["sequences"]:
        sq["id"], sq["name"] = bad + "id", bad + "name"
    for r in m["roster"]["rows"]:
        for k in ("contact", "company", "seat", "tier"):
            r[k] = bad + k
    html = va._accounts_view(m)
    assert "<script>" not in html
    esc = "&lt;script&gt;x&lt;/script&gt;"
    for k in ("contact", "company", "seat", "tier", "name", "id"):
        assert esc + k in html, k


def _cells(row):
    return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)


@pytest.mark.parametrize("build,slug", [(_seed_every_site, EVERY), (_seed_with_roster, None)])
def test_each_row_keeps_tier_and_status_in_their_own_columns(tmp_path, build, slug):
    rows = _rows(_view(tmp_path, build, slug)).values()
    left = VISIBLE.index("What is left to do")
    for row in rows:
        cells = _cells(row)
        # A pill belongs to the Status cell alone ("status unmapped"); a tier pill would not.
        for i, cell in enumerate(cells):
            pills = [a for _p, _t, a, _anc in elements(cell) if "pill" in classes(a)]
            assert not pills or i == VISIBLE.index("Status"), (i, cell)
        tds = [a for _p, t, a, _anc in elements(row) if t == "td"]
        assert sum("tech" in classes(a) for a in tds) == len(TECH)
        cell = cells[left]
        assert row.count('<span class="tech">') == cell.count('<span class="tech">')
    # Control: a staged row's id span exists, so the containment check above is not vacuous.
    assert any("Sequence id:" in _cells(r)[left] for r in rows)


def test_a_contact_less_row_says_role_inbox_or_dash(tmp_path):
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    acme, other = m["roster"]["rows"]
    acme["contact"] = ""  # an address, no person: a role inbox
    other["contact"], other["email"] = "", ""  # neither
    rows = _rows(va._accounts_view(m))
    contact = VISIBLE.index("Contact")
    assert _cells(rows["Acme"])[contact] == '<span class="muted">role inbox</span>'
    assert _cells(rows[other["company"]])[contact] == '<span class="muted">—</span>'


def test_each_tile_carries_its_sub_line(tmp_path):
    tiles = section(_view(tmp_path, _seed_with_roster), "account-tiles")
    for pred in ("co_no_email", "co_role_inbox", "co_signal_sourced"):
        assert f'data-count-pred="{pred}"' in tiles


def test_no_visible_accounts_text_names_a_tier(tmp_path):
    """The PRD puts tier behind the technical toggle — the group heading included."""
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    m["packs"] = {"packs": [{"account": "riverbend-logistics"}]}  # so the pack group renders
    html = va._accounts_view(m)
    assert 'data-group="pack"><td' in html
    assert "Tier" not in visible_text(html, skip=frozenset({"tech"}))
    assert "Tier" in visible_text(html)  # control: the tech column header still says it


def test_one_row_of_tiles_and_the_filter_reaches_every_one(tmp_path):
    fmt._tiles_reset()
    html = _view(tmp_path, _seed_with_roster)
    tiles = fmt._tiles_recorded()
    assert [t["label"] for t in tiles] == [
        "accounts researched",
        "have a verified address",
        "resolve to a named seat",
        "carry a dated why-now",
        "have a drafted email",
    ]
    assert all(t["reach"] for t in tiles)
    frozen = sum(not filters.reactive(t["src"]) for t in tiles)
    assert html.count('class="stat-why" hidden') == frozen == 1


def test_a_partial_roster_shows_its_rows_names_the_gap_and_refuses_the_tiles(tmp_path):
    from tests.contracts.test_dashboard_aggregation_refusal import BASE
    from tests.contracts.test_dashboard_aggregation_refusal import _seed as _two

    _two(tmp_path, second=BASE)  # only mine-20260904 declares a roster
    m = gd.build_model("acme", tmp_path)
    both = va._accounts_view(gd.scope_to_campaign(m, "mine-20260904,other-20260718"))
    assert "Analytical Engine" in section(both, "account-table")
    assert "Covers 1 of 2 campaigns" in visible_text(both)
    assert "Not shown for" in section(both, "account-tiles") and "accounts researched" not in both


def test_a_scope_with_no_roster_says_so_and_offers_no_filter(tmp_path):
    html = _view(tmp_path, _seed)
    assert "no campaign roster" in html and "<table" not in html
    assert section(html, "filter") == "" and section(html, "account-tiles") == ""
    assert "worklist" not in visible_text(html).lower()


def test_the_list_vs_provider_gap_is_reported_in_operator_notes_not_here(tmp_path):
    m = gd.build_model(_seed_every_site(tmp_path), tmp_path)
    assert "enrolled at the provider" in va._list_vs_provider(m)  # the gap is real…
    assert "enrolled at the provider" not in va._accounts_view(m)  # …and not on this tab


@pytest.mark.parametrize(
    "build,slug",
    [(_seed_every_site, None), (_seed_every_site, EVERY), (_full_fixture, FULL), (_dropped, None)],
)
def test_visible_account_text_uses_the_operators_words(tmp_path, build, slug):
    shown = visible_text(_view(tmp_path, build, slug), skip=frozenset({"tech"}))
    assert [h for h in ov.findings(text=shown) if h[0] == "<text>"] == []


def test_the_filter_payload_carries_no_contact_name(tmp_path):
    """A GUARD, not a first-red test. `filters.payload_rows` builds each row from named keys
    (`FACETS`, `PREDICATES`), so once `roster.py` carries `contact` this cannot fail on
    today's code. Before that it fails only on the missing key. It exists for the day someone
    adds `contact` to the payload, which would ship the name inside the page's JSON."""
    m = gd.build_model(_seed_with_roster(tmp_path), tmp_path)
    blob = json.dumps(filters.payload_rows(m))
    for row in m["roster"]["rows"]:
        assert row["contact"] and row["contact"] not in blob


def test_an_excluded_row_without_a_reason_names_the_research_call():
    """The researcher's own reason wins; without one, the row names the verdict it was
    excluded on — read off the row, never a fixed word."""
    row = {"verdict": "wrong-market", "verdict_reason": ""}
    assert va._stands(row, "excluded", {}, {}) == "Research call: wrong-market."
    row["verdict_reason"] = "Parent company already a customer."
    assert va._stands(row, "excluded", {}, {}) == "Parent company already a customer."


def test_t3_3_replied_account_shows_waiting_on_you_they_replied(tmp_path, monkeypatch):
    """PS20 Phase 3 T3.3: an account with status 'replied' in latest.json routed to hold
    under engaged-account shows 'Waiting on you' with reason 'they replied'.
    An engaged-account hold from any other status shows no such reason."""
    from gtm_core import lanes
    from gtm_core.lanes import decisions as dec
    from gtm_core.prospect_paths import evals_dir

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = _seed_with_roster(tmp_path)
    latest_file = tmp_path / profile / "prospects" / "latest.json"
    latest_file.parent.mkdir(parents=True, exist_ok=True)
    latest_data = {
        "profile": profile,
        "items": [
            {"company": "Acme", "domain": "acme.example", "status": "customer"},
            {
                "company": "Riverbend Logistics",
                "domain": "copperline.example",
                "status": "replied",
            },
        ],
    }
    latest_file.write_text(json.dumps(latest_data), encoding="utf-8")

    ctx = lanes.load_context(profile, content_root=tmp_path)
    rows = [
        {
            "email": "ada@acme.example",
            "company": "Acme",
            "company_domain": "acme.example",
            "account_id": "",
            "tier": "A",
            "score": "90",
            "verdict": "",
        },
        {
            "email": "quinn@summitline.example",
            "company": "Riverbend Logistics",
            "company_domain": "copperline.example",
            "account_id": "",
            "tier": "B",
            "score": "50",
            "verdict": "send",
        },
    ]

    res = lanes.route(rows, [], ctx)
    lanes_dir = evals_dir(profile, tmp_path)
    lanes_dir.mkdir(parents=True, exist_ok=True)
    dec.write_state(res, lanes_dir / "lanes-state.jsonl", "2026-09-25")

    m = gd.build_model(profile, tmp_path)
    html = va._accounts_view(m)
    rows_by_co = _rows(html)

    # Riverbend Logistics was "replied" -> routed to hold: engaged-account -> Waiting on you
    assert "Riverbend Logistics" in rows_by_co
    rl_row = rows_by_co["Riverbend Logistics"]
    assert "Waiting on you" in rl_row
    assert "they replied" in rl_row

    # Acme was "customer" -> shows no "they replied"
    assert "they replied" not in rows_by_co["Acme"]
