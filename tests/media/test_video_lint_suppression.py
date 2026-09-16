"""Suppression handling — the sidecar-JSON mechanism (`lint_suppressions`), never deck_lint's
inline `<!-- lint-ok -->` comment (an .mp4 has no comment syntax). Closes deck_lint's two known
gaps: a reason is required, and suppressions are always counted/reported, never silent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_lint as vl

FFMPEG = shutil.which("ffmpeg")


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


# ── #1: stale_suppressions — pure ────────────────────────────────────────────────────────────


def test_stale_suppressions_flags_a_tier_that_ran_and_matched_nothing():
    supp = vl.Suppression(tier="V9", asset="cut.mp4", reason="static intro is intentional here")
    assert vl.stale_suppressions([supp], {"V9": 0}, asset="cut.mp4") == [supp]


def test_stale_suppressions_does_not_flag_a_tier_that_matched_something():
    supp = vl.Suppression(tier="V9", asset="cut.mp4", reason="static intro is intentional here")
    assert vl.stale_suppressions([supp], {"V9": 1}, asset="cut.mp4") == []


def test_stale_suppressions_does_not_flag_a_tier_absent_from_counts():
    """Absent means 'did not run this pass' (--fast, or V11 with no captions payload) — calling
    it stale would be a false accusation, not a finding."""
    supp = vl.Suppression(tier="V11", asset="cut.mp4", reason="contrast checked by hand here")
    assert vl.stale_suppressions([supp], {}, asset="cut.mp4") == []


def test_stale_suppressions_ignores_a_suppression_for_a_different_asset():
    supp = vl.Suppression(tier="V9", asset="other.mp4", reason="static intro is intentional here")
    assert vl.stale_suppressions([supp], {"V9": 0}, asset="cut.mp4") == []


def test_stale_suppressions_normalizes_asset_whitespace_like_validate_suppressions_does():
    supp = vl.Suppression(tier="V9", asset=" cut.mp4 ", reason="static intro is intentional here")
    assert vl.stale_suppressions([supp], {"V9": 0}, asset="cut.mp4") == [supp]


def test_stale_suppressions_handles_several_suppressions_independently():
    stale = vl.Suppression(tier="V9", asset="cut.mp4", reason="static intro is intentional here")
    live = vl.Suppression(tier="V1", asset="cut.mp4", reason="resolution is intentionally low")
    other_asset = vl.Suppression(tier="V5", asset="other.mp4", reason="not this asset at all")
    result = vl.stale_suppressions(
        [stale, live, other_asset], {"V9": 0, "V1": 1, "V5": 0}, asset="cut.mp4"
    )
    assert result == [stale]


# ── #1: stale_suppressions — the text report line ────────────────────────────────────────────


def test_report_prints_a_stale_suppression_line():
    import io
    from contextlib import redirect_stdout

    supp = vl.Suppression(tier="V9", asset="cut.mp4", reason="static intro is intentional here")
    buf = io.StringIO()
    with redirect_stdout(buf):
        vl.report("cut.mp4", [], {}, stale=[supp])
    out = buf.getvalue()
    assert "stale suppression: V9 matched nothing this run" in out
    assert "hides the next real V9" in out


def test_report_prints_nothing_extra_when_stale_is_none_or_empty():
    import io
    from contextlib import redirect_stdout

    for stale in (None, []):
        buf = io.StringIO()
        with redirect_stdout(buf):
            vl.report("cut.mp4", [], {}, stale=stale)
        assert "stale suppression" not in buf.getvalue()


# ── #1: stale_suppressions — through the CLI ─────────────────────────────────────────────────


def _clip(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_cli_reports_a_stale_suppression_for_a_tier_that_ran_and_matched_nothing(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(
        json.dumps(
            {
                "lint_suppressions": [
                    {"tier": "V5", "asset": "cut.mp4", "reason": "no bed declared on purpose here"}
                ]
            }
        ),
        encoding="utf-8",
    )
    vl.main([str(clip), "--ratio", "9:16", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    assert out["stale_suppressions"] == [
        {"tier": "V5", "asset": "cut.mp4", "reason": "no bed declared on purpose here"}
    ]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_cli_does_not_report_a_suppression_that_matched_a_real_finding(tmp_path, capsys):
    """320x240 is under the 9:16 floor, so V1 'resolution' always fires — a suppression for it
    has genuinely matched something and must never read as safe to delete."""
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(
        json.dumps(
            {
                "lint_suppressions": [
                    {"tier": "V1", "asset": "cut.mp4", "reason": "resolution is intentionally low"}
                ]
            }
        ),
        encoding="utf-8",
    )
    vl.main([str(clip), "--ratio", "9:16", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    assert out["stale_suppressions"] == []
    assert out["suppressed"] == {"V1": 1}


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_cli_fast_mode_reports_no_stale_suppressions_for_fast_skipped_tiers(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(
        json.dumps(
            {
                "lint_suppressions": [
                    {"tier": "V9", "asset": "cut.mp4", "reason": "static intro shot is intentional"}
                ]
            }
        ),
        encoding="utf-8",
    )
    vl.main([str(clip), "--ratio", "9:16", "--fast", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    assert out["stale_suppressions"] == []


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_cli_does_not_flag_v11_stale_when_the_manifest_has_no_captions_payload(tmp_path, capsys):
    """V11's contrast measurement needs a captions payload to sample; without one it never ran
    this pass, and a suppression recorded for it must not read as safe to delete."""
    clip = _clip(tmp_path / "cut.mp4")
    manifest = tmp_path / "finish-9x16.json"
    manifest.write_text(
        json.dumps(
            {
                "captions": None,
                "lint_suppressions": [
                    {"tier": "V11", "asset": "cut.mp4", "reason": "contrast checked by hand here"}
                ],
            }
        ),
        encoding="utf-8",
    )
    vl.main([str(clip), "--ratio", "9:16", "--json", "--manifest", str(manifest)])
    out = json.loads(capsys.readouterr().out)
    assert out["stale_suppressions"] == []


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_cli_exit_code_is_unaffected_by_stale_reporting(tmp_path, capsys):
    clip = _clip(tmp_path / "cut.mp4")
    manifest_with = tmp_path / "finish-with.json"
    manifest_with.write_text(
        json.dumps(
            {"lint_suppressions": [{"tier": "V5", "asset": "cut.mp4", "reason": "no bed at all"}]}
        ),
        encoding="utf-8",
    )
    manifest_without = tmp_path / "finish-without.json"
    manifest_without.write_text(json.dumps({}), encoding="utf-8")
    code_with = vl.main([str(clip), "--ratio", "9:16", "--json", "--manifest", str(manifest_with)])
    code_without = vl.main(
        [str(clip), "--ratio", "9:16", "--json", "--manifest", str(manifest_without)]
    )
    assert code_with == code_without
