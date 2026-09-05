"""Tests for source-driven refresh selection — `knowledge_refresh impact`.

Every profile, topic and URL in the fixtures is invented (``docs/RULES.md`` R9). The two
real-corpus tests at the end name no tenant: they iterate whatever profiles are committed
and assert PROPERTIES, because `CLAUDE.md` requires a new deterministic CLI to be proven
against real-data properties before a skill cites it, and a fixture cannot prove that the
selector actually finds anything in the corpus it will be run against.

The property that matters is the SPLIT: `due` answers "has enough time passed?" and
`impact` answers "did the source change?". A topic on `review: evergreen` must be absent
from the first and present in the second — that combination is the whole reason this
module exists, and it is the one a fixture is least likely to catch by accident.
"""

from __future__ import annotations

from datetime import date

import pytest

from gtm_core import knowledge_meta as km
from gtm_core.knowledge_refresh import due_topics, impact_topics

TODAY = date(2026, 9, 1)


def _topic(
    root,
    profile,
    relpath,
    *,
    source="manual",
    review="90d",
    refreshed="2026-08-01",
    triggers=None,
    reflects=None,
):
    path = root / profile / "knowledge" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [f"source: {source}", f"refreshed: {refreshed}", f"review: {review}"]
    if triggers:
        fm.append(f"triggers: {triggers}")
    if reflects:
        fm.append(f"reflects: {reflects}")
    path.write_text("---\n" + "\n".join(fm) + "\n---\n# body\n", encoding="utf-8")
    (root / profile / "PROFILE.md").write_text("# profile\n", encoding="utf-8")
    return path


def test_impact_with_no_question_returns_nothing_not_everything(tmp_path):
    """ "Impacted by nothing" is the honest answer to an empty question — returning the whole
    corpus would look like a working selector while selecting nothing."""
    _topic(tmp_path, "acme", "alpha.md", triggers="sales-deck")
    assert impact_topics(tmp_path, "acme", TODAY) == []


def test_source_mode_inverts_the_existing_source_field(tmp_path):
    """The fan-out is already in the corpus: many topics condensed from one doc site share a
    `source:` URL. Inverting it costs no backfill, which is why this mode exists at all."""
    site = "https://docs.example.com/products/widget/"
    _topic(tmp_path, "acme", "a.md", source=site)
    _topic(tmp_path, "acme", "b.md", source=site + "deep-dive")
    _topic(tmp_path, "acme", "c.md", source="manual")
    hit = {m.relpath for m, _ in impact_topics(tmp_path, "acme", TODAY, source=site)}
    assert hit == {"a.md", "b.md"}, "substring match must catch a deeper path on the same origin"


def test_source_match_is_case_insensitive(tmp_path):
    _topic(tmp_path, "acme", "a.md", source="https://Docs.Example.COM/widget/")
    hit = impact_topics(tmp_path, "acme", TODAY, source="docs.example.com")
    assert [m.relpath for m, _ in hit] == ["a.md"]


def test_source_kind_mode_matches_triggers_exactly(tmp_path):
    """`triggers:` is a closed vocabulary, so matching is exact — a substring match here would
    make `case-study` select `case-studies` and similar near-misses."""
    _topic(tmp_path, "acme", "a.md", triggers="sales-deck,pricing")
    _topic(tmp_path, "acme", "b.md", triggers="product-release")
    hit = {m.relpath for m, _ in impact_topics(tmp_path, "acme", TODAY, source_kind="sales-deck")}
    assert hit == {"a.md"}


def test_impact_includes_evergreen_which_due_can_never_return(tmp_path):
    """THE load-bearing test. `evergreen` means "no clock", not "never review".

    A hook matrix or a voice guide carries it, is read by many skills, and is invalidated by
    exactly the events this selector matches — but it can never appear in `due`, so before
    `impact` there was no path by which a new deck could surface it.
    """
    _topic(
        tmp_path,
        "acme",
        "evergreen-topic.md",
        review="evergreen",
        refreshed="2020-01-01",
        triggers="sales-deck",
    )
    assert due_topics(tmp_path, "acme", TODAY) == [], "evergreen must stay out of the clock path"
    hit = impact_topics(tmp_path, "acme", TODAY, source_kind="sales-deck")
    assert [m.relpath for m, _ in hit] == ["evergreen-topic.md"]
    assert hit[0][1] == "evergreen", "status is reported, not filtered on"


def test_impact_reports_fresh_topics_too(tmp_path):
    """A topic coupled to a changed source is impacted whether or not its clock has run out —
    filtering on status would hide the topic that was refreshed yesterday against the OLD deck."""
    _topic(tmp_path, "acme", "fresh.md", refreshed=TODAY.isoformat(), triggers="sales-deck")
    hit = impact_topics(tmp_path, "acme", TODAY, source_kind="sales-deck")
    assert [(m.relpath, s) for m, s in hit] == [("fresh.md", "fresh")]


def test_reflects_is_carried_so_the_report_can_say_already_current(tmp_path):
    """`refreshed` dates the verification act; `reflects` names the material. Without the second,
    a report can say a topic is coupled but never whether it is behind."""
    _topic(tmp_path, "acme", "a.md", triggers="sales-deck", reflects="deck-2026-08")
    _topic(tmp_path, "acme", "b.md", triggers="sales-deck")
    got = {
        m.relpath: m.reflects
        for m, _ in impact_topics(tmp_path, "acme", TODAY, source_kind="sales-deck")
    }
    assert got == {"a.md": "deck-2026-08", "b.md": None}


def test_an_unknown_trigger_kind_is_a_metadata_error(tmp_path):
    """The vocabulary is closed for the same reason `review:` is: a typo that silently matches
    nothing is worse than one that fails loudly. Operators twice tried to invent a cadence value."""
    p = _topic(tmp_path, "acme", "a.md", triggers="on-next-deck-revision")
    meta = km.read_meta(p, tmp_path / "acme" / "knowledge")
    assert any("unknown 'triggers' kind" in e for e in meta.errors), meta.errors


# --- real corpus (CLAUDE.md: prove a new CLI on real-data properties) -----------------


def _committed_profiles():
    from gtm_core.paths import resolve_profiles_root

    root = resolve_profiles_root()
    return root, [
        p.name for p in sorted(root.iterdir()) if p.is_dir() and (p / "PROFILE.md").is_file()
    ]


def test_some_committed_topic_declares_triggers_or_impact_is_dead_code():
    """A selector nothing feeds is this repo's named recurring failure. If no committed topic
    carries `triggers:`, `--source-kind` returns nothing for every question and the wiring is
    decorative."""
    root, profiles = _committed_profiles()
    total = sum(
        1
        for profile in profiles
        for meta, _ in km.profile_meta(root, profile, date.today())
        if meta.triggers
    )
    assert total, "no committed topic declares `triggers:` — --source-kind can never select"


@pytest.mark.parametrize("kind", sorted(km.TRIGGER_KINDS))
def test_every_trigger_kind_is_selectable_or_deliberately_unused(kind):
    """Each kind either selects something in the committed corpus, or is unused — both fine.
    What must NOT happen is a kind raising, so the CLI's `choices=` can offer all of them."""
    root, profiles = _committed_profiles()
    for profile in profiles:
        impact_topics(root, profile, date.today(), source_kind=kind)
