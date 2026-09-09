"""A coarse cost figure at routing time, so a lane is not chosen blind to spend.

This is the change that most directly answers the loudest pain in the market scan: credit burn on
generations the buyer did not know they were authorising. The operator picks a lane from a menu;
until now that menu said what each lane produces and nothing about what it costs.

Two properties matter more than the number itself:

* **`None` beats a guess.** Where no rate is derivable the field is `None` and the menu omits it.
  A coarse figure read as a quote is the failure mode — an operator who is told "≈23 credits" and
  spends 200 trusts the next estimate less than one who was told nothing.
* **It costs nothing to produce.** Every input is a module constant or the lane's own shape. The
  preflight runs on every routing decision and must stay free; a cost estimate that itself
  required a provider call would be self-defeating.

Where a published rate and a ledger-measured rate disagree, **the ledger wins** — it prices the
surface we actually bill through. HeyGen's API docs quote 0.1 credits/sec (6/min); this tenant's
own ledger measured ~23/min on the MCP plan. Different billing planes, and only one of them sends
us an invoice.

Design: the 2026-08-29 video-router hardening note, item C5 (rates from C13/P4, P5).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import video_preflight as vp


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    """A profile with every handle present, so no lane is blocked for an unrelated reason."""
    root = tmp_path / "profiles"
    prof = root / "acme"
    (prof / "knowledge").mkdir(parents=True)
    (prof / "packs.toml").write_text('active = ["creator"]\n', encoding="utf-8")
    (prof / "knowledge" / "BRAND.toml").write_text(
        "\n".join(
            [
                "[disclosure]",
                'line = "Made with AI."',
                "[identity]",
                'voice_id = "voice-abc"',
                'heygen_avatar_id = "avatar-abc"',
                'heygen_voice_grade = "professional"',
                'restyle_preset_id = "restyle-abc"',
            ]
        ),
        encoding="utf-8",
    )
    return root


def _lane(pf: vp.Preflight, variant: str) -> vp.LaneStatus:
    return next(lane for lane in pf.lanes if lane.variant == variant)


def test_a_lane_with_a_known_rate_reports_an_estimate(profiles: Path):
    """`presenter-video` renders on HeyGen, whose per-second rate this tenant has measured from
    its own ledger four times over (see `gtm_core.heygen_cost`)."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = _lane(pf, "presenter-video")
    assert lane.estimated_credits is not None
    assert lane.estimated_credits > 0


def test_a_lane_with_no_derivable_rate_reports_none(profiles: Path):
    """`None`, never `0`. A zero reads as free, which is the most expensive possible lie about a
    lane that generates video."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = _lane(pf, "short-form-video")
    assert lane.estimated_credits is None


def test_the_estimate_is_derived_from_the_published_rate_constant(profiles: Path):
    """Pinned to the constant, so re-measuring the rate updates the estimate rather than leaving
    a literal behind — the same derive-don't-restate rule as C3."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = _lane(pf, "presenter-video")
    assert lane.estimated_credits == vp.HEYGEN_CREDITS_PER_MINUTE * vp.ESTIMATE_BASIS_MINUTES


def test_the_menu_omits_the_figure_when_it_is_none(profiles: Path, capsys):
    """No "None credits" in operator-facing text."""
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out
    assert "None" not in out, out


def test_the_menu_shows_the_figure_when_it_is_known(profiles: Path, capsys):
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out
    assert str(vp.HEYGEN_CREDITS_PER_MINUTE) in out, out


def test_the_estimate_is_labelled_as_an_estimate(profiles: Path, capsys):
    """A coarse figure presented as a quote is worse than no figure. The hedge is load-bearing."""
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out.lower()
    assert "~" in out or "approx" in out or "est" in out, out


def test_the_estimate_costs_no_provider_call(profiles: Path, monkeypatch):
    """Pure. If this ever needs the network, it does not belong in a preflight that runs on
    every routing decision."""
    import urllib.request

    def _boom(*args, **kwargs):  # pragma: no cover - the point is that it never runs
        raise AssertionError("the cost estimate made a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    pf = vp.preflight("acme", profiles_root=profiles)
    assert _lane(pf, "presenter-video").estimated_credits is not None


def test_the_json_output_carries_the_estimate(profiles: Path, capsys):
    vp.main(["--profile", "acme", "--profiles-root", str(profiles), "--json"])
    payload = json.loads(capsys.readouterr().out)
    lanes = {lane["variant"]: lane for lane in payload["lanes"]}
    assert lanes["presenter-video"]["estimated_credits"] is not None
    assert lanes["short-form-video"]["estimated_credits"] is None


# --- Reap plan facts (C13) ---------------------------------------------------------------------
#
# `get_plan_usage` returns four operationally load-bearing fields the pipeline fetched and then
# ignored. Two of them bound behaviour rather than merely informing it: `maxConcurrentProjects`
# caps any Reap fan-out, and `projectRetentionDays` is a purge window a parked run can outlive.


def test_reap_lanes_carry_the_published_rate_card(profiles: Path):
    """The clip lanes bill through Reap, whose per-billed-minute rate card is published on the
    tool itself — so their estimate is derivable for free, exactly like HeyGen's."""
    pf = vp.preflight("acme", profiles_root=profiles)
    for variant in ("repurpose-clips", "demo-clips"):
        lane = _lane(pf, variant)
        assert lane.estimated_credits is not None, f"{variant} reports no estimate"
        assert lane.estimated_credits == (
            vp.REAP_CLIPPING_CREDITS_PER_MINUTE * vp.ESTIMATE_BASIS_MINUTES
        )


def test_a_higgsfield_priced_lane_still_reports_no_estimate(profiles: Path):
    """Positive control on the None path: `restyle-shorts` runs on Higgsfield's shorts studio,
    which prices per call via `get_cost` — a provider round-trip the preflight will not make."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert _lane(pf, "restyle-shorts").estimated_credits is None


def test_the_reap_estimate_is_far_cheaper_than_the_heygen_one(profiles: Path):
    """The comparison is the point. A menu that prices a clip lane beside a synthetic-presenter
    lane makes the cost difference a visible input to the choice rather than a surprise."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert (
        _lane(pf, "repurpose-clips").estimated_credits
        < _lane(pf, "presenter-video").estimated_credits
    )


