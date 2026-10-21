"""R2.5 in the angle resolver: an agentic source list can qualify a row, behind its own switch.

Same fictional tenant as `test_messaging_resolve`. The premise below has no terms, so no record
text can ever satisfy it: either a list names the account (and the routing switch is open), or
the row is refused. Both switches closed is byte-identical to a world with no lists.
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core.messaging import resolve
from gtm_core.signal_obs import observations, switch
from gtm_core.signal_obs.registry import locate
from gtm_core.signal_sources import store_capture
from unit.test_messaging_resolve import (
    _ANGLES,
    _CISO,
    _EVIDENCE_NONE,
    _PREMISE_TOML,
    _PROFILE,
    _resolve,
    _row,
    _tenant,
)

DOMAIN = "copperline.example"
TODAY = datetime.date(2026, 10, 1)

_PREMISES = (
    _PREMISE_TOML
    + """
[premise.agents-in-operation]
claim = "the reader has put AI agents into operation or pilot"
min_distinct = 1
attests_boundary = false
attested_by_source = true
terms = []
"""
)

_ANGLES_LISTED = [
    *_ANGLES,
    {
        "id": "k1-security-agents-in-operation",
        "seat": "security",
        "premise": "agents-in-operation",
        "claim": "audit-signed",
        "proof": "rollout-outcome",
        "opener_kind": "account-event",
        "summary": "Your agent pilot needs an accountable identity.",
        "status": "draft",
    },
]

_SOURCES = """
schema = 1

[[source]]
id = "agent-register"
title = "Agent pilot register"
url = "https://register.example.test/cohort"
kind = "regulator_register"
premise = "agents-in-operation"
attestation = "{attestation}"
{basis}
claim_gap = "Listing shows a pilot, not production."
precision = "8/10"
member_role = "buyer"
timing = "listing_date"
cadence_days = 90
extractor = "links"
extractor_args = {{}}
max_members = 50
expires_on = "2027-03-20"
owner = "ops"
"""


def _world(tmp_path, monkeypatch, name, *, attestation="agentic", member=True):
    reg, root = _tenant(tmp_path, monkeypatch, name, angles=_ANGLES_LISTED, premises=_PREMISES)
    basis = 'agentic_basis = "chosen on agent use cases"' if attestation == "agentic" else ""
    (root / _PROFILE / "knowledge" / "signal-sources.toml").write_text(
        _SOURCES.format(attestation=attestation, basis=basis), encoding="utf-8"
    )
    content = tmp_path / f"content-{name}"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    sha = store_capture(
        "https://register.example.test/cohort",
        "# cohort\n",
        sources_dir=content / _PROFILE / "sources",
        fetched_at="2026-09-30T01:00:00+00:00",
    )
    if member:
        rec = observations.make_observation(
            kind="source_member",
            product=locate(_PROFILE, None, profiles_root=root)[1],
            source_id="agent-register",
            source_url="https://register.example.test/cohort",
            capture_sha256=sha,
            account_key=DOMAIN,
            observed="2026-09-30",
            observed_basis="first_seen_in_capture",
            role="buyer",
            premise_at_write="agents-in-operation",
            writer="amy",
        )
        observations.append(
            content / _PROFILE / "prospects" / "observations", "amy-2026-09.jsonl", [rec]
        )
    return reg, root


def _listed_row():
    return {**_row(_CISO, "Singapore", _EVIDENCE_NONE), "domain": DOMAIN}


def test_a_listed_account_gets_the_list_angle_only_with_both_switches_open(tmp_path, monkeypatch):
    reg, root = _world(tmp_path, monkeypatch, "open")
    got = _resolve(_listed_row(), reg, root)
    assert got.refusal is None and got.angle_id == "k1-security-agents-in-operation"
    assert got.attestation == "source"

    monkeypatch.delenv(switch.VIEW_SETTING)  # collecting only: measured, never applied
    assert _resolve(_listed_row(), reg, root).refusal == resolve.PREMISE_UNSUPPORTED

    monkeypatch.setenv(switch.VIEW_SETTING, "1")
    monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED")  # lists off: no shard is read
    assert _resolve(_listed_row(), reg, root).refusal == resolve.PREMISE_UNSUPPORTED


def test_an_account_the_list_does_not_name_is_refused_as_before(tmp_path, monkeypatch):
    reg, root = _world(tmp_path, monkeypatch, "stranger", member=False)
    assert _resolve(_listed_row(), reg, root).refusal == resolve.PREMISE_UNSUPPORTED


def test_a_pool_list_never_qualifies_an_account_by_membership(tmp_path, monkeypatch):
    reg, root = _world(tmp_path, monkeypatch, "pool", attestation="pool")
    assert _resolve(_listed_row(), reg, root).refusal == resolve.PREMISE_UNSUPPORTED


def test_the_lists_never_change_a_row_the_record_already_qualifies(tmp_path, monkeypatch):
    reg, root = _world(tmp_path, monkeypatch, "record")
    from unit.test_messaging_resolve import _EVIDENCE_MULTI

    row = {**_row(_CISO, "Singapore", _EVIDENCE_MULTI), "domain": DOMAIN}
    got = _resolve(row, reg, root)
    assert got.attestation == "record" and got.angle_id == "a1-security-multi-framework"


@pytest.mark.parametrize("sources", ["", "1"])
def test_both_switches_closed_is_the_same_answer_as_no_lists_at_all(tmp_path, monkeypatch, sources):
    reg, root = _world(tmp_path, monkeypatch, f"closed{sources}")
    monkeypatch.delenv(switch.VIEW_SETTING)
    if not sources:
        monkeypatch.delenv("GTM_SIGNAL_SOURCES_ENABLED")
    assert _resolve(_listed_row(), reg, root).refusal == resolve.PREMISE_UNSUPPORTED
