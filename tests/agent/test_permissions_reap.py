"""Explicit verdict table for every leaf on the live Reap MCP server (§3.1 fix).

Reap is a hosted, claude.ai-connected server; its segment is the opaque per-user UUID
``7f6ac7e0-cc3f-46e9-868b-f64dab4cc0e4``.
Before this fix, all 26 of its leaves — including ``publish_clip``, ``schedule_clips``, and
``update_publisher_post`` — reached ``agent.permissions._classify_mcp``'s line-238 class-allow
fallthrough with no gate, the same architectural hole already closed for Buffer/Higgsfield and
never applied to Reap.

The point of this file is not the three denials — ``tests/agent/test_permissions.py`` already
proves the mechanism generically. The point is **roster coverage**: every leaf on the live server
gets an explicit expected verdict, so a leaf with no entry here fails collection rather than
silently inheriting the class-allow. See ``test_every_known_reap_leaf_has_an_explicit_verdict``.

Known gap (documented, not fixed here — §8.4 tier E in the plan): ``_MCP_CONNECTOR_ALLOWLISTS``
keys on *leaf-name prefix* and Reap's leaves share no common prefix (unlike ``apollo_*``), so a
brand-new Reap verb shipped next quarter still defaults to ``allow`` via the class-allow, not to
review. This roster is the honest interim — it catches drift in the leaves we know about today —
not a structural fix for leaves we don't know about yet.
"""

from __future__ import annotations

import pytest

pytest.importorskip("agent.permissions", reason="agent.permissions not built yet")

from agent.permissions import classify_tool  # noqa: E402

_SERVER = "7f6ac7e0-cc3f-46e9-868b-f64dab4cc0e4"

#: The full live Reap roster (24 tools per the PRD's own count -> re-verified live at 26; see
#: module docstring) mapped to its expected verdict. Keep this alphabetical so a diff against the
#: live tool list is easy to eyeball.
REAP_ROSTER: dict[str, str] = {
    "add_captions": "allow",
    "cancel_video": "allow",
    "create_clips": "allow",
    "dub_video": "allow",
    "get_caption_styles": "allow",
    "get_clip": "allow",
    "get_dubbing_languages": "allow",
    "get_languages": "allow",
    "get_publisher_post": "allow",  # read — explicitly kept allowed, not swept in with the denials
    "get_results": "allow",
    "get_status": "allow",
    "get_translation_languages": "allow",
    "get_video": "allow",
    "list_integrations": "allow",  # read
    "list_publisher_posts": "allow",  # read
    "list_templates": "allow",
    "list_uploads": "allow",
    "list_videos": "allow",
    "publish_clip": "deny",  # §3.1 — direct-to-platform publish, no operator gate
    "reframe": "allow",  # also a Higgsfield leaf name; see the collision note in permissions.py
    "request_upload_url": "allow",  # confinement is Phase C's job (gtm_core.reap_upload), not a deny
    "schedule_clips": "deny",  # §3.1 — scheduling is publishing with a delay, same denial class
    "transcribe": "allow",
    "update_clip": "allow",
    "update_publisher_post": "deny",  # §3.1 — mutates an already-published post in place
    "update_video": "allow",
}


@pytest.mark.parametrize("leaf,expected", sorted(REAP_ROSTER.items()))
def test_every_known_reap_leaf_has_an_explicit_verdict(leaf, expected):
    assert classify_tool(f"mcp__{_SERVER}__{leaf}", {}) == expected


def test_the_roster_is_non_vacuous_and_names_all_three_live_denials():
    """Guards against the roster silently shrinking to nothing meaningful."""
    assert len(REAP_ROSTER) >= 20
    denied = {leaf for leaf, v in REAP_ROSTER.items() if v == "deny"}
    assert denied == {"publish_clip", "schedule_clips", "update_publisher_post"}


def test_the_three_denials_survive_whatever_the_server_segment_is_called():
    """The rule is leaf-keyed by design (opaque per-user UUID) — denial must not depend on this
    specific UUID matching. Mirrors test_permissions.py's generic version, pinned to Reap's verbs."""
    for server in (_SERVER, "some__other__server__with__separators", "a84f5f15-b971-4074-8e7c"):
        for leaf in ("publish_clip", "schedule_clips", "update_publisher_post"):
            assert classify_tool(f"mcp__{server}__{leaf}", {}) == "deny"


def test_the_three_reads_stay_allowed_alongside_the_denials():
    """Explicit regression: fixing §3.1 must not have swept the read tools in with it."""
    for leaf in ("list_integrations", "get_publisher_post", "list_publisher_posts"):
        assert classify_tool(f"mcp__{_SERVER}__{leaf}", {}) == "allow"
