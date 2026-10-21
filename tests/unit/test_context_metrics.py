"""W4 measurement (PRD 2026-10-19a): what a run's context is made of, and how it grows.

These are pure-function tests over fictional CLI ``get_context_usage`` payloads. The payload
*shape* is read from the SDK's ``ContextUsageResponse`` type; the ``skills`` sub-object is only
partly typed there, so the parser is defensive and these tests pin that an unknown or missing
shape yields ``None`` (unmeasured), never ``0`` (which would claim "measured, and free").
"""

from __future__ import annotations

from gtm_core.context_metrics import TurnTally, summarize_context_usage

_RESP = {
    "totalTokens": 61_000,
    "categories": [
        {"name": "System prompt", "tokens": 4_000, "color": "x"},
        {"name": "Skills", "tokens": 9_999, "color": "x"},
    ],
    "memoryFiles": [
        {"path": "/app/CLAUDE.md", "type": "Project", "tokens": 10_000},
        {"path": "/app/other.md", "type": "Project", "tokens": 500},
    ],
    "mcpTools": [
        {"name": "mcp__worker__draft", "serverName": "worker", "tokens": 700, "isLoaded": True},
        {"name": "mcp__worker__batch", "serverName": "worker", "tokens": 300, "isLoaded": True},
        {"name": "mcp__vision__ocr", "serverName": "vision", "tokens": 400, "isLoaded": True},
        {"name": "mcp__news__query", "serverName": "news", "tokens": 5_000, "isLoaded": False},
    ],
    "skills": {
        "totalSkills": 80,
        "includedSkills": 61,
        "skillFrontmatter": [
            {"name": "alpha", "tokens": 120},
            {"name": "beta", "tokens": 80},
        ],
    },
}


def test_summarize_splits_skills_memory_and_each_mcp_server():
    s = summarize_context_usage(_RESP)
    assert s["available"] is True
    assert s["skills_tokens"] == 200  # sum of the frontmatter entries, not the category figure
    assert s["skills_included"] == 61 and s["skills_total"] == 80
    assert s["memory_files_tokens"] == 10_500
    assert s["mcp_tokens_by_server"] == {"worker": 1_000, "vision": 400}
    assert s["mcp_tokens_total"] == 1_400
    assert s["mcp_deferred_tokens_total"] == 5_000  # not in-context, so kept out of the loaded sum
    assert s["total_tokens"] == 61_000


def test_skills_fall_back_to_the_category_when_the_detail_is_absent():
    resp = {k: v for k, v in _RESP.items() if k != "skills"}
    assert summarize_context_usage(resp)["skills_tokens"] == 9_999


def test_absence_is_unmeasured_never_zero():
    s = summarize_context_usage({"totalTokens": 5})
    assert s["available"] is True
    assert s["skills_tokens"] is None
    assert s["memory_files_tokens"] is None
    assert s["mcp_tokens_total"] is None
    assert s["mcp_tokens_by_server"] == {}


def test_unusable_payload_is_unavailable_not_an_exception():
    for bad in (None, [], "x", 3):
        s = summarize_context_usage(bad)
        assert s["available"] is False
        assert s["skills_tokens"] is None and s["mcp_tokens_total"] is None


def test_garbage_token_values_are_ignored_not_summed():
    resp = {
        "mcpTools": [
            {"serverName": "worker", "tokens": "many"},
            {
                "serverName": "worker",
                "tokens": True,
            },  # bool is an int subclass: must not count as 1
            {"serverName": "worker", "tokens": -5},
            {"serverName": "worker", "tokens": 7},
            {"tokens": 9},  # no server name: attributed to nothing
        ]
    }
    assert summarize_context_usage(resp)["mcp_tokens_by_server"] == {"worker": 7}


def test_tally_reports_first_cache_write_and_last_turn_input():
    t = TurnTally()
    t.note(
        {"input_tokens": 10, "cache_creation_input_tokens": 50_000, "cache_read_input_tokens": 0}
    )
    t.note(
        {"input_tokens": 20, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 50_000}
    )
    d = t.as_dict()
    assert d["turns"] == 2
    assert d["first_turn_cache_creation_input_tokens"] == 50_000
    assert d["last_turn_input_tokens"] == 20 + 300 + 50_000


def test_tally_counts_top_level_turns_only():
    t = TurnTally()
    t.note({"input_tokens": 5, "cache_creation_input_tokens": 100}, parent_tool_use_id=None)
    t.note(
        {"input_tokens": 9_999, "cache_creation_input_tokens": 9_999}, parent_tool_use_id="toolu_x"
    )
    d = t.as_dict()
    assert d["turns"] == 1
    assert d["last_turn_input_tokens"] == 105


def test_empty_tally_is_unmeasured():
    d = TurnTally().as_dict()
    assert d == {
        "turns": 0,
        "first_turn_cache_creation_input_tokens": None,
        "last_turn_input_tokens": None,
    }
