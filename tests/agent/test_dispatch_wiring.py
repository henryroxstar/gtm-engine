"""F4 — the intent ledger wired into the two dispatchers (always-on hardening test plan F4-1..F4-7, F4-11).

Real ``Ledgers`` + real ``FileIntentStore`` on tmp_path. Only the OUTSIDE world is faked: the Saleshandy
request functions and the LinkedIn transport — the claim-before-call, outcome classification and refusal
logic under test are the real code. The one thing a fake provider can never prove is how Saleshandy
actually treats a duplicate (F4-0, unverified: the operator skipped the live probe), which is exactly why
the guard does not depend on the answer.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import email_dispatch
from agent.email_dispatch import dispatch_approved_enrollment
from agent.ledgers import Ledgers
from agent.publish import LinkedInPublisher, PublishResult, PublishSettings
from agent.publish_dispatch import dispatch_approved_publish
from gtm_core.dispatch_intents import PUBLISH_SCOPE, FileIntentStore, recipient_key
from gtm_core.publish_hash import content_hash

REPO = Path(__file__).resolve().parents[2]
SERVER = "agent.mcp.saleshandy.server"

POST = "A fictional post about onboarding a fictional customer."
DRAFT = {
    "tool": "import_prospects_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": [],
    "prospect_list": [{"Email": "a@acme.example"}, {"Email": "B@Acme.Example"}],
}
LEADS_DRAFT = {
    "tool": "add_leads_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": [],
    "lead_ids": [101],
}


def _led(tmp_path: Path) -> Ledgers:
    return Ledgers(SimpleNamespace(content_root=tmp_path), "example")


def _intent_rows(tmp_path: Path) -> list[dict]:
    p = tmp_path / "example" / "dispatch-intents.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _history(tmp_path: Path) -> list[dict]:
    p = tmp_path / "example" / "history.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── publish ──────────────────────────────────────────────────────────────────────────────────


class ScriptedPublisher:
    """Duck-types the publisher: returns a scripted PublishResult and counts calls."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def publish(self, post, media, is_published=None, scheduled_at=None):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _publish(tmp_path, publisher, **kw):
    return asyncio.run(
        dispatch_approved_publish(
            publisher, _led(tmp_path), post=POST, staged_hash=content_hash(POST, ()), **kw
        )
    )


def test_f4_1_a_repeat_publish_is_refused_with_zero_publisher_calls(tmp_path):
    pub = ScriptedPublisher(PublishResult(ok=True, status="published"))
    assert _publish(tmp_path, pub).ok
    again = _publish(tmp_path, pub)
    assert not again.ok and again.status == "already_dispatched"
    assert pub.calls == 1


def _real_publisher(transport):
    s = PublishSettings(
        enabled=True,
        url="https://hooks.example.test/p",
        secret="s",
        max_per_hour=50,
        max_chars=3000,
        timeout_s=5.0,
        schedule_enabled=False,
        max_horizon_days=30,
    )
    return LinkedInPublisher(s, transport=transport)


@pytest.mark.parametrize(
    "transport_result, expected_state",
    [
        (TimeoutError("read timed out"), "unknown"),
        ((500, {}), "unknown"),
        ((502, {}), "unknown"),
        ((408, {}), "unknown"),
        ((400, {}), "not_sent"),
        ((401, {}), "not_sent"),
        ((429, {}), "not_sent"),
        ((200, {"id": "p1"}), "ok"),
    ],
)
def test_f4_4_publisher_classifies_what_it_can_prove(transport_result, expected_state):
    calls = []

    async def transport(url, headers, json, timeout):  # noqa: A002
        calls.append(1)
        if isinstance(transport_result, Exception):
            raise transport_result
        return transport_result

    pub = _real_publisher(transport)
    result = asyncio.run(pub.publish(POST, ()))
    assert result.effective_sent_state() == expected_state


