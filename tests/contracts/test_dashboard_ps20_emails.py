"""PS20 Phase 2 — the Emails tab: one row per registered sequence, opening to its emails
(PRD Phase 2)."""

import copy
import json
import re

from gtm_core import email_campaign_dashboard as gd
from gtm_core import prospects_consolidate as pc
from gtm_core.email_campaign_dashboard import views_emails as ve
from gtm_core.email_campaign_dashboard.config import SECTIONS
from gtm_core.email_campaign_dashboard.format import _seat_label
from tests.contracts.dashboard_page import block, section, section_ids
from tests.contracts.test_dashboard_ps20_injection import ESCAPED_SCRIPT, RAW_SCRIPT, SCRIPT_PAYLOAD
from tests.contracts.test_dashboard_ps20_trust import _fig, _fixture_10_24_1
from tests.contracts.test_dashboard_tenant_prose import DATE, _full_fixture
from tests.contracts.test_dashboard_tenant_prose import SLUG as FULL
from tests.test_email_campaign_dashboard import _seed, _seed_operational

HEAD = [
    "For whom",
    "First subject",
    "The argument it opens on",
    "People",
    "Emails",
    "Checks",
    "Go-live",
]


def _rows(html):
    return re.findall(r'<tr data-sequence="[^"]*">.*?</tr>', section(html, "email-table"), re.S)


def _scoped_full(tmp_path, *, register_dated=False):
    profile = _full_fixture(tmp_path)
    if register_dated:  # register the dated spec, so its samples belong to a row
        cells = pc._prospects_dir(profile, tmp_path) / "sequences" / "cells.toml"
        text = cells.read_text(encoding="utf-8")
        cells.write_text(
            text.replace("spec-demo-2026-08-18.md", f"spec-demo-{DATE}.md"), encoding="utf-8"
        )
    return gd.scope_to_campaign(gd.build_model(profile, tmp_path), FULL)


