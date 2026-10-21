"""A scoped dashboard page carries no OUT-OF-SCOPE campaign's identifiers.

WHY THIS EXISTS (2026-09-04/05). A campaign page's headline tiles read "0 of 990 emails ·
51 people · 3 sequences" for a run whose real figures were 8 emails and 4 people. Every one
of those numbers was real, correctly computed, and belonged to `agent-gateway-cross-org` —
the page rolled up the profile under one campaign's title. The fix (``scope_to_campaign``)
was verified by hand, with ``grep``, about a dozen times over one session, and the check
disappeared with the session. This is that grep, made permanent and run on every page the
renderer can produce.

WHAT IT KEYS ON, AND WHY NOT THE OBVIOUS THINGS. Measured against the live tenant's pages
before this test was written:

* **Sequence ids never render.** ``dlPyoJA6zL`` appears zero times on every page. A leak
  check keyed on them would pass vacuously forever — the failure mode this repo calls a
  fail-open rule, and the reason the 2026-09-04 gates passed a defective batch.
* **Slugs mostly do not render.** ``agent-gateway-cross-org`` appears zero times even on
  the profile-wide page that includes it.
* **Titles DO render.** ``Cross-org agent trust`` appears twice profile-wide and four times
  on the union page. So titles and roster-glob CSV basenames are the keys.
* **Never numbers.** A scoped page legitimately shows pool-wide figures (734, 608, 869)
  under ``_pool_scope_note``, which says in so many words that they are not scoped. A
  number-based leak check would convict those and be switched off within a week.

WHAT IT CANNOT CATCH: a leaked figure carrying no identifier beside it (the original 990 —
which is why ``test_dashboard_tile_provenance.py`` exists and checks the arithmetic instead);
a title so generic it is a substring of ordinary copy; and anything on the profile-wide page,
which is correctly about every campaign and is therefore excluded by construction.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from gtm_core import email_campaign_dashboard as gd  # noqa: E402
from gtm_core.email_campaign_dashboard.scope import resolve  # noqa: E402
from tests.contracts.dashboard_page import panel, section

#: A title short or generic enough to appear in ordinary copy would convict every page. Two
#: words is the floor at which a campaign title is a name rather than a phrase.
_MIN_TITLE_WORDS = 2


def _identifiers(campaign: dict) -> set[str]:
    """The strings that would betray this campaign's presence on a page it is not part of."""
    out = {campaign.get("slug", "")}
    title = (campaign.get("title") or "").strip()
    if len(title.split()) >= _MIN_TITLE_WORDS:
        out.add(title)
    out |= {Path(g).name for g in (campaign.get("roster_globs") or []) if "*" not in Path(g).name}
    return {x for x in out if x}


def assert_no_leak(page_html: str, model: dict, in_scope: set[str]) -> list[str]:
    """Return every out-of-scope identifier found on the page. Empty is clean."""
    leaks = []
    for c in model["_all_campaigns"]:
        if c.get("slug") in in_scope:
            continue
        leaks += [ident for ident in _identifiers(c) if ident in page_html]
    return sorted(set(leaks))


def _model(tmp_path, profile="acme"):
    m = gd.build_model(profile, tmp_path)
    m["_all_campaigns"] = list(m["campaigns"]["campaigns"])
    return m


CSV = "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"


def _seed(tmp_path, profile="acme"):
    """Two campaigns with distinct titles and one declaring its own roster — the shape of
    the live profile, which is what makes the union page a usable positive control."""
    from gtm_core import prospects_consolidate as pc

    pros = pc._prospects_dir(profile, tmp_path)
    (pros / "sequences").mkdir(parents=True, exist_ok=True)
    (pros / "mine-20260904-hubspot.csv").write_text(
        CSV + "Ada,L,ada@analytical.example,Analytical Engine,analytical.example,verified,A\n",
        encoding="utf-8",
    )
    camps = pros.parent / "plans" / "campaigns"
    camps.mkdir(parents=True, exist_ok=True)
    (camps / "mine-20260904.campaign.toml").write_text(
        'slug = "mine-20260904"\n'
        'title = "Singapore agentic builders"\n'
        'status = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n'
        "[targets]\nprospects = 4\nemails = 8\nreply_rate = 0.018\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 2\n",
        encoding="utf-8",
    )
    (camps / "other-20260718.campaign.toml").write_text(
        'slug = "other-20260718"\n'
        'title = "Cross-org agent trust"\n'
        'status = "active"\n'
        "[targets]\nprospects = 330\nemails = 990\nreply_rate = 0.018\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 3\n",
        encoding="utf-8",
    )
    return pros


def test_a_scoped_page_carries_no_other_campaigns_identifiers(tmp_path):
    """The NEGATIVE control: the filter works."""
    _seed(tmp_path)
    m = _model(tmp_path)
    scoped = gd.scope_to_campaign(m, "mine-20260904")
    leaks = assert_no_leak(gd.render_html(scoped), m, {"mine-20260904"})
    assert not leaks, (
        f"a page scoped to mine-20260904 names {leaks}. Something is reading the "
        "profile-wide model instead of the scoped one — check what scope_to_campaign "
        "does NOT filter (supply, intent, market, cells) and whether the block should "
        "be _scoped_out or carry a _pool_scope_note."
    )


