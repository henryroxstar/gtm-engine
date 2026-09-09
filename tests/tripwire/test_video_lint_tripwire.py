"""Tripwire corpus for ``gtm_core.video_lint`` — every rule family fires, at its severity, or CI is red.

Read ``tests/tripwire/README.md`` first. ``evaluate()`` is pure, so each fixture is a fabricated
ffprobe payload (run through the real ``Probe.from_ffprobe_json``) plus the context the CLI would
assemble. The CLI goldens put a fake ``ffprobe`` on PATH, so ``probe()`` itself is exercised
without media or ffmpeg on the machine.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import video_lint as vl
from tests.tripwire import support

CORPUS = support.HERE / "video_lint"

Triple = tuple[str, str, str]

#: Every (tier, rule, severity) ``evaluate()`` can emit. ``V1 fps`` and ``V3 caption_over_face``
#: carry two severities (fps band; rendered identity on screen), so both appear.
INVENTORY: frozenset[Triple] = frozenset(
    {
        ("V1", "resolution", "error"),
        ("V1", "fps", "error"),
        ("V1", "fps", "warn"),
        ("V1", "bitrate", "error"),
        ("V2", "aspect_exact", "error"),
        ("V3", "stale_sidecar", "error"),
        ("V3", "caption_outside_safe_area", "error"),
        ("V3", "caption_over_face", "error"),
        ("V3", "caption_over_face", "warn"),
        ("V3", "edge_luma_fallback", "warn"),
        ("V4", "predictor_duration_over_cap", "error"),
        ("V5", "music_bed_without_ducking", "warn"),
        ("V5", "music_bed_too_loud", "warn"),
        ("V6", "no_scene_changes", "warn"),
        ("V6", "scene_changes_too_frequent", "warn"),
        ("V6", "scene_changes_too_sparse", "warn"),
        ("V7", "caption_screen_too_dense", "warn"),
        ("V7", "caption_load_too_high", "warn"),
        ("V8", "caption_diverges_from_voice", "warn"),
        ("V9", "static_shots", "warn"),
        ("V10", "no_audio_stream", "error"),
        ("V10", "dead_air_fraction", "error"),
        ("V10", "dead_air_run", "error"),
        ("V11", "caption_contrast", "error"),
        ("V12", "transition_density_high", "warn"),
        ("V13", "longest_hold_exceeds_cadence", "warn"),
    }
)

#: (fixture, expected ordered findings) — order is ``evaluate()``'s tier order.
RUNS: list[tuple[str, list[Triple]]] = [
    ("clean.json", []),
    (
        "delivery.json",
        [
            ("V1", "fps", "error"),
            ("V1", "bitrate", "error"),
            ("V2", "aspect_exact", "error"),
        ],
    ),
    (
        "lowres-silent.json",
        [
            ("V1", "resolution", "error"),
            ("V1", "fps", "warn"),
            ("V10", "no_audio_stream", "error"),
        ],
    ),
    (
        "captions-identity.json",
        [
            ("V3", "caption_outside_safe_area", "error"),
            ("V3", "caption_over_face", "error"),
            ("V7", "caption_screen_too_dense", "warn"),
            ("V7", "caption_load_too_high", "warn"),
            ("V8", "caption_diverges_from_voice", "warn"),
            ("V11", "caption_contrast", "error"),
        ],
    ),
    ("captions-noidentity.json", [("V3", "caption_over_face", "warn")]),
    ("stale-sidecar.json", [("V3", "stale_sidecar", "error")]),
    (
        "edge-predictor.json",
        [
            ("V3", "edge_luma_fallback", "warn"),
            ("V4", "predictor_duration_over_cap", "error"),
            ("V5", "music_bed_without_ducking", "warn"),
            ("V6", "no_scene_changes", "warn"),
            ("V9", "static_shots", "warn"),
        ],
    ),
    (
        "dead-air.json",
        [
            ("V5", "music_bed_too_loud", "warn"),
            ("V6", "scene_changes_too_frequent", "warn"),
            ("V6", "scene_changes_too_sparse", "warn"),
            # No V13 here: this fixture carries no motion_stats, and a hold is NO CUT *and* NO
            # MOTION — an 89s tail without motion data is unmeasured, and unmeasured is a skip,
            # never a finding. `long-hold.json` is the V13 fixture; its block is static by design.
            ("V10", "dead_air_fraction", "error"),
            ("V10", "dead_air_run", "error"),
        ],
    ),
    # C6 — the two rhythm tiers V6 cannot see. `long-hold` has a HEALTHY average (six changes in
    # 20s) and one 7s block, which is exactly the case an average hides. `transitions-heavy` also
    # trips V6's frequency rule, honestly so: a blend IS a boundary, so eight of them in twelve
    # seconds is both dense in blends and frequent in cuts. The tiers describe different
    # properties of one edit rather than substituting for each other.
    # The 7s block is static — which is what makes it a hold — so V9 sees it too. Two tiers,
    # two different reasons: V13 says the picture went unchanged too long, V9 says a shot has
    # no internal motion. Both are true of the same seconds.
    (
        "long-hold.json",
        [("V13", "longest_hold_exceeds_cadence", "warn"), ("V9", "static_shots", "warn")],
    ),
    (
        "transitions-heavy.json",
        [
            ("V6", "scene_changes_too_frequent", "warn"),
            ("V12", "transition_density_high", "warn"),
        ],
    ),
]


def triples(findings: list[vl.Finding]) -> list[Triple]:
    return [(f.tier, f.rule, f.severity) for f in findings]


def _run(fixture: str) -> list[Triple]:
    doc = json.loads((CORPUS / fixture).read_text(encoding="utf-8"))
    probe = vl.Probe.from_ffprobe_json(doc["ffprobe"])
    return triples(vl.evaluate(probe, ratio=doc["ratio"], **doc["context"]))


@pytest.mark.parametrize(("fixture", "expected"), RUNS, ids=[r[0] for r in RUNS])
def test_fixture_fires_exactly(fixture: str, expected: list[Triple]) -> None:
    assert _run(fixture) == expected


def test_clean_fixture_fires_nothing() -> None:
    assert _run("clean.json") == []


def test_every_rule_family_is_tripped_by_the_corpus() -> None:
    """Set EQUALITY: a family that stops firing is named, and a new family must join the corpus."""
    fired: set[Triple] = set()
    for fixture, _ in RUNS:
        fired.update(_run(fixture))
    assert fired == INVENTORY


def test_tier_registry_matches_the_inventory() -> None:
    """The one linter with a real registry: SHIPPED + CANDIDATES, keyed by id. Every registered
    tier must be reachable from the corpus and every fired tier must be registered."""
    assert tuple(t.id for t in vl.SHIPPED) == ("V1", "V2", "V3", "V4", "V10", "V11")
    assert tuple(t.id for t in vl.CANDIDATES) == ("V5", "V6", "V7", "V8", "V9", "V12", "V13")
    assert all(t.severity == vl.ERROR for t in vl.SHIPPED)
    assert all(t.severity == vl.WARN for t in vl.CANDIDATES)
    assert set(vl._TIERS_BY_ID) == {tier for tier, _, _ in INVENTORY}
    assert set(vl.SAFE_AREAS) == set(vl.FACE_BANDS) == {"9:16", "4:5", "1:1", "16:9"}


def test_suppression_validation_is_still_strict() -> None:
    """A reason-less or whitespace-padded suppression is malformed input, never a quiet pass."""
    good = [{"tier": "V5", "asset": "a.mp4", "reason": "the bed is a deliberate wash"}]
    assert [s.tier for s in vl._validate_suppressions(good)] == ["V5"]
    for bad in (
        {"tier": "V3 ", "asset": "a.mp4", "reason": "padded tier id is malformed"},
        {"tier": "V99", "asset": "a.mp4", "reason": "unknown tier is malformed"},
        {"tier": "V3", "asset": "", "reason": "must be scoped to one asset"},
        {"tier": "V3", "asset": "a.mp4", "reason": "tbd"},
    ):
        with pytest.raises(vl.BadSuppression):
            vl._validate_suppressions([bad])


def test_help_golden() -> None:
    support.check_help("video_lint")


@pytest.mark.parametrize("case", support.CASES["video_lint"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