def test_one_row_per_registered_sequence_with_the_prds_columns(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    html = ve._emails_view(m)
    assert set(section_ids(html)) <= SECTIONS["emails"]
    assert re.findall(r"<th>([^<]+)</th>", section(html, "email-table"))[:7] == HEAD
    (row,) = _rows(html)
    msg = m["messages"][0]
    # `_seed`'s audience: seats architect, cto, security; segment enterprise (probed 2026-09-25).
    assert "<td>architect, cto, security · enterprise</td>" in row
    assert "your first security review" in row and msg["copy"][0]["opener"][:40] in row
    assert _fig(row, "email-people-S1") == str(sum(c["enrolled"] for c in msg["audience"]))
    assert _fig(row, "email-steps-S1") == "1"
    assert '<span class="pill">PASS</span>' in row and "no blocking problems" in row
    assert _fig(row, "email-word-S1") == "paused"  # _seed's snapshot row says so
    # The go-live word is a plain word, not an alarm: no warn/risk class on its pill.
    assert '<td><span class="pill" data-figure="email-word-S1">' in row
    # The row's drawer spans the whole table: seven columns, written out, not read off `_HEAD`.
    assert len(HEAD) == 7 and '<tr class="more"><td colspan="7">' in html
    assert "Each row is a sequence as it is registered. A row opens to its emails." in html


def _go_word(m):
    return _fig(ve._emails_view(m), "email-word-S1")


def test_the_go_live_word_reads_the_snapshot_and_the_campaign_record(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    unreadable = copy.deepcopy(m)
    unreadable["status"]["snapshot"]["unreadable"] = True
    assert _go_word(unreadable) == "unknown"  # a snapshot nobody could read claims nothing
    no_row = copy.deepcopy(m)
    no_row["status"]["sequences"] = []
    assert _go_word(no_row) == "staged"  # a campaign lists it, the snapshot has no row
    no_record = copy.deepcopy(no_row)
    no_record["campaigns"]["campaigns"] = []
    assert _go_word(no_record) == "none"  # and no campaign lists it either


def test_the_first_subject_is_the_first_email_that_has_one_and_the_opener_is_clipped(tmp_path):
    m = gd.build_model(_seed(tmp_path), tmp_path)
    (first,) = m["messages"][0]["copy"]
    m["messages"][0]["copy"] = [
        {**first, "subject": "", "opener": "a reply in the thread"},
        {**first, "step": 2, "subject": "the second subject", "opener": "y" * 250},
    ]
    (row,) = _rows(ve._emails_view(m))
    assert "<td><code>the second subject</code></td>" in row
    assert "a reply in the thread" not in row
    assert "y" * 200 in row and "y" * 201 not in row


def test_the_checks_cell_counts_in_words():
    assert ve._checks({"verdict": "FAIL", "errors": 1}).endswith(" 1 blocking problem")
    assert ve._checks({"verdict": "FAIL", "errors": 0}).endswith(" a blocking check failed")


def test_for_whom_is_seats_then_segments_each_side_dropped_when_empty():
    def whom(*audience):
        return ve._for_whom({"audience": [{"seat": s, "segment": g} for s, g in audience]})

    assert whom(("cto", "enterprise"), ("architect", "enterprise"), ("cto", "startup")) == (
        f"{_seat_label('architect')}, {_seat_label('cto')} · enterprise, startup"
    )
    assert whom(("cto", "")) == _seat_label("cto")  # no segment: no separator either
    assert whom(("", "enterprise")) == "enterprise"  # no seat: no leading separator
    assert whom() == "—"
    # Segments sort too, whatever order the audience arrives in.
    assert whom(("cto", "startup"), ("cto", "enterprise")) == "cto · enterprise, startup"
    # The internal `unknown` seat is shown by its label, never its key.
    assert whom(("unknown", "")) == _seat_label("unknown") == "other"


def test_a_second_registration_is_a_second_row(tmp_path):
    profile = _seed(tmp_path)
    cells = pc._prospects_dir(profile, tmp_path) / "sequences" / "cells.toml"
    cells.write_text(
        cells.read_text(encoding="utf-8") + '\n[[sequence]]\nid = "S2"\ntitle = "Second"\n'
        'csv = "list.csv"\nspec = "spec-demo-2026-08-18.md"\n',
        encoding="utf-8",
    )
    m = gd.build_model(profile, tmp_path)
    assert len(_rows(ve._emails_view(m))) == len(m["messages"]) == 2


def test_checks_read_unchecked_passed_or_problems(tmp_path):
    bare = gd.build_model(_seed(tmp_path, "bare", lint=False), tmp_path)
    assert "unchecked" in _rows(ve._emails_view(bare))[0]
    profile = _seed(tmp_path, "failed")
    lint = pc._pool_dir(profile, tmp_path) / "lint-S1.json"
    lint.write_text(
        json.dumps(
            {**json.loads(lint.read_text(encoding="utf-8")), "verdict": "FAIL", "errors": 2}
        ),
        encoding="utf-8",
    )
    row = _rows(ve._emails_view(gd.build_model(profile, tmp_path)))[0]
    assert '<span class="pill risk" data-risk="blocking-check">FAIL</span>' in row
    assert "2 blocking problems" in row


def test_a_revised_sequence_is_not_cleared_to_start_on_its_row(tmp_path):
    row = _rows(ve._emails_view(gd.build_model(_seed_operational(tmp_path), tmp_path)))[0]
    assert '<span class="pill risk" data-risk="re-push">not cleared to start</span>' in row


def test_the_go_live_word_is_the_one_rule_per_sequence(tmp_path):
    html = ve._emails_view(gd.build_model(_fixture_10_24_1(tmp_path), tmp_path))
    assert _fig(html, "email-word-S1") == "started"  # sent 7, no status


def test_a_row_opens_to_its_emails_and_its_own_samples(tmp_path):
    html = ve._emails_view(_scoped_full(tmp_path, register_dated=True))
    more = block(html, 'data-more="S1"')
    assert "Email 1" in more and "riley@summitline.example" in more
    assert "Hi {{First Name}}" in more  # the template, merge tags intact, beside the sample
    assert section(html, "hand-sent") == ""  # every sample belongs to a registered sequence


def test_samples_no_registered_sequence_uses_are_hand_sent(tmp_path):
    html = ve._emails_view(_scoped_full(tmp_path))
    hand = section(html, "hand-sent")
    assert "riley@summitline.example" in hand and "<strong>Subject:</strong>" in hand
    assert "a second renderer is how a page starts showing copy nobody sends" in hand
    # S1's drawer holds S1's own samples only; the unclaimed ones are not repeated there.
    more = block(html, 'data-more="S1"')
    assert "riley@summitline.example" not in more and "{{First Name}}" not in more
    # The fixture's lint carries drift, so the drawer says the check is of the reviewed files.
    assert "The check describes the reviewed files, not what would go out today." in more


def test_the_table_note_says_when_the_later_emails_land(tmp_path):
    m = _scoped_full(tmp_path)
    (t1,) = m["samples"]["touches"]
    m["samples"]["touches"].append({**t1, "n": 2, "day": 5})
    note = re.search(
        r'<p class="note">Each row is.*?</p>', section(ve._emails_view(m), "email-table")
    )
    assert "Touch 2 on day 5 — the same for everyone" in note.group(0)


def test_the_one_to_one_emails_are_a_short_list_with_their_bodies(tmp_path):
    m = _scoped_full(tmp_path)
    assert m["packs"]["packs"] == [] and m["samples"]["packs"]  # a pre-capability-era pack
    packs = section(ve._emails_view(m), "packs-list")
    assert "Copperline Labs" in packs and "a question about agent audit" in packs
    assert f"1:1 emails, written by hand ({len(m['samples']['packs'])})</h2>" in packs
    # The campaign's own samples win over the rollup, which carries no bodies.
    m["packs"] = {"packs": [{"account": "Rollup Co"}]}
    packs = section(ve._emails_view(m), "packs-list")
    assert "Rollup Co" not in packs and "Copperline Labs" in packs


def test_every_value_on_the_tab_is_escaped(tmp_path, monkeypatch):
    """Every field `views_emails` interpolates carries the payload; none reaches the page raw.
    The go-live word and the later-touches sentence are computed (a closed word set; int
    touch numbers and days), so they are stubbed to prove the VIEW escapes what it is handed."""
    m = _scoped_full(tmp_path)
    msg = m["messages"][0]
    msg["audience"][0].update(seat=SCRIPT_PAYLOAD, segment=SCRIPT_PAYLOAD)
    msg["copy"][0].update(subject=SCRIPT_PAYLOAD, opener=SCRIPT_PAYLOAD)
    msg["lint"] = {**(msg.get("lint") or {}), "verdict": SCRIPT_PAYLOAD}
    for p in m["samples"]["packs"]:
        p.update(company=SCRIPT_PAYLOAD, date=SCRIPT_PAYLOAD, capability=SCRIPT_PAYLOAD)
    monkeypatch.setattr(ve, "sequence_word", lambda *a: SCRIPT_PAYLOAD)
    monkeypatch.setattr(ve, "_later_touches", lambda m: SCRIPT_PAYLOAD)
    rollup = copy.deepcopy(m)
    rollup["samples"]["packs"] = []
    rollup["packs"] = {"packs": [{"account": SCRIPT_PAYLOAD}]}
    for html in (ve._emails_view(m), ve._emails_view(rollup)):
        assert RAW_SCRIPT not in html and ESCAPED_SCRIPT in html


def test_no_heading_uses_tool_jargon_and_no_state_column(tmp_path):
    html = ve._emails_view(_scoped_full(tmp_path))
    for text in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S):
        for word in ("sequencer", "enrolled", "merge tag", "variant", "cell"):
            assert word not in text.lower(), (word, text)
    # The column that HOLDS the go-live word is headed "Go-live", and no header anywhere on
    # the tab says "State" (`test_email_campaign_dashboard.py:473-486`). Reading the column by
    # what it holds is what makes this fail if the header is renamed back.
    heads = re.findall(r"<th[^>]*>([^<]+)</th>", section(html, "email-table"))
    col = heads.index("Go-live")
    cells = re.findall(r"<td[^>]*>(.*?)</td>", _rows(html)[0], re.S)
    assert 'data-figure="email-word-' in cells[col]
    assert [
        h for h in re.findall(r"<th[^>]*>([^<]+)</th>", html) if h.strip().lower() == "state"
    ] == []
