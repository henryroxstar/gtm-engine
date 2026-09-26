"""Tests for W8 send cards review surface (R8.1 - R8.4).

R8.1: One card per cell_id, plain-words title, emails hidden by default, example member named,
      every personalised opener/source listed, panel verdict absent in initial DOM and present
      after decision event, revealed_before_decision recorded.
R8.2: Closed decision set ('send this cell', 'not this wave', 'rewrite'), untick members,
      blank -> 'not decided', unknown decision refused at apply.
R8.3: apply writes .pending/<run-id>.enroll-draft.json only for 'send this cell', excludes unticked,
      removes and lists suppressed, matches Step 6a schema, carries card_ids, source: 'send-cards',
      and expires_on under R0.2; rewrite rows -> repair queue.
R8.4: Gate-2 preview check: refuses without source: 'send-cards' and card_ids when send_cards_required
      is true/absent/unreadable; with false, draft is offered.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest

from gtm_core import send_cards
from gtm_core.send_cards import (
    VALID_DECISIONS,
    check_gate2_preview,
    generate_cards_page,
    is_send_cards_required,
    render_gate2_preview,
    send_cards_apply,
)


class SimpleHTMLDOM(HTMLParser):
    """Minimal HTML parser to inspect initial DOM elements for headless test."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.classes: list[str] = []
        self.elements_by_class: dict[str, list[dict]] = {}
        self.emails: list[str] = []
        self._current_attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: v or "" for k, v in attrs}
        self.tags.append(tag)
        if "class" in attr_dict:
            for cls in attr_dict["class"].split():
                self.classes.append(cls)
                self.elements_by_class.setdefault(cls, []).append({"tag": tag, "attrs": attr_dict})

    def handle_data(self, data: str) -> None:
        if "@" in data and "." in data:
            self.emails.append(data.strip())


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_cells():
    return [
        {
            "cell_id": "base:enterprise:technical:run500-tech",
            "cohort": "enterprise",
            "seat": "technical",
            "message_variant": "run500-tech",
            "angle": "API Gateway Architecture",
            "segment": "enterprise",
            "premise_ids": ["premise-gateway-auth"],
            "proof_ids": ["proof-fhir-proxy"],
            "gate_receipt": {
                "integrity": "pass",
                "suppression": "pass",
                "compliance": "pass",
                "freshness": "pass",
            },
            "is_personalised": True,
            "example_member": {
                "name": "Ada Lovelace",
                "email": "ada@lovelace-analytics.example.com",
                "company": "Lovelace Analytics",
                "subject": "Agent identity in production at Lovelace Analytics",
                "body": "<p>Hi Ada, saw your recent talk on proxy security...</p>",
            },
            "members": [
                {
                    "name": "Ada Lovelace",
                    "email": "ada@lovelace-analytics.example.com",
                    "company": "Lovelace Analytics",
                    "industry": "Software",
                    "country": "US",
                    "level": "C-Level",
                    "seat": "technical",
                    "opener": "Saw your keynote on multi-agent auth at TechCon",
                    "source_url": "https://example.com/talks/ada",
                    "capture_date": "2026-09-01",
                    "signal_kind": "event",
                    "ticked": True,
                },
                {
                    "name": "Charles Babbage",
                    "email": "charles@babbage-engines.example.com",
                    "company": "Babbage Engines",
                    "industry": "Hardware",
                    "country": "UK",
                    "level": "VP",
                    "seat": "technical",
                    "opener": "Noticed your architecture blog post on distributed proxies",
                    "source_url": "https://example.com/blog/charles",
                    "capture_date": "2026-08-15",
                    "signal_kind": "event",
                    "ticked": True,
                },
            ],
            "panel_verdicts": [
                {
                    "persona": "Enterprise Architect",
                    "verdict": "pass",
                    "notes": "Technical win and architectural proof clear.",
                },
                {
                    "persona": "CISO",
                    "verdict": "pass",
                    "notes": "Security framing aligns with NIST gate.",
                },
            ],
            "sequence_id": "seq-tech-01",
            "step_id": "step-tech-01",
            "steps": [
                {
                    "step_id": "step-tech-01",
                    "variants": [
                        {
                            "subject": "Agent identity in production at {{Company}}",
                            "content": "<p>Hi {{First Name}}, saw your talk...</p>",
                            "preheader": "",
                        }
                    ],
                }
            ],
        },
        {
            "cell_id": "base:startup:founder:run500-founder",
            "cohort": "startup",
            "seat": "founder",
            "message_variant": "run500-founder",
            "angle": "Time to First Revenue",
            "segment": "startup",
            "premise_ids": ["premise-startup-velocity"],
            "proof_ids": ["proof-seed-growth"],
            "gate_receipt": {
                "integrity": "pass",
                "suppression": "pass",
                "compliance": "pass",
                "freshness": "pass",
            },
            "is_personalised": False,
            "example_member": {
                "name": "Grace Hopper",
                "email": "grace@compiler-labs.example.com",
                "company": "Compiler Labs",
                "subject": "Quick question for Compiler Labs",
                "body": "<p>Hi Grace, building agent pipelines is tough...</p>",
            },
            "members": [
                {
                    "name": "Grace Hopper",
                    "email": "grace@compiler-labs.example.com",
                    "company": "Compiler Labs",
                    "industry": "DevTools",
                    "country": "US",
                    "level": "Founder",
                    "seat": "founder",
                    "ticked": True,
                }
            ],
            "panel_verdicts": [
                {
                    "persona": "Founder",
                    "verdict": "pass",
                    "notes": "Direct, concise, no fluff.",
                }
            ],
            "sequence_id": "seq-founder-01",
            "step_id": "step-founder-01",
            "steps": [
                {
                    "step_id": "step-founder-01",
                    "variants": [
                        {
                            "subject": "Quick question for {{Company}}",
                            "content": "<p>Hi {{First Name}}, building agent pipelines...</p>",
                            "preheader": "",
                        }
                    ],
                }
            ],
        },
    ]


