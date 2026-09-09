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
from gtm_core.hook_coverage.fit import _norm_segment
from gtm_core.hook_coverage.matrix import MatrixShape, parse_matrix
from gtm_core.merge_hygiene import SEGMENTS

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


def _rubric(profile: str) -> dict | None:
    path = PROFILES / profile / "knowledge" / "icp-scoring.toml"
    return tomllib.loads(path.read_text()) if path.is_file() else None


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
