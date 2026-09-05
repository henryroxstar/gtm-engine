"""Suppression handling — the sidecar-JSON mechanism (`lint_suppressions`), never deck_lint's
inline `<!-- lint-ok -->` comment (an .mp4 has no comment syntax). Closes deck_lint's two known
gaps: a reason is required, and suppressions are always counted/reported, never silent.
"""

from __future__ import annotations

import pytest

from gtm_core import video_lint as vl


def _manifest_with_clipped_caption():
    return {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "box": {"x": 0, "y": 1700, "w": 1080, "h": 150}}],
    }


def test_a_valid_suppression_is_accepted_and_hides_the_matching_finding():
    findings = vl.evaluate(
        vl.Probe(1080, 1920, 30.0, 10.0, 8_000_000),
        ratio="9:16",
        manifest=_manifest_with_clipped_caption(),
    )
    findings = [
        vl.Finding(f.tier, f.rule, f.severity, "asset.mp4", f.excerpt, f.fix) for f in findings
    ]
    suppressions = vl._validate_suppressions(
        [
            {
                "tier": "V3",
                "asset": "asset.mp4",
                "reason": "logo lockup is intentionally bled to the edge",
            }
        ]
    )
    kept, counts = vl.apply_suppressions(findings, suppressions, asset="asset.mp4")
    assert kept == []
    assert counts == {"V3": 1}


def test_a_suppression_scoped_to_a_different_asset_does_not_apply():
    findings = vl.evaluate(
        vl.Probe(1080, 1920, 30.0, 10.0, 8_000_000),
        ratio="9:16",
        manifest=_manifest_with_clipped_caption(),
    )
    findings = [
        vl.Finding(f.tier, f.rule, f.severity, "asset.mp4", f.excerpt, f.fix) for f in findings
    ]
    suppressions = vl._validate_suppressions(
        [{"tier": "V3", "asset": "some-other-asset.mp4", "reason": "not the file in question here"}]
    )
    kept, counts = vl.apply_suppressions(findings, suppressions, asset="asset.mp4")
    assert len(kept) == 1
    assert counts == {}


@pytest.mark.parametrize("reason", ["", "n/a", "N/A", "ok", "tbd", "-", "short"])
def test_a_placeholder_or_short_reason_is_rejected(reason):
    with pytest.raises(vl.BadSuppression, match="no real reason"):
        vl._validate_suppressions([{"tier": "V3", "asset": "asset.mp4", "reason": reason}])


def test_an_unknown_tier_is_rejected():
    with pytest.raises(vl.BadSuppression, match="unknown tier"):
        vl._validate_suppressions(
            [{"tier": "V99", "asset": "asset.mp4", "reason": "a perfectly good reason here"}]
        )


@pytest.mark.parametrize("tier", ["V3 ", "v3", "V03", "V", "3", "V1a"])
def test_a_malformed_tier_id_is_rejected(tier):
    with pytest.raises(vl.BadSuppression):
        vl._validate_suppressions(
            [{"tier": tier, "asset": "asset.mp4", "reason": "a good reason indeed"}]
        )


def test_a_missing_asset_is_rejected():
    with pytest.raises(vl.BadSuppression, match="asset"):
        vl._validate_suppressions([{"tier": "V3", "reason": "a perfectly good reason here"}])


def test_zero_suppressions_are_still_counted_and_reported(capsys):
    vl.report("asset.mp4", [], {})
    out = capsys.readouterr().out
    assert "suppressed: 0" in out


def test_a_suppression_is_reported_with_its_per_tier_count(capsys):
    vl.report("asset.mp4", [], {"V3": 2, "V4": 1})
    out = capsys.readouterr().out
    assert "suppressed: 3" in out
    assert "V3:2" in out
    assert "V4:1" in out


# ── the promotion invariant, mechanical not editorial ───────────────────────────────────


def test_no_shipped_tier_is_error_with_fewer_than_two_evidence_entries():
    for tier in vl.SHIPPED:
        if tier.severity == vl.ERROR:
            assert len(tier.evidence) >= 2, tier.id


def test_constructing_an_error_tier_with_one_evidence_entry_raises():
    with pytest.raises(ValueError, match="evidence"):
        vl.Tier("V5", "a hypothetical candidate", vl.ERROR, evidence=("only one defect",))


def test_constructing_an_error_tier_with_zero_evidence_entries_raises():
    with pytest.raises(ValueError, match="evidence"):
        vl.Tier("V5", "a hypothetical candidate", vl.ERROR, evidence=())


def test_a_warn_tier_needs_no_evidence():
    vl.Tier("V5", "a hypothetical candidate", vl.WARN)  # must not raise


def test_the_shipped_tier_set_is_exactly_what_is_documented():
    """A closed set, not a floor. Adding a tier is a deliberate act with an evidence cost (see
    Tier.__post_init__), so it should also cost an edit here and in the module docstring."""
    assert {t.id for t in vl.SHIPPED} == {"V1", "V2", "V3", "V4", "V10", "V11"}


def test_every_shipped_tier_is_error_and_carries_two_pieces_of_evidence():
    """The promotion invariant, asserted from the outside as well as at import — a future edit
    that loosens __post_init__ would otherwise silently let a guess ship as an ERROR gate."""
    for tier in vl.SHIPPED:
        assert tier.severity == vl.ERROR, f"{tier.id} is in SHIPPED but is not an ERROR tier"
        assert len(tier.evidence) >= 2, f"{tier.id} ships as ERROR on {len(tier.evidence)} entries"


# ── missing ffprobe: exit 3, named, never a clean pass ──────────────────────────────────


def test_probe_raises_probe_unavailable_when_ffprobe_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty dir — ffprobe is not on it
    with pytest.raises(vl.ProbeUnavailable):
        vl.probe(tmp_path / "any.mp4")


def test_cli_exits_3_and_names_ffprobe_when_it_is_absent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    fake = tmp_path / "any.mp4"
    fake.write_bytes(b"not real media")
    rc = vl.main([str(fake), "--ratio", "9:16"])
    assert rc == 3
    assert "ffprobe" in capsys.readouterr().err