# ── R8.1 Tests: Card generator ────────────────────────────────────────────────


def test_r8_1_one_card_per_cell_id(sample_cells):
    """R8.1: exactly one card per cell_id (cohort x seat x message_variant)."""
    cards = send_cards.generate_cards(sample_cells)
    assert len(cards) == 2
    assert [c.cell_id for c in cards] == [
        "base:enterprise:technical:run500-tech",
        "base:startup:founder:run500-founder",
    ]


def test_r8_1_card_title_in_words(sample_cells):
    """R8.1: card title in plain words (seat · angle · segment)."""
    cards = send_cards.generate_cards(sample_cells)
    c0 = cards[0]
    # Expect words representing seat, angle, segment
    assert "technical" in c0.title.lower()
    assert "api gateway" in c0.title.lower()
    assert "enterprise" in c0.title.lower()
    assert " · " in c0.title


def test_r8_1_emails_hidden_by_default(sample_cells):
    """R8.1: emails are hidden by default in rendered HTML."""
    html_content = generate_cards_page(sample_cells)
    dom = SimpleHTMLDOM()
    dom.feed(html_content)

    # Email elements must have a hidden class or hidden attribute
    hidden_elements = dom.elements_by_class.get("email-hidden", [])
    assert len(hidden_elements) > 0
    for el in hidden_elements:
        assert "email-hidden" in el["attrs"].get("class", "")


def test_r8_1_example_member_named(sample_cells):
    """R8.1: the email rendered for a named example member."""
    cards = send_cards.generate_cards(sample_cells)
    c0 = cards[0]
    assert c0.example_member is not None
    assert c0.example_member["name"] == "Ada Lovelace"
    assert "Lovelace Analytics" in c0.example_member["company"]

    html_content = generate_cards_page(sample_cells)
    assert "Ada Lovelace" in html_content
    assert "Lovelace Analytics" in html_content


def test_r8_1_personalised_cards_list_every_member_opener_and_source(sample_cells):
    """R8.1: for personalised cells, EVERY member's opening line with its source link and capture date."""
    cards = send_cards.generate_cards(sample_cells)
    c0 = cards[0]
    assert c0.is_personalised is True
    assert len(c0.members) == 2

    # Verify both members have opener, source_url, capture_date
    m0, m1 = c0.members[0], c0.members[1]
    assert m0.opener == "Saw your keynote on multi-agent auth at TechCon"
    assert m0.source_url == "https://example.com/talks/ada"
    assert m0.capture_date == "2026-09-01"

    assert m1.opener == "Noticed your architecture blog post on distributed proxies"
    assert m1.source_url == "https://example.com/blog/charles"
    assert m1.capture_date == "2026-08-15"

    html_content = generate_cards_page(sample_cells)
    assert "Saw your keynote on multi-agent auth at TechCon" in html_content
    assert "https://example.com/talks/ada" in html_content
    assert "2026-09-01" in html_content
    assert "Noticed your architecture blog post on distributed proxies" in html_content
    assert "https://example.com/blog/charles" in html_content
    assert "2026-08-15" in html_content


