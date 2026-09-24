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


# --- managed roots: products/ is in scope, namespaced so topics can't collide -----


def test_profile_meta_covers_products_with_a_namespaced_relpath(tmp_path):
    """``products/`` joined the cycle on 2026-08-29. knowledge/ topics keep BARE relpaths (the
    vocabulary every skill and staged candidate already uses); product topics carry a ``products/``
    prefix. Both must appear in one sweep."""
    prof = tmp_path / "acme"
    (prof / "knowledge").mkdir(parents=True)
    (prof / "products" / "widget").mkdir(parents=True)
    (prof / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")
    (prof / "knowledge" / "company.md").write_text(VALID_FM, encoding="utf-8")
    (prof / "products" / "widget" / "PRODUCT.md").write_text(VALID_FM, encoding="utf-8")

    rels = {m.relpath for m, _ in km.profile_meta(tmp_path, "acme", date(2026, 1, 1))}
    assert rels == {"company.md", "products/widget/PRODUCT.md"}


def test_check_reports_products_under_their_own_root(tmp_path):
    prof = tmp_path / "acme"
    (prof / "products" / "widget").mkdir(parents=True)
    (prof / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")
    (prof / "products" / "widget" / "PRODUCT.md").write_text("# no frontmatter\n", encoding="utf-8")
    assert km.check(tmp_path, ["acme"]) == ["acme/products/widget/PRODUCT.md: missing frontmatter"]


def test_a_knowledge_topic_cannot_be_shadowed_by_a_product_topic(tmp_path):
    """The prefix is what keeps the two namespaces disjoint — same trailing path, distinct topics."""
    prof = tmp_path / "acme"
    (prof / "knowledge" / "widget").mkdir(parents=True)
    (prof / "products" / "widget").mkdir(parents=True)
    (prof / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")
    (prof / "knowledge" / "widget" / "PRODUCT.md").write_text(VALID_FM, encoding="utf-8")
    (prof / "products" / "widget" / "PRODUCT.md").write_text(VALID_FM, encoding="utf-8")

    rels = sorted(m.relpath for m, _ in km.profile_meta(tmp_path, "acme", date(2026, 1, 1)))
    assert rels == ["products/widget/PRODUCT.md", "widget/PRODUCT.md"]


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


# --- generated views are exempt from lifecycle metadata ----------------------


_GENERATED = (
    "<!-- gtm_core.messaging:generated — do not edit; regenerate from angles.toml -->\n"
    "\n# Outreach hook matrix\n"
)


def test_a_generated_topic_needs_no_frontmatter(tmp_path):
    """A generated view's freshness belongs to its SOURCE file, not to itself.

    Seeding `refreshed:` here would be a second home for that fact, and a wrong one — a
    regenerated-but-unchanged view would move the date while nothing changed — and it would
    break the generator's drift check, which compares committed bytes against a fresh render
    and must stay date-independent.
    """
    path = tmp_path / "hook-matrix.md"
    path.write_text(_GENERATED, encoding="utf-8")
    meta = km.read_meta(path, tmp_path)
    assert meta.generated is True
    assert meta.errors == ()


def test_an_arbitrary_topic_cannot_claim_the_exemption_with_the_banner(tmp_path):
    """The finding this allowlist closes: a marker any file can paste is an opt-out, not a marker.

    An earlier version matched any `gtm_core.<anything>:generated` comment on line 1. One pasted
    line would then have exempted `product.md` or `case-studies.md` from this gate — and unlike
    `hook-matrix.md` nothing regenerates those, so there is NO drift check behind the exemption.
    The file would simply stop being checked for staleness by anything at all.
    """
    for name in ("product.md", "case-studies.md", "company.md"):
        path = tmp_path / name
        path.write_text(_GENERATED, encoding="utf-8")
        meta = km.read_meta(path, tmp_path)
        assert meta.generated is False, f"{name} claimed the generated exemption"
        assert "missing frontmatter" in meta.errors


def test_the_registered_banner_matches_the_one_the_generator_writes():
    """`_GENERATED_TOPICS` is a second home for the generator's banner; pin the two together.

    Importing `matrix_view` here rather than in `knowledge_meta` keeps the production import
    graph clean — `matrix_view` reaches `hook_coverage.config`, which puts `tests/linter` on
    `sys.path` at import time.
    """
    from gtm_core.messaging import matrix_view

    assert matrix_view.MATRIX_FILE in km._GENERATED_TOPICS
    pattern = km._GENERATED_TOPICS[matrix_view.MATRIX_FILE]
    assert pattern.match(matrix_view.BANNER), (
        "matrix_view's banner no longer matches the pattern knowledge_meta exempts on — a "
        "generated matrix would start failing the frontmatter gate"
    )


def test_a_hand_kept_topic_of_the_same_name_still_needs_frontmatter(tmp_path):
    """The negative control, and the reason the exemption is content-based not name-based.

    Other profiles still hand-keep a `hook-matrix.md`, where the frontmatter is real. A
    filename exclusion would have switched the gate off for all of them at once.
    """
    path = tmp_path / "hook-matrix.md"
    path.write_text("# Outreach hook matrix\n\nhand-kept, no banner.\n", encoding="utf-8")
    meta = km.read_meta(path, tmp_path)
    assert meta.generated is False
    assert "missing frontmatter" in meta.errors


def test_a_banner_buried_below_the_first_line_does_not_claim_the_exemption(tmp_path):
    """A hand-authored file cannot opt out by pasting the banner further down.

    Same property `agent/publish.py` holds for its gate markers: the marker is only a marker
    where the format says it is, never wherever it appears.
    """
    path = tmp_path / "hook-matrix.md"
    path.write_text("# Notes\n\nSomeone pasted this:\n" + _GENERATED, encoding="utf-8")
    meta = km.read_meta(path, tmp_path)
    assert meta.generated is False
    assert "missing frontmatter" in meta.errors


def test_the_exemption_survives_frontmatter_above_the_banner(tmp_path):
    """A generated file that later grows frontmatter is still read as generated.

    `parse_frontmatter` strips the block, so the banner is still the body's first line — and a
    stale `refreshed:` inherited from the file's hand-kept past must not start being enforced.
    """
    path = tmp_path / "hook-matrix.md"
    path.write_text(VALID_FM.split("---\n")[1].join(("---\n", "---\n")) + _GENERATED, "utf-8")
    meta = km.read_meta(path, tmp_path)
    assert meta.generated is True
    assert meta.errors == ()
