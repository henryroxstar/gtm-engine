"""A figure that cannot be aggregated honestly over a scope REFUSES, and says why.

WHY THIS EXISTS (2026-09-05). Adding ``--scope open`` reproduces the "0 of 990" class of
error one level up. The tiles already looped over ``m["campaigns"]["campaigns"]``, so on a
multi-campaign page they silently took whichever campaign won the loop:
``window = window or c.get("window")`` (first wins) and, three functions away on the SAME
page, ``tgt = c.get("targets") or {}`` (last wins) — two different campaigns answering
"what is this scope's target?" in one render. Every value real, every value someone else's.

The rules themselves live as comments beside the arithmetic in ``aggregate.py`` — a rule in
a doc is a rule nobody runs. This file is the check that they still hold, and it is written
against the DEFECT rather than the implementation: each test names a figure and the reason
that figure cannot be a single number, so a rewrite that keeps the honesty passes and one
that quietly restores first-wins does not.

WHAT IT CANNOT CATCH: whether the aggregation rule chosen for a figure is the RIGHT rule.
That an email target is summed and a ceiling is not is a judgement about what those numbers
mean; this only proves the code does what the rule says. Nor does it check the prose of a
sub-line beyond the phrase each assertion pins.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from gtm_core import email_campaign_dashboard as gd  # noqa: E402
from gtm_core.email_campaign_dashboard.aggregate import _scope_figures  # noqa: E402
from gtm_core.email_campaign_dashboard.format import _agree  # noqa: E402

CSV = "First Name,Last Name,Email,Company Name,Company Domain Name,Email Status,GTM_Tier\n"


def _seed(tmp_path, *, second: str, profile="acme"):
    """One campaign with a full manifest, plus a second whose body the test controls."""
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
        'slug = "mine-20260904"\ntitle = "Singapore agentic builders"\nstatus = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n'
        "[targets]\nprospects = 100\nemails = 200\nreply_rate = 0.02\n"
        "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 2\n",
        encoding="utf-8",
    )
    (camps / "other-20260718.campaign.toml").write_text(second, encoding="utf-8")
    return pros


def _figures(tmp_path):
    return _scope_figures(gd.build_model("acme", tmp_path))


def _page(tmp_path):
    return gd.render_html(gd.build_model("acme", tmp_path))


BASE = (
    'slug = "other-20260718"\ntitle = "Cross-org agent trust"\nstatus = "active"\n'
    "[targets]\nprospects = 300\nemails = 900\nreply_rate = 0.02\n"
    "[window]\ndaily_cap = 90\nmailboxes = 9\ntouches = 2\n"
)


def test_a_shared_ceiling_is_never_summed(tmp_path):
    """The mailboxes are the same mailboxes. 90 + 90 is capacity that does not exist."""
    _seed(tmp_path, second=BASE)
    cap, why = _figures(tmp_path)["cap"]
    assert cap == 90 and why is None, "two campaigns declaring 90/day share one 90/day"
    assert "shared across all 2 campaigns, not each" in _page(tmp_path)


def test_disagreeing_ceilings_refuse_rather_than_pick_one(tmp_path):
    _seed(tmp_path, second=BASE.replace("daily_cap = 90", "daily_cap = 30"))
    cap, why = _figures(tmp_path)["cap"]
    assert cap is None, "a scope with two different ceilings has no single ceiling"
    assert "different sending ceilings" in why and "30" in why and "90" in why
    page = _page(tmp_path)
    assert "not a total" in page


def test_disagreeing_cadences_refuse_the_whole_forecast(tmp_path):
    """Every duration divides by ``touches``; at two cadences no sentence in that card is
    true of any sequence on the page. This is the live case — 3 touches vs 2."""
    _seed(tmp_path, second=BASE.replace("touches = 2", "touches = 3"))
    _touches, why = _figures(tmp_path)["touches"]
    assert why and "cadences" in why
    page = _page(tmp_path)
    assert "Not shown as one figure" in page
    assert "Done by" not in page, "the forecast rendered a finish date it cannot compute"
    assert "do not run side by side" in page, "the refusal must still say what IS true"


def test_a_partial_email_target_refuses_rather_than_reporting_a_total(tmp_path):
    """One of two campaigns declaring a target makes the sum half a plan wearing the whole
    label — the same shape as a scoped page showing the profile's numbers."""
    _seed(tmp_path, second='slug = "other-20260718"\ntitle = "No targets"\nstatus = "active"\n')
    planned, why = _figures(tmp_path)["planned"]
    assert planned is None and "1 of the 2 campaigns" in why
    assert "of —" in _page(tmp_path), "a refused target must not render as a number"


