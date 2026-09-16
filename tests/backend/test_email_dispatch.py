"""A11 — the email-enrollment dispatch: driven by an APPROVED draft, never the brain,
and (backend) by DECLARED metadata (`external_effect == "email_enroll"`), never the
node's name; BYOK-scoped, no shared destination fallback; dry_run never dispatches.

Mirrors tests/backend/test_publish_dispatch.py's structure and file placement (the
agent-level shared helper and the backend wrapper share one test file, one per
dispatch kind) for the second Python-only dispatcher — one path away from the LinkedIn
publish gate: closes the same class of gap agent.publish_dispatch closes, for
Saleshandy lead/prospect enrollment.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent import email_dispatch, permissions
from agent.email_dispatch import dispatch_approved_enrollment
from agent.graph import Graph, Node
from backend.email_dispatch import dispatch_backend_email_enroll, email_enroll_gated_node

_WS = "22222222-2222-4222-8222-222222222222"

#: The approved copy (client issue #244) — plain HTML as the model staged it.
STEPS = [
    {
        "step_id": "step-1",
        "variants": [
            {"subject": "A question about {{Company}}", "content": "<p>Hi &amp; hello</p>"}
        ],
    },
    {"step_id": "step-2", "variants": [{"subject": "", "content": "Following up"}]},
]

DRAFT = {
    "tool": "add_leads_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": STEPS,
    "lead_ids": [111, 222],
}

IMPORT_PROSPECTS_DRAFT = {
    "tool": "import_prospects_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": STEPS,
    "prospect_list": [
        {"Email": "a@example.com", "First Name": "Ada", "Why Now": "Opened a second site"},
        {"Email": "b@example.com", "First Name": "Bo", "Why Now": ""},
    ],
}


class FakeSaleshandy:
    """The three documented reads, answering as Saleshandy would for DRAFT's sequence —
    with the copy re-wrapped in Saleshandy's own HTML, which is not a wording change."""

    def __init__(self):
        self.sequences = [
            {"id": "seq-other", "steps": [{"id": "x"}]},
            {"id": "seq-1", "active": False, "steps": [{"id": "step-1"}, {"id": "step-2"}]},
        ]
        self.variants = {
            "step-1": [
                {
                    "id": "v1",
                    "payload": {
                        "subject": "A question about {{Company}}",
                        "content": '<div><span style="font-size: 13px;">Hi &amp;  hello</span></div>',
                        "preheader": None,
                    },
                }
            ],
            "step-2": [{"id": "v2", "payload": {"subject": "", "content": "<p>Following up</p>"}}],
        }
        self.fields = [
            {"id": "f-email", "label": "Email"},
            {"id": "f-first", "label": "First Name"},
            {"id": "f-why", "label": "Why Now"},
        ]
        self.calls: list[str] = []

    async def list_sequences(self, api_key, page, page_size):
        self.calls.append("sequences")
        start = (page - 1) * page_size
        return json.dumps({"payload": self.sequences[start : start + page_size]})

    async def step_variants(self, api_key, sequence_id, step_id):
        self.calls.append(f"step:{step_id}")
        return json.dumps(self.variants.get(step_id, []))

    async def list_fields(self, api_key):
        self.calls.append("fields")
        return json.dumps({"payload": self.fields})


@pytest.fixture(autouse=True)
def saleshandy():
    fake = FakeSaleshandy()
    server = "agent.mcp.saleshandy.server"
    with (
        patch(f"{server}._list_sequences_page_request", fake.list_sequences),
        patch(f"{server}._get_step_variants_request", fake.step_variants),
        patch(f"{server}._list_fields_request", fake.list_fields),
    ):
        yield fake


class FakeLedgers:
    # `profile` mirrors the real Ledgers accessor: the dispatcher resolves the enrollment
    # lane state from it, and refuses fail-closed when it is absent.
    def __init__(self, raise_on_write: bool = False, profile: str = "example") -> None:
        self.raise_on_write = raise_on_write
        self.profile = profile
        self.history: list[dict] = []

    def append_history(self, entry):
        if self.raise_on_write:
            raise RuntimeError("disk full")
        self.history.append(entry)


def _agent_cfg(api_key: str | None = "sk-test-key"):
    return SimpleNamespace(saleshandy_api_key=api_key)


# ── agent-level shared helper (agent.email_dispatch.dispatch_approved_enrollment) ──


def test_dispatch_enrolls_via_add_leads_and_audits():
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ) as mock_add:
        outcome = asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(), FakeLedgers(), draft=DRAFT)
        )
    assert outcome.ok is True and outcome.status == "enrolled"
    mock_add.assert_called_once_with("sk-test-key", [111, 222], "seq-1", "step-1", None, None)


