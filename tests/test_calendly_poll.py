"""Tests for gtm_core.calendly_poll — optional, off-by-default meeting read-back."""

from __future__ import annotations

import asyncio

from gtm_core import calendly_poll
from gtm_core.calendly_poll import CalendlySettings
from gtm_core.outcomes import read_outcomes

PROFILE = "example"


def _event(uri, *, status="active", name="Intro call", utm="acct-42"):
    return {
        "uri": uri,
        "status": status,
        "name": name,
        "start_time": "2026-07-21T15:00:00Z",
        "tracking": {"utm_campaign": utm, "utm_source": "outreach"},
    }


def test_disabled_is_a_noop(tmp_path):
    # No PAT → disabled → returns 0, writes nothing (safe to call from cron unconditionally).
    settings = CalendlySettings(pat=None)
    n = asyncio.run(
        calendly_poll.poll(tmp_path, PROFILE, since="2026-07-01T00:00:00Z", settings=settings)
    )
    assert n == 0
    assert read_outcomes(tmp_path, PROFILE) == []


def test_poll_appends_meeting_outcomes_and_dedupes(tmp_path):
    calls: list[dict] = []

    async def fake_transport(url, *, headers, params, timeout):
        calls.append({"url": url, "auth": headers.get("Authorization"), "params": params})
        return 200, {"collection": [_event("cal_evt_1"), _event("cal_evt_2")]}

    settings = CalendlySettings(pat="pat_secret", user_uri="https://api.calendly.com/users/me")

    n = asyncio.run(
        calendly_poll.poll(
            tmp_path,
            PROFILE,
            since="2026-07-01T00:00:00Z",
            settings=settings,
            transport=fake_transport,
        )
    )
    assert n == 2
    rows = read_outcomes(tmp_path, PROFILE)
    assert {r["ref"] for r in rows} == {"cal_evt_1", "cal_evt_2"}
    assert all(r["channel"] == "calendly" and r["outcome"] == "meeting" for r in rows)
    assert rows[0]["meta"]["utm_campaign"] == "acct-42"  # attribution carried through
    # Secret only ever in the header.
    assert calls[0]["auth"] == "Bearer pat_secret"

    # Re-poll the same window → already-recorded uris are skipped.
    n2 = asyncio.run(
        calendly_poll.poll(
            tmp_path,
            PROFILE,
            since="2026-07-01T00:00:00Z",
            settings=settings,
            transport=fake_transport,
        )
    )
    assert n2 == 0
    assert len(read_outcomes(tmp_path, PROFILE)) == 2


def test_canceled_event_maps_to_cancel_outcome(tmp_path):
    async def fake_transport(url, *, headers, params, timeout):
        return 200, {"collection": [_event("cal_evt_x", status="canceled")]}

    settings = CalendlySettings(pat="p")
    asyncio.run(
        calendly_poll.poll(
            tmp_path,
            PROFILE,
            since="2026-07-01T00:00:00Z",
            settings=settings,
            transport=fake_transport,
        )
    )
    rows = read_outcomes(tmp_path, PROFILE)
    assert rows and rows[0]["outcome"] == "meeting_canceled"


def test_api_error_is_swallowed(tmp_path):
    async def boom(url, *, headers, params, timeout):
        raise TimeoutError("slow")

    settings = CalendlySettings(pat="p")
    n = asyncio.run(
        calendly_poll.poll(
            tmp_path,
            PROFILE,
            since="2026-07-01T00:00:00Z",
            settings=settings,
            transport=boom,
        )
    )
    assert n == 0
    assert read_outcomes(tmp_path, PROFILE) == []
