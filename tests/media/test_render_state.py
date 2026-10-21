"""`gtm_core.render_state` — the handoff is generated from data and keeps a hand-written one.

Fictional run folder. The property under test is that a declared-but-unbuilt overlay cannot fall
out of the handoff, because the handoff is the coverage lint's output and not a memory of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.render_state import GENERATOR_MARK, main, render_state, write_state

DOC = {
    "source_item": "ci-fixture-03",
    "total_duration_s": 6.0,
    "style_scaffold": {"look": "flat", "provider_model": "fixture_v1"},
    "shots": [
        {
            "id": "s01",
            "n": 1,
            "duration_s": 3.0,
            "role": "broll",
            "camera": "static",
            "motion_prompt": "she nods once",
            "visual": "a desk",
            "production": {"overlay": {"kind": "incoming bubble", "text": "hi"}},
        },
        {
            "id": "s02",
            "n": 2,
            "duration_s": 3.0,
            "role": "broll",
            "camera": "static",
            "motion_prompt": "the mug slides",
            "visual": "a table",
            "audio_bed": "silent — by design",
        },
    ],
}


@pytest.fixture
def run(tmp_path: Path) -> tuple[Path, Path]:
    shots = tmp_path / "fixture.shots.json"
    shots.write_text(json.dumps(DOC))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return shots, run_dir


def test_header_table_and_trailer_are_present(run):
    shots, run_dir = run
    text = render_state(DOC, shots_path=shots, run_dir=run_dir)
    assert text.startswith("# RENDER-STATE\n")
    assert f"- shot list: `{shots}`" in text
    assert "| n | dur | role |" in text
    assert "| 1 | 3.0s | broll |" in text and "| 2 | 3.0s | broll |" in text
    assert "declared, NOT burned" in text
    assert text.rstrip().endswith("; do not edit by hand.")
    assert GENERATOR_MARK in text


def test_a_missing_overlay_yields_the_overlays_verb(run):
    shots, run_dir = run
    text = render_state(DOC, shots_path=shots, run_dir=run_dir)
    assert "- `video_finish overlays` — s01 production.overlay:" in text
    assert "## Money\n\n- unmetered — refuse to write the manifest until metered" in text


def test_a_burned_overlay_and_a_metered_manifest_change_the_derived_sections(run):
    shots, run_dir = run
    (run_dir / "shots-overlaid").mkdir()
    (run_dir / "shots-overlaid" / "s01-overlaid.overlays.json").write_text(
        json.dumps({"frame": [1080, 1920], "shot_id": "s01", "overlays": [{"kind": "incoming"}]})
    )
    (run_dir / "render-9x16.json").write_text(
        json.dumps(
            {
                "cost_credits": 12,
                "cost_source": "preflight",
                "cost_ledger_ts": "2026-01-01T00:00:00Z",
                "shots": [{"n": "1", "video_job_id": "vid-1", "start_image_job_id": "img-1"}],
            }
        )
    )
    text = render_state(DOC, shots_path=shots, run_dir=run_dir)
    assert "video_finish overlays" not in text
    assert "- cost_credits: 12" in text and "- cost_source: preflight" in text
    assert "| img-1 | - | vid-1 |" in text
    assert "`video_finish stitch` → `video_finish run` → `video_lint --manifest`" in text


def test_a_hand_written_file_is_backed_up_never_deleted(run):
    shots, run_dir = run
    target = run_dir / "RENDER-STATE.md"
    target.write_text("# my notes\nclips done, ship it\n")
    written, backup = write_state(DOC, shots_path=shots, run_dir=run_dir)
    assert written == target.resolve()
    assert backup is not None and backup.parent == run_dir.resolve()
    assert backup.name.startswith("RENDER-STATE.hand-written.") and backup.suffix == ".md"
    assert backup.read_text() == "# my notes\nclips done, ship it\n"
    assert GENERATOR_MARK in target.read_text()
    # A generated file is regenerated in place — no second backup.
    _, again = write_state(DOC, shots_path=shots, run_dir=run_dir)
    assert again is None
    assert len(list(run_dir.glob("RENDER-STATE.hand-written.*"))) == 1


def test_out_outside_the_run_folder_is_refused(run, tmp_path):
    shots, run_dir = run
    with pytest.raises(ValueError, match="outside"):
        write_state(DOC, shots_path=shots, run_dir=run_dir, out=tmp_path / "elsewhere.md")
    assert not (tmp_path / "elsewhere.md").exists()


def test_cli_writes_the_default_path(run, capsys):
    shots, run_dir = run
    assert main(["--shots", str(shots), "--dir", str(run_dir)]) == 0
    assert (run_dir / "RENDER-STATE.md").is_file()
    assert "wrote" in capsys.readouterr().out
    assert (
        main(["--shots", str(shots), "--dir", str(run_dir), "--out", str(run_dir.parent / "x.md")])
        == 2
    )
