"""`gtm_core.shots_coverage` — every declared field needs a consumer at its stage.

Fixtures are fictional. The shape under test is the one that shipped once: a list whose shots
declare bubbles, cues, a bed and an end frame, and a run folder in which none of those were built.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.shots_coverage import check_coverage, main

# Three shots on purpose: one with an `id`, one falling back to the positional `shot-01`, one
# that declares nothing audible so it must never produce a finding.
SHOTS = {
    "source_item": "ci-fixture-01",
    "total_duration_s": 9.0,
    "style_scaffold": {"look": "flat studio light", "provider_model": "fixture_v1"},
    "shots": [
        {
            "id": "s01",
            "n": 1,
            "duration_s": 3.0,
            "role": "broll",
            "camera": "static",
            "motion_prompt": "she taps the card twice",
            "visual": "a desk",
            "sfx": "two card taps",
            "audio_bed": "room tone and a low note",
            "caption_text_override": "Two taps.",
            "production": {"overlay": {"kind": "outgoing bubble", "text": "hello"}},
        },
        {
            "n": 2,
            "duration_s": 3.0,
            "role": "broll",
            "camera": "slow push",
            "motion_prompt": "settling",
            "visual": "a window",
            "end_frame": "stills/s02-end.png",
            "sfx": "a latch",
        },
        {
            "id": "s03",
            "n": 3,
            "duration_s": 3.0,
            "role": "broll",
            "camera": "static",
            "motion_prompt": "the light shifts",
            "visual": "a wall",
            "sfx": "none",
            "audio_bed": "silent — by design",
        },
    ],
}


@pytest.fixture
def shots_path(tmp_path: Path) -> Path:
    p = tmp_path / "fixture.shots.json"
    p.write_text(json.dumps(SHOTS), encoding="utf-8")
    return p


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    return d


def _fields(report, *, missing: bool = True) -> set[tuple[str, str]]:
    rows = report.missing if missing else report.ok
    return {(f.shot_id, f.field) for f in rows}


def _build_consumers(run_dir: Path) -> None:
    """Every consumer the fixture's declarations need, on disk."""
    sfx = run_dir / "sfx"
    sfx.mkdir()
    (sfx / "taps.wav").write_bytes(b"x")
    (sfx / "latch.wav").write_bytes(b"x")
    (sfx / "tone.wav").write_bytes(b"x")
    (sfx / "cues.json").write_text(
        json.dumps(
            {
                "bed": None,
                "room_tone": {"path": "tone.wav"},
                "cues": [
                    {"shot_id": "s01", "label": "taps", "path": "taps.wav", "at_s": 0.1},
                    {"shot_id": "shot-01", "label": "latch", "path": "latch.wav", "at_s": 0.5},
                ],
            }
        )
    )
    (run_dir / "render-9x16.json").write_text(
        json.dumps({"shots": [{"n": "1"}, {"n": "2", "end_image_job_id": "job-end-2"}]})
    )
    (run_dir / "shots-overlaid").mkdir()
    (run_dir / "shots-overlaid" / "s01-overlaid.overlays.json").write_text(
        json.dumps({"frame": [1080, 1920], "shot_id": "s01", "overlays": [{"kind": "outgoing"}]})
    )
    (run_dir / "shots-captioned").mkdir()
    (run_dir / "shots-captioned" / "s01-captioned.captions.json").write_text("{}")


# ── plan ──────────────────────────────────────────────────────────────────────────────────────


def test_plan_stage_passes_a_well_formed_overlay_and_asks_nothing_of_disk(run_dir):
    report = check_coverage(SHOTS, run_dir=run_dir, stage="plan")
    assert report.missing == ()
    assert ("s01", "production.overlay") in _fields(report, missing=False)


def test_plan_stage_refuses_a_malformed_overlay(run_dir):
    doc = json.loads(json.dumps(SHOTS))
    doc["shots"][0]["production"]["overlay"] = {"kind": "chip, then bubble", "text": "x"}
    report = check_coverage(doc, run_dir=run_dir, stage="plan")
    assert _fields(report) == {("s01", "production.overlay")}