def test_the_detector_finds_a_title_when_it_is_genuinely_there(tmp_path):
    """The POSITIVE control, and the reason this test is not vacuous.

    A check that convicts nothing is usually broken, not vindicated. The union page
    legitimately contains BOTH titles, so pointing the detector at it with only one slug
    declared in-scope must convict — proving the detector reads what the page says rather
    than always returning an empty list.
    """
    _seed(tmp_path)
    m = _model(tmp_path)
    union = gd.render_html(gd.scope_to_campaign(_model(tmp_path), "mine-20260904,other-20260718"))
    assert assert_no_leak(union, m, {"mine-20260904"}), (
        "the union page contains both campaigns and the detector found neither — it is "
        "not reading the page. Check _identifiers() against what actually renders: "
        "sequence ids and slugs largely do not, titles do."
    )
    assert not assert_no_leak(union, m, {"mine-20260904", "other-20260718"}), (
        "both campaigns are in scope on a union page, so neither is a leak"
    )


def test_a_seeded_filter_failure_is_convicted(tmp_path):
    """SEEDED VIOLATION: disable the campaign filter and the check must fail loudly.

    This is the regression the whole test exists for — if ``scope_to_campaign`` stopped
    narrowing ``campaigns``, the page would render the rollup under one campaign's title
    exactly as it did on 2026-09-04, and every other assertion here would still pass.
    """
    _seed(tmp_path)
    m = _model(tmp_path)
    broken = gd.scope_to_campaign(_model(tmp_path), "mine-20260904")
    broken["campaigns"] = dict(broken["campaigns"], campaigns=m["_all_campaigns"])
    leaks = assert_no_leak(gd.render_html(broken), m, {"mine-20260904"})
    assert "Cross-org agent trust" in leaks, (
        "the campaign filter was removed and the leak check did not notice. It is fail-open."
    )


@pytest.mark.parametrize("scope_mode", ["campaign", "open"])
def test_every_scope_the_cli_can_produce_is_clean(tmp_path, scope_mode):
    """Not just the one slug a test remembered — every scope the flag can resolve to."""
    _seed(tmp_path)
    m = _model(tmp_path)
    sc = resolve(scope_mode, "mine-20260904", m["_all_campaigns"])
    page = gd.render_html(gd.scope_to_campaign(_model(tmp_path), sc.csv))
    leaks = assert_no_leak(page, m, set(sc.slugs))
    assert not leaks, f"--scope {scope_mode} leaked {leaks}"


def test_the_profile_wide_page_is_exempt_by_construction(tmp_path):
    """It is ABOUT every campaign, so naming them all is correct, not a leak. Asserted so a
    future reader does not 'fix' the rollup into hiding the campaigns it exists to roll up."""
    _seed(tmp_path)
    page = gd.render_html(_model(tmp_path))
    assert "Cross-org agent trust" in page and "Singapore agentic builders" in page


def test_a_scoped_page_never_shows_a_pool_figure_unlabelled(tmp_path):
    """The reason this check keys on identifiers and never on numbers.

    Pool-wide figures on a scoped page are correct and deliberate — scoping a shared pool
    would trade one wrong number for another. They are legal precisely BECAUSE the page
    says so. There are exactly two honest shapes, and this pins both: with a roster the
    whole "who" panel is REPLACED by that campaign's accounts and no pool figure appears;
    without one the pool figures render under an explicit not-scoped note. The illegal
    third shape — pool figures under a campaign's title with no note — is what put "731
    people we will actually email" on a page whose goal was 4 people.
    """
    _seed(tmp_path)
    with_roster = gd.render_html(gd.scope_to_campaign(_model(tmp_path), "mine-20260904"))
    assert "Analytical Engine" in section(panel(with_roster, "accounts"), "account-table")
    assert "people we will actually email" not in with_roster, (
        "the shared pool's headline figure rendered on a page that has its own roster"
    )

    # other-20260718 declares no roster_globs, so this page takes the pool branch.
    no_roster = gd.render_html(gd.scope_to_campaign(_model(tmp_path), "other-20260718"))
    assert "people we will actually email" in no_roster, "expected the pool branch here"
    assert re.search(r"Profile-wide, not (this campaign|the|these)", no_roster), (
        "a pool-wide block rendered on a scoped page with no note saying it is not scoped "
        "— see format._pool_scope_note."
    )


def test_subject_card_does_not_render_twice_on_scoped_page(tmp_path):
    """The subject-line card should appear exactly once on a campaign-scoped page.

    BUG (2026-09-25): views_what.py had {subjects_card} on one line and
    {subjects_card or subjects_full} on the next, causing a double render on scoped pages.
    """
    _seed(tmp_path)
    scoped = gd.render_html(gd.scope_to_campaign(_model(tmp_path), "mine-20260904"))
    heading_text = "Every subject line in the campaign"
    count = scoped.count(heading_text)
    assert count == 1, f"heading '{heading_text}' appears {count} times, expected 1"