def test_dispatch_imports_prospects_in_the_documented_fields_shape():
    """Rows keyed by field label become Saleshandy's ``{"fields": [{id, value}]}`` import
    shape — every approved value, including the personalised Why Now, reaches its field."""
    with patch(
        "agent.mcp.saleshandy.server._import_prospects_to_sequence_request",
        return_value='{"requestId": "r-1"}',
    ) as mock_import:
        outcome = asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(), FakeLedgers(), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is True and outcome.status == "enrolled"
    mock_import.assert_called_once_with(
        "sk-test-key",
        [
            {
                "fields": [
                    {"id": "f-email", "value": "a@example.com"},
                    {"id": "f-first", "value": "Ada"},
                    {"id": "f-why", "value": "Opened a second site"},
                ]
            },
            {
                "fields": [
                    {"id": "f-email", "value": "b@example.com"},
                    {"id": "f-first", "value": "Bo"},
                ]
            },
        ],
        "step-1",
        False,
        "",
    )


def _refused(draft=DRAFT):
    """Dispatch ``draft``; return (outcome, ledgers, add_mock, import_mock)."""
    with (
        patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add,
        patch("agent.mcp.saleshandy.server._import_prospects_to_sequence_request") as mock_import,
    ):
        ledgers = FakeLedgers()
        outcome = asyncio.run(dispatch_approved_enrollment(_agent_cfg(), ledgers, draft=draft))
    return outcome, ledgers, mock_add, mock_import


def _assert_nothing_enrolled(outcome, ledgers, mock_add, mock_import, reason: str):
    assert outcome.ok is False and outcome.status == "enroll_failed"
    assert reason in outcome.detail
    mock_add.assert_not_called()
    mock_import.assert_not_called()
    assert ledgers.history[0]["event"] == "enroll_failed"
    assert reason in ledgers.history[0]["detail"]


def test_wording_changed_in_saleshandy_after_approval_enrolls_nobody(saleshandy):
    """Client issue #244: enrollment uses exactly the approved copy, or nothing."""
    saleshandy.variants["step-1"][0]["payload"]["subject"] = "A different question"
    _assert_nothing_enrolled(*_refused(), reason="'step-1'")


def test_a_changed_link_is_a_changed_copy(saleshandy):
    """Tags are dropped to compare wording, so link and image addresses are compared on
    their own — otherwise a link swapped in Saleshandy after approval would enroll."""
    approved = {
        **STEPS[1],
        "variants": [
            {"subject": "", "content": '<a href="https://example.com/a">Following up</a>'}
        ],
    }
    draft = {**DRAFT, "steps": [STEPS[0], approved]}
    live = saleshandy.variants["step-2"][0]["payload"]
    live["content"] = '<p><a href="https://example.com/a">Following up</a></p>'
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ):
        assert asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(), FakeLedgers(), draft=draft)
        ).ok

    live["content"] = '<p><a href="https://example.net/b">Following up</a></p>'
    _assert_nothing_enrolled(*_refused(draft), reason="'step-2'")


def test_an_active_sequence_enrolls_nobody(saleshandy):
    """Enrolling into a sequence that is already sending would send — the draft's
    sequence_id is model-written, so being paused is checked, not assumed."""
    saleshandy.sequences[1]["active"] = True
    _assert_nothing_enrolled(*_refused(), reason="not paused")

    del saleshandy.sequences[1]["active"]  # a missing status is not proof of paused
    _assert_nothing_enrolled(*_refused(), reason="not paused")


def test_an_unexpected_response_shape_refuses_instead_of_raising(saleshandy):
    saleshandy.sequences[1]["steps"] = ["step-1", "step-2"]
    _assert_nothing_enrolled(*_refused(), reason="could not read")

    saleshandy.sequences[1]["steps"] = [{"id": "step-1"}, {"id": "step-2"}]
    saleshandy.variants["step-1"] = [{"id": "v1", "payload": ["not", "an", "object"]}]
    _assert_nothing_enrolled(*_refused(), reason="'step-1'")


def test_a_step_the_approval_never_saw_enrolls_nobody(saleshandy):
    saleshandy.sequences[1]["steps"].append({"id": "step-3"})
    _assert_nothing_enrolled(*_refused(), reason="not the approved steps")


def test_an_added_or_dropped_variant_enrolls_nobody(saleshandy):
    extra = {"id": "v3", "payload": {"subject": "", "content": "Following up"}}
    saleshandy.variants["step-2"].append(extra)
    _assert_nothing_enrolled(*_refused(), reason="'step-2'")

    saleshandy.variants["step-2"] = []
    _assert_nothing_enrolled(*_refused(), reason="'step-2'")