def test_f4_5_a_publish_timeout_does_not_re_post(tmp_path):
    n = []

    async def transport(url, headers, json, timeout):  # noqa: A002
        n.append(1)
        raise TimeoutError("read timed out after the request was written")

    pub = _real_publisher(transport)
    first = _publish(tmp_path, pub)
    assert not first.ok and first.sent_state == "unknown" and first.batch_id
    assert "can't tell whether" in first.operator_line()
    second = _publish(tmp_path, pub)
    assert second.status == "outcome_unknown" and second.open_batches == (first.batch_id,)
    assert len(n) == 1  # ZERO further transport calls
    assert _history(tmp_path)[-1]["intent_outcome"] == "unknown"


def test_f4_5_a_pre_send_failure_and_an_explicit_4xx_release_the_slot(tmp_path):
    pub = ScriptedPublisher(
        PublishResult(ok=False, status="invalid", error="too long"),
        PublishResult(ok=False, status="error", error="HTTP 400", sent_state="not_sent"),
        PublishResult(ok=True, status="published"),
    )
    assert not _publish(tmp_path, pub).ok
    assert not _publish(tmp_path, pub).ok
    assert _publish(tmp_path, pub).ok  # neither earlier failure blocked this
    assert pub.calls == 3


def test_an_unexpected_exception_from_the_publisher_is_unknown_not_a_crash(tmp_path):
    pub = ScriptedPublisher(RuntimeError("boom"))
    out = _publish(tmp_path, pub)
    assert not out.ok and out.sent_state == "unknown"
    assert _publish(tmp_path, ScriptedPublisher()).status == "outcome_unknown"


def test_f4_6_an_unreadable_publish_ledger_refuses_and_says_what_to_check(tmp_path):
    led = _led(tmp_path)

    class Broken(Ledgers):
        def published_content_hashes(self):
            raise OSError("history.jsonl unreadable")

    pub = ScriptedPublisher(PublishResult(ok=True, status="published"))
    out = asyncio.run(
        dispatch_approved_publish(
            pub, Broken(led._cfg, "example"), post=POST, staged_hash=content_hash(POST, ())
        )
    )
    assert not out.ok and out.status == "ledger_unreadable" and pub.calls == 0
    assert "history.jsonl" in out.operator_line()


def test_a_corrupt_intent_ledger_refuses_the_publish(tmp_path):
    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "dispatch-intents.jsonl").write_text("{garbage\n")
    pub = ScriptedPublisher(PublishResult(ok=True, status="published"))
    out = _publish(tmp_path, pub)
    assert out.status == "ledger_unreadable" and pub.calls == 0


def test_a_dry_run_writes_no_intent(tmp_path):
    pub = ScriptedPublisher()
    out = _publish(tmp_path, pub, dry_run=True)
    assert out.status == "dry_run" and pub.calls == 0
    assert not (tmp_path / "example" / "dispatch-intents.jsonl").exists()


def test_integrity_failures_write_no_intent(tmp_path):
    pub = ScriptedPublisher()
    out = asyncio.run(
        dispatch_approved_publish(pub, _led(tmp_path), post=POST, staged_hash="wrong")
    )
    assert out.status == "hash_mismatch" and not _intent_rows(tmp_path)


def test_publish_history_event_names_are_unchanged_and_agree_with_the_intent(tmp_path):
    ok = ScriptedPublisher(PublishResult(ok=True, status="published", post_id="p1"))
    _publish(tmp_path, ok)
    h = _history(tmp_path)[-1]
    assert h["event"] == "published" and h["intent_outcome"] == "dispatched"
    out = [r for r in _intent_rows(tmp_path) if r["kind"] == "outcome"]
    assert out[-1]["state"] == "dispatched"


def test_a_voided_schedule_releases_its_intent_so_the_same_bytes_can_be_re_approved(tmp_path):
    pub = ScriptedPublisher(
        PublishResult(ok=True, status="scheduled", scheduled_at="2030-01-01T00:00:00Z")
    )
    assert _publish(tmp_path, pub).ok
    assert _publish(tmp_path, pub).status == "already_dispatched"
    import os

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.ledger_cli",
            "record-schedule-void",
            "--profile",
            "example",
            "--post",
            POST,
        ],
        cwd=REPO,
        env={**os.environ, "GTM_CONTENT_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    pub2 = ScriptedPublisher(
        PublishResult(ok=True, status="scheduled", scheduled_at="2030-01-02T00:00:00Z")
    )
    assert _publish(tmp_path, pub2).ok and pub2.calls == 1