def test_r8_1_panel_verdict_absent_from_initial_dom_and_present_after_decision(sample_cells):
    """R8.1: panel verdict is absent from the initial DOM state and present only after a decision event (headless DOM test)."""
    html_content = generate_cards_page(sample_cells)
    dom = SimpleHTMLDOM()
    dom.feed(html_content)

    # Initial DOM state: panel verdicts are absent from rendered markup
    assert "panel-verdict-revealed" not in dom.classes
    assert len(dom.elements_by_class.get("panel-verdict-revealed", [])) == 0

    # Simulate decision event in client state
    state = send_cards.simulate_client_decision(
        sample_cells,
        card_id="base:enterprise:technical:run500-tech",
        decision="send this cell",
        reveal_first=False,
    )
    assert state["panel_verdict_rendered"] is True
    assert state["revealed_before_decision"] is False


def test_r8_1_export_records_revealed_before_decision(sample_cells):
    """R8.1: the export records revealed_before_decision."""
    # When user revealed panel verdict before deciding
    export_rev = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        revealed_before={"base:enterprise:technical:run500-tech": True},
    )
    c0 = next(
        c for c in export_rev["cards"] if c["cell_id"] == "base:enterprise:technical:run500-tech"
    )
    assert c0["revealed_before_decision"] is True

    # When user did not reveal panel verdict before deciding
    export_not_rev = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        revealed_before={"base:enterprise:technical:run500-tech": False},
    )
    c0_not = next(
        c
        for c in export_not_rev["cards"]
        if c["cell_id"] == "base:enterprise:technical:run500-tech"
    )
    assert c0_not["revealed_before_decision"] is False


# ── R8.2 Tests: Decision vocabulary ───────────────────────────────────────────


def test_r8_2_closed_decision_set():
    """R8.2: closed set: send this cell, not this wave, rewrite."""
    assert VALID_DECISIONS == frozenset({"send this cell", "not this wave", "rewrite"})


def test_r8_2_blank_decision_exports_as_not_decided(sample_cells):
    """R8.2: a blank decision exports as 'not decided' and sends nothing."""
    export = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": ""},  # left blank
    )
    c0 = next(c for c in export["cards"] if c["cell_id"] == "base:enterprise:technical:run500-tech")
    assert c0["decision"] == "not decided"


def test_r8_2_unknown_decision_refused_at_apply(tmp_path, sample_cells):
    """R8.2: unknown decision word refused at apply."""
    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "approve"},  # invalid word!
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown decision 'approve'"):
        send_cards_apply(export_path, profile="demo", content_root=tmp_path)


def test_r8_2_personalised_members_can_be_unticked_individually(sample_cells):
    """R8.2: personalised members can be unticked individually."""
    export = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        unticked_members={
            "base:enterprise:technical:run500-tech": ["charles@babbage-engines.example.com"]
        },
    )
    c0 = next(c for c in export["cards"] if c["cell_id"] == "base:enterprise:technical:run500-tech")
    members = c0["members"]
    assert members[0]["email"] == "ada@lovelace-analytics.example.com"
    assert members[0]["ticked"] is True
    assert members[1]["email"] == "charles@babbage-engines.example.com"
    assert members[1]["ticked"] is False


# ── R8.3 Tests: apply ─────────────────────────────────────────────────────────


def test_r8_3_apply_writes_draft_only_for_send_this_cell(tmp_path, sample_cells):
    """R8.3: writes one enroll draft only for 'send this cell'."""
    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={
            "base:enterprise:technical:run500-tech": "send this cell",
            "base:startup:founder:run500-founder": "not this wave",
        },
        run_id="run-1234",
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = send_cards_apply(export_path, profile="demo", content_root=tmp_path, run_id="run-1234")

    # Only one draft written for the approved sequence
    assert len(result.drafts) == 1
    assert result.drafts[0]["sequence_id"] == "seq-tech-01"
    pending_file = (
        tmp_path / "demo" / "prospects" / "sequences" / ".pending" / "run-1234.enroll-draft.json"
    )
    assert pending_file.is_file()