def test_a_sequence_found_on_a_later_page_is_checked(saleshandy, monkeypatch):
    monkeypatch.setattr(email_dispatch, "_SEQUENCE_PAGE_SIZE", 1)
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ):
        outcome = asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(), FakeLedgers(), draft=DRAFT)
        )
    assert outcome.ok is True
    assert saleshandy.calls.count("sequences") == 2


def test_a_sequence_saleshandy_does_not_have_enrolls_nobody(saleshandy):
    saleshandy.sequences = [s for s in saleshandy.sequences if s["id"] != "seq-1"]
    _assert_nothing_enrolled(*_refused(), reason="not found")


def test_an_unreadable_saleshandy_response_enrolls_nobody():
    async def _error(*args):
        return "[saleshandy-error] HTTP 500"

    with patch("agent.mcp.saleshandy.server._get_step_variants_request", _error):
        _assert_nothing_enrolled(*_refused(), reason="HTTP 500")


def test_a_field_label_saleshandy_does_not_have_enrolls_nobody(saleshandy):
    """Dropping an unknown field would enroll people without a value the copy relies on."""
    saleshandy.fields = [f for f in saleshandy.fields if f["label"] != "Why Now"]
    _assert_nothing_enrolled(*_refused(IMPORT_PROSPECTS_DRAFT), reason="Why Now")


def test_a_field_label_saleshandy_has_twice_enrolls_nobody(saleshandy):
    saleshandy.fields.append({"id": "f-why-2", "label": "why now"})
    _assert_nothing_enrolled(*_refused(IMPORT_PROSPECTS_DRAFT), reason="Why Now")


def test_a_draft_without_the_approved_copy_enrolls_nobody():
    no_copy = {k: v for k, v in DRAFT.items() if k != "steps"}
    _assert_nothing_enrolled(*_refused(no_copy), reason="no sequence copy")


def test_successful_dispatch_is_audited_with_the_approved_request_shape():
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ):
        ledgers = FakeLedgers()
        asyncio.run(dispatch_approved_enrollment(_agent_cfg(), ledgers, draft=DRAFT))
    entry = ledgers.history[0]
    assert entry["event"] == "enrolled"
    assert entry["tool"] == "add_leads_to_sequence"
    assert entry["sequence_id"] == "seq-1"
    assert entry["lead_count"] == 2
    assert entry["detail"] is None


def test_dispatch_without_api_key_fails_closed_and_never_calls_saleshandy():
    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add:
        ledgers = FakeLedgers()
        outcome = asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(api_key=None), ledgers, draft=DRAFT)
        )
    assert outcome.ok is False and outcome.status == "not_configured"
    mock_add.assert_not_called()
    assert ledgers.history == [], "an un-dispatched enrollment must not be audited as an attempt"


def test_saleshandy_error_response_is_audited_as_enroll_failed():
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value="[saleshandy-error] HTTP 429",
    ):
        ledgers = FakeLedgers()
        outcome = asyncio.run(dispatch_approved_enrollment(_agent_cfg(), ledgers, draft=DRAFT))
    assert outcome.ok is False and outcome.status == "enroll_failed"
    assert outcome.detail == "[saleshandy-error] HTTP 429"
    assert ledgers.history[0]["event"] == "enroll_failed"
    assert ledgers.history[0]["detail"] == "[saleshandy-error] HTTP 429"


def test_history_write_failure_does_not_break_the_dispatch():
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"success": true}',
    ):
        outcome = asyncio.run(
            dispatch_approved_enrollment(
                _agent_cfg(), FakeLedgers(raise_on_write=True), draft=DRAFT
            )
        )
    assert outcome.ok, "an audit-write failure must not turn a successful dispatch into a failure"


def test_unrecognized_draft_tool_fails_without_calling_saleshandy():
    bad_draft = {"tool": "not_a_real_tool", "sequence_id": "s", "step_id": "t"}
    with (
        patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add,
        patch("agent.mcp.saleshandy.server._import_prospects_to_sequence_request") as mock_import,
    ):
        ledgers = FakeLedgers()
        outcome = asyncio.run(dispatch_approved_enrollment(_agent_cfg(), ledgers, draft=bad_draft))
    assert outcome.ok is False and outcome.status == "enroll_failed"
    mock_add.assert_not_called()
    mock_import.assert_not_called()
    assert ledgers.history == []