def test_a_timed_out_publish_stays_blocked_in_process_too_not_rolled_back():
    async def transport(url, headers, json, timeout):  # noqa: A002
        raise TimeoutError("x")

    pub = _real_publisher(transport)
    asyncio.run(pub.publish(POST, ()))
    assert content_hash(POST, ()) in pub._published  # an unknown send must not free its slot


# ── enrollment ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _prechecks_pass(monkeypatch):
    async def ok(*_a, **_k):
        return None

    monkeypatch.setattr(email_dispatch, "_lane_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(email_dispatch, "_load_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(email_dispatch, "_live_copy_refusal", ok)

    async def rows(_k, prospects):
        return [{"fields": [{"id": "f", "value": r["Email"]}]} for r in prospects], None

    monkeypatch.setattr(email_dispatch, "_import_rows", rows)


def _enroll(tmp_path, draft=DRAFT, **kw):
    return asyncio.run(
        dispatch_approved_enrollment(
            SimpleNamespace(saleshandy_api_key="k", content_root=tmp_path),
            _led(tmp_path),
            draft=draft,
            **kw,
        )
    )


def test_f4_1_approving_the_same_enrollment_twice_makes_zero_provider_calls_the_second_time(
    tmp_path,
):
    with patch(
        f"{SERVER}._import_prospects_to_sequence_request", return_value='{"ok": true}'
    ) as imp:
        first = _enroll(tmp_path)
        second = _enroll(tmp_path)
    assert first.ok and first.status == "enrolled"
    assert (
        not second.ok and second.code == "already_dispatched" and second.status == "enroll_failed"
    )
    assert imp.call_count == 1


def test_f4_2_an_overlapping_batch_is_refused_whole_naming_the_recipient(tmp_path):
    overlapping = {
        **DRAFT,
        "prospect_list": [{"Email": "  A@ACME.example "}, {"Email": "new@acme.example"}],
    }
    with patch(
        f"{SERVER}._import_prospects_to_sequence_request", return_value='{"ok": true}'
    ) as imp:
        _enroll(tmp_path)
        out = _enroll(tmp_path, overlapping)
        disjoint = _enroll(tmp_path, {**DRAFT, "prospect_list": [{"Email": "z@acme.example"}]})
    assert out.code == "already_dispatched" and "a@acme.example" in out.detail
    assert disjoint.ok
    assert imp.call_count == 2


@pytest.mark.parametrize(
    "raw, state, blocks",
    [
        ("[saleshandy-error] HTTP 400", "failed", False),
        ("[saleshandy-error] HTTP 403", "failed", False),
        ("[saleshandy-error] HTTP 429", "failed", False),
        ("[saleshandy-error] HTTP 408", "unknown", True),
        ("[saleshandy-error] HTTP 500", "unknown", True),
        ("[saleshandy-error] HTTP 503", "unknown", True),
        ("[saleshandy-error] request failed: ReadTimeout", "unknown", True),
        ("[saleshandy-error] request failed: ConnectError", "unknown", True),
        ("[saleshandy-error] non-JSON response", "unknown", True),
        ('{"requestId": "r1"}', "dispatched", True),
    ],
)
def test_f4_4_enrollment_outcome_classification(tmp_path, raw, state, blocks):
    with patch(f"{SERVER}._import_prospects_to_sequence_request", return_value=raw) as imp:
        first = _enroll(tmp_path)
        second = _enroll(tmp_path)
    assert [r["state"] for r in _intent_rows(tmp_path) if r["kind"] == "outcome"][0] == state
    assert (imp.call_count == 1) is blocks  # blocked => the provider was not called again
    h = [x for x in _history(tmp_path) if x["skill"] == "email-sequence"][0]
    assert h["event"] == (
        "enrolled" if state == "dispatched" else "enroll_failed"
    )  # names unchanged
    assert h["intent_outcome"] == state
    assert first.code is None or first.code == "outcome_unknown"
    if state == "unknown":
        assert second.code == "outcome_unknown" and "batch of 2 emails" in second.detail


def test_an_exception_in_the_provider_call_is_unknown_not_a_crash(tmp_path):
    with patch(
        f"{SERVER}._import_prospects_to_sequence_request", side_effect=RuntimeError("socket")
    ):
        out = _enroll(tmp_path)
    assert not out.ok and out.sent_state == "unknown"
    with patch(f"{SERVER}._import_prospects_to_sequence_request") as imp:
        assert _enroll(tmp_path).code == "outcome_unknown"
    imp.assert_not_called()


def test_a_corrupt_intent_ledger_refuses_the_enrollment(tmp_path):
    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "dispatch-intents.jsonl").write_text("not json\n")
    with patch(f"{SERVER}._import_prospects_to_sequence_request") as imp:
        out = _enroll(tmp_path)
    assert not out.ok and out.code == "intent_ledger_unreadable"
    imp.assert_not_called()