def test_the_concurrency_ceiling_is_reported(profiles: Path):
    """Surfaced for fan-out planning: a batch wider than this queues rather than failing, and
    C6's batch answer has to respect it."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.reap_max_concurrent_projects == 3


def test_the_footage_retention_window_is_reported(profiles: Path):
    """The live hazard for a run parked at a capture gate: footage the provider has purged."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.reap_project_retention_days == 60


def test_the_plan_facts_carry_the_date_they_were_probed(profiles: Path):
    """A plan-tier fact is stable but not eternal. Recording WHEN it was read is what separates
    a re-verifiable constant from the snapshot class C3 exists to stamp out."""
    assert vp.REAP_PLAN_VERIFIED_ON, "no probe date recorded for the Reap plan facts"


def test_no_credit_balance_is_baked_into_the_module():
    """A BALANCE is never a constant — it changes on every call and the live read is free. Rates
    and plan limits are; 600/599/60 are not."""
    import inspect

    source = inspect.getsource(vp)
    for balance in ("599", "600"):
        assert balance not in source, (
            f"{balance!r} looks like a baked-in credit balance. Rates and plan ceilings may be "
            "constants; a balance must be read live."
        )


# --- C11c: engine-probe staleness is SURFACED, never enforced -----------------------------------
#
# A capability ban outlives the probe that justified it (P2). The registry now dates each engine;
# the preflight reports the date a lane's render role depends on. It is a prompt to run
# `render_engines --audit`, not a reason to withhold a lane — a stale probe does not make a render
# wrong, it makes the claim behind it unverified, and those are different things.


def test_preflight_reports_the_oldest_engine_verification_date(profiles: Path):
    """The date reaches the operator instead of sitting unread in the registry."""
    from gtm_core import render_engines

    pf = vp.preflight("acme", profiles_root=profiles)
    lane = _lane(pf, "short-form-video")
    assert lane.engine_verified_on, (
        "a lane that resolves a render engine reports no probe date, so its capability claims "
        "still have no expiry the operator can see"
    )
    assert lane.engine_verified_on == render_engines.oldest_verification("broll")

    # A lane that renders nothing has no engine claim to date, and must not invent one.
    assert _lane(pf, "live-action-video").engine_verified_on == "", (
        "the live-action lane reports an engine probe date, but it resolves no render engine at "
        "all — the engine is a camera and a human"
    )


def test_a_stale_engine_date_is_a_caveat_not_a_block(profiles: Path, tmp_path: Path, monkeypatch):
    """The decision-not-gate property, checked against a registry probed long ago."""
    from gtm_core import render_engines

    registry = tmp_path / "aged_registry.toml"
    registry.write_text(
        """
[meta]
version = 1

[engines.ancient]
provider = "higgsfield"
model = "wan2_7"
output = "video"
identity_faithful = false
lip_sync = "none"
verified_on = "2020-01-01"

[roles.broll]
engine = "ancient"
requires = { output = "video" }
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(render_engines.ENV_OVERRIDE, str(registry))

    lane = _lane(vp.preflight("acme", profiles_root=profiles), "short-form-video")
    assert lane.ready, "a stale probe blocked the lane; staleness is a decision, not a gate"
    assert lane.engine, "the engine still resolved"
    stale = [c for c in lane.caveats if "verified" in c.lower()]
    assert stale, (
        f"no staleness caveat was raised for a 2020 probe date; caveats were {lane.caveats}"
    )
    assert "--audit" in stale[0], (
        "the caveat does not name the command that answers it — a staleness report with no next "
        "step is noise the operator learns to skip"
    )


# --- V-1: the HeyGen rate is a BAND, not a figure ------------------------------------------------
#
# Measured 2026-08-29: 11 credits for `avatar_iv` at 720p/16:9, against ~23 for the 2026-08-27
# `avatar_v` + `avatar_iii` batch. Cost moves with engine and resolution — neither of which the
# router knows at routing time. Reporting one end as a point estimate over- or under-states the
# lane by 2x, which is precisely how a coarse figure starts reading as a quote.


def test_a_lane_whose_rate_varies_reports_a_band(profiles: Path):
    lane = _lane(vp.preflight("acme", profiles_root=profiles), "presenter-video")
    assert lane.estimated_credits_min is not None, (
        "presenter-video reports no low end, so its 2x spread is invisible"
    )
    assert lane.estimated_credits_min < lane.estimated_credits


def test_a_lane_with_a_single_published_rate_reports_no_band(profiles: Path):
    """Positive control: Reap publishes one per-minute rate that does not vary, so inventing a
    band there would be noise dressed as precision."""
    lane = _lane(vp.preflight("acme", profiles_root=profiles), "repurpose-clips")
    assert lane.estimated_credits is not None
    assert lane.estimated_credits_min is None


def test_the_menu_shows_the_band_as_a_band(profiles: Path, capsys):
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out
    assert f"{vp.HEYGEN_CREDITS_PER_MINUTE_MIN}-{vp.HEYGEN_CREDITS_PER_MINUTE}" in out, out
    assert "varies by engine" in out, (
        "the band is shown without saying WHAT varies, so the operator cannot act on it"
    )
