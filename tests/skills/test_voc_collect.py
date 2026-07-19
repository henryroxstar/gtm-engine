"""Guards for the voice-of-customer source-coverage collector.

The collector is the mechanical guarantee behind the skill's primary rigor rule:
every source carries a **speaker** (customer-voice / bd-focus / synthetic-persona /
mixed) assigned in code, so the brief can never silently merge market demand with
BD's assumptions. These tests pin the speaker map, the latest-by-embedded-date pick,
graceful handling of missing sources, and that it resolves against `_template`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from gtm_core.paths import resolve_content_root, resolve_profiles_root
from gtm_core.voc import collect as voc

TODAY = date(2026, 7, 18)


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_fixture(tmp_path: Path) -> tuple[Path, Path]:
    content = tmp_path / "content"
    profiles = tmp_path / "profiles"
    p = "acme"
    croot = content / p

    # 1 Syften market-signals — two files; the later date must win.
    _write(croot / "market-signals" / "2026-27-2wk-snapshot-2026-07-04.md")
    _write(croot / "market-signals" / "2026-29-2wk-snapshot-2026-07-18.md")
    # 2 research — a date-range file whose END date should sort last.
    _write(croot / "research" / "signal-2026-07-15-to-18.html")
    _write(croot / "research" / "note-2026-07-10.md")
    # 3a intent JSON with topic + account arrays.
    _write(
        croot / "prospects" / "intent" / "rr-intentsify-latest.json",
        json.dumps({"topics": ["Topic A", "Topic B", "Topic C"], "accounts": []}),
    )
    # 3b prospect briefs + pipeline pointer.
    _write(croot / "prospects" / "prospects-20260717.md")
    _write(croot / "prospects" / "prospects-20260718-net-new.md")
    _write(croot / "prospects" / "latest.json", json.dumps({"items": [{}, {}, {}]}))
    # 4 linkedin drafts — one in linkedin/, one under an account.
    _write(croot / "linkedin" / "linkedin-reply-someone-20260718.md")
    _write(croot / "accounts" / "beta" / "linkedin-reply-beta-20260717.md")
    # 5 dossiers + outreach packs.
    _write(croot / "accounts" / "beta" / "dossier-spec.json", "{}")
    _write(
        croot / "accounts" / "beta" / "prospects-20260718-outreach-beta.md",
        "# Outreach Pack — Beta — 2026-07-18\n**Tier:** A\n",
    )
    # 6 adversary personas — README excluded from the count.
    _write(profiles / p / "knowledge" / "adversary-testing" / "README.md")
    _write(profiles / p / "knowledge" / "adversary-testing" / "one-viewpoint.md")
    _write(profiles / p / "knowledge" / "adversary-testing" / "two-viewpoint.md")
    return content, profiles


def _by_id(manifest: dict) -> dict:
    return {s["id"]: s for s in manifest["sources"]}


def test_speaker_map_is_fixed_per_source(tmp_path):
    content, profiles = _build_fixture(tmp_path)
    src = _by_id(voc.collect(content, profiles, "acme", today=TODAY))
    assert src["syften_market_signals"]["speaker"] == "customer-voice"
    assert src["intent_topics"]["speaker"] == "customer-voice"
    assert src["news_research"]["speaker"] == "mixed"
    assert src["prospect_briefs"]["speaker"] == "bd-focus"
    assert src["linkedin_replies"]["speaker"] == "bd-focus"
    assert src["account_dossiers"]["speaker"] == "bd-focus"
    assert src["adversary_personas"]["speaker"] == "expert-lens"


def test_latest_picks_the_end_date_of_a_range_and_newest_file(tmp_path):
    content, profiles = _build_fixture(tmp_path)
    src = _by_id(voc.collect(content, profiles, "acme", today=TODAY))
    assert src["syften_market_signals"]["latest"]["date"] == "2026-07-18"
    assert src["syften_market_signals"]["latest"]["age_days"] == 0
    # Range file "…-2026-07-15-to-18" must sort on the trailing 2026-07-18, beating -07-10.
    assert src["news_research"]["latest"]["date"] == "2026-07-18"


def test_counts_and_extras(tmp_path):
    content, profiles = _build_fixture(tmp_path)
    src = _by_id(voc.collect(content, profiles, "acme", today=TODAY))
    assert src["intent_topics"]["extra"] == {
        "topics": 3,
        "topic_names": ["Topic A", "Topic B", "Topic C"],
        "surging_accounts": 0,
    }
    assert src["prospect_briefs"]["extra"]["pipeline_accounts"] == 3
    assert src["linkedin_replies"]["corpus_count"] == 2  # linkedin/ + accounts/
    assert src["account_dossiers"]["extra"] == {"outreach_packs": 1, "dossier_specs": 1}
    assert src["adversary_personas"]["corpus_count"] == 2  # README excluded


def test_missing_sources_report_present_false_not_error(tmp_path):
    content = tmp_path / "content"
    profiles = tmp_path / "profiles"
    (content / "empty").mkdir(parents=True)
    manifest = voc.collect(content, profiles, "empty", today=TODAY)
    assert all(s["present"] is False for s in manifest["sources"])
    assert manifest["summary"]["present"] == 0
    assert len(manifest["summary"]["missing"]) == len(manifest["sources"])


def test_resolves_against_template_profile():
    # Integrity: the collector must run against the canonical `_template` scaffold
    # without raising, even where content sources are absent.
    manifest = voc.collect(
        resolve_content_root(), resolve_profiles_root(), "_template", today=TODAY
    )
    assert manifest["profile"] == "_template"
    assert {s["speaker"] for s in manifest["sources"]} <= {
        "customer-voice",
        "bd-focus",
        "expert-lens",
        "mixed",
    }


def test_rejects_unsafe_profile_segment(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        voc.collect(tmp_path / "content", tmp_path / "profiles", "../evil", today=TODAY)
