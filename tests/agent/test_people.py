"""Unit tests for `gtm_core.people.People` — the per-profile people engagement ledger.

Pure stdlib (json / pathlib / datetime / fcntl), SDK-free: these build a `PathConfig`
pointed at pytest's `tmp_path` so they never touch the real, gitignored `content/` volume.

Locked interface under test (class People(cfg, profile)):
  upsert(record) -> person
  log_reply(pid, event) -> person
  set_status(pid, status, account=None) -> person
  query(*, tag, status, engaged_min, account) -> list[person]

State: content/<profile>/prospects/people.json
"""

from __future__ import annotations

import json

import pytest

from gtm_core.paths import PathConfig
from gtm_core.people import People, derive_id

PROFILE = "example"


@pytest.fixture()
def people(tmp_path):
    cfg = PathConfig(content_root=tmp_path, profiles_root=tmp_path, default_profile=PROFILE)
    return People(cfg, PROFILE)


def _jane():
    return {
        "name": "Jane Doe",
        "headline": "Head of AI Platform at Acme",
        "company": "Acme",
        "profile_url": "https://www.linkedin.com/in/Jane-Doe/",
        "tags": ["linkedin", "agentic-ai"],
        "engagement": {
            "type": "comment",
            "post_url": "https://lnkd.in/p1",
            "post_slug": "agents-trust",
            "date": "2026-06-18T09:00:00Z",
            "comment_text": "spot on",
        },
    }


# ── identity ────────────────────────────────────────────────────────────────


def test_id_from_profile_url_is_normalized():
    assert (
        derive_id({"profile_url": "https://www.linkedin.com/in/Jane-Doe/"})
        == "linkedin.com/in/jane-doe"
    )


def test_id_falls_back_to_name_company_slug():
    assert derive_id({"name": "Jane Doe", "company": "Acme Corp"}) == "jane-doe-acme-corp"


def test_id_requires_url_or_name():
    with pytest.raises(ValueError):
        derive_id({"company": "Acme"})


# ── upsert ──────────────────────────────────────────────────────────────────


def test_upsert_creates_person_and_file(people):
    person = people.upsert(_jane())
    assert person["id"] == "linkedin.com/in/jane-doe"
    assert person["status"] == "lead"
    assert person["engagement_count"] == 1
    assert person["tags"] == ["linkedin", "agentic-ai"]
    assert people.path == people._dir / "people.json"
    assert people.path.is_file()
    # valid JSON, no leftover temp file
    json.loads(people.path.read_text())
    assert not list(people._dir.glob(".people.*.tmp"))


def test_upsert_merges_tags_and_appends_engagement(people):
    people.upsert(_jane())
    second = dict(_jane())
    second["tags"] = ["linkedin", "champion"]
    second["engagement"] = {
        "type": "like",
        "post_url": "https://lnkd.in/p2",
        "date": "2026-06-20T09:00:00Z",
    }
    person = people.upsert(second)
    assert person["engagement_count"] == 2
    assert person["tags"] == ["linkedin", "agentic-ai", "champion"]  # union, order preserved
    assert person["first_seen"] == "2026-06-18T09:00:00Z"
    assert person["last_seen"] == "2026-06-20T09:00:00Z"
    assert len(people.read()["people"]) == 1  # still one person


def test_upsert_dedups_identical_engagement(people):
    people.upsert(_jane())
    person = people.upsert(_jane())  # same (type, post_url, date)
    assert person["engagement_count"] == 1


def test_upsert_does_not_clobber_existing_identity(people):
    people.upsert(_jane())
    person = people.upsert(
        {"profile_url": "https://www.linkedin.com/in/Jane-Doe/", "company": "NewCo"}
    )
    assert person["company"] == "Acme"  # existing value preserved


# ── log_reply ─────────────────────────────────────────────────────────────────


def test_log_reply_appends_and_bumps_status(people):
    people.upsert(_jane())
    person = people.log_reply(
        "linkedin.com/in/jane-doe",
        {"post_url": "https://lnkd.in/p1", "date": "2026-06-21T09:00:00Z", "note": "sent it"},
    )
    assert person["status"] == "replied"
    assert person["engagement_count"] == 2
    assert person["engagements"][-1]["type"] == "reply_sent"


def test_log_reply_never_downgrades_status(people):
    people.upsert(_jane())
    people.set_status("linkedin.com/in/jane-doe", "account", account="acme-corp")
    person = people.log_reply("linkedin.com/in/jane-doe", {"date": "2026-06-22T09:00:00Z"})
    assert person["status"] == "account"  # not downgraded to replied


def test_log_reply_unknown_id_raises(people):
    people.upsert(_jane())
    with pytest.raises(KeyError):
        people.log_reply("nobody", {})


# ── set_status ────────────────────────────────────────────────────────────────


def test_set_status_tracks_conversion(people):
    people.upsert(_jane())
    person = people.set_status("linkedin.com/in/jane-doe", "opportunity", account="acme-corp")
    assert person["status"] == "opportunity"
    assert person["linked_account"] == "acme-corp"


def test_set_status_rejects_invalid(people):
    people.upsert(_jane())
    with pytest.raises(ValueError):
        people.set_status("linkedin.com/in/jane-doe", "winning")


# ── query ─────────────────────────────────────────────────────────────────────


def test_query_filters(people):
    people.upsert(_jane())
    people.upsert(
        {
            "name": "Bob Roe",
            "company": "Beta",
            "tags": ["linkedin"],
            "engagement": {
                "type": "like",
                "post_url": "https://lnkd.in/p9",
                "date": "2026-06-18T10:00:00Z",
            },
        }
    )
    assert len(people.query(tag="linkedin")) == 2
    assert len(people.query(tag="agentic-ai")) == 1
    assert len(people.query(engaged_min=1)) == 2
    people.set_status("linkedin.com/in/jane-doe", "account", account="acme-corp")
    assert [p["id"] for p in people.query(status="account")] == ["linkedin.com/in/jane-doe"]
    assert [p["id"] for p in people.query(account="acme-corp")] == ["linkedin.com/in/jane-doe"]
