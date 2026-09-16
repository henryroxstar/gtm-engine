"""Unit tests for the Syften worker MCP (``agent.mcp.syften``) — the deterministic
match-aggregation helpers (``_verdict``, ``_parse_analysis``, ``_summarize``, ``_sample``).

Real ``items/get`` records carry the AI verdict at ``item.analysis``, and — verified
2026-09-14 over 10,232 raw matches in ``content/<profile>/community-signals/raw/pull-*.json``
— it is often a Python-repr STRING rather than a JSON object, e.g.
``"{'nsfw': False, 'accept': False, 'rejection_reason': '...'}"``. A prior version of
``_verdict`` read only a top-level ``analysis`` field (absent from those records) and so
returned "unscored" for everything — its own docstring claimed the field was absent
entirely, which was itself wrong. These tests pin the fix: parse ``item.analysis`` safely
(``ast.literal_eval``, never ``eval``), tolerating the dict form, the repr-string form, a
missing form, and a malformed string (degrade to unscored, never raise).
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")

from agent.mcp.syften import server  # noqa: E402


def _match(item_analysis) -> dict:
    """A record shaped like the real Syften API response: no top-level ``analysis``,
    fictional post content only."""
    return {
        "id": "match-id",
        "matched_on": "2026-09-14T00:00:00Z",
        "filter": "widget filter",
        "item": {
            "backend": "dev.to",
            "type": "article",
            "text": "a fictional post about widgets",
            "title": "fictional title",
            "author": "fictional-author",
            "analysis": item_analysis,
        },
    }


def test_verdict_parses_repr_string_analysis() -> None:
    m = _match("{'nsfw': False, 'accept': False, 'rejection_reason': 'not about widgets'}")
    assert server._verdict(m) == "rejected"


def test_verdict_parses_dict_analysis() -> None:
    m = _match({"nsfw": False, "accept": True, "rejection_reason": ""})
    assert server._verdict(m) == "accepted"


def test_verdict_missing_analysis_is_unscored() -> None:
    m = _match(None)
    assert server._verdict(m) == "unscored"
    m.pop("item")
    assert server._verdict(m) == "unscored"


def test_verdict_malformed_analysis_string_is_unscored_never_raises() -> None:
    m = _match("{'accept': True, unterminated")
    assert server._verdict(m) == "unscored"


def test_verdict_falls_back_to_top_level_analysis() -> None:
    m = {"filter": "f", "item": {"backend": "reddit"}, "analysis": {"accept": False}}
    assert server._verdict(m) == "rejected"


def test_parse_analysis_never_uses_the_unsafe_builtin() -> None:
    # ast.literal_eval only evaluates literal containers/values — it must refuse to run
    # arbitrary code smuggled into a match body.
    assert server._parse_analysis("__import__('os').system('echo pwned')") is None
    assert server._parse_analysis("not a dict at all") is None
    assert server._parse_analysis(42) is None
    assert server._parse_analysis({"accept": True}) == {"accept": True}


def test_summarize_computes_real_tallies_from_item_analysis() -> None:
    # Before the fix this scored 0 accepted/rejected regardless of the true split, because
    # _verdict only ever saw the (always-absent) top-level `analysis` field.
    matches = [
        _match({"accept": False}),
        _match({"accept": False}),
        _match({"accept": False}),
        _match({"accept": True}),
        _match("{'accept': True}"),
    ]
    summary = server._summarize(matches)
    bucket = summary["per_filter"]["widget filter"]
    assert bucket == {"accepted": 2, "rejected": 3, "unscored": 0, "total": 5}


def test_sample_reads_item_analysis() -> None:
    matches = [_match({"accept": False, "excerpt": "fictional excerpt"})]
    sample = server._sample(matches)
    assert sample[0]["accept"] is False
    assert sample[0]["excerpt"] == "fictional excerpt"


def test_sample_tolerates_repr_string_analysis() -> None:
    matches = [_match("{'accept': True, 'excerpt': 'fictional excerpt 2'}")]
    sample = server._sample(matches)
    assert sample[0]["accept"] is True
    assert sample[0]["excerpt"] == "fictional excerpt 2"


def test_raw_dir_falls_back_to_resolved_content_root(monkeypatch, tmp_path):
    monkeypatch.delenv("GTM_CONTENT_ROOT", raising=False)
    monkeypatch.setenv("GTM_PROFILE", "demo")
    monkeypatch.setattr(server, "resolve_content_root", lambda: tmp_path)
    assert server._raw_dir() == tmp_path / "demo" / "community-signals" / "raw"


def test_raw_dir_honours_content_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILE", "demo")
    assert server._raw_dir() == tmp_path.resolve() / "demo" / "community-signals" / "raw"


@pytest.mark.parametrize("profile", ["", "../x", "a/b", ".."])
def test_raw_dir_refuses_missing_or_unsafe_profile(monkeypatch, tmp_path, profile):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILE", profile)
    assert server._raw_dir() is None
