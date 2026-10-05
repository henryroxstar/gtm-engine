"""R2.1: Closed sets for signal_obs and signal_view."""

from __future__ import annotations

from gtm_core import signal_view
from gtm_core.signal_obs import observations as obs


def test_observation_kinds_are_a_closed_set():
    assert obs.KINDS == frozenset(
        {
            "source_member",
            "source_join",
            "source_leave_noted",
            "job_post",
            "job_change",
            "community_mention",
            "content_engagement",
            "repo_engagement",
            "retracted",
        }
    )


def test_observation_roles_are_a_closed_set():
    assert obs.ROLES == frozenset({"buyer", "vendor", "mixed"})


def test_membership_kinds_are_a_closed_subset_of_kinds():
    assert obs.MEMBERSHIP_KINDS == frozenset({"source_member", "source_join", "source_leave_noted"})
    assert obs.MEMBERSHIP_KINDS <= obs.KINDS


def test_identity_kinds_are_a_closed_subset_of_membership():
    assert obs._IDENTITY_KINDS == frozenset({"source_member", "source_join"})
    assert obs._IDENTITY_KINDS <= obs.MEMBERSHIP_KINDS


def test_timing_kinds_are_a_closed_set():
    assert signal_view.TIMING_KINDS == frozenset(
        {
            "source_join",
            "job_post",
            "job_change",
            "community_mention",
            "content_engagement",
            "repo_engagement",
            "news_event",
            "topic_intent",
            "none",
        }
    )


def test_premise_vias_are_a_closed_set():
    assert signal_view.PREMISE_VIAS == frozenset({"evidence", "source", "industry", "seat", "none"})


def test_view_bases_are_a_closed_set():
    assert signal_view.VIEW_BASES == frozenset({"observations", "legacy", "mixed", "refused"})


def test_view_basis_closed_set():
    allowed_bases = {"observations", "legacy", "mixed", "refused"}
    row = {"email": "a@x.example", "why_now": "something happened"}
    v = signal_view.derive(row, None)
    assert v.view_basis in allowed_bases
    assert v.timing_kind in signal_view.TIMING_KINDS
    assert v.premise_via in signal_view.PREMISE_VIAS
