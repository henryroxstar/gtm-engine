"""Contract: a tenant's targeting data has to agree with itself, and with the code that reads it.

Every failure this file pins was found on 2026-09-04 in one profile, and every one of them was
**silent**. None raised, none logged, none showed up in a report as an error — each degraded a
number instead. That is the shared shape and the reason these live together:

* A segment named in ``PROFILE.md`` ``segment_mix`` but absent from ``merge_hygiene.SEGMENTS``
  does not fail — ``clean_segment`` passes the raw string through, ``check_row`` files a *warning*,
  and ``hook_coverage`` books every row of that segment as *unassignable*. The tenant reads a
  coverage report showing 0% and concludes the copy is bad.
* A market in ``target_markets`` with no ``geo_bonus`` key does not fail — ``.get(market, 0)``
  scores it zero, so an entire market silently ranks below every other allowed one by the full
  spread of that table, and the backlog quietly never enriches it.
* A lower-weight cohort placed above a higher-weight one does not fail — first match wins outright,
  so the higher weight is simply unreachable for every account both cohorts match, and the reader
  who sees ``weight = 30`` two blocks down has no way to notice.
* A segment with a rubric but no hook-matrix grid does not fail — it produces outreach with no
  opener vocabulary, measured as an absence of hooks rather than an absence of a table.

The rule these share: **a targeting fact split across two files must be pinned by a third thing
that reads both.** Prose cross-references are what drifted; these assertions are what cannot.

Scoped to profiles that actually ship the file in question, so a profile mid-onboarding (or a
public OSS carve, where ``profiles/`` is absent entirely) yields nothing rather than failing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agent.profiles import list_profiles, read_profile_field
from gtm_core.email_compliance import normalize_market
from gtm_core.experiments import list_overlays
from gtm_core.hook_coverage.fit import _norm_segment
from gtm_core.hook_coverage.matrix import MatrixShape, parse_matrix
from gtm_core.merge_hygiene import SEGMENTS
from gtm_core.paths import resolve_knowledge_file

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "profiles"

#: Segments that name a *scoring* population, not a real one. ``unspecified`` is a kept value for a
#: row nobody classified; it is never selected into a run mix and never needs a hook grid.
_NON_SELECTABLE = frozenset({"unspecified"})


def _profiles() -> list[str]:
    return list_profiles(PROFILES)


def _segment_mix(profile: str) -> set[str]:
    """Segments a profile actually selects into a run, from ``PROFILE.md`` ``segment_mix``.

    Parsed from the shape the field is written in — ``50% startup / 30% enterprise / 20% builder``
    — rather than from a schema, because the field is prose the operator edits by hand. A token
    that is not a bare word after its percentage is ignored: this check exists to catch a *missing*
    segment, and inventing one from a malformed value would be worse than reading nothing.
    """
    raw = read_profile_field((PROFILES / profile / "PROFILE.md").read_text(), "segment_mix")
    if not raw:
        return set()
    out = set()
    for part in raw.split("/"):
        words = part.strip().split()
        if len(words) == 2 and words[0].rstrip("%").replace(".", "").isdigit():
            out.add(words[1].strip().lower())
    return out


def _rubric(profile: str, overlay: str | None = None) -> dict | None:
    """The rubric a run would actually score against, overlay included.

    Resolved rather than constructed since 2026-09-21. Reading
    ``<profile>/knowledge/icp-scoring.toml`` directly is what made this whole file blind to an
    experiment overlay: every assertion below would have passed while the file a run really
    used went unchecked, which is precisely the silent drift the module docstring is about —
    one layer up from where it was written.
    """
    path = resolve_knowledge_file(PROFILES, profile, "icp-scoring.toml", overlay=overlay)
    return tomllib.loads(path.read_text()) if path.is_file() else None


def _overlays() -> list[tuple[str, str]]:
    """Every (profile, overlay) committed to the tree."""
    return [(profile, slug) for profile in _profiles() for slug in list_overlays(profile, PROFILES)]


def cohort_inversions(rubric: dict) -> list[str]:
    """Cohorts whose weight is lower than the one below them, described.

    A pure function over the rubric so the rule can be exercised on both a good and a bad
    input. An assertion that only ever sees the real tree cannot be shown to discriminate:
    it passes today whether it checks the right thing or nothing at all (§R18).
    """
    cohorts = rubric.get("cohort", [])
    return [
        f"{a.get('name')} ({a.get('weight')}) is above {b.get('name')} ({b.get('weight')})"
        for a, b in zip(cohorts, cohorts[1:], strict=False)
        if int(a.get("weight", 0)) < int(b.get("weight", 0))
    ]


def missing_geo_keys(rubric: dict, markets: set[str]) -> list[str]:
    """Target markets with no ``[geo_bonus]`` key — scored 0, not left unranked."""
    have = {normalize_market(k) for k in rubric.get("geo_bonus", {})}
    return sorted(markets - have)


def _markets_of(profile: str) -> set[str]:
    raw = read_profile_field((PROFILES / profile / "PROFILE.md").read_text(), "target_markets")
    if not raw:
        return set()
    return {normalize_market(m) for m in raw.strip().strip("[]").split(",") if m.strip()}


# --- segment vocabulary ---------------------------------------------------------------


@pytest.mark.parametrize("profile", _profiles())
def test_every_selectable_segment_is_in_the_canonical_vocabulary(profile: str) -> None:
    """``segment_mix`` may not name a segment ``merge_hygiene.SEGMENTS`` does not know.

    The 2026-09-04 case: ``builder`` was added to the run mix, given its own gates, rubric,
    firmographic floor and cohort — and left out of this tuple. Every builder row would have
    normalised to itself, warned as ``segment-unknown``, and been counted as unassignable by the
    coverage audit. Nothing would have failed.
    """
    missing = sorted(_segment_mix(profile) - set(SEGMENTS))
    assert not missing, (
        f"{profile}/PROFILE.md `segment_mix` names segment(s) absent from "
        f"gtm_core.merge_hygiene.SEGMENTS: {missing}.\n"
        "Add them to that tuple — it is the vocabulary every downstream comparison normalises "
        "against, and an absent value degrades silently rather than failing."
    )


@pytest.mark.parametrize("profile", _profiles())
def test_every_selectable_segment_has_a_hook_matrix_grid(profile: str) -> None:
    """A segment in the run mix needs a segment in the hook matrix — *if* the matrix files by
    segment at all.

    The matrix's top axis is a tenant's own filing decision and only sometimes the ICP segment:
    other profiles in this repo file it by product line or by priority tier instead. Both are
    legitimate — a tenant selling one product to one segment gains nothing from a segment axis.
    So the check applies only when the tenant has *chosen* the segment vocabulary, evidenced by
    at least one section normalising into :data:`SEGMENTS`; then a missing one is a gap rather
    than a different convention.

    Only meaningful for a matrix that carries a persona x signal axis at all; a tenant whose file
    is ``UNSUPPORTED`` is reported by ``hook_coverage`` under its own name and is not this
    assertion's business.
    """
    path = PROFILES / profile / "knowledge" / "hook-matrix.md"
    if not path.is_file():
        pytest.skip(f"{profile} ships no hook-matrix.md")
    matrix = parse_matrix(path)
    if matrix.shape == MatrixShape.UNSUPPORTED:
        pytest.skip(f"{profile} hook-matrix.md carries no persona/signal axis")

    have = {_norm_segment(s) for s in matrix.segments}
    if not (have & set(SEGMENTS)):
        pytest.skip(f"{profile} hook-matrix.md files by something other than ICP segment: {have}")
    want = _segment_mix(profile) - _NON_SELECTABLE
    missing = sorted(want - have)
    assert not missing, (
        f"{profile}/knowledge/hook-matrix.md has no grid for selectable segment(s) {missing} "
        f"(grids present: {sorted(have)}).\n"
        "`gtm_core.hook_coverage` computes coverage from this grid, so a segment with hooks "
        "written anywhere else — an ICP doc, a campaign brief — is measured as having none."
    )


# --- scoring rubric -------------------------------------------------------------------


@pytest.mark.parametrize("profile", _profiles())
def test_cohorts_are_in_non_increasing_weight_order(profile: str) -> None:
    """First match wins outright, so file order is a scoring decision, not a filing decision.

    A cohort placed above a heavier one caps every account both match at the lighter weight, and
    the file reads as though the heavier one applies. Found 2026-09-04: ``agent-factory-sme``
    (30) sat below ``compliance-provenance`` (20), under a comment asserting the opposite.
    """
    rubric = _rubric(profile)
    if rubric is None:
        pytest.skip(f"{profile} ships no icp-scoring.toml")
    cohorts = rubric.get("cohort", [])
    pairs = list(zip(cohorts, cohorts[1:], strict=False))
    inversions = [
        f"{a.get('name')} ({a.get('weight')}) is above {b.get('name')} ({b.get('weight')})"
        for a, b in pairs
        if int(a.get("weight", 0)) < int(b.get("weight", 0))
    ]
    assert not inversions, (
        f"{profile}/knowledge/icp-scoring.toml cohorts are not in non-increasing weight order:\n  "
        + "\n  ".join(inversions)
        + "\n\nThe first matching cohort wins outright, so the heavier weight below is unreachable "
        "for every account both cohorts match. Reorder, or merge the two."
    )


@pytest.mark.parametrize("profile", _profiles())
def test_every_target_market_has_a_geo_bonus_key(profile: str) -> None:
    """A market you are allowed to email needs an ordering weight, or it sorts last.

    ``geo_bonus`` orders *within* the allowed set; it is not the gate. But a missing key is not
    "no opinion" — it is a zero, and therefore a demotion by the full spread of the table. India
    was a target market for 24 days with no key here. It cost nothing only because the backlog
    happened to hold no India rows.
    """
    rubric = _rubric(profile)
    if rubric is None:
        pytest.skip(f"{profile} ships no icp-scoring.toml")
    raw = read_profile_field((PROFILES / profile / "PROFILE.md").read_text(), "target_markets")
    if not raw:
        pytest.skip(f"{profile} declares no target_markets")

    markets = {normalize_market(m) for m in raw.strip("[]").split(",") if m.strip()}
    have = {normalize_market(k) for k in rubric.get("geo_bonus", {})}
    missing = sorted(markets - have)
    assert not missing, (
        f"{profile}/knowledge/icp-scoring.toml `[geo_bonus]` has no key for target market(s) "
        f"{missing}.\nA missing key scores 0, which ranks that market below every other allowed "
        "one. Add an explicit weight even if it is deliberately the lowest."
    )


# --- the same invariants, against an experiment overlay --------------------------------
#
# An overlay may replace `icp-scoring.toml` and `hook-matrix.md` wholesale, so every property
# above can be broken by one without touching a single file this module used to read. These
# re-run the two that are pure functions of the rubric against the MERGED result.
#
# Scoped to (profile, overlay) pairs found on disk, so a tree with no experiments yields
# nothing rather than failing — the same posture as every other check here.


@pytest.mark.parametrize("profile,overlay", _overlays())
def test_overlay_cohorts_are_in_non_increasing_weight_order(profile: str, overlay: str) -> None:
    """An overlay's rubric obeys the first-match-wins ordering rule, or its heavier cohorts
    are unreachable exactly as they would be in the tenant's own file.

    Worth stating plainly: an experiment is the MOST likely place for this defect, because a
    new cohort is usually appended to the bottom of a copied file, and appending is how a
    weight-30 cohort ends up below a weight-20 one.
    """
    rubric = _rubric(profile, overlay)
    if rubric is None:
        pytest.skip(f"{profile}/{overlay} ships no icp-scoring.toml")
    cohorts = rubric.get("cohort", [])
    inversions = [
        f"{a.get('name')} ({a.get('weight')}) is above {b.get('name')} ({b.get('weight')})"
        for a, b in zip(cohorts, cohorts[1:], strict=False)
        if int(a.get("weight", 0)) < int(b.get("weight", 0))
    ]
    assert not inversions, (
        f"profiles/{profile}/experiments/{overlay}/icp-scoring.toml cohorts are not in "
        f"non-increasing weight order:\n  " + "\n  ".join(inversions)
    )


@pytest.mark.parametrize("profile,overlay", _overlays())
def test_overlay_geo_bonus_covers_every_target_market(profile: str, overlay: str) -> None:
    """`target_markets` is NOT overlayable, so an overlay rubric still has to cover the
    profile's markets.

    A market with no `geo_bonus` key is not neutral — `.get(market, 0)` scores it zero, so an
    entire market ranks below every other allowed one by the full spread of the table and the
    backlog quietly never enriches it. An overlay copied from an older rubric is the easiest
    way to reintroduce that, because the market it forgets is the one added most recently.
    """
    rubric = _rubric(profile, overlay)
    if rubric is None or "geo_bonus" not in rubric:
        pytest.skip(f"{profile}/{overlay} declares no geo_bonus")
    raw = read_profile_field((PROFILES / profile / "PROFILE.md").read_text(), "target_markets")
    if not raw:
        pytest.skip(f"{profile} declares no target_markets")
    markets = {normalize_market(m) for m in raw.strip().strip("[]").split(",") if m.strip()}
    have = {normalize_market(k) for k in rubric["geo_bonus"]}
    missing = sorted(markets - have)
    assert not missing, (
        f"profiles/{profile}/experiments/{overlay}/icp-scoring.toml `[geo_bonus]` has no key "
        f"for target market(s) {missing}. A missing key scores 0, which demotes the whole "
        f"market rather than leaving it unranked."
    )


# --- do these checks discriminate? -------------------------------------------------------
#
# Every assertion above currently passes. That is exactly why these exist: a check that has
# only ever seen a clean tree cannot be distinguished from a check that never fires. Each
# pair below feeds the helper one input it must reject and one it must accept, differing
# only in the field under test.


def test_cohort_order_check_rejects_an_inversion_and_accepts_the_fix() -> None:
    bad = {"cohort": [{"name": "light", "weight": 10}, {"name": "heavy", "weight": 30}]}
    good = {"cohort": [{"name": "heavy", "weight": 30}, {"name": "light", "weight": 10}]}
    assert cohort_inversions(bad) == ["light (10) is above heavy (30)"]
    assert cohort_inversions(good) == []
    # Equal weights are legal: first match wins, and the tie costs only the cohort LABEL.
    tie = {"cohort": [{"name": "a", "weight": 30}, {"name": "b", "weight": 30}]}
    assert cohort_inversions(tie) == []


def test_geo_bonus_check_rejects_a_missing_market_and_accepts_the_fix() -> None:
    markets = {normalize_market("United States"), normalize_market("Singapore")}
    assert missing_geo_keys({"geo_bonus": {"united states": 5}}, markets) == ["singapore"]
    assert missing_geo_keys({"geo_bonus": {"united states": 5, "singapore": 4}}, markets) == []
