"""Unit + corpus contract tests for gtm_core.knowledge_meta (knowledge-lifecycle PRD, Phase 1).

Stdlib + pytest only (no external deps), matching the rest of the suite. The final test is the
CI enforcement surface: every committed profile's managed knowledge must carry valid frontmatter —
the pytest analogue of the local `bash tests/lint/knowledge_meta_check.sh` gate (uv isn't available
in the CI `gates` job, so validity is enforced here, in the pytest job — mirroring test_codegen).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from gtm_core import knowledge_meta as km
from gtm_core.paths import resolve_profiles_root

VALID_FM = "---\nsource: manual\nrefreshed: 2026-01-01\nreview: 90d\n---\n# Title\nbody\n"


# --- frontmatter parsing ------------------------------------------------------


def test_parse_frontmatter_present():
    meta, body = km.parse_frontmatter(VALID_FM)
    assert meta == {"source": "manual", "refreshed": "2026-01-01", "review": "90d"}
    assert body == "# Title\nbody\n"


def test_parse_frontmatter_absent():
    meta, body = km.parse_frontmatter("# No frontmatter\ntext\n")
    assert meta == {}
    assert body == "# No frontmatter\ntext\n"


def test_parse_frontmatter_unterminated_is_not_frontmatter():
    text = "---\nsource: manual\n# never closed\n"
    meta, body = km.parse_frontmatter(text)
    assert meta == {}
    assert body == text


# --- managed-topic classification ---------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "company.md",
        "icp-personas.md",
        "guidance/01-nist.md",
        "industry/fs-payments.md",
        "adversary-testing/red-team-airq-viewpoint.md",
        "messaging/agent-identity-primitives.md",
    ],
)
def test_is_managed_topic_includes(rel):
    assert km.is_managed_topic(rel) is True


@pytest.mark.parametrize(
    "rel",
    [
        "REFRESH.md",  # the refresh SOP, not a topic
        "deck-composer.md",  # a skill definition living in knowledge/
        "source/01-company-overview.md",  # raw long-form brief
        "brand/BRAND-ASSETS-README.md",  # brand asset dir
        "industry/README.md",  # directory index
        "guidance/SOURCES.md",  # directory sources doc
        "voice-bans.txt",  # not markdown
    ],
)
def test_is_managed_topic_excludes(rel):
    assert km.is_managed_topic(rel) is False


# --- metadata validation ------------------------------------------------------


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_read_meta_valid(tmp_path):
    p = _write(tmp_path, "company.md", VALID_FM)
    meta = km.read_meta(p, tmp_path)
    assert meta.errors == ()
    assert meta.source == "manual"
    assert meta.refreshed == date(2026, 1, 1)
    assert meta.review == "90d"


def test_read_meta_missing_frontmatter(tmp_path):
    p = _write(tmp_path, "company.md", "# no frontmatter\n")
    meta = km.read_meta(p, tmp_path)
    assert meta.has_frontmatter is False
    assert meta.errors == ("missing frontmatter",)


def test_read_meta_flags_missing_field_bad_date_bad_cadence(tmp_path):
    p = _write(tmp_path, "company.md", "---\nreview: weekly\nrefreshed: 2026-13-40\n---\nbody\n")
    meta = km.read_meta(p, tmp_path)
    joined = " ".join(meta.errors)
    assert "missing required field 'source'" in joined
    assert "invalid 'refreshed' date" in joined
    assert "invalid 'review' cadence" in joined


# --- staleness ----------------------------------------------------------------


def _meta(refreshed: date, review: str) -> km.KnowledgeMeta:
    return km.KnowledgeMeta(
        relpath="x.md", has_frontmatter=True, source="manual", refreshed=refreshed, review=review
    )


def test_status_fresh():
    today = date(2026, 6, 1)
    assert km.status_of(_meta(today - timedelta(days=10), "90d"), today) == "fresh"


def test_status_due_soon():
    today = date(2026, 6, 1)
    # due in 7 days (within the 14-day window)
    assert km.status_of(_meta(today - timedelta(days=83), "90d"), today) == "due-soon"


def test_status_overdue():
    today = date(2026, 6, 1)
    assert km.status_of(_meta(today - timedelta(days=100), "90d"), today) == "overdue"


def test_status_evergreen_never_stale():
    today = date(2026, 6, 1)
    assert km.status_of(_meta(date(2000, 1, 1), "evergreen"), today) == "evergreen"


def test_status_unknown_without_frontmatter():
    today = date(2026, 6, 1)
    m = km.KnowledgeMeta(relpath="x.md", has_frontmatter=False, errors=("missing frontmatter",))
    assert km.status_of(m, today) == "unknown"


# --- seeding + defaults -------------------------------------------------------


def test_seed_file_inserts_then_idempotent(tmp_path):
    kdir = tmp_path / "knowledge"
    p = _write(tmp_path, "knowledge/company.md", "# Company\nfacts\n")
    assert km.seed_file(p, kdir, tmp_path) is True  # inserted
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "# Company\nfacts\n" in text  # body preserved below frontmatter
    meta = km.read_meta(p, kdir)
    assert meta.errors == ()  # seeded frontmatter is valid
    assert km.seed_file(p, kdir, tmp_path) is False  # second run is a no-op


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("voice.md", "evergreen"),
        ("hook-matrix.md", "evergreen"),
        ("company.md", "90d"),
        ("guidance/01-nist.md", "180d"),
        ("adversary-testing/x-viewpoint.md", "180d"),
        ("travel-policy.md", "365d"),
        ("industry/fs-payments.md", "90d"),
    ],
)
def test_default_review(rel, expected):
    assert km.default_review(rel) == expected


def test_refreshed_date_reads_frontmatter(tmp_path):
    p = _write(tmp_path, "company.md", VALID_FM)
    assert km.refreshed_date(p) == date(2026, 1, 1)


def test_refreshed_date_none_without_frontmatter(tmp_path):
    p = _write(tmp_path, "company.md", "# no frontmatter\n")
    assert km.refreshed_date(p) is None


# --- check() over a synthetic profile tree ------------------------------------


def test_check_flags_then_passes(tmp_path):
    prof = tmp_path / "acme"
    (prof).mkdir()
    (prof / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")
    kdir = prof / "knowledge"
    kdir.mkdir()
    (kdir / "company.md").write_text("# Company\nfacts\n", encoding="utf-8")  # no frontmatter
    (kdir / "REFRESH.md").write_text("not a topic\n", encoding="utf-8")  # excluded

    problems = km.check(tmp_path, ["acme"])
    assert problems == ["acme/knowledge/company.md: missing frontmatter"]

    km.seed_file(kdir / "company.md", kdir, tmp_path)
    assert km.check(tmp_path, ["acme"]) == []


# --- CI enforcement: the real committed corpus is valid -----------------------


def test_committed_corpus_has_valid_metadata():
    """Every managed knowledge topic across all committed profiles must have valid frontmatter.
    Fix drift with: uv run python -m gtm_core.knowledge_meta seed --all"""
    profiles_root = resolve_profiles_root()
    profiles = km._all_profiles(profiles_root)
    assert profiles, "no profiles found — resolve_profiles_root() misconfigured?"
    problems = km.check(profiles_root, profiles)
    assert problems == [], (
        "knowledge files missing/invalid lifecycle metadata:\n  "
        + "\n  ".join(problems)
        + "\nrun: uv run python -m gtm_core.knowledge_meta seed --all"
    )
