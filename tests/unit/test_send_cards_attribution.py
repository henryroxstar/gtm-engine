"""R6.1: a card carries what evidence each person was chosen on, and says so only when there is some.

The three fields ride the existing member record; the merge labels a sequence is loaded with do not
move, and a card with no such evidence is exactly what it was before (bytes, not just meaning).
"""

from __future__ import annotations

from gtm_core.send_cards import _process_send_cell, generate_cards, generate_cards_page

MEMBER = {
    "name": "Ana Reyes",
    "email": "ana@northwind.example.test",
    "company": "Northwind",
    "level": "director",
    "country": "SG",
    "opener": "Northwind joined an agent pilot.",
    "source_url": "https://register.example.test/cohort",
    "capture_date": "2026-10-01",
}
LISTED = {**MEMBER, "signal_class": "source_list", "premise_via": "source", "source_id": "reg-1"}


def _cell(members):
    return {
        "cell_id": "cell-a",
        "seat": "security",
        "segment": "enterprise",
        "angle": "k1",
        "is_personalised": True,
        "members": members,
        "example_member": {"name": "Ana Reyes"},
        "sequence_id": "seq-a",
        "step_id": "step-a",
    }


def test_the_fields_map_onto_the_card_member():
    (card,) = generate_cards([_cell([LISTED])])
    m = card.members[0]
    assert (m.signal_class, m.premise_via, m.source_id) == ("source_list", "source", "reg-1")


def test_a_card_with_no_evidence_serialises_exactly_as_before():
    (card,) = generate_cards([_cell([MEMBER])])
    assert not {"signal_class", "premise_via", "source_id"} & set(card.to_dict()["members"][0])


def test_a_card_with_evidence_serialises_it():
    (card,) = generate_cards([_cell([LISTED])])
    assert card.to_dict()["members"][0]["source_id"] == "reg-1"


def test_the_page_names_the_class_in_plain_words_only_when_there_is_one():
    plain = generate_cards_page([_cell([MEMBER])])
    listed = generate_cards_page([_cell([LISTED])])
    assert "on a published list" not in plain and "on a published list" in listed
    assert "(source_list)" not in listed  # the stored word is not what the reader is shown


def _draft(tmp_path, members):
    (tmp_path / "realshape").mkdir()
    pending = tmp_path / "pending"
    pending.mkdir()
    card = _cell(members)
    path, payload = _process_send_cell(
        card, "cell-a", 1, None, "realshape", tmp_path, "run-1", pending, [], []
    )
    return payload


def test_the_draft_carries_attribution_beside_the_rows_and_never_inside_them(tmp_path):
    payload = _draft(
        tmp_path, [LISTED, {**MEMBER, "name": "Bo Tan", "email": "bo@contoso.example.test"}]
    )
    assert payload["attribution"] == [
        {
            "email": "ana@northwind.example.test",
            "signal_class": "source_list",
            "premise_via": "source",
            "source_id": "reg-1",
        }
    ]
    for row in payload["prospect_list"]:
        assert not {"signal_class", "premise_via", "source_id", "attribution"} & set(row)
    assert set(payload["prospect_list"][0]) == {
        "Email",
        "First Name",
        "Last Name",
        "Company",
        "Why Now",
    }


def test_a_draft_with_no_evidence_has_no_attribution_key(tmp_path):
    assert "attribution" not in _draft(tmp_path, [MEMBER])


def test_someone_taken_off_the_list_is_not_attributed(tmp_path):
    off = {**LISTED, "email": "off@northwind.example.test", "ticked": False}
    payload = _draft(tmp_path, [LISTED, off])
    assert [a["email"] for a in payload["attribution"]] == ["ana@northwind.example.test"]
