"""The closed sets the source-list design names, pinned as written.

Each set is spelled once in code. The spec names them, and a widened set is a design change (a new
observation kind or extractor needs its own reader, test and review), so a drift fails here rather
than passing unnoticed.
"""

from __future__ import annotations

from gtm_core.signal_obs import observations, registry


def test_observation_kinds_are_the_specified_nine():
    assert observations.KINDS == {
        "source_member", "source_join", "source_leave_noted", "job_post", "job_change",
        "community_mention", "content_engagement", "repo_engagement", "retracted",
    }  # fmt: skip


def test_membership_kinds_are_a_subset_and_events_key_on_the_day():
    assert observations.MEMBERSHIP_KINDS <= observations.KINDS
    assert observations.MEMBERSHIP_KINDS == {"source_member", "source_join", "source_leave_noted"}


def test_registry_kinds_roles_timings_and_extractors_are_the_specified_sets():
    assert registry.KINDS == {
        "member_directory", "regulator_register", "job_board_query", "partner_directory",
        "agent_registry",
    }  # fmt: skip
    assert registry.MEMBER_ROLES == {"buyer", "vendor", "mixed"}
    assert registry.TIMINGS == {"listing_date", "none"}
    assert set(registry.EXTRACTORS) == {"links", "table_column", "heading_list", "brain_list"}


def test_an_observation_role_is_one_of_the_registry_member_roles():
    assert observations.ROLES == registry.MEMBER_ROLES
