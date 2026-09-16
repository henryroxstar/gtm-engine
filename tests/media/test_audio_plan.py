"""``gtm_core.audio_plan`` — every sound the shot list designed has an asset, or the plan refuses.

The defect: nine shots declared nine ``sfx`` cues and eight ``audio_bed`` directions; none
existed as a file; the film shipped over a synthesized noise floor. All fixtures here are
fictional shot lists in a temp dir — the assets are empty files, because existence is the
property under test, not content.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from gtm_core import audio_plan
from gtm_core.audio_plan import load_shots, plan_audio, shot_ids
from gtm_core.video_finish.sfx_cues import SfxCue


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _touch(root: Path, rel: str) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return rel


def _shots() -> dict:
    return {
        "shots": [
            {
                "id": "s01",
                "sfx": "distant cafe murmur, one cup meeting a saucer",
                "audio_bed": "room tone alone",
            },
            {
                "id": "s02",
                "sfx": "keyboard taps",
                "audio_bed": "the felt-piano figure enters here, low and warm",
            },
            {
                "id": "s03",
                "spoken": "a line of dialogue",
                "sfx": "none",
                "audio_bed": "silent — deliberate: the line carries it",
            },
            {
                "id": "s04",
                "sfx": "silence — the scroll stops and the sound stops with it",
                "audio_bed": "room tone",
            },
        ]
    }


def _complete_cues(root: Path) -> dict:
    return {
        "bed": {"path": _touch(root, "bed/felt-piano.wav"), "start_at_s": 0.0, "gain_db": -18.0},
        "room_tone": {"path": _touch(root, "bed/room-tone.m4a")},
        "cues": [
            {
                "shot_id": "s01",
                "label": "cup on saucer",
                "path": _touch(root, "sfx/cup.wav"),
                "at_s": 1.2,
                "gain_db": -14.0,
                "trim_s": 0.05,
            },
            {
                "shot_id": "s02",
                "label": "keyboard taps",
                "path": _touch(root, "sfx/keys.wav"),
                "at_s": 0.4,
                "gain_db": -12.0,
            },
        ],
    }


def _run(tmp_path: Path, shots: dict, cues: dict, *extra: str) -> tuple[int, dict]:
    shots_p = _write(tmp_path / "cut.shots.json", shots)
    cues_p = _write(tmp_path / "cues.json", cues)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = audio_plan.main(["--shots", str(shots_p), "--cues", str(cues_p), "--json", *extra])
    return code, json.loads(buf.getvalue())


# ── complete ───────────────────────────────────────────────────────────────────────────────


def test_a_complete_plan_exits_zero_and_emits_a_runnable_mix_plan(tmp_path):
    code, out = _run(tmp_path, _shots(), _complete_cues(tmp_path))
    assert code == 0
    assert out["complete"] is True
    assert out["missing"] == [] and out["errors"] == [] and out["warnings"] == []
    assert {r["shot_id"]: r["status"] for r in out["shots"]} == {
        "s01": "ok",
        "s02": "ok",
        "s03": "n/a",
        "s04": "ok",
    }
    assert set(out["mix_plan"]) == {"s01", "s02"}
    cue = out["mix_plan"]["s02"]["cues"][0]
    assert cue["label"] == "keyboard taps" and cue["at_s"] == 0.4 and cue["gain_db"] == -12.0
    assert cue["trim_s"] == 0.0, "an omitted trim is the SfxCue default, not an error"
    assert Path(cue["path"]).is_absolute() and Path(cue["path"]).is_file()


def test_the_mix_plan_payload_is_exactly_what_mix_sfx_accepts(tmp_path):
    """Read-only contract with gtm_core.video_finish.sfx_cues.SfxCue: same field names, so the
    payload round-trips into `mix-sfx --cues` with no transcription in between."""
    _, out = _run(tmp_path, _shots(), _complete_cues(tmp_path))
    expected = {f.name for f in dataclasses.fields(SfxCue)}
    for shot in out["mix_plan"].values():
        for cue in shot["cues"]:
            assert set(cue) == expected
            SfxCue(**cue)


def test_the_text_report_names_every_shot_and_the_verdict(tmp_path, capsys):
    shots_p = _write(tmp_path / "cut.shots.json", _shots())
    cues_p = _write(tmp_path / "cues.json", _complete_cues(tmp_path))
    assert audio_plan.main(["--shots", str(shots_p), "--cues", str(cues_p)]) == 0
    out = capsys.readouterr().out
    # s03 declares sfx "none" and s04 "silence — …": both ask for nothing, so 2 declare sfx
    assert "4 shots, 2 declare sfx, 3 declare a bed" in out
    assert "s01" in out and "s04" in out and "✓ every declared cue and bed has an asset" in out


# ── missing ────────────────────────────────────────────────────────────────────────────────


def test_a_declared_cue_with_no_asset_is_named_and_refused(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["cues"] = [c for c in cues["cues"] if c["shot_id"] != "s02"]
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    assert out["complete"] is False
    assert len(out["missing"]) == 1
    assert "s02" in out["missing"][0] and "keyboard taps" in out["missing"][0]
    assert next(r for r in out["shots"] if r["shot_id"] == "s02")["status"] == "missing"


def test_a_cue_entry_whose_file_is_absent_does_not_count_as_coverage(tmp_path):
    cues = _complete_cues(tmp_path)
    (tmp_path / "sfx/keys.wav").unlink()
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    assert any("s02" in m and "keys.wav" in m and "not found" in m for m in out["missing"])
    assert "s02" in out["mix_plan"], "the plan still shows what was asked for"


def test_a_declared_bed_with_neither_bed_nor_room_tone_is_refused(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["bed"] = None
    cues["room_tone"] = None
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    missing = "\n".join(out["missing"])
    assert "s01 audio_bed" in missing and "s02 audio_bed" in missing and "s04 audio_bed" in missing
    assert "s03" not in missing, "a declared-silent bed asks for nothing"


def test_a_bed_entry_whose_file_is_absent_is_reported_once(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["bed"] = {"path": "bed/does-not-exist.wav"}
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    assert sum("does-not-exist.wav" in m for m in out["missing"]) == 1


# ── warn: a floor is not a bed ─────────────────────────────────────────────────────────────


def test_a_music_bed_covered_only_by_room_tone_warns_but_does_not_refuse(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["bed"] = None
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 0, out
    assert len(out["warnings"]) == 1
    assert "s02" in out["warnings"][0] and "felt-piano" in out["warnings"][0]
    assert "a noise floor is not the bed that was designed" in out["warnings"][0]
    statuses = {r["shot_id"]: r["status"] for r in out["shots"]}
    assert statuses["s02"] == "warn"
    assert statuses["s01"] == "ok", "'room tone alone' is exactly what room tone covers"


@pytest.mark.parametrize(
    "prose",
    [
        "a flat unresolving figure that repeats without varying",
        "a single low warm NOTE — the film's first sound",
        "strings enter underneath",
        "the score resolves and ends clean on the cut",
    ],
)
def test_the_music_words_are_matched_case_insensitively_and_as_prefixes(tmp_path, prose):
    shots = {"shots": [{"id": "s01", "audio_bed": prose}]}
    cues = {"room_tone": {"path": _touch(tmp_path, "bed/tone.m4a")}, "cues": []}
    code, out = _run(tmp_path, shots, cues)
    assert code == 0 and len(out["warnings"]) == 1


def test_a_bed_direction_that_is_not_music_is_covered_by_room_tone(tmp_path):
    shots = {"shots": [{"id": "s01", "audio_bed": "a low sustained tone, cold and unresolved"}]}
    cues = {"room_tone": {"path": _touch(tmp_path, "bed/tone.m4a")}, "cues": []}
    code, out = _run(tmp_path, shots, cues)
    assert code == 0 and out["warnings"] == []


# ── errors ─────────────────────────────────────────────────────────────────────────────────


def test_a_cue_naming_an_unknown_shot_is_an_error_that_names_it(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["cues"].append(
        {"shot_id": "s09", "label": "stray chime", "path": "sfx/cup.wav", "at_s": 0.1}
    )
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    assert len(out["errors"]) == 1
    assert "'s09'" in out["errors"][0] and "stray chime" in out["errors"][0]
    assert "s01" in out["errors"][0], "the error lists the ids the shot list does have"


def test_a_malformed_cue_is_an_error_not_a_crash(tmp_path):
    cues = _complete_cues(tmp_path)
    cues["cues"].append({"shot_id": "s01", "label": "no timing", "path": "sfx/cup.wav"})
    code, out = _run(tmp_path, _shots(), cues)
    assert code == 2
    assert any("no timing" in e and "malformed" in e for e in out["errors"])


def test_unreadable_inputs_exit_three(tmp_path, capsys):
    shots_p = _write(tmp_path / "cut.shots.json", _shots())
    bad = tmp_path / "cues.json"
    bad.write_text("[]", encoding="utf-8")
    assert audio_plan.main(["--shots", str(shots_p), "--cues", str(bad)]) == 3
    assert "not a JSON object" in capsys.readouterr().err
    assert audio_plan.main(["--shots", str(tmp_path / "nope.json"), "--cues", str(bad)]) == 3


# ── shot identity and path resolution ──────────────────────────────────────────────────────


def test_id_less_shots_get_the_same_positional_name_the_burn_uses():
    """`shot-NN`, zero-padded to two digits from index 0 — the fallback in
    gtm_core.video_finish.burn_captions. Two derivations of one id is how a cue silently keys
    to nothing."""
    assert shot_ids([{}, {"id": ""}, {"id": "s03"}, {}]) == ["shot-00", "shot-01", "s03", "shot-03"]


def test_a_bare_list_is_accepted_as_a_shot_list(tmp_path):
    p = _write(tmp_path / "bare.json", [{"id": "a"}, {"id": "b"}])
    assert [s["id"] for s in load_shots(p)] == ["a", "b"]
    with pytest.raises(ValueError, match="not a shot list"):
        load_shots(_write(tmp_path / "bad.json", {"shots": "nope"}))


def test_paths_resolve_against_shots_root_when_given(tmp_path):
    assets = tmp_path / "elsewhere"
    _touch(assets, "sfx/keys.wav")
    shots = {"shots": [{"sfx": "keyboard taps"}]}
    cues = {"cues": [{"shot_id": "shot-00", "label": "keys", "path": "sfx/keys.wav", "at_s": 0.4}]}
    code, out = _run(tmp_path, shots, cues, "--shots-root", str(assets))
    assert code == 0, out
    code, out = _run(tmp_path, shots, cues)  # default root: the cues file's directory
    assert code == 2


def test_plan_audio_is_usable_as_a_library_call(tmp_path):
    shots = [{"id": "s01", "sfx": "one soft confirmation tick"}]
    plan = plan_audio(shots, {"cues": []}, root=tmp_path)
    assert not plan.complete
    assert plan.rows[0].status == "missing" and plan.rows[0].problems
