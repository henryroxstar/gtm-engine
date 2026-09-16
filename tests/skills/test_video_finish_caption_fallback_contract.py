"""The Reap -> local caption fall back is a route, not a judgement call.

The seam is deliberately split and both halves are checked here. `gtm_core` owns the VOCABULARY
and the FORMAT of a suppression (a library cannot attempt an MCP tool call, so it cannot own the
attempt); the `video-finish` body owns the attempt/fallback flow. Prose is what failed on
2026-08-30 — the paragraph telling a human to switch routes was already written, and the v6 pair
shipped uncaptioned anyway — so the prose half is asserted too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import video_finish as vf

BODY_FILE = (
    Path(__file__).resolve().parents[2] / "plugin" / "skills" / "video-finish" / "body_template.md"
)
BODY = BODY_FILE.read_text(encoding="utf-8") if BODY_FILE.is_file() else ""
_body_stubbed = pytest.mark.skipif(
    not BODY_FILE.is_file(), reason="video-finish body_template.md not present (paid-tier stub)"
)


# ── the vocabulary and format, which code owns ───────────────────────────────────────────


def test_a_suppression_names_a_known_failure_class():
    out = vf.vendor_fallback_suppression(
        "schema_marshalling",
        detail="Invalid literal value, expected true",
        observed="2026-08-30",
    )
    assert out.startswith("vendor-fallback:schema_marshalling")
    assert "2026-08-30" in out
    assert "Invalid literal value, expected true" in out


def test_an_invented_failure_class_is_refused():
    """A new class is a change to the frozenset with its own comment, not a string someone typed."""
    with pytest.raises(vf.PlanError, match="unknown caption fallback reason"):
        vf.vendor_fallback_suppression("reap_was_sad", detail="x", observed="2026-08-30")


def test_a_suppression_with_no_evidence_is_refused():
    """plan()'s gate accepts ANY non-empty string, so "local burn" used to pass it. This is the
    half that makes the recorded reason mean something."""
    with pytest.raises(vf.PlanError, match="verbatim"):
        vf.vendor_fallback_suppression("vendor_error", detail="   ", observed="2026-08-30")


def test_an_undated_suppression_is_refused():
    """A route that failed once is not a route that is broken. Undated, it becomes permanent."""
    with pytest.raises(vf.PlanError, match="ISO-8601"):
        vf.vendor_fallback_suppression("vendor_error", detail="500 from the vendor", observed="")


def test_the_generated_string_satisfies_the_gate_it_exists_for():
    """The round trip is the point: a suppression this helper builds must let plan() proceed on
    the local route with a preset resolved — the exact combination the gate refuses without one."""
    suppression = vf.vendor_fallback_suppression(
        "tool_unavailable", detail="add_captions not in the tool surface", observed="2026-08-30"
    )
    finish_plan = vf.plan(
        profile="tenant",
        slug="film",
        ratio="16:9",
        source="film.mp4",
        spec={
            "caption_text": "one two three",
            "total_s": 6.0,
            "captions_preset": "system_indigo",
            "caption_route": "local",
            "caption_route_suppression": suppression,
        },
    )
    assert finish_plan.caption_route == "local"
    assert finish_plan.caption_route_suppression == suppression


def test_the_same_plan_without_a_suppression_is_still_refused():
    """Guards the guard: if this ever stops raising, the test above proves nothing."""
    with pytest.raises(vf.PlanError):
        vf.plan(
            profile="tenant",
            slug="film",
            ratio="16:9",
            source="film.mp4",
            spec={
                "caption_text": "one two three",
                "total_s": 6.0,
                "captions_preset": "system_indigo",
                "caption_route": "local",
            },
        )


# ── the attempt/fallback flow, which the prompt owns ─────────────────────────────────────
@_body_stubbed
def test_the_body_names_the_local_burn_verb():
    assert "burn-captions" in BODY


@_body_stubbed
def test_the_body_names_the_suppression_helper_rather_than_free_text():
    assert "vendor_fallback_suppression" in BODY
    for code in ("tool_unavailable", "schema_marshalling", "quota_exhausted", "vendor_error"):
        assert code in BODY, f"the body does not offer the {code!r} class"


@_body_stubbed
def test_the_body_makes_the_fallback_automatic_not_a_question():
    """The failure mode being fixed is a human having to notice. If this instruction softens back
    into "ask the operator", an uncaptioned asset ships again while somebody is asleep."""
    assert "do not stop and do not ask" in BODY


@_body_stubbed
def test_the_body_records_the_known_vendor_failure_so_it_is_not_re_diagnosed():
    assert "Invalid literal value, expected true" in BODY
    assert "schema" in BODY


@_body_stubbed
def test_the_body_says_why_the_burn_is_per_shot():
    """Without the reason, the next reader burns onto the master because it is one command."""
    assert "0.637" in BODY
    assert (
        "never onto the assembled master" in BODY.lower() or "never onto the master" in BODY.lower()
    )