def test_a_complete_email_target_sums_and_shows_its_composition(tmp_path):
    _seed(tmp_path, second=BASE)
    planned, why = _figures(tmp_path)["planned"]
    assert planned == 1100 and why is None
    assert "2 campaign plans added together, each set on its own" in _page(tmp_path)


def test_the_reply_target_is_weighted_by_prospects_never_pooled_from_replies(tmp_path):
    """``Σreplies/Σprospects`` would print 0.0% here — NEITHER manifest declares ``replies``,
    which is exactly true of the live profile. The only blend that keeps the meaning of
    "target" weights each declared rate by the people it was set against."""
    _seed(tmp_path, second=BASE.replace("reply_rate = 0.02", "reply_rate = 0.06"))
    fig = _figures(tmp_path)
    rate, why = fig["target_rate"]
    assert why is None and fig["rates_differ"]
    # 100 people at 2% and 300 at 6% -> 5%, not the unweighted 4% and not 0%.
    assert abs(rate - 0.05) < 1e-9, f"expected the prospect-weighted 5%, got {rate!r}"
    page = _page(tmp_path)
    assert "(weighted by prospects)" in page
    assert "No single verdict" in page, (
        "the comparator paragraph still asserted one verdict about a blended number nobody "
        "set — it must list the campaigns' own goals instead"
    )


def test_one_shared_target_keeps_its_comparator_verdict(tmp_path):
    """The blend only appears when there is something to blend; agreement is not a blend."""
    _seed(tmp_path, second=BASE)
    fig = _figures(tmp_path)
    assert fig["target_rate"][0] == 0.02 and not fig["rates_differ"]
    page = _page(tmp_path)
    assert "(weighted by prospects)" not in page and "No single verdict" not in page


def test_a_partial_roster_refuses_rather_than_claiming_the_whole_segment(tmp_path):
    """Only one campaign declares where its accounts live, so the union covers part of the
    scope while the tiles say "the whole segment, not a slice".

    Reachable only on a SCOPED page — ``build_model`` sets no roster, so the profile-wide
    rollup never renders the block at all. That is why the guard lives at the tile and not
    at the model: the same union is honest for one campaign and dishonest for two.
    """
    _seed(tmp_path, second=BASE)  # BASE declares no roster_globs
    both = gd.render_html(
        gd.scope_to_campaign(gd.build_model("acme", tmp_path), "mine-20260904,other-20260718")
    )
    assert "Not shown for" in both and "declare where their accounts live" in both
    assert "the whole segment, not a slice" not in both
    assert "Every account in" not in both, "the who-tab table asserts completeness too"

    # The SAME roster, scoped to the one campaign that declares it, is honest and renders.
    alone = gd.render_html(gd.scope_to_campaign(gd.build_model("acme", tmp_path), "mine-20260904"))
    assert "the whole segment, not a slice" in alone


def test_agree_never_reads_a_silent_campaign_as_a_zero(tmp_path):
    """A campaign that declares nothing cannot disagree, and must not drag the figure down.
    This is the unit that every rule above is built on."""
    assert _agree([], "x") == (None, None)
    assert _agree([("a", 90)], "x") == (90, None)
    assert _agree([("a", 90), ("b", 90)], "x") == (90, None)
    value, why = _agree([("a", 90), ("b", 30)], "ceilings")
    assert value is None and "a: 90" in why and "b: 30" in why