def test_r8_3_apply_unticked_members_excluded(tmp_path, sample_cells):
    """R8.3: unticked personalised members are excluded from the enroll draft."""
    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        unticked_members={
            "base:enterprise:technical:run500-tech": ["charles@babbage-engines.example.com"]
        },
        run_id="run-1234",
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = send_cards_apply(export_path, profile="demo", content_root=tmp_path, run_id="run-1234")

    draft = result.drafts[0]
    emails_in_draft = [r["Email"] for r in draft["prospect_list"]]
    assert emails_in_draft == ["ada@lovelace-analytics.example.com"]
    assert "charles@babbage-engines.example.com" not in emails_in_draft
    assert len(result.unticked_excluded) == 1
    assert result.unticked_excluded[0]["email"] == "charles@babbage-engines.example.com"


def test_r8_3_apply_suppressed_members_removed_and_listed(tmp_path, sample_cells):
    """R8.3: members suppressed since render are removed and listed."""
    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        run_id="run-1234",
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    # Simulate suppression ledger having Ada
    suppression_file = tmp_path / "demo" / "prospects" / "sequences" / ".pool" / "suppression.csv"
    suppression_file.parent.mkdir(parents=True, exist_ok=True)
    suppression_file.write_text(
        "email,domain,person,reason,created_at\n"
        "ada@lovelace-analytics.example.com,lovelace-analytics.example.com,p:ada lovelace@lovelace-analytics.example.com,dnc-optout,2026-09-25T00:00:00Z\n",
        encoding="utf-8",
    )

    result = send_cards_apply(export_path, profile="demo", content_root=tmp_path, run_id="run-1234")
    draft = result.drafts[0]
    emails_in_draft = [r["Email"] for r in draft["prospect_list"]]
    assert "ada@lovelace-analytics.example.com" not in emails_in_draft
    assert emails_in_draft == ["charles@babbage-engines.example.com"]
    assert len(result.suppressed_removed) == 1
    assert result.suppressed_removed[0]["email"] == "ada@lovelace-analytics.example.com"


def test_r8_3_apply_draft_matches_step_6a_schema_and_carries_required_fields(
    tmp_path, sample_cells
):
    """R8.3: draft matches Step 6a schema, carries card_ids, source: 'send-cards', and expires_on under R0.2."""
    from agent.gate_actions import parse_enroll_draft

    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={"base:enterprise:technical:run500-tech": "send this cell"},
        run_id="run-step6a",
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = send_cards_apply(
        export_path, profile="demo", content_root=tmp_path, run_id="run-step6a"
    )
    draft = result.drafts[0]

    # Validate against gate_actions Step 6a schema parser
    parsed = parse_enroll_draft(json.dumps(draft), source="test")
    assert parsed["tool"] == "import_prospects_to_sequence"
    assert parsed["sequence_id"] == "seq-tech-01"
    assert parsed["step_id"] == "step-tech-01"
    assert len(parsed["steps"]) == 1
    assert parsed["source"] == "send-cards"
    assert parsed["card_ids"] == ["base:enterprise:technical:run500-tech"]
    # Earliest signal: Charles is 2026-08-15, limit for enterprise event is 90 days -> 2026-11-13
    assert parsed["expires_on"] == "2026-11-13"