def test_plan_stage_requires_the_named_product_kit(run_dir, tmp_path):
    profiles = tmp_path / "profiles"
    report = check_coverage(
        SHOTS,
        run_dir=run_dir,
        stage="plan",
        profile="acme",
        product="widget",
        profiles_root=profiles,
    )
    assert _fields(report) == {("*", "product")}
    (profiles / "acme" / "products" / "widget").mkdir(parents=True)
    (profiles / "acme" / "products" / "widget" / "BRAND.toml").write_text("")
    report = check_coverage(
        SHOTS,
        run_dir=run_dir,
        stage="plan",
        profile="acme",
        product="widget",
        profiles_root=profiles,
    )
    assert report.missing == ()


def test_a_traversal_product_slug_is_refused_not_resolved(run_dir, tmp_path):
    report = check_coverage(
        SHOTS,
        run_dir=run_dir,
        stage="plan",
        profile="acme",
        product="../other",
        profiles_root=tmp_path,
    )
    assert [f.field for f in report.missing] == ["product"]
    assert "unsafe" in report.missing[0].detail


# ── render / finish ───────────────────────────────────────────────────────────────────────────


def test_render_stage_names_every_declared_field_with_no_consumer(run_dir):
    report = check_coverage(SHOTS, run_dir=run_dir, stage="render")
    assert _fields(report) == {
        ("s01", "sfx"),
        ("s01", "audio_bed"),
        ("shot-01", "sfx"),
        ("shot-01", "end_frame"),
    }, "a silent shot (`none` / `silent — ...`) must never produce a finding"


def test_render_stage_is_clean_once_cues_and_the_end_frame_job_exist(run_dir):
    _build_consumers(run_dir)
    report = check_coverage(SHOTS, run_dir=run_dir, stage="render")
    assert report.missing == ()


def test_a_cue_whose_file_is_absent_does_not_count(run_dir):
    _build_consumers(run_dir)
    (run_dir / "sfx" / "latch.wav").unlink()
    report = check_coverage(SHOTS, run_dir=run_dir, stage="render")
    assert _fields(report) == {("shot-01", "sfx")}


def test_finish_stage_wants_the_burned_overlay_and_the_caption_sidecar(run_dir):
    _build_consumers(run_dir)
    (run_dir / "shots-overlaid" / "s01-overlaid.overlays.json").unlink()
    (run_dir / "shots-captioned" / "s01-captioned.captions.json").unlink()
    report = check_coverage(SHOTS, run_dir=run_dir, stage="finish")
    assert _fields(report) == {("s01", "production.overlay"), ("s01", "captions")}


def test_finish_manifest_entries_stand_in_for_sidecars(run_dir):
    _build_consumers(run_dir)
    (run_dir / "shots-overlaid" / "s01-overlaid.overlays.json").unlink()
    (run_dir / "shots-captioned" / "s01-captioned.captions.json").unlink()
    (run_dir / "finish-9x16.json").write_text(
        json.dumps(
            {
                "captions_preburned": True,
                "preburned": {"captions": {"screens": [{"text": "Two taps."}]}, "overlays": None},
                "overlays": [{"shot_id": "s01", "kind": "outgoing"}],
            }
        )
    )
    report = check_coverage(SHOTS, run_dir=run_dir, stage="finish")
    assert report.missing == ()


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────


def test_cli_exits_2_with_json_and_writes_nothing(shots_path, run_dir, capsys):
    before = sorted(p.name for p in run_dir.rglob("*"))
    rc = main(["--shots", str(shots_path), "--dir", str(run_dir), "--stage", "render", "--json"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "render"
    assert {(m["shot_id"], m["field"]) for m in payload["missing"]} == {
        ("s01", "sfx"),
        ("s01", "audio_bed"),
        ("shot-01", "sfx"),
        ("shot-01", "end_frame"),
    }
    assert {"shot_id", "field", "consumer", "detail"} <= set(payload["missing"][0])
    assert sorted(p.name for p in run_dir.rglob("*")) == before, "the lint must never write"


def test_cli_exits_0_and_prints_a_table_when_clean(shots_path, run_dir, capsys):
    _build_consumers(run_dir)
    rc = main(["--shots", str(shots_path), "--dir", str(run_dir), "--stage", "finish"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "s01" in out and "production.overlay" in out and "MISSING" not in out


def test_cli_exits_1_on_an_unreadable_list(run_dir, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["--shots", str(bad), "--dir", str(run_dir), "--stage", "plan"]) == 1