def test_agent_dry_run_never_dispatches(saleshandy):
    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add:
        ledgers = FakeLedgers()
        outcome = asyncio.run(
            dispatch_approved_enrollment(_agent_cfg(), ledgers, draft=DRAFT, dry_run=True)
        )
    assert outcome.status == "dry_run" and outcome.ok is False
    mock_add.assert_not_called()
    assert saleshandy.calls == [], "a dry run makes no Saleshandy call at all, reads included"
    assert ledgers.history == []


def test_dispatch_sets_email_context_only_during_the_saleshandy_call():
    """The email-context flag must be set while the request function runs and cleared
    immediately after — the same window agent/permissions.py's email_context() guards."""
    seen_inside: list[bool] = []

    async def _fake_add(*args, **kwargs):
        seen_inside.append(permissions.in_email_context())
        return '{"success": true}'

    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request", _fake_add):
        assert permissions.in_email_context() is False
        asyncio.run(dispatch_approved_enrollment(_agent_cfg(), FakeLedgers(), draft=DRAFT))
        assert permissions.in_email_context() is False

    assert seen_inside == [True]


# ── metadata-driven trigger (backend) ───────────────────────────────────────────


def test_email_enroll_gated_node_checks_the_effect_value_not_just_truthiness():
    """A node declaring a DIFFERENT effect (e.g. publish) must not be mistaken for an
    email-enroll dispatch node — this is the exact bug the plain publish_gated_node
    truthy check would reintroduce once a second effect kind exists."""
    graph = Graph(
        nodes=(
            Node(id="sequence-enroll", gate=True, external_effect="email_enroll"),
            Node(id="publish", gate=True, external_effect="publish"),
            Node(id="sequence", gate=True),
        )
    )
    assert email_enroll_gated_node(graph, "sequence-enroll") is True
    assert email_enroll_gated_node(graph, "publish") is False
    assert email_enroll_gated_node(graph, "sequence") is False
    assert email_enroll_gated_node(graph, "no-such-node") is False


# ── dispatch ───────────────────────────────────────────────────────────────────


def test_backend_dispatch_never_raises_when_unconfigured(tmp_path):
    """No Saleshandy key on cfg ⇒ dispatch_approved_enrollment returns 'not_configured'
    ⇒ the backend wrapper returns None (fail-closed, no shared-account fallback)."""
    cfg = SimpleNamespace(saleshandy_api_key=None, content_root=tmp_path)
    outcome = asyncio.run(
        dispatch_backend_email_enroll(
            cfg, "example-profile", pool=None, workspace_id=_WS, draft=DRAFT
        )
    )
    assert outcome is None