def test_r8_3_apply_rewrite_rows_go_to_repair_queue_with_note(tmp_path, sample_cells):
    """R8.3: rewrite rows go to the repair queue with the note. not this wave changes nothing."""
    export_data = send_cards.create_card_export(
        sample_cells,
        decisions={
            "base:enterprise:technical:run500-tech": "rewrite",
            "base:startup:founder:run500-founder": "not this wave",
        },
        notes={
            "base:enterprise:technical:run500-tech": "Re-angle hook to emphasize latency instead of proxy auth"
        },
        run_id="run-rewrite",
    )
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = send_cards_apply(
        export_path, profile="demo", content_root=tmp_path, run_id="run-rewrite"
    )
    assert len(result.drafts) == 0  # No draft written
    assert len(result.repair_rows) == 2
    for r in result.repair_rows:
        assert r["note"] == "Re-angle hook to emphasize latency instead of proxy auth"
        assert r["decision"] == "rewrite"

    repair_queue_file = (
        tmp_path / "demo" / "prospects" / "sequences" / ".pool" / "repair-queue.jsonl"
    )
    assert repair_queue_file.is_file()
    lines = [
        json.loads(line) for line in repair_queue_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len(lines) == 2
    assert lines[0]["email"] == "ada@lovelace-analytics.example.com"
    assert lines[0]["note"] == "Re-angle hook to emphasize latency instead of proxy auth"


# ── R8.4 Tests: Gate-2 preview check ──────────────────────────────────────────


def test_r8_4_gate2_preview_refused_when_source_or_card_ids_missing(tmp_path):
    """R8.4: refuses to offer approval for a draft without source: 'send-cards' and card_ids when send_cards_required is true."""
    profile_dir = tmp_path / "demo"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "settings.json").write_text(
        json.dumps({"send_cards_required": True}), encoding="utf-8"
    )

    draft_without_source = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": "seq-1",
        "step_id": "step-1",
        "steps": [],
        "prospect_list": [{"Email": "a@example.com"}],
        "card_ids": ["card-1"],
    }
    offered, reason = check_gate2_preview(draft_without_source, "demo", content_root=tmp_path)
    assert offered is False
    assert "source 'send-cards'" in reason

    draft_without_card_ids = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": "seq-1",
        "step_id": "step-1",
        "steps": [],
        "prospect_list": [{"Email": "a@example.com"}],
        "source": "send-cards",
    }
    offered, reason = check_gate2_preview(draft_without_card_ids, "demo", content_root=tmp_path)
    assert offered is False
    assert "card_ids" in reason


def test_r8_4_gate2_preview_refused_when_settings_json_absent_or_unreadable(tmp_path):
    """R8.4: absent or unreadable settings.json -> send_cards_required defaults to True."""
    profile_dir = tmp_path / "demo"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 1. Absent settings.json
    assert is_send_cards_required("demo", content_root=tmp_path) is True

    draft_without_source = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": "seq-1",
        "step_id": "step-1",
        "steps": [],
        "prospect_list": [{"Email": "a@example.com"}],
    }
    offered, reason = check_gate2_preview(draft_without_source, "demo", content_root=tmp_path)
    assert offered is False

    # 2. Corrupted settings.json
    (profile_dir / "settings.json").write_text("{not valid json", encoding="utf-8")
    assert is_send_cards_required("demo", content_root=tmp_path) is True
    offered, reason = check_gate2_preview(draft_without_source, "demo", content_root=tmp_path)
    assert offered is False


def test_r8_4_gate2_preview_offered_when_send_cards_required_false(tmp_path):
    """R8.4: with send_cards_required: false, draft without send-cards source is offered."""
    profile_dir = tmp_path / "demo"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "settings.json").write_text(
        json.dumps({"send_cards_required": False}), encoding="utf-8"
    )

    assert is_send_cards_required("demo", content_root=tmp_path) is False

    draft = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": "seq-1",
        "step_id": "step-1",
        "steps": [],
        "prospect_list": [{"Email": "a@example.com"}],
    }
    offered, reason = check_gate2_preview(draft, "demo", content_root=tmp_path)
    assert offered is True
    assert reason is None


def test_r8_4_gate2_preview_offered_when_compliant_and_lists_card_titles(tmp_path):
    """R8.4: compliant draft with source: 'send-cards' and card_ids is offered and lists card titles."""
    profile_dir = tmp_path / "demo"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "settings.json").write_text(
        json.dumps({"send_cards_required": True}), encoding="utf-8"
    )

    draft = {
        "tool": "import_prospects_to_sequence",
        "sequence_id": "seq-1",
        "step_id": "step-1",
        "steps": [],
        "prospect_list": [{"Email": "a@example.com"}],
        "source": "send-cards",
        "card_ids": ["base:enterprise:technical:run500-tech"],
        "card_titles": ["Technical · API Gateway · Enterprise"],
    }
    offered, reason = check_gate2_preview(draft, "demo", content_root=tmp_path)
    assert offered is True
    assert reason is None

    preview = render_gate2_preview(draft, "demo", content_root=tmp_path)
    assert preview["offered"] is True
    assert preview["card_titles"] == ["Technical · API Gateway · Enterprise"]
