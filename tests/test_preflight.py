"""Tests for the connectivity preflight gate.

The regression these lock in is concrete: the 2026-08-11 bulk run spent ~$77 and
delivered zero contacts because RocketReach was absent and nobody checked first.
"""

from __future__ import annotations

import pytest

from gtm_core.preflight import adjudicate

ALL_OK = {"vibe": "ok", "rocketreach": "ok", "apollo": "ok", "saleshandy": "ok"}
YESTERDAY = {
    "vibe": "ok",
    "rocketreach": "absent",
    "apollo": "api_inaccessible",
    "saleshandy": "ok",
}


class TestTheRegressionThisPrevents:
    def test_absent_rocketreach_flags_contacts_as_degraded(self):
        """The exact 2026-08-11 state must not report a clean bill of health."""
        v = adjudicate(YESTERDAY, ["discovery", "intent", "contacts"])
        assert v["capabilities"]["contacts"]["degraded"] is True
        assert v["capabilities"]["contacts"]["via"] == "vibe"
        assert "contacts" in v["degraded"]
        assert "rocketreach" in v["capabilities"]["contacts"]["note"]

    def test_all_connected_is_clean(self):
        v = adjudicate(ALL_OK, ["discovery", "intent", "contacts", "double_intent"])
        assert v["proceed"] is True
        assert not v["degraded"] and not v["blocking"]
        assert v["capabilities"]["contacts"]["via"] == "rocketreach"


class TestBlocking:
    def test_no_paid_source_blocks_contacts(self):
        """Down to the free web floor for a requested capability = hard stop."""
        v = adjudicate(
            {"vibe": "absent", "rocketreach": "absent", "apollo": "absent"}, ["contacts"]
        )
        assert v["proceed"] is False
        assert "contacts" in v["blocking"]

    def test_unrequested_capability_never_blocks(self):
        v = adjudicate({"vibe": "absent", "rocketreach": "absent"}, ["sequencing"])
        assert "contacts" not in v["blocking"]

    def test_web_floor_is_always_available_but_never_clean(self):
        v = adjudicate({"vibe": "absent"}, ["discovery"])
        assert v["capabilities"]["discovery"]["available"] is True
        assert v["capabilities"]["discovery"]["via"] == "web"
        assert v["proceed"] is False  # web-only discovery cannot serve a real run


class TestDoubleIntent:
    @pytest.mark.parametrize(
        "observed",
        [{"vibe": "ok", "rocketreach": "absent"}, {"vibe": "absent", "rocketreach": "ok"}],
    )
    def test_needs_both_feeds(self, observed):
        v = adjudicate(observed, [])
        assert v["capabilities"]["double_intent"]["available"] is False

    def test_both_feeds_present(self):
        v = adjudicate({"vibe": "ok", "rocketreach": "ok"}, [])
        assert v["capabilities"]["double_intent"]["available"] is True


class TestApolloPaywall:
    def test_api_inaccessible_is_not_ok(self):
        """A paywalled-but-authorized Apollo must count as unavailable, not healthy."""
        v = adjudicate(
            {"apollo": "api_inaccessible", "rocketreach": "absent", "vibe": "absent"}, ["contacts"]
        )
        assert v["capabilities"]["contacts"]["via"] == "web"
        assert v["proceed"] is False