def test_backend_dispatch_never_raises_on_internal_exception(tmp_path):
    cfg = SimpleNamespace(saleshandy_api_key="sk-test", content_root=tmp_path)
    with patch(
        "agent.email_dispatch.dispatch_approved_enrollment",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        outcome = asyncio.run(
            dispatch_backend_email_enroll(
                cfg, "example-profile", pool=None, workspace_id=_WS, draft=DRAFT
            )
        )
    assert outcome is None


def test_backend_dispatch_forwards_dry_run(tmp_path):
    cfg = SimpleNamespace(saleshandy_api_key="sk-test", content_root=tmp_path)
    with patch("agent.mcp.saleshandy.server._add_leads_to_sequence_request") as mock_add:
        outcome = asyncio.run(
            dispatch_backend_email_enroll(
                cfg, "example-profile", pool=None, workspace_id=_WS, draft=DRAFT, dry_run=True
            )
        )
    assert outcome is not None and outcome.status == "dry_run"
    mock_add.assert_not_called()


def test_backend_dispatch_enrolls_and_returns_the_outcome(tmp_path):
    cfg = SimpleNamespace(saleshandy_api_key="sk-test", content_root=tmp_path)
    with (
        patch(
            "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
            return_value='{"success": true}',
        ),
        patch("agent.ledgers.Ledgers") as mock_ledgers,
    ):
        mock_ledgers.return_value.append_history = lambda entry: None
        outcome = asyncio.run(
            dispatch_backend_email_enroll(
                cfg, "example-profile", pool=None, workspace_id=_WS, draft=DRAFT
            )
        )
    assert outcome is not None and outcome.ok is True and outcome.status == "enrolled"


# ── the enrollment lane gate, at dispatch ─────────────────────────────────────
# gtm_core/enrollment_gate.py holds the rules that stop a hold/excluded/unrouted row being
# enrolled, but its only caller was `account_integrity --require-verdict` — a CLI the brain
# is told to run by skill prose. A gate that holds only when the model chooses to invoke it
# is not a gate (§R13). These pin it on the dispatch path, where it holds regardless.


def _lane_state(tmp_path, profile: str, rows: list[dict]) -> SimpleNamespace:
    """Write evals/lanes-state.jsonl under an isolated content root; return a cfg for it."""
    from gtm_core.prospect_paths import evals_dir

    d = evals_dir(profile, tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "lanes-state.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return SimpleNamespace(saleshandy_api_key="sk-test-key", content_root=tmp_path)


def _no_enroll_calls():
    """Patch both enroll verbs and assert neither fires."""
    return patch(
        "agent.mcp.saleshandy.server._import_prospects_to_sequence_request",
        side_effect=AssertionError("enrollment dispatched despite a lane refusal"),
    )


def test_a_row_parked_on_hold_is_not_enrolled(tmp_path):
    """The regression this gate exists for: a person the operator parked on `hold` must
    not be emailed because a skill skipped the verdict CLI."""
    cfg = _lane_state(
        tmp_path,
        "example",
        [
            {"email": "a@example.com", "lane": "hold"},
            {"email": "b@example.com", "lane": "send"},
        ],
    )
    with _no_enroll_calls():
        outcome = asyncio.run(
            dispatch_approved_enrollment(cfg, FakeLedgers(), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is False
    assert outcome.status == "enroll_failed"
    assert "hold" in (outcome.detail or "").lower()


def test_a_row_absent_from_lane_state_is_not_enrolled(tmp_path):
    """An unrouted row has no verdict at all — enrolling it is the same mistake as
    enrolling a parked one."""
    cfg = _lane_state(tmp_path, "example", [{"email": "a@example.com", "lane": "send"}])
    with _no_enroll_calls():
        outcome = asyncio.run(
            dispatch_approved_enrollment(cfg, FakeLedgers(), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is False and outcome.status == "enroll_failed"


def test_rows_all_routed_to_send_still_enroll(tmp_path):
    """Positive control (§R12). A gate that refuses everything discriminates nothing —
    without this, returning a constant refusal would pass every test above."""
    cfg = _lane_state(
        tmp_path,
        "example",
        [
            {"email": "a@example.com", "lane": "send"},
            {"email": "b@example.com", "lane": "send"},
        ],
    )
    with patch(
        "agent.mcp.saleshandy.server._import_prospects_to_sequence_request",
        return_value='{"requestId": "r-1"}',
    ) as mock_import:
        outcome = asyncio.run(
            dispatch_approved_enrollment(cfg, FakeLedgers(), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is True and outcome.status == "enrolled"
    mock_import.assert_called_once()


def test_a_tenant_with_no_lane_state_is_not_blocked(tmp_path):
    """Scoping decision, pinned so it is a choice rather than an accident: a tenant that
    never ran the lane router has no lane a row could violate, so the lane checks are
    skipped. The copy-match and paused-sequence checks still run. Refusing here would
    block a legitimate enrollment on the absence of a feature."""
    cfg = SimpleNamespace(saleshandy_api_key="sk-test-key", content_root=tmp_path)
    with patch(
        "agent.mcp.saleshandy.server._import_prospects_to_sequence_request",
        return_value='{"requestId": "r-1"}',
    ):
        outcome = asyncio.run(
            dispatch_approved_enrollment(cfg, FakeLedgers(), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is True and outcome.status == "enrolled"


def test_a_ledger_without_a_profile_refuses(tmp_path):
    """Fail-closed: the lane state is resolved per profile, so a dispatcher handed a
    profile-less ledger cannot check anything — and must not enroll anyway."""
    cfg = SimpleNamespace(saleshandy_api_key="sk-test-key", content_root=tmp_path)
    with _no_enroll_calls():
        outcome = asyncio.run(
            dispatch_approved_enrollment(cfg, FakeLedgers(profile=""), draft=IMPORT_PROSPECTS_DRAFT)
        )
    assert outcome.ok is False and "no profile" in (outcome.detail or "")


def test_lead_id_drafts_have_no_rows_to_lane_check(tmp_path):
    """add_leads_to_sequence carries opaque lead ids, not rows — there is nothing
    lane-shaped to verify, and the gate must not invent a refusal."""
    cfg = _lane_state(tmp_path, "example", [{"email": "a@example.com", "lane": "hold"}])
    with patch(
        "agent.mcp.saleshandy.server._add_leads_to_sequence_request",
        return_value='{"requestId": "r-1"}',
    ):
        outcome = asyncio.run(dispatch_approved_enrollment(cfg, FakeLedgers(), draft=DRAFT))
    assert outcome.ok is True and outcome.status == "enrolled"