def test_a_dry_run_enrollment_writes_no_intent(tmp_path):
    with patch(f"{SERVER}._import_prospects_to_sequence_request") as imp:
        out = _enroll(tmp_path, dry_run=True)
    assert out.status == "dry_run"
    imp.assert_not_called()
    assert not (tmp_path / "example" / "dispatch-intents.jsonl").exists()


def test_a_refusal_before_the_call_writes_no_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(email_dispatch, "_expiry_refusal", lambda _d: "draft expired on 2020-01-01")
    with patch(f"{SERVER}._import_prospects_to_sequence_request") as imp:
        out = _enroll(tmp_path)
    assert not out.ok
    imp.assert_not_called()
    assert not (tmp_path / "example" / "dispatch-intents.jsonl").exists()


def test_f4_11_the_same_person_through_both_draft_shapes_is_NOT_deduplicated_a_stated_residual(
    tmp_path,
):
    """add_leads (lead ids) vs import_prospects (email) do not share a key. Documented and tested so a
    future change to merge them is deliberate, not accidental."""
    with (
        patch(f"{SERVER}._add_leads_to_sequence_request", return_value='{"ok": true}') as add,
        patch(
            f"{SERVER}._import_prospects_to_sequence_request", return_value='{"ok": true}'
        ) as imp,
    ):
        a = _enroll(tmp_path, LEADS_DRAFT)
        b = _enroll(tmp_path, {**DRAFT, "prospect_list": [{"Email": "a@acme.example"}]})
    assert a.ok and b.ok and add.call_count == 1 and imp.call_count == 1
    assert recipient_key(lead_id=101) != recipient_key("a@acme.example")


def test_the_resolve_path_unblocks_an_unknown_enrollment(tmp_path):
    with patch(
        f"{SERVER}._import_prospects_to_sequence_request",
        return_value="[saleshandy-error] HTTP 502",
    ):
        first = _enroll(tmp_path)
    store = FileIntentStore(_led(tmp_path))
    open_ = asyncio.run(store.unresolved())
    assert [b.scope for b in open_] == ["seq-1"] and first.batch_id == open_[0].batch_id
    asyncio.run(store.resolve(first.batch_id, "not_sent"))
    with patch(
        f"{SERVER}._import_prospects_to_sequence_request", return_value='{"ok": true}'
    ) as imp:
        assert _enroll(tmp_path).ok
    assert imp.call_count == 1


def test_publish_scope_constant_is_what_the_dispatcher_uses(tmp_path):
    pub = ScriptedPublisher(PublishResult(ok=True, status="published"))
    _publish(tmp_path, pub)
    claim = [r for r in _intent_rows(tmp_path) if r["kind"] == "claim"][0]
    assert claim["scope"] == PUBLISH_SCOPE and claim["recipients"] == [content_hash(POST, ())]
